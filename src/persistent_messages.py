import disnake

from discord_helpers import delete_configured_message, fetch_configured_message, get_discord_channel
from i18n import t
from leaderboard import cached_leaderboard_summoners, send_or_edit_leaderboard
from state import admin_channel_id, ensure_admin_state, ensure_matchmaking_state, leaderboard_channel_id, load_json_data, matchmaking_channel_id
from utils.auditUtils import log_event
from utils.commonUtils import jsonFile
from utils.jsonUtils import writeToJsonFile


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
        return t(json_data, "persistent.unknown")

    channel = await get_discord_channel(channel_id)
    if not channel:
        return t(json_data, "persistent.channel_missing", channel_id=channel_id)
    if not message_id:
        return t(json_data, "persistent.no_message", channel=channel.mention)
    message = await fetch_configured_message(channel_id, message_id)
    if not message:
        return t(json_data, "persistent.message_missing", channel=channel.mention)
    return t(json_data, "persistent.ok_message", channel=channel.mention, message_id=message.id)

async def recreate_persistent_messages(json_data):
    ensure_admin_state(json_data)
    results = []

    admin_channel = await get_discord_channel(admin_channel_id(json_data)) if json_data.get("adminChannelId") else None
    if admin_channel:
        json_data["adminMessageId"] = await refresh_admin_message(admin_channel, json_data)
        results.append(t(json_data, "persistent.admin_result", message_id=json_data["adminMessageId"]))
    else:
        results.append(t(json_data, "persistent.admin_missing"))

    matchmaking_channel = await get_discord_channel(matchmaking_channel_id(json_data))
    if matchmaking_channel:
        json_data["matchmakingMessageId"] = await refresh_matchmaking_message(matchmaking_channel, json_data)
        results.append(t(json_data, "persistent.matchmaking_result", message_id=json_data["matchmakingMessageId"]))
    else:
        results.append(t(json_data, "persistent.matchmaking_missing"))

    leaderboard_channel = await get_discord_channel(leaderboard_channel_id(json_data))
    if leaderboard_channel:
        cached_summoners = cached_leaderboard_summoners(json_data)
        current_leaderboard = await fetch_configured_message(leaderboard_channel_id(json_data), json_data.get("leaderboardMessageId"))
        if cached_summoners:
            json_data["leaderboardMessageId"] = await send_or_edit_leaderboard(leaderboard_channel, json_data, cached_summoners)
            results.append(t(json_data, "persistent.leaderboard_result", message_id=json_data["leaderboardMessageId"]))
        elif current_leaderboard:
            results.append(t(json_data, "persistent.leaderboard_ok", message_id=current_leaderboard.id))
        else:
            results.append(t(json_data, "persistent.leaderboard_skipped"))
    else:
        results.append(t(json_data, "persistent.leaderboard_missing"))

    writeToJsonFile(jsonFile, json_data)
    return "\n".join(results)

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
        message = t(json_data, "persistent.leaderboard_channel_moved", channel=channel.mention)
    elif json_data.get("leaderboardMessageId"):
        message = t(json_data, "persistent.leaderboard_channel_set", channel=channel.mention)
    else:
        message = t(json_data, "persistent.leaderboard_channel_next", channel=channel.mention)

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
    message = t(json_data, "persistent.matchmaking_channel_set", channel=channel.mention, message_id=message_id)
    log_event("matchmaking_channel_changed", actor=actor, status="success", summary=message, details={"channelId": str(channel.id), "messageId": str(message_id)})
    return message

async def refresh_matchmaking_message(channel, json_data=None):
    from matchmaking import MatchmakingView, matchmaking_embed

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
    from admin_panel import AdminView, admin_embed

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
