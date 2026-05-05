import json
import os
from datetime import datetime, timezone


AUDIT_LOG_PATH = "logs/audit.jsonl"
MAX_AUDIT_EVENTS = 1000


def system_actor():
    return {"actorId": "system", "actorName": "system"}


def interaction_actor(inter):
    author = getattr(inter, "author", None)
    if author is None:
        return system_actor()
    return {
        "actorId": str(getattr(author, "id", "unknown")),
        "actorName": str(getattr(author, "display_name", getattr(author, "name", "unknown")))
    }


def log_event(event, actor=None, status="info", summary="", details=None):
    actor = actor or system_actor()
    details = details or {}
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actorId": str(actor.get("actorId", "system")),
        "actorName": str(actor.get("actorName", "system")),
        "status": status,
        "summary": summary,
        "details": details
    }

    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        trim_audit_log()
    except OSError as error:
        print(f"Failed to write audit log: {error}")


def read_audit_events(limit=None, status=None, event_contains=None):
    if not os.path.exists(AUDIT_LOG_PATH):
        return []

    events = []
    try:
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as file:
            for line in file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if status is not None and event.get("status") != status:
                    continue
                if event_contains is not None and event_contains.lower() not in event.get("event", "").lower():
                    continue
                events.append(event)
    except OSError as error:
        print(f"Failed to read audit log: {error}")
        return []

    if limit is not None:
        return events[-limit:]
    return events


def recent_error_events(limit=5):
    events = read_audit_events()
    filtered_events = [
        event for event in events
        if event.get("status") == "error" or "riot" in event.get("event", "").lower()
    ]
    return filtered_events[-limit:]


def trim_audit_log(max_events=MAX_AUDIT_EVENTS):
    if not os.path.exists(AUDIT_LOG_PATH):
        return

    try:
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as file:
            lines = file.readlines()
        if len(lines) <= max_events:
            return
        with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as file:
            file.writelines(lines[-max_events:])
    except OSError as error:
        print(f"Failed to trim audit log: {error}")
