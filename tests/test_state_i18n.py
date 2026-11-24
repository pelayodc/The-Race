from i18n import DEFAULT_LANGUAGE, supported_language, validate_locale_keys
from state import ensure_admin_state, effective_matchmaking_team_mode, effective_odd_players_policy


def test_ensure_admin_state_sets_defaults(no_database):
    state = ensure_admin_state({})

    assert state["botLanguage"] == DEFAULT_LANGUAGE
    assert state["summoners"] == {}
    assert state["matchData"] == {}
    assert state["matchTimelineData"] == {}
    assert state["runtime"] == 0
    assert state["discordLinks"] == {}
    assert state["leaderboardChatCommandsEnabled"] is False
    assert state["soloQueueStatus"] == {}
    assert state["soloQueueSubscriptions"] == {}
    assert effective_matchmaking_team_mode(state) == "random"
    assert effective_odd_players_policy(state) == "allow_uneven"


def test_locale_files_have_matching_keys(no_database):
    assert supported_language("en") is True
    assert supported_language("es") is True
    assert validate_locale_keys() == {}


def test_format_patch_notes_message_is_localized(no_database):
    from discord_helpers import format_patch_notes_message

    es = {"botLanguage": "es"}
    en = {"botLanguage": "en"}

    msg_tomorrow = format_patch_notes_message(es, "26.10", -1, 15, "https://example.com/patch")
    assert msg_tomorrow == "Parche 26.10\nmañana\n\nhttps://example.com/patch"

    msg_today = format_patch_notes_message(es, "26.10", 0, 14, "https://example.com/patch")
    assert msg_today == "Parche 26.10\nhoy\n\nhttps://example.com/patch"

    msg_past = format_patch_notes_message(es, "26.10", 3, 11, "https://example.com/patch")
    assert msg_past == "Parche 26.10\nhace 3 días\npróximo parche en: 11 días\nhttps://example.com/patch"

    msg_en = format_patch_notes_message(en, "26.10", -1, 15, "https://example.com/patch")
    assert msg_en == "Patch 26.10\ntomorrow\n\nhttps://example.com/patch"
