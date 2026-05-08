import disnake
from disnake import ApplicationCommandInteraction
from disnake.ext import commands

from discord_helpers import get_guild_member, require_admin_interaction, send_ephemeral_inter_send
from i18n import t
from leaderboard import add_summoner_to_data, remove_summoner_from_data
from linked_accounts import find_summoner_key, link_summoner_to_discord, normalize_tagline, primary_summoner_for_user, request_discord_link, set_primary_summoner_for_user, unlink_summoner_from_discord
from personal_report import personal_report_embed, personal_report_view
from persistent_messages import configure_leaderboard_channel, configure_matchmaking_channel, refresh_configured_admin_message, setup_admin_message, setup_matchmaking_message
from state import can_configure_channels, ensure_admin_state, leaderboard_chat_commands_enabled, load_json_data, missing_bot_channel_permissions
from utils.auditUtils import interaction_actor, log_event
from utils.commonUtils import jsonFile, platforms, regions
from utils.dataUtils import checkForNewPatchNotes
from utils.jsonUtils import openJsonFile, writeToJsonFile


def register_commands(bot):
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
            message = t(json_data, "personal_report.not_linked_me", target=target.mention)
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
            await inter.send(t(ensure_admin_state(load_json_data()), "commands.patch_failed"))

    @bot.slash_command(name="admin_matchmaking", description="Create or refresh the matchmaking message")
    async def matchmaking(inter: ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        message_id = await setup_matchmaking_message()
        if message_id:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "commands.matchmaking_ready", message_id=message_id))
        else:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "commands.matchmaking_failed"))

    @bot.slash_command(name="admin_setup", description="Create or move the administration message")
    async def setup(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.command_server_only"))
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.manage_server_setup_required"))
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.no_permission_channel", channel=channel.mention, permissions=", ".join(missing_permissions)))
            return

        message_id = await setup_admin_message(channel)
        log_event("admin_setup", actor=interaction_actor(inter), status="success", summary=f"Administration channel set to {channel.mention}.", details={"channelId": str(channel.id), "messageId": str(message_id)})
        await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "commands.admin_setup_done", channel=channel.mention, message_id=message_id))

    @bot.slash_command(name="admin_set_ranking_channel", description="Set the channel for the editable leaderboard message")
    async def setrankingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.command_server_only"))
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.manage_server_channels_required"))
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.no_permission_channel", channel=channel.mention, permissions=", ".join(missing_permissions)))
            return

        message = await configure_leaderboard_channel(channel, interaction_actor(inter))
        await send_ephemeral_inter_send(inter, message)

    @bot.slash_command(name="admin_set_matchmaking_channel", description="Set the channel for the matchmaking message")
    async def setmatchmakingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.command_server_only"))
            return
        if not can_configure_channels(inter):
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.manage_server_channels_required"))
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral_inter_send(inter, t(ensure_admin_state(load_json_data()), "common.no_permission_channel", channel=channel.mention, permissions=", ".join(missing_permissions)))
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
            message = t(json_data, "commands.add_disabled")
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
            message = t(json_data, "commands.remove_disabled")
            log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="error", summary=message, details={"name": name, "tagline": tagline})
            await send_ephemeral_inter_send(inter, message)
            return

        success, message = remove_summoner_from_data(name, tagline)
        log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"name": name, "tagline": tagline})
        await refresh_configured_admin_message()
        await send_ephemeral_inter_send(inter, message)
