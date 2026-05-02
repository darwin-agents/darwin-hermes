"""
save_entity — darwin_tools/entities.py

The only Darwin-specific table that lives in Hermes' state.db.
Agent-created business objects (suppliers, clients, strategies, products)
are written here and synced immediately to the backend so they appear
on the frontend canvas.

Falls back to synced_at=NULL and retries on next session if the backend
push fails — the entity is never lost.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

# Module-level connection — one per container, reused for all save_entity calls
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS darwin_entities (
    id           TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    synced_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_entities_unsynced
  ON darwin_entities(synced_at) WHERE synced_at IS NULL;
"""


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = os.path.join(os.environ["HERMES_HOME"], "state.db")
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.executescript(_SCHEMA)
    return _conn


def register_entities_tools(ctx) -> None:
    ctx.register_tool("save_entity", _schema_save_entity, _handle_save_entity)


_schema_save_entity = {
    "name": "save_entity",
    "description": (
        "Persist a business object to your local database and sync it to the frontend. "
        "Use for every meaningful business object you create or discover: "
        "supplier accounts (entity_type='account'), clients/leads ('client'/'lead'), "
        "strategies ('strategy'), products ('product'), deployments ('deployment'). "
        "The entity appears in the frontend's Entities panel immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "description": "Category — e.g. 'account', 'client', 'strategy', 'product', 'deployment'",
            },
            "entity_id": {
                "type": "string",
                "description": "Your unique identifier for this entity",
            },
            "data": {
                "type": "object",
                "description": "Any JSON-serialisable dict describing the entity",
            },
        },
        "required": ["entity_type", "entity_id", "data"],
    },
}


async def _handle_save_entity(params: dict) -> dict:
    entity_type = params["entity_type"]
    entity_id = params["entity_id"]
    data = params["data"]

    conn = _get_db()
    now = int(time.time())

    conn.execute(
        """
        INSERT INTO darwin_entities (id, entity_type, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            data = excluded.data,
            updated_at = excluded.updated_at,
            synced_at = NULL
        """,
        (entity_id, entity_type, json.dumps(data), now, now),
    )
    conn.commit()

    from .backend_client import get_backend_client
    client = get_backend_client()
    try:
        await client.post("/telemetry/entity", {
            "darwin_id": client.darwin_id,
            "session_id": client.session_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "data": data,
            "operation": "UPSERT",
        })
        conn.execute(
            "UPDATE darwin_entities SET synced_at = ? WHERE id = ?",
            (now, entity_id),
        )
        conn.commit()
        return {"saved": True, "synced": True}
    except Exception:
        return {"saved": True, "synced": False, "retry_on_next_session": True}
    finally:
        conn.close()
