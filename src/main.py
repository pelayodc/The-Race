import math
import os
import random
import asyncio
from io import BytesIO
from itertools import combinations
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import disnake
import pytz
from disnake import ApplicationCommandInteraction
from disnake.ext import commands, tasks
import requests
from utils.commonUtils import requestLimit, jsonFile, dailyPostTimer, discordChannel, platforms, regions, riotApKey, discordToken
from utils.dataUtils import checkForNewPatchNotes, numberOfSummoners, update, riotBackoffRemaining, riotBackoffTimestamp, riot_get
from utils.drawUtils import generateGoldGraphImage
from utils.jsonUtils import openJsonFile, writeToJsonFile
from utils.auditUtils import AUDIT_LOG_PATH, interaction_actor, log_event, read_audit_events, recent_error_events, system_actor

bot = commands.InteractionBot()
matchmaking_view_registered = False
MAX_SELECT_OPTIONS = 25
MATCHMAKING_TEAM_MODES = ["random", "balanced_rank", "captains"]
MATCHMAKING_TEAM_MODE_LABELS = {
    "random": "Random",
    "balanced_rank": "Balanced rank",
    "captains": "Captains"
}
MATCHMAKING_ODD_PLAYER_POLICIES = ["allow_uneven", "require_even"]
MATCHMAKING_ODD_PLAYER_POLICY_LABELS = {
    "allow_uneven": "Allow uneven teams",
    "require_even": "Require even teams"
}
CAPTAIN_DRAFT_TIMEOUT_SECONDS = 90
EPHEMERAL_DELETE_SECONDS = 120
AUDIT_CATEGORY_LABELS = {
    "admin": "Admin",
    "matchmaking": "Matchmaking",
    "riot": "Riot/API",
    "leaderboard": "Leaderboard",
    "links": "Linked accounts",
    "operations": "Operations",
}
AUDIT_CATEGORY_PREFIXES = {
    "admin": ["admin_", "leaderboard_channel_changed", "matchmaking_channel_changed"],
    "matchmaking": ["matchmaking_"],
    "riot": ["riot_", "leaderboard_update_skipped"],
    "leaderboard": ["leaderboard_", "personal_report_"],
    "links": ["discord_link_"],
    "operations": ["operations_"],
}


def rank_icon(tier):
    icons = {
        "IRON": "⬛",
        "BRONZE": "<:bronze:1500229920066895912>",
        "SILVER": "<:silver:1500229923116155122>",
        "GOLD": "<:gold:1500217761820049419>",
        "PLATINUM": "<:platinum:1500216979817103550>",
        "EMERALD": "<:emerald:1500216736694407318>",
        "DIAMOND": "<:diamond:1500230077072281792>",
        "MASTER": "<:master:1500229921023328449>",
        "GRANDMASTER": "<:grandmaster:1500229922130624512>",
        "CHALLENGER": "<:challenger:1500229924378906865>",
    }
    return icons.get(tier, "▫️")


def delta_text(value):
    if value > 0:
        return f" (+{value})"
    if value < 0:
        return f" ({value})"
    return ""


def recent_results_text(summoner):
    results = []
    for game in range(1, 6):
        remake = getattr(summoner, f"game{game}Remake", False)
        win = getattr(summoner, f"game{game}Win", None)
        if remake:
            results.append("➖")
        elif win is True:
            results.append("✅")
        elif win is False:
            results.append("❌")
        else:
            results.append("▫️")
    return "".join(results)


def leaderboard_embed(summoners, daily=False, date_str=None):
    title = "Solo/Duo Leaderboard"
    if daily:
        title = f"Daily Solo/Duo Leaderboard - {date_str}"

    embed = disnake.Embed(
        title=title,
        colour=disnake.Colour.gold(),
        timestamp=datetime.now()
    )
    embed.set_author(name="The Race")

    summoner_lines = []
    rank_lines = []
    results_lines = []

    for summoner in summoners:
        rank = f"#{summoner.leaderboardPosition}"
        raw_name = summoner.name
        tag = summoner.tagline

        display_name = raw_name
        if len(display_name) > 18:
            display_name = f"{display_name[:15]}..."

        safe_game_name = raw_name.replace(" ", "%20")
        safe_tag = tag.replace(" ", "%20")
        name = f"[{display_name}](https://dpm.lol/{safe_game_name}-{safe_tag})"

        score_delta = summoner.deltaDailyScore if daily else summoner.deltaScore
        position_delta = summoner.deltaDailyLeaderboardPosition if daily else summoner.deltaLeaderboardPosition
        games_delta = summoner.deltaDailyGamesPlayed if daily else summoner.deltaGamesPlayed
        lp_delta = delta_text(score_delta)
        if score_delta == 0 and games_delta:
            lp_delta = " (-0)"

        tier_rank = f"{summoner.tier} {summoner.rank}"
        lp = f"{summoner.leaguePoints} LP"
        line_left = f"**{rank}** {name}"
        line_right = f"{rank_icon(summoner.tier)} {tier_rank} - **{lp}** {lp_delta}".rstrip()
        line_results = recent_results_text(summoner)
        if position_delta > 0:
            line_left += " ▲"
        elif position_delta < 0:
            line_left += " ▼"

        if len("\n".join(summoner_lines + [line_left])) > 1024:
            break
        if len("\n".join(rank_lines + [line_right])) > 1024:
            break
        if len("\n".join(results_lines + [line_results])) > 1024:
            break

        summoner_lines.append(line_left)
        rank_lines.append(line_right)
        results_lines.append(line_results)

    embed.add_field(name="Summoners", value="\n".join(summoner_lines) or "-", inline=True)
    embed.add_field(name="Ranks", value="\n".join(rank_lines) or "-", inline=True)
    embed.add_field(name="Last 5", value="\n".join(results_lines) or "-", inline=True)
    embed.set_footer(text="Updated leaderboard")
    return embed


async def send_or_edit_leaderboard(channel, json_data, summoners, daily=False, date_str=None):
    embed = leaderboard_embed(summoners, daily, date_str)
    message_id = json_data.get("leaderboardMessageId")

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=embed)
            return message.id
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException, ValueError):
            pass

    message = await channel.send(embed=embed)
    return message.id


async def get_discord_channel(channel_id):
    channel = bot.get_channel(channel_id)
    if channel:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (disnake.Forbidden, disnake.NotFound, disnake.HTTPException) as error:
        print(f"Could not fetch Discord channel {channel_id}: {error}")
        return None


def load_json_data():
    return openJsonFile(jsonFile) or {}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def configured_channel_id(json_data, key):
    try:
        return int(json_data.get(key) or discordChannel)
    except (TypeError, ValueError):
        return discordChannel


def leaderboard_channel_id(json_data):
    return configured_channel_id(json_data, "leaderboardChannelId")


def matchmaking_channel_id(json_data):
    return configured_channel_id(json_data, "matchmakingChannelId")


def admin_channel_id(json_data):
    return configured_channel_id(json_data, "adminChannelId")


async def fetch_configured_message(channel_id, message_id):
    if not message_id:
        return None
    channel = await get_discord_channel(channel_id)
    if not channel:
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException, ValueError):
        return None


async def delete_configured_message(channel_id, message_id):
    message = await fetch_configured_message(channel_id, message_id)
    if not message:
        return False
    try:
        await message.delete()
        return True
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
        return False


def can_configure_channels(inter):
    permissions = getattr(inter.author, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


def missing_bot_channel_permissions(channel, bot_member):
    if bot_member is None:
        return ["bot member lookup"]

    permissions = channel.permissions_for(bot_member)
    required_permissions = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History"
    }
    missing = []
    for permission, label in required_permissions.items():
        if not getattr(permissions, permission, False):
            missing.append(label)
    return missing


async def delete_interaction_original_later(inter, delay_seconds=EPHEMERAL_DELETE_SECONDS):
    await asyncio.sleep(delay_seconds)
    try:
        await inter.delete_original_message()
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
        pass


async def send_ephemeral_response(inter, message=None, **kwargs):
    if message is None and "content" in kwargs:
        message = kwargs.pop("content")
    await inter.response.send_message(message, ephemeral=True, **kwargs)
    asyncio.create_task(delete_interaction_original_later(inter))


async def send_ephemeral_followup(inter, message=None, **kwargs):
    if message is None and "content" in kwargs:
        message = kwargs.pop("content")
    try:
        sent_message = await inter.followup.send(message, ephemeral=True, wait=True, **kwargs)
    except TypeError:
        sent_message = await inter.followup.send(message, ephemeral=True, **kwargs)
    if sent_message:
        asyncio.create_task(delete_message_later(sent_message, EPHEMERAL_DELETE_SECONDS))


async def send_ephemeral_inter_send(inter, message=None, **kwargs):
    if message is None and "content" in kwargs:
        message = kwargs.pop("content")
    sent_message = await inter.send(message, ephemeral=True, **kwargs)
    if sent_message:
        asyncio.create_task(delete_message_later(sent_message, EPHEMERAL_DELETE_SECONDS))
    else:
        asyncio.create_task(delete_interaction_original_later(inter))


async def send_ephemeral(inter, message=None, embed=None, view=None):
    if inter.response.is_done():
        await send_ephemeral_followup(inter, message, embed=embed, view=view)
    else:
        await send_ephemeral_response(inter, message, embed=embed, view=view)


async def delete_message_later(message, delay_seconds=60):
    await asyncio.sleep(delay_seconds)
    try:
        await message.delete()
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
        pass


async def send_temporary_public_message(channel, message, delay_seconds=60):
    if not channel or not message:
        return None
    try:
        sent_message = await channel.send(message)
    except (disnake.Forbidden, disnake.HTTPException):
        return None
    asyncio.create_task(delete_message_later(sent_message, delay_seconds))
    return sent_message


def public_matchmaking_announcement(message):
    return bool(message and (message.startswith("Match started") or message.startswith("Captain draft started")))


async def require_admin_interaction(inter):
    if not inter.guild:
        await send_ephemeral(inter, "This action can only be used inside a server.")
        return False
    if not can_configure_channels(inter):
        await send_ephemeral(inter, "You need Manage Server permission to use administration.")
        return False
    return True


def ensure_matchmaking_state(json_data):
    json_data.setdefault("matchmakingQueue", [])
    json_data.setdefault("matchmakingSeparateChannels", False)
    json_data.setdefault("matchmakingSeparateChannelsForced", None)
    json_data.setdefault("matchmakingTeamChannelIds", [])
    json_data.setdefault("matchmakingInProgress", False)
    if json_data.get("matchmakingOddPlayersPolicy") not in MATCHMAKING_ODD_PLAYER_POLICIES:
        json_data["matchmakingOddPlayersPolicy"] = "allow_uneven"
    if json_data.get("matchmakingTeamMode") not in MATCHMAKING_TEAM_MODES:
        json_data["matchmakingTeamMode"] = "random"
    if json_data.get("matchmakingTeamModeForced") not in MATCHMAKING_TEAM_MODES:
        json_data["matchmakingTeamModeForced"] = None
    json_data.setdefault("matchmakingDraft", None)
    return json_data


def ensure_admin_state(json_data):
    ensure_matchmaking_state(json_data)
    json_data.setdefault("discordLinks", {})
    json_data.setdefault("discordLinkRequests", [])
    json_data.setdefault("leaderboardChatCommandsEnabled", False)
    json_data.setdefault("adminMessageId", None)
    json_data.setdefault("leaderboardLastUpdateAt", None)
    json_data.setdefault("leaderboardLastUpdateMode", None)
    json_data.setdefault("leaderboardLastUpdateStatus", None)
    json_data.setdefault("leaderboardLastEstimatedApiCalls", 0)
    json_data.setdefault("lastRiotError", None)
    return json_data


def leaderboard_chat_commands_enabled(json_data):
    return bool(json_data.get("leaderboardChatCommandsEnabled", False))


