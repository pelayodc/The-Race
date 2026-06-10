from datetime import datetime, timezone
from urllib.parse import quote

import disnake

from bot_runtime import MAX_SELECT_OPTIONS, bot
from discord_helpers import get_guild_member
from i18n import t
from state import ensure_admin_state, load_json_data, utc_now_iso
from utils.auditUtils import log_event, system_actor
from utils.commonUtils import riotApKey
from utils.dataUtils import riot_get_status
from utils.jsonUtils import writeToJsonFile
from utils.commonUtils import jsonFile


SOLO_QUEUE_ID = 420
SOLO_QUEUE_ICON = "🎮"


def ensure_solo_queue_state(json_data):
    ensure_admin_state(json_data)
    json_data.setdefault("soloQueueStatus", {})
    json_data.setdefault("soloQueueSubscriptions", {})
    json_data.setdefault("soloQueuePendingFinish", {})
    return json_data


def dpm_url(summoner):
    return f"https://dpm.lol/{quote(summoner.name, safe='')}-{quote(summoner.tagline, safe='')}"


def summoner_data(json_data, summoner):
    return (json_data.get("summoners") or {}).get(summoner.fullName, {})


def is_visible_primary_summoner(json_data, summoner):
    data = summoner_data(json_data, summoner)
    return bool(data.get("discordUserId") and data.get("discordPrimary") is not False)


def visible_solo_queue_targets(json_data, summoners, limit=None):
    targets = []
    seen_player_ids = set()
    for summoner in sorted(summoners, key=lambda item: item.leaderboardPosition):
        data = summoner_data(json_data, summoner)
        player_id = str(data.get("discordUserId") or "")
        if not player_id or player_id in seen_player_ids:
            continue
        if not is_visible_primary_summoner(json_data, summoner):
            continue
        seen_player_ids.add(player_id)
        targets.append({
            "playerId": player_id,
            "summoner": summoner,
            "summonerFullName": summoner.fullName,
            "displayName": data.get("discordDisplayName") or summoner.name,
        })
        if limit and len(targets) >= limit:
            break
    return targets


async def refresh_target_display_names(targets, guild):
    if not guild:
        return targets

    refreshed = []
    for target in targets:
        player_id = str(target.get("playerId") or "")
        member = guild.get_member(int(player_id)) if player_id.isdigit() else None
        if not member and player_id.isdigit():
            member = await get_guild_member(guild, player_id)
        if member:
            target = {**target, "displayName": member.display_name}
        refreshed.append(target)
    return refreshed


def solo_queue_player_id(json_data, summoner):
    data = summoner_data(json_data, summoner)
    if data.get("discordPrimary") is False:
        return None
    player_id = data.get("discordUserId")
    return str(player_id) if player_id else None


def is_player_in_solo_queue(json_data, player_id):
    status = (json_data.get("soloQueueStatus") or {}).get(str(player_id), {})
    return bool(status.get("inGame"))


def add_solo_queue_icon(json_data, summoner, linked_name):
    player_id = solo_queue_player_id(json_data, summoner)
    if player_id and is_player_in_solo_queue(json_data, player_id):
        return f"{SOLO_QUEUE_ICON} {linked_name}"
    return linked_name


def subscription_targets(json_data, summoners):
    return visible_solo_queue_targets(json_data, summoners, MAX_SELECT_OPTIONS)


def subscriptions_for_player(json_data, player_id):
    player_id = str(player_id)
    subscribers = []
    for subscriber_id, player_ids in (json_data.get("soloQueueSubscriptions") or {}).items():
        if player_id in [str(value) for value in player_ids]:
            subscribers.append(str(subscriber_id))
    return subscribers


def is_subscribed(json_data, subscriber_id, player_id):
    player_ids = (json_data.get("soloQueueSubscriptions") or {}).get(str(subscriber_id), [])
    return str(player_id) in [str(value) for value in player_ids]


