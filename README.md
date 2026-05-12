![The Race banner](img/banner.png)

# The Race

The Race is a League of Legends Discord bot for running a Solo/Duo leaderboard and related server workflows. It tracks ranked progress, daily LP movement, recent games, MVP/crown status, patch notes, linked Discord accounts, personal reports, and matchmaking queues from Discord.

## Features

- Live Solo/Duo leaderboard with LP, rank, position, and recent-game context.
- Daily ranking image generation and posting.
- MVP and crown tracking based on recent match performance.
- Patch note checks and manual patch lookup.
- Discord account linking for personal `/me` reports.
- Matchmaking queue with random, balanced-rank, and captain modes.
- Admin panel for configuration, status, logs, backups, force refreshes, and persistent messages.
- Multi-language Discord UI strings through `src/locales/`.

## Documentation

Detailed documentation is staged in [`docs/wiki/`](docs/wiki/) for GitHub Wiki publication:

- [Wiki Home](docs/wiki/Home.md)
- [Project Scope](docs/wiki/Project-Scope.md)
- [Setup and Configuration](docs/wiki/Setup-and-Configuration.md)
- [Operations Guide](docs/wiki/Operations-Guide.md)
- [Code Map](docs/wiki/Code-Map.md)
- [State and Artifacts](docs/wiki/State-and-Artifacts.md)
- [Troubleshooting](docs/wiki/Troubleshooting.md)
- [Contributing to The Race](docs/wiki/Contributing-to-The-Race.md)

## Quick Start

### Prerequisites

- Python 3.10+
- Discord bot token
- Riot API key
- A Discord server where the bot can send messages and embeds

### Setup

1. Install dependencies:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Create a `.env` file in the repository root:

   ```env
   DISCORD_TOKEN=your-discord-token
   RIOT_API_KEY=your-riot-api-key
   DISCORD_CHANNEL=123456789012345678
   REQUESTS=100
   DAILY=21
   ```

3. Start the bot:

   ```bash
   python3 src/main.py
   ```

4. In Discord, use `/admin_setup` to create the administration message, then configure the leaderboard and matchmaking channels from the admin panel.

For full setup details, permissions, channel configuration, and first-run checks, see [Setup and Configuration](docs/wiki/Setup-and-Configuration.md).

## Screenshots

Ranking image:

![Ranking image](img/Rank_list1.png)

Daily ranking image:

![Daily ranking image](img/Daily_Rank_list1.png)

Command example:

![Command example](img/Screenshot_1.png)

Patch note example:

![Patch note example](img/Screenshot_4.png)

## Repository Layout

- `src/`: bot runtime, Discord commands/events, admin panel, leaderboard, matchmaking, linked accounts, personal reports, state, localization, and utilities.
- `src/Imgs/`: rank icons, champion icons, and generated/downloaded visual assets used by image rendering.
- `docs/wiki/`: GitHub Wiki-ready documentation.
- `specs/`: Spec Kit feature specifications, plans, and tasks.
- `data.json`: local runtime state; do not commit real production data.

See [Code Map](docs/wiki/Code-Map.md) and [State and Artifacts](docs/wiki/State-and-Artifacts.md) for details.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [Contributing to The Race](docs/wiki/Contributing-to-The-Race.md) before changing code. Changes should preserve Discord UX, Riot API discipline, state safety, localization, and focused verification.

## License

The Race is licensed under the [CC BY-NC-SA 4.0 License](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en).

![Footer](img/footer.png)
