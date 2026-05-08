from disnake.ext import commands

bot = commands.InteractionBot()
matchmaking_view_registered = False
TASKS = {}
MAX_SELECT_OPTIONS = 25
MATCHMAKING_TEAM_MODES = ["random", "balanced_rank", "captains"]
MATCHMAKING_TEAM_MODE_LABELS = {
    "random": "Random",
    "balanced_rank": "Balanced rank",
    "captains": "Captains"
}
MATCHMAKING_ODD_PLAYER_POLICIES = ["allow_uneven", "require_even"]
MATCHMAKING_ODD_PLAYER_POLICY_LABELS = {
    "allow_uneven": "Allow uneven teams",
    "require_even": "Require even teams"
}
CAPTAIN_DRAFT_TIMEOUT_SECONDS = 90
EPHEMERAL_DELETE_SECONDS = 120
AUDIT_CATEGORY_LABELS = {
    "admin": "Admin",
    "matchmaking": "Matchmaking",
    "riot": "Riot/API",
    "leaderboard": "Leaderboard",
    "links": "Linked accounts",
    "operations": "Operations",
}
AUDIT_CATEGORY_PREFIXES = {
    "admin": ["admin_", "leaderboard_channel_changed", "matchmaking_channel_changed"],
    "matchmaking": ["matchmaking_"],
    "riot": ["riot_", "leaderboard_update_skipped"],
    "leaderboard": ["leaderboard_", "personal_report_"],
    "links": ["discord_link_"],
    "operations": ["operations_"],
}
