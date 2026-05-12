# Troubleshooting

## Riot Rate Limit or Backoff

**Symptom**: Leaderboard refreshes are skipped and status shows Riot backoff.

Likely causes:

- Riot returned HTTP 429.
- Another refresh was attempted before the retry window expired.

Checks:

1. Open **Admin panel -> Status / Logs**.
2. Check **Riot backoff** and **Last Riot error**.
3. Check recent audit logs for `riot_api_error` or `leaderboard_update_skipped`.
4. Wait until the displayed retry time. Do not repeatedly force refresh during backoff.

## Leaderboard Update Returns No Summoners

**Symptom**: Status shows skipped update or "no summoners returned".

Likely causes:

- No summoners are configured.
- Configured summoners are unranked.
- Riot rank data failed for one or more players.
- `data.json` is missing expected summoner fields.

Checks:

1. Open **Leaderboard users** in the admin panel.
2. Confirm summoners have platform and region values.
3. Check recent Riot errors in **Status / Logs**.
4. Confirm `RIOT_API_KEY` is configured and valid.

## Daily Image Is Not Generated or Not Sent

**Symptom**: The daily rank image is missing in Discord or status shows an error.

Likely causes:

- No ranked summoners were available.
- Riot backoff blocked the refresh.
- `Daily Rank list.png` was not generated.
- `DISCORD_CHANNEL` points to a missing channel.
- The bot cannot send files/messages in the daily channel.

Checks:

1. Open **Status / Logs** and review **Last daily image sent**.
2. Use **Test permissions** for configured channels.
3. Confirm `DISCORD_CHANNEL` points to the intended text channel.
4. Check whether Riot backoff is active.
5. Use **Force daily image** only when backoff is inactive.

## Discord Channel or Permission Failure

**Symptom**: Setup, refresh, or sending actions fail silently or return a missing channel/permission message.

Likely causes:

- Channel ID is stale or deleted.
- Bot lacks View Channel, Send Messages, Embed Links, or Read Message History.
- Matchmaking separate channels also need Manage Channels and Move Members.

Checks:

1. Run **Status / Logs -> Test permissions**.
2. Reconfigure channels from **App settings** or slash commands.
3. Confirm the bot role is above relevant managed roles where voice movement is needed.

## Persistent Message Missing or Stale

**Symptom**: Admin, leaderboard, or matchmaking message disappeared or stopped updating.

Likely causes:

- Message was deleted.
- Channel was moved or deleted.
- Stored message ID no longer matches a Discord message.

Checks:

1. Open **Status / Logs -> Health check**.
2. Use **Recreate messages**.
3. Reconfigure the affected channel if the health check reports a missing channel.

## Linked Account Request Issues

**Symptom**: A user cannot link their Discord account or an admin cannot approve a request.

Likely causes:

- Summoner is not in the leaderboard.
- Tagline was entered with extra `#` or casing differences.
- Summoner is already linked to another Discord user.
- Pending request became stale after summoner removal.

Checks:

1. Confirm the summoner exists in **Leaderboard users**.
2. Use normalized `name` and `tagline`.
3. Open **Linked accounts** in the admin panel.
4. Rebuild/refresh the admin panel by opening **Refresh** if data changed recently.

## Matchmaking Queue or Draft Stuck

**Symptom**: Queue is not starting, captain draft is stuck, or team channels remain.

Likely causes:

- Fewer than two queued players.
- Odd players are blocked by policy.
- Role matching requires 10 players.
- A captain did not pick before timeout.
- Bot lacks voice channel permissions.

Checks:

1. Review the matchmaking embed for mode, queue size, role mode, and odd-player policy.
2. Use admin matchmaking controls to inspect configuration.
3. Wait for captain draft timeout or remove inactive players.
4. Run **Test permissions** if separate voice channels are enabled.
5. Voice-state updates clean empty generated team channels; if not, verify bot permissions.
