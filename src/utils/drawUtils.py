from datetime import datetime, timedelta
from io import BytesIO
import os
import requests
from PIL import ImageFont
from PIL import Image, ImageDraw

from .commonUtils import version, Rank, assetPath, outputPath


def drawChampionPlaceholder(canvas, x, y, size):
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((x, y, x + size, y + size), fill=(31, 32, 40), outline=(64, 68, 82), width=3)
    font = ImageFont.truetype(assetPath("ARIAL.TTF"), max(18, size // 3))
    drawTextCentered(canvas, "-", x + size / 2, y + size / 2, font, (132, 138, 154))


def drawChampionImage(canvas, x, y, champ, win, remake, mvp, size=100, drawMvpBadge=True):
    if not champ:
        drawChampionPlaceholder(canvas, x, y, size)
        return

    champName = "Fiddlesticks" if champ == "FiddleSticks" else champ
    imgPath = assetPath("Imgs", "Champ icons", f"{champName}.png")

    try:
        if os.path.exists(imgPath):
            img = Image.open(imgPath).convert("RGBA")
        else:
            response = requests.get(f"http://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champName}.png")
            img = Image.open(BytesIO(response.content)).convert("RGBA")
            os.makedirs(os.path.dirname(imgPath), exist_ok=True)
            img.save(imgPath)
    except Exception:
        drawChampionPlaceholder(canvas, x, y, size)
        return

    # Crop the edges 5%
    width, height = img.size
    left = int(width * 0.05)
    top = int(height * 0.05)
    right = int(width * 0.95)
    bottom = int(height * 0.95)
    img = img.crop((left, top, right, bottom))

    # Make the image round
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, img.size[0], img.size[1]), fill=255)
    img.putalpha(mask)

    # Add a stroke around the circle
    draw = ImageDraw.Draw(img)
    if remake:
        strokeColor = (99, 105, 120)
    else:
        if win:
            if mvp:
                if drawMvpBadge:
                    mvp = Image.open(assetPath("Imgs", "mvp win.png")).convert("RGBA")
                    canvas.paste(mvp, (x + size - 20, y + size - 20), mvp)
                strokeColor = (224, 206, 83)
            else:
                strokeColor = (71, 201, 118)
        else:
            strokeColor = (218, 79, 87)

    strokeWidth = max(3, size // 22)
    draw.ellipse((0, 0, img.size[0], img.size[1]), outline=strokeColor, width=strokeWidth)

    img = img.resize((size, size), resample=Image.BICUBIC)

    # Paste the image onto the canvas at the specified coordinates
    canvas.paste(img, (x, y), img)


def drawURLImage(canvas, url, x, y, w=0, opacity=1.0, cropTop=None, cropBottom=None, makeRound=False):
    response = requests.get(url)
    img = Image.open(BytesIO(response.content))
    img = img.convert("RGBA")

    # Crop the top and bottom of the image
    if cropTop is not None and cropBottom is not None:
        height = img.size[1]
        top = int(height * cropTop)
        bottom = int(height * (1 - cropBottom))
        img = img.crop((0, top, img.size[0], bottom))

    if w != 0:
        # Resize the image
        newWidth = w
        widthPercent = (newWidth / float(img.size[0]))
        newHeight = int((float(img.size[1]) * float(widthPercent)))
        img = img.resize((newWidth, newHeight))

    if makeRound:
        # Create a circular mask
        mask = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, img.size[0], img.size[1]), fill=255)

        # Apply the mask to the image
        img.putalpha(mask)

    if opacity != 1:
        img.putalpha(int(255 * opacity))

    canvas.paste(img, (x, y), img)


