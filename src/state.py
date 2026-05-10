from datetime import datetime, timezone

from bot_runtime import MATCHMAKING_ODD_PLAYER_POLICIES, MATCHMAKING_ODD_PLAYER_POLICY_LABELS, MATCHMAKING_ROLE_LABELS, MATCHMAKING_ROLE_MODE_LABELS, MATCHMAKING_ROLE_MODES, MATCHMAKING_ROLE_SOURCE_LABELS, MATCHMAKING_ROLE_SOURCES, MATCHMAKING_ROLES, MATCHMAKING_TEAM_MODES, MATCHMAKING_TEAM_MODE_LABELS
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
    if json_data.get("matchmakingRoleSource") not in MATCHMAKING_ROLE_SOURCES:
        json_data["matchmakingRoleSource"] = "history"
    if json_data.get("matchmakingRoleMode") not in MATCHMAKING_ROLE_MODES:
        json_data["matchmakingRoleMode"] = "off"
    role_preferences = json_data.get("discordRolePreferences")
    json_data["discordRolePreferences"] = role_preferences if isinstance(role_preferences, dict) else {}
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
    json_data.setdefault("leaderboardLastDailyImageAt", None)
    json_data.setdefault("leaderboardLastDailyImageStatus", None)
    json_data.setdefault("leaderboardLastDailyImageMessageId", None)
    json_data.setdefault("leaderboardLastDailyImageChannelId", None)
    json_data.setdefault("leaderboardLastDailyImageError", None)
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

def role_label(role, json_data=None):
    labels = {
        "top": "state.role_top",
        "jungle": "state.role_jungle",
        "mid": "state.role_mid",
        "adc": "state.role_adc",
        "support": "state.role_support",
        "fill": "state.role_fill",
    }
    key = labels.get(role)
    if key:
        return t(json_data or {}, key)
    return MATCHMAKING_ROLE_LABELS.get(role, "Fill")

def role_source_label(source, json_data=None):
    labels = {
        "history": "state.role_source_history",
        "player": "state.role_source_player",
        "admin": "state.role_source_admin",
    }
    key = labels.get(source)
    if key:
        return t(json_data or {}, key)
    return MATCHMAKING_ROLE_SOURCE_LABELS.get(source, "Cached history")

def role_mode_label(mode, json_data=None):
    labels = {
        "off": "state.role_mode_off",
        "preferred": "state.role_mode_preferred",
        "inverse": "state.role_mode_inverse",
    }
    key = labels.get(mode)
    if key:
        return t(json_data or {}, key)
    return MATCHMAKING_ROLE_MODE_LABELS.get(mode, "Off")

def effective_matchmaking_role_source(json_data):
    source = json_data.get("matchmakingRoleSource")
    return source if source in MATCHMAKING_ROLE_SOURCES else "history"

def effective_matchmaking_role_mode(json_data):
    mode = json_data.get("matchmakingRoleMode")
    return mode if mode in MATCHMAKING_ROLE_MODES else "off"

def normalize_matchmaking_role(role):
    role = str(role or "").lower().strip()
    aliases = {
        "utility": "support",
        "support": "support",
        "bottom": "adc",
        "bot": "adc",
        "adc": "adc",
        "middle": "mid",
        "mid": "mid",
        "jungle": "jungle",
        "top": "top",
    }
    return aliases.get(role) if role in aliases else role if role in MATCHMAKING_ROLES else None
