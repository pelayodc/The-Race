# The Race Wiki

The Race is a League of Legends Discord bot for running ranked leaderboard and server coordination workflows from Discord. It combines Riot API data, Discord slash commands, persistent admin/matchmaking messages, JSON runtime state, audit logs, and generated rank images.

## Primary Audiences

- **Operators** configure the bot, check health, manage channels, and diagnose failed updates.
- **Maintainers** review scope, publish documentation, and decide whether proposed features fit the project.
- **Developers** use the code map to find the modules involved in leaderboard, matchmaking, linked accounts, image generation, and state changes.
- **Contributors** use the contribution guidance to avoid regressions in Discord UX, Riot API usage, state files, and generated artifacts.

## Documentation Navigation

- [[Project Scope|Project-Scope]]: supported features, workflows, non-goals, dependencies, and limitations.
- [[Setup and Configuration|Setup-and-Configuration]]: prerequisites, `.env`, Discord permissions, startup, and first-run checks.
- [[Operations Guide|Operations-Guide]]: scheduled jobs, admin panel actions, persistent messages, force refreshes, matchmaking, and linked accounts.
- [[Code Map|Code-Map]]: source modules and maintenance notes by feature area.
- [[State and Artifacts|State-and-Artifacts]]: durable state, caches, generated files, source assets, and files operators should not edit manually.
- [[Troubleshooting]]: common Riot, Discord, image, persistent message, linked account, and matchmaking failures.
- [[Contributing to The Race|Contributing-to-The-Race]]: code-area selection, constitution expectations, localization, state safety, and verification.

## Quick Links

- Start here for installation: [[Setup and Configuration|Setup-and-Configuration]]
- Start here for a failed daily image: [[Troubleshooting]]
- Start here for a code change: [[Code Map|Code-Map]]
- Start here for project boundaries: [[Project Scope|Project-Scope]]

## Publication Note

These pages are staged in `docs/wiki/` so they can be reviewed with code changes. Publish them manually with `scripts/publish_wiki.sh` or let the `Publish GitHub Wiki` GitHub Actions workflow sync them after changes land on `main` or `master`.
