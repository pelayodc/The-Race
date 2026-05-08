import asyncio

import disnake

from bot_runtime import EPHEMERAL_DELETE_SECONDS, bot
from i18n import t
from state import load_json_data
from state import can_configure_channels


async def get_discord_channel(channel_id):
    channel = bot.get_channel(channel_id)
    if channel:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (disnake.Forbidden, disnake.NotFound, disnake.HTTPException) as error:
        print(f"Could not fetch Discord channel {channel_id}: {error}")
        return None

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
    return bool(message and (
        message.startswith("Match started")
        or message.startswith("Captain draft started")
        or message.startswith("Partida iniciada")
        or message.startswith("Draft de capitanes iniciado")
    ))

async def require_admin_interaction(inter):
    json_data = load_json_data()
    if not inter.guild:
        await send_ephemeral(inter, t(json_data, "common.server_only"))
        return False
    if not can_configure_channels(inter):
        await send_ephemeral(inter, t(json_data, "common.manage_server_required"))
        return False
    return True

async def get_guild_member(guild, user_id):
    member = guild.get_member(int(user_id))
    if member:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
        return None
