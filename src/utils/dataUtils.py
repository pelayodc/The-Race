import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote
import os
import numpy as np
import time
import requests

from .commonUtils import jsonFile, statisticsForMvp, Summoner, riotApKey, Rank, assetPath
from .drawUtils import generateImage
from .jsonUtils import openJsonFile, writeToJsonFile
from .auditUtils import log_event, system_actor

riotBackoffUntil = 0
HIGH_ELO_CACHE_SECONDS = 600


def riotBackoffRemaining():
    return max(0, riotBackoffUntil - time.time())


def riotBackoffTimestamp():
    return riotBackoffUntil


def record_riot_error(context, summary, details=None):
    details = details or {}
    jsonData = openJsonFile(jsonFile) or {}
    jsonData["lastRiotError"] = {
        "timestamp": datetime.now().isoformat(),
        "context": context,
        "summary": summary,
        "details": details
    }
    writeToJsonFile(jsonFile, jsonData)
    log_event(
        "riot_api_error",
        actor=system_actor(),
        status="error",
        summary=summary,
        details={"context": context, **details}
    )


def riot_get(url, context):
    global riotBackoffUntil

    if riotBackoffRemaining() > 0:
        return False, None, riotBackoffRemaining()

    response = requests.get(url)
    if response.status_code == 429:
        retryAfter = int(response.headers.get("Retry-After", 60))
        riotBackoffUntil = time.time() + retryAfter
        summary = f"Riot rate limited {context}. Retrying after {retryAfter} seconds."
        print(summary)
        record_riot_error(context, summary, {"statusCode": 429, "retryAfter": retryAfter})
        return False, None, retryAfter

    if response.status_code != 200:
        summary = f"Failed Riot request for {context}: status code {response.status_code}"
        print(f"{summary}, response: {response.text[:200]}")
        record_riot_error(context, summary, {"statusCode": response.status_code, "responseSnippet": response.text[:200]})
        return False, None, None

    try:
        return True, response.json(), None
    except ValueError as error:
        summary = f"Failed to decode Riot response for {context}"
        print(f"{summary}: {error}")
        record_riot_error(context, summary, {"error": str(error)})
        return False, None, None


def calculateZScore(value, multiplier, mean, std):
    if mean is None or std is None or std == 0:
        return 0
    return ((value - mean) / std) * multiplier


def calculateMeanAndStd(data, matchId, stat):
    values = []
    for participant in data["matchData"][matchId]["info"]['participants']:
        if 'challenges' in participant and stat in participant['challenges']:
            values.append(participant['challenges'][stat])
        elif stat in participant:
            values.append(participant[stat])
    if not values:
        return None, None

    return np.mean(values), np.std(values)


def fetchMatchData(i, summoner, data, matchId):
    mvpPuuid = None
    maxZScore = float('-inf')

    summoner.__setattr__(f'game{i + 1}GameLength', data["matchData"][matchId]["info"]["gameDuration"])

    # Calculate mean and std for each statistic
    meanStdDict = {stat: calculateMeanAndStd(data, matchId, stat) for stat in statisticsForMvp}

    for participant in data["matchData"][matchId]["info"]['participants']:
        playerPuuid = participant['puuid']
        zScores = {}
        for stat, (mean, std) in meanStdDict.items():
            if stat in participant['challenges']:
                originalValue = participant['challenges'][stat]
            elif stat in participant:
                originalValue = participant[stat]
            else:
                originalValue = 0  # or any default value you prefer if the statistic is missing

            # Calculate the Z-score using the multiplier from statisticsForMvp
            zScores[stat] = calculateZScore(originalValue, statisticsForMvp[stat], mean, std)

        totalZScore = sum(zScores.values())
        if participant['puuid'] == summoner.puuid:
            summoner.__setattr__(f'game{i + 1}MvpScore', totalZScore)

        # MVP
        if totalZScore > maxZScore:
            maxZScore = totalZScore
            mvpPuuid = playerPuuid

        if participant['puuid'] == summoner.puuid:
            summoner.__setattr__(f'game{i + 1}Champion', participant['championName'])
            summoner.__setattr__(f'game{i + 1}Kills', participant['kills'])
            summoner.__setattr__(f'game{i + 1}Deaths', participant['deaths'])
            summoner.__setattr__(f'game{i + 1}Assists', participant['assists'])
            summoner.__setattr__(f'game{i + 1}DamageDealtToChampions', participant['totalDamageDealtToChampions'])
            summoner.__setattr__(f'game{i + 1}Win', participant['win'])
            summoner.__setattr__(f'game{i + 1}Remake', participant['gameEndedInEarlySurrender'])

        # FIX NAME CHANGE
        if i == 0 and participant['puuid'] == summoner.puuid:
            gameName = participant['riotIdGameName'] + '#' + participant['riotIdTagline']
            savedName = summoner.fullName

            if gameName != savedName and gameName != "#":
                summoner.fullName = gameName
                summoner.tagline = participant['riotIdTagline']
                summoner.name = participant['riotIdGameName']
                print(f"{savedName} has changed their name to {gameName}")
                data["summoners"][gameName] = data["summoners"].pop(savedName)
                writeToJsonFile(jsonFile, data)

    # Print MVP
    # print(f"MVP: {mvpPuuid}")

    if mvpPuuid == summoner.puuid:
        summoner.__setattr__(f'game{i + 1}Mvp', True)
    else:
        summoner.__setattr__(f'game{i + 1}Mvp', False)


