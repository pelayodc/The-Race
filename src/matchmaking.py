import asyncio
import math
import random
from datetime import datetime, timezone
from itertools import combinations, permutations

import disnake

from bot_runtime import CAPTAIN_DRAFT_TIMEOUT_SECONDS, MATCHMAKING_ODD_PLAYER_POLICIES, MATCHMAKING_ROLE_MODES, MATCHMAKING_ROLE_SOURCES, MATCHMAKING_ROLES, MATCHMAKING_TEAM_MODES, MAX_SELECT_OPTIONS
from discord_helpers import get_discord_channel, get_guild_member, public_matchmaking_announcement, require_admin_interaction, send_ephemeral_followup, send_ephemeral_response, send_temporary_public_message
from i18n import t
from linked_accounts import primary_summoner_queue_data
from persistent_messages import refresh_configured_admin_message, refresh_configured_matchmaking_message
from state import effective_matchmaking_role_mode, effective_matchmaking_role_source, effective_matchmaking_separate_channels, effective_matchmaking_team_mode, effective_odd_players_policy, ensure_matchmaking_state, forced_mode_text, load_json_data, matchmaking_channel_id, normalize_matchmaking_role, odd_players_policy_label, role_label, role_mode_label, role_source_label, team_mode_label, team_mode_lock_text, voice_mode_label
from utils.auditUtils import interaction_actor, log_event, system_actor
from utils.commonUtils import jsonFile
from utils.jsonUtils import writeToJsonFile


def user_queue_index(queue, user_id):
    user_id = str(user_id)
    for index, player in enumerate(queue):
        if str(player.get("userId")) == user_id:
            return index
    return None

def remove_user_from_matchmaking_queue(json_data, user_id):
    ensure_matchmaking_state(json_data)
    queue = json_data["matchmakingQueue"]
    index = user_queue_index(queue, user_id)
    if index is None:
        return False
    del queue[index]
    return True

def player_id(player):
    return str(player.get("userId"))

def player_score(player, neutral_score=0):
    score = player.get("score")
    if score is None:
        return neutral_score
    try:
        return int(score)
    except (TypeError, ValueError):
        return neutral_score

def player_valid_score(player):
    if player.get("tier") is None or player.get("rank") is None or player.get("leaguePoints") is None:
        return None
    score = player.get("score")
    if score is None:
        return None
    try:
        return int(score)
    except (TypeError, ValueError):
        return None

def valid_player_scores(players):
    return [score for score in (player_valid_score(player) for player in players) if score is not None]

def has_valid_player_scores(players):
    return bool(valid_player_scores(players))

def neutral_unlinked_score(players):
    scores = valid_player_scores(players)
    if not scores:
        return 0
    return round(sum(scores) / len(scores))

def matchmaking_player_label(player, include_score=False, neutral_score=0):
    summoner = player.get("summonerFullName") or "Unlinked"
    label = f"<@{player['userId']}> - {summoner}"
    if include_score:
        score = player_valid_score(player)
        if score is None and neutral_score:
            score = neutral_score
        label += f" ({score if score is not None else '-'})"
    return label

def format_team(players, neutral_score=0):
    if not players:
        return "-"
    return "\n".join(matchmaking_player_label(player, True, neutral_score) for player in players)

def players_by_id(players):
    return {player_id(player): player for player in players}

def discord_role_preferences(json_data, user_id):
    ensure_matchmaking_state(json_data)
    user_id = str(user_id)
    preferences = json_data.setdefault("discordRolePreferences", {})
    record = preferences.setdefault(user_id, {})
    return record

def role_preference_for_player(json_data, player):
    source = effective_matchmaking_role_source(json_data)
    user_id = player_id(player)
    record = discord_role_preferences(json_data, user_id)
    if source == "player":
        return normalize_matchmaking_role(record.get("playerRole"))
    if source == "admin":
        return normalize_matchmaking_role(record.get("adminRole"))
    return infer_history_role_for_player(json_data, player)

def infer_history_role_for_player(json_data, player):
    summoner_name = player.get("summonerFullName")
    puuid = player.get("puuid")
    if not summoner_name or not puuid:
        return None

    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    match_data = json_data.get("matchData") or {}
    role_counts = {}
    for match_id in summoner_data.get("recentMatchIds", []):
        match = match_data.get(match_id)
        if not match:
            continue
        for participant in match.get("info", {}).get("participants", []):
            if participant.get("puuid") != puuid:
                continue
            role = normalize_matchmaking_role(
                participant.get("teamPosition")
                or participant.get("individualPosition")
                or participant.get("lane")
            )
            if role:
                role_counts[role] = role_counts.get(role, 0) + 1
            break
    if not role_counts:
        return None
    return max(role_counts.items(), key=lambda item: (item[1], -MATCHMAKING_ROLES.index(item[0])))[0]

def player_role_label(json_data, player):
    role = role_preference_for_player(json_data, player)
    return role_label(role or "fill", json_data)

def matchmaking_player_role_label(json_data, player, include_score=False, neutral_score=0):
    label = matchmaking_player_label(player, include_score, neutral_score)
    return f"{label} - {player_role_label(json_data, player)}"

def set_player_role_preference(json_data, user, role, source, actor=None):
    role = normalize_matchmaking_role(role)
    if role not in MATCHMAKING_ROLES:
        return False
    record = discord_role_preferences(json_data, user.id)
    record["displayName"] = getattr(user, "display_name", None) or str(user.id)
    timestamp = datetime.now(timezone.utc).isoformat()
    if source == "admin":
        record["adminRole"] = role
        record["adminRoleUpdatedAt"] = timestamp
        if actor:
            record["adminRoleActorId"] = str(actor.get("actorId") or "")
            record["adminRoleActorName"] = actor.get("actorName")
    else:
        record["playerRole"] = role
        record["playerRoleUpdatedAt"] = timestamp
    return True

def assign_roles_to_team(json_data, players, role_mode):
    if len(players) != 5 or role_mode not in ("preferred", "inverse"):
        return [dict(player) for player in players], 0

    best_assignment = None
    best_score = None
    roles = MATCHMAKING_ROLES[:]
    random.shuffle(roles)
    for role_order in permutations(roles):
        score = 0
        for player, assigned_role in zip(players, role_order):
            preferred_role = role_preference_for_player(json_data, player)
            if not preferred_role:
                continue
            if role_mode == "preferred" and assigned_role == preferred_role:
                score += 1
            elif role_mode == "inverse" and assigned_role != preferred_role:
                score += 1
        if best_score is None or score > best_score:
            best_score = score
            best_assignment = role_order

    assigned_players = []
    for player, assigned_role in zip(players, best_assignment or MATCHMAKING_ROLES):
        assigned_player = dict(player)
        assigned_player["assignedRole"] = assigned_role
        assigned_players.append(assigned_player)
    return assigned_players, best_score or 0

def team_score_diff(team_one, team_two):
    players = team_one + team_two
    neutral_score = neutral_unlinked_score(players)
    return abs(
        sum(player_score(player, neutral_score) for player in team_one)
        - sum(player_score(player, neutral_score) for player in team_two)
    )

