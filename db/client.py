import sqlite3
import json
import time
import uuid
from pathlib import Path


class SQLiteClient:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self):
        schema = open(Path(__file__).parent / "schema.sql").read()
        self.conn.executescript(schema)
        self.conn.commit()

    def run_pending_migrations(self):
        migration_dir = Path(__file__).parent / "migrations"
        applied = {r["version"] for r in self.conn.execute(
            "SELECT version FROM darwin_migrations"
        )}
        for script in sorted(migration_dir.glob("*.sql")):
            version = int(script.stem.split("_")[0])
            if version not in applied:
                self.conn.executescript(script.read_text())
                self.conn.execute(
                    "INSERT INTO darwin_migrations (version, applied_at) VALUES (?, ?)",
                    (version, int(time.time()))
                )
                self.conn.commit()

    def build_working_memory(self) -> dict:
        row = self.conn.execute(
            "SELECT value FROM hermes_memory WHERE key = 'working_state'"
        ).fetchone()
        if row:
            return json.loads(row["value"])
        return {}

    def get_last_episodic_summary(self) -> str | None:
        row = self.conn.execute(
            "SELECT summary FROM hermes_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["summary"] if row else None

    def get_current_session_transcript(self) -> str | None:
        row = self.conn.execute(
            "SELECT transcript FROM hermes_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["transcript"] if row else None

    def save_episodic_summary(self, summary: str) -> None:
        row = self.conn.execute(
            "SELECT id FROM hermes_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE hermes_sessions SET summary = ? WHERE id = ?",
                (summary, row["id"])
            )
        else:
            self.conn.execute(
                "INSERT INTO hermes_sessions (id, summary, created_at) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), summary, int(time.time()))
            )
        self.conn.commit()

    def save_entity(self, entity_type: str, entity_id: str, data: dict) -> None:
        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO darwin_entities (id, entity_type, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (entity_id, entity_type, json.dumps(data), now, now)
        )
        self.conn.commit()

    def entity_exists(self, entity_id: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM darwin_entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return row is not None

    def get_unsynced_entities(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM darwin_entities WHERE synced_at IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_entity_synced(self, entity_id: str) -> None:
        self.conn.execute(
            "UPDATE darwin_entities SET synced_at = ? WHERE id = ?",
            (int(time.time()), entity_id)
        )
        self.conn.commit()

    def save_skill(self, skill_id: str, title: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO darwin_skills (id, title, content, created_at, usage_count) VALUES (?, ?, ?, ?, 0)",
            (skill_id, title, content, int(time.time()))
        )
        self.conn.commit()

    def search_skills_local(self, query: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM darwin_skills WHERE darwin_skills MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def record_local_expense(
        self,
        amount: float,
        balance_after: float,
        tx_type: str,
        description: str,
        external_ref: str = None,
    ):
        self.conn.execute(
            """
            INSERT INTO darwin_ledger (id, amount, balance_after, type, description, external_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), amount, balance_after, tx_type, description, external_ref, int(time.time()))
        )
        self.conn.commit()

    def get_local_balance(self) -> float:
        row = self.conn.execute(
            "SELECT balance_after FROM darwin_ledger ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["balance_after"] if row else 0.0

    def close(self):
        self.conn.close()