def drawFileImage(canvas, file, x, y, w=0, opacity=1.0, cropTop=None, cropBottom=None):
    img = Image.open(file)
    img = img.convert("RGBA")

    if cropTop != None and cropBottom != None:
        # Crop the top and bottom of the image
        height = img.size[1]
        top = int(height * cropTop)
        bottom = int(height * (1 - cropBottom))
        img = img.crop((0, top, img.size[0], bottom))

    if w != 0:
        # Resize the image
        newWidth = w
        widthPercent = (newWidth / float(img.size[0]))
        newHeight = int((float(img.size[1]) * float(widthPercent)))
        img = img.resize((newWidth, newHeight))

    if opacity != 1:
        img.putalpha(int(255 * opacity))

    canvas.paste(img, (x, y), img)


def drawTextCentered(canvas, text, x, y, font, colour=((255, 255, 255))):
    draw = ImageDraw.Draw(canvas)
    textBbox = draw.textbbox((0, 0), text, font=font)

    centerX = x - (textBbox[2] + textBbox[0]) / 2
    centerY = y - (textBbox[3] + textBbox[1]) / 2

    draw.text((centerX, centerY), text, colour, font=font)


def drawSegmentedTextCentered(canvas, segments, x, y, font):
    draw = ImageDraw.Draw(canvas)
    totalWidth = sum(textWidth(draw, text, font) for text, _ in segments)
    textBbox = draw.textbbox((0, 0), "".join(text for text, _ in segments), font=font)
    currentX = x - totalWidth / 2
    centerY = y - (textBbox[3] + textBbox[1]) / 2

    for text, colour in segments:
        draw.text((currentX, centerY), text, fill=colour, font=font)
        currentX += textWidth(draw, text, font)


def formatTime(seconds):
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s"


def formatDamage(damage):
    if damage is None:
        return "-"
    return "{:,}".format(damage)


def textWidth(draw, text, font):
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0]


def truncateText(draw, text, font, maxWidth):
    text = str(text)
    if textWidth(draw, text, font) <= maxWidth:
        return text

    suffix = "..."
    while text and textWidth(draw, text + suffix, font) > maxWidth:
        text = text[:-1]
    return text + suffix if text else suffix


def drawLabel(draw, text, x, y, font, fill=(139, 146, 164)):
    draw.text((x, y), text.upper(), fill=fill, font=font)