async def send_dm(user_id, message):
    try:
        user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
        await user.send(message)
        return True
    except (disnake.Forbidden, disnake.NotFound, disnake.HTTPException, ValueError):
        return False


async def toggle_subscription(json_data, subscriber_id, target):
    ensure_solo_queue_state(json_data)
    subscriber_id = str(subscriber_id)
    player_id = str(target["playerId"])
    subscriptions = json_data["soloQueueSubscriptions"].setdefault(subscriber_id, [])
    subscriptions = [str(value) for value in subscriptions]

    if player_id in subscriptions:
        subscriptions.remove(player_id)
        if subscriptions:
            json_data["soloQueueSubscriptions"][subscriber_id] = subscriptions
        else:
            json_data["soloQueueSubscriptions"].pop(subscriber_id, None)
        writeToJsonFile(jsonFile, json_data)
        return True, t(json_data, "solo_queue.unsubscribed", summoner=target["summonerFullName"])

    message = t(json_data, "solo_queue.dm_confirmed", summoner=target["summonerFullName"])
    if not await send_dm(subscriber_id, message):
        return False, t(json_data, "solo_queue.dm_closed")

    subscriptions.append(player_id)
    json_data["soloQueueSubscriptions"][subscriber_id] = subscriptions
    writeToJsonFile(jsonFile, json_data)
    return True, t(json_data, "solo_queue.subscribed", summoner=target["summonerFullName"])


async def fetch_active_game(summoner):
    ok, data, retry_after, status = riot_get_status(
        f"https://{summoner.platform}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{summoner.puuid}?api_key={riotApKey}",
        f"active game for {summoner.fullName}",
        allowed_statuses=[404]
    )
    if not ok:
        return None
    if status == 404:
        return {"inGame": False}
    return {
        "inGame": data.get("gameQueueConfigId") == SOLO_QUEUE_ID,
        "gameId": str(data.get("gameId") or ""),
        "gameStartTime": data.get("gameStartTime"),
        "queueId": data.get("gameQueueConfigId"),
    }


def match_participant_for_game(json_data, summoner, game_id):
    if not game_id:
        return None
    for match_id in summoner_data(json_data, summoner).get("recentMatchIds", []):
        match_data = (json_data.get("matchData") or {}).get(match_id) or {}
        info = match_data.get("info") or {}
        if str(info.get("gameId") or "") != str(game_id) and not str(match_id).endswith(str(game_id)):
            continue
        for participant in info.get("participants", []):
            if participant.get("puuid") == summoner.puuid:
                return participant
    return None


def finish_message(json_data, status, summoner, participant):
    result = t(json_data, "solo_queue.win") if participant.get("win") else t(json_data, "solo_queue.loss")
    lp_delta = (summoner.score or 0) - int(status.get("startScore") or 0)
    lp_text = f"+{lp_delta}" if lp_delta > 0 else str(lp_delta)
    return t(
        json_data,
        "solo_queue.dm_finished",
        summoner=summoner.fullName,
        result=result,
        lp_delta=lp_text,
        url=dpm_url(summoner)
    )


async def notify_subscribers(json_data, player_id, message, notified_key=None, status=None):
    sent = []
    for subscriber_id in subscriptions_for_player(json_data, player_id):
        if status and subscriber_id in status.get(notified_key or "", []):
            continue
        if await send_dm(subscriber_id, message):
            sent.append(subscriber_id)
    return sent