def fetchAllSummonerData(force, daily):
    summoners = []
    summonersList = []
    jsonData = openJsonFile(jsonFile)
    jsonData.setdefault("highEloCache", {})
    failedSummoners = []

    # assign ids, ranks, score
    for summonerName in jsonData["summoners"]:

        summoner = Summoner()

        summoner.fullName = summonerName
        summoner.name = summonerName.split("#")[0]
        summoner.tagline = summonerName.split("#")[1]
        summoner.id = jsonData["summoners"][summonerName]["id"]
        summoner.puuid = jsonData["summoners"][summonerName]["puuid"]
        summoner.platform = jsonData["summoners"][summonerName]["platform"]
        summoner.region = jsonData["summoners"][summonerName]["region"]
        # print(f'Fetching {summoner.fullName} rank data')
        try:
            ok, riotApiData, retryAfter = riot_get(
                f'https://{summoner.platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{summoner.puuid}?api_key={riotApKey}',
                f"rank data for {summoner.fullName}"
            )
            if not ok:
                failedSummoners.append(summoner.fullName)
                continue

            if not isinstance(riotApiData, list):
                failedSummoners.append(summoner.fullName)
                summary = f"Failed to fetch rank data for {summoner.fullName}: unexpected response"
                print(f"{summary}: {riotApiData}")
                record_riot_error(f"rank data for {summoner.fullName}", summary)
                continue

            for data in riotApiData:
                if data['queueType'] == 'RANKED_SOLO_5x5':
                    summoner.tier = data['tier']
                    summoner.rank = data['rank']
                    summoner.leaguePoints = data['leaguePoints']
                    summoner.wins = data['wins']
                    summoner.losses = data['losses']
                    summoner.hotStreak = data['hotStreak']

                    if 'miniSeries' in data:
                        summoner.series = True
                        summoner.seriesWins = data['miniSeries']['wins']
                        summoner.seriesLosses = data['miniSeries']['losses']
                    else:
                        summoner.series = False

            if summoner.tier is None:
                print(f"{summoner.fullName} is unranked")
                continue

            summoner.previousScore = jsonData["summoners"][summonerName]["score"]
            summoner.score = Rank.calculateScore(summoner.tier, summoner.rank, summoner.leaguePoints)
            summoner.deltaScore = summoner.score - summoner.previousScore
            summoner.previousLeaderboardPosition = jsonData["summoners"][summonerName]["leaderboardPosition"]
            summoner.gamesPlayed = summoner.wins + summoner.losses
            summoner.previousGamesPlayed = jsonData["summoners"][summonerName]["gamesPlayed"]
            summoner.deltaGamesPlayed = summoner.gamesPlayed - summoner.previousGamesPlayed

            if daily:
                summoner.dailyScore = jsonData["summoners"][summonerName]['dailyScore']
                summoner.deltaDailyScore = summoner.score - summoner.dailyScore
                summoner.dailyGamesPlayed = jsonData["summoners"][summonerName]['dailyGamesPlayed']
                summoner.deltaDailyGamesPlayed = summoner.gamesPlayed - summoner.dailyGamesPlayed
                summoner.dailyLeaderboardPosition = jsonData["summoners"][summonerName]['dailyLeaderboardPosition']

            summoners.append(summoner)

        except (KeyError, TypeError, ValueError) as error:
            failedSummoners.append(summoner.fullName)
            summary = f"Failed to fetch rank data for {summoner.fullName}"
            print(f"{summary}: {error}")
            log_event("leaderboard_data_error", actor=system_actor(), status="error", summary=summary, details={"summoner": summoner.fullName, "error": str(error)})

    if failedSummoners:
        summary = f"Skipping leaderboard update because rank data failed for: {', '.join(failedSummoners)}"
        print(summary)
        log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"failedSummoners": failedSummoners})
        return [], False

    if not summoners:
        summary = "Skipping leaderboard update because no ranked summoners were available."
        print(summary)
        log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary)
        return [], False

    summoners.sort(key=lambda s: (Rank.tierOrder[s.tier], Rank.rankOrder[s.rank], s.leaguePoints, int(s.wins / (s.wins + s.losses) * 100)), reverse=True)

    for i, summoner in enumerate(summoners):
        summoner.leaderboardPosition = i + 1
        summoner.deltaLeaderboardPosition = summoner.previousLeaderboardPosition - summoner.leaderboardPosition
        jsonData["summoners"][summoner.fullName]['score'] = summoner.score
        jsonData["summoners"][summoner.fullName]['tier'] = summoner.tier
        jsonData["summoners"][summoner.fullName]['rank'] = summoner.rank
        jsonData["summoners"][summoner.fullName]['leaguePoints'] = summoner.leaguePoints
        jsonData["summoners"][summoner.fullName]['leaderboardPosition'] = summoner.leaderboardPosition
        jsonData["summoners"][summoner.fullName]['gamesPlayed'] = summoner.gamesPlayed
        if daily:
            jsonData["summoners"][summoner.fullName]['dailyScore'] = summoner.score
            jsonData["summoners"][summoner.fullName]['dailyLeaderboardPosition'] = summoner.leaderboardPosition
            jsonData["summoners"][summoner.fullName]['dailyGamesPlayed'] = summoner.gamesPlayed
            summoner.deltaDailyLeaderboardPosition = summoner.dailyLeaderboardPosition - summoner.leaderboardPosition

    updated = False
    for summoner in summoners:
        if daily:
            if summoner.deltaDailyScore != 0 or summoner.deltaDailyLeaderboardPosition != 0 or summoner.deltaDailyGamesPlayed != 0:
                updated = True
        else:
            if summoner.deltaScore != 0 or summoner.deltaLeaderboardPosition != 0 or summoner.deltaGamesPlayed != 0 or force:
                updated = True

    summonersList = summoners[:]

    if updated or force:
        allMatchesIds = []
        highEloPlayersPlatforms = []
        highEloPlayersData = {}
        summonersToRefreshMatches = []
        for summoner in summoners:
            savedMatchIds = jsonData["summoners"][summoner.fullName].get("recentMatchIds", [])
            shouldRefreshMatches = force or daily or summoner.deltaGamesPlayed != 0 or len(savedMatchIds) < 5
            if shouldRefreshMatches:
                summonersToRefreshMatches.append(summoner)

        # Collect unique platforms of summoners in high elo
        for summoner in summoners:
            if summoner.tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
                platform = summoner.platform
                if platform not in highEloPlayersPlatforms:
                    highEloPlayersPlatforms.append(platform)

        # Fetch high elo player data for each unique platform
        for platform in highEloPlayersPlatforms:
            cache = jsonData["highEloCache"].get(platform, {})
            if time.time() - cache.get("timestamp", 0) < HIGH_ELO_CACHE_SECONDS:
                highEloPlayersData[platform] = cache.get("players", [])
                continue

            urls = [
                f"https://{platform.lower()}.api.riotgames.com/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5?api_key={riotApKey}",
                f"https://{platform.lower()}.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5?api_key={riotApKey}",
                f"https://{platform.lower()}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5?api_key={riotApKey}",
            ]

            combinedHighEloPlayers = []
            for url in urls:
                ok, data, retryAfter = riot_get(url, f"high elo data for {platform}")
                if not ok:
                    failedSummoners.append(f"high elo cache {platform}")
                    break
                combinedHighEloPlayers.extend(data.get("entries", []))

            if failedSummoners:
                summary = f"Skipping leaderboard update because high elo data failed for {platform}"
                print(summary)
                log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"platform": platform})
                return [], False

            highEloPlayersData[platform] = sorted(combinedHighEloPlayers, key=lambda x: (-x["leaguePoints"], -x["wins"]))
            jsonData["highEloCache"][platform] = {
                "timestamp": time.time(),
                "players": highEloPlayersData[platform]
            }

        # Assign ranks to summoners based on fetched data for their respective platforms
        for summoner in summoners:
            if summoner.tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
                platform = summoner.platform
                for index, player in enumerate(highEloPlayersData[platform], start=1):
                    if player["summonerId"] == summoner.id:
                        summoner.rank = index
                        break

        for summoner in summoners:
            # solo 420 flex 440
            if summoner in summonersToRefreshMatches:
                ok, riotApiData, retryAfter = riot_get(
                    f'https://{summoner.region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{summoner.puuid}/ids?queue=420&start=0&count=5&api_key={riotApKey}',
                    f"match ids for {summoner.fullName}"
                )
                if not ok or not isinstance(riotApiData, list):
                    summary = f"Skipping leaderboard update because match ids failed for {summoner.fullName}"
                    print(summary)
                    log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"summoner": summoner.fullName})
                    return [], False
                jsonData["summoners"][summoner.fullName]["recentMatchIds"] = riotApiData[:5]
            else:
                riotApiData = jsonData["summoners"][summoner.fullName].get("recentMatchIds", [])

            for i, matchId in enumerate(riotApiData):
                allMatchesIds.append(matchId)
                if matchId in jsonData["matchData"]:
                    # print(f"Found {matchId} in json")
                    fetchMatchData(i, summoner, jsonData, matchId)
                else:
                    # print(f'Fetching {matchId}')
                    ok, matchData, retryAfter = riot_get(
                        f'https://{summoner.region}.api.riotgames.com/lol/match/v5/matches/{matchId}?api_key={riotApKey}',
                        f"match data {matchId}"
                    )
                    if not ok:
                        summary = f"Skipping leaderboard update because match data failed for {matchId}"
                        print(summary)
                        log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"matchId": matchId})
                        return [], False

                    jsonData["matchData"][matchId] = matchData

                    fetchMatchData(i, summoner, jsonData, matchId)

        # give crown to the best recent 5 games
        for summoner in summoners:
            summoner.MvpScoreTotal = sum(
                score or 0 for score in [
                    summoner.game1MvpScore,
                    summoner.game2MvpScore,
                    summoner.game3MvpScore,
                    summoner.game4MvpScore,
                    summoner.game5MvpScore
                ]
            )
            # print(f"{summoner.name}, Total: {summoner.MvpScoreTotal}, Game 1: {summoner.game1MvpScore}, Game 2: {summoner.game2MvpScore}, Game 3: {summoner.game3MvpScore}, Game 4: {summoner.game4MvpScore}, Game 5: {summoner.game5MvpScore}")

        playerWithHighestScore = max(summoners, key=lambda x: x.MvpScoreTotal)
        playerWithHighestScore.hasCrown = True

        # clean up matchData
        keysToDelete = []
        for matchId in jsonData["matchData"].keys():
            if matchId not in allMatchesIds:
                # print(f'Deleting {matchId}')
                keysToDelete.append(matchId)

        # Now delete the keys outside of the loop
        for matchId in keysToDelete:
            del jsonData["matchData"][matchId]

        # Save the updated data back to the JSON file
        writeToJsonFile(jsonFile, jsonData)

    return summonersList, updated


