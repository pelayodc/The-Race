# Project Scope

## Core Features

The Race currently focuses on these Discord server workflows:

- Solo/Duo leaderboard tracking for configured Riot accounts.
- Daily leaderboard image generation and posting.
- Recent-game summaries with champion, KDA, damage, win/loss, remake, MVP, and streak context.
- MVP and crown tracking based on recent match performance.
- Patch note detection and manual patch note lookup.
- Discord account linking to leaderboard summoners.
- Personal `/me` reports backed by cached leaderboard and match data.
- Matchmaking queue with random, balanced-rank, and captain draft modes.
- Admin panel for configuration, status, logs, backups, forced refreshes, persistent messages, and language selection.

## Supported Workflows

- Add or remove leaderboard summoners from Discord when chat commands are enabled or through admin controls.
- Configure leaderboard, matchmaking, and admin channels.
- Refresh or recreate persistent Discord messages.
- Force a leaderboard refresh or daily image regeneration from the admin status panel.
- Link a Discord user to one or more summoners and choose a primary summoner.
- Join, leave, configure, and start matchmaking queues.
- Review recent audit logs and operational health checks.

## Non-Goals

- The bot is not a general Riot API dashboard.
- The bot is not a replacement for Riot account authentication.
- The bot does not bypass Riot API rate limits.
- The bot does not provide a web UI.
- The bot does not make generated image files the source of truth.
- The bot does not guarantee historical analytics beyond the current cached match/state model.

## External Dependencies

- Discord API through `disnake`.
- Riot API with a valid `RIOT_API_KEY`.
- Data Dragon for champion and patch-related assets.
- Local JSON persistence through `data.json`.
- Local image assets in `src/Imgs/` and generated PNG outputs.

## Current Limitations

- Runtime state is file-based JSON, so operators should avoid concurrent manual edits.
- Riot API failures or 429 responses pause refresh behavior through the in-process backoff model.
- Daily image posting depends on the configured daily channel and Discord file-send permissions.
- Matchmaking separate voice channels require Discord permissions to manage channels and move members.
- Documentation is staged in-repository; GitHub Wiki publication is a maintainer action.
