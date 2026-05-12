# Contributing to The Race

## Start With the Existing Guidelines

Read `CONTRIBUTING.md` before opening a pull request. Contributions should avoid dramatic changes to core behavior, preserve existing functionality, follow the local style, and solve a real issue or feature request.

## Choose the Right Code Area

Use [[Code Map|Code-Map]] before editing:

- Leaderboard, daily image, summoner add/remove: `src/leaderboard.py`, `src/utils/dataUtils.py`, `src/utils/drawUtils.py`.
- Admin panel and operations: `src/admin_panel.py`, `src/persistent_messages.py`, `src/discord_helpers.py`.
- Matchmaking: `src/matchmaking.py`, `src/state.py`.
- Linked accounts: `src/linked_accounts.py`, `src/bot_commands.py`.
- Personal reports: `src/personal_report.py`.
- Translations: `src/i18n.py`, `src/locales/`.

## Constitution-Driven Expectations

The project constitution in `.specify/memory/constitution.md` defines the working rules:

- Preserve Riot/Discord reliability and respect backoff.
- Keep Discord UX consistent and use established admin permission checks.
- Protect `data.json` state shape and generated artifact boundaries.
- Verify changes according to risk.
- Prefer small, local changes over broad rewrites.

## Localization Expectations

If user-facing or admin-facing text uses locale files, update every file in `src/locales/`:

- `en.json`
- `es.json`
- `fr.json`
- `it.json`
- `pt.json`

After locale edits, run:

```bash
PYTHONPATH=src python3 -c "from i18n import validate_locale_keys; print(validate_locale_keys())"
```

The expected result is `{}`.

## State and Generated Artifact Safety

- Do not commit real production `data.json` content.
- Add durable state defaults through `src/state.py` helpers when practical.
- Treat `Rank list.png` and `Daily Rank list.png` as generated outputs.
- Stop the bot and take a backup before manual state recovery.
- Keep audit logs useful when adding operational behavior.

## Verification Expectations

Use verification that matches the change:

- Python syntax/import-only change: `python3 -m py_compile` for touched modules.
- Locale change: `validate_locale_keys()`.
- Leaderboard/Riot behavior: verify backoff, status fields, audit logs, and no unnecessary Riot calls.
- Discord admin control: verify permission guard, ephemeral feedback, audit log, and persistent message refresh.
- Image rendering: generate and visually inspect a sample image.
- Documentation change: verify links, source paths, README length, and required sections.

## Relationship to `CONTRIBUTING.md`

`CONTRIBUTING.md` remains the general contribution policy. This page adds The Race-specific maintenance expectations for Discord workflows, Riot API behavior, JSON state, image generation, localization, and documentation.
