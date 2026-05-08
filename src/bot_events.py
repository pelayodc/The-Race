import math
from datetime import datetime, timedelta

import disnake
import pytz
from disnake.ext import tasks

import bot_runtime
from admin_panel import AdminView
from bot_runtime import TASKS
from discord_helpers import get_discord_channel, send_temporary_public_message
from leaderboard import estimate_leaderboard_api_calls, send_or_edit_leaderboard, set_leaderboard_runtime_status
from matchmaking import MatchmakingView, delete_empty_matchmaking_team_channels, finish_captain_draft_if_complete, is_draft_complete, process_captain_draft_timeout, remove_player_from_matchmaking_draft, user_queue_index
from persistent_messages import refresh_admin_message, refresh_configured_admin_message, refresh_matchmaking_message, setup_matchmaking_message
from state import admin_channel_id, ensure_admin_state, ensure_matchmaking_state, leaderboard_channel_id, load_json_data, matchmaking_channel_id
from utils.auditUtils import log_event, system_actor
from utils.commonUtils import dailyPostTimer, discordChannel, jsonFile, requestLimit
from utils.dataUtils import checkForNewPatchNotes, numberOfSummoners, riotBackoffRemaining, riotBackoffTimestamp, update
from utils.jsonUtils import openJsonFile, writeToJsonFile