def role_matched_teams(json_data, players, prefer_rank_balance=False):
    role_mode = effective_matchmaking_role_mode(json_data)
    indexed_players = list(enumerate(players))
    best_candidates = []
    best_key = None

    for combo in combinations(indexed_players, 5):
        team_one_indexes = {index for index, _ in combo}
        raw_team_one = [player for index, player in indexed_players if index in team_one_indexes]
        raw_team_two = [player for index, player in indexed_players if index not in team_one_indexes]
        team_one, team_one_role_score = assign_roles_to_team(json_data, raw_team_one, role_mode)
        team_two, team_two_role_score = assign_roles_to_team(json_data, raw_team_two, role_mode)
        role_score = team_one_role_score + team_two_role_score
        diff = team_score_diff(team_one, team_two) if prefer_rank_balance else 0
        key = (-role_score, diff)
        if best_key is None or key < best_key:
            best_key = key
            best_candidates = [(team_one, team_two)]
        elif key == best_key:
            best_candidates.append((team_one, team_two))

    return random.choice(best_candidates)

def ensure_role_assignments(json_data, team_one, team_two):
    role_mode = effective_matchmaking_role_mode(json_data)
    if role_mode not in ("preferred", "inverse"):
        return team_one, team_two
    if all(player.get("assignedRole") for player in team_one + team_two):
        return team_one, team_two
    assigned_team_one, _ = assign_roles_to_team(json_data, team_one, role_mode)
    assigned_team_two, _ = assign_roles_to_team(json_data, team_two, role_mode)
    return assigned_team_one, assigned_team_two

def format_role_team(players, json_data):
    if not players:
        return "-"
    ordered = sorted(players, key=lambda player: MATCHMAKING_ROLES.index(player.get("assignedRole")) if player.get("assignedRole") in MATCHMAKING_ROLES else 99)
    lines = []
    for player in ordered:
        role = role_label(player.get("assignedRole") or "fill", json_data).upper()
        summoner = player.get("summonerFullName") or "Unlinked"
        lines.append(f"{role} - <@{player['userId']}> ({summoner})")
    return "\n".join(lines)

def balanced_rank_teams(players):
    neutral_score = neutral_unlinked_score(players)
    target_size = len(players) // 2
    allowed_sizes = {target_size}
    if len(players) % 2:
        allowed_sizes.add(target_size + 1)

    best_team_one = None
    best_diff = None
    indexed_players = list(enumerate(players))
    total_score = sum(player_score(player, neutral_score) for player in players)

    for team_size in allowed_sizes:
        for combo in combinations(indexed_players, team_size):
            team_one_indexes = {index for index, _ in combo}
            team_one_score = sum(player_score(player, neutral_score) for _, player in combo)
            diff = abs(total_score - (team_one_score * 2))
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_team_one = team_one_indexes

    team_one = [player for index, player in indexed_players if index in best_team_one]
    team_two = [player for index, player in indexed_players if index not in best_team_one]
    return team_one, team_two

def random_teams(players):
    shuffled_players = players[:]
    random.shuffle(shuffled_players)
    return shuffled_players[::2], shuffled_players[1::2]

def select_captains(players):
    linked_players = [player for player in players if player.get("summonerFullName")]
    captain_pool = linked_players if len(linked_players) >= 2 else players
    return random.sample(captain_pool, 2)

def build_captain_pick_order(captain_ids, pick_count):
    first, second = captain_ids
    pattern = [first, second, second, first]
    return [pattern[index % len(pattern)] for index in range(pick_count)]

def draft_team_key(draft, captain_id):
    if str(captain_id) == str(draft["captainIds"][0]):
        return "teamOne"
    return "teamTwo"

def draft_team_for_player_id(draft, user_id):
    user_id = str(user_id)
    if user_id in [str(value) for value in draft.get("teamOne", [])]:
        return "teamOne"
    if user_id in [str(value) for value in draft.get("teamTwo", [])]:
        return "teamTwo"
    return None

def normalize_draft_turn(draft):
    remaining = [str(user_id) for user_id in draft.get("remainingPlayerIds", [])]
    draft["remainingPlayerIds"] = remaining
    if not remaining:
        draft["turnCaptainId"] = None
        return draft

    max_team_size = math.ceil((len(remaining) + len(draft.get("teamOne", [])) + len(draft.get("teamTwo", []))) / 2)
    pick_order = [str(user_id) for user_id in draft.get("pickOrder", [])]
    pick_index = int(draft.get("pickIndex", 0))
    while pick_index < len(pick_order):
        captain_id = pick_order[pick_index]
        team_key = draft_team_key(draft, captain_id)
        if len(draft.get(team_key, [])) < max_team_size:
            draft["pickIndex"] = pick_index
            draft["turnCaptainId"] = captain_id
            return draft
        pick_index += 1

    draft["turnCaptainId"] = pick_order[-1] if pick_order else draft["captainIds"][0]
    draft["pickIndex"] = len(pick_order)
    return draft

def create_captain_draft(players, starter_user_id):
    captains = select_captains(players)
    captain_ids = [player_id(captain) for captain in captains]
    first_pick_captain_id = random.choice(captain_ids)
    second_captain_id = captain_ids[1] if first_pick_captain_id == captain_ids[0] else captain_ids[0]
    remaining_player_ids = [player_id(player) for player in players if player_id(player) not in captain_ids]
    draft = {
        "captainIds": captain_ids,
        "teamOne": [captain_ids[0]],
        "teamTwo": [captain_ids[1]],
        "remainingPlayerIds": remaining_player_ids,
        "turnCaptainId": first_pick_captain_id,
        "pickOrder": build_captain_pick_order([first_pick_captain_id, second_captain_id], len(remaining_player_ids)),
        "pickIndex": 0,
        "startedByUserId": str(starter_user_id),
        "lastTurnAt": datetime.now(timezone.utc).timestamp()
    }
    return normalize_draft_turn(draft)

def apply_draft_pick(json_data, picked_user_id, autopick=False):
    draft = json_data.get("matchmakingDraft")
    if not draft:
        return False, "No captain draft is currently active."

    picked_user_id = str(picked_user_id)
    remaining = [str(user_id) for user_id in draft.get("remainingPlayerIds", [])]
    if picked_user_id not in remaining:
        return False, "That player is no longer available."

    captain_id = str(draft.get("turnCaptainId"))
    team_key = draft_team_key(draft, captain_id)
    draft.setdefault(team_key, []).append(picked_user_id)
    draft["remainingPlayerIds"] = [user_id for user_id in remaining if user_id != picked_user_id]
    draft["pickIndex"] = int(draft.get("pickIndex", 0)) + 1
    draft["lastTurnAt"] = datetime.now(timezone.utc).timestamp()
    normalize_draft_turn(draft)
    picked_player = players_by_id(json_data.get("matchmakingQueue", [])).get(picked_user_id, {"userId": picked_user_id})
    source = t(json_data, "matchmaking.autopicked_source") if autopick else t(json_data, "matchmaking.picked_source")
    return True, t(json_data, "matchmaking.picked", source=source, player=matchmaking_player_label(picked_player))

def captain_draft_teams(json_data):
    draft = json_data.get("matchmakingDraft") or {}
    by_id = players_by_id(json_data.get("matchmakingQueue", []))
    team_one = [by_id[user_id] for user_id in [str(value) for value in draft.get("teamOne", [])] if user_id in by_id]
    team_two = [by_id[user_id] for user_id in [str(value) for value in draft.get("teamTwo", [])] if user_id in by_id]
    return team_one, team_two