def drawPill(draw, box, fill, outline=None, radius=18, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def drawLineChart(draw, points, colour, width=4):
    if len(points) < 2:
        return
    draw.line(points, fill=colour, width=width, joint="curve")


def timelineGoldSeries(matchData, timelineData):
    participants = {
        participant.get("participantId"): participant
        for participant in matchData.get("info", {}).get("participants", [])
    }
    series = {
        participantId: []
        for participantId in participants
        if participantId is not None
    }

    for frame in timelineData.get("info", {}).get("frames", []):
        minute = int(frame.get("timestamp", 0) // 60000)
        participantFrames = frame.get("participantFrames", {})
        for participantId in series:
            frameData = participantFrames.get(str(participantId)) or participantFrames.get(participantId)
            if frameData:
                series[participantId].append((minute, frameData.get("totalGold", 0)))

    return participants, {participantId: values for participantId, values in series.items() if values}


def generateGoldGraphImage(matchData, timelineData):
    participants, series = timelineGoldSeries(matchData, timelineData)
    if not series:
        return None

    canvasWidth = 1500
    canvasHeight = 900
    paddingLeft = 110
    paddingRight = 360
    paddingTop = 115
    paddingBottom = 95
    chartLeft = paddingLeft
    chartTop = paddingTop
    chartRight = canvasWidth - paddingRight
    chartBottom = canvasHeight - paddingBottom

    canvas = Image.new("RGBA", (canvasWidth, canvasHeight), (35, 37, 46, 255))
    draw = ImageDraw.Draw(canvas)

    fonts = {
        "title": ImageFont.truetype(assetPath("ARIAL.TTF"), 42),
        "subtitle": ImageFont.truetype(assetPath("ARIAL.TTF"), 23),
        "axis": ImageFont.truetype(assetPath("ARIAL.TTF"), 18),
        "legend": ImageFont.truetype(assetPath("ARIAL.TTF"), 19),
        "small": ImageFont.truetype(assetPath("ARIAL.TTF"), 16),
    }

    info = matchData.get("info", {})
    duration = formatTime(int(info.get("gameDuration", 0))) if info.get("gameDuration") else "-"
    draw.text((34, 28), "Gold graph", fill=(248, 249, 252), font=fonts["title"])
    draw.text((37, 78), f"Total gold by minute - {duration}", fill=(139, 146, 164), font=fonts["subtitle"])

    maxMinute = max(minute for values in series.values() for minute, _ in values)
    maxGold = max(gold for values in series.values() for _, gold in values)
    maxMinute = max(1, maxMinute)
    maxGold = max(1000, int(((maxGold + 999) // 1000) * 1000))

    draw.rounded_rectangle((chartLeft - 18, chartTop - 18, chartRight + 18, chartBottom + 18), radius=22, fill=(31, 32, 40), outline=(57, 61, 74), width=2)

    gridColour = (54, 58, 70)
    labelColour = (166, 173, 190)
    for step in range(0, 6):
        y = chartBottom - ((chartBottom - chartTop) * step / 5)
        gold = int(maxGold * step / 5)
        draw.line((chartLeft, y, chartRight, y), fill=gridColour, width=1)
        draw.text((24, y - 10), f"{round(gold / 1000, 1)}k", fill=labelColour, font=fonts["axis"])

    xStep = 5 if maxMinute <= 35 else 10
    for minute in range(0, maxMinute + 1, xStep):
        x = chartLeft + ((chartRight - chartLeft) * minute / maxMinute)
        draw.line((x, chartTop, x, chartBottom), fill=(45, 49, 60), width=1)
        drawTextCentered(canvas, str(minute), x, chartBottom + 34, fonts["axis"], labelColour)

    draw.text((chartLeft + 330, chartBottom + 58), "Minute", fill=labelColour, font=fonts["axis"])
    draw.text((30, chartTop - 36), "Gold", fill=labelColour, font=fonts["axis"])

    teamColours = {
        100: [(88, 166, 255), (91, 206, 250), (80, 190, 140), (163, 221, 112), (212, 235, 130)],
        200: [(255, 120, 120), (255, 161, 102), (232, 193, 91), (214, 133, 255), (184, 142, 255)],
    }
    teamIndexes = {100: 0, 200: 0}
    lineColours = {}

    sortedParticipants = sorted(
        participants.values(),
        key=lambda participant: (participant.get("teamId", 0), participant.get("participantId", 0))
    )
    for participant in sortedParticipants:
        teamId = participant.get("teamId")
        palette = teamColours.get(teamId, [(220, 225, 236)])
        index = teamIndexes.get(teamId, 0)
        lineColours[participant.get("participantId")] = palette[index % len(palette)]
        teamIndexes[teamId] = index + 1

    def chartPoint(minute, gold):
        x = chartLeft + ((chartRight - chartLeft) * minute / maxMinute)
        y = chartBottom - ((chartBottom - chartTop) * gold / maxGold)
        return x, y

    for participantId, values in series.items():
        points = [chartPoint(minute, gold) for minute, gold in values]
        drawLineChart(draw, points, lineColours.get(participantId, (220, 225, 236)))

    legendX = chartRight + 52
    legendY = chartTop - 8
    for teamId, label in [(100, "Blue side"), (200, "Red side")]:
        draw.text((legendX, legendY), label, fill=(248, 249, 252), font=fonts["subtitle"])
        legendY += 36
        for participant in [p for p in sortedParticipants if p.get("teamId") == teamId]:
            participantId = participant.get("participantId")
            colour = lineColours.get(participantId, (220, 225, 236))
            name = participant.get("riotIdGameName") or participant.get("summonerName") or "Unknown"
            champion = participant.get("championName", "Unknown")
            text = truncateText(draw, f"{champion} - {name}", fonts["legend"], 275)
            draw.rounded_rectangle((legendX, legendY + 4, legendX + 26, legendY + 16), radius=6, fill=colour)
            draw.text((legendX + 38, legendY - 2), text, fill=(220, 225, 236), font=fonts["legend"])
            legendY += 30
        legendY += 24

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def drawSmallMvpBadge(canvas, x, y):
    draw = ImageDraw.Draw(canvas)
    badgeBox = (x, y, x + 46, y + 20)
    draw.rounded_rectangle(badgeBox, radius=7, fill=(78, 54, 20), outline=(224, 206, 83), width=2)
    font = ImageFont.truetype(assetPath("ARIAL.TTF"), 14)
    drawTextCentered(canvas, "MVP", x + 23, y + 10, font, (255, 232, 104))


def drawChangeBadge(canvas, x, y, daily, summoner, fonts):
    draw = ImageDraw.Draw(canvas)
    scoreDelta = summoner.deltaDailyScore if daily else summoner.deltaScore
    gamesDelta = summoner.deltaDailyGamesPlayed if daily else summoner.deltaGamesPlayed
    positionDelta = summoner.deltaDailyLeaderboardPosition if daily else summoner.deltaLeaderboardPosition

    if scoreDelta > 0:
        text = f"+{scoreDelta}"
        fill = (43, 112, 70)
        colour = (112, 234, 150)
    elif scoreDelta < 0:
        text = str(scoreDelta)
        fill = (116, 49, 56)
        colour = (255, 132, 140)
    elif gamesDelta:
        text = "-0"
        fill = (83, 62, 45)
        colour = (255, 179, 95)
    else:
        text = "0"
        fill = (42, 45, 56)
        colour = (139, 146, 164)

    drawPill(draw, (x, y, x + 88, y + 38), fill, None, radius=19)
    drawTextCentered(canvas, text, x + 44, y + 19, fonts["change"], colour)

    if positionDelta:
        arrowColour = (112, 234, 150) if positionDelta > 0 else (255, 132, 140)
        if positionDelta > 0:
            points = [(x + 36, y + 55), (x + 44, y + 43), (x + 52, y + 55)]
        else:
            points = [(x + 36, y + 43), (x + 44, y + 55), (x + 52, y + 43)]
        draw.polygon(points, fill=arrowColour)


def drawMatchChip(canvas, summoner, gameNumber, x, y, fonts):
    draw = ImageDraw.Draw(canvas)
    champ = summoner.__dict__.get(f"game{gameNumber}Champion")
    win = summoner.__dict__.get(f"game{gameNumber}Win")
    remake = summoner.__dict__.get(f"game{gameNumber}Remake")
    mvp = summoner.__dict__.get(f"game{gameNumber}Mvp")
    kills = summoner.__dict__.get(f"game{gameNumber}Kills")
    deaths = summoner.__dict__.get(f"game{gameNumber}Deaths")
    assists = summoner.__dict__.get(f"game{gameNumber}Assists")
    damage = summoner.__dict__.get(f"game{gameNumber}DamageDealtToChampions")

    chipFill = (38, 40, 50) if champ else (31, 32, 40)
    chipOutline = (64, 68, 82)
    if remake:
        chipOutline = (101, 107, 122)
    elif win is True:
        chipOutline = (54, 136, 84)
    elif win is False:
        chipOutline = (144, 58, 65)

    drawPill(draw, (x, y, x + 202, y + 82), chipFill, chipOutline, radius=24, width=2)
    drawChampionImage(canvas, x + 10, y + 9, champ, win, remake, mvp, size=64, drawMvpBadge=False)

    kda = f"{kills}/{deaths}/{assists}" if None not in (kills, deaths, assists) else "-/-/-"
    draw.text((x + 84, y + 17), kda, fill=(244, 246, 252), font=fonts["kda"])
    draw.text((x + 84, y + 47), formatDamage(damage), fill=(166, 173, 190), font=fonts["damage"])

    if mvp:
        drawSmallMvpBadge(canvas, x + 148, y + 8)


def drawHeader(canvas, daily, fonts, canvasWidth, headerHeight):
    draw = ImageDraw.Draw(canvas)
    title = "Daily Ranking" if daily else "The Race Ranking"
    subtitle = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%y") if daily else "Live solo queue standings"

    draw.text((28, 24), title, fill=(248, 249, 252), font=fonts["title"])
    draw.text((31, 72), subtitle, fill=(139, 146, 164), font=fonts["subtitle"])

    drawLabel(draw, "Player", 244, headerHeight - 35, fonts["label"])
    drawLabel(draw, "Change", 598, headerHeight - 35, fonts["label"])
    drawLabel(draw, "Recent games", 720, headerHeight - 35, fonts["label"])
    draw.line((28, headerHeight - 12, canvasWidth - 28, headerHeight - 12), fill=(57, 61, 74), width=2)


def recentLosses(summoner):
    losses = 0
    for gameNumber in range(1, 6):
        if summoner.__dict__.get(f"game{gameNumber}Remake"):
            continue
        if summoner.__dict__.get(f"game{gameNumber}Win") is False:
            losses += 1
        else:
            break
    return losses


def drawSummonerRow(canvas, summoner, index, y, daily, icons, hotStreakIcon, coldStreakIcon, crownIcon, fonts):
    draw = ImageDraw.Draw(canvas)
    x = 28
    rowWidth = 1764
    rowHeight = 124
    accentColours = [(218, 178, 74), (194, 199, 208), (190, 119, 51)]
    accent = accentColours[index] if index < len(accentColours) else (70, 75, 91)

    drawPill(draw, (x, y, x + rowWidth, y + rowHeight), (42, 44, 55), accent, radius=26, width=3 if index < 3 else 1)
    draw.rounded_rectangle((x, y, x + 10, y + rowHeight), radius=5, fill=accent)

    rankCenterX = x + 42
    rankCenterY = y + 62
    draw.ellipse((rankCenterX - 34, rankCenterY - 34, rankCenterX + 34, rankCenterY + 34), fill=(31, 32, 40))
    drawTextCentered(canvas, str(summoner.leaderboardPosition), rankCenterX, rankCenterY, fonts["rank"], (248, 249, 252))

    iconBack = (x + 92, y + 17, x + 182, y + 107)
    draw.ellipse(iconBack, fill=(31, 32, 40))
    tierIcon = icons.get(summoner.tier)
    if tierIcon:
        canvas.paste(tierIcon, (x + 104, y + 29), tierIcon)

    if summoner.hasCrown:
        canvas.paste(crownIcon, (x + 128, y + 10), crownIcon)

    playerX = x + 212
    name = truncateText(draw, summoner.name, fonts["name"], 280)
    draw.text((playerX, y + 16), name, fill=(248, 249, 252), font=fonts["name"])
    nameWidth = textWidth(draw, name, fonts["name"])
    tagline = truncateText(draw, f"#{summoner.tagline}", fonts["tagline"], 78)
    draw.text((playerX + nameWidth + 8, y + 38), tagline, fill=(178, 184, 199), font=fonts["tagline"])

    streakX = playerX + nameWidth + textWidth(draw, tagline, fonts["tagline"]) + 16
    if summoner.hotStreak:
        canvas.paste(hotStreakIcon, (streakX, y + 27), hotStreakIcon)
    elif recentLosses(summoner) >= 3:
        canvas.paste(coldStreakIcon, (streakX, y + 27), coldStreakIcon)

    rankText = f"{summoner.tier} {Rank.rankToNumber(summoner.rank)}"
    draw.text((playerX, y + 58), rankText, fill=(229, 233, 242), font=fonts["tier"])

    totalGames = summoner.wins + summoner.losses
    winrate = round(summoner.wins / totalGames * 100, 1) if totalGames else 0
    statY = y + 87
    statFill = (31, 32, 40)
    lpFill = (48, 53, 67)
    statCenterY = statY + 17
    drawPill(draw, (playerX, statY, playerX + 108, statY + 34), lpFill, (82, 90, 112), radius=17, width=1)
    drawTextCentered(canvas, f"{summoner.leaguePoints} LP", playerX + 54, statCenterY, fonts["lp"], (248, 249, 252))
    drawPill(draw, (playerX + 116, statY, playerX + 210, statY + 34), statFill, None, radius=17)
    drawSegmentedTextCentered(
        canvas,
        [
            (str(summoner.wins), (112, 234, 150)),
            ("/", (178, 184, 199)),
            (str(summoner.losses), (255, 132, 140)),
        ],
        playerX + 163,
        statCenterY,
        fonts["small"],
    )
    drawPill(draw, (playerX + 218, statY, playerX + 302, statY + 34), statFill, None, radius=17)
    drawTextCentered(canvas, f"{winrate}%", playerX + 260, statCenterY, fonts["small"], (220, 225, 236))

    drawChangeBadge(canvas, x + 570, y + 34, daily, summoner, fonts)

    for gameNumber in range(1, 6):
        drawMatchChip(canvas, summoner, gameNumber, x + 692 + ((gameNumber - 1) * 214), y + 21, fonts)


def generateImage(summones, daily):
    canvasWidth = 1820
    headerHeight = 130
    rowHeight = 124
    rowGap = 14
    footerHeight = 28
    canvasHeight = headerHeight + (rowHeight + rowGap) * len(summones) + footerHeight

    canvas = Image.new('RGBA', (canvasWidth, canvasHeight), (35, 37, 46, 255))
    draw = ImageDraw.Draw(canvas)

    icons = {
        tier: Image.open(Rank.iconPath[tier]).convert("RGBA").resize((66, 66), resample=Image.BICUBIC)
        for tier in Rank.iconPath
    }

    hotStreakIcon = Image.open(assetPath('Imgs', 'Fire emoji.png')).convert("RGBA").resize((26, 26), resample=Image.BICUBIC)
    coldStreakIcon = Image.open(assetPath('Imgs', 'Skull emoji.png')).convert("RGBA").resize((26, 26), resample=Image.BICUBIC)
    crownIcon = Image.open(assetPath("Imgs", "crown.png")).convert("RGBA").resize((32, 32), resample=Image.BICUBIC)

    fonts = {
        "title": ImageFont.truetype(assetPath("ARIAL.TTF"), 42),
        "subtitle": ImageFont.truetype(assetPath("ARIAL.TTF"), 22),
        "label": ImageFont.truetype(assetPath("ARIAL.TTF"), 17),
        "rank": ImageFont.truetype(assetPath("ARIAL.TTF"), 42),
        "name": ImageFont.truetype(assetPath("ARIAL.TTF"), 35),
        "tagline": ImageFont.truetype(assetPath("ARIAL.TTF"), 15),
        "tier": ImageFont.truetype(assetPath("ARIAL.TTF"), 24),
        "small": ImageFont.truetype(assetPath("ARIAL.TTF"), 16),
        "lp": ImageFont.truetype(assetPath("ARIAL.TTF"), 20),
        "change": ImageFont.truetype(assetPath("ARIAL.TTF"), 25),
        "kda": ImageFont.truetype(assetPath("ARIAL.TTF"), 23),
        "damage": ImageFont.truetype(assetPath("ARIAL.TTF"), 20),
        "footer": ImageFont.truetype(assetPath("ARIAL.TTF"), 22),
    }

    drawHeader(canvas, daily, fonts, canvasWidth, headerHeight)

    y = headerHeight
    for index, summoner in enumerate(summones):
        drawSummonerRow(canvas, summoner, index, y, daily, icons, hotStreakIcon, coldStreakIcon, crownIcon, fonts)
        y += rowHeight + rowGap

    if daily:
        canvas.save(outputPath('Daily Rank list.png'))
    else:
        canvas.save(outputPath('Rank list.png'))
