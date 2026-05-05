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


def ensure_matchmaking_state(json_data):
    json_data.setdefault("matchmakingQueue", [])
    json_data.setdefault("matchmakingSeparateChannels", False)
    json_data.setdefault("matchmakingTeamChannelIds", [])
    json_data.setdefault("matchmakingInProgress", False)
    return json_data


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


def matchmaking_embed(json_data):
    ensure_matchmaking_state(json_data)
    queue = json_data["matchmakingQueue"]
    separate_channels = json_data["matchmakingSeparateChannels"]
    ready_text = "Ready to start" if len(queue) >= 2 else "Waiting for at least 2 players"

    embed = disnake.Embed(
        title="Matchmaking",
        description=f"{ready_text}\nPlayers: **{len(queue)}/10**\nSeparate channels: **{'On' if separate_channels else 'Off'}**",
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
        await refresh_matchmaking_message(inter.channel, json_data)
        await inter.followup.send(response, ephemeral=True)

    @disnake.ui.button(label="Leave", style=disnake.ButtonStyle.red, custom_id="matchmaking:leave")
    async def leave(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        if not remove_user_from_matchmaking_queue(json_data, inter.author.id):
            await inter.followup.send("You are not in the matchmaking queue.", ephemeral=True)
            return

        writeToJsonFile(jsonFile, json_data)
        await refresh_matchmaking_message(inter.channel, json_data)
        await inter.followup.send("You left the matchmaking queue.", ephemeral=True)

    @disnake.ui.button(label="Separate channels", style=disnake.ButtonStyle.blurple, custom_id="matchmaking:separate_channels")
    async def separate_channels(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        if user_queue_index(json_data["matchmakingQueue"], inter.author.id) is None:
            await inter.followup.send("Only queued players can change matchmaking mode.", ephemeral=True)
            return

        json_data["matchmakingSeparateChannels"] = not json_data["matchmakingSeparateChannels"]
        writeToJsonFile(jsonFile, json_data)
        await refresh_matchmaking_message(inter.channel, json_data)
        mode = "enabled" if json_data["matchmakingSeparateChannels"] else "disabled"
        await inter.followup.send(f"Separate channels {mode}.", ephemeral=True)

    @disnake.ui.button(label="Start match", style=disnake.ButtonStyle.gray, custom_id="matchmaking:start")
    async def start_match(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        json_data = ensure_matchmaking_state(load_json_data())
        queue = json_data["matchmakingQueue"]
        active_queue = []
        for player in queue:
            member = await get_guild_member(inter.guild, player["userId"])
            if member and member.voice and member.voice.channel:
                player["voiceChannelId"] = member.voice.channel.id
                active_queue.append(player)

        if len(active_queue) != len(queue):
            json_data["matchmakingQueue"] = active_queue
            queue = active_queue
            writeToJsonFile(jsonFile, json_data)
            await refresh_matchmaking_message(inter.channel, json_data)

        if user_queue_index(queue, inter.author.id) is None:
            await inter.followup.send("Only queued players can start matchmaking.", ephemeral=True)
            return
        if len(queue) < 2:
            await inter.followup.send("At least 2 players are required to start.", ephemeral=True)
            return
        if len(queue) > 10:
            await inter.followup.send("The queue cannot contain more than 10 players.", ephemeral=True)
            return

        json_data["matchmakingInProgress"] = True
        writeToJsonFile(jsonFile, json_data)

        players = queue[:]
        random.shuffle(players)
        team_one = players[::2]
        team_two = players[1::2]
        created_channels = []

        if json_data["matchmakingSeparateChannels"]:
            try:
                category = None
                first_voice_id = players[0].get("voiceChannelId")
                first_voice = inter.guild.get_channel(int(first_voice_id)) if first_voice_id else None
                if first_voice:
                    category = first_voice.category

                overwrites = {
                    inter.guild.default_role: disnake.PermissionOverwrite(view_channel=True, connect=True)
                }
                team_one_channel = await inter.guild.create_voice_channel("Team 1", category=category, overwrites=overwrites)
                team_two_channel = await inter.guild.create_voice_channel("Team 2", category=category, overwrites=overwrites)
                created_channels = [team_one_channel.id, team_two_channel.id]

                for player in team_one:
                    member = await get_guild_member(inter.guild, player["userId"])
                    if member and member.voice:
                        await member.move_to(team_one_channel)
                for player in team_two:
                    member = await get_guild_member(inter.guild, player["userId"])
                    if member and member.voice:
                        await member.move_to(team_two_channel)
            except disnake.Forbidden:
                json_data["matchmakingInProgress"] = False
                writeToJsonFile(jsonFile, json_data)
                for channel_id in created_channels:
                    channel = inter.guild.get_channel(int(channel_id))
                    if channel:
                        try:
                            await channel.delete()
                        except (disnake.Forbidden, disnake.HTTPException):
                            pass
                await inter.followup.send("I do not have permission to create voice channels or move members.", ephemeral=True)
                return
            except disnake.HTTPException:
                json_data["matchmakingInProgress"] = False
                writeToJsonFile(jsonFile, json_data)
                for channel_id in created_channels:
                    channel = inter.guild.get_channel(int(channel_id))
                    if channel:
                        try:
                            await channel.delete()
                        except (disnake.Forbidden, disnake.HTTPException):
                            pass
                await inter.followup.send("Discord rejected the channel creation or member move request.", ephemeral=True)
                return

        json_data["matchmakingQueue"] = []
        json_data["matchmakingTeamChannelIds"] = created_channels
        json_data["matchmakingInProgress"] = False
        writeToJsonFile(jsonFile, json_data)
        await refresh_matchmaking_message(inter.channel, json_data)

        team_one_text = ", ".join(f"<@{player['userId']}>" for player in team_one)
        team_two_text = ", ".join(f"<@{player['userId']}>" for player in team_two)
        await inter.followup.send(f"Match started.\nTeam 1: {team_one_text}\nTeam 2: {team_two_text}", ephemeral=True)


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


async def setup_matchmaking_message():
    channel = await get_discord_channel(discordChannel)
    if not channel:
        print("Matchmaking message was not created because the configured channel was not found.")
        return None
    return await refresh_matchmaking_message(channel)


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
            matchmaking_view_registered = True
        await setup_matchmaking_message()
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
            channel = await get_discord_channel(discordChannel)
            if channel:
                await refresh_matchmaking_message(channel, json_data)

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
                channel = await get_discord_channel(discordChannel)
                if not channel:
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners, True, dateStr)
                writeToJsonFile("data.json", latest_json_data)
        else:
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, False, returnData=True, generate=False)
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(discordChannel)
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
        jsonData = openJsonFile(jsonFile)
        summoners = jsonData.get("summoners")

        if "#" in tagline:
            tagline = tagline.replace("#", "")

        if summoners is None:
            jsonData['summoners'] = {}

        summonerFullName = f"{name}#{tagline}"
        summonerList = [summoner.lower() for summoner in jsonData['summoners']]

        if summonerFullName.lower() in summonerList:
            await inter.send(f'{summonerFullName} is already added')

        else:
            response = requests.get(
                f'https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tagline}?api_key={riotApKey}'
            )
            if response.status_code == 200:
                apiData1 = response.json()
                summonerFullName = apiData1['gameName'] + '#' + apiData1['tagLine']
                summonerPuuid = apiData1['puuid']

                response = requests.get(
                    f'https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{summonerPuuid}?api_key={riotApKey}'
                )
                apiData2 = response.json()

                data = jsonData

                data["summoners"][summonerFullName] = {
                    "id": apiData2['puuid'],
                    "puuid": summonerPuuid,
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

                writeToJsonFile("data.json", data)
                await inter.send(f'{summonerFullName} added')
            else:
                await inter.send(f'Invalid summoner: {summonerFullName}')


    @bot.slash_command(description="Remove summoner from the list")
    async def remove(inter: ApplicationCommandInteraction, name: str, tagline: str):
        await inter.response.defer()
        jsonData = openJsonFile(jsonFile)
        if "#" in tagline:
            tagline = tagline.replace("#", "")

        summonerFullName = f"{name}#{tagline}"
        summonerList = [summoner.lower() for summoner in jsonData['summoners']]

        if summonerFullName.lower() not in summonerList:
            await inter.send(f"{summonerFullName} has not been added")
        else:
            # Find the matching summoner in the original case
            originalCaseSummoner = next(
                summoner for summoner in jsonData['summoners']
                if summoner.lower() == summonerFullName.lower())
            del jsonData['summoners'][originalCaseSummoner]
            writeToJsonFile("data.json", jsonData)
            await inter.send(f"{originalCaseSummoner} removed")

    print(discordToken)
    bot.run(discordToken)
