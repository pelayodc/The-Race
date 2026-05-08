from bot_commands import register_commands
from bot_events import register_events
from bot_runtime import bot
from utils.commonUtils import discordToken


def main():
    register_events(bot)
    register_commands(bot)

    if not discordToken:
        raise RuntimeError("DISCORD_TOKEN is not configured.")

    bot.run(discordToken)


if __name__ == "__main__":
    main()
