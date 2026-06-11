from pathlib import Path

from utils.jsonUtils import openJsonFile, writeToJsonFile


def test_json_utils_fallback_reads_and_writes_file(no_database, tmp_path):
    path = tmp_path / "state.json"
    payload = {"summoners": {"Player#EUW": {"tier": "GOLD"}}}

    writeToJsonFile(str(path), payload)

    assert openJsonFile(str(path)) == payload


def test_audit_utils_fallback_writes_jsonl(no_database, tmp_path, monkeypatch):
    import utils.auditUtils as audit_utils

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_utils, "AUDIT_LOG_PATH", str(log_path))

    audit_utils.log_event("test_event", status="success", summary="ok", details={"value": 1})

    events = audit_utils.read_audit_events()
    assert len(events) == 1
    assert events[0]["event"] == "test_event"
    assert events[0]["status"] == "success"
    assert events[0]["details"] == {"value": 1}
