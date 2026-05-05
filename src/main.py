import math
import random
from datetime import datetime, timedelta
import disnake
import pytz
from disnake import ApplicationCommandInteraction
from disnake.ext import commands, tasks
import requests
from utils.commonUtils import requestLimit, jsonFile, dailyPostTimer, discordChannel, platforms, regions, riotApKey, discordToken
from utils.dataUtils import checkForNewPatchNotes, numberOfSummoners, update, crownData, mvpData, riotBackoffRemaining, riotBackoffTimestamp
from utils.jsonUtils import openJsonFile, writeToJsonFile

bot = commands.InteractionBot()
matchmaking_view_registered = False
MAX_SELECT_OPTIONS = 25


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


async def send_ephemeral(inter, message=None, embed=None, view=None):
    if inter.response.is_done():
        await inter.followup.send(message, embed=embed, view=view, ephemeral=True)
    else:
        await inter.response.send_message(message, embed=embed, view=view, ephemeral=True)


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
    return json_data


def ensure_admin_state(json_data):
    ensure_matchmaking_state(json_data)
    json_data.setdefault("adminMessageId", None)
    return json_data


def effective_matchmaking_separate_channels(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is not None:
        return forced_mode
    return json_data.get("matchmakingSeparateChannels", False)


def forced_mode_text(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is True:
        return "Forced on"
    if forced_mode is False:
        return "Forced off"
    return "Unlocked"


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


def normalize_tagline(tagline):
    return tagline.replace("#", "").strip()


def find_summoner_key(json_data, name, tagline):
    summoners = json_data.get("summoners") or {}
    summoner_full_name = f"{name}#{normalize_tagline(tagline)}"
    for summoner in summoners:
        if summoner.lower() == summoner_full_name.lower():
            return summoner
    return None


def format_summoner_summary(json_data):
    summoners = list((json_data.get("summoners") or {}).keys())
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
        players.append(f"**{index}.** <@{player['userId']}> - {voice}")
    return "\n".join(players)


def matchmaking_embed(json_data):
    ensure_matchmaking_state(json_data)
    queue = json_data["matchmakingQueue"]
    separate_channels = effective_matchmaking_separate_channels(json_data)
    mode_text = forced_mode_text(json_data)
    ready_text = "Ready to start" if len(queue) >= 2 else "Waiting for at least 2 players"

    embed = disnake.Embed(
        title="Matchmaking",
        description=f"{ready_text}\nPlayers: **{len(queue)}/10**\nSeparate channels: **{'On' if separate_channels else 'Off'}**\nAdmin lock: **{mode_text}**",
        colour=disnake.Colour.blurple(),
        timestamp=datetime.now()
    )

    if queue:
        players = []
        for index, player in enumerate(queue, start=1):
            user = f"<@{player['userId']}>"
            voice = f"<#{player['voiceChannelId']}>" if player.get("voiceChannelId") else "No voice channel"
            players.append(f"**{index}.** {user} - {voice}")
        embed.add_field(name="Current players", value="\n".join(players), inline=False)
    else:
        embed.add_field(name="Current players", value="No players in queue.", inline=False)

    embed.add_field(
        name="Controls",
        value="Use the buttons below to join, leave, toggle separate voice channels, or start the match.",
        inline=False
    )
    embed.set_footer(text="Join requires being in a voice channel.")
    return embed


def admin_embed(json_data):
    ensure_admin_state(json_data)
    summoners = json_data.get("summoners") or {}
    queue = json_data.get("matchmakingQueue", [])
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
    embed.add_field(name="Matchmaking queue", value=f"{len(queue)}/10", inline=True)
    embed.add_field(name="Separate channels", value=f"{'On' if effective_matchmaking_separate_channels(json_data) else 'Off'} ({forced_mode_text(json_data)})", inline=True)
    embed.set_footer(text="Administration actions require Manage Server.")
    return embed


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
    writeToJsonFile(jsonFile, json_data)
    return True, f"{summoner_key} removed"


async def configure_leaderboard_channel(channel):
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
        return f"Leaderboard channel set to {channel.mention}. Current embed moved."
    if json_data.get("leaderboardMessageId"):
        return f"Leaderboard channel set to {channel.mention}."
    return f"Leaderboard channel set to {channel.mention}. The message will be created on the next leaderboard update."


async def configure_matchmaking_channel(channel):
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
    return f"Matchmaking channel set to {channel.mention}. Message ready: {message_id}"


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


async def start_matchmaking_queue(guild, json_data):
    json_data = ensure_matchmaking_state(json_data)
    queue = await active_matchmaking_queue(guild, json_data)

    if len(queue) < 2:
        writeToJsonFile(jsonFile, json_data)
        return False, "At least 2 players are required to start.", json_data
    if len(queue) > 10:
        writeToJsonFile(jsonFile, json_data)
        return False, "The queue cannot contain more than 10 players.", json_data

    json_data["matchmakingInProgress"] = True
    writeToJsonFile(jsonFile, json_data)

    players = queue[:]
    random.shuffle(players)
    team_one = players[::2]
    team_two = players[1::2]
    created_channels = []

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
    json_data["matchmakingTeamChannelIds"] = created_channels
    json_data["matchmakingInProgress"] = False
    writeToJsonFile(jsonFile, json_data)

    team_one_text = ", ".join(f"<@{player['userId']}>" for player in team_one)
    team_two_text = ", ".join(f"<@{player['userId']}>" for player in team_two)
    return True, f"Match started.\nTeam 1: {team_one_text}\nTeam 2: {team_two_text}", json_data


class MatchmakingView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Join", style=disnake.ButtonStyle.green, custom_id="matchmaking:join")
    async def join(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        member = inter.author
        voice_channel = member.voice.channel if getattr(member, "voice", None) and member.voice else None
        if voice_channel is None:
            await inter.followup.send("You need to be in a voice channel to join the queue.", ephemeral=True)
            return

        json_data = ensure_matchmaking_state(load_json_data())
        queue = json_data["matchmakingQueue"]
        index = user_queue_index(queue, member.id)
        if index is not None:
            queue[index]["displayName"] = member.display_name
            queue[index]["voiceChannelId"] = voice_channel.id
            response = "Your voice channel was updated."
        else:
            if len(queue) >= 10:
                await inter.followup.send("The matchmaking queue is full.", ephemeral=True)
                return
            queue.append({
                "userId": member.id,
                "displayName": member.display_name,
                "voiceChannelId": voice_channel.id
            })
            response = "You joined the matchmaking queue."

        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.followup.send(response, ephemeral=True)

    @disnake.ui.button(label="Leave", style=disnake.ButtonStyle.red, custom_id="matchmaking:leave")
    async def leave(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        if not remove_user_from_matchmaking_queue(json_data, inter.author.id):
            await inter.followup.send("You are not in the matchmaking queue.", ephemeral=True)
            return

        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.followup.send("You left the matchmaking queue.", ephemeral=True)

    @disnake.ui.button(label="Separate channels", style=disnake.ButtonStyle.blurple, custom_id="matchmaking:separate_channels")
    async def separate_channels(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        if json_data.get("matchmakingSeparateChannelsForced") is not None:
            await inter.followup.send(f"Separate channels mode is locked by administration: {forced_mode_text(json_data)}.", ephemeral=True)
            return

        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await inter.followup.send("Only queued players can change matchmaking mode.", ephemeral=True)
            return

        json_data["matchmakingSeparateChannels"] = not json_data["matchmakingSeparateChannels"]
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        mode = "enabled" if json_data["matchmakingSeparateChannels"] else "disabled"
        await inter.followup.send(f"Separate channels {mode}.", ephemeral=True)

    @disnake.ui.button(label="Start match", style=disnake.ButtonStyle.gray, custom_id="matchmaking:start")
    async def start_match(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        queue = await active_matchmaking_queue(inter.guild, json_data)
        if user_queue_index(queue, inter.author.id) is None:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_matchmaking_message(json_data)
            await refresh_configured_admin_message(json_data)
            await inter.followup.send("Only queued players can start matchmaking.", ephemeral=True)
            return

        success, message, json_data = await start_matchmaking_queue(inter.guild, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.followup.send(message, ephemeral=True)


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
        await refresh_configured_admin_message()
        await inter.followup.send(message, ephemeral=True)


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
        await refresh_configured_admin_message()
        json_data = load_json_data()
        await inter.response.edit_message(embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data))
        await inter.followup.send(message, ephemeral=True)


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
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.response.edit_message(embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))
        response = f"Removed <@{user_id}> from the queue." if removed else "That user is no longer in the queue."
        await inter.followup.send(response, ephemeral=True)


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
        message = await configure_leaderboard_channel(channel)
        await inter.followup.send(message, ephemeral=True)

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
        message = await configure_matchmaking_channel(channel)
        await inter.followup.send(message, ephemeral=True)


class LeaderboardUsersAdminView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=300)
        summoners = list((json_data.get("summoners") or {}).keys())
        if summoners:
            self.add_item(LeaderboardRemoveSelect(summoners))

    @disnake.ui.button(label="Add summoner", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard:add")
    async def add_summoner(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(AddSummonerModal())


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
        success, message, json_data = await start_matchmaking_queue(inter.guild, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.followup.send(message, ephemeral=True)

    @disnake.ui.button(label="Unlocked", style=disnake.ButtonStyle.gray, custom_id="admin:matchmaking:unlock")
    async def unlock_mode(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await self.set_forced_mode(inter, None)

    @disnake.ui.button(label="Forced on", style=disnake.ButtonStyle.blurple, custom_id="admin:matchmaking:force_on")
    async def force_on(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await self.set_forced_mode(inter, True)

    @disnake.ui.button(label="Forced off", style=disnake.ButtonStyle.red, custom_id="admin:matchmaking:force_off")
    async def force_off(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await self.set_forced_mode(inter, False)

    async def set_forced_mode(self, inter, value):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_matchmaking_state(load_json_data())
        json_data["matchmakingSeparateChannelsForced"] = value
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.response.edit_message(embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))


class AdminView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="App settings", style=disnake.ButtonStyle.blurple, custom_id="admin:settings")
    async def settings(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await inter.response.send_message(embed=settings_admin_embed(json_data), view=SettingsAdminView(), ephemeral=True)

    @disnake.ui.button(label="Leaderboard users", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard")
    async def leaderboard_users(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = load_json_data()
        await inter.response.send_message(embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data), ephemeral=True)

    @disnake.ui.button(label="Matchmaking", style=disnake.ButtonStyle.gray, custom_id="admin:matchmaking")
    async def matchmaking(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_matchmaking_state(load_json_data())
        await inter.response.send_message(embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data), ephemeral=True)

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.gray, custom_id="admin:refresh")
    async def refresh(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await inter.response.send_message("Administration refreshed.", ephemeral=True)


def settings_admin_embed(json_data):
    embed = disnake.Embed(
        title="App settings",
        description="Select the channels used by persistent bot messages.",
        colour=disnake.Colour.dark_teal()
    )
    embed.add_field(name="Leaderboard", value=f"<#{leaderboard_channel_id(json_data)}>", inline=True)
    embed.add_field(name="Matchmaking", value=f"<#{matchmaking_channel_id(json_data)}>", inline=True)
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


def matchmaking_admin_embed(json_data):
    ensure_matchmaking_state(json_data)
    embed = disnake.Embed(
        title="Matchmaking administration",
        description=f"Queue: **{len(json_data['matchmakingQueue'])}/10**\nSeparate channels: **{'On' if effective_matchmaking_separate_channels(json_data) else 'Off'}**\nAdmin lock: **{forced_mode_text(json_data)}**",
        colour=disnake.Colour.blurple()
    )
    embed.add_field(name="Queued players", value=format_matchmaking_queue(json_data), inline=False)
    return embed


async def refresh_matchmaking_message(channel, json_data=None):
    json_data = ensure_matchmaking_state(json_data or load_json_data())
    embed = matchmaking_embed(json_data)
    view = MatchmakingView()
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
                queue_changed = True
            else:
                queue[index]["voiceChannelId"] = after.channel.id
                queue_changed = True

        if queue_changed:
            writeToJsonFile(jsonFile, json_data)
            channel = await get_discord_channel(matchmaking_channel_id(json_data))
            if channel:
                await refresh_matchmaking_message(channel, json_data)
            await refresh_configured_admin_message(json_data)

        await delete_empty_matchmaking_team_channels(member.guild)


    @tasks.loop(minutes=120)
    async def updatePatchNotes():
        updateAvailable, updatedPatch, daysAgo, daysTillNext, fullUrl, imagePath = checkForNewPatchNotes("data.json", False)
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


    @tasks.loop(seconds=60)
    async def updateRaceImage():
        safeRequestLimit = requestLimit if requestLimit and requestLimit > 0 else 100
        calculatedInterval = math.floor(60 * numberOfSummoners(5) / (safeRequestLimit * 0.7))
        interval = max(calculatedInterval, 120)

        updateRaceImage.change_interval(seconds=interval)

        if riotBackoffRemaining() > 0:
            retryTime = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
            print(f"Skipping Riot update until {retryTime} due to rate limit")
            return

        json_data = openJsonFile(jsonFile)
        lastRunTime = json_data['runtime']
        # Set the timezone to Europe/London
        timezone = pytz.timezone('Europe/Madrid')
        currentTime = datetime.now(tz=timezone)
        dateStr = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%y")

        dailyTime = currentTime.replace(hour=dailyPostTimer, minute=0, second=0, microsecond=0).timestamp()

        # If it's past 9pm and last run time is before 9pm today, update the image
        if currentTime.timestamp() > dailyTime > lastRunTime:
            json_data['runtime'] = dailyTime
            writeToJsonFile("data.json", json_data)
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, True, returnData=True, generate=False)
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners, True, dateStr)
                writeToJsonFile("data.json", latest_json_data)
        else:
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, False, returnData=True, generate=False)
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners)
                writeToJsonFile("data.json", latest_json_data)


    @bot.slash_command(description="Full list of summoners")
    async def list(inter: ApplicationCommandInteraction):
        await inter.response.defer()
        jsonData = openJsonFile(jsonFile)
        summonerList = []
        for summoner in jsonData['summoners']:
            summonerList.append(summoner)
        await inter.send("\n".join(summonerList))


    @bot.slash_command(description="lp needed for challenger and grandmaster")
    async def chall(inter: ApplicationCommandInteraction, platform: str = commands.Param(choices=platforms)):
        await inter.response.defer()

        mastersUrl = f"https://{platform}.api.riotgames.com/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5?api_key=RGAPI-f42c18f5-4234-48aa-b354-c977e092238d"
        grandMastersUrl = f"https://{platform}.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5?api_key=RGAPI-f42c18f5-4234-48aa-b354-c977e092238d"
        challengerUrl = f"https://{platform}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?api_key=RGAPI-f42c18f5-4234-48aa-b354-c977e092238d"
        combinedHighEloPlayers = []
        for url in [mastersUrl, grandMastersUrl, challengerUrl]:
            response = requests.get(url)
            if response.status_code == 200:
                players = response.json().get("entries", [])
                combinedHighEloPlayers.extend(players)
            else:
                print(f"Failed to fetch data from {url}. Status code:", response.status_code)

        sortedHighEloPlayers = sorted(combinedHighEloPlayers, key=lambda x: (-x["leaguePoints"], x["summonerName"]))

        challenger_lp_needed = sortedHighEloPlayers[299]["leaguePoints"] + 1 if len(sortedHighEloPlayers) > 299 else None
        grandmaster_lp_needed = sortedHighEloPlayers[999]["leaguePoints"] + 1 if len(sortedHighEloPlayers) > 999 else None

        await inter.send(f"{platform}\nLP needed for Challenger: {challenger_lp_needed}\nLP needed for Grandmaster: {grandmaster_lp_needed}")


    @bot.slash_command(description="Patch notes")
    async def patch(inter: ApplicationCommandInteraction):
        await inter.response.defer()
        update_available, updated_patch, days_ago, days_till_next, full_url, image_path = checkForNewPatchNotes("data.json", True)
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


    @bot.slash_command(description="Create or refresh the matchmaking message")
    async def matchmaking(inter: ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        message_id = await setup_matchmaking_message()
        if message_id:
            await inter.send(f"Matchmaking message ready: {message_id}", ephemeral=True)
        else:
            await inter.send("Could not create the matchmaking message. Check the configured channel and bot permissions.", ephemeral=True)


    @bot.slash_command(description="Create or move the administration message")
    async def setup(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await inter.send("This command can only be used inside a server.", ephemeral=True)
            return
        if not can_configure_channels(inter):
            await inter.send("You need Manage Server permission to set up administration.", ephemeral=True)
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await inter.send(f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.", ephemeral=True)
            return

        message_id = await setup_admin_message(channel)
        await inter.send(f"Administration channel set to {channel.mention}. Message ready: {message_id}", ephemeral=True)


    @bot.slash_command(description="Set the channel for the editable leaderboard message")
    async def setrankingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await inter.send("This command can only be used inside a server.", ephemeral=True)
            return
        if not can_configure_channels(inter):
            await inter.send("You need Manage Server permission to change bot channels.", ephemeral=True)
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await inter.send(f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.", ephemeral=True)
            return

        message = await configure_leaderboard_channel(channel)
        await inter.send(message, ephemeral=True)


    @bot.slash_command(description="Set the channel for the matchmaking message")
    async def setmatchmakingchannel(inter: ApplicationCommandInteraction, channel: disnake.TextChannel):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await inter.send("This command can only be used inside a server.", ephemeral=True)
            return
        if not can_configure_channels(inter):
            await inter.send("You need Manage Server permission to change bot channels.", ephemeral=True)
            return

        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await inter.send(f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.", ephemeral=True)
            return

        message = await configure_matchmaking_channel(channel)
        await inter.send(message, ephemeral=True)


    @bot.slash_command(description="breakdown of mvp score for a given game")
    async def mvp(inter: ApplicationCommandInteraction, name: str, tagline: str, region: str = commands.Param(choices=regions), game: int = commands.Param(choices=[1, 2, 3, 4, 5])):
        await inter.response.defer()
        response = requests.get(
            f'https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tagline}?api_key={riotApKey}'
        )
        if response.status_code == 200:
            apiData1 = response.json()
            summonerPuuid = apiData1['puuid']
            summonerName = apiData1['gameName']
            summonerTagline = apiData1['tagLine']
            riotApiData = requests.get(f'https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{summonerPuuid}/ids?queue=420&start=0&count=5&api_key={riotApKey}').json()

            matchId = riotApiData[game - 1]
            mvpData(matchId)

            with open("mvp data.txt", 'rb') as f:
                dataFile = disnake.File(f)
            await inter.send(f'Mvp scores for: {summonerName}#{summonerTagline}, game: {game}', file=dataFile)
        else:
            summonerFullName = f"{name}#{tagline}"
            await inter.send(f'Invalid summoner: {summonerFullName}')


    @bot.slash_command(description="Mvp scores for all summoners")
    async def crown(inter: ApplicationCommandInteraction):
        await inter.response.defer()
        crownData()
        with open("crown data.txt", 'rb') as f:
            dataFile = disnake.File(f)
        await inter.send(f'Mvp scores', file=dataFile)


    @bot.slash_command(description="Add summoner to the list")
    async def add(inter: ApplicationCommandInteraction, name: str, tagline: str, platform: str = commands.Param(choices=platforms), region: str = commands.Param(choices=regions)):
        await inter.response.defer()
        success, message = await add_summoner_to_data(name, tagline, platform, region)
        await refresh_configured_admin_message()
        await inter.send(message)


    @bot.slash_command(description="Remove summoner from the list")
    async def remove(inter: ApplicationCommandInteraction, name: str, tagline: str):
        await inter.response.defer()
        success, message = remove_summoner_from_data(name, tagline)
        await refresh_configured_admin_message()
        await inter.send(message)

    print(discordToken)
    bot.run(discordToken)