def register_events(bot):
    @bot.event
    async def on_ready():
        print('Logged in as {0.user} at {1}'.format(bot, datetime.now().strftime('%I:%M:%S %p %d/%m/%Y')))
        print("")
        if not bot_runtime.matchmaking_view_registered:
            bot.add_view(MatchmakingView())
            bot.add_view(AdminView())
            bot_runtime.matchmaking_view_registered = True
        await setup_matchmaking_message()
        json_data = load_json_data()
        if json_data.get("adminChannelId"):
            admin_channel = await get_discord_channel(admin_channel_id(json_data))
            if admin_channel:
                await refresh_admin_message(admin_channel, json_data)
        if not updateRaceImage.is_running():
            updateRaceImage.start()
            updatePatchNotes.start()
        if not captainDraftTimeout.is_running():
            captainDraftTimeout.start()

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
                draft_changed, draft_cancelled = remove_player_from_matchmaking_draft(json_data, member.id)
                queue_changed = True
                if draft_cancelled:
                    log_event("matchmaking_captain_draft_cancelled", actor=system_actor(), status="error", summary=f"Captain draft cancelled because <@{member.id}> left voice.", details={"userId": str(member.id)})
                elif draft_changed:
                    log_event("matchmaking_captain_draft_player_removed", actor=system_actor(), status="success", summary=f"<@{member.id}> removed from active captain draft.", details={"userId": str(member.id)})
            else:
                queue[index]["voiceChannelId"] = after.channel.id
                queue_changed = True

        if queue_changed:
            if json_data.get("matchmakingDraft") and is_draft_complete(json_data):
                success, message, json_data = await finish_captain_draft_if_complete(member.guild, json_data)
                log_event("matchmaking_captain_draft_finished", actor=system_actor(), status="success" if success else "error", summary=message or "Captain draft finished after voice update.")
            else:
                writeToJsonFile(jsonFile, json_data)
            channel = await get_discord_channel(matchmaking_channel_id(json_data))
            if channel:
                await refresh_matchmaking_message(channel, json_data)
                if message:
                    await send_temporary_public_message(channel, message)
            await refresh_configured_admin_message(json_data)

        await delete_empty_matchmaking_team_channels(member.guild)

    @tasks.loop(minutes=120)
    async def updatePatchNotes():
        updateAvailable, updatedPatch, daysAgo, daysTillNext, fullUrl, imagePath = checkForNewPatchNotes(jsonFile, False)
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

    @tasks.loop(seconds=15)
    async def captainDraftTimeout():
        try:
            await process_captain_draft_timeout()
        except Exception as error:
            log_event("matchmaking_captain_timeout_error", actor=system_actor(), status="error", summary=f"Captain draft timeout failed: {error}")

    @tasks.loop(seconds=60)
    async def updateRaceImage():
        json_data = ensure_admin_state(openJsonFile(jsonFile) or {})
        estimated_calls = estimate_leaderboard_api_calls(json_data)
        safeRequestLimit = requestLimit if requestLimit and requestLimit > 0 else 100
        calculatedInterval = math.floor(60 * numberOfSummoners(5) / (safeRequestLimit * 0.7))
        interval = max(calculatedInterval, 120)

        updateRaceImage.change_interval(seconds=interval)

        if riotBackoffRemaining() > 0:
            retryTime = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
            summary = f"Skipping Riot update until {retryTime} due to rate limit"
            print(summary)
            json_data = set_leaderboard_runtime_status(json_data, "normal", "rate_limited", estimated_calls, summary)
            log_event("leaderboard_update_skipped", actor=system_actor(), status="error", summary=summary, details={"retryTime": retryTime})
            await refresh_configured_admin_message(json_data)
            return

        lastRunTime = json_data['runtime']
        # Set the timezone to Europe/London
        timezone = pytz.timezone('Europe/Madrid')
        currentTime = datetime.now(tz=timezone)
        dateStr = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%y")

        dailyTime = currentTime.replace(hour=dailyPostTimer, minute=0, second=0, microsecond=0).timestamp()

        # If it's past 9pm and last run time is before 9pm today, update the image
        if currentTime.timestamp() > dailyTime > lastRunTime:
            json_data['runtime'] = dailyTime
            writeToJsonFile(jsonFile, json_data)
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, True, returnData=True, generate=False)
            status = "updated" if summoners and (updated or force_leaderboard) else "no_changes" if summoners else "skipped"
            json_data = set_leaderboard_runtime_status(json_data, "daily", status, estimated_calls, None if summoners else "Daily leaderboard update returned no summoners.")
            log_event("leaderboard_update", actor=system_actor(), status="success" if summoners else "error", summary=f"Daily leaderboard update {status}.", details={"updated": bool(updated), "force": bool(force_leaderboard), "summoners": len(summoners or [])})
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    log_event("leaderboard_update", actor=system_actor(), status="error", summary="Leaderboard channel was not found.", details={"channelId": str(leaderboard_channel_id(json_data))})
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners, True, dateStr)
                writeToJsonFile(jsonFile, latest_json_data)
            await refresh_configured_admin_message()
        else:
            force_leaderboard = not json_data.get("leaderboardMessageId")
            summoners, updated = update(force_leaderboard, False, returnData=True, generate=False)
            status = "updated" if summoners and (updated or force_leaderboard) else "no_changes" if summoners else "skipped"
            json_data = set_leaderboard_runtime_status(json_data, "normal", status, estimated_calls, None if summoners else "Leaderboard update returned no summoners.")
            log_event("leaderboard_update", actor=system_actor(), status="success" if summoners else "error", summary=f"Normal leaderboard update {status}.", details={"updated": bool(updated), "force": bool(force_leaderboard), "summoners": len(summoners or [])})
            if summoners and (updated or force_leaderboard):
                channel = await get_discord_channel(leaderboard_channel_id(json_data))
                if not channel:
                    log_event("leaderboard_update", actor=system_actor(), status="error", summary="Leaderboard channel was not found.", details={"channelId": str(leaderboard_channel_id(json_data))})
                    return
                latest_json_data = openJsonFile(jsonFile)
                latest_json_data['leaderboardMessageId'] = await send_or_edit_leaderboard(channel, latest_json_data, summoners)
                writeToJsonFile(jsonFile, latest_json_data)
            await refresh_configured_admin_message()

    TASKS["updateRaceImage"] = updateRaceImage
    TASKS["updatePatchNotes"] = updatePatchNotes
    TASKS["captainDraftTimeout"] = captainDraftTimeout
