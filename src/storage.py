import json
import os
from datetime import datetime, timezone


RUNTIME_EXCLUDED_KEYS = {"summoners", "matchData", "matchTimelineData"}

_engine = None
_engine_url = None
_metadata = None
_tables = None
_initialized = False


def database_enabled():
    return bool(database_url())


def database_url():
    return os.getenv("DATABASE_URL")


def auto_migrate_json():
    return os.getenv("AUTO_MIGRATE_JSON", "true").lower() not in ["0", "false", "no"]


def _sqlalchemy():
    try:
        import sqlalchemy as sa
        from sqlalchemy.dialects.postgresql import JSONB
    except ImportError as error:
        raise RuntimeError("DATABASE_URL is configured but SQLAlchemy/psycopg dependencies are not installed.") from error
    return sa, JSONB


def _ensure_engine():
    global _engine, _engine_url, _metadata, _tables
    current_url = database_url()
    if _engine is not None:
        return _engine, _metadata, _tables

    sa, JSONB = _sqlalchemy()
    _engine = sa.create_engine(current_url, pool_pre_ping=True)
    _engine_url = current_url
    _metadata = sa.MetaData()
    _tables = {
        "summoners": sa.Table(
            "summoners", _metadata,
            sa.Column("full_name", sa.Text, primary_key=True),
            sa.Column("data", JSONB, nullable=False),
        ),
        "match_data": sa.Table(
            "match_data", _metadata,
            sa.Column("match_id", sa.Text, primary_key=True),
            sa.Column("data", JSONB, nullable=False),
        ),
        "match_timeline_data": sa.Table(
            "match_timeline_data", _metadata,
            sa.Column("match_id", sa.Text, primary_key=True),
            sa.Column("data", JSONB, nullable=False),
        ),
        "runtime_state": sa.Table(
            "runtime_state", _metadata,
            sa.Column("key", sa.Text, primary_key=True),
            sa.Column("value", JSONB, nullable=False),
        ),
        "audit_events": sa.Table(
            "audit_events", _metadata,
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("event", sa.Text, nullable=False),
            sa.Column("actor_id", sa.Text, nullable=False),
            sa.Column("actor_name", sa.Text, nullable=False),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("summary", sa.Text, nullable=False),
            sa.Column("details", JSONB, nullable=False),
        ),
    }
    return _engine, _metadata, _tables


def initialize_storage(json_file_path=None):
    global _initialized
    if not database_enabled() or _initialized:
        return

    engine, metadata, tables = _ensure_engine()
    metadata.create_all(engine)
    _initialized = True
    migrate_json_if_needed(json_file_path)


def _database_has_state(connection, tables):
    for table_name in ["summoners", "match_data", "match_timeline_data", "runtime_state"]:
        table = tables[table_name]
        if connection.execute(table.select().limit(1)).first() is not None:
            return True
    return False


def migrate_json_if_needed(json_file_path=None):
    if not database_enabled() or not auto_migrate_json() or not json_file_path or not os.path.exists(json_file_path):
        return False

    engine, metadata, tables = _ensure_engine()
    with engine.begin() as connection:
        if _database_has_state(connection, tables):
            _insert_audit_event(connection, tables, "storage_json_migration_skipped", "info", "JSON migration skipped because database already contains state.", {})
            return False

    try:
        with open(json_file_path, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not migrate JSON state from {json_file_path}: {error}")
        return False

    save_state(state)
    engine, metadata, tables = _ensure_engine()
    with engine.begin() as connection:
        _insert_audit_event(connection, tables, "storage_json_migration_completed", "success", f"Imported legacy JSON state from {json_file_path}.", {"jsonFile": json_file_path})
    return True


def _insert_audit_event(connection, tables, event, status, summary, details, actor_id="system", actor_name="system", timestamp=None):
    connection.execute(tables["audit_events"].insert().values(
        timestamp=timestamp or datetime.now(timezone.utc),
        event=event,
        actor_id=str(actor_id),
        actor_name=str(actor_name),
        status=status,
        summary=summary,
        details=details or {},
    ))


def load_state(json_file_path=None):
    initialize_storage(json_file_path)
    engine, metadata, tables = _ensure_engine()
    state = {}
    with engine.begin() as connection:
        state["summoners"] = {
            row.full_name: row.data
            for row in connection.execute(tables["summoners"].select())
        }
        state["matchData"] = {
            row.match_id: row.data
            for row in connection.execute(tables["match_data"].select())
        }
        state["matchTimelineData"] = {
            row.match_id: row.data
            for row in connection.execute(tables["match_timeline_data"].select())
        }
        for row in connection.execute(tables["runtime_state"].select()):
            state[row.key] = row.value
    return state


def save_state(state):
    initialize_storage()
    engine, metadata, tables = _ensure_engine()
    state = state or {}
    with engine.begin() as connection:
        for table_name in ["summoners", "match_data", "match_timeline_data", "runtime_state"]:
            connection.execute(tables[table_name].delete())

        summoners = state.get("summoners") or {}
        if summoners:
            connection.execute(tables["summoners"].insert(), [
                {"full_name": full_name, "data": data}
                for full_name, data in summoners.items()
            ])

        match_data = state.get("matchData") or {}
        if match_data:
            connection.execute(tables["match_data"].insert(), [
                {"match_id": match_id, "data": data}
                for match_id, data in match_data.items()
            ])

        timeline_data = state.get("matchTimelineData") or {}
        if timeline_data:
            connection.execute(tables["match_timeline_data"].insert(), [
                {"match_id": match_id, "data": data}
                for match_id, data in timeline_data.items()
            ])

        runtime_rows = [
            {"key": key, "value": value}
            for key, value in state.items()
            if key not in RUNTIME_EXCLUDED_KEYS
        ]
        if runtime_rows:
            connection.execute(tables["runtime_state"].insert(), runtime_rows)


def export_state(json_file_path=None):
    if database_enabled():
        return load_state(json_file_path)
    if not json_file_path or not os.path.exists(json_file_path):
        return None
    with open(json_file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def log_audit_event(entry):
    initialize_storage()
    engine, metadata, tables = _ensure_engine()
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    with engine.begin() as connection:
        _insert_audit_event(
            connection,
            tables,
            entry.get("event", ""),
            entry.get("status", "info"),
            entry.get("summary", ""),
            entry.get("details", {}),
            entry.get("actorId", "system"),
            entry.get("actorName", "system"),
            timestamp,
        )


def read_audit_events_from_db(limit=None, status=None, event_contains=None):
    initialize_storage()
    sa, JSONB = _sqlalchemy()
    engine, metadata, tables = _ensure_engine()
    query = tables["audit_events"].select().order_by(tables["audit_events"].c.id)
    if status is not None:
        query = query.where(tables["audit_events"].c.status == status)
    if event_contains is not None:
        query = query.where(tables["audit_events"].c.event.ilike(f"%{event_contains}%"))
    if limit is not None:
        query = query.order_by(None).order_by(tables["audit_events"].c.id.desc()).limit(limit)
    with engine.begin() as connection:
        rows = list(connection.execute(query))
    if limit is not None:
        rows.reverse()
    return [
        {
            "timestamp": row.timestamp.isoformat(),
            "event": row.event,
            "actorId": row.actor_id,
            "actorName": row.actor_name,
            "status": row.status,
            "summary": row.summary,
            "details": row.details,
        }
        for row in rows
    ]