def effective_matchmaking_separate_channels(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is not None:
        return forced_mode
    return json_data.get("matchmakingSeparateChannels", False)


def forced_mode_text(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is True:
        return "Forced separate channels"
    if forced_mode is False:
        return "Forced same channel"
    return "Unlocked"


def effective_matchmaking_team_mode(json_data):
    forced_mode = json_data.get("matchmakingTeamModeForced")
    if forced_mode in MATCHMAKING_TEAM_MODES:
        return forced_mode
    mode = json_data.get("matchmakingTeamMode")
    return mode if mode in MATCHMAKING_TEAM_MODES else "random"


def team_mode_label(mode):
    return MATCHMAKING_TEAM_MODE_LABELS.get(mode, "Random")


def team_mode_lock_text(json_data):
    forced_mode = json_data.get("matchmakingTeamModeForced")
    if forced_mode in MATCHMAKING_TEAM_MODES:
        return f"Forced {team_mode_label(forced_mode)}"
    return "Unlocked"


def odd_players_policy_label(policy):
    return MATCHMAKING_ODD_PLAYER_POLICY_LABELS.get(policy, "Allow uneven teams")


def effective_odd_players_policy(json_data):
    policy = json_data.get("matchmakingOddPlayersPolicy")
    return policy if policy in MATCHMAKING_ODD_PLAYER_POLICIES else "allow_uneven"


def voice_mode_label(separate_channels):
    return "Separate team channels" if separate_channels else "Same channel"


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
    source = "Autopicked" if autopick else "Picked"
    return True, f"{source} {matchmaking_player_label(picked_player)}."


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


def normalize_tagline(tagline):
    return tagline.replace("#", "").strip()


def find_summoner_key(json_data, name, tagline):
    summoners = json_data.get("summoners") or {}
    summoner_full_name = f"{name}#{normalize_tagline(tagline)}"
    for summoner in summoners:
        if summoner.lower() == summoner_full_name.lower():
            return summoner
    return None


def parse_discord_user_id(value):
    value = str(value).strip()
    for char in ["<", ">", "@", "!"]:
        value = value.replace(char, "")
    return value if value.isdigit() else None


async def discord_user_from_text(guild, value):
    user_id = parse_discord_user_id(value)
    if not user_id:
        return None
    return await get_guild_member(guild, user_id)


def rebuild_discord_links_from_summoners(json_data):
    links = {}
    summoners = json_data.get("summoners") or {}

    for summoner_name, summoner_data in summoners.items():
        discord_user_id = summoner_data.get("discordUserId")
        if not discord_user_id:
            continue

        discord_user_id = str(discord_user_id)
        link = links.setdefault(discord_user_id, {
            "displayName": summoner_data.get("discordDisplayName") or discord_user_id,
            "summoners": [],
            "primarySummoner": None
        })
        if summoner_name not in link["summoners"]:
            link["summoners"].append(summoner_name)
        if summoner_data.get("discordPrimary"):
            link["primarySummoner"] = summoner_name

    for discord_user_id, link in links.items():
        if not link["primarySummoner"] and link["summoners"]:
            link["primarySummoner"] = link["summoners"][0]
            summoners[link["primarySummoner"]]["discordPrimary"] = True

    json_data["discordLinks"] = links
    return json_data


def linked_summoners_for_user(json_data, user_id):
    rebuild_discord_links_from_summoners(json_data)
    link = json_data.get("discordLinks", {}).get(str(user_id), {})
    return link.get("summoners", [])


def primary_summoner_for_user(json_data, user_id):
    rebuild_discord_links_from_summoners(json_data)
    link = json_data.get("discordLinks", {}).get(str(user_id), {})
    primary_summoner = link.get("primarySummoner")
    if primary_summoner in (json_data.get("summoners") or {}):
        return primary_summoner
    summoners = link.get("summoners", [])
    return summoners[0] if summoners else None


def link_summoner_to_discord(json_data, user, summoner_full_name, primary=True):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, f"{summoner_full_name} has not been added"

    user_id = str(user.id)
    display_name = user.display_name
    current_user_id = summoners[summoner_full_name].get("discordUserId")
    if current_user_id and str(current_user_id) != user_id:
        return False, f"{summoner_full_name} is already linked to <@{current_user_id}>"

    link = json_data["discordLinks"].setdefault(user_id, {
        "displayName": display_name,
        "summoners": [],
        "primarySummoner": None
    })
    link["displayName"] = display_name
    if summoner_full_name not in link["summoners"]:
        link["summoners"].append(summoner_full_name)

    summoners[summoner_full_name]["discordUserId"] = user_id
    summoners[summoner_full_name]["discordDisplayName"] = display_name
    summoners[summoner_full_name]["discordLinkedAt"] = utc_now_iso()

    if primary or not link.get("primarySummoner"):
        for linked_summoner in link["summoners"]:
            if linked_summoner in summoners:
                summoners[linked_summoner]["discordPrimary"] = linked_summoner == summoner_full_name
        link["primarySummoner"] = summoner_full_name
    else:
        summoners[summoner_full_name]["discordPrimary"] = False

    json_data["discordLinkRequests"] = [
        request for request in (json_data.get("discordLinkRequests") or [])
        if not isinstance(request, dict) or request.get("summonerFullName") != summoner_full_name
    ]
    return True, f"{summoner_full_name} linked to <@{user_id}>"


def unlink_summoner_from_discord(json_data, summoner_full_name):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, f"{summoner_full_name} has not been added"

    user_id = summoners[summoner_full_name].get("discordUserId")
    if not user_id:
        return False, f"{summoner_full_name} is not linked"

    user_id = str(user_id)
    for key in ["discordUserId", "discordDisplayName", "discordLinkedAt", "discordPrimary"]:
        summoners[summoner_full_name].pop(key, None)

    link = json_data.get("discordLinks", {}).get(user_id)
    if link:
        link["summoners"] = [summoner for summoner in link.get("summoners", []) if summoner != summoner_full_name]
        if link.get("primarySummoner") == summoner_full_name:
            link["primarySummoner"] = link["summoners"][0] if link["summoners"] else None
            if link["primarySummoner"] in summoners:
                summoners[link["primarySummoner"]]["discordPrimary"] = True
        if not link["summoners"]:
            del json_data["discordLinks"][user_id]

    return True, f"{summoner_full_name} unlinked from <@{user_id}>"


def set_primary_summoner_for_user(json_data, user, summoner_full_name):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    user_id = str(user.id)
    link = json_data.get("discordLinks", {}).get(user_id)
    if not link or summoner_full_name not in link.get("summoners", []):
        return False, f"{summoner_full_name} is not linked to <@{user_id}>"

    for linked_summoner in link["summoners"]:
        if linked_summoner in summoners:
            summoners[linked_summoner]["discordPrimary"] = linked_summoner == summoner_full_name
    link["displayName"] = user.display_name
    link["primarySummoner"] = summoner_full_name
    return True, f"{summoner_full_name} set as primary for <@{user_id}>"


def discord_display_name(user):
    return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)


def discord_link_request_id(user_id, summoner_full_name):
    return f"{user_id}:{summoner_full_name.lower()}"


def discord_link_requests(json_data):
    ensure_admin_state(json_data)
    requests = []
    seen = set()
    summoners = json_data.get("summoners") or {}
    for request in json_data.get("discordLinkRequests") or []:
        if not isinstance(request, dict):
            continue
        user_id = str(request.get("discordUserId") or "")
        summoner = request.get("summonerFullName")
        if not user_id or summoner not in summoners:
            continue
        if summoners[summoner].get("discordUserId"):
            continue
        request_id = request.get("id") or discord_link_request_id(user_id, summoner)
        if request_id in seen:
            continue
        request["id"] = request_id
        request["discordUserId"] = user_id
        request["summonerFullName"] = summoner
        request.setdefault("discordDisplayName", user_id)
        request.setdefault("requestedAt", utc_now_iso())
        requests.append(request)
        seen.add(request_id)
    json_data["discordLinkRequests"] = requests
    return requests


def find_discord_link_request(json_data, request_id):
    for request in discord_link_requests(json_data):
        if request.get("id") == request_id:
            return request
    return None


def remove_discord_link_request(json_data, request_id):
    requests = discord_link_requests(json_data)
    before = len(requests)
    json_data["discordLinkRequests"] = [request for request in requests if request.get("id") != request_id]
    return len(json_data["discordLinkRequests"]) != before


def request_discord_link(json_data, user, summoner_full_name):
    ensure_admin_state(json_data)
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, f"{summoner_full_name} has not been added"

    user_id = str(user.id)
    current_user_id = summoners[summoner_full_name].get("discordUserId")
    if current_user_id and str(current_user_id) == user_id:
        return False, f"{summoner_full_name} is already linked to your Discord."
    if current_user_id:
        return False, f"{summoner_full_name} is already linked to <@{current_user_id}>."

    requests = discord_link_requests(json_data)
    request_id = discord_link_request_id(user_id, summoner_full_name)
    for request in requests:
        if request.get("summonerFullName", "").lower() != summoner_full_name.lower():
            continue
        if request.get("discordUserId") == user_id:
            request["discordDisplayName"] = discord_display_name(user)
            request["requestedAt"] = utc_now_iso()
            return True, f"Your link request for {summoner_full_name} was refreshed. An admin must approve it."
        return False, f"{summoner_full_name} already has a pending link request."

    requests.append({
        "id": request_id,
        "discordUserId": user_id,
        "discordDisplayName": discord_display_name(user),
        "summonerFullName": summoner_full_name,
        "requestedAt": utc_now_iso()
    })
    json_data["discordLinkRequests"] = requests
    return True, f"Your link request for {summoner_full_name} was sent. An admin must approve it."


def approve_discord_link_request(json_data, request_id, user):
    request = find_discord_link_request(json_data, request_id)
    if not request:
        return False, "That link request is no longer pending."

    primary = not bool(linked_summoners_for_user(json_data, user.id))
    success, message = link_summoner_to_discord(json_data, user, request["summonerFullName"], primary=primary)
    if success:
        remove_discord_link_request(json_data, request_id)
    return success, message


def primary_summoner_queue_data(json_data, user_id):
    summoner_name = primary_summoner_for_user(json_data, user_id)
    if not summoner_name:
        return {}

    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    return {
        "summonerFullName": summoner_name,
        "puuid": summoner_data.get("puuid"),
        "platform": summoner_data.get("platform"),
        "region": summoner_data.get("region"),
        "score": summoner_data.get("score", 0),
        "tier": summoner_data.get("tier"),
        "rank": summoner_data.get("rank"),
        "leaguePoints": summoner_data.get("leaguePoints")
    }


def format_cached_rank(summoner_data):
    tier = summoner_data.get("tier")
    rank = summoner_data.get("rank")
    league_points = summoner_data.get("leaguePoints")
    if not tier or rank is None or league_points is None:
        return "No cached ranked data yet."
    return f"{rank_icon(tier)} **{tier} {rank} - {league_points} LP**"


def format_signed_delta(value):
    if value is None:
        return "-"
    if value > 0:
        return f"+{value}"
    return str(value)


def cached_daily_change_text(summoner_data):
    score = summoner_data.get("score")
    daily_score = summoner_data.get("dailyScore")
    games_played = summoner_data.get("gamesPlayed")
    daily_games_played = summoner_data.get("dailyGamesPlayed")
    position = summoner_data.get("leaderboardPosition")
    daily_position = summoner_data.get("dailyLeaderboardPosition")

    score_delta = score - daily_score if score is not None and daily_score is not None else None
    games_delta = games_played - daily_games_played if games_played is not None and daily_games_played is not None else None
    position_delta = daily_position - position if position is not None and daily_position is not None else None
    if position_delta is None:
        position_text = "-"
    elif position_delta > 0:
        position_text = f"+{position_delta} positions"
    elif position_delta < 0:
        position_text = f"{position_delta} positions"
    else:
        position_text = "No position change"

    return (
        f"LP/score: **{format_signed_delta(score_delta)}**\n"
        f"Games: **{format_signed_delta(games_delta)}**\n"
        f"Leaderboard: **{position_text}**"
    )


def format_match_duration(seconds):
    if not seconds:
        return "-"
    minutes = int(seconds) // 60
    remaining_seconds = int(seconds) % 60
    return f"{minutes}:{remaining_seconds:02d}"


def format_compact_damage(value):
    if value is None:
        return "-"
    return f"{round(value / 1000, 1)}k"


def cached_match_participant(match_data, puuid):
    for participant in match_data.get("info", {}).get("participants", []):
        if participant.get("puuid") == puuid:
            return participant
    return None


def cached_recent_games(json_data, summoner_name, limit=5):
    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    puuid = summoner_data.get("puuid")
    games = []
    for match_id in summoner_data.get("recentMatchIds", [])[:limit]:
        match_data = (json_data.get("matchData") or {}).get(match_id)
        if not match_data or not puuid:
            games.append({"matchId": match_id, "cached": False})
            continue

        participant = cached_match_participant(match_data, puuid)
        if not participant:
            games.append({"matchId": match_id, "cached": False})
            continue

        info = match_data.get("info", {})
        games.append({
            "matchId": match_id,
            "cached": True,
            "champion": participant.get("championName", "Unknown"),
            "kills": participant.get("kills", 0),
            "deaths": participant.get("deaths", 0),
            "assists": participant.get("assists", 0),
            "win": participant.get("win"),
            "remake": participant.get("gameEndedInEarlySurrender", False) or info.get("gameDuration", 0) < 300,
            "damage": participant.get("totalDamageDealtToChampions"),
            "duration": info.get("gameDuration"),
            "creation": info.get("gameCreation"),
            "position": participant.get("teamPosition") or participant.get("individualPosition") or participant.get("lane"),
            "cs": participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0),
            "vision": participant.get("visionScore"),
            "killParticipation": participant.get("challenges", {}).get("killParticipation"),
        })
    return games


def game_result_icon(game):
    if not game.get("cached"):
        return "▫️"
    if game.get("remake"):
        return "➖"
    return "✅" if game.get("win") else "❌"


def format_cached_game_line(index, game):
    if not game.get("cached"):
        return f"**{index}.** ▫️ Match data not cached"

    kda = f"{game.get('kills', 0)}/{game.get('deaths', 0)}/{game.get('assists', 0)}"
    damage = format_compact_damage(game.get("damage"))
    duration = format_match_duration(game.get("duration"))
    position = game.get("position") or "-"
    cs = game.get("cs", "-")
    vision = game.get("vision", "-")
    return (
        f"**{index}.** {game_result_icon(game)} **{game.get('champion', 'Unknown')}** "
        f"({position}) - {kda} - {damage} dmg - {cs} CS - {vision} vision - {duration}"
    )


def cached_games_summary(games):
    cached = [game for game in games if game.get("cached")]
    if not cached:
        return "No cached match details available."

    wins = len([game for game in cached if game.get("win") and not game.get("remake")])
    losses = len([game for game in cached if game.get("win") is False and not game.get("remake")])
    remakes = len([game for game in cached if game.get("remake")])
    kills = sum(game.get("kills", 0) for game in cached)
    deaths = sum(game.get("deaths", 0) for game in cached)
    assists = sum(game.get("assists", 0) for game in cached)
    damage_values = [game.get("damage") for game in cached if game.get("damage") is not None]
    kill_participation_values = [
        game.get("killParticipation")
        for game in cached
        if game.get("killParticipation") is not None
    ]
    avg_damage = sum(damage_values) / len(damage_values) if damage_values else None
    avg_kill_participation = (
        sum(kill_participation_values) / len(kill_participation_values)
        if kill_participation_values else None
    )
    kda_ratio = (kills + assists) / max(1, deaths)
    kp_text = f"{avg_kill_participation * 100:.0f}%" if avg_kill_participation is not None else "-"

    return (
        f"Results: **{wins}W / {losses}L / {remakes}R**\n"
        f"Total KDA: **{kills}/{deaths}/{assists}** ({kda_ratio:.2f})\n"
        f"Avg damage: **{format_compact_damage(avg_damage)}**\n"
        f"Avg kill participation: **{kp_text}**"
    )


def personal_report_embed(json_data, member):
    rebuild_discord_links_from_summoners(json_data)
    summoner_name = primary_summoner_for_user(json_data, member.id)
    if not summoner_name:
        return None

    summoners = json_data.get("summoners") or {}
    summoner_data = summoners.get(summoner_name, {})
    linked_summoners = linked_summoners_for_user(json_data, member.id)
    games = cached_recent_games(json_data, summoner_name)
    leaderboard_position = summoner_data.get("leaderboardPosition")
    games_played = summoner_data.get("gamesPlayed")
    last_update = json_data.get("leaderboardLastUpdateAt", "Unknown")
    last_status = json_data.get("leaderboardLastUpdateStatus", "unknown")

    embed = disnake.Embed(
        title=f"{member.display_name} report",
        description=f"Primary account: **{summoner_name}**",
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )
    embed.set_author(name="The Race")
    embed.add_field(name="Rank", value=format_cached_rank(summoner_data), inline=False)
    embed.add_field(name="Leaderboard", value=f"Position: **#{leaderboard_position}**\nScore: **{summoner_data.get('score', 0)}**", inline=True)
    embed.add_field(name="Ranked games", value=f"Total cached games played: **{games_played if games_played is not None else '-'}**", inline=True)
    embed.add_field(name="Today's cached change", value=cached_daily_change_text(summoner_data), inline=True)

    if linked_summoners:
        linked_text = "\n".join(
            f"{'•' if linked == summoner_name else '-'} {linked}"
            for linked in linked_summoners[:8]
        )
        if len(linked_summoners) > 8:
            linked_text += f"\n...and {len(linked_summoners) - 8} more."
        embed.add_field(name="Linked accounts", value=linked_text[:1024], inline=False)

    embed.add_field(name="Last cached games", value=cached_games_summary(games), inline=False)
    if games:
        game_lines = "\n".join(format_cached_game_line(index, game) for index, game in enumerate(games, start=1))
        embed.add_field(name="Recent match detail", value=game_lines[:1024], inline=False)
    else:
        embed.add_field(name="Recent match detail", value="No recent match IDs cached yet.", inline=False)

    embed.set_footer(text=f"Cache only. Last leaderboard update: {last_update} ({last_status})")
    return embed


def compact_number(value):
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def percent_text(value):
    if value is None:
        return "-"
    return f"{value * 100:.0f}%"


