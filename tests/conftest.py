"""
Shared fixtures for the darwin-agent test suite.

All fixtures use in-memory SQLite, fakeredis, and respx for httpx mocking
so no real network calls or file-system side effects occur during tests.
"""
import asyncio
import pytest
import respx
import httpx

from config import Settings
from db.client import SQLiteClient
from telemetry.client import TelemetryClient


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@pytest.fixture
def settings():
    return Settings(
        darwin_id="test-darwin-01",
        session_id="test-session-01",
        agent_jwt="test-jwt",
        backend_url="http://test-backend",
        redis_url="redis://localhost:6379",
        vcc_ref="vcc_ref_test",
        llm_model="claude-sonnet-4-6",
    )


# ---------------------------------------------------------------------------
# In-memory SQLite database
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Fresh SQLiteClient backed by a real on-disk temp file (not :memory:)
    so FTS5 virtual tables work correctly."""
    client = SQLiteClient(str(tmp_path / "test_hermes.db"))
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Telemetry client (records all events in memory — no HTTP)
# ---------------------------------------------------------------------------

class FakeTelemetry:
    def __init__(self):
        self.events: list[dict] = []
        self.metrics: list[dict] = []
        self.entities: list[dict] = []

    async def push_event(self, event_type, payload, **kwargs):
        self.events.append({"event_type": event_type, "payload": payload, **kwargs})
        return f"fake-event-{len(self.events)}"

    async def push_metrics(self, metrics):
        self.metrics.append(metrics)

    async def push_entity(self, entity_type, entity_id, data, operation):
        self.entities.append({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "data": data,
            "operation": operation,
        })

    async def close(self):
        pass


@pytest.fixture
def telemetry():
    return FakeTelemetry()


# ---------------------------------------------------------------------------
# respx router — intercepts all httpx calls to the fake backend
# ---------------------------------------------------------------------------

@pytest.fixture
def backend_mock():
    """
    Activate an respx router that intercepts calls to http://test-backend.
    Tests customise individual routes via backend_mock.get(...) / .post(...).
    """
    with respx.mock(base_url="http://test-backend", assert_all_called=False) as router:
        # Finance defaults
        router.get("/api/v1/finance/ledger/test-darwin-01").mock(
            return_value=httpx.Response(200, json={
                "current_balance": 80.00,
                "initial_budget": 100.00,
                "transactions": [
                    {"id": "tx1", "amount": -20.00, "description": "test spend"},
                ],
            })
        )
        router.post("/api/v1/finance/ledger/update").mock(
            return_value=httpx.Response(200, json={
                "new_balance": 67.01,
                "transaction_id": "tx-new",
            })
        )
        router.post("/api/v1/finance/inject-payment").mock(
            return_value=httpx.Response(200, json={"injected": True})
        )
        # Notifications
        router.post("/api/v1/notifications/send").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        # Telemetry
        router.post("/api/v1/telemetry/entity").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/telemetry/stream-event").mock(
            return_value=httpx.Response(200, json={"event_id": "ev-123"})
        )
        # Memory
        router.post("/api/v1/memory/upload").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/memory/query").mock(
            return_value=httpx.Response(200, json={"skills": []})
        )
        # Assets
        router.get("/api/v1/assets/presigned-url").mock(
            return_value=httpx.Response(200, json={
                "upload_url": "http://storage/upload",
                "public_url": "http://storage/file.jpg",
                "asset_id": "asset-123",
            })
        )
        router.put("http://storage/upload").mock(
            return_value=httpx.Response(200)
        )
        router.post("/api/v1/assets/index").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        # Audit
        router.post("/api/v1/audit/log").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        # Comms
        router.post("/api/v1/comms/send-email").mock(
            return_value=httpx.Response(200, json={"sent": True, "message_id": "msg-1"})
        )
        # Hibernate
        router.post(
            respx.pattern.url_pattern("http://test-backend/api/v1/darwins/test-darwin-01/hibernate")
        ).mock(return_value=httpx.Response(200, json={"ok": True}))

        yield router
