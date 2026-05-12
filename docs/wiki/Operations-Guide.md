# Operations Guide

## Scheduled Leaderboard and Daily Image Updates

`src/bot_events.py` registers `updateRaceImage`, which runs as a background loop. The loop:

- Estimates Riot API calls from configured summoners and high-elo cache age.
- Dynamically adjusts its interval using `REQUESTS`.
- Skips work while Riot backoff is active.
- Runs a daily update after the configured `DAILY` hour in Europe/Madrid time.
- Generates `Daily Rank list.png` for daily updates and sends it to `DISCORD_CHANNEL`.
- Performs normal leaderboard refreshes and edits or creates the leaderboard embed in the configured leaderboard channel.

Runtime status is stored through `set_leaderboard_runtime_status()` and `set_daily_image_status()` in `src/leaderboard.py`.

## Patch Notes Checks

`updatePatchNotes` runs every 120 minutes and uses `checkForNewPatchNotes()` in `src/utils/dataUtils.py`. When a new patch is detected, it posts the patch message and optional image to `DISCORD_CHANNEL`.

The `/patch` command runs the same check manually with force behavior.

## Admin Panel Overview

The persistent admin message is created with `/admin_setup` and rendered by `src/admin_panel.py`. Admin actions require `Manage Server` through `require_admin_interaction()` in `src/discord_helpers.py`.

Main admin areas:

- **App settings**: channels, language, and `/add /remove` toggle.
- **Leaderboard users**: add or remove summoners.
- **Linked accounts**: link, unlink, set primary, approve, or reject requests.
- **Matchmaking**: configure modes, role behavior, locks, and force start.
- **Status / Logs**: health check, permission test, persistent message recreation, data backup, force refreshes, recent logs, and audit summaries.

Most operational feedback is ephemeral and automatically cleaned up after the configured ephemeral timeout.

## Persistent Message Management

Persistent messages are managed in `src/persistent_messages.py`.

- Admin message ID is stored in `adminMessageId`.
- Matchmaking message ID is stored in `matchmakingMessageId`.
- Leaderboard message ID is stored in `leaderboardMessageId`.

Use **Status / Logs -> Recreate messages** when messages are missing, stale, or moved. The recreate flow refreshes admin and matchmaking messages and recreates the leaderboard embed from cached rank data when available.

## Force Refresh Controls

The status panel exposes:

- **Force leaderboard refresh**: calls `force_leaderboard_refresh()` and updates the leaderboard embed.
- **Force daily image**: calls `force_daily_rank_image()`, regenerates `Daily Rank list.png`, sends it to `DISCORD_CHANNEL`, and records daily image status.

Both controls respect Riot backoff. If backoff is active, the operator receives a localized message with the retry time.

## Matchmaking Operations

Matchmaking behavior is implemented in `src/matchmaking.py`.

Supported team modes:

- Random
- Balanced rank
- Captains

Additional controls:

- Same-channel or separate voice channels.
- Odd-player policy.
- Role source: cached history, player selection, or admin selection.
- Role mode: off, preferred roles, or inverse roles.
- Captain draft timeout handling.

Separate voice channels require Discord permissions to manage channels and move members. Empty generated team channels are cleaned up after voice state updates.

## Linked Account Operations

Linked accounts are implemented in `src/linked_accounts.py`.

Users can request a link with `/link_discord`. Admins can:

- Link a summoner to a Discord user.
- Unlink a summoner.
- Set the primary summoner.
- Approve or reject pending requests.

The link state is mirrored into summoner records and the `discordLinks` map inside `data.json`.

## Data Backup

The **Download data backup** admin action sends a timestamped copy of `data.json` and includes the audit log when present. Use this before manual data recovery or migration work.
