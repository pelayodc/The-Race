# Code Map

## Entrypoint and Runtime

- `src/main.py`: registers events and commands, validates `DISCORD_TOKEN`, and starts the bot.
- `src/bot_runtime.py`: global bot instance, background task registry, matchmaking constants, audit category labels, and interaction timing constants.

Maintenance notes:

- Runtime constants are shared across multiple modules. Check downstream UI labels and state helpers before changing them.

## Discord Commands and Events

- `src/bot_commands.py`: slash commands for list, personal reports, patch notes, admin setup, channel configuration, linked accounts, and public add/remove.
- `src/bot_events.py`: `on_ready`, voice state updates, scheduled leaderboard loop, scheduled patch loop, and captain draft timeout loop.
- `src/discord_helpers.py`: channel/message fetch helpers, ephemeral responses, temporary messages, permission checks, and admin interaction guard.

Maintenance notes:

- Admin commands and admin panel actions should use the same permission model.
- Public posts should be intentional; operational feedback should generally be ephemeral.

## Admin Panel

- `src/admin_panel.py`: admin embeds and views for settings, leaderboard users, linked accounts, matchmaking, status/logs, health checks, backups, force refreshes, and audit views.
- `src/persistent_messages.py`: creates, edits, moves, refreshes, and recreates admin, leaderboard, and matchmaking persistent messages.

Maintenance notes:

- New admin actions should call `require_admin_interaction()`, log an audit event, update runtime status when applicable, and refresh persistent messages affected by the change.

## Leaderboard and Daily Image Flow

- `src/leaderboard.py`: leaderboard embeds, daily image send, runtime status fields, cached leaderboard reconstruction, forced refresh, forced daily image, add/remove summoners.
- `src/utils/dataUtils.py`: Riot API calls, backoff, rank refresh, match cache refresh, high-elo cache, MVP score inputs, patch note checks.
- `src/utils/drawUtils.py`: Pillow rendering for leaderboard images, daily rank image, champion chips, LP change badges, MVP badges, and gold graph images.
- `src/utils/commonUtils.py`: environment values, Riot/Data Dragon version, rank scoring, rank icon paths, and `Summoner` model.

Maintenance notes:

- Do not bypass `riot_get()` for Riot requests that participate in refresh loops.
- Generated images are outputs. The source of truth is fetched/cached leaderboard data.
- Image layout changes should be visually checked with generated sample output.

## Matchmaking

- `src/matchmaking.py`: queue state, team balancing, captain draft, role preferences, voice channel movement, matchmaking embeds, public and admin views, and draft timeout processing.
- `src/state.py`: matchmaking defaults and effective settings.

Maintenance notes:

- Separate team channels require Manage Channels and Move Members.
- Balanced rank depends on linked/cached summoner score data when available.
- Captain draft state must be cleared on completion, cancellation, timeout, or player removal.

## Linked Accounts

- `src/linked_accounts.py`: Discord-to-summoner link state, pending requests, primary summoner selection, admin modals/selects, and admin embed.
- `src/bot_commands.py`: user and admin slash commands for linking/unlinking.

Maintenance notes:

- Link state is mirrored between `discordLinks` and fields on each summoner record. Use existing rebuild helpers after summoner removal or state migration.

## Personal Reports

- `src/personal_report.py`: cached rank report, recent game summary, match detail views, team comparison, gold graph generation, and report navigation.
- `src/utils/drawUtils.py`: gold graph image rendering used by report views.

Maintenance notes:

- Personal reports are cache-only. They rely on leaderboard refresh data and linked primary summoners.

## Persistence, State, and Audit

- `src/state.py`: channel ID resolution, admin/matchmaking state defaults, effective settings, labels, and role normalization.
- `src/utils/jsonUtils.py`: JSON file read/write helpers.
- `src/utils/auditUtils.py`: audit actors, JSONL event logging, recent error lookup, and log trimming.
- `data.json`: runtime state file, not source code.

Maintenance notes:

- Add new durable keys through state initialization helpers where practical.
- Audit operational actions and external-service failures with enough detail for the status panel.

## Image and Asset Pipeline

- `src/Imgs/`: source image assets, including rank icons, champion icons, MVP/crown/fire/skull images, and patch highlight cache.
- `src/ARIAL.TTF`: font used for generated images.
- `src/utils/drawUtils.py`: image rendering helpers and final `generateImage()` output.
- `Rank list.png` and `Daily Rank list.png`: generated root-level outputs.

Maintenance notes:

- Champion icons may be fetched from Data Dragon and cached locally.
- Keep text centered and bounded when changing image layouts.

## Localization

- `src/i18n.py`: translation loading, language selection, lookup fallback, and locale key validation.
- `src/locales/en.json`
- `src/locales/es.json`
- `src/locales/fr.json`
- `src/locales/it.json`
- `src/locales/pt.json`

Maintenance notes:

- Add every new locale-backed string to all locale files.
- Run `validate_locale_keys()` after locale changes.

## Required Path Checklist

- `src/main.py`
- `src/bot_runtime.py`
- `src/bot_events.py`
- `src/bot_commands.py`
- `src/admin_panel.py`
- `src/leaderboard.py`
- `src/matchmaking.py`
- `src/linked_accounts.py`
- `src/personal_report.py`
- `src/persistent_messages.py`
- `src/state.py`
- `src/i18n.py`
- `src/locales/`
- `src/utils/auditUtils.py`
- `src/utils/commonUtils.py`
- `src/utils/dataUtils.py`
- `src/utils/drawUtils.py`
- `src/utils/jsonUtils.py`
- `src/Imgs/`
