import math
from datetime import datetime
from io import BytesIO

import disnake

from bot_runtime import MAX_SELECT_OPTIONS
from discord_helpers import send_ephemeral_followup, send_ephemeral_response
from i18n import t
from leaderboard import rank_icon
from linked_accounts import linked_summoners_for_user, primary_summoner_for_user, rebuild_discord_links_from_summoners
from state import ensure_admin_state, load_json_data
from utils.auditUtils import interaction_actor, log_event
from utils.commonUtils import jsonFile, riotApKey
from utils.dataUtils import riot_get
from utils.drawUtils import generateGoldGraphImage
from utils.jsonUtils import writeToJsonFile


def format_cached_rank(summoner_data):
    tier = summoner_data.get("tier")
    rank = summoner_data.get("rank")
    league_points = summoner_data.get("leaguePoints")
    if not tier or rank is None or league_points is None:
        return t({}, "personal_report.no_rank")
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
        position_text = t({}, "personal_report.positions_positive", count=position_delta)
    elif position_delta < 0:
        position_text = t({}, "personal_report.positions", count=position_delta)
    else:
        position_text = t({}, "personal_report.no_position_change")

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
        return t({}, "personal_report.no_match_details")

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
        title=t(json_data, "personal_report.title", member=member.display_name),
        description=t(json_data, "personal_report.description", summoner=summoner_name),
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )
    embed.set_author(name="The Race")
    embed.add_field(name=t(json_data, "personal_report.rank"), value=format_cached_rank(summoner_data), inline=False)
    embed.add_field(name=t(json_data, "personal_report.leaderboard"), value=t(json_data, "personal_report.leaderboard_value", position=leaderboard_position, score=summoner_data.get("score", 0)), inline=True)
    embed.add_field(name=t(json_data, "personal_report.ranked_games"), value=t(json_data, "personal_report.ranked_games_value", games=games_played if games_played is not None else "-"), inline=True)
    embed.add_field(name=t(json_data, "personal_report.daily_change"), value=cached_daily_change_text(summoner_data), inline=True)

    if linked_summoners:
        linked_text = "\n".join(
            f"{'•' if linked == summoner_name else '-'} {linked}"
            for linked in linked_summoners[:8]
        )
        if len(linked_summoners) > 8:
            linked_text += f"\n...and {len(linked_summoners) - 8} more."
        embed.add_field(name=t(json_data, "personal_report.linked_accounts"), value=linked_text[:1024], inline=False)

    embed.add_field(name=t(json_data, "personal_report.last_cached_games"), value=cached_games_summary(games), inline=False)
    if games:
        game_lines = "\n".join(format_cached_game_line(index, game) for index, game in enumerate(games, start=1))
        embed.add_field(name=t(json_data, "personal_report.recent_match_detail"), value=game_lines[:1024], inline=False)
    else:
        embed.add_field(name=t(json_data, "personal_report.recent_match_detail"), value=t(json_data, "personal_report.no_recent_match_ids"), inline=False)

    embed.set_footer(text=t(json_data, "personal_report.footer", update=last_update, status=last_status))
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
    result = t(json_data, "personal_report.remake") if game.get("remake") else t(json_data, "personal_report.victory") if participant.get("win") else t(json_data, "personal_report.defeat")
    kda = f"{participant.get('kills', 0)}/{participant.get('deaths', 0)}/{participant.get('assists', 0)}"
    duration = format_match_duration(info.get("gameDuration"))
    match_id = game.get("matchId")

    embed = disnake.Embed(
        title=f"Game {game_index + 1}: {participant.get('championName', 'Unknown')} - {result}",
        description=f"Match: `{match_id}`\nPlayer: **{summoner_name}**\nKDA: **{kda}** - Duration: **{duration}**",
        colour=disnake.Colour.green() if participant.get("win") else disnake.Colour.red(),
        timestamp=datetime.now()
    )
    embed.add_field(name=t(json_data, "personal_report.personal_performance"), value=personal_match_summary(participant, info.get("gameDuration", 0)), inline=True)

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

    embed.set_footer(text=t(json_data, "personal_report.badges_footer"))
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
        title=t(json_data, "personal_report.team_comparison", index=game_index + 1),
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
        result = t(json_data, "personal_report.win") if (teams.get(team_id) or {}).get("win") else t(json_data, "personal_report.loss")
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
    embed.set_footer(text=t(json_data, "personal_report.objectives_footer"))
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
        json_data = ensure_admin_state(load_json_data())
        label = t(json_data, "personal_report.game_button", index=index + 1)
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
        super().__init__(label=t(ensure_admin_state(load_json_data()), "personal_report.gold_graph"), style=disnake.ButtonStyle.green, row=0)
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
            await send_ephemeral_followup(inter, t(json_data, "personal_report.no_timeline"))
            return

        file = disnake.File(image_buffer, filename=f"{match_id}-gold.png")
        await send_ephemeral_followup(inter, content=status, file=file)

class CompareTeamsButton(disnake.ui.Button):
    def __init__(self, summoner_name, game_index):
        super().__init__(label=t(ensure_admin_state(load_json_data()), "personal_report.compare_teams"), style=disnake.ButtonStyle.blurple, row=0)
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
        json_data = ensure_admin_state(load_json_data())
        label = t(json_data, "personal_report.prev_match") if direction < 0 else t(json_data, "personal_report.next_match")
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
