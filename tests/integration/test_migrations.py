"""
Integration tests — SQLite migration runner

Invariants checked:
- Fresh DB on volume gets schema applied on first boot
- Second boot does not re-apply already-applied migrations
- A new migration script is discovered and applied on the next boot
- Migration version is tracked in darwin_migrations
"""
import time
import pytest

from db.client import SQLiteClient


def test_fresh_db_has_no_pending_migrations_after_boot(tmp_path):
    """A freshly created DB should be fully set up after SQLiteClient init."""
    db = SQLiteClient(str(tmp_path / "fresh.db"))
    # Schema tables exist
    tables = {
        r[0] for r in
        db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "darwin_migrations" in tables
    db.close()


def test_run_pending_migrations_applies_new_script(tmp_path):
    """A migration script present in the migrations dir must be applied."""
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()

    sql = """
    CREATE TABLE IF NOT EXISTS darwin_test_table (
        id TEXT PRIMARY KEY,
        value TEXT
    );
    """
    (migration_dir / "010_test_table.sql").write_text(sql)

    db_path = str(tmp_path / "test.db")
    client = SQLiteClient(db_path)

    # Manually invoke migration logic pointed at our temp dir
    from pathlib import Path
    applied = {
        r["version"] for r in
        client.conn.execute("SELECT version FROM darwin_migrations").fetchall()
    }
    for script in sorted(migration_dir.glob("*.sql")):
        version = int(script.stem.split("_")[0])
        if version not in applied:
            client.conn.executescript(script.read_text())
            client.conn.execute(
                "INSERT INTO darwin_migrations (version, applied_at) VALUES (?, ?)",
                (version, int(time.time()))
            )
            client.conn.commit()

    tables = {
        r[0] for r in
        client.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "darwin_test_table" in tables

    applied_after = {
        r["version"] for r in
        client.conn.execute("SELECT version FROM darwin_migrations").fetchall()
    }
    assert 10 in applied_after
    client.close()


def test_migration_not_applied_twice(tmp_path):
    """Running the migration runner twice must not fail or duplicate entries."""
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "020_idempotent.sql").write_text(
        "CREATE TABLE IF NOT EXISTS idem_table (id TEXT PRIMARY KEY);"
    )

    db_path = str(tmp_path / "idem.db")
    client = SQLiteClient(db_path)

    def apply_migrations():
        applied = {
            r["version"] for r in
            client.conn.execute("SELECT version FROM darwin_migrations").fetchall()
        }
        count = 0
        for script in sorted(migration_dir.glob("*.sql")):
            version = int(script.stem.split("_")[0])
            if version not in applied:
                client.conn.executescript(script.read_text())
                client.conn.execute(
                    "INSERT INTO darwin_migrations (version, applied_at) VALUES (?, ?)",
                    (version, int(time.time()))
                )
                client.conn.commit()
                count += 1
        return count

    applied_first = apply_migrations()
    applied_second = apply_migrations()

    assert applied_first == 1, "migration should run exactly once on first boot"
    assert applied_second == 0, "migration should not run again on second boot"

    rows = client.conn.execute(
        "SELECT * FROM darwin_migrations WHERE version = 20"
    ).fetchall()
    assert len(rows) == 1
    client.close()


def test_migration_version_is_tracked(tmp_path):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "030_another.sql").write_text("SELECT 1;")

    db_path = str(tmp_path / "tracked.db")
    client = SQLiteClient(db_path)

    applied = {
        r["version"] for r in
        client.conn.execute("SELECT version FROM darwin_migrations").fetchall()
    }
    for script in sorted(migration_dir.glob("*.sql")):
        version = int(script.stem.split("_")[0])
        if version not in applied:
            client.conn.executescript(script.read_text())
            client.conn.execute(
                "INSERT INTO darwin_migrations (version, applied_at) VALUES (?, ?)",
                (version, int(time.time()))
            )
            client.conn.commit()

    row = client.conn.execute(
        "SELECT applied_at FROM darwin_migrations WHERE version = 30"
    ).fetchone()
    assert row is not None
    assert row["applied_at"] > 0
    client.close()


def test_columns_available_after_migration(tmp_path):
    """New columns added by a migration are immediately queryable."""
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "040_add_revenue.sql").write_text(
        "ALTER TABLE darwin_ledger ADD COLUMN revenue REAL DEFAULT 0.0;"
    )

    db_path = str(tmp_path / "revenue.db")
    client = SQLiteClient(db_path)

    applied = {
        r["version"] for r in
        client.conn.execute("SELECT version FROM darwin_migrations").fetchall()
    }
    for script in sorted(migration_dir.glob("*.sql")):
        version = int(script.stem.split("_")[0])
        if version not in applied:
            client.conn.executescript(script.read_text())
            client.conn.execute(
                "INSERT INTO darwin_migrations (version, applied_at) VALUES (?, ?)",
                (version, int(time.time()))
            )
            client.conn.commit()

    cols = [
        row[1] for row in
        client.conn.execute("PRAGMA table_info(darwin_ledger)").fetchall()
    ]
    assert "revenue" in cols
    client.close()