def is_draft_complete(json_data):
    draft = json_data.get("matchmakingDraft")
    return bool(draft is not None and not draft.get("remainingPlayerIds"))

def cancel_matchmaking_draft(json_data):
    json_data["matchmakingDraft"] = None
    return json_data

def remove_player_from_matchmaking_draft(json_data, user_id):
    draft = json_data.get("matchmakingDraft")
    if not draft:
        return False, False

    user_id = str(user_id)
    if user_id in [str(value) for value in draft.get("captainIds", [])]:
        json_data["matchmakingDraft"] = None
        return True, True

    changed = False
    for key in ["remainingPlayerIds", "teamOne", "teamTwo"]:
        values = [str(value) for value in draft.get(key, [])]
        filtered = [value for value in values if value != user_id]
        if filtered != values:
            draft[key] = filtered
            changed = True

    if changed:
        normalize_draft_turn(draft)
    return changed, False

def format_matchmaking_queue(json_data):
    queue = json_data.get("matchmakingQueue", [])
    if not queue:
        return t(json_data, "matchmaking.no_players_queue")

    players = []
    for index, player in enumerate(queue, start=1):
        voice = f"<#{player['voiceChannelId']}>" if player.get("voiceChannelId") else "-"
        summoner = player.get("summonerFullName") or "Unlinked"
        role = player_role_label(json_data, player)
        players.append(f"**{index}.** <@{player['userId']}> - {summoner} - {role} - {voice}")
    return "\n".join(players)

def matchmaking_embed(json_data):
    ensure_matchmaking_state(json_data)
    queue = json_data["matchmakingQueue"]
    separate_channels = effective_matchmaking_separate_channels(json_data)
    separate_mode_text = forced_mode_text(json_data)
    team_mode = effective_matchmaking_team_mode(json_data)
    team_mode_text = team_mode_label(team_mode, json_data)
    team_mode_lock = team_mode_lock_text(json_data)
    odd_policy = effective_odd_players_policy(json_data)
    role_source = effective_matchmaking_role_source(json_data)
    role_mode = effective_matchmaking_role_mode(json_data)
    draft = json_data.get("matchmakingDraft")
    ready_text = t(json_data, "matchmaking.ready") if len(queue) >= 2 else t(json_data, "matchmaking.waiting")
    if draft:
        ready_text = t(json_data, "matchmaking.draft_in_progress")
    fallback_text = ""
    if queue and team_mode == "balanced_rank" and not has_valid_player_scores(queue):
        fallback_text = f"\n{t(json_data, 'matchmaking.fallback_random')}"

    embed = disnake.Embed(
        title=t(json_data, "matchmaking.title"),
        description=(
            f"{ready_text}\n"
            f"{t(json_data, 'matchmaking.players')}: **{len(queue)}/10**\n"
            f"{t(json_data, 'matchmaking.team_mode')}: **{team_mode_text}** ({team_mode_lock})\n"
            f"{t(json_data, 'matchmaking.role_matching')}: **{role_mode_label(role_mode, json_data)}**\n"
            f"{t(json_data, 'matchmaking.role_source')}: **{role_source_label(role_source, json_data)}**\n"
            f"{t(json_data, 'matchmaking.voice')}: **{voice_mode_label(separate_channels, json_data)}** ({separate_mode_text})\n"
            f"{t(json_data, 'matchmaking.odd_players')}: **{odd_players_policy_label(odd_policy, json_data)}**"
            f"{fallback_text}"
        ),
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )

    if draft:
        by_id = players_by_id(queue)
        neutral_score = neutral_unlinked_score(queue)
        team_one, team_two = captain_draft_teams(json_data)
        remaining = [by_id[user_id] for user_id in [str(value) for value in draft.get("remainingPlayerIds", [])] if user_id in by_id]
        turn = draft.get("turnCaptainId")
        embed.add_field(name=t(json_data, "matchmaking.turn"), value=t(json_data, "matchmaking.turn_value", user_id=turn) if turn else t(json_data, "matchmaking.draft_finishing"), inline=False)
        embed.add_field(name=t(json_data, "matchmaking.team_1"), value=format_team(team_one, neutral_score), inline=True)
        embed.add_field(name=t(json_data, "matchmaking.team_2"), value=format_team(team_two, neutral_score), inline=True)
        embed.add_field(name=t(json_data, "matchmaking.remaining_players"), value=format_team(remaining, neutral_score), inline=False)
    elif queue:
        players = []
        for index, player in enumerate(queue, start=1):
            user = f"<@{player['userId']}>"
            voice = f"<#{player['voiceChannelId']}>" if player.get("voiceChannelId") else "-"
            summoner = player.get("summonerFullName") or "Unlinked"
            role = player_role_label(json_data, player)
            players.append(f"**{index}.** {user} - {summoner} - {role} - {voice}")
        embed.add_field(name=t(json_data, "matchmaking.current_players"), value="\n".join(players), inline=False)
    else:
        embed.add_field(name=t(json_data, "matchmaking.current_players"), value=t(json_data, "matchmaking.no_players_queue"), inline=False)

    embed.add_field(
        name=t(json_data, "matchmaking.controls"),
        value=t(json_data, "matchmaking.controls_value"),
        inline=False
    )
    embed.set_footer(text=t(json_data, "matchmaking.join_footer"))
    return embed

async def active_matchmaking_queue(guild, json_data):
    active_queue = []
    queue = json_data.get("matchmakingQueue", [])
    for player in queue:
        member = await get_guild_member(guild, player["userId"])
        if member and member.voice and member.voice.channel:
            player["voiceChannelId"] = member.voice.channel.id
            active_queue.append(player)
    json_data["matchmakingQueue"] = active_queue
    return active_queue

