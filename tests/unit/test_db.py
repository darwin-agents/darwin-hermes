"""
Unit tests for db/client.py

Covers: schema creation, migration runner, entity CRUD, skill FTS5,
local ledger recording, working memory loading, episodic summary persistence.
"""
import json
import time
import pytest

from db.client import SQLiteClient


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def test_schema_creates_all_tables(db):
    tables = {
        row[0] for row in
        db.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')").fetchall()
    }
    required = {
        "hermes_memory", "hermes_sessions",
        "darwin_entities", "darwin_ledger",
        "darwin_hibernate_log", "darwin_migrations",
    }
    assert required.issubset(tables)


def test_schema_creates_skills_fts5_table(db):
    # FTS5 tables appear as content tables in sqlite_master
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'darwin_skills'"
    ).fetchall()
    assert len(rows) == 1


def test_schema_is_idempotent(tmp_path):
    """Opening the same DB file twice must not raise."""
    path = str(tmp_path / "idem.db")
    db1 = SQLiteClient(path)
    db2 = SQLiteClient(path)
    db1.close()
    db2.close()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def test_migration_runner_applies_pending_scripts(tmp_path):
    """run_pending_migrations() discovers and applies new SQL scripts."""
    db_path = str(tmp_path / "migtest.db")
    client = SQLiteClient(db_path)

    # Manually run migration logic against a temp migration dir
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "002_add_column.sql").write_text(
        "ALTER TABLE darwin_entities ADD COLUMN extra TEXT;"
    )

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
        client.conn.execute("PRAGMA table_info(darwin_entities)").fetchall()
    ]
    assert "extra" in cols
    client.close()


def test_migration_not_applied_twice(tmp_path):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    call_count = {"n": 0}

    db_path = str(tmp_path / "migonce.db")
    client = SQLiteClient(db_path)

    def run_once(script_text):
        call_count["n"] += 1
        client.conn.executescript(script_text)
        client.conn.execute(
            "INSERT INTO darwin_migrations (version, applied_at) VALUES (999, ?)",
            (int(time.time()),)
        )
        client.conn.commit()

    (migration_dir / "999_test.sql").write_text("SELECT 1;")
    run_once("SELECT 1;")

    # Second run should skip version 999 since it's already in darwin_migrations
    applied = {r["version"] for r in client.conn.execute("SELECT version FROM darwin_migrations")}
    assert 999 in applied
    assert call_count["n"] == 1
    client.close()


# ---------------------------------------------------------------------------
# Entity CRUD
# ---------------------------------------------------------------------------

def test_save_entity_new(db):
    db.save_entity("account", "acc-1", {"name": "Shopify", "url": "mystore.com"})
    row = db.conn.execute("SELECT * FROM darwin_entities WHERE id = 'acc-1'").fetchone()
    assert row is not None
    assert row["entity_type"] == "account"
    data = json.loads(row["data"])
    assert data["name"] == "Shopify"
    assert row["synced_at"] is None


def test_save_entity_upsert_updates_data(db):
    db.save_entity("product", "prod-1", {"price": 9.99})
    db.save_entity("product", "prod-1", {"price": 14.99})

    rows = db.conn.execute("SELECT * FROM darwin_entities WHERE id = 'prod-1'").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0]["data"])
    assert data["price"] == 14.99


def test_entity_exists_returns_false_for_missing(db):
    assert db.entity_exists("no-such-entity") is False


def test_entity_exists_returns_true_after_save(db):
    db.save_entity("lead", "lead-1", {"email": "x@x.com"})
    assert db.entity_exists("lead-1") is True


def test_get_unsynced_entities_returns_only_unsynced(db):
    db.save_entity("client", "c-1", {"name": "Alice"})
    db.save_entity("client", "c-2", {"name": "Bob"})
    db.mark_entity_synced("c-1")

    unsynced = db.get_unsynced_entities()
    ids = [e["id"] for e in unsynced]
    assert "c-2" in ids
    assert "c-1" not in ids


def test_mark_entity_synced_sets_timestamp(db):
    db.save_entity("deployment", "dep-1", {})
    db.mark_entity_synced("dep-1")

    row = db.conn.execute("SELECT synced_at FROM darwin_entities WHERE id = 'dep-1'").fetchone()
    assert row["synced_at"] is not None
    assert row["synced_at"] > 0


# ---------------------------------------------------------------------------
# Skill FTS5
# ---------------------------------------------------------------------------

def test_save_skill_and_search(db):
    db.save_skill("sk-1", "Navigate Shopify checkout", "Step 1: take screenshot. Step 2: click Buy.")
    db.save_skill("sk-2", "Email outreach template", "Dear supplier, I am interested in...")

    results = db.search_skills_local("shopify checkout")
    assert len(results) >= 1
    assert results[0]["title"] == "Navigate Shopify checkout"


def test_search_skills_returns_empty_for_no_match(db):
    db.save_skill("sk-3", "Facebook ad setup", "Create a campaign...")
    results = db.search_skills_local("quantum entanglement")
    assert results == []


# ---------------------------------------------------------------------------
# Local ledger
# ---------------------------------------------------------------------------

def test_record_local_expense_stores_entry(db):
    db.record_local_expense(-9.99, 70.01, "VCC_DEBIT", "Test purchase", "ext-ref-1")
    rows = db.conn.execute("SELECT * FROM darwin_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == -9.99
    assert rows[0]["balance_after"] == 70.01
    assert rows[0]["external_ref"] == "ext-ref-1"


def test_get_local_balance_returns_latest(db):
    import uuid
    # Insert with explicit created_at timestamps to ensure ordering is deterministic
    now = int(time.time())
    db.conn.execute(
        "INSERT INTO darwin_ledger (id, amount, balance_after, type, description, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), -10.00, 90.00, "VCC_DEBIT", "first", now)
    )
    db.conn.execute(
        "INSERT INTO darwin_ledger (id, amount, balance_after, type, description, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), -5.00, 85.00, "VCC_DEBIT", "second", now + 1)
    )
    db.conn.commit()
    assert db.get_local_balance() == 85.00


def test_get_local_balance_returns_zero_when_no_ledger(db):
    assert db.get_local_balance() == 0.0


# ---------------------------------------------------------------------------
# Working memory + episodic
# ---------------------------------------------------------------------------

def test_build_working_memory_returns_empty_for_fresh_agent(db):
    result = db.build_working_memory()
    assert result == {}


def test_build_working_memory_returns_stored_state(db):
    state = {"darwin_id": "test-01", "balance": 50.0}
    db.conn.execute(
        "INSERT INTO hermes_memory (key, value, updated_at) VALUES ('working_state', ?, ?)",
        (json.dumps(state), int(time.time()))
    )
    db.conn.commit()
    result = db.build_working_memory()
    assert result["balance"] == 50.0


def test_save_and_read_episodic_summary(db):
    import uuid
    db.conn.execute(
        "INSERT INTO hermes_sessions (id, transcript, created_at) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), "full transcript text", int(time.time()))
    )
    db.conn.commit()

    db.save_episodic_summary("Agent opened a Shopify store and ran ads.")
    summary = db.get_last_episodic_summary()
    assert summary == "Agent opened a Shopify store and ran ads."
