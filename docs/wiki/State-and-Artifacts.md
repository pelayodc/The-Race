# State and Artifacts

## PostgreSQL Runtime State

PostgreSQL is the primary runtime state store. `DATABASE_URL` points the bot at the database. A legacy `DATA_JSON` file can be imported automatically on first startup when `AUTO_MIGRATE_JSON=true` and the database is empty.

Primary tables:

- `summoners`: configured leaderboard players and cached rank fields.
- `match_data`: cached match details used for recent games and reports.
- `match_timeline_data`: cached timelines used for gold graphs.
- `runtime_state`: channels, message IDs, linked accounts, matchmaking, high-elo cache, SoloQ subscriptions, runtime status, and Riot errors.
- `audit_events`: operational audit log rows.

`src/storage.py` owns database initialization, JSON migration, state import/export, and audit persistence. `src/state.py` still initializes defaults for callers that work with the compatibility state dictionary.

## Audit Log

Audit events are written to PostgreSQL through `src/utils/auditUtils.py` when `DATABASE_URL` is configured. Without a database, the legacy JSONL audit log fallback is still available for local development.

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

## Files and Tables Operators Should Not Edit Manually

Avoid manual edits while the bot is running:

- PostgreSQL runtime tables
- legacy `data.json` migration source
- audit JSONL log
- generated `Rank list.png`
- generated `Daily Rank list.png`
- cached champion or patch assets unless intentionally refreshing assets

If manual recovery is required, stop the bot first and download a backup from the admin panel where possible.
