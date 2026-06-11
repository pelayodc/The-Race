import json

import pytest


@pytest.mark.postgres
def test_storage_save_load_and_export_roundtrip(postgres_storage):
    state = {
        "summoners": {"Player#EUW": {"tier": "GOLD", "rank": "I"}},
        "matchData": {"EUW1_1": {"info": {"gameId": 1}}},
        "matchTimelineData": {"EUW1_1": {"metadata": {"matchId": "EUW1_1"}}},
        "discordLinks": {"10": {"primarySummoner": "Player#EUW"}},
        "soloQueueSubscriptions": {"99": ["10"]},
    }

    postgres_storage.save_state(state)

    assert postgres_storage.load_state() == state
    assert postgres_storage.export_state() == state


@pytest.mark.postgres
def test_storage_migrates_legacy_json_only_when_database_is_empty(postgres_storage, tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.json"
    legacy_state = {
        "summoners": {"Legacy#EUW": {"tier": "SILVER"}},
        "matchData": {},
        "matchTimelineData": {},
        "discordLinks": {"1": {"primarySummoner": "Legacy#EUW"}},
    }
    legacy_path.write_text(json.dumps(legacy_state), encoding="utf-8")

    monkeypatch.setenv("AUTO_MIGRATE_JSON", "true")
    assert postgres_storage.migrate_json_if_needed(str(legacy_path)) is True
    assert postgres_storage.load_state()["summoners"] == legacy_state["summoners"]

    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_text(
        json.dumps({"summoners": {"Replacement#EUW": {"tier": "CHALLENGER"}}}),
        encoding="utf-8",
    )

    assert postgres_storage.migrate_json_if_needed(str(replacement_path)) is False
    assert "Replacement#EUW" not in postgres_storage.load_state()["summoners"]


@pytest.mark.postgres
def test_storage_migration_ignores_runtime_state_when_primary_tables_are_empty(postgres_storage, tmp_path, monkeypatch):
    postgres_storage.save_state({"runtime": 0, "leaderboardChatCommandsEnabled": False})
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps({"summoners": {"Legacy#EUW": {"tier": "GOLD"}}, "runtime": 123}),
        encoding="utf-8",
    )

    monkeypatch.setenv("AUTO_MIGRATE_JSON", "true")

    assert postgres_storage.migrate_json_if_needed(str(legacy_path)) is True
    state = postgres_storage.load_state()
    assert state["summoners"] == {"Legacy#EUW": {"tier": "GOLD"}}
    assert state["runtime"] == 123


@pytest.mark.postgres
def test_storage_audit_events_filter_and_limit(postgres_storage):
    postgres_storage.log_audit_event({
        "event": "leaderboard_update",
        "status": "success",
        "summary": "ok",
        "actorId": "system",
        "actorName": "system",
        "details": {"count": 1},
    })
    postgres_storage.log_audit_event({
        "event": "riot_api_error",
        "status": "error",
        "summary": "failed",
        "actorId": "system",
        "actorName": "system",
        "details": {"statusCode": 504},
    })

    errors = postgres_storage.read_audit_events_from_db(status="error")
    assert len(errors) == 1
    assert errors[0]["event"] == "riot_api_error"
    assert errors[0]["details"] == {"statusCode": 504}

    latest = postgres_storage.read_audit_events_from_db(limit=1)
    assert len(latest) == 1
    assert latest[0]["event"] == "riot_api_error"
