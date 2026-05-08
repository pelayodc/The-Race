from datetime import datetime
from types import SimpleNamespace

import disnake
import requests

from discord_helpers import get_discord_channel
from i18n import t
from linked_accounts import find_summoner_key, normalize_tagline, rebuild_discord_links_from_summoners
from state import ensure_admin_state, leaderboard_channel_id, load_json_data, utc_now_iso
from utils.auditUtils import log_event
from utils.commonUtils import jsonFile, riotApKey
from utils.dataUtils import riotBackoffRemaining, riotBackoffTimestamp, update
from utils.jsonUtils import openJsonFile, writeToJsonFile


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
    json_data = load_json_data()
    title = t(json_data, "leaderboard.title")
    if daily:
        title = t(json_data, "leaderboard.daily_title", date=date_str)

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

    embed.add_field(name=t(json_data, "leaderboard.summoners"), value="\n".join(summoner_lines) or "-", inline=True)
    embed.add_field(name=t(json_data, "leaderboard.ranks"), value="\n".join(rank_lines) or "-", inline=True)
    embed.add_field(name=t(json_data, "leaderboard.last_5"), value="\n".join(results_lines) or "-", inline=True)
    embed.set_footer(text=t(json_data, "leaderboard.updated_footer"))
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

def format_summoner_summary(json_data):
    summoners = [summoner for summoner in (json_data.get("summoners") or {}).keys()]
    if not summoners:
        return t(json_data, "leaderboard.no_summoners")

    visible_summoners = summoners[:10]
    summary = "\n".join(f"- {summoner}" for summoner in visible_summoners)
    if len(summoners) > len(visible_summoners):
        summary += f"\n...and {len(summoners) - len(visible_summoners)} more."
    return summary

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

async def force_leaderboard_refresh(actor=None):
    json_data = ensure_admin_state(load_json_data())
    if riotBackoffRemaining() > 0:
        retry_time = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
        message = t(json_data, "leaderboard.refresh_backoff", time=retry_time)
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message)
        return False, message, json_data

    summoners, updated = update(True, False, returnData=True, generate=False)
    status = "updated" if summoners else "skipped"
    json_data = set_leaderboard_runtime_status(json_data, "normal", status, estimate_leaderboard_api_calls(json_data), None if summoners else "Manual leaderboard refresh returned no summoners.")
    if not summoners:
        message = t(json_data, "leaderboard.refresh_no_summoners")
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message)
        return False, message, json_data

    channel = await get_discord_channel(leaderboard_channel_id(json_data))
    if not channel:
        message = t(json_data, "leaderboard.channel_not_found")
        log_event("leaderboard_force_refresh", actor=actor, status="error", summary=message, details={"channelId": str(leaderboard_channel_id(json_data))})
        return False, message, json_data

    latest_json_data = openJsonFile(jsonFile) or json_data
    latest_json_data["leaderboardMessageId"] = await send_or_edit_leaderboard(channel, latest_json_data, summoners)
    writeToJsonFile(jsonFile, latest_json_data)
    message = t(json_data, "leaderboard.refresh_done", updated=bool(updated), count=len(summoners))
    log_event("leaderboard_force_refresh", actor=actor, status="success", summary=message, details={"updated": bool(updated), "summoners": len(summoners)})
    return True, message, latest_json_data

async def add_summoner_to_data(name, tagline, platform, region):
    json_data = load_json_data()
    json_data.setdefault("summoners", {})
    tagline = normalize_tagline(tagline)
    summoner_full_name = f"{name}#{tagline}"

    if find_summoner_key(json_data, name, tagline):
        return False, t(json_data, "leaderboard.already_added", summoner=summoner_full_name)

    response = requests.get(
        f'https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tagline}?api_key={riotApKey}'
    )
    if response.status_code != 200:
        return False, t(json_data, "leaderboard.invalid_summoner", summoner=summoner_full_name)

    account_data = response.json()
    summoner_full_name = account_data['gameName'] + '#' + account_data['tagLine']
    summoner_puuid = account_data['puuid']

    response = requests.get(
        f'https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{summoner_puuid}?api_key={riotApKey}'
    )
    if response.status_code != 200:
        return False, t(json_data, "leaderboard.profile_fetch_failed", summoner=summoner_full_name)

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
    return True, t(json_data, "leaderboard.added", summoner=summoner_full_name)

def remove_summoner_from_data(name, tagline):
    json_data = load_json_data()
    summoner_key = find_summoner_key(json_data, name, tagline)
    if not summoner_key:
        return False, t(json_data, "leaderboard.not_added", summoner=f"{name}#{normalize_tagline(tagline)}")

    del json_data["summoners"][summoner_key]
    rebuild_discord_links_from_summoners(json_data)
    writeToJsonFile(jsonFile, json_data)
    return True, t(json_data, "leaderboard.removed", summoner=summoner_key)
