import math
from datetime import datetime, timedelta
import disnake
import pytz
from disnake import ApplicationCommandInteraction
from disnake.ext import commands, tasks
import requests
from utils.commonUtils import requestLimit, jsonFile, dailyPostTimer, discordChannel, platforms, regions, riotApKey, discordToken
from utils.dataUtils import checkForNewPatchNotes, numberOfSummoners, update, crownData, mvpData
from utils.jsonUtils import openJsonFile, writeToJsonFile

bot = commands.InteractionBot()


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
        name = summoner.name
        if len(name) > 18:
            name = f"{name[:15]}..."

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


if __name__ == "__main__":

    @bot.event
    async def on_ready():
        print('Logged in as {0.user} at {1}'.format(bot, datetime.now().strftime('%I:%M:%S %p %d/%m/%Y')))
        print("")
        if not updateRaceImage.is_running():
            updateRaceImage.start()
            updatePatchNotes.start()


    @tasks.loop(minutes=120)
    async def updatePatchNotes():
        updateAvailable, updatedPatch, daysAgo, daysTillNext, fullUrl, imagePath = checkForNewPatchNotes("data.json", False)
        if daysAgo > 12:
            updatePatchNotes.change_interval(minutes=15)

        if updateAvailable:
            channel = bot.get_channel(discordChannel)
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
        interval = math.floor(60 * numberOfSummoners(5) / (requestLimit * 0.7))

        updateRaceImage.change_interval(seconds=interval)

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
            if updated or force_leaderboard:
                channel = bot.get_channel(discordChannel)
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners, True, dateStr)
                writeToJsonFile("data.json", latest_json_data)
        else:
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, False, returnData=True, generate=False)
            if updated or force_leaderboard:
                channel = bot.get_channel(discordChannel)
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
