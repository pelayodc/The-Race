# State and Artifacts

## `data.json` and Runtime Status Fields

`data.json` is the primary runtime state file. Its path defaults to the repository root and can be overridden with `DATA_JSON`.

Important state groups:

- `summoners`: configured leaderboard players and cached rank fields.
- `matchData`: cached match details used for recent games and reports.
- `highEloCache`: cached Master/Grandmaster/Challenger league data by platform.
- `discordLinks`: normalized Discord user to summoner mapping.
- `discordLinkRequests`: pending user link requests.
- `matchmakingQueue`: active matchmaking players.
- `matchmakingDraft`: active captain draft state.
- `adminMessageId`, `leaderboardMessageId`, `matchmakingMessageId`: persistent Discord message IDs.
- `leaderboardLastUpdateAt`, `leaderboardLastUpdateMode`, `leaderboardLastUpdateStatus`, `leaderboardLastEstimatedApiCalls`: leaderboard runtime status.
- `leaderboardLastDailyImageAt`, `leaderboardLastDailyImageStatus`, `leaderboardLastDailyImageMessageId`, `leaderboardLastDailyImageChannelId`, `leaderboardLastDailyImageError`: daily image status.
- `lastRiotError`: most recent Riot/API failure summary.

`src/state.py` initializes defaults for admin and matchmaking state. Prefer using existing helpers rather than editing state shape ad hoc.

## Audit Log

Audit events are written by `src/utils/auditUtils.py` to a JSONL file. The admin status panel can show recent logs, filter by category, search by actor, and summarize the last 24 hours.

Audit categories are defined in `src/bot_runtime.py` and include admin, matchmaking, Riot/API, leaderboard, links, and operations.

## Match and High-Elo Cache

Match cache and high-elo cache reduce Riot API calls:

- `matchData` stores match payloads by match ID.
- `recentMatchIds` on summoner records tracks recent Solo/Duo games.
- `highEloCache` stores high-elo league entries and expires after 600 seconds.

Do not clear these caches while the bot is running unless you intend to force fresh Riot API calls.

## Rank Image Outputs

Generated image outputs are artifacts, not source data:

- `Rank list.png`
- `Daily Rank list.png`

They are produced by `generateImage()` in `src/utils/drawUtils.py`, using leaderboard data fetched in `src/utils/dataUtils.py`. `send_daily_rank_image()` sends `Daily Rank list.png` to Discord.

## Source Assets

Source assets live under `src/Imgs/` and include:

- Rank icons in `src/Imgs/Ranks/`.
- Champion icons in `src/Imgs/Champ icons/`.
- MVP, crown, fire, and skull images.
- Patch highlight image cache under `src/Imgs/patch highlights/`.

`src/ARIAL.TTF` is used by Pillow image rendering.

## Files Operators Should Not Edit Manually

Avoid manual edits to these files while the bot is running:

- `data.json`
- audit JSONL log
- generated `Rank list.png`
- generated `Daily Rank list.png`
- cached champion or patch assets unless intentionally refreshing assets

If manual recovery is required, stop the bot first and download a backup from the admin panel where possible.
