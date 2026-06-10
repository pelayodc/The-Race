from datetime import datetime
import os
from types import SimpleNamespace
from urllib.parse import quote

import disnake
import requests

from discord_helpers import get_discord_channel, get_guild_member, send_ephemeral_response
from i18n import t
from linked_accounts import find_summoner_key, normalize_tagline, rebuild_discord_links_from_summoners
from solo_queue import add_solo_queue_icon, is_subscribed, subscription_targets, toggle_subscription
from state import ensure_admin_state, leaderboard_channel_id, load_json_data, utc_now_iso
from utils.auditUtils import log_event
from utils.commonUtils import discordChannel, jsonFile, outputPath, riotApKey
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


def hydrate_cached_recent_results(json_data, summoner, summoner_data):
    match_data_by_id = json_data.get("matchData") or {}
    for index, match_id in enumerate(summoner_data.get("recentMatchIds", [])[:5], start=1):
        match_data = match_data_by_id.get(match_id) or {}
        participants = match_data.get("info", {}).get("participants", [])
        participant = next(
            (
                participant
                for participant in participants
                if participant.get("puuid") == summoner.puuid
            ),
            None
        )
        if not participant:
            continue

        setattr(summoner, f"game{index}Win", participant.get("win"))
        setattr(summoner, f"game{index}Remake", participant.get("gameEndedInEarlySurrender", False))


def is_secondary_summoner(json_data, summoner):
    summoner_data = (json_data.get("summoners") or {}).get(summoner.fullName, {})
    return bool(summoner_data.get("discordUserId") and summoner_data.get("discordPrimary") is False)


def truncate_display_name(display_name):
    display_name = str(display_name)
    if len(display_name) > 18:
        return f"{display_name[:15]}..."
    return display_name


def escape_link_text(text):
    return str(text).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


async def discord_display_name_for_summoner(json_data, summoner, guild):
    summoner_data = (json_data.get("summoners") or {}).get(summoner.fullName, {})
    user_id = summoner_data.get("discordUserId")
    if not user_id:
        return None

    if guild:
        member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if not member and str(user_id).isdigit():
            member = await get_guild_member(guild, user_id)
        if member:
            return member.display_name

    return summoner_data.get("discordDisplayName")


async def leaderboard_display_name(json_data, summoner, guild, use_discord_display_names):
    if use_discord_display_names:
        discord_name = await discord_display_name_for_summoner(json_data, summoner, guild)
        if discord_name:
            return discord_name
    return summoner.name


async def leaderboard_embed(json_data, summoners, daily=False, date_str=None, guild=None, include_secondaries=False, use_discord_display_names=True, renumber_visible=True, title_key=None):
    rebuild_discord_links_from_summoners(json_data)
    title = t(json_data, "leaderboard.title")
    if title_key:
        title = t(json_data, title_key)
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
    visible_position = 0

    for summoner in summoners:
        if not include_secondaries and is_secondary_summoner(json_data, summoner):
            continue

        visible_position += 1
        rank_value = visible_position if renumber_visible else summoner.leaderboardPosition
        rank = f"#{rank_value}"
        raw_name = summoner.name
        tag = summoner.tagline

        display_name = await leaderboard_display_name(json_data, summoner, guild, use_discord_display_names)
        display_name = truncate_display_name(display_name)

        safe_game_name = quote(raw_name, safe="")
        safe_tag = quote(tag, safe="")
        linked_name = f"[{escape_link_text(display_name)}](https://dpm.lol/{safe_game_name}-{safe_tag})"
        name = add_solo_queue_icon(json_data, summoner, linked_name)

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


