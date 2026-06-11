# Setup and Configuration

## Prerequisites

- Docker and Docker Compose for the recommended setup.
- Python 3.10 or newer for local development.
- PostgreSQL 14 or newer for local development without Docker.
- A Discord bot application and token.
- A Riot API key.
- A Discord server where the bot can be invited.
- Permission to manage the server or configure channels.

## Required Environment Variables

The bot loads `.env` from the repository root first and also supports legacy `src/.env`.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DISCORD_TOKEN` | Yes | None | Token used by `src/main.py` to start the Discord bot. |
| `RIOT_API_KEY` | Yes | None | Riot API key used for account, rank, match, and high-elo requests. |
| `DISCORD_CHANNEL` | Yes for posting | `0` | Fallback channel for daily rank images and patch notes. |
| `REQUESTS` | No | `100` | Request budget used to calculate leaderboard loop interval. |
| `DAILY` | No | `21` | Daily image posting hour in Europe/Madrid time. |
| `DATABASE_URL` | Yes | `postgresql+psycopg://therace:therace@db:5432/therace` in Docker | PostgreSQL connection string used for runtime state. |
| `AUTO_MIGRATE_JSON` | No | `true` | Import legacy `DATA_JSON` into an empty database on startup. |
| `DATA_JSON` | No | `data.json` or `/app/data.json` in Docker | Legacy JSON path used only as a migration source or export fallback. |

## Discord Permissions and Channels

The bot checks channel permissions before setup/configuration where possible. Required baseline permissions are:

- View Channel
- Send Messages
- Embed Links
- Read Message History

Matchmaking voice-channel separation also needs:

- Manage Channels
- Move Members

Recommended channel setup:

- **Admin channel**: created or moved with `/admin_setup`.
- **Leaderboard channel**: configured with `/admin_set_ranking_channel` or the admin settings panel.
- **Matchmaking channel**: configured with `/admin_set_matchmaking_channel` or the admin settings panel.
- **Daily/patch channel**: configured by `DISCORD_CHANNEL`.

## Docker Setup

Copy the environment template and fill in Discord/Riot credentials:

```bash
cp .env.example .env
```

Start the database and bot:

```bash
docker compose up --build
```

`docker-compose.yml` runs two services:

- `db`: PostgreSQL with the `postgres-data` volume.
- `bot`: Python bot container connected through `DATABASE_URL`.

To import a legacy JSON backup during the first Docker startup, mount it explicitly as `/app/data.json` before starting with an empty database.

## Local Dependency Installation

Install Python dependencies from the repository root:

```bash
python3 -m pip install -r requirements.txt
```

Current runtime dependencies include `disnake`, `requests`, `Pillow`, `python-dotenv`, `beautifulsoup4`, `numpy`, `pytz`, `SQLAlchemy`, and `psycopg`.

## Local Startup Command

Run the bot from the repository root after configuring `DATABASE_URL`:

```bash
python3 src/main.py
```

`src/main.py` initializes PostgreSQL storage, runs legacy JSON migration when needed, registers events and commands, checks `DISCORD_TOKEN`, and starts the `InteractionBot`.

## First-Run Validation

1. Confirm the bot logs in.
2. Run `/admin_setup` in Discord and choose an admin channel.
3. From the admin panel, open **Status / Logs** and run **Health check**.
4. Configure leaderboard and matchmaking channels.
5. Use **Test permissions** to confirm the bot has required channel permissions.
6. Add at least one summoner through the admin panel or enable `/add /remove` temporarily in settings.
7. Use **Force leaderboard refresh** only when Riot backoff is inactive.

## Notes for Operators

- PostgreSQL is the runtime source of truth. Do not edit database rows manually while the bot is running unless you have stopped the bot and have a backup.
- If migrating from legacy JSON, keep `DATA_JSON` available for the first startup with an empty database.
- Do not treat generated PNG files as source data.
- If Riot backoff is active, wait for it to expire instead of retrying repeatedly.
- To publish documentation to GitHub Wiki, enable the repository wiki and use `scripts/publish_wiki.sh` or the `Publish GitHub Wiki` workflow.
