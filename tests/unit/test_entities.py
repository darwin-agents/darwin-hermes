"""
Unit tests for darwin_tools/entities.py

Invariants:
- save_entity writes to darwin_entities in state.db
- save_entity syncs to backend and marks synced_at
- Falls back to synced=False on backend failure
"""

import json
import sqlite3
import pytest

from darwin_tools.entities import register_entities_tools, _get_db, _SCHEMA


@pytest.fixture
def tools(ctx):
    register_entities_tools(ctx)
    return {name: handler for name, (schema, handler) in ctx.tools.items()}


@pytest.mark.asyncio
async def test_save_entity_writes_to_sqlite(tools, hermes_home_env, backend_mock):
    result = await tools["save_entity"]({
        "entity_type": "account",
        "entity_id": "acc-001",
        "data": {"name": "Stripe", "url": "stripe.com"},
    })
    assert result["saved"] is True

    row = _get_db().execute(
        "SELECT * FROM darwin_entities WHERE id = 'acc-001'"
    ).fetchone()
    assert row is not None
    data = json.loads(row[2])
    assert data["name"] == "Stripe"


@pytest.mark.asyncio
async def test_save_entity_marks_synced(tools, hermes_home_env, backend_mock):
    await tools["save_entity"]({
        "entity_type": "client",
        "entity_id": "client-001",
        "data": {"email": "buyer@example.com"},
    })
    row = _get_db().execute(
        "SELECT synced_at FROM darwin_entities WHERE id = 'client-001'"
    ).fetchone()
    assert row[0] is not None


@pytest.mark.asyncio
async def test_save_entity_synced_false_on_backend_failure(tools, hermes_home_env, backend_mock):
    import httpx
    backend_mock.post("/api/v1/telemetry/entity").mock(
        side_effect=httpx.ConnectError("backend down")
    )
    result = await tools["save_entity"]({
        "entity_type": "product",
        "entity_id": "prod-fail",
        "data": {"name": "widget"},
    })
    assert result["saved"] is True
    assert result["synced"] is False
    assert result["retry_on_next_session"] is True

    row = _get_db().execute(
        "SELECT synced_at FROM darwin_entities WHERE id = 'prod-fail'"
    ).fetchone()
    # synced_at should be NULL since backend failed
    assert row[0] is None


@pytest.mark.asyncio
async def test_save_entity_upsert_updates_data(tools, hermes_home_env, backend_mock):
    await tools["save_entity"]({
        "entity_type": "strategy",
        "entity_id": "strat-001",
        "data": {"name": "dropship v1"},
    })
    await tools["save_entity"]({
        "entity_type": "strategy",
        "entity_id": "strat-001",
        "data": {"name": "dropship v2"},
    })
    rows = _get_db().execute(
        "SELECT data FROM darwin_entities WHERE id = 'strat-001'"
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["name"] == "dropship v2"
