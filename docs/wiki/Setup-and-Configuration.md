# Setup and Configuration

## Prerequisites

- Python 3.10 or newer.
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
| `DATA_JSON` | No | `data.json` at repository root | Override path for runtime state. |

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

## Dependency Installation

Install Python dependencies from the repository root:

```bash
python3 -m pip install -r requirements.txt
```

Current runtime dependencies include `disnake`, `requests`, `Pillow`, `python-dotenv`, `beautifulsoup4`, `numpy`, and `pytz`.

## Startup Command

Run the bot from the repository root:

```bash
python3 src/main.py
```

`src/main.py` registers events and commands, checks `DISCORD_TOKEN`, and starts the `InteractionBot`.

## First-Run Validation

1. Confirm the bot logs in.
2. Run `/admin_setup` in Discord and choose an admin channel.
3. From the admin panel, open **Status / Logs** and run **Health check**.
4. Configure leaderboard and matchmaking channels.
5. Use **Test permissions** to confirm the bot has required channel permissions.
6. Add at least one summoner through the admin panel or enable `/add /remove` temporarily in settings.
7. Use **Force leaderboard refresh** only when Riot backoff is inactive.

## Notes for Operators

- Do not edit `data.json` while the bot is running unless you have stopped the bot and have a backup.
- Do not treat generated PNG files as source data.
- If Riot backoff is active, wait for it to expire instead of retrying repeatedly.
- To publish documentation to GitHub Wiki, enable the repository wiki and use `scripts/publish_wiki.sh` or the `Publish GitHub Wiki` workflow.
