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
