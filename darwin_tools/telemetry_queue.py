"""
Durable telemetry queue — SQLite fallback when the God Layer is temporarily unreachable.

Writes to $HERMES_HOME/state.db (Hermes state). Flushed on session start/end.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from darwin_tools.backend_client import BackendClient

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS darwin_telemetry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""


def _db_path() -> Path:
    home = os.environ.get("HERMES_HOME", "/tmp")
    return Path(home) / "state.db"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE_SQL)
    conn.commit()


def enqueue(path: str, body: dict[str, Any]) -> None:
    try:
        db = _db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO darwin_telemetry_queue (path, body, created_at) VALUES (?, ?, ?)",
            (path, json.dumps(body), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("telemetry_queue enqueue failed: %s", exc)


def delete_row(row_id: int) -> None:
    try:
        conn = sqlite3.connect(str(_db_path()))
        conn.execute("DELETE FROM darwin_telemetry_queue WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def bump_attempts(row_id: int) -> None:
    try:
        conn = sqlite3.connect(str(_db_path()))
        conn.execute(
            "UPDATE darwin_telemetry_queue SET attempts = attempts + 1 WHERE id = ?",
            (row_id,),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def fetch_pending(limit: int = 200) -> list[tuple[int, str, str]]:
    try:
        db = _db_path()
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        _ensure_table(conn)
        cur = conn.execute(
            "SELECT id, path, body FROM darwin_telemetry_queue ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]
    except Exception as exc:
        log.warning("telemetry_queue fetch failed: %s", exc)
        return []


async def flush_pending(client: BackendClient) -> int:
    """POST queued items; return approximate number successfully delivered."""
    sent = 0
    for row_id, path, body_s in fetch_pending():
        try:
            body = json.loads(body_s)
            await client.post(path, body)
            delete_row(row_id)
            sent += 1
        except SystemExit:
            raise
        except Exception:
            bump_attempts(row_id)
    return sent


def flush_pending_sync(client: BackendClient) -> int:
    """Same as ``flush_pending`` but uses sync HTTP (Hermes hooks are synchronous)."""
    sent = 0
    for row_id, path, body_s in fetch_pending():
        try:
            body = json.loads(body_s)
            client.post_sync_deliver(path, body)
            delete_row(row_id)
            sent += 1
        except SystemExit:
            raise
        except Exception:
            bump_attempts(row_id)
    return sent
