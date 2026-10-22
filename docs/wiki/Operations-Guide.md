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

- **App settings**: channels, language, and explicit enable/disable actions for `/add /remove`.
- **Leaderboard users**: select a platform before entering a Riot ID; the routing region is automatic. Removing a summoner requires confirmation.
- **Linked accounts**: select a Discord member, then an existing summoner to link, unlink, or set as primary. The first linked account becomes primary; additional links remain secondary until explicitly changed. Unlinking requires confirmation.
- **Link requests**: select a request to inspect its user and summoner, then approve or reject it.
- **Matchmaking**: configure modes, role behavior, locks, and force start.
- **Status**: background task status, Riot backoff, health checks, and permissions.
- **Logs**: category filters, actor search, and the last 24 hours summary.
- **Maintenance**: persistent message recreation, backup download, leaderboard refresh, and daily image generation.

The public admin buttons open a private session owned by the requesting administrator. Use the section selector and Back button to navigate in the same message. Sessions expire 120 seconds after opening; navigation does not extend the deadline. Permissions are checked again for each action. Lists paginate in groups of up to 25 and are refreshed before applying changes.

## Persistent Message Management

Persistent messages are managed in `src/persistent_messages.py`.

- Admin message ID is stored in `adminMessageId`.
- Matchmaking message ID is stored in `matchmakingMessageId`.
- Leaderboard message ID is stored in `leaderboardMessageId`.

Use **Maintenance -> Recreate messages** when messages are missing, stale, or moved. The recreate flow refreshes admin and matchmaking messages and recreates the leaderboard embed from cached rank data when available.

## Force Refresh Controls

The Maintenance section exposes:

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

Public and administrative settings are grouped into Teams, Channels, and Roles. Public controls show current values and disable options locked by administration. Administrators assign roles by selecting a member and a role. Queue removal remains available from the administrative Matchmaking section.

Separate voice channels require Discord permissions to manage channels and move members. Empty generated team channels are cleaned up after voice state updates.

## Linked Account Operations

Linked accounts are implemented in `src/linked_accounts.py`.

Users can request a link with `/link_discord`. Admins can:

- Link a summoner to a Discord user.
- Unlink a summoner.
- Set the primary summoner.
- Approve or reject pending requests.

The link state is mirrored into summoner records and the `discordLinks` map stored in PostgreSQL runtime state.

## SoloQ Subscriptions

Open Subscriptions below the leaderboard. Available players lists the Top 25 eligible primary accounts. Selecting a player shows their current server display name, summoner, and subscription state; use Subscribe or Cancel subscription to apply the change. Subscribing requires a successful confirmation DM.

My subscriptions also includes previously subscribed players outside the current eligible list, so those subscriptions can still be cancelled. The private menu lasts 120 seconds and does not change the monitoring scope. Selecting a player alone never changes a subscription.

## Data Backup

The **Maintenance -> Download backup** action attaches the PostgreSQL runtime state as JSON and audit events as JSONL to the private menu. Download the files before navigating away or the session expires. Use this before manual data recovery or migration work.

For command-line backups, run:

```bash
python3 scripts/export_db_backup.py > data-backup.json
```

Legacy `data.json` is only used as an import source when migrating an empty database.

## Publishing the GitHub Wiki

The repository keeps wiki source pages in `docs/wiki/`.

Manual publication:

```bash
scripts/publish_wiki.sh
```

Automatic publication:

- The `Publish GitHub Wiki` GitHub Actions workflow runs when `docs/wiki/**`, `scripts/publish_wiki.sh`, or the workflow file changes on `main` or `master`.
- The workflow uses `GITHUB_TOKEN` to push to `<repository>.wiki.git`.
- The GitHub Wiki must be enabled and initialized at least once in repository settings.

If the wiki remote cannot be cloned, confirm that the wiki is enabled and that the repository token has permission to write contents.