async def handle_game_started(json_data, target, active_game):
    player_id = target["playerId"]
    summoner = target["summoner"]
    status = {
        "inGame": True,
        "gameId": active_game.get("gameId"),
        "summonerFullName": summoner.fullName,
        "startedAt": utc_now_iso(),
        "gameStartTime": active_game.get("gameStartTime"),
        "startScore": summoner.score,
        "startLeaguePoints": summoner.leaguePoints,
        "notifiedStartUserIds": [],
    }
    message = t(
        json_data,
        "solo_queue.dm_started",
        summoner=summoner.fullName,
        tier=summoner.tier,
        rank=summoner.rank,
        lp=summoner.leaguePoints,
        url=dpm_url(summoner)
    )
    status["notifiedStartUserIds"] = await notify_subscribers(json_data, player_id, message)
    json_data["soloQueueStatus"][player_id] = status
    log_event("solo_queue_started", actor=system_actor(), status="success", summary=f"{summoner.fullName} started a SoloQ.", details={"playerId": player_id, "gameId": status.get("gameId")})


async def handle_game_finished(json_data, target, previous_status):
    player_id = target["playerId"]
    summoner = target["summoner"]
    participant = match_participant_for_game(json_data, summoner, previous_status.get("gameId"))
    if not participant:
        json_data["soloQueuePendingFinish"][player_id] = {
            **previous_status,
            "endedDetectedAt": utc_now_iso(),
            "summonerFullName": summoner.fullName,
        }
        json_data["soloQueueStatus"][player_id] = {"inGame": False, "lastCheckedAt": utc_now_iso()}
        return

    message = finish_message(json_data, previous_status, summoner, participant)
    sent = await notify_subscribers(json_data, player_id, message)
    json_data["soloQueuePendingFinish"].pop(player_id, None)
    json_data["soloQueueStatus"][player_id] = {"inGame": False, "lastCheckedAt": utc_now_iso(), "lastFinishedAt": utc_now_iso()}
    log_event("solo_queue_finished", actor=system_actor(), status="success", summary=f"{summoner.fullName} finished a SoloQ.", details={"playerId": player_id, "gameId": previous_status.get("gameId"), "notified": sent})


async def retry_pending_finishes(json_data, target):
    player_id = target["playerId"]
    pending = (json_data.get("soloQueuePendingFinish") or {}).get(player_id)
    if not pending:
        return False
    participant = match_participant_for_game(json_data, target["summoner"], pending.get("gameId"))
    if not participant:
        return False
    message = finish_message(json_data, pending, target["summoner"], participant)
    sent = await notify_subscribers(json_data, player_id, message)
    json_data["soloQueuePendingFinish"].pop(player_id, None)
    json_data["soloQueueStatus"][player_id] = {"inGame": False, "lastCheckedAt": utc_now_iso(), "lastFinishedAt": utc_now_iso()}
    log_event("solo_queue_finished", actor=system_actor(), status="success", summary=f"{target['summonerFullName']} finished a pending SoloQ.", details={"playerId": player_id, "gameId": pending.get("gameId"), "notified": sent})
    return True


async def update_solo_queue_status(json_data, summoners):
    ensure_solo_queue_state(json_data)
    changed = False
    targets = visible_solo_queue_targets(json_data, summoners)
    target_ids = {target["playerId"] for target in targets}

    for player_id in list(json_data["soloQueueStatus"].keys()):
        if player_id not in target_ids:
            json_data["soloQueueStatus"].pop(player_id, None)
            changed = True

    for target in targets:
        if await retry_pending_finishes(json_data, target):
            changed = True
        active_game = await fetch_active_game(target["summoner"])
        if active_game is None:
            continue

        player_id = target["playerId"]
        previous = json_data["soloQueueStatus"].get(player_id, {})
        was_in_game = bool(previous.get("inGame"))
        is_in_game = bool(active_game.get("inGame"))

        if is_in_game and (not was_in_game or str(previous.get("gameId")) != str(active_game.get("gameId"))):
            await handle_game_started(json_data, target, active_game)
            changed = True
        elif not is_in_game and was_in_game:
            await handle_game_finished(json_data, target, previous)
            changed = True
        else:
            json_data["soloQueueStatus"][player_id] = {**previous, "inGame": is_in_game, "lastCheckedAt": utc_now_iso()}

    if changed:
        writeToJsonFile(jsonFile, json_data)
    return changed, json_data