def participant_name(participant):
    game_name = participant.get("riotIdGameName") or participant.get("summonerName") or "Unknown"
    tagline = participant.get("riotIdTagline")
    if tagline:
        return f"{game_name}#{tagline}"
    return game_name


def participant_position(participant):
    return participant.get("teamPosition") or participant.get("individualPosition") or participant.get("lane") or "-"


def participant_cs(participant):
    return participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0)


def participant_kda_ratio(participant):
    kills = participant.get("kills", 0)
    deaths = participant.get("deaths", 0)
    assists = participant.get("assists", 0)
    return (kills + assists) / max(1, deaths)


def participant_stat_value(participant, stat):
    challenges = participant.get("challenges", {}) or {}
    if stat in challenges:
        return challenges.get(stat)
    return participant.get(stat)


def match_mvp_participant_id(participants):
    stat_names = ["killParticipation", "kda", "totalDamageDealtToChampions", "damageDealtToBuildings", "totalDamageTaken", "goldPerMinute", "visionScore"]
    weights = {
        "killParticipation": 1,
        "kda": 1.2,
        "totalDamageDealtToChampions": 1,
        "damageDealtToBuildings": 0.7,
        "totalDamageTaken": 0.7,
        "goldPerMinute": 1,
        "visionScore": 1,
    }
    means = {}
    stds = {}
    for stat in stat_names:
        values = [participant_stat_value(participant, stat) for participant in participants]
        values = [value for value in values if isinstance(value, (int, float))]
        if not values:
            means[stat] = None
            stds[stat] = None
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means[stat] = mean
        stds[stat] = math.sqrt(variance)

    best_participant_id = None
    best_score = float("-inf")
    for participant in participants:
        score = 0
        for stat in stat_names:
            value = participant_stat_value(participant, stat)
            mean = means.get(stat)
            std = stds.get(stat)
            if not isinstance(value, (int, float)) or mean is None or not std:
                continue
            score += ((value - mean) / std) * weights[stat]
        if score > best_score:
            best_score = score
            best_participant_id = participant.get("participantId")
    return best_participant_id


def participant_badges(participant, leaders):
    participant_id = participant.get("participantId")
    badges = []
    if participant_id == leaders.get("mvp"):
        badges.append("MVP")
    if participant_id == leaders.get("damage"):
        badges.append("DMG")
    if participant_id == leaders.get("gold"):
        badges.append("GOLD")
    if participant_id == leaders.get("vision"):
        badges.append("VIS")
    if participant_id == leaders.get("kda"):
        badges.append("KDA")
    return f" [{' '.join(badges)}]" if badges else ""


def match_leaders(participants):
    if not participants:
        return {}
    return {
        "mvp": match_mvp_participant_id(participants),
        "damage": max(participants, key=lambda p: p.get("totalDamageDealtToChampions", 0)).get("participantId"),
        "gold": max(participants, key=lambda p: p.get("goldEarned", 0)).get("participantId"),
        "vision": max(participants, key=lambda p: p.get("visionScore", 0)).get("participantId"),
        "kda": max(participants, key=participant_kda_ratio).get("participantId"),
    }


def format_scoreboard_line(participant, leaders):
    kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
    kp = percent_text((participant.get("challenges", {}) or {}).get("killParticipation"))
    name = participant_name(participant)
    if len(name) > 22:
        name = f"{name[:19]}..."
    champion = participant.get("championName", "Unknown")
    if len(champion) > 13:
        champion = f"{champion[:10]}..."
    return (
        f"`{champion:<13}` **{name}** ({participant_position(participant)}) "
        f"{kda} | {participant_cs(participant)} CS | {compact_number(participant.get('goldEarned'))}g | "
        f"{format_compact_damage(participant.get('totalDamageDealtToChampions'))} dmg | "
        f"{participant.get('visionScore', 0)} vis | KP {kp}{participant_badges(participant, leaders)}"
    )


def personal_match_summary(participant, duration_seconds):
    minutes = max(1, duration_seconds / 60) if duration_seconds else 1
    damage_per_minute = participant.get("totalDamageDealtToChampions", 0) / minutes
    gold_per_minute = participant.get("goldEarned", 0) / minutes
    cs_per_minute = participant_cs(participant) / minutes
    vision_per_minute = participant.get("visionScore", 0) / minutes
    kp = percent_text((participant.get("challenges", {}) or {}).get("killParticipation"))
    return (
        f"KP: **{kp}**\n"
        f"Damage/min: **{damage_per_minute:.0f}**\n"
        f"Gold/min: **{gold_per_minute:.0f}**\n"
        f"CS/min: **{cs_per_minute:.1f}**\n"
        f"Vision/min: **{vision_per_minute:.1f}**"
    )


def cached_personal_game_context(json_data, summoner_name, game_index):
    games = cached_recent_games(json_data, summoner_name)
    if game_index < 0 or game_index >= len(games):
        return None, "That match is no longer available in the recent match cache."
    game = games[game_index]
    if not game.get("cached"):
        return None, "That match is not cached yet."
    match_data = (json_data.get("matchData") or {}).get(game.get("matchId"))
    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    participant = cached_match_participant(match_data or {}, summoner_data.get("puuid"))
    if not match_data or not participant:
        return None, "That cached match does not contain this player's participant data."
    return {
        "games": games,
        "game": game,
        "match_data": match_data,
        "participant": participant,
        "summoner_data": summoner_data,
    }, None