def update(force, daily, returnData=False, generate=True):
    list = fetchAllSummonerData(force, daily)

    print(f"\r{datetime.now().strftime('%I:%M:%S %p %d/%m/%Y')}", end="", flush=True)

    if generate and list[0] and (list[1] or force):
        generateImage(list[0], daily)
    if returnData:
        return list
    return list[1]


def checkForNewPatchNotes(jsonFilePath, forceUpdate):
    daysAgo = 0
    daysUntilNextPatch = 0

    def patchVersion(version):
        return tuple(int(part) for part in str(version).split("."))

    def imageUrlFromTag(imgTag):
        imageUrl = imgTag.get("src") or imgTag.get("data-src")
        if imageUrl:
            return imageUrl

        srcset = imgTag.get("srcset")
        if srcset:
            return srcset.split(",")[-1].strip().split()[0]

        return None

    def findPatchHighlightsImage(patchSoup):
        patchHighlightsHeader = patchSoup.find(
            lambda tag: tag.name in ["h2", "h3"] and "patch highlights" in tag.get_text(" ", strip=True).lower()
        )

        if patchHighlightsHeader:
            imageTag = patchHighlightsHeader.find_next("img")
            if imageTag:
                return imageUrlFromTag(imageTag)

        imageTag = patchSoup.find(
            "img",
            src=lambda src: src and ("highlight" in src.lower() or "patch" in src.lower())
        )
        if imageTag:
            return imageUrlFromTag(imageTag)

        return None

    def downloadImage(imageUrl, saveDir):
        response = requests.get(imageUrl)
        if response.status_code == 200:
            # Create the directory if it doesn't exist
            os.makedirs(saveDir, exist_ok=True)

            # Parse the URL to remove query parameters
            parsedUrl = urlparse(imageUrl)
            cleanedFilename = os.path.basename(unquote(parsedUrl.path))

            # Split the filename and extension
            filenameParts = cleanedFilename.split('.')
            if len(filenameParts) > 1:
                # Use the last part as the file extension
                fileExtension = filenameParts[-1]
                # Construct the full path to save the image with correct extension
                savePath = os.path.join(saveDir, f"patch_image.{fileExtension}")
                # Save the image
                with open(savePath, 'wb') as f:
                    f.write(response.content)
                return True, savePath
            else:
                print("Failed to extract file extension from image URL.")
                return False, None
        else:
            print("Failed to download image.")
            return False, None

    latestPatchData = openJsonFile(jsonFilePath) or {}

    latestPatch = latestPatchData.get("latestPatch", 0)

    # URL of the League of Legends patch notes page
    url = "https://www.leagueoflegends.com/en-us/news/tags/patch-notes/"

    response = requests.get(url)
    if response.status_code != 200:
        print(f"Failed to fetch patch notes. Status code: {response.status_code}")
        return False, None, daysAgo, daysUntilNextPatch, None, None

    soup = BeautifulSoup(response.content, "html.parser")

    patchRegex = re.compile(r"(?:League of Legends\s+)?Patch\s+(\d+(?:\.\d+)?)\s+Notes", re.IGNORECASE)
    patchLink = None
    patchMatch = None

    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)
        match = patchRegex.search(text)
        if match:
            patchLink = link
            patchMatch = match
            break

    if patchLink is None or patchMatch is None:
        print("Failed to find latest patch notes article.")
        return False, None, daysAgo, daysUntilNextPatch, None, None

    newPatch = patchMatch.group(1)
    fullUrl = urljoin(url, patchLink["href"])

    timeElement = patchLink.find("time") or soup.find("time")
    if timeElement and timeElement.get("datetime"):
        datetimeStr = timeElement["datetime"]
        datetimeObjDate = datetime.strptime(datetimeStr[:10], "%Y-%m-%d").date()
        dateNow = datetime.now().date()

        daysDifference = abs((datetimeObjDate - dateNow).days)
        daysAgo = daysDifference - 1
        nextPatchDate = datetimeObjDate + timedelta(weeks=2)
        daysUntilNextPatch = (nextPatchDate - dateNow).days + 1

    if (patchVersion(newPatch) > patchVersion(latestPatch)) or forceUpdate:
        patchResponse = requests.get(fullUrl)
        if patchResponse.status_code != 200:
            print(f"Failed to fetch patch article. Status code: {patchResponse.status_code}")
            return False, None, daysAgo, daysUntilNextPatch, None, None

        patchSoup = BeautifulSoup(patchResponse.content, "html.parser")
        imageUrl = findPatchHighlightsImage(patchSoup)
        imgPath = None

        if imageUrl:
            imageUrl = urljoin(fullUrl, imageUrl)
            saveDir = assetPath("Imgs", "patch highlights")
            imgDownloaded, imgPath = downloadImage(imageUrl, saveDir)
            if not imgDownloaded:
                imgPath = None

        latestPatchData["latestPatch"] = newPatch
        writeToJsonFile(jsonFilePath, latestPatchData)

        return True, newPatch, daysAgo, daysUntilNextPatch, fullUrl, imgPath

    return False, newPatch, daysAgo, daysUntilNextPatch, fullUrl, None


def numberOfSummoners(wiggleRoom):
    jsonData = openJsonFile(jsonFile) or {}
    
    summoners = jsonData.get("summoners")
    if isinstance(summoners, (dict, list)):
        count = len(summoners)
    else:
        count = 0

    return count + wiggleRoom