async def finish_matchmaking_teams(guild, json_data, team_one, team_two, mode, note=None):
    team_one, team_two = ensure_role_assignments(json_data, team_one, team_two)
    json_data["matchmakingInProgress"] = True
    writeToJsonFile(jsonFile, json_data)
    created_channels = []
    players = team_one + team_two

    if effective_matchmaking_separate_channels(json_data):
        try:
            category = None
            first_voice_id = players[0].get("voiceChannelId")
            first_voice = guild.get_channel(int(first_voice_id)) if first_voice_id else None
            if first_voice:
                category = first_voice.category

            overwrites = {
                guild.default_role: disnake.PermissionOverwrite(view_channel=True, connect=True)
            }
            team_one_channel = await guild.create_voice_channel("Team 1", category=category, overwrites=overwrites)
            team_two_channel = await guild.create_voice_channel("Team 2", category=category, overwrites=overwrites)
            created_channels = [team_one_channel.id, team_two_channel.id]

            for player in team_one:
                member = await get_guild_member(guild, player["userId"])
                if member and member.voice:
                    await member.move_to(team_one_channel)
            for player in team_two:
                member = await get_guild_member(guild, player["userId"])
                if member and member.voice:
                    await member.move_to(team_two_channel)
        except disnake.Forbidden:
            json_data["matchmakingInProgress"] = False
            json_data["matchmakingDraft"] = None
            writeToJsonFile(jsonFile, json_data)
            for channel_id in created_channels:
                channel = guild.get_channel(int(channel_id))
                if channel:
                    try:
                        await channel.delete()
                    except (disnake.Forbidden, disnake.HTTPException):
                        pass
            return False, "I do not have permission to create voice channels or move members.", json_data
        except disnake.HTTPException:
            json_data["matchmakingInProgress"] = False
            json_data["matchmakingDraft"] = None
            writeToJsonFile(jsonFile, json_data)
            for channel_id in created_channels:
                channel = guild.get_channel(int(channel_id))
                if channel:
                    try:
                        await channel.delete()
                    except (disnake.Forbidden, disnake.HTTPException):
                        pass
            return False, "Discord rejected the channel creation or member move request.", json_data

    json_data["matchmakingQueue"] = []
    json_data["matchmakingDraft"] = None
    json_data["matchmakingTeamChannelIds"] = created_channels
    json_data["matchmakingInProgress"] = False
    writeToJsonFile(jsonFile, json_data)

    neutral_score = neutral_unlinked_score(players)
    scores_available = has_valid_player_scores(players)
    team_one_score = sum(player_score(player, neutral_score) for player in team_one) if scores_available else "-"
    team_two_score = sum(player_score(player, neutral_score) for player in team_two) if scores_available else "-"
    role_mode = effective_matchmaking_role_mode(json_data)
    if role_mode in ("preferred", "inverse"):
        team_one_mentions = "\n" + format_role_team(team_one, json_data)
        team_two_mentions = "\n" + format_role_team(team_two, json_data)
    else:
        team_one_mentions = ", ".join(f"<@{player['userId']}>" for player in team_one)
        team_two_mentions = ", ".join(f"<@{player['userId']}>" for player in team_two)
    message = t(
        json_data,
        "matchmaking.match_started",
        mode=team_mode_label(mode, json_data),
        team_one_count=len(team_one),
        team_one_score=team_one_score,
        team_one=team_one_mentions,
        team_two_count=len(team_two),
        team_two_score=team_two_score,
        team_two=team_two_mentions
    )
    if note:
        message += f"\n{note}"
    return True, message, json_data

async def start_matchmaking_queue(guild, json_data, starter_user_id=None):
    json_data = ensure_matchmaking_state(json_data)
    queue = json_data.get("matchmakingQueue", [])
    role_mode = effective_matchmaking_role_mode(json_data)

    if len(queue) < 2:
        writeToJsonFile(jsonFile, json_data)
        return False, t(json_data, "matchmaking.at_least_two"), json_data
    if len(queue) > 10:
        writeToJsonFile(jsonFile, json_data)
        return False, t(json_data, "matchmaking.max_ten"), json_data
    if len(queue) % 2 and effective_odd_players_policy(json_data) == "require_even":
        writeToJsonFile(jsonFile, json_data)
        return False, t(json_data, "matchmaking.require_even"), json_data
    if role_mode in ("preferred", "inverse") and len(queue) != 10:
        writeToJsonFile(jsonFile, json_data)
        return False, t(json_data, "matchmaking.roles_need_ten"), json_data

    mode = effective_matchmaking_team_mode(json_data)
    if mode == "captains":
        if json_data.get("matchmakingDraft"):
            writeToJsonFile(jsonFile, json_data)
            return False, t(json_data, "matchmaking.draft_already_active"), json_data
        json_data["matchmakingDraft"] = create_captain_draft(queue, starter_user_id or queue[0]["userId"])
        if is_draft_complete(json_data):
            return await finish_captain_draft_if_complete(guild, json_data)
        writeToJsonFile(jsonFile, json_data)
        captains = ", ".join(f"<@{captain_id}>" for captain_id in json_data["matchmakingDraft"]["captainIds"])
        return True, t(json_data, "matchmaking.draft_started", captains=captains), json_data

    if mode == "balanced_rank":
        if role_mode in ("preferred", "inverse"):
            team_one, team_two = role_matched_teams(json_data, queue, prefer_rank_balance=True)
            note = t(json_data, "matchmaking.role_matching_note", mode=role_mode_label(role_mode, json_data))
        elif has_valid_player_scores(queue):
            team_one, team_two = balanced_rank_teams(queue)
            note = None
        else:
            team_one, team_two = random_teams(queue)
            note = t(json_data, "matchmaking.balance_fallback_note")
    else:
        if role_mode in ("preferred", "inverse"):
            team_one, team_two = role_matched_teams(json_data, queue, prefer_rank_balance=False)
            note = t(json_data, "matchmaking.role_matching_note", mode=role_mode_label(role_mode, json_data))
        else:
            team_one, team_two = random_teams(queue)
            note = None

    return await finish_matchmaking_teams(guild, json_data, team_one, team_two, mode, note)

async def finish_captain_draft_if_complete(guild, json_data):
    if not is_draft_complete(json_data):
        return False, None, json_data
    team_one, team_two = captain_draft_teams(json_data)
    return await finish_matchmaking_teams(guild, json_data, team_one, team_two, "captains")