def match_detail_embed(json_data, summoner_name, game_index):
    context, error = cached_personal_game_context(json_data, summoner_name, game_index)
    if error:
        return None, error

    game = context["game"]
    match_data = context["match_data"]
    participant = context["participant"]
    info = match_data.get("info", {})
    participants = info.get("participants", [])
    leaders = match_leaders(participants)
    result = "Remake" if game.get("remake") else "Victory" if participant.get("win") else "Defeat"
    kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
    duration = format_match_duration(info.get("gameDuration"))
    match_id = game.get("matchId")

    embed = disnake.Embed(
        title=f"Game {game_index + 1}: {participant.get('championName', 'Unknown')} - {result}",
        description=f"Match: `{match_id}`\nPlayer: **{summoner_name}**\nKDA: **{kda}** - Duration: **{duration}**",
        colour=disnake.Colour.green() if participant.get("win") else disnake.Colour.red(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Personal performance", value=personal_match_summary(participant, info.get("gameDuration", 0)), inline=True)

    timeline_cached = match_id in (json_data.get("matchTimelineData") or {})
    embed.add_field(
        name="Data freshness",
        value=f"Match cached: **Yes**\nTimeline cached: **{'Yes' if timeline_cached else 'No'}**",
        inline=True
    )

    for team_id, team_name in [(100, "Blue side"), (200, "Red side")]:
        team_participants = [p for p in participants if p.get("teamId") == team_id]
        lines = [format_scoreboard_line(p, leaders) for p in team_participants]
        embed.add_field(name=team_name, value=("\n".join(lines) or "-")[:1024], inline=False)

    embed.set_footer(text="Badges: MVP, top damage, top gold, top vision, best KDA.")
    return embed, None


def team_objective_text(team):
    objectives = team.get("objectives", {}) or {}
    return (
        f"Towers {objectives.get('tower', {}).get('kills', 0)}, "
        f"Dragons {objectives.get('dragon', {}).get('kills', 0)}, "
        f"Herald {objectives.get('riftHerald', {}).get('kills', 0)}, "
        f"Baron {objectives.get('baron', {}).get('kills', 0)}, "
        f"Inhib {objectives.get('inhibitor', {}).get('kills', 0)}"
    )


def compare_teams_embed(json_data, summoner_name, game_index):
    context, error = cached_personal_game_context(json_data, summoner_name, game_index)
    if error:
        return None, error

    match_data = context["match_data"]
    teams = {team.get("teamId"): team for team in match_data.get("info", {}).get("teams", [])}
    participants = match_data.get("info", {}).get("participants", [])
    embed = disnake.Embed(
        title=f"Game {game_index + 1}: team comparison",
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )
    for team_id, team_name in [(100, "Blue side"), (200, "Red side")]:
        team_participants = [p for p in participants if p.get("teamId") == team_id]
        kills = sum(p.get("kills", 0) for p in team_participants)
        deaths = sum(p.get("deaths", 0) for p in team_participants)
        assists = sum(p.get("assists", 0) for p in team_participants)
        gold = sum(p.get("goldEarned", 0) for p in team_participants)
        damage = sum(p.get("totalDamageDealtToChampions", 0) for p in team_participants)
        vision = sum(p.get("visionScore", 0) for p in team_participants)
        result = "Win" if (teams.get(team_id) or {}).get("win") else "Loss"
        embed.add_field(
            name=f"{team_name} - {result}",
            value=(
                f"KDA: **{kills}/{deaths}/{assists}**\n"
                f"Gold: **{compact_number(gold)}**\n"
                f"Damage: **{compact_number(damage)}**\n"
                f"Vision: **{vision}**\n"
                f"{team_objective_text(teams.get(team_id) or {})}"
            ),
            inline=True
        )
    embed.set_footer(text="Objective values are from cached Riot match data when available.")
    return embed, None


def personal_report_view(json_data, summoner_name):
    games = cached_recent_games(json_data, summoner_name)
    cached_games = [game for game in games if game.get("cached")]
    if not cached_games:
        return None
    return PersonalReportView(summoner_name, games)


async def fetch_match_timeline(json_data, summoner_name, match_id):
    json_data.setdefault("matchTimelineData", {})
    if match_id in json_data["matchTimelineData"]:
        return True, json_data["matchTimelineData"][match_id], "Timeline cached."

    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    region = summoner_data.get("region")
    if not region:
        return False, None, "This summoner does not have a Riot region configured."
    if not riotApKey:
        return False, None, "RIOT_API_KEY is not configured."

    ok, timeline_data, retry_after = riot_get(
        f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline?api_key={riotApKey}",
        f"match timeline {match_id}"
    )
    if not ok or not timeline_data:
        if retry_after:
            return False, None, f"Riot is rate limiting requests. Try again in {round(retry_after)} seconds."
        return False, None, "The Riot timeline request failed."

    latest_json_data = ensure_admin_state(load_json_data())
    latest_json_data.setdefault("matchTimelineData", {})
    latest_json_data["matchTimelineData"][match_id] = timeline_data
    writeToJsonFile(jsonFile, latest_json_data)
    return True, timeline_data, "Timeline fetched now."


class PersonalMatchButton(disnake.ui.Button):
    def __init__(self, summoner_name, game, index):
        label = f"Game {index + 1}"
        if game.get("cached"):
            label = f"{label}: {game.get('champion', 'Unknown')}"
        super().__init__(
            label=label[:80],
            style=disnake.ButtonStyle.gray,
            row=index // 3,
            disabled=not game.get("cached")
        )
        self.summoner_name = summoner_name
        self.game_index = index

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_admin_state(load_json_data())
        embed, error = match_detail_embed(json_data, self.summoner_name, self.game_index)
        if error:
            await send_ephemeral_response(inter, error)
            return
        await send_ephemeral_response(inter, embed=embed, view=MatchDetailView(self.summoner_name, self.game_index))


class PersonalReportView(disnake.ui.View):
    def __init__(self, summoner_name, games):
        super().__init__(timeout=180)
        for index, game in enumerate(games[:5]):
            if game.get("cached"):
                self.add_item(PersonalMatchButton(summoner_name, game, index))


class GoldGraphButton(disnake.ui.Button):
    def __init__(self, summoner_name, game_index):
        super().__init__(label="Gold graph", style=disnake.ButtonStyle.green, row=0)
        self.summoner_name = summoner_name
        self.game_index = game_index

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        context, error = cached_personal_game_context(json_data, self.summoner_name, self.game_index)
        if error:
            await send_ephemeral_followup(inter, error)
            return

        match_id = context["game"]["matchId"]
        ok, timeline_data, status = await fetch_match_timeline(json_data, self.summoner_name, match_id)
        if not ok:
            await send_ephemeral_followup(inter, status)
            return

        image_buffer = generateGoldGraphImage(context["match_data"], timeline_data)
        if not image_buffer:
            await send_ephemeral_followup(inter, "No timeline gold data was available for this match.")
            return

        file = disnake.File(image_buffer, filename=f"{match_id}-gold.png")
        await send_ephemeral_followup(inter, content=status, file=file)


class CompareTeamsButton(disnake.ui.Button):
    def __init__(self, summoner_name, game_index):
        super().__init__(label="Compare teams", style=disnake.ButtonStyle.blurple, row=0)
        self.summoner_name = summoner_name
        self.game_index = game_index

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_admin_state(load_json_data())
        embed, error = compare_teams_embed(json_data, self.summoner_name, self.game_index)
        if error:
            await send_ephemeral_response(inter, error)
            return
        await inter.response.edit_message(embed=embed, view=MatchDetailView(self.summoner_name, self.game_index))


class MatchNavigationButton(disnake.ui.Button):
    def __init__(self, summoner_name, game_index, direction, disabled):
        label = "Prev match" if direction < 0 else "Next match"
        super().__init__(label=label, style=disnake.ButtonStyle.gray, row=1, disabled=disabled)
        self.summoner_name = summoner_name
        self.game_index = game_index
        self.direction = direction

    async def callback(self, inter: disnake.MessageInteraction):
        next_index = self.game_index + self.direction
        json_data = ensure_admin_state(load_json_data())
        embed, error = match_detail_embed(json_data, self.summoner_name, next_index)
        if error:
            await send_ephemeral_response(inter, error)
            return
        await inter.response.edit_message(embed=embed, view=MatchDetailView(self.summoner_name, next_index))


class MatchDetailView(disnake.ui.View):
    def __init__(self, summoner_name, game_index):
        super().__init__(timeout=180)
        json_data = ensure_admin_state(load_json_data())
        games = cached_recent_games(json_data, summoner_name)
        previous_index = game_index - 1
        next_index = game_index + 1
        previous_disabled = previous_index < 0 or previous_index >= len(games) or not games[previous_index].get("cached")
        next_disabled = next_index < 0 or next_index >= len(games) or not games[next_index].get("cached")
        self.add_item(GoldGraphButton(summoner_name, game_index))
        self.add_item(CompareTeamsButton(summoner_name, game_index))
        self.add_item(MatchNavigationButton(summoner_name, game_index, -1, previous_disabled))
        self.add_item(MatchNavigationButton(summoner_name, game_index, 1, next_disabled))


def format_linked_accounts_summary(json_data):
    rebuild_discord_links_from_summoners(json_data)
    links = json_data.get("discordLinks", {})
    if not links:
        return "No linked accounts configured."

    lines = []
    for user_id, link in [item for item in links.items()][:10]:
        primary = link.get("primarySummoner") or "-"
        count = len(link.get("summoners", []))
        lines.append(f"<@{user_id}> - {count} account(s), primary: **{primary}**")
    if len(links) > 10:
        lines.append(f"...and {len(links) - 10} more.")
    return "\n".join(lines)


def format_discord_link_requests_summary(json_data):
    requests = discord_link_requests(json_data)
    if not requests:
        return "No pending link requests."

    lines = []
    for request in requests[:10]:
        user_id = request.get("discordUserId")
        summoner = request.get("summonerFullName")
        display_name = request.get("discordDisplayName") or user_id
        lines.append(f"<@{user_id}> ({display_name}) - **{summoner}**")
    if len(requests) > 10:
        lines.append(f"...and {len(requests) - 10} more.")
    return "\n".join(lines)


def format_summoner_summary(json_data):
    summoners = [summoner for summoner in (json_data.get("summoners") or {}).keys()]
    if not summoners:
        return "No summoners configured."

    visible_summoners = summoners[:10]
    summary = "\n".join(f"- {summoner}" for summoner in visible_summoners)
    if len(summoners) > len(visible_summoners):
        summary += f"\n...and {len(summoners) - len(visible_summoners)} more."
    return summary


def format_matchmaking_queue(json_data):
    queue = json_data.get("matchmakingQueue", [])
    if not queue:
        return "No players in queue."

    players = []
    for index, player in enumerate(queue, start=1):
        voice = f"<#{player['voiceChannelId']}>" if player.get("voiceChannelId") else "No voice channel"
        summoner = player.get("summonerFullName") or "Unlinked"
        players.append(f"**{index}.** <@{player['userId']}> - {summoner} - {voice}")
    return "\n".join(players)


def estimate_leaderboard_api_calls(json_data):
    summoners = json_data.get("summoners") or {}
    estimated_calls = len(summoners)
    now = datetime.now().timestamp()
    high_elo_cache = json_data.get("highEloCache") or {}
    for cache in high_elo_cache.values():
        if now - cache.get("timestamp", 0) >= 600:
            estimated_calls += 3
    return estimated_calls


def set_leaderboard_runtime_status(json_data, mode, status, estimated_calls, last_error=None):
    latest_json_data = load_json_data()
    latest_json_data["leaderboardLastUpdateAt"] = utc_now_iso()
    latest_json_data["leaderboardLastUpdateMode"] = mode
    latest_json_data["leaderboardLastUpdateStatus"] = status
    latest_json_data["leaderboardLastEstimatedApiCalls"] = estimated_calls
    if last_error:
        latest_json_data["lastRiotError"] = {
            "timestamp": utc_now_iso(),
            "context": "leaderboard",
            "summary": last_error
        }
    writeToJsonFile(jsonFile, latest_json_data)
    return latest_json_data


def format_log_event(event):
    timestamp = event.get("timestamp", "-")
    timestamp = timestamp.replace("T", " ")[:19]
    actor = event.get("actorName") or event.get("actorId") or "system"
    status = event.get("status", "info")
    summary = event.get("summary", "")
    return f"`{timestamp}` **{event.get('event', 'event')}** [{status}] {actor}: {summary}"[:1000]


def audit_event_category(event):
    event_name = event.get("event", "")
    for category, prefixes in AUDIT_CATEGORY_PREFIXES.items():
        if any(event_name.startswith(prefix) for prefix in prefixes):
            return category
    return "other"


def parse_audit_timestamp(event):
    timestamp = event.get("timestamp")
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_audit_events(category=None, actor_query=None, since=None, limit=10):
    events = read_audit_events()
    if category:
        events = [event for event in events if audit_event_category(event) == category]
    if actor_query:
        query = actor_query.lower()
        events = [
            event for event in events
            if query in str(event.get("actorId", "")).lower()
            or query in str(event.get("actorName", "")).lower()
        ]
    if since:
        events = [
            event for event in events
            if (parse_audit_timestamp(event) or datetime.min.replace(tzinfo=timezone.utc)) >= since
        ]
    if limit:
        return events[-limit:]
    return events


def audit_logs_embed(title="Audit logs", category=None, actor_query=None, limit=10):
    events = filter_audit_events(category=category, actor_query=actor_query, limit=limit)
    embed = disnake.Embed(
        title=title,
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    if category:
        embed.add_field(name="Category", value=AUDIT_CATEGORY_LABELS.get(category, category), inline=True)
    if actor_query:
        embed.add_field(name="Actor filter", value=actor_query[:100], inline=True)
    if not events:
        embed.description = "No matching audit logs found."
        return embed

    value = "\n".join(format_log_event(event) for event in events)
    embed.description = value[-4000:]
    return embed


def audit_summary_24h_embed():
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    events = filter_audit_events(since=since, limit=None)
    error_events = [event for event in events if event.get("status") == "error"]
    critical_events = [
        event for event in events
        if audit_event_category(event) in ["admin", "operations", "leaderboard", "matchmaking"]
    ]
    category_counts = {}
    for event in events:
        category = audit_event_category(event)
        category_counts[category] = category_counts.get(category, 0) + 1

    embed = disnake.Embed(
        title="Audit summary - last 24h",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Total events", value=str(len(events)), inline=True)
    embed.add_field(name="Errors", value=str(len(error_events)), inline=True)
    embed.add_field(name="Critical/admin events", value=str(len(critical_events)), inline=True)

    if category_counts:
        category_lines = [
            f"**{AUDIT_CATEGORY_LABELS.get(category, category.title())}:** {count}"
            for category, count in sorted(category_counts.items(), key=lambda item: item[0])
        ]
        embed.add_field(name="By category", value="\n".join(category_lines)[:1024], inline=False)
    else:
        embed.add_field(name="By category", value="No audit events in the last 24h.", inline=False)

    if error_events:
        embed.add_field(name="Recent errors", value="\n".join(format_log_event(event) for event in error_events[-5:])[-1024:], inline=False)
    return embed


def matchmaking_embed(json_data):
    ensure_matchmaking_state(json_data)
    queue = json_data["matchmakingQueue"]
    separate_channels = effective_matchmaking_separate_channels(json_data)
    separate_mode_text = forced_mode_text(json_data)
    team_mode = effective_matchmaking_team_mode(json_data)
    team_mode_text = team_mode_label(team_mode)
    team_mode_lock = team_mode_lock_text(json_data)
    odd_policy = effective_odd_players_policy(json_data)
    draft = json_data.get("matchmakingDraft")
    ready_text = "Ready to start" if len(queue) >= 2 else "Waiting for at least 2 players"
    if draft:
        ready_text = "Captain draft in progress"
    fallback_text = ""
    if queue and team_mode == "balanced_rank" and not has_valid_player_scores(queue):
        fallback_text = "\nBalance fallback: **random teams** (no valid scores cached)"

    embed = disnake.Embed(
        title="Matchmaking",
        description=(
            f"{ready_text}\n"
            f"Players: **{len(queue)}/10**\n"
            f"Team mode: **{team_mode_text}** ({team_mode_lock})\n"
            f"Voice: **{voice_mode_label(separate_channels)}** ({separate_mode_text})\n"
            f"Odd players: **{odd_players_policy_label(odd_policy)}**"
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
        embed.add_field(name="Turn", value=f"<@{turn}> must pick a player." if turn else "Draft is finishing.", inline=False)
        embed.add_field(name="Team 1", value=format_team(team_one, neutral_score), inline=True)
        embed.add_field(name="Team 2", value=format_team(team_two, neutral_score), inline=True)
        embed.add_field(name="Remaining players", value=format_team(remaining, neutral_score), inline=False)
    elif queue:
        players = []
        for index, player in enumerate(queue, start=1):
            user = f"<@{player['userId']}>"
            voice = f"<#{player['voiceChannelId']}>" if player.get("voiceChannelId") else "No voice channel"
            summoner = player.get("summonerFullName") or "Unlinked"
            players.append(f"**{index}.** {user} - {summoner} - {voice}")
        embed.add_field(name="Current players", value="\n".join(players), inline=False)
    else:
        embed.add_field(name="Current players", value="No players in queue.", inline=False)

    embed.add_field(
        name="Controls",
        value="Use the buttons below to join, leave, open private settings, pick during captain draft, or start the match.",
        inline=False
    )
    embed.set_footer(text="Join requires being in a voice channel.")
    return embed


def admin_embed(json_data):
    ensure_admin_state(json_data)
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    queue = json_data.get("matchmakingQueue", [])
    linked_accounts = json_data.get("discordLinks", {})
    leaderboard_channel = leaderboard_channel_id(json_data)
    matchmaking_channel = matchmaking_channel_id(json_data)
    admin_channel = admin_channel_id(json_data)

    embed = disnake.Embed(
        title="Administration",
        description="Use the buttons below to open private administration panels.",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Leaderboard channel", value=f"<#{leaderboard_channel}>", inline=True)
    embed.add_field(name="Matchmaking channel", value=f"<#{matchmaking_channel}>", inline=True)
    embed.add_field(name="Admin channel", value=f"<#{admin_channel}>", inline=True)
    embed.add_field(name="Leaderboard users", value=str(len(summoners)), inline=True)
    embed.add_field(name="Linked Discord users", value=str(len(linked_accounts)), inline=True)
    embed.add_field(name="Matchmaking queue", value=f"{len(queue)}/10", inline=True)
    embed.add_field(name="Voice mode", value=f"{voice_mode_label(effective_matchmaking_separate_channels(json_data))} ({forced_mode_text(json_data)})", inline=True)
    embed.add_field(name="Team mode", value=f"{team_mode_label(effective_matchmaking_team_mode(json_data))} ({team_mode_lock_text(json_data)})", inline=True)
    embed.add_field(name="Odd players", value=odd_players_policy_label(effective_odd_players_policy(json_data)), inline=True)
    embed.add_field(name="Leaderboard chat commands", value="Enabled" if leaderboard_chat_commands_enabled(json_data) else "Disabled", inline=True)
    embed.add_field(name="Leaderboard status", value=json_data.get("leaderboardLastUpdateStatus") or "Unknown", inline=True)
    embed.set_footer(text="Administration actions require Manage Server.")
    return embed


def status_admin_embed(json_data):
    ensure_admin_state(json_data)
    backoff_remaining = int(riotBackoffRemaining())
    if backoff_remaining > 0:
        backoff_until = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
        backoff_text = f"Active until {backoff_until} ({backoff_remaining}s remaining)"
    else:
        backoff_text = "Inactive"

    last_update = json_data.get("leaderboardLastUpdateAt") or "Never"
    update_mode = json_data.get("leaderboardLastUpdateMode") or "-"
    update_status = json_data.get("leaderboardLastUpdateStatus") or "-"
    estimated_calls = estimate_leaderboard_api_calls(json_data)
    stored_estimate = json_data.get("leaderboardLastEstimatedApiCalls", 0)
    last_error = json_data.get("lastRiotError") or {}
    error_summary = last_error.get("summary", "No Riot errors recorded.")

    embed = disnake.Embed(
        title="Status / Logs",
        description="Operational status for leaderboard updates and Riot API usage.",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Riot backoff", value=backoff_text, inline=False)
    embed.add_field(name="Last leaderboard update", value=f"{last_update}\nMode: **{update_mode}**\nStatus: **{update_status}**", inline=False)
    embed.add_field(name="Estimated API calls", value=f"Next normal cycle: **{estimated_calls}**\nLast stored estimate: **{stored_estimate}**\nMatch history/details only refresh on new games, daily, or force.", inline=False)
    embed.add_field(name="Last Riot error", value=error_summary[:1024], inline=False)

    errors = recent_error_events(5)
    if errors:
        embed.add_field(name="Recent errors", value="\n".join(format_log_event(event) for event in errors)[-1024:], inline=False)
    else:
        embed.add_field(name="Recent errors", value="No recent errors.", inline=False)
    return embed


def recent_logs_embed(limit=10):
    return audit_logs_embed(title="Recent audit logs", limit=limit)


def task_status(task):
    return "Running" if task.is_running() else "Stopped"


def file_size_text(path):
    if not os.path.exists(path):
        return "missing"
    size = os.path.getsize(path)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024, 1)} KB"
    return f"{round(size / (1024 * 1024), 1)} MB"


def data_summary(json_data):
    ensure_admin_state(json_data)
    return (
        f"Summoners: **{len(json_data.get('summoners') or {})}**\n"
        f"Matchmaking queue: **{len(json_data.get('matchmakingQueue') or [])}/10**\n"
        f"Draft active: **{'Yes' if json_data.get('matchmakingDraft') else 'No'}**\n"
        f"Match cache: **{len(json_data.get('matchData') or {})}**\n"
        f"Audit log: **{file_size_text(AUDIT_LOG_PATH)}**\n"
        f"Data file: **{file_size_text(jsonFile)}**"
    )


def cached_leaderboard_summoners(json_data):
    summoners = []
    for full_name, data in (json_data.get("summoners") or {}).items():
        if not data.get("tier") or not data.get("rank"):
            continue
        name, tagline = full_name.split("#", 1) if "#" in full_name else (full_name, "")
        summoner = SimpleNamespace(
            fullName=full_name,
            name=name,
            tagline=tagline,
            leaderboardPosition=data.get("leaderboardPosition", 100),
            tier=data.get("tier"),
            rank=data.get("rank"),
            leaguePoints=data.get("leaguePoints", 0),
            deltaScore=0,
            deltaDailyScore=0,
            deltaGamesPlayed=0,
            deltaDailyGamesPlayed=0,
            deltaLeaderboardPosition=0,
            deltaDailyLeaderboardPosition=0,
        )
        summoners.append(summoner)
    return sorted(summoners, key=lambda item: item.leaderboardPosition)


async def configured_message_status(json_data, kind):
    if kind == "admin":
        channel_id = admin_channel_id(json_data)
        message_id = json_data.get("adminMessageId")
    elif kind == "leaderboard":
        channel_id = leaderboard_channel_id(json_data)
        message_id = json_data.get("leaderboardMessageId")
    elif kind == "matchmaking":
        channel_id = matchmaking_channel_id(json_data)
        message_id = json_data.get("matchmakingMessageId")
    else:
        return "unknown"

    channel = await get_discord_channel(channel_id)
    if not channel:
        return f"Channel missing (<#{channel_id}>)"
    if not message_id:
        return f"No message configured in {channel.mention}"
    message = await fetch_configured_message(channel_id, message_id)
    if not message:
        return f"Message missing in {channel.mention}"
    return f"OK in {channel.mention} (`{message.id}`)"


async def operations_health_embed(json_data):
    ensure_admin_state(json_data)
    backoff_remaining = int(riotBackoffRemaining())
    if backoff_remaining > 0:
        backoff_text = f"Active for {backoff_remaining}s"
    else:
        backoff_text = "Inactive"

    embed = disnake.Embed(
        title="Operations health check",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(
        name="Persistent messages",
        value=(
            f"Admin: {await configured_message_status(json_data, 'admin')}\n"
            f"Leaderboard: {await configured_message_status(json_data, 'leaderboard')}\n"
            f"Matchmaking: {await configured_message_status(json_data, 'matchmaking')}"
        )[:1024],
        inline=False
    )
    embed.add_field(
        name="Tasks",
        value=(
            f"Leaderboard loop: **{task_status(updateRaceImage)}**\n"
            f"Patch loop: **{task_status(updatePatchNotes)}**\n"
            f"Captain draft timeout: **{task_status(captainDraftTimeout)}**"
        ),
        inline=False
    )
    embed.add_field(
        name="Riot / leaderboard",
        value=(
            f"Backoff: **{backoff_text}**\n"
            f"Last update: **{json_data.get('leaderboardLastUpdateStatus') or 'Unknown'}**\n"
            f"Last update at: **{json_data.get('leaderboardLastUpdateAt') or 'Never'}**\n"
            f"Last error: {(json_data.get('lastRiotError') or {}).get('summary', 'None')}"
        )[:1024],
        inline=False
    )
    embed.add_field(name="Data", value=data_summary(json_data), inline=False)
    return embed


async def bot_permission_report(guild, json_data):
    bot_member = guild.me or await get_guild_member(guild, bot.user.id)
    checks = [
        ("Admin", admin_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history"]),
        ("Leaderboard", leaderboard_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history"]),
        ("Matchmaking", matchmaking_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history", "manage_channels", "move_members"]),
    ]
    labels = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
        "manage_channels": "Manage Channels",
        "move_members": "Move Members",
    }
    lines = []
    for name, channel_id, permissions_to_check in checks:
        channel = await get_discord_channel(channel_id)
        if not channel:
            lines.append(f"**{name}:** channel missing (<#{channel_id}>)")
            continue
        permissions = channel.permissions_for(bot_member)
        missing = [labels[item] for item in permissions_to_check if not getattr(permissions, item, False)]
        if missing:
            lines.append(f"**{name}:** missing {', '.join(missing)} in {channel.mention}")
        else:
            lines.append(f"**{name}:** OK in {channel.mention}")
    return "\n".join(lines)


async def permission_report_embed(guild, json_data):
    embed = disnake.Embed(
        title="Permission check",
        description=(await bot_permission_report(guild, json_data))[:4000],
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    return embed


async def recreate_persistent_messages(json_data):
    ensure_admin_state(json_data)
    results = []

    admin_channel = await get_discord_channel(admin_channel_id(json_data)) if json_data.get("adminChannelId") else None
    if admin_channel:
        json_data["adminMessageId"] = await refresh_admin_message(admin_channel, json_data)
        results.append(f"Admin: `{json_data['adminMessageId']}`")
    else:
        results.append("Admin: channel not configured or missing")

    matchmaking_channel = await get_discord_channel(matchmaking_channel_id(json_data))
    if matchmaking_channel:
        json_data["matchmakingMessageId"] = await refresh_matchmaking_message(matchmaking_channel, json_data)
        results.append(f"Matchmaking: `{json_data['matchmakingMessageId']}`")
    else:
        results.append("Matchmaking: channel missing")

    leaderboard_channel = await get_discord_channel(leaderboard_channel_id(json_data))
    if leaderboard_channel:
        cached_summoners = cached_leaderboard_summoners(json_data)
        current_leaderboard = await fetch_configured_message(leaderboard_channel_id(json_data), json_data.get("leaderboardMessageId"))
        if cached_summoners:
            json_data["leaderboardMessageId"] = await send_or_edit_leaderboard(leaderboard_channel, json_data, cached_summoners)
            results.append(f"Leaderboard: `{json_data['leaderboardMessageId']}`")
        elif current_leaderboard:
            results.append(f"Leaderboard: OK (`{current_leaderboard.id}`)")
        else:
            results.append("Leaderboard: skipped, no cached rank data")
    else:
        results.append("Leaderboard: channel missing")

    writeToJsonFile(jsonFile, json_data)
    return "\n".join(results)


async def force_leaderboard_refresh(actor=None):
    json_data = ensure_admin_state(load_json_data())
    if riotBackoffRemaining() > 0:
        retry_time = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
        message = f"Leaderboard refresh blocked by Riot backoff until {retry_time}."
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message)
        return False, message, json_data

    summoners, updated = update(True, False, returnData=True, generate=False)
    status = "updated" if summoners else "skipped"
    json_data = set_leaderboard_runtime_status(json_data, "normal", status, estimate_leaderboard_api_calls(json_data), None if summoners else "Manual leaderboard refresh returned no summoners.")
    if not summoners:
        message = "Manual leaderboard refresh returned no summoners."
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message)
        return False, message, json_data

    channel = await get_discord_channel(leaderboard_channel_id(json_data))
    if not channel:
        message = "Leaderboard channel was not found."
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message, details={"channelId": str(leaderboard_channel_id(json_data))})
        return False, message, json_data

    latest_json_data = openJsonFile(jsonFile) or json_data
    latest_json_data["leaderboardMessageId"] = await send_or_edit_leaderboard(channel, latest_json_data, summoners)
    writeToJsonFile(jsonFile, latest_json_data)
    message = f"Leaderboard force refresh completed. Updated: {bool(updated)}. Summoners: {len(summoners)}."
    log_event("leaderboard_force_refresh", actor=actor, status="success", summary=message, details={"updated": bool(updated), "summoners": len(summoners)})
    return True, message, latest_json_data


async def refresh_configured_matchmaking_message(json_data=None):
    json_data = ensure_matchmaking_state(json_data or load_json_data())
    channel = await get_discord_channel(matchmaking_channel_id(json_data))
    if not channel:
        return None
    return await refresh_matchmaking_message(channel, json_data)


async def refresh_configured_admin_message(json_data=None):
    json_data = ensure_admin_state(json_data or load_json_data())
    if not json_data.get("adminChannelId"):
        return None
    channel = await get_discord_channel(admin_channel_id(json_data))
    if not channel:
        return None
    return await refresh_admin_message(channel, json_data)


async def add_summoner_to_data(name, tagline, platform, region):
    json_data = load_json_data()
    json_data.setdefault("summoners", {})
    tagline = normalize_tagline(tagline)
    summoner_full_name = f"{name}#{tagline}"

    if find_summoner_key(json_data, name, tagline):
        return False, f"{summoner_full_name} is already added"

    response = requests.get(
        f'https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tagline}?api_key={riotApKey}'
    )
    if response.status_code != 200:
        return False, f"Invalid summoner: {summoner_full_name}"

    account_data = response.json()
    summoner_full_name = account_data['gameName'] + '#' + account_data['tagLine']
    summoner_puuid = account_data['puuid']

    response = requests.get(
        f'https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{summoner_puuid}?api_key={riotApKey}'
    )
    if response.status_code != 200:
        return False, f"Could not fetch summoner profile for {summoner_full_name}"

    summoner_data = response.json()
    json_data["summoners"][summoner_full_name] = {
        "id": summoner_data['puuid'],
        "puuid": summoner_puuid,
        "profileIconId": 123,
        "platform": platform,
        "region": region,
        "score": 0,
        "dailyScore": 0,
        "leaderboardPosition": 100,
        "dailyLeaderboardPosition": 100,
        "gamesPlayed": 0,
        "dailyGamesPlayed": 0
    }

    writeToJsonFile(jsonFile, json_data)
    return True, f"{summoner_full_name} added"


def remove_summoner_from_data(name, tagline):
    json_data = load_json_data()
    summoner_key = find_summoner_key(json_data, name, tagline)
    if not summoner_key:
        return False, f"{name}#{normalize_tagline(tagline)} has not been added"

    del json_data["summoners"][summoner_key]
    rebuild_discord_links_from_summoners(json_data)
    writeToJsonFile(jsonFile, json_data)
    return True, f"{summoner_key} removed"


async def configure_leaderboard_channel(channel, actor=None):
    json_data = load_json_data()
    old_channel_id = leaderboard_channel_id(json_data)
    old_message_id = json_data.get("leaderboardMessageId")
    old_message = await fetch_configured_message(old_channel_id, old_message_id)
    old_embed = old_message.embeds[0] if old_message and old_message.embeds else None

    json_data["leaderboardChannelId"] = channel.id
    if old_message_id and old_message is None:
        json_data["leaderboardMessageId"] = None

    moved_message = None
    if old_channel_id != channel.id:
        json_data["leaderboardMessageId"] = None
        if old_embed:
            moved_message = await channel.send(embed=old_embed)
            json_data["leaderboardMessageId"] = moved_message.id
        await delete_configured_message(old_channel_id, old_message_id)

    writeToJsonFile(jsonFile, json_data)
    await refresh_configured_admin_message(json_data)

    if moved_message:
        message = f"Leaderboard channel set to {channel.mention}. Current embed moved."
    elif json_data.get("leaderboardMessageId"):
        message = f"Leaderboard channel set to {channel.mention}."
    else:
        message = f"Leaderboard channel set to {channel.mention}. The message will be created on the next leaderboard update."

    log_event("leaderboard_channel_changed", actor=actor, status="success", summary=message, details={"channelId": str(channel.id)})
    return message


async def configure_matchmaking_channel(channel, actor=None):
    json_data = ensure_matchmaking_state(load_json_data())
    old_channel_id = matchmaking_channel_id(json_data)
    old_message_id = json_data.get("matchmakingMessageId")

    json_data["matchmakingChannelId"] = channel.id
    if old_channel_id != channel.id:
        json_data["matchmakingMessageId"] = None

    writeToJsonFile(jsonFile, json_data)
    message_id = await refresh_matchmaking_message(channel, json_data)

    if old_channel_id != channel.id:
        await delete_configured_message(old_channel_id, old_message_id)

    await refresh_configured_admin_message(json_data)
    message = f"Matchmaking channel set to {channel.mention}. Message ready: {message_id}"
    log_event("matchmaking_channel_changed", actor=actor, status="success", summary=message, details={"channelId": str(channel.id), "messageId": str(message_id)})
    return message


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
    team_one_mentions = ", ".join(f"<@{player['userId']}>" for player in team_one)
    team_two_mentions = ", ".join(f"<@{player['userId']}>" for player in team_two)
    message = (
        f"Match started ({team_mode_label(mode)}).\n"
        f"Team 1 ({len(team_one)} players, {team_one_score} score): {team_one_mentions}\n"
        f"Team 2 ({len(team_two)} players, {team_two_score} score): {team_two_mentions}"
    )
    if note:
        message += f"\n{note}"
    return True, message, json_data


async def start_matchmaking_queue(guild, json_data, starter_user_id=None):
    json_data = ensure_matchmaking_state(json_data)
    queue = json_data.get("matchmakingQueue", [])

    if len(queue) < 2:
        writeToJsonFile(jsonFile, json_data)
        return False, "At least 2 players are required to start.", json_data
    if len(queue) > 10:
        writeToJsonFile(jsonFile, json_data)
        return False, "The queue cannot contain more than 10 players.", json_data
    if len(queue) % 2 and effective_odd_players_policy(json_data) == "require_even":
        writeToJsonFile(jsonFile, json_data)
        return False, "Odd player policy requires an even number of players before starting.", json_data

    mode = effective_matchmaking_team_mode(json_data)
    if mode == "captains":
        if json_data.get("matchmakingDraft"):
            writeToJsonFile(jsonFile, json_data)
            return False, "A captain draft is already active.", json_data
        json_data["matchmakingDraft"] = create_captain_draft(queue, starter_user_id or queue[0]["userId"])
        if is_draft_complete(json_data):
            return await finish_captain_draft_if_complete(guild, json_data)
        writeToJsonFile(jsonFile, json_data)
        captains = ", ".join(f"<@{captain_id}>" for captain_id in json_data["matchmakingDraft"]["captainIds"])
        return True, f"Captain draft started. Captains: {captains}.", json_data

    if mode == "balanced_rank":
        if has_valid_player_scores(queue):
            team_one, team_two = balanced_rank_teams(queue)
            note = None
        else:
            team_one, team_two = random_teams(queue)
            note = "Balance fallback: random teams were used because no valid player scores were cached."
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
            placeholder="Pick a player for your team",
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
            await send_ephemeral_followup(inter, "No captain draft is currently active.")
            return
        if str(draft.get("turnCaptainId")) != str(inter.author.id):
            await send_ephemeral_followup(inter, "It is not your turn to pick.")
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
            await send_ephemeral_followup(inter, f"{message}\nTeams announced publicly and will be deleted in 60 seconds.")
        else:
            await send_ephemeral_followup(inter, f"{message}\nWaiting for the next captain pick.")


class CaptainPickView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=90)
        self.add_item(CaptainPickSelect(json_data))


def matchmaking_settings_embed(json_data, admin=False):
    ensure_matchmaking_state(json_data)
    title = "Matchmaking admin settings" if admin else "Matchmaking settings"
    description = (
        f"Team mode: **{team_mode_label(effective_matchmaking_team_mode(json_data))}** ({team_mode_lock_text(json_data)})\n"
        f"Voice: **{voice_mode_label(effective_matchmaking_separate_channels(json_data))}** ({forced_mode_text(json_data)})\n"
        f"Odd players: **{odd_players_policy_label(effective_odd_players_policy(json_data))}**"
    )
    if json_data.get("matchmakingDraft"):
        description += "\nCaptain draft is active; team mode changes are locked until it finishes."
    embed = disnake.Embed(
        title=title,
        description=description,
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )
    return embed


def team_mode_options(selected_mode=None, include_unlocked=False, forced_mode=None):
    options = []
    if include_unlocked:
        options.append(disnake.SelectOption(label="Unlocked", value="unlocked", default=forced_mode not in MATCHMAKING_TEAM_MODES))
    for mode in MATCHMAKING_TEAM_MODES:
        label = team_mode_label(mode)
        value = f"force:{mode}" if include_unlocked else mode
        default = forced_mode == mode if include_unlocked else selected_mode == mode
        options.append(disnake.SelectOption(label=label if not include_unlocked else f"Force {label}", value=value, default=default))
    return options


def voice_mode_options(selected_value=None, include_unlocked=False, forced_value=None):
    if include_unlocked:
        return [
            disnake.SelectOption(label="Unlocked", value="unlocked", default=forced_value is None),
            disnake.SelectOption(label="Force same channel", value="force:same", default=forced_value is False),
            disnake.SelectOption(label="Force separate team channels", value="force:separate", default=forced_value is True),
        ]
    return [
        disnake.SelectOption(label="Same channel", value="same", default=selected_value is False),
        disnake.SelectOption(label="Separate team channels", value="separate", default=selected_value is True),
    ]


def odd_policy_options(selected_policy):
    return [
        disnake.SelectOption(label=odd_players_policy_label(policy), value=policy, default=selected_policy == policy)
        for policy in MATCHMAKING_ODD_PLAYER_POLICIES
    ]


async def refresh_matchmaking_setting_views(inter, json_data, admin=False):
    await refresh_configured_matchmaking_message(json_data)
    await refresh_configured_admin_message(json_data)
    view = MatchmakingAdminSettingsView(json_data) if admin else MatchmakingSettingsView(inter.author.id, json_data)
    await inter.response.edit_message(embed=matchmaking_settings_embed(json_data, admin), view=view)


async def require_queued_settings_user(inter, json_data):
    if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
        await send_ephemeral_response(inter, "Only queued players can change matchmaking settings.")
        return False
    return True


class PublicTeamModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        forced = json_data.get("matchmakingTeamModeForced") in MATCHMAKING_TEAM_MODES
        disabled = bool(json_data.get("matchmakingDraft") or forced)
        selected_mode = effective_matchmaking_team_mode(json_data) if forced else json_data.get("matchmakingTeamMode")
        super().__init__(
            placeholder="Team mode",
            min_values=1,
            max_values=1,
            options=team_mode_options(selected_mode),
            disabled=disabled
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_response(inter, "Team mode cannot be changed during a captain draft.")
            return
        if json_data.get("matchmakingTeamModeForced") in MATCHMAKING_TEAM_MODES:
            await send_ephemeral_response(inter, f"Team mode is locked by administration: {team_mode_lock_text(json_data)}.")
            return
        json_data["matchmakingTeamMode"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        mode = team_mode_label(effective_matchmaking_team_mode(json_data))
        log_event("matchmaking_team_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Team mode set to {mode}.", details={"mode": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)


class PublicVoiceModeSelect(disnake.ui.Select):
    def __init__(self, json_data):
        forced = json_data.get("matchmakingSeparateChannelsForced")
        selected_value = effective_matchmaking_separate_channels(json_data) if forced is not None else json_data.get("matchmakingSeparateChannels", False)
        super().__init__(
            placeholder="Voice channels",
            min_values=1,
            max_values=1,
            options=voice_mode_options(selected_value),
            disabled=forced is not None
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        if json_data.get("matchmakingSeparateChannelsForced") is not None:
            await send_ephemeral_response(inter, f"Voice mode is locked by administration: {forced_mode_text(json_data)}.")
            return
        json_data["matchmakingSeparateChannels"] = self.values[0] == "separate"
        writeToJsonFile(jsonFile, json_data)
        mode = voice_mode_label(json_data["matchmakingSeparateChannels"])
        log_event("matchmaking_voice_mode_selected", actor=interaction_actor(inter), status="success", summary=f"Voice mode set to {mode}.", details={"separate": json_data["matchmakingSeparateChannels"]})
        await refresh_matchmaking_setting_views(inter, json_data)


class PublicOddPolicySelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder="Odd players",
            min_values=1,
            max_values=1,
            options=odd_policy_options(effective_odd_players_policy(json_data))
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if not await require_queued_settings_user(inter, json_data):
            return
        json_data["matchmakingOddPlayersPolicy"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        policy = odd_players_policy_label(self.values[0])
        log_event("matchmaking_odd_players_policy_selected", actor=interaction_actor(inter), status="success", summary=f"Odd player policy set to {policy}.", details={"policy": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data)


class MatchmakingSettingsView(disnake.ui.View):
    def __init__(self, user_id, json_data=None):
        super().__init__(timeout=180)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        self.user_id = str(user_id)
        self.add_item(PublicTeamModeSelect(json_data))
        self.add_item(PublicVoiceModeSelect(json_data))
        self.add_item(PublicOddPolicySelect(json_data))


class AdminTeamModeLockSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder="Team mode lock",
            min_values=1,
            max_values=1,
            options=team_mode_options(include_unlocked=True, forced_mode=json_data.get("matchmakingTeamModeForced"))
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_response(inter, "Team mode cannot be locked while a captain draft is active.")
            return
        value = None if self.values[0] == "unlocked" else self.values[0].split(":", 1)[1]
        json_data["matchmakingTeamModeForced"] = value
        writeToJsonFile(jsonFile, json_data)
        log_event("matchmaking_team_mode_forced", actor=interaction_actor(inter), status="success", summary=f"Team mode lock set to {team_mode_lock_text(json_data)}", details={"forced": value})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)


class AdminVoiceModeLockSelect(disnake.ui.Select):
    def __init__(self, json_data):
        super().__init__(
            placeholder="Voice channel lock",
            min_values=1,
            max_values=1,
            options=voice_mode_options(include_unlocked=True, forced_value=json_data.get("matchmakingSeparateChannelsForced"))
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
            placeholder="Odd players policy",
            min_values=1,
            max_values=1,
            options=odd_policy_options(effective_odd_players_policy(json_data))
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        json_data["matchmakingOddPlayersPolicy"] = self.values[0]
        writeToJsonFile(jsonFile, json_data)
        log_event("matchmaking_odd_players_policy_selected", actor=interaction_actor(inter), status="success", summary=f"Odd player policy set to {odd_players_policy_label(self.values[0])}.", details={"policy": self.values[0]})
        await refresh_matchmaking_setting_views(inter, json_data, admin=True)


class MatchmakingAdminSettingsView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=180)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        self.add_item(AdminTeamModeLockSelect(json_data))
        self.add_item(AdminVoiceModeLockSelect(json_data))
        self.add_item(AdminOddPolicySelect(json_data))


class CaptainPickButton(disnake.ui.Button):
    def __init__(self):
        super().__init__(
            label="Pick player",
            style=disnake.ButtonStyle.green,
            custom_id="matchmaking:captains:pick",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        draft = json_data.get("matchmakingDraft")
        if not draft:
            await send_ephemeral_response(inter, "No captain draft is currently active.")
            return
        if str(draft.get("turnCaptainId")) != str(inter.author.id):
            await send_ephemeral_response(inter, "It is not your turn to pick.")
            return
        by_id = players_by_id(json_data.get("matchmakingQueue", []))
        available_remaining = [user_id for user_id in [str(value) for value in draft.get("remainingPlayerIds", [])] if user_id in by_id]
        if not available_remaining:
            await send_ephemeral_response(inter, "There are no players left to pick.")
            return
        await send_ephemeral_response(inter, "Choose a player for your team.", view=CaptainPickView(json_data))


class StartMatchButton(disnake.ui.Button):
    def __init__(self):
        super().__init__(
            label="Start match",
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
                await send_ephemeral_followup(inter, "Only queued players can start matchmaking.")
                return

            success, message, json_data = await start_matchmaking_queue(inter.guild, json_data, inter.author.id)
            await refresh_configured_matchmaking_message(json_data)
            await refresh_configured_admin_message(json_data)
            log_event("matchmaking_start", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"mode": effective_matchmaking_team_mode(json_data)})
            if success and public_matchmaking_announcement(message):
                await send_temporary_public_message(inter.channel, message)
                await send_ephemeral_followup(inter, "Announcement posted publicly and will be deleted in 60 seconds.")
            else:
                await send_ephemeral_followup(inter, message)
        except Exception as error:
            log_event("matchmaking_start", actor=interaction_actor(inter), status="error", summary=f"Start match failed: {error}")
            await send_ephemeral_followup(inter, f"Start match failed: {error}")


class MatchmakingView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=None)
        json_data = ensure_matchmaking_state(json_data or load_json_data())
        draft = json_data.get("matchmakingDraft")
        queue = json_data.get("matchmakingQueue", [])
        if draft and draft.get("remainingPlayerIds"):
            self.add_item(CaptainPickButton())
        elif not draft and len(queue) >= 2:
            self.add_item(StartMatchButton())

    @disnake.ui.button(label="Join", style=disnake.ButtonStyle.green, custom_id="matchmaking:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        member = inter.author
        voice_channel = member.voice.channel if getattr(member, "voice", None) and member.voice else None
        if voice_channel is None:
            await send_ephemeral_followup(inter, "You need to be in a voice channel to join the queue.")
            return

        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingDraft"):
            await send_ephemeral_followup(inter, "A captain draft is already active. Wait for it to finish before joining.")
            return
        queue = json_data["matchmakingQueue"]
        index = user_queue_index(queue, member.id)
        if index is not None:
            queue[index]["displayName"] = member.display_name
            queue[index]["voiceChannelId"] = voice_channel.id
            queue[index].update(primary_summoner_queue_data(json_data, member.id))
            response = "Your voice channel was updated."
        else:
            if len(queue) >= 10:
                await send_ephemeral_followup(inter, "The matchmaking queue is full.")
                return
            player = {
                "userId": member.id,
                "displayName": member.display_name,
                "voiceChannelId": voice_channel.id
            }
            player.update(primary_summoner_queue_data(json_data, member.id))
            queue.append(player)
            response = "You joined the matchmaking queue."

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
            await send_ephemeral_followup(inter, "You are not in the matchmaking queue.")
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
            await send_ephemeral_followup(inter, "You left the matchmaking queue. Captain draft cancelled.")
        elif draft_changed:
            log_event("matchmaking_captain_draft_player_removed", actor=interaction_actor(inter), status="success", summary="User left active captain draft.")
            message = "You left the matchmaking queue and were removed from the captain draft."
            if finished_message:
                message += "\nTeams announced publicly and will be deleted in 60 seconds."
            await send_ephemeral_followup(inter, message)
        else:
            await send_ephemeral_followup(inter, "You left the matchmaking queue.")

    @disnake.ui.button(label="Settings", style=disnake.ButtonStyle.blurple, custom_id="matchmaking:settings")
    async def settings(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        json_data = ensure_matchmaking_state(load_json_data())
        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await send_ephemeral_response(inter, "Only queued players can open matchmaking settings.")
            return
        await send_ephemeral_response(inter, embed=matchmaking_settings_embed(json_data), view=MatchmakingSettingsView(inter.author.id, json_data))


class AddSummonerModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(label="Name", custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label="Tagline", custom_id="tagline", required=True, max_length=16),
            disnake.ui.TextInput(label="Platform", custom_id="platform", required=True, max_length=8, placeholder="EUW1"),
            disnake.ui.TextInput(label="Region", custom_id="region", required=True, max_length=12, placeholder="EUROPE"),
        ]
        super().__init__(title="Add summoner", custom_id="admin:add_summoner_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        name = inter.text_values["name"].strip()
        tagline = inter.text_values["tagline"].strip()
        platform = inter.text_values["platform"].strip().upper()
        region = inter.text_values["region"].strip().upper()

        if platform not in platforms:
            await send_ephemeral(inter, f"Invalid platform. Use one of: {', '.join(platforms)}")
            return
        if region not in regions:
            await send_ephemeral(inter, f"Invalid region. Use one of: {', '.join(regions)}")
            return

        await inter.response.defer(ephemeral=True)
        success, message = await add_summoner_to_data(name, tagline, platform, region)
        log_event("leaderboard_summoner_add", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"name": name, "tagline": tagline, "platform": platform, "region": region})
        await refresh_configured_admin_message()
        await send_ephemeral_followup(inter, message)


class LinkAccountModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(label="Discord user ID or mention", custom_id="discord_user", required=True, max_length=32),
            disnake.ui.TextInput(label="Summoner name", custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label="Tagline", custom_id="tagline", required=True, max_length=16),
            disnake.ui.TextInput(label="Primary (yes/no)", custom_id="primary", required=False, max_length=8, placeholder="yes"),
        ]
        super().__init__(title="Link Discord account", custom_id="admin:link_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        member = await discord_user_from_text(inter.guild, inter.text_values["discord_user"])
        if not member:
            message = "Invalid Discord user."
            log_event("discord_link_created", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_followup(inter, message)
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_created", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(member.id)})
            await send_ephemeral_followup(inter, message)
            return

        primary_value = inter.text_values.get("primary", "").strip().lower()
        primary = primary_value not in ["no", "false", "0", "n"]
        success, message = link_summoner_to_discord(json_data, member, summoner, primary)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_created", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(member.id), "summoner": summoner, "primary": primary})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)


class UnlinkAccountModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(label="Summoner name", custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label="Tagline", custom_id="tagline", required=True, max_length=16),
        ]
        super().__init__(title="Unlink Discord account", custom_id="admin:unlink_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_removed", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_response(inter, message)
            return

        success, message = unlink_summoner_from_discord(json_data, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_removed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_response(inter, message)


class SetPrimaryAccountModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(label="Discord user ID or mention", custom_id="discord_user", required=True, max_length=32),
            disnake.ui.TextInput(label="Summoner name", custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label="Tagline", custom_id="tagline", required=True, max_length=16),
        ]
        super().__init__(title="Set primary summoner", custom_id="admin:primary_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        member = await discord_user_from_text(inter.guild, inter.text_values["discord_user"])
        if not member:
            message = "Invalid Discord user."
            log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_followup(inter, message)
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(member.id)})
            await send_ephemeral_followup(inter, message)
            return

        success, message = set_primary_summoner_for_user(json_data, member, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(member.id), "summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)


class LeaderboardRemoveSelect(disnake.ui.Select):
    def __init__(self, summoners):
        options = [
            disnake.SelectOption(label=summoner[:100], value=summoner)
            for summoner in summoners[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(
            placeholder="Remove a leaderboard user",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:leaderboard:remove"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        summoner = self.values[0]
        name, tagline = summoner.split("#", 1)
        success, message = remove_summoner_from_data(name, tagline)
        log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"summoner": summoner})
        await refresh_configured_admin_message()
        json_data = load_json_data()
        await inter.response.edit_message(embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data))
        await send_ephemeral_followup(inter, message)


class QueueRemoveSelect(disnake.ui.Select):
    def __init__(self, queue):
        options = []
        for player in queue[:MAX_SELECT_OPTIONS]:
            label = player.get("displayName") or str(player.get("userId"))
            options.append(disnake.SelectOption(label=label[:100], value=str(player["userId"])))
        super().__init__(
            placeholder="Kick a queued player",
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
        response = f"Removed <@{user_id}> from the queue." if removed else "That user is no longer in the queue."
        if draft_cancelled:
            response += " Captain draft cancelled."
        elif draft_changed and finished_message:
            response += "\nTeams announced publicly and will be deleted in 60 seconds."
        log_event("matchmaking_queue_kick", actor=interaction_actor(inter), status="success" if removed else "error", summary=response, details={"userId": str(user_id)})
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        if finished_message:
            await send_temporary_public_message(inter.channel, finished_message)
        await inter.response.edit_message(embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))
        await send_ephemeral_followup(inter, response)


class SettingsAdminView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.channel_select(placeholder="Set leaderboard channel", channel_types=[disnake.ChannelType.text], custom_id="admin:settings:leaderboard", min_values=1, max_values=1)
    async def leaderboard_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        channel = select.values[0]
        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        await inter.response.defer(ephemeral=True)
        message = await configure_leaderboard_channel(channel, interaction_actor(inter))
        await send_ephemeral_followup(inter, message)

    @disnake.ui.channel_select(placeholder="Set matchmaking channel", channel_types=[disnake.ChannelType.text], custom_id="admin:settings:matchmaking", min_values=1, max_values=1)
    async def matchmaking_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        channel = select.values[0]
        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        await inter.response.defer(ephemeral=True)
        message = await configure_matchmaking_channel(channel, interaction_actor(inter))
        await send_ephemeral_followup(inter, message)

    @disnake.ui.button(label="Toggle /add /remove", style=disnake.ButtonStyle.gray, custom_id="admin:settings:leaderboard_chat_commands", row=2)
    async def toggle_leaderboard_chat_commands(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        json_data["leaderboardChatCommandsEnabled"] = not leaderboard_chat_commands_enabled(json_data)
        writeToJsonFile(jsonFile, json_data)
        enabled = leaderboard_chat_commands_enabled(json_data)
        status = "enabled" if enabled else "disabled"
        log_event("leaderboard_chat_commands_toggle", actor=interaction_actor(inter), status="success", summary=f"Leaderboard /add and /remove commands {status}.", details={"enabled": enabled})
        await refresh_configured_admin_message(json_data)
        await inter.response.edit_message(embed=settings_admin_embed(json_data), view=SettingsAdminView())


class LeaderboardUsersAdminView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=300)
        summoners = [summoner for summoner in (json_data.get("summoners") or {}).keys()]
        if summoners:
            self.add_item(LeaderboardRemoveSelect(summoners))

    @disnake.ui.button(label="Add summoner", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard:add")
    async def add_summoner(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(AddSummonerModal())


def link_request_select_options(requests):
    options = []
    for request in requests[:MAX_SELECT_OPTIONS]:
        user_id = request.get("discordUserId")
        summoner = request.get("summonerFullName") or "Unknown summoner"
        display_name = request.get("discordDisplayName") or user_id
        label = f"{display_name} - {summoner}"[:100]
        options.append(disnake.SelectOption(label=label, value=request["id"], description=f"Discord ID: {user_id}"[:100]))
    return options


class ApproveLinkRequestSelect(disnake.ui.Select):
    def __init__(self, requests):
        super().__init__(
            placeholder="Accept a pending link request",
            min_values=1,
            max_values=1,
            options=link_request_select_options(requests),
            custom_id="admin:links:request_accept",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        request_id = self.values[0]
        request = find_discord_link_request(json_data, request_id)
        if not request:
            await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
            await send_ephemeral_followup(inter, "That link request is no longer pending.")
            return

        member = await get_guild_member(inter.guild, request["discordUserId"])
        if not member:
            await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
            await send_ephemeral_followup(inter, "Could not find that Discord user in this server. The request was kept pending.")
            return

        success, message = approve_discord_link_request(json_data, request_id, member)
        if success:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_admin_message(json_data)
        log_event("discord_link_request_accepted", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"requestId": request_id, "discordUserId": request["discordUserId"], "summoner": request["summonerFullName"]})
        await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
        await send_ephemeral_followup(inter, message)


class RejectLinkRequestSelect(disnake.ui.Select):
    def __init__(self, requests):
        super().__init__(
            placeholder="Reject a pending link request",
            min_values=1,
            max_values=1,
            options=link_request_select_options(requests),
            custom_id="admin:links:request_reject",
            row=2
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        request_id = self.values[0]
        request = find_discord_link_request(json_data, request_id)
        removed = remove_discord_link_request(json_data, request_id)
        if removed:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_admin_message(json_data)
        summoner = request.get("summonerFullName") if request else None
        message = f"Rejected link request for {summoner}." if removed else "That link request is no longer pending."
        log_event("discord_link_request_rejected", actor=interaction_actor(inter), status="success" if removed else "error", summary=message, details={"requestId": request_id, "summoner": summoner})
        await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
        await send_ephemeral_followup(inter, message)


class LinkedAccountsAdminView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=300)
        if json_data is None:
            json_data = ensure_admin_state(load_json_data())
        requests = discord_link_requests(json_data)
        if requests:
            self.add_item(ApproveLinkRequestSelect(requests))
            self.add_item(RejectLinkRequestSelect(requests))

    @disnake.ui.button(label="Link account", style=disnake.ButtonStyle.green, custom_id="admin:links:link")
    async def link_account(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(LinkAccountModal())

    @disnake.ui.button(label="Unlink account", style=disnake.ButtonStyle.red, custom_id="admin:links:unlink")
    async def unlink_account(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(UnlinkAccountModal())

    @disnake.ui.button(label="Set primary", style=disnake.ButtonStyle.blurple, custom_id="admin:links:primary")
    async def set_primary(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(SetPrimaryAccountModal())


class MatchmakingAdminView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=300)
        queue = json_data.get("matchmakingQueue", [])
        if queue:
            self.add_item(QueueRemoveSelect(queue))

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
                await send_ephemeral_followup(inter, "Announcement posted publicly and will be deleted in 60 seconds.")
            else:
                await send_ephemeral_followup(inter, message)
        except Exception as error:
            log_event("matchmaking_force_start", actor=interaction_actor(inter), status="error", summary=f"Force start failed: {error}")
            await send_ephemeral_followup(inter, f"Force start failed: {error}")

    @disnake.ui.button(label="Configure", style=disnake.ButtonStyle.blurple, custom_id="admin:matchmaking:configure", row=1)
    async def configure(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        json_data = ensure_matchmaking_state(load_json_data())
        await send_ephemeral_response(inter, embed=matchmaking_settings_embed(json_data, admin=True), view=MatchmakingAdminSettingsView(json_data))


class AuditActorSearchModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Actor ID or name",
                custom_id="actor_query",
                required=True,
                max_length=100,
                placeholder="Discord ID, display name, or system"
            )
        ]
        super().__init__(title="Search audit logs by actor", custom_id="admin:audit:actor_search_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        actor_query = inter.text_values["actor_query"].strip()
        log_event("operations_audit_actor_search", actor=interaction_actor(inter), status="success", summary=f"Audit actor search requested for {actor_query}.")
        await send_ephemeral_response(inter, embed=audit_logs_embed(title="Audit logs by actor", actor_query=actor_query, limit=15))


class AuditCategorySelect(disnake.ui.Select):
    def __init__(self):
        options = [
            disnake.SelectOption(label=label, value=category)
            for category, label in AUDIT_CATEGORY_LABELS.items()
        ]
        super().__init__(
            placeholder="Filter audit logs by category",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:audit:category",
            row=3
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        category = self.values[0]
        label = AUDIT_CATEGORY_LABELS.get(category, category)
        log_event("operations_audit_category_filter", actor=interaction_actor(inter), status="success", summary=f"Audit category filter requested for {label}.")
        await send_ephemeral_response(inter, embed=audit_logs_embed(title=f"{label} audit logs", category=category, limit=15))


class StatusLogsAdminView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AuditCategorySelect())

    @disnake.ui.button(label="Refresh status", style=disnake.ButtonStyle.blurple, custom_id="admin:status:refresh", row=0)
    async def refresh_status(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await inter.response.edit_message(embed=status_admin_embed(json_data), view=StatusLogsAdminView())

    @disnake.ui.button(label="Health check", style=disnake.ButtonStyle.green, custom_id="admin:status:health", row=0)
    async def health_check(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        log_event("operations_health_check", actor=interaction_actor(inter), status="success", summary="Health check requested.")
        await send_ephemeral_response(inter, embed=await operations_health_embed(json_data))

    @disnake.ui.button(label="Test permissions", style=disnake.ButtonStyle.gray, custom_id="admin:status:permissions", row=0)
    async def test_permissions(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        embed = await permission_report_embed(inter.guild, json_data)
        log_event("operations_permission_check", actor=interaction_actor(inter), status="success", summary="Permission check requested.")
        await send_ephemeral_response(inter, embed=embed)

    @disnake.ui.button(label="Recreate messages", style=disnake.ButtonStyle.blurple, custom_id="admin:status:recreate_messages", row=1)
    async def recreate_messages(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        summary = await recreate_persistent_messages(json_data)
        log_event("operations_recreate_messages", actor=interaction_actor(inter), status="success", summary=summary)
        await send_ephemeral_followup(inter, f"Persistent messages checked/recreated:\n{summary}")

    @disnake.ui.button(label="Download data backup", style=disnake.ButtonStyle.gray, custom_id="admin:status:data_backup", row=1)
    async def download_data_backup(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        if not os.path.exists(jsonFile):
            await send_ephemeral_response(inter, "No data file exists yet.")
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(jsonFile, "rb") as file:
            data_file = disnake.File(BytesIO(file.read()), filename=f"data-backup-{timestamp}.json")

        files = [data_file]
        if os.path.exists(AUDIT_LOG_PATH):
            files.append(disnake.File(AUDIT_LOG_PATH, filename=f"audit-{timestamp}.jsonl"))

        log_event("operations_data_backup_download", actor=interaction_actor(inter), status="success", summary="Data backup downloaded.", details={"includedAuditLog": os.path.exists(AUDIT_LOG_PATH)})
        await send_ephemeral_response(inter, "Data backup:", files=files)

    @disnake.ui.button(label="Force leaderboard refresh", style=disnake.ButtonStyle.red, custom_id="admin:status:force_leaderboard", row=1)
    async def force_refresh_leaderboard(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        success, message, json_data = await force_leaderboard_refresh(interaction_actor(inter))
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)

    @disnake.ui.button(label="View recent logs", style=disnake.ButtonStyle.gray, custom_id="admin:status:recent_logs", row=2)
    async def view_recent_logs(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await send_ephemeral_response(inter, embed=recent_logs_embed())

    @disnake.ui.button(label="Audit summary 24h", style=disnake.ButtonStyle.blurple, custom_id="admin:audit:summary_24h", row=4)
    async def audit_summary_24h(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        log_event("operations_audit_summary_24h", actor=interaction_actor(inter), status="success", summary="Audit summary requested.")
        await send_ephemeral_response(inter, embed=audit_summary_24h_embed())

    @disnake.ui.button(label="Search actor", style=disnake.ButtonStyle.gray, custom_id="admin:audit:actor_search", row=4)
    async def search_audit_actor(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.send_modal(AuditActorSearchModal())


class AdminView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="App settings", style=disnake.ButtonStyle.blurple, custom_id="admin:settings")
    async def settings(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await send_ephemeral_response(inter, embed=settings_admin_embed(json_data), view=SettingsAdminView())

    @disnake.ui.button(label="Leaderboard users", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard")
    async def leaderboard_users(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = load_json_data()
        await send_ephemeral_response(inter, embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data))

    @disnake.ui.button(label="Linked accounts", style=disnake.ButtonStyle.green, custom_id="admin:links", row=1)
    async def linked_accounts(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        rebuild_discord_links_from_summoners(json_data)
        writeToJsonFile(jsonFile, json_data)
        await send_ephemeral_response(inter, embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))

    @disnake.ui.button(label="Matchmaking", style=disnake.ButtonStyle.gray, custom_id="admin:matchmaking")
    async def matchmaking(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_matchmaking_state(load_json_data())
        await send_ephemeral_response(inter, embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))

    @disnake.ui.button(label="Status / Logs", style=disnake.ButtonStyle.blurple, custom_id="admin:status")
    async def status_logs(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await send_ephemeral_response(inter, embed=status_admin_embed(json_data), view=StatusLogsAdminView())

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.gray, custom_id="admin:refresh")
    async def refresh_admin_panel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_response(inter, "Administration refreshed.")


def settings_admin_embed(json_data):
    ensure_admin_state(json_data)
    commands_status = "Enabled" if leaderboard_chat_commands_enabled(json_data) else "Disabled"
    embed = disnake.Embed(
        title="App settings",
        description="Select the channels used by persistent bot messages and control chat command shortcuts.",
        colour=disnake.Colour.dark_teal()
    )
    embed.add_field(name="Leaderboard", value=f"<#{leaderboard_channel_id(json_data)}>", inline=True)
    embed.add_field(name="Matchmaking", value=f"<#{matchmaking_channel_id(json_data)}>", inline=True)
    embed.add_field(name="/add and /remove", value=f"**{commands_status}**\nWhen enabled, anyone can use them from chat.", inline=False)
    return embed


def leaderboard_users_admin_embed(json_data):
    summoners = json_data.get("summoners") or {}
    embed = disnake.Embed(
        title="Leaderboard users",
        description=f"Configured summoners: **{len(summoners)}**",
        colour=disnake.Colour.green()
    )
    embed.add_field(name="Current users", value=format_summoner_summary(json_data), inline=False)
    if len(summoners) > MAX_SELECT_OPTIONS:
        embed.set_footer(text=f"Only the first {MAX_SELECT_OPTIONS} users are available in the remove selector.")
    return embed


def linked_accounts_admin_embed(json_data):
    rebuild_discord_links_from_summoners(json_data)
    links = json_data.get("discordLinks", {})
    requests = discord_link_requests(json_data)
    linked_summoners = [
        summoner for summoner, data in (json_data.get("summoners") or {}).items()
        if data.get("discordUserId")
    ]

    embed = disnake.Embed(
        title="Linked accounts",
        description=(
            f"Linked Discord users: **{len(links)}**\n"
            f"Linked summoners: **{len(linked_summoners)}**\n"
            f"Pending requests: **{len(requests)}**"
        ),
        colour=disnake.Colour.green()
    )
    embed.add_field(name="Current links", value=format_linked_accounts_summary(json_data), inline=False)
    embed.add_field(name="Pending requests", value=format_discord_link_requests_summary(json_data), inline=False)
    return embed


def matchmaking_admin_embed(json_data):
    ensure_matchmaking_state(json_data)
    fallback_text = ""
    if json_data["matchmakingQueue"] and effective_matchmaking_team_mode(json_data) == "balanced_rank" and not has_valid_player_scores(json_data["matchmakingQueue"]):
        fallback_text = "\nBalance fallback: **random teams** (no valid scores cached)"
    embed = disnake.Embed(
        title="Matchmaking administration",
        description=(
            f"Queue: **{len(json_data['matchmakingQueue'])}/10**\n"
            f"Team mode: **{team_mode_label(effective_matchmaking_team_mode(json_data))}** ({team_mode_lock_text(json_data)})\n"
            f"Voice: **{voice_mode_label(effective_matchmaking_separate_channels(json_data))}** ({forced_mode_text(json_data)})\n"
            f"Odd players: **{odd_players_policy_label(effective_odd_players_policy(json_data))}**"
            f"{fallback_text}"
        ),
        colour=disnake.Colour.blurple()
    )
    embed.add_field(name="Queued players", value=format_matchmaking_queue(json_data), inline=False)
    if json_data.get("matchmakingDraft"):
        draft = json_data["matchmakingDraft"]
        embed.add_field(name="Captain draft", value=f"Turn: <@{draft.get('turnCaptainId')}>\nRemaining: **{len(draft.get('remainingPlayerIds', []))}**", inline=False)
    return embed


async def refresh_matchmaking_message(channel, json_data=None):
    json_data = ensure_matchmaking_state(json_data or load_json_data())
    embed = matchmaking_embed(json_data)
    view = MatchmakingView(json_data)
    message_id = json_data.get("matchmakingMessageId")

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=embed, view=view)
            print(f"Updated matchmaking message {message.id}")
            return message.id
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException, ValueError) as error:
            print(f"Could not update matchmaking message {message_id}: {error}")
            pass

    message = await channel.send(embed=embed, view=view)
    json_data["matchmakingMessageId"] = message.id
    writeToJsonFile(jsonFile, json_data)
    print(f"Created matchmaking message {message.id}")
    return message.id


async def refresh_admin_message(channel, json_data=None):
    json_data = ensure_admin_state(json_data or load_json_data())
    embed = admin_embed(json_data)
    view = AdminView()
    message_id = json_data.get("adminMessageId")

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=embed, view=view)
            print(f"Updated admin message {message.id}")
            return message.id
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException, ValueError) as error:
            print(f"Could not update admin message {message_id}: {error}")
            pass

    message = await channel.send(embed=embed, view=view)
    json_data["adminMessageId"] = message.id
    writeToJsonFile(jsonFile, json_data)
    print(f"Created admin message {message.id}")
    return message.id


async def setup_admin_message(channel):
    json_data = ensure_admin_state(load_json_data())
    old_channel_id = admin_channel_id(json_data)
    old_message_id = json_data.get("adminMessageId")

    json_data["adminChannelId"] = channel.id
    if old_channel_id != channel.id:
        json_data["adminMessageId"] = None

    writeToJsonFile(jsonFile, json_data)
    message_id = await refresh_admin_message(channel, json_data)

    if old_channel_id != channel.id:
        await delete_configured_message(old_channel_id, old_message_id)

    return message_id


async def setup_matchmaking_message():
    json_data = ensure_matchmaking_state(load_json_data())
    channel = await get_discord_channel(matchmaking_channel_id(json_data))
    if not channel:
        print("Matchmaking message was not created because the configured channel was not found.")
        return None
    return await refresh_matchmaking_message(channel, json_data)


async def get_guild_member(guild, user_id):
    member = guild.get_member(int(user_id))
    if member:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
        return None


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


if __name__ == "__main__":

    @bot.event
    async def on_ready():
        global matchmaking_view_registered
        print('Logged in as {0.user} at {1}'.format(bot, datetime.now().strftime('%I:%M:%S %p %d/%m/%Y')))
        print("")
        if not matchmaking_view_registered:
            bot.add_view(MatchmakingView())
            bot.add_view(AdminView())
            matchmaking_view_registered = True
        await setup_matchmaking_message()
        json_data = load_json_data()
        if json_data.get("adminChannelId"):
            admin_channel = await get_discord_channel(admin_channel_id(json_data))
            if admin_channel:
                await refresh_admin_message(admin_channel, json_data)
        if not updateRaceImage.is_running():
            updateRaceImage.start()
            updatePatchNotes.start()
        if not captainDraftTimeout.is_running():
            captainDraftTimeout.start()


    @bot.event
    async def on_voice_state_update(member, before, after):
        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingInProgress"):
            await delete_empty_matchmaking_team_channels(member.guild)
            return

        queue = json_data["matchmakingQueue"]
        index = user_queue_index(queue, member.id)
        queue_changed = False

        if index is not None:
            if after.channel is None:
                del queue[index]
                draft_changed, draft_cancelled = remove_player_from_matchmaking_draft(json_data, member.id)
                queue_changed = True
                if draft_cancelled:
                    log_event("matchmaking_captain_draft_cancelled", actor=system_actor(), status="error", summary=f"Captain draft cancelled because <@{member.id}> left voice.", details={"userId": str(member.id)})
                elif draft_changed:
                    log_event("matchmaking_captain_draft_player_removed", actor=system_actor(), status="success", summary=f"<@{member.id}> removed from active captain draft.", details={"userId": str(member.id)})
            else:
                queue[index]["voiceChannelId"] = after.channel.id
                queue_changed = True

        if queue_changed:
            if json_data.get("matchmakingDraft") and is_draft_complete(json_data):
                success, message, json_data = await finish_captain_draft_if_complete(member.guild, json_data)
                log_event("matchmaking_captain_draft_finished", actor=system_actor(), status="success" if success else "error", summary=message or "Captain draft finished after voice update.")
            else:
                writeToJsonFile(jsonFile, json_data)
            channel = await get_discord_channel(matchmaking_channel_id(json_data))
            if channel:
                await refresh_matchmaking_message(channel, json_data)
                if message:
                    await send_temporary_public_message(channel, message)
            await refresh_configured_admin_message(json_data)

        await delete_empty_matchmaking_team_channels(member.guild)


    @tasks.loop(minutes=120)
    async def updatePatchNotes():
        updateAvailable, updatedPatch, daysAgo, daysTillNext, fullUrl, imagePath = checkForNewPatchNotes(jsonFile, False)
        if daysAgo > 12:
            updatePatchNotes.change_interval(minutes=15)

        if updateAvailable:
            channel = await get_discord_channel(discordChannel)
            if not channel:
                return
            # print("There is a new patch available. Patch version:", updatedPatch, fullUrl, "Image saved at:", imagePath)
            message = (f'Patch {updatedPatch}\n'
                       f'{"tomorrow" if daysAgo == -1 else "today" if daysAgo == 0 else "yesterday" if daysAgo == 1 else f"{daysAgo} days ago"}\n'
                       f'{"" if daysAgo < 1 or daysTillNext == 13 or daysTillNext == 0 else f"next patch in: {daysTillNext} days"}\n'
                       f'{fullUrl}')
            if imagePath:
                with open(imagePath, 'rb') as f:
                    image = disnake.File(f)
                    await channel.send(message, file=image)
            else:
                await channel.send(message)


    @tasks.loop(seconds=15)
    async def captainDraftTimeout():
        try:
            await process_captain_draft_timeout()
        except Exception as error:
            log_event("matchmaking_captain_timeout_error", actor=system_actor(), status="error", summary=f"Captain draft timeout failed: {error}")


    @tasks.loop(seconds=60)
    async def updateRaceImage():
        json_data = ensure_admin_state(openJsonFile(jsonFile) or {})
        estimated_calls = estimate_leaderboard_api_calls(json_data)
        safeRequestLimit = requestLimit if requestLimit and requestLimit > 0 else 100
        calculatedInterval = math.floor(60 * numberOfSummoners(5) / (safeRequestLimit * 0.7))
        interval = max(calculatedInterval, 120)

        updateRaceImage.change_interval(seconds=interval)

        if riotBackoffRemaining() > 0:
            retryTime = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
            summary = f"Skipping Riot update until {retryTime} due to rate limit"
            print(summary)
            json_data = set_leaderboard_runtime_status(json_data, "normal", "rate_limited", estimated_calls, summary)
            log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"retryTime": retryTime})
            await refresh_configured_admin_message(json_data)
            return

        lastRunTime = json_data['runtime']
        # Set the timezone to Europe/London
        timezone = pytz.timezone('Europe/Madrid')
        currentTime = datetime.now(tz=timezone)
        dateStr = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%y")

        dailyTime = currentTime.replace(hour=dailyPostTimer, minute=0, second=0, microsecond=0).timestamp()

        # If it's past 9pm and last run time is before 9pm today, update the image
        if currentTime.timestamp() > dailyTime > lastRunTime:
            json_data['runtime'] = dailyTime
            writeToJsonFile(jsonFile, json_data)
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, True, returnData=True, generate=False)
            status = "updated" if summoners and (updated or force_leaderboard) else "no_changes" if summoners else "skipped"
            json_data = set_leaderboard_runtime_status(json_data, "daily", status, estimated_calls, None if summoners else "Daily leaderboard update returned no summoners.")
            log_event("leaderboard_update", actor=system_actor(), status="success" if summoners else "error", summary=f"Daily leaderboard update {status}.", details={"updated": bool(updated), "force": bool(force_leaderboard), "summoners": len(summoners or [])})
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    log_event("leaderboard_update", actor=system_actor(), status="error", summary="Leaderboard channel was not found.", details={"channelId": str(leaderboard_channel_id(json_data))})
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners, True, dateStr)
                writeToJsonFile(jsonFile, latest_json_data)
            await refresh_configured_admin_message()
        else:
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, False, returnData=True, generate=False)
            status = "updated" if summoners and (updated or force_leaderboard) else "no_changes" if summoners else "skipped"
            json_data = set_leaderboard_runtime_status(json_data, "normal", status, estimated_calls, None if summoners else "Leaderboard update returned no summoners.")
            log_event("leaderboard_update", actor=system_actor(), status="success" if summoners else "error", summary=f"Normal leaderboard update {status}.", details={"updated": bool(updated), "force": bool(force_leaderboard), "summoners": len(summoners or [])})
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    log_event("leaderboard_update", actor=system_actor(), status="error", summary="Leaderboard channel was not found.", details={"channelId": str(leaderboard_channel_id(json_data))})
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners)
                writeToJsonFile(jsonFile, latest_json_data)
            await refresh_configured_admin_message()


    @bot.slash_command(description="Full list of summoners")
    async def list(inter: ApplicationCommandInteraction):
        await inter.response.defer()
        jsonData = openJsonFile(jsonFile)
        summonerList = []
        for summoner in jsonData['summoners']:
            summonerList.append(summoner)
        await inter.send("\n".join(summonerList))


    @bot.slash_command(description="Show your cached leaderboard report")
    async def me(inter: ApplicationCommandInteraction, user: disnake.Member = None, private: bool = True):
        await inter.response.defer(ephemeral=private)
        target = user or inter.author
        json_data = ensure_admin_state(load_json_data())
        embed = personal_report_embed(json_data, target)
        if not embed:
            message = (
                f"{target.mention} does not have a linked leaderboard account yet. "
                "Use /link_discord to request a link, or ask an admin to link one from the administration panel."
            )
            log_event("personal_report_view", actor=interaction_actor(inter), status="error", summary=message, details={"targetUserId": str(target.id)})
            if private:
                await send_ephemeral_inter_send(inter, message)
            else:
                await inter.send(message)
            return

        log_event("personal_report_view", actor=interaction_actor(inter), status="success", summary=f"Cached personal report viewed for {target.display_name}.", details={"targetUserId": str(target.id)})
        summoner_name = primary_summoner_for_user(json_data, target.id)
        view = personal_report_view(json_data, summoner_name) if summoner_name else None
        if private:
            await send_ephemeral_inter_send(inter, embed=embed, view=view)
        else:
            await inter.send(embed=embed, view=view)


    @bot.slash_command(description="Patch notes")
    async def patch(inter: ApplicationCommandInteraction):
        await inter.response.defer()
        update_available, updated_patch, days_ago, days_till_next, full_url, image_path = checkForNewPatchNotes(jsonFile, True)
        if update_available:
            # print("There is a new patch available. Patch version:", updated_patch, full_url, "Image saved at:", image_path)
            message = (f'Patch {updated_patch}\n'
                       f'{"tomorrow" if days_ago == -1 else "today" if days_ago == 0 else "yesterday" if days_ago == 1 else f"{days_ago} days ago"}\n'
                       f'{"" if days_ago < 1 or days_till_next == 13 or days_till_next == 0 else f"next patch in: {days_till_next} days"}\n'
                       f'{full_url}')
            if image_path:
                with open(image_path, 'rb') as f:
                    image = disnake.File(f)
                    await inter.send(message, file=image)
            else:
                await inter.send(message)
        else:
            await inter.send("Could not fetch the latest patch notes.")


    @bot.slash_command(name="admin_matchmaking", description="Create or refresh the matchmaking message")
    async def matchmaking(inter: ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        message_id = await setup_matchmaking_message()
        if message_id:
            await send_ephemeral_inter_send(inter, f"Matchmaking message ready: {message_id}")
        else:
            await send_ephemeral_inter_send(inter, "Could not create the matchmaking message. Check the configured channel and bot permissions.")


    @bot.slash_command(name="admin_setup", description="Create or move the administration message")
    async def setup(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, "This command can only be used inside a server.")
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, "You need Manage Server permission to set up administration.")
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        message_id = await setup_admin_message(channel)
        log_event("admin_setup", actor=interaction_actor(inter), status="success", summary=f"Administration channel set to {channel.mention}.", details={"channelId": str(channel.id), "messageId": str(message_id)})
        await send_ephemeral_inter_send(inter, f"Administration channel set to {channel.mention}. Message ready: {message_id}")


    @bot.slash_command(name="admin_set_ranking_channel", description="Set the channel for the editable leaderboard message")
    async def setrankingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, "This command can only be used inside a server.")
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, "You need Manage Server permission to change bot channels.")
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        message = await configure_leaderboard_channel(channel, interaction_actor(inter))
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(name="admin_set_matchmaking_channel", description="Set the channel for the matchmaking message")
    async def setmatchmakingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, "This command can only be used inside a server.")
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, "You need Manage Server permission to change bot channels.")
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        message = await configure_matchmaking_channel(channel, interaction_actor(inter))
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(name="link_discord", description="Request linking your Discord to a leaderboard summoner")
    async def link_discord(inter: ApplicationCommandInteraction, name: str, tagline: str):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, name, tagline)
        if not summoner:
            message = f"{name}#{normalize_tagline(tagline)} has not been added"
            log_event("discord_link_request_created", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(inter.author.id)})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = request_discord_link(json_data, inter.author, summoner)
        if success:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_admin_message(json_data)
        log_event("discord_link_request_created", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(inter.author.id), "summoner": summoner})
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(name="admin_link_discord", description="Link a leaderboard summoner to a Discord user")
    async def linkdiscord(inter: ApplicationCommandInteraction, user: disnake.Member, name: str, tagline: str, primary: bool = True):
        await inter.response.defer(ephemeral=True)
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, name, tagline)
        if not summoner:
            message = f"{name}#{normalize_tagline(tagline)} has not been added"
            log_event("discord_link_created", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(user.id)})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = link_summoner_to_discord(json_data, user, summoner, primary)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_created", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(user.id), "summoner": summoner, "primary": primary})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(name="admin_unlink_discord", description="Unlink a leaderboard summoner from Discord")
    async def unlinkdiscord(inter: ApplicationCommandInteraction, name: str, tagline: str):
        await inter.response.defer(ephemeral=True)
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, name, tagline)
        if not summoner:
            message = f"{name}#{normalize_tagline(tagline)} has not been added"
            log_event("discord_link_removed", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = unlink_summoner_from_discord(json_data, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_removed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(name="admin_primary_discord", description="Set the primary summoner for a linked Discord user")
    async def primarydiscord(inter: ApplicationCommandInteraction, user: disnake.Member, name: str, tagline: str):
        await inter.response.defer(ephemeral=True)
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, name, tagline)
        if not summoner:
            message = f"{name}#{normalize_tagline(tagline)} has not been added"
            log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(user.id)})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = set_primary_summoner_for_user(json_data, user, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(user.id), "summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(description="Add summoner to the list")
    async def add(inter: ApplicationCommandInteraction, name: str, tagline: str, platform: str = commands.Param(choices=platforms), region: str = commands.Param(choices=regions)):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        if not leaderboard_chat_commands_enabled(json_data):
            message = "/add is disabled from App settings. Use the administration panel or enable the chat commands there."
            log_event("leaderboard_summoner_add", actor=interaction_actor(inter), status="error", summary=message, details={"name": name, "tagline": tagline, "platform": platform, "region": region})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = await add_summoner_to_data(name, tagline, platform, region)
        log_event("leaderboard_summoner_add", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"name": name, "tagline": tagline, "platform": platform, "region": region})
        await refresh_configured_admin_message()
        await send_ephemeral_inter_send(inter, message)


    @bot.slash_command(description="Remove summoner from the list")
    async def remove(inter: ApplicationCommandInteraction, name: str, tagline: str):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        if not leaderboard_chat_commands_enabled(json_data):
            message = "/remove is disabled from App settings. Use the administration panel or enable the chat commands there."
            log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="error", summary=message, details={"name": name, "tagline": tagline})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = remove_summoner_from_data(name, tagline)
        log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"name": name, "tagline": tagline})
        await refresh_configured_admin_message()
        await send_ephemeral_inter_send(inter, message)

    if not discordToken:
        raise RuntimeError("DISCORD_TOKEN is not configured.")
    bot.run(discordToken)
