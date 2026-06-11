from bot_commands import register_commands
from bot_events import register_events
from bot_runtime import bot
from storage import initialize_storage
from utils.commonUtils import discordToken, jsonFile


def main():
    initialize_storage(jsonFile)
    register_events(bot)
    register_commands(bot)

    if not discordToken:
        raise RuntimeError("DISCORD_TOKEN is not configured.")

    bot.run(discordToken)


if __name__ == "__main__":
    main()