class CaptainPickSelect(disnake.ui.Select):
    def __init__(self, json_data):
        draft = json_data.get("matchmakingDraft") or {}
        by_id = players_by_id(json_data.get("matchmakingQueue", []))
        options = []
        for user_id in [str(value) for value in draft.get("remainingPlayerIds", [])][:MAX_SELECT_OPTIONS]:
            player = by_id.get(user_id)
            if not player:
                continue
            summoner = player.get("summonerFullName") or "Unlinked"
            label = f"{player.get('displayName') or user_id} - {summoner}"
            options.append(disnake.SelectOption(label=label[:100], value=user_id))
        super().__init__(
            placeholder=t(json_data, "matchmaking.pick_select_placeholder"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="matchmaking:captains:pick_select"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        draft = json_data.get("matchmakingDraft")
        if not draft:
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.no_draft"))
            return
        if str(draft.get("turnCaptainId")) != str(inter.author.id):
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.not_your_turn"))
            return

        success, message = apply_draft_pick(json_data, self.values[0])
        if not success:
            await send_ephemeral_followup(inter, message)
            return

        finished, finish_message, json_data = await finish_captain_draft_if_complete(inter.guild, json_data)
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        log_event("matchmaking_captain_pick", actor=interaction_actor(inter), status="success", summary=message, details={"pickedUserId": str(self.values[0]), "finished": finished})
        if finished and finish_message:
            await send_temporary_public_message(inter.channel, finish_message)
            await send_ephemeral_followup(inter, f"{message}\n{t(json_data, 'matchmaking.teams_announced_deleted')}")
        else:
            await send_ephemeral_followup(inter, f"{message}\n{t(json_data, 'matchmaking.waiting_next_pick')}")

class CaptainPickView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=90)
        self.add_item(CaptainPickSelect(json_data))

def matchmaking_settings_embed(json_data, admin=False):
    ensure_matchmaking_state(json_data)
    title = t(json_data, "matchmaking.admin_settings_title") if admin else t(json_data, "matchmaking.settings_title")
    description = (
        f"{t(json_data, 'matchmaking.team_mode')}: **{team_mode_label(effective_matchmaking_team_mode(json_data), json_data)}** ({team_mode_lock_text(json_data)})\n"
        f"{t(json_data, 'matchmaking.role_matching')}: **{role_mode_label(effective_matchmaking_role_mode(json_data), json_data)}**\n"
        f"{t(json_data, 'matchmaking.role_source')}: **{role_source_label(effective_matchmaking_role_source(json_data), json_data)}**\n"
        f"{t(json_data, 'matchmaking.voice')}: **{voice_mode_label(effective_matchmaking_separate_channels(json_data), json_data)}** ({forced_mode_text(json_data)})\n"
        f"{t(json_data, 'matchmaking.odd_players')}: **{odd_players_policy_label(effective_odd_players_policy(json_data), json_data)}**"
    )
    if json_data.get("matchmakingDraft"):
        description += f"\n{t(json_data, 'matchmaking.draft_active_lock')}"
    embed = disnake.Embed(
        title=title,
        description=description,
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )
    return embed

def team_mode_options(selected_mode=None, include_unlocked=False, forced_mode=None, json_data=None):
    json_data = json_data or {}
    options = []
    if include_unlocked:
        options.append(disnake.SelectOption(label=t(json_data, "matchmaking.unlocked"), value="unlocked", default=forced_mode not in MATCHMAKING_TEAM_MODES))
    for mode in MATCHMAKING_TEAM_MODES:
        label = team_mode_label(mode, json_data)
        value = f"force:{mode}" if include_unlocked else mode
        default = forced_mode == mode if include_unlocked else selected_mode == mode
        options.append(disnake.SelectOption(label=label if not include_unlocked else t(json_data, "matchmaking.force_mode", mode=label), value=value, default=default))
    return options

def voice_mode_options(selected_value=None, include_unlocked=False, forced_value=None, json_data=None):
    json_data = json_data or {}
    if include_unlocked:
        return [
            disnake.SelectOption(label=t(json_data, "matchmaking.unlocked"), value="unlocked", default=forced_value is None),
            disnake.SelectOption(label=t(json_data, "matchmaking.force_same"), value="force:same", default=forced_value is False),
            disnake.SelectOption(label=t(json_data, "matchmaking.force_separate"), value="force:separate", default=forced_value is True),
        ]
    return [
        disnake.SelectOption(label=t(json_data, "matchmaking.same_channel"), value="same", default=selected_value is False),
        disnake.SelectOption(label=t(json_data, "matchmaking.separate_channels"), value="separate", default=selected_value is True),
    ]

def odd_policy_options(selected_policy, json_data=None):
    json_data = json_data or {}
    return [
        disnake.SelectOption(label=odd_players_policy_label(policy, json_data), value=policy, default=selected_policy == policy)
        for policy in MATCHMAKING_ODD_PLAYER_POLICIES
    ]

def role_source_options(selected_source, json_data=None):
    json_data = json_data or {}
    return [
        disnake.SelectOption(label=role_source_label(source, json_data), value=source, default=selected_source == source)
        for source in MATCHMAKING_ROLE_SOURCES
    ]

def role_mode_options(selected_mode, json_data=None):
    json_data = json_data or {}
    return [
        disnake.SelectOption(label=role_mode_label(mode, json_data), value=mode, default=selected_mode == mode)
        for mode in MATCHMAKING_ROLE_MODES
    ]

def role_options(selected_role=None, json_data=None):
    json_data = json_data or {}
    return [
        disnake.SelectOption(label=role_label(role, json_data), value=role, default=selected_role == role)
        for role in MATCHMAKING_ROLES
    ]

async def refresh_matchmaking_setting_views(inter, json_data, admin=False):
    await refresh_configured_matchmaking_message(json_data)
    await refresh_configured_admin_message(json_data)
    view = MatchmakingAdminSettingsView(json_data) if admin else MatchmakingSettingsView(inter.author.id, json_data)
    await inter.response.edit_message(embed=matchmaking_settings_embed(json_data, admin), view=view)

async def require_queued_settings_user(inter, json_data):
    if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
        await send_ephemeral_response(inter, t(json_data, "matchmaking.only_queued_change"))
        return False
    return True

class PublicTeamModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        forced = json_data.get("matchmakingTeamModeForced") in MATCHMAKING_TEAM_MODES
        disabled = bool(json_data.get("matchmakingDraft") or forced)
        selected_mode = effective_matchmaking_team_mode(json_data) if forced else json_data.get("matchmakingTeamMode")
        super().__init__(
            placeholder=t(json_data, "matchmaking.team_mode_placeholder"),
            min_values=1,
            max_values=1,
            options=team_mode_options(selected_mode, json_data=json_data),
            disabled=disabled,
            row=0
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_response(inter, t(json_data, "matchmaking.team_mode_draft_locked"))
            return
        if json_data.get("matchmakingTeamModeForced") in MATCHMAKING_TEAM_MODES:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.team_locked_admin", lock=team_mode_lock_text(json_data)))
            return
        json_data["matchmakingTeamMode"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        mode = team_mode_label(effective_matchmaking_team_mode(json_data), json_data)
        log_event("matchmaking_team_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Team mode set to {mode}.", details={"mode": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)

class PublicVoiceModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        forced = json_data.get("matchmakingSeparateChannelsForced")
        selected_value = effective_matchmaking_separate_channels(json_data) if forced is not None else json_data.get("matchmakingSeparateChannels", False)
        super().__init__(
            placeholder=t(json_data, "matchmaking.voice_placeholder"),
            min_values=1,
            max_values=1,
            options=voice_mode_options(selected_value, json_data=json_data),
            disabled=forced is not None,
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        if json_data.get("matchmakingSeparateChannelsForced") is not None:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.voice_locked_admin", lock=forced_mode_text(json_data)))
            return
        json_data["matchmakingSeparateChannels"] = self.values[0] == "separate"
        writeToJsonFile(jsonFile, json_data)
        mode = voice_mode_label(json_data["matchmakingSeparateChannels"], json_data)
        log_event("matchmaking_voice_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Voice mode set to {mode}.", details={"separate": json_data["matchmakingSeparateChannels"]})
        await refresh_matchmaking_setting_views(inter, json_data)

class PublicOddPolicySelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.odd_placeholder"),
            min_values=1,
            max_values=1,
            options=odd_policy_options(effective_odd_players_policy(json_data), json_data),
            row=2
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        json_data["matchmakingOddPlayersPolicy"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        policy = odd_players_policy_label(self.values[0], json_data)
        log_event("matchmaking_odd_players_policy_selected", actor=interaction_actor(inter), status="success", summary=f"Odd player policy set to {policy}.", details={"policy": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)

class PublicRoleSourceSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.role_source_placeholder"),
            min_values=1,
            max_values=1,
            options=role_source_options(effective_matchmaking_role_source(json_data), json_data),
            row=3
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        json_data["matchmakingRoleSource"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        source = role_source_label(self.values[0], json_data)
        log_event("matchmaking_role_source_selected", actor=interaction_actor(inter), status="success", summary=f"Role source set to {source}.", details={"source": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)

class PublicRoleModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.role_mode_placeholder"),
            min_values=1,
            max_values=1,
            options=role_mode_options(effective_matchmaking_role_mode(json_data), json_data),
            row=4
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        json_data["matchmakingRoleMode"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        mode = role_mode_label(self.values[0], json_data)
        log_event("matchmaking_role_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Role matching set to {mode}.", details={"mode": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)

class PlayerRoleSelect(disnake.ui.Select):
    def __init__(self, user_id, json_data):
        record = discord_role_preferences(json_data, user_id)
        super().__init__(
            placeholder=t(json_data, "matchmaking.my_role_placeholder"),
            min_values=1,
            max_values=1,
            options=role_options(normalize_matchmaking_role(record.get("playerRole")), json_data),
            row=0
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.only_queued_change"))
            return
        set_player_role_preference(json_data, inter.author, self.values[0], "player")
        writeToJsonFile(jsonFile, json_data)
        role = role_label(self.values[0], json_data)
        log_event("matchmaking_player_role_selected", actor=interaction_actor(inter), status="success", summary=f"Player role set to {role}.", details={"role": self.values[0]})
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.response.edit_message(content=t(json_data, "matchmaking.my_role_saved", role=role), view=PlayerRoleView(inter.author.id, json_data))

class PlayerRoleView(disnake.ui.View):
    def __init__(self, user_id, json_data=None):
        super().__init__(timeout=180)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        self.add_item(PlayerRoleSelect(user_id, json_data))

class MatchmakingSettingsView(disnake.ui.View):
    def __init__(self, user_id, json_data=None):
        super().__init__(timeout=180)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        self.user_id = str(user_id)
        self.add_item(PublicTeamModeSelect(json_data))
        self.add_item(PublicVoiceModeSelect(json_data))
        self.add_item(PublicOddPolicySelect(json_data))
        self.add_item(PublicRoleSourceSelect(json_data))
        self.add_item(PublicRoleModeSelect(json_data))

class AdminTeamModeLockSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.team_lock_placeholder"),
            min_values=1,
            max_values=1,
            options=team_mode_options(include_unlocked=True, forced_mode=json_data.get("matchmakingTeamModeForced"), json_data=json_data),
            row=0
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_response(inter, t(json_data, "matchmaking.team_lock_active"))
            return
        value = None if self.values[0] == "unlocked" else self.values[0].split(":", 1)[1]
        json_data["matchmakingTeamModeForced"] = value
        writeToJsonFile(jsonFile, json_data)
        log_event("matchmaking_team_mode_forced", actor=interaction_actor(inter), status="success", summary=f"Team mode lock set to {team_mode_lock_text(json_data)}", details={"forced": value})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)

class AdminVoiceModeLockSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.voice_lock_placeholder"),
            min_values=1,
            max_values=1,
            options=voice_mode_options(include_unlocked=True, forced_value=json_data.get("matchmakingSeparateChannelsForced"), json_data=json_data),
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        value = None
        if self.values[0] == "force:same":
            value = False
        elif self.values[0] == "force:separate":
            value = True
        json_data["matchmakingSeparateChannelsForced"] = value
        writeToJsonFile(jsonFile, json_data)
        log_event("matchmaking_separate_channels_forced", actor=interaction_actor(inter), status="success", summary=f"Voice lock set to {forced_mode_text(json_data)}", details={"forced": value})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)

class AdminOddPolicySelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.odd_policy_placeholder"),
            min_values=1,
            max_values=1,
            options=odd_policy_options(effective_odd_players_policy(json_data), json_data),
            row=2
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        json_data["matchmakingOddPlayersPolicy"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        log_event("matchmaking_odd_players_policy_selected", actor=interaction_actor(inter), status="success", summary=f"Odd player policy set to {odd_players_policy_label(self.values[0])}.", details={"policy": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)

class AdminRoleSourceSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.role_source_placeholder"),
            min_values=1,
            max_values=1,
            options=role_source_options(effective_matchmaking_role_source(json_data), json_data),
            row=3
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        json_data["matchmakingRoleSource"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        source = role_source_label(self.values[0], json_data)
        log_event("matchmaking_role_source_selected", actor=interaction_actor(inter), status="success", summary=f"Role source set to {source}.", details={"source": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)

class AdminRoleModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder=t(json_data, "matchmaking.role_mode_placeholder"),
            min_values=1,
            max_values=1,
            options=role_mode_options(effective_matchmaking_role_mode(json_data), json_data),
            row=4
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        json_data["matchmakingRoleMode"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        mode = role_mode_label(self.values[0], json_data)
        log_event("matchmaking_role_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Role matching set to {mode}.", details={"mode": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)

class MatchmakingAdminSettingsView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=180)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        self.add_item(AdminTeamModeLockSelect(json_data))
        self.add_item(AdminVoiceModeLockSelect(json_data))
        self.add_item(AdminOddPolicySelect(json_data))
        self.add_item(AdminRoleSourceSelect(json_data))
        self.add_item(AdminRoleModeSelect(json_data))

class CaptainPickButton(disnake.ui.Button):
    def __init__(self, json_data=None):
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        super().__init__(
            label=t(json_data, "matchmaking.pick_player"),
            style=disnake.ButtonStyle.green,
            custom_id="matchmaking:captains:pick",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        draft = json_data.get("matchmakingDraft")
        if not draft:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.no_draft"))
            return
        if str(draft.get("turnCaptainId")) != str(inter.author.id):
            await send_ephemeral_response(inter, t(json_data, "matchmaking.not_your_turn"))
            return
        by_id = players_by_id(json_data.get("matchmakingQueue", []))
        available_remaining = [user_id for user_id in [str(value) for value in draft.get("remainingPlayerIds", [])] if user_id in by_id]
        if not available_remaining:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.no_players_pick"))
            return
        await send_ephemeral_response(inter, t(json_data, "matchmaking.choose_player"), view=CaptainPickView(json_data))

class MyRoleButton(disnake.ui.Button):
    def __init__(self, json_data=None):
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        super().__init__(
            label=t(json_data, "matchmaking.my_role"),
            style=disnake.ButtonStyle.gray,
            custom_id="matchmaking:my_role",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.only_queued_settings"))
            return
        await send_ephemeral_response(inter, t(json_data, "matchmaking.choose_my_role"), view=PlayerRoleView(inter.author.id, json_data))

class StartMatchButton(disnake.ui.Button):
    def __init__(self, json_data=None):
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        super().__init__(
            label=t(json_data, "matchmaking.start_match"),
            style=disnake.ButtonStyle.gray,
            custom_id="matchmaking:start",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        try:
            json_data = ensure_matchmaking_state(load_json_data())
            queue = await active_matchmaking_queue(inter.guild, json_data)
            if user_queue_index(queue, inter.author.id) is None:
                writeToJsonFile(jsonFile, json_data)
                await refresh_configured_matchmaking_message(json_data)
                await refresh_configured_admin_message(json_data)
                await send_ephemeral_followup(inter, t(json_data, "matchmaking.only_queued_start"))
                return

            success, message, json_data = await start_matchmaking_queue(inter.guild, json_data, inter.author.id)
            await refresh_configured_matchmaking_message(json_data)
            await refresh_configured_admin_message(json_data)
            log_event("matchmaking_start", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"mode": effective_matchmaking_team_mode(json_data)})
            if success and public_matchmaking_announcement(message):
                await send_temporary_public_message(inter.channel, message)
                await send_ephemeral_followup(inter, t(json_data, "matchmaking.announcement_deleted"))
            else:
                await send_ephemeral_followup(inter, message)
        except Exception as error:
            log_event("matchmaking_start", actor=interaction_actor(inter), status="error", summary=f"Start match failed: {error}")
            await send_ephemeral_followup(inter, t(ensure_matchmaking_state(load_json_data()), "matchmaking.start_failed", error=error))

class MatchmakingView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=None)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        draft = json_data.get("matchmakingDraft")
        queue = json_data.get("matchmakingQueue", [])
        if draft and draft.get("remainingPlayerIds"):
            self.add_item(CaptainPickButton(json_data))
        elif not draft and len(queue) >= 2:
            self.add_item(StartMatchButton(json_data))
        if not draft:
            self.add_item(MyRoleButton(json_data))
        for child in self.children:
            if getattr(child, "custom_id", None) == "matchmaking:join":
                child.label = t(json_data, "matchmaking.join")
            elif getattr(child, "custom_id", None) == "matchmaking:leave":
                child.label = t(json_data, "matchmaking.leave")
            elif getattr(child, "custom_id", None) == "matchmaking:settings":
                child.label = t(json_data, "matchmaking.settings")

    @disnake.ui.button(label="Join", style=disnake.ButtonStyle.green, custom_id="matchmaking:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        member = inter.author
        voice_channel = member.voice.channel if getattr(member, "voice", None) and member.voice else None
        if voice_channel is None:
            await send_ephemeral_followup(inter, t(ensure_matchmaking_state(load_json_data()), "matchmaking.need_voice"))
            return

        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.draft_active_join"))
            return
        queue = json_data["matchmakingQueue"]
        index = user_queue_index(queue, member.id)
        if index is not None:
            queue[index]["displayName"] = member.display_name
            queue[index]["voiceChannelId"] = voice_channel.id
            queue[index].update(primary_summoner_queue_data(json_data, member.id))
            response = t(json_data, "matchmaking.voice_updated")
        else:
            if len(queue) >= 10:
                await send_ephemeral_followup(inter, t(json_data, "matchmaking.queue_full"))
                return
            player = {
                "userId": member.id,
                "displayName": member.display_name,
                "voiceChannelId": voice_channel.id
            }
            player.update(primary_summoner_queue_data(json_data, member.id))
            queue.append(player)
            response = t(json_data, "matchmaking.joined")

        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        log_event("matchmaking_join", actor=interaction_actor(inter), status="success", summary=response)
        await send_ephemeral_followup(inter, response)

    @disnake.ui.button(label="Leave", style=disnake.ButtonStyle.red, custom_id="matchmaking:leave")
    async def leave(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        if not remove_user_from_matchmaking_queue(json_data, inter.author.id):
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.not_in_queue"))
            return
        draft_changed, draft_cancelled = remove_player_from_matchmaking_draft(json_data, inter.author.id)

        finished_message = None
        if json_data.get("matchmakingDraft") and is_draft_complete(json_data):
            success, finished_message, json_data = await finish_captain_draft_if_complete(inter.guild, json_data)
            log_event("matchmaking_captain_draft_finished", actor=interaction_actor(inter), status="success" if success else "error", summary=finished_message or "Captain draft finished after queue leave.")
        else:
            writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        if finished_message:
            await send_temporary_public_message(inter.channel, finished_message)
        log_event("matchmaking_leave", actor=interaction_actor(inter), status="success", summary="User left matchmaking queue.")
        if draft_cancelled:
            log_event("matchmaking_captain_draft_cancelled", actor=interaction_actor(inter), status="error", summary="Captain draft cancelled because a captain left the queue.")
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.left_cancelled"))
        elif draft_changed:
            log_event("matchmaking_captain_draft_player_removed", actor=interaction_actor(inter), status="success", summary="User left active captain draft.")
            message = t(json_data, "matchmaking.left_removed")
            if finished_message:
                message += f"\n{t(json_data, 'matchmaking.teams_announced_deleted')}"
            await send_ephemeral_followup(inter, message)
        else:
            await send_ephemeral_followup(inter, t(json_data, "matchmaking.left"))

    @disnake.ui.button(label="Settings", style=disnake.ButtonStyle.blurple, custom_id="matchmaking:settings")
    async def settings(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.only_queued_settings"))
            return
        await send_ephemeral_response(inter, embed=matchmaking_settings_embed(json_data), view=MatchmakingSettingsView(inter.author.id, json_data))

class QueueRemoveSelect(disnake.ui.Select):
    def __init__(self, queue, json_data=None):
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        options = []
        for player in queue[:MAX_SELECT_OPTIONS]:
            label = player.get("displayName") or str(player.get("userId"))
            options.append(disnake.SelectOption(label=label[:100], value=str(player["userId"])))
        super().__init__(
            placeholder=t(json_data, "matchmaking.queued_players"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:matchmaking:kick"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_matchmaking_state(load_json_data())
        user_id = self.values[0]
        removed = remove_user_from_matchmaking_queue(json_data, user_id)
        draft_changed, draft_cancelled = remove_player_from_matchmaking_draft(json_data, user_id)
        finished_message = None
        if json_data.get("matchmakingDraft") and is_draft_complete(json_data):
            success, finished_message, json_data = await finish_captain_draft_if_complete(inter.guild, json_data)
            log_event("matchmaking_captain_draft_finished", actor=interaction_actor(inter), status="success" if success else "error", summary=finished_message or "Captain draft finished after admin kick.")
        else:
            writeToJsonFile(jsonFile, json_data)
        response = t(json_data, "matchmaking.removed_from_queue", user_id=user_id) if removed else t(json_data, "matchmaking.no_longer_queue")
        if draft_cancelled:
            response += t(json_data, "matchmaking.captain_cancelled_suffix")
        elif draft_changed and finished_message:
            response += f"\n{t(json_data, 'matchmaking.teams_announced_deleted')}"
        log_event("matchmaking_queue_kick", actor=interaction_actor(inter), status="success" if removed else "error", summary=response, details={"userId": str(user_id)})
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        if finished_message:
            await send_temporary_public_message(inter.channel, finished_message)
        await inter.response.edit_message(embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))
        await send_ephemeral_followup(inter, response)

def parse_discord_user_id(value):
    value = str(value).strip()
    for char in ["<", ">", "@", "!"]:
        value = value.replace(char, "")
    return value if value.isdigit() else None

class SetAdminRoleModal(disnake.ui.Modal):
    def __init__(self, json_data=None):
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        components = [
            disnake.ui.TextInput(
                label=t(json_data, "matchmaking.discord_user_label"),
                custom_id="discord_user",
                required=True,
                max_length=100,
                placeholder="1234567890 or @user"
            ),
            disnake.ui.TextInput(
                label=t(json_data, "matchmaking.role_label"),
                custom_id="role",
                required=True,
                max_length=20,
                placeholder="top, jungle, mid, adc, support"
            )
        ]
        super().__init__(title=t(json_data, "matchmaking.set_admin_role"), custom_id="admin:matchmaking:set_role_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        user_id = parse_discord_user_id(inter.text_values["discord_user"])
        role = normalize_matchmaking_role(inter.text_values["role"])
        if not user_id:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.invalid_discord_user"))
            return
        if role not in MATCHMAKING_ROLES:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.invalid_role"))
            return
        member = await get_guild_member(inter.guild, user_id)
        if not member:
            await send_ephemeral_response(inter, t(json_data, "matchmaking.invalid_discord_user"))
            return

        set_player_role_preference(json_data, member, role, "admin", actor=interaction_actor(inter))
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        role_text = role_label(role, json_data)
        message = t(json_data, "matchmaking.admin_role_saved", user_id=user_id, role=role_text)
        log_event("matchmaking_admin_role_set", actor=interaction_actor(inter), status="success", summary=message, details={"userId": str(user_id), "role": role})
        await send_ephemeral_response(inter, message)

class MatchmakingAdminView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=300)
        queue = json_data.get("matchmakingQueue", [])
        if queue:
            self.add_item(QueueRemoveSelect(queue, json_data))
        for child in self.children:
            if getattr(child, "custom_id", None) == "admin:matchmaking:force_start":
                child.label = t(json_data, "matchmaking.force_start")
            elif getattr(child, "custom_id", None) == "admin:matchmaking:configure":
                child.label = t(json_data, "matchmaking.configure")
            elif getattr(child, "custom_id", None) == "admin:matchmaking:set_role":
                child.label = t(json_data, "matchmaking.set_admin_role")

    @disnake.ui.button(label="Force start", style=disnake.ButtonStyle.green, custom_id="admin:matchmaking:force_start")
    async def force_start(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        try:
            json_data["matchmakingQueue"] = await active_matchmaking_queue(inter.guild, json_data)
            success, message, json_data = await start_matchmaking_queue(inter.guild, json_data, inter.author.id)
            log_event("matchmaking_force_start", actor=interaction_actor(inter), status="success" if success else "error", summary=message)
            await refresh_configured_matchmaking_message(json_data)
            await refresh_configured_admin_message(json_data)
            if success and public_matchmaking_announcement(message):
                await send_temporary_public_message(inter.channel, message)
                await send_ephemeral_followup(inter, t(json_data, "matchmaking.announcement_deleted"))
            else:
                await send_ephemeral_followup(inter, message)
        except Exception as error:
            log_event("matchmaking_force_start", actor=interaction_actor(inter), status="error", summary=f"Force start failed: {error}")
            await send_ephemeral_followup(inter, t(json_data if 'json_data' in locals() else ensure_matchmaking_state(load_json_data()), "matchmaking.force_start_failed", error=error))

    @disnake.ui.button(label="Configure", style=disnake.ButtonStyle.blurple, custom_id="admin:matchmaking:configure", row=1)
    async def configure(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        await send_ephemeral_response(inter, embed=matchmaking_settings_embed(json_data, admin=True), view=MatchmakingAdminSettingsView(json_data))

    @disnake.ui.button(label="Set user role", style=disnake.ButtonStyle.gray, custom_id="admin:matchmaking:set_role", row=1)
    async def set_user_role(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(SetAdminRoleModal(ensure_matchmaking_state(load_json_data())))

def matchmaking_admin_embed(json_data):
    ensure_matchmaking_state(json_data)
    fallback_text = ""
    if json_data["matchmakingQueue"] and effective_matchmaking_team_mode(json_data) == "balanced_rank" and not has_valid_player_scores(json_data["matchmakingQueue"]):
        fallback_text = f"\n{t(json_data, 'matchmaking.fallback_random')}"
    embed = disnake.Embed(
        title=t(json_data, "matchmaking.admin_title"),
        description=(
            f"{t(json_data, 'matchmaking.queue')}: **{len(json_data['matchmakingQueue'])}/10**\n"
            f"{t(json_data, 'matchmaking.team_mode')}: **{team_mode_label(effective_matchmaking_team_mode(json_data), json_data)}** ({team_mode_lock_text(json_data)})\n"
            f"{t(json_data, 'matchmaking.role_matching')}: **{role_mode_label(effective_matchmaking_role_mode(json_data), json_data)}**\n"
            f"{t(json_data, 'matchmaking.role_source')}: **{role_source_label(effective_matchmaking_role_source(json_data), json_data)}**\n"
            f"{t(json_data, 'matchmaking.voice')}: **{voice_mode_label(effective_matchmaking_separate_channels(json_data), json_data)}** ({forced_mode_text(json_data)})\n"
            f"{t(json_data, 'matchmaking.odd_players')}: **{odd_players_policy_label(effective_odd_players_policy(json_data), json_data)}**"
            f"{fallback_text}"
        ),
        colour=disnake.Colour.blurple()
    )
    embed.add_field(name=t(json_data, "matchmaking.queued_players"), value=format_matchmaking_queue(json_data), inline=False)
    if json_data.get("matchmakingDraft"):
        draft = json_data["matchmakingDraft"]
        embed.add_field(name=t(json_data, "matchmaking.captain_draft"), value=f"{t(json_data, 'matchmaking.turn')}: <@{draft.get('turnCaptainId')}>\n{t(json_data, 'matchmaking.remaining_players')}: **{len(draft.get('remainingPlayerIds', []))}**", inline=False)
    return embed

async def delete_empty_matchmaking_team_channels(guild):
    json_data = ensure_matchmaking_state(load_json_data())
    channel_ids = json_data.get("matchmakingTeamChannelIds", [])
    remaining_channel_ids = []
    changed = False

    for channel_id in channel_ids:
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            changed = True
            continue
        if len(channel.members) == 0:
            try:
                await channel.delete()
                changed = True
            except (disnake.Forbidden, disnake.HTTPException):
                remaining_channel_ids.append(channel_id)
        else:
            remaining_channel_ids.append(channel_id)

    if changed:
        json_data["matchmakingTeamChannelIds"] = remaining_channel_ids
        writeToJsonFile(jsonFile, json_data)

async def process_captain_draft_timeout():
    json_data = ensure_matchmaking_state(load_json_data())
    draft = json_data.get("matchmakingDraft")
    if not draft or not draft.get("remainingPlayerIds"):
        return

    last_turn_at = float(draft.get("lastTurnAt", 0))
    if datetime.now(timezone.utc).timestamp() - last_turn_at < CAPTAIN_DRAFT_TIMEOUT_SECONDS:
        return

    by_id = players_by_id(json_data.get("matchmakingQueue", []))
    remaining_players = [
        by_id[user_id]
        for user_id in [str(value) for value in draft.get("remainingPlayerIds", [])]
        if user_id in by_id
    ]
    if not remaining_players:
        json_data["matchmakingDraft"] = None
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        log_event("matchmaking_captain_draft_cancelled", actor=system_actor(), status="error", summary="Captain draft cancelled because no remaining players were available.")
        return

    neutral_score = neutral_unlinked_score(json_data.get("matchmakingQueue", []))
    picked_player = max(remaining_players, key=lambda player: player_score(player, neutral_score))
    success, message = apply_draft_pick(json_data, picked_player["userId"], autopick=True)
    if not success:
        log_event("matchmaking_captain_autopick", actor=system_actor(), status="error", summary=message)
        return

    channel = await get_discord_channel(matchmaking_channel_id(json_data))
    guild = channel.guild if channel else None
    if guild:
        finished, finish_message, json_data = await finish_captain_draft_if_complete(guild, json_data)
    else:
        finished, finish_message = False, None

    writeToJsonFile(jsonFile, json_data)
    await refresh_configured_matchmaking_message(json_data)
    await refresh_configured_admin_message(json_data)
    if finished and finish_message and channel:
        await send_temporary_public_message(channel, finish_message)
    log_event("matchmaking_captain_autopick", actor=system_actor(), status="success", summary=finish_message or message, details={"pickedUserId": str(picked_player["userId"]), "finished": finished})
