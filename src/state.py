from datetime import datetime, timezone

from bot_runtime import MATCHMAKING_ODD_PLAYER_POLICIES, MATCHMAKING_ODD_PLAYER_POLICY_LABELS, MATCHMAKING_TEAM_MODES, MATCHMAKING_TEAM_MODE_LABELS
from i18n import DEFAULT_LANGUAGE, supported_language, t
from utils.commonUtils import discordChannel, jsonFile
from utils.jsonUtils import openJsonFile


def load_json_data():
    return openJsonFile(jsonFile) or {}

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def configured_channel_id(json_data, key):
    try:
        return int(json_data.get(key) or discordChannel)
    except (TypeError, ValueError):
        return discordChannel

def leaderboard_channel_id(json_data):
    return configured_channel_id(json_data, "leaderboardChannelId")

def matchmaking_channel_id(json_data):
    return configured_channel_id(json_data, "matchmakingChannelId")

def admin_channel_id(json_data):
    return configured_channel_id(json_data, "adminChannelId")

def can_configure_channels(inter):
    permissions = getattr(inter.author, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)

def missing_bot_channel_permissions(channel, bot_member):
    if bot_member is None:
        return ["bot member lookup"]

    permissions = channel.permissions_for(bot_member)
    required_permissions = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History"
    }
    missing = []
    for permission, label in required_permissions.items():
        if not getattr(permissions, permission, False):
            missing.append(label)
    return missing

def ensure_matchmaking_state(json_data):
    json_data.setdefault("matchmakingQueue", [])
    json_data.setdefault("matchmakingSeparateChannels", False)
    json_data.setdefault("matchmakingSeparateChannelsForced", None)
    json_data.setdefault("matchmakingTeamChannelIds", [])
    json_data.setdefault("matchmakingInProgress", False)
    if json_data.get("matchmakingOddPlayersPolicy") not in MATCHMAKING_ODD_PLAYER_POLICIES:
        json_data["matchmakingOddPlayersPolicy"] = "allow_uneven"
    if json_data.get("matchmakingTeamMode") not in MATCHMAKING_TEAM_MODES:
        json_data["matchmakingTeamMode"] = "random"
    if json_data.get("matchmakingTeamModeForced") not in MATCHMAKING_TEAM_MODES:
        json_data["matchmakingTeamModeForced"] = None
    json_data.setdefault("matchmakingDraft", None)
    return json_data

def ensure_admin_state(json_data):
    ensure_matchmaking_state(json_data)
    if not supported_language(json_data.get("botLanguage")):
        json_data["botLanguage"] = DEFAULT_LANGUAGE
    json_data.setdefault("discordLinks", {})
    json_data.setdefault("discordLinkRequests", [])
    json_data.setdefault("leaderboardChatCommandsEnabled", False)
    json_data.setdefault("adminMessageId", None)
    json_data.setdefault("leaderboardLastUpdateAt", None)
    json_data.setdefault("leaderboardLastUpdateMode", None)
    json_data.setdefault("leaderboardLastUpdateStatus", None)
    json_data.setdefault("leaderboardLastEstimatedApiCalls", 0)
    json_data.setdefault("lastRiotError", None)
    return json_data

def leaderboard_chat_commands_enabled(json_data):
    return bool(json_data.get("leaderboardChatCommandsEnabled", False))

def effective_matchmaking_separate_channels(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is not None:
        return forced_mode
    return json_data.get("matchmakingSeparateChannels", False)

def forced_mode_text(json_data):
    forced_mode = json_data.get("matchmakingSeparateChannelsForced")
    if forced_mode is True:
        return t(json_data, "state.forced_separate_channels")
    if forced_mode is False:
        return t(json_data, "state.forced_same_channel")
    return t(json_data, "state.unlocked")

def effective_matchmaking_team_mode(json_data):
    forced_mode = json_data.get("matchmakingTeamModeForced")
    if forced_mode in MATCHMAKING_TEAM_MODES:
        return forced_mode
    mode = json_data.get("matchmakingTeamMode")
    return mode if mode in MATCHMAKING_TEAM_MODES else "random"

def team_mode_label(mode, json_data=None):
    labels = {
        "random": "state.team_random",
        "balanced_rank": "state.team_balanced_rank",
        "captains": "state.team_captains",
    }
    key = labels.get(mode)
    if key:
        return t(json_data or {}, key)
    return MATCHMAKING_TEAM_MODE_LABELS.get(mode, "Random")

def team_mode_lock_text(json_data):
    forced_mode = json_data.get("matchmakingTeamModeForced")
    if forced_mode in MATCHMAKING_TEAM_MODES:
        return t(json_data, "state.force_mode", mode=team_mode_label(forced_mode, json_data))
    return t(json_data, "state.unlocked")

def odd_players_policy_label(policy, json_data=None):
    labels = {
        "allow_uneven": "state.odd_allow_uneven",
        "require_even": "state.odd_require_even",
    }
    key = labels.get(policy)
    if key:
        return t(json_data or {}, key)
    return MATCHMAKING_ODD_PLAYER_POLICY_LABELS.get(policy, "Allow uneven teams")

def effective_odd_players_policy(json_data):
    policy = json_data.get("matchmakingOddPlayersPolicy")
    return policy if policy in MATCHMAKING_ODD_PLAYER_POLICIES else "allow_uneven"

def voice_mode_label(separate_channels, json_data=None):
    return t(json_data or {}, "state.voice_separate" if separate_channels else "state.voice_same")