class LeaderboardView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        json_data = ensure_admin_state(load_json_data())
        for child in self.children:
            if getattr(child, "custom_id", None) == "leaderboard:full":
                child.label = t(json_data, "leaderboard.full_button")
            elif getattr(child, "custom_id", None) == "leaderboard:subscriptions":
                child.label = t(json_data, "solo_queue.subscriptions_button")

    @disnake.ui.button(label="Full leaderboard", style=disnake.ButtonStyle.blurple, custom_id="leaderboard:full")
    async def full_leaderboard(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        json_data = ensure_admin_state(load_json_data())
        summoners = cached_leaderboard_summoners(json_data)
        embed = await leaderboard_embed(
            json_data,
            summoners,
            guild=inter.guild,
            include_secondaries=True,
            use_discord_display_names=False,
            renumber_visible=False,
            title_key="leaderboard.full_title"
        )
        await send_ephemeral_response(inter, embed=embed)

    @disnake.ui.button(label="Subscriptions", style=disnake.ButtonStyle.gray, custom_id="leaderboard:subscriptions")
    async def subscriptions(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        json_data = ensure_admin_state(load_json_data())
        summoners = cached_leaderboard_summoners(json_data)
        targets = subscription_targets(json_data, summoners)
        if not targets:
            await send_ephemeral_response(inter, t(json_data, "solo_queue.no_targets"))
            return
        await send_ephemeral_response(
            inter,
            t(json_data, "solo_queue.menu_description"),
            view=SoloQueueSubscriptionView(json_data, inter.author.id, targets)
        )


class SoloQueueSubscriptionSelect(disnake.ui.Select):
    def __init__(self, json_data, subscriber_id, targets):
        self.subscriber_id = str(subscriber_id)
        self.targets = {target["playerId"]: target for target in targets}
        options = []
        for target in targets:
            subscribed = is_subscribed(json_data, self.subscriber_id, target["playerId"])
            label_prefix = "✓ " if subscribed else ""
            options.append(disnake.SelectOption(
                label=f"{label_prefix}{target['displayName']}"[:100],
                description=target["summonerFullName"][:100],
                value=target["playerId"]
            ))
        super().__init__(
            placeholder=t(json_data, "solo_queue.select_placeholder"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="leaderboard:subscriptions:select"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        target = self.targets.get(self.values[0])
        if not target:
            await inter.edit_original_message(content=t(json_data, "solo_queue.target_missing"), view=None)
            return
        success, message = await toggle_subscription(json_data, inter.author.id, target)
        latest_json_data = ensure_admin_state(load_json_data())
        targets = subscription_targets(latest_json_data, cached_leaderboard_summoners(latest_json_data))
        view = SoloQueueSubscriptionView(latest_json_data, inter.author.id, targets) if targets else None
        await inter.edit_original_message(content=message, view=view)


class SoloQueueSubscriptionView(disnake.ui.View):
    def __init__(self, json_data, subscriber_id, targets):
        super().__init__(timeout=120)
        self.add_item(SoloQueueSubscriptionSelect(json_data, subscriber_id, targets))


async def send_or_edit_leaderboard(channel, json_data, summoners, daily=False, date_str=None):
    embed = await leaderboard_embed(
        json_data,
        summoners,
        daily=daily,
        date_str=date_str,
        guild=getattr(channel, "guild", None),
        include_secondaries=False,
        use_discord_display_names=True,
        renumber_visible=True
    )
    view = LeaderboardView()
    message_id = json_data.get("leaderboardMessageId")

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=embed, view=view)
            return message.id
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException, ValueError):
            pass

    message = await channel.send(embed=embed, view=view)
    return message.id

async def send_daily_rank_image(channel, json_data):
    image_path = outputPath("Daily Rank list.png")
    if not os.path.exists(image_path):
        return False, "Daily Rank list.png was not generated."

    try:
        with open(image_path, "rb") as file:
            message = await channel.send(file=disnake.File(file, filename="Daily Rank list.png"))
        return True, str(message.id)
    except (disnake.Forbidden, disnake.HTTPException, OSError) as error:
        return False, str(error)

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
    estimated_calls += len([
        data for data in summoners.values()
        if data.get("discordUserId") and data.get("discordPrimary") is not False and data.get("tier") and data.get("rank")
    ])
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

def set_daily_image_status(status, message_id=None, error=None, channel_id=None):
    latest_json_data = load_json_data()
    latest_json_data["leaderboardLastDailyImageAt"] = utc_now_iso()
    latest_json_data["leaderboardLastDailyImageStatus"] = status
    latest_json_data["leaderboardLastDailyImageMessageId"] = message_id
    latest_json_data["leaderboardLastDailyImageChannelId"] = channel_id
    if error:
        latest_json_data["leaderboardLastDailyImageError"] = error
    else:
        latest_json_data["leaderboardLastDailyImageError"] = None
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
            puuid=data.get("puuid"),
            leaderboardPosition=data.get("leaderboardPosition", 100),
            tier=data.get("tier"),
            rank=data.get("rank"),
            leaguePoints=data.get("leaguePoints", 0),
            score=data.get("score", 0),
            deltaScore=0,
            deltaDailyScore=0,
            deltaGamesPlayed=0,
            deltaDailyGamesPlayed=0,
            deltaLeaderboardPosition=0,
            deltaDailyLeaderboardPosition=0,
        )
        hydrate_cached_recent_results(json_data, summoner, data)
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

async def force_daily_rank_image(actor=None):
    json_data = ensure_admin_state(load_json_data())
    if riotBackoffRemaining() > 0:
        retry_time = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
        message = t(json_data, "leaderboard.refresh_backoff", time=retry_time)
        log_event("leaderboard_daily_image_force", actor=actor, status="error", summary=message)
        return False, message, json_data

    summoners, updated = update(True, True, returnData=True, generate=True)
    status = "updated" if summoners else "skipped"
    json_data = set_leaderboard_runtime_status(json_data, "daily_forced", status, estimate_leaderboard_api_calls(json_data), None if summoners else "Forced daily image refresh returned no summoners.")
    if not summoners:
        message = t(json_data, "leaderboard.daily_image_no_summoners")
        set_daily_image_status("error", error=message, channel_id=str(discordChannel))
        log_event("leaderboard_daily_image_force", actor=actor, status="error", summary=message)
        return False, message, load_json_data()

    channel = await get_discord_channel(discordChannel)
    if not channel:
        message = t(json_data, "leaderboard.daily_image_channel_not_found")
        set_daily_image_status("error", error=message, channel_id=str(discordChannel))
        log_event("leaderboard_daily_image_force", actor=actor, status="error", summary=message, details={"channelId": str(discordChannel)})
        return False, message, load_json_data()

    sent, result = await send_daily_rank_image(channel, json_data)
    if sent:
        latest_json_data = set_daily_image_status("sent", message_id=result, channel_id=str(discordChannel))
        message = t(json_data, "leaderboard.daily_image_force_done", updated=bool(updated), count=len(summoners), message_id=result)
        log_event("leaderboard_daily_image_force", actor=actor, status="success", summary=message, details={"updated": bool(updated), "summoners": len(summoners), "channelId": str(discordChannel), "messageId": result})
        return True, message, latest_json_data

    latest_json_data = set_daily_image_status("error", error=result, channel_id=str(discordChannel))
    message = t(json_data, "leaderboard.daily_image_send_failed", error=result)
    log_event("leaderboard_daily_image_force", actor=actor, status="error", summary=message, details={"channelId": str(discordChannel)})
    return False, message, latest_json_data

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
