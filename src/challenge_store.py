"""Atomic challenge documents, independent of legacy whole-state writes."""

from sqlalchemy.dialects.postgresql import insert

from storage import database_enabled, initialize_storage, _ensure_engine


def edit_challenges(guild_id, change):
    if not database_enabled():
        raise RuntimeError("SoloQ Challenge requires DATABASE_URL.")
    initialize_storage()
    engine, _, tables = _ensure_engine()
    table = tables["solo_challenges"]
    key = str(guild_id)
    with engine.begin() as connection:
        connection.execute(insert(table).values(guild_id=key, data={"editions": []}).on_conflict_do_nothing())
        document = connection.execute(table.select().where(table.c.guild_id == key).with_for_update()).one().data
        result = change(document)
        connection.execute(table.update().where(table.c.guild_id == key).values(data=document))
    return result


def read_challenges(guild_id):
    if not database_enabled():
        return {"editions": []}
    initialize_storage()
    engine, _, tables = _ensure_engine()
    table = tables["solo_challenges"]
    with engine.connect() as connection:
        row = connection.execute(table.select().where(table.c.guild_id == str(guild_id))).first()
    return row.data if row else {"editions": []}
