"""Riot collection and durable Discord delivery for challenge editions."""

import asyncio
import logging
import math
from urllib.parse import quote

import disnake
import requests

from challenge_store import read_challenges, edit_challenges
from soloq_challenge import phase, timestamp, record_match, pending, match_week
from utils.commonUtils import riotApKey, version


log = logging.getLogger(__name__)
_backoff = 0
_catalogs = {}
_tick_lock = asyncio.Lock()
_budget = None


async def riot_json(url):
    global _backoff, _budget
    from utils.dataUtils import riotBackoffRemaining
    if timestamp() < _backoff or riotBackoffRemaining() > 0:
        raise RuntimeError("Riot backoff")
    if _budget is not None:
        if _budget <= 0:
            raise RuntimeError("Challenge request budget exhausted")
        _budget -= 1
    response = await asyncio.to_thread(requests.get, url, headers={"X-Riot-Token": riotApKey}, timeout=20)
    if response.status_code == 429:
        _backoff = timestamp() + int(response.headers.get("Retry-After", 120))
    response.raise_for_status()
    return response.json()


async def champion_catalog():
    key = "champions"
    if key not in _catalogs:
        response = await asyncio.to_thread(requests.get, f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json", timeout=20)
        response.raise_for_status()
        _catalogs[key] = response.json()["data"]
    return _catalogs[key]


async def boot_ids(game_version):
    minor = ".".join(game_version.split(".")[:2])
    if minor not in _catalogs:
        response = await asyncio.to_thread(requests.get, "https://ddragon.leagueoflegends.com/api/versions.json", timeout=20)
        response.raise_for_status()
        patch = next((v for v in response.json() if v.startswith(minor + ".")), None)
        if not patch:
            return None
        response = await asyncio.to_thread(requests.get, f"https://ddragon.leagueoflegends.com/cdn/{patch}/data/en_US/item.json", timeout=20)
        response.raise_for_status()
        _catalogs[minor] = {int(key) for key, item in response.json()["data"].items() if "Boots" in item.get("tags", [])}
    return _catalogs[minor]


async def resolve_account(full_name, platform):
    from discord_menus import PLATFORM_REGIONS
    name, tag = full_name.strip().rsplit("#", 1)
    region = PLATFORM_REGIONS[platform]
    data = await riot_json(f"https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(name, safe='')}/{quote(tag, safe='')}")
    return {"name": f"{data['gameName']}#{data['tagLine']}", "puuid": data["puuid"], "platform": platform, "region": region}


async def collect_player(guild_id, edition_id, user_id):
    from personal_report import match_mvp_participant_id
    edition = next(e for e in read_challenges(guild_id)["editions"] if e["id"] == edition_id)
    player = edition["players"][user_id]
    if not player.get("approved", True):
        return
    now = timestamp()
    if now < edition["starts_at"]:
        return
    # Paginate the complete competition window, not the leaderboard's five-game cache.
    ids = []
    offset = 0
    while True:
        page = await riot_json(f"https://{player['region']}.api.riotgames.com/lol/match/v5/matches/by-puuid/{player['puuid']}/ids?queue=420&startTime={int(edition['starts_at'])}&endTime={math.ceil(min(now, edition['ends_at']))}&start={offset}&count=100")
        ids.extend(page)
        if len(page) < 100:
            break
        offset += 100
    known = set(player.get("seen", [])) | {r["id"] for r in player["records"]}
    for match_id in reversed(ids):
        if match_id in known:
            continue
        match = await riot_json(f"https://{player['region']}.api.riotgames.com/lol/match/v5/matches/{match_id}")
        # Failed requests abort this player's chronological scan; later matches never jump the queue.
        current = next(e for e in read_challenges(guild_id)["editions"] if e["id"] == edition_id)
        timeline, boots = None, None
        start = match["info"]["gameStartTimestamp"] / 1000
        week = match_week(current, start)
        counted = sum(r["week"] == week for r in current["players"][user_id]["records"])
        tasks = sorted([s for s in pending(current, user_id) if s.get("launched_at", now) <= start], key=lambda s: s["launched_at"])
        task = tasks[0] if tasks else None
        if week is not None and counted < current["config"]["weekly_limit"] and task and task["state"] == "assigned" and task.get("task") in ("no_boots", "no_starter"):
            try:
                timeline = await riot_json(f"https://{player['region']}.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline")
                if task["task"] == "no_boots":
                    boots = await boot_ids(match["info"].get("gameVersion", ""))
            except (requests.RequestException, RuntimeError, ValueError):
                pass  # Domain validation will leave this evidence for moderation.
        mvp = match_mvp_participant_id(match["info"]["participants"])
        def apply(document):
            target = next(e for e in document["editions"] if e["id"] == edition_id)
            if not target.get("cancelled") and user_id in target["players"]:
                record_match(target, user_id, match, mvp, timestamp(), timeline, boots)
                target["players"][user_id].setdefault("seen", []).append(match_id)
        edit_challenges(guild_id, apply)
    def scanned(document):
        target = next(e for e in document["editions"] if e["id"] == edition_id)
        if user_id in target["players"]:
            target["players"][user_id]["scan_at"] = now
    edit_challenges(guild_id, scanned)


async def ensure_channels(guild, edition):
    from challenge_ui import ChallengePublicView, leaderboard_text, registration_text, shell_board_text, tr, refresh_display_names
    await refresh_display_names(edition, guild)
    guild_id, edition_id = guild.id, edition["id"]
    category = guild.get_channel(edition["channels"].get("category", 0))
    if category is None:
        category = await guild.create_category("SoloQ Challenge", reason="SoloQ Challenge")
        def remember(document):
            next(e for e in document["editions"] if e["id"] == edition_id)["channels"]["category"] = category.id
        edit_challenges(guild_id, remember)
    stage = phase(edition, timestamp())
    kinds = ("registration", "admin") if stage == "registration" else ("ranking", "shells", "history", "admin")
    for kind in kinds:
        channel = guild.get_channel(edition["channels"].get(kind, 0))
        if channel is None:
            # Recover channels if the process stopped between Discord creation and DB commit.
            channel = next((c for c in category.text_channels if c.name == f"sq-{kind}"), None)
        if channel is None:
            overwrites = {guild.default_role: disnake.PermissionOverwrite(view_channel=kind != "admin", send_messages=False), guild.me: disnake.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True, manage_channels=True)}
            if kind == "admin":
                for role in guild.roles:
                    if role.permissions.manage_guild or role.permissions.administrator:
                        overwrites[role] = disnake.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            channel = await guild.create_text_channel(f"sq-{kind}", category=category, overwrites=overwrites)
        elif kind == "admin":
            # Keep the private channel private when administrator roles change.
            overwrites = {guild.default_role: disnake.PermissionOverwrite(view_channel=False), guild.me: disnake.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
            for role in guild.roles:
                if role.permissions.manage_guild or role.permissions.administrator:
                    overwrites[role] = disnake.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            await channel.edit(overwrites=overwrites)
        def remember(document):
            next(e for e in document["editions"] if e["id"] == edition_id)["channels"][kind] = channel.id
        edit_challenges(guild_id, remember)
        edition["channels"][kind] = channel.id
        if kind == "history":
            continue
        content = {"registration": registration_text, "ranking": leaderboard_text, "shells": shell_board_text}.get(kind, lambda e, g: tr(e, "admin_info"))(edition, guild)
        embed = disnake.Embed(title=tr(edition, kind), description=content[:4096], colour=disnake.Colour.gold())
        view = ChallengePublicView(kind)
        message = None
        if edition["messages"].get(kind):
            try:
                message = await channel.fetch_message(edition["messages"][kind])
            except disnake.NotFound:
                pass
        if message:
            await message.edit(embed=embed, view=view, allowed_mentions=disnake.AllowedMentions.none())
        else:
            message = await channel.send(embed=embed, view=view, allowed_mentions=disnake.AllowedMentions.none())
            def remember_message(document):
                next(e for e in document["editions"] if e["id"] == edition_id)["messages"][kind] = message.id
            edit_challenges(guild_id, remember_message)
    if stage != "registration":
        registration = guild.get_channel(edition["channels"].get("registration", 0))
        if registration:
            await registration.delete(reason="SoloQ Challenge registration closed")


async def publish_history(guild, edition):
    from challenge_ui import match_text, tr
    channel = guild.get_channel(edition["channels"].get("history", 0))
    if not channel:
        return
    for user_id, player in edition["players"].items():
        for record in player["records"]:
            content = match_text(edition, guild, user_id, record)
            if content == record.get("published_content"):
                continue
            message_id = record.get("message_id")
            message = None
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except disnake.NotFound:
                    pass
            if message is None:
                # Reconcile the outbox after a crash before the sent ID was committed.
                marker = f"{user_id}:{record['id']}"
                async for candidate in channel.history(limit=None):
                    if candidate.author.id == guild.me.id and candidate.embeds and candidate.embeds[0].footer.text == marker:
                        message = candidate
                        break
            embed = disnake.Embed(title=tr(edition, "history"), description=content[:4096], colour=disnake.Colour.dark_teal())
            embed.set_footer(text=f"{user_id}:{record['id']}")
            if message:
                await message.edit(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
            else:
                message = await channel.send(embed=embed, allowed_mentions=disnake.AllowedMentions.none())
            def delivered(document):
                target = next(e for e in document["editions"] if e["id"] == edition["id"])
                saved = next(r for r in target["players"][user_id]["records"] if r["id"] == record["id"])
                saved.update(message_id=message.id, published_content=content)
            edit_challenges(guild.id, delivered)


async def challenge_tick(bot):
    global _budget
    from utils.commonUtils import requestLimit
    if _tick_lock.locked():
        return
    async with _tick_lock:
        _budget = max(1, int((requestLimit or 100) * 0.35))
        for guild in bot.guilds:
            document = read_challenges(guild.id)
            if not document["editions"]:
                continue
            edition = document["editions"][-1]
            if edition.get("cancelled"):
                continue
            try:
                if phase(edition, timestamp()) in ("week1", "week2", "settling"):
                    for user_id in sorted(edition["players"], key=lambda uid: edition["players"][uid].get("scan_at", 0)):
                        if _budget <= 0 or timestamp() < _backoff:
                            break
                        try:
                            await collect_player(guild.id, edition["id"], user_id)
                        except Exception:
                            log.exception("Challenge scan failed for %s/%s", guild.id, user_id)
                edition = read_challenges(guild.id)["editions"][-1]
                await ensure_channels(guild, edition)
                await publish_history(guild, read_challenges(guild.id)["editions"][-1])
            except Exception:
                log.exception("Challenge lifecycle/delivery failed for %s", guild.id)
        _budget = None
