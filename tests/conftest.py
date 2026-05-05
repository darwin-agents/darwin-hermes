"""
Shared fixtures for the darwin-agent plugin test suite.

All tests mock the God Layer HTTP calls via respx and mock the Hermes ctx
object to verify tool + hook registration without needing the real runtime.
"""

import os
import pytest
import respx
import httpx

# Point darwin_tools at a temp HERMES_HOME so darwin_entities table is isolated
@pytest.fixture(autouse=True)
def hermes_home_env(tmp_path):
    os.environ["HERMES_HOME"] = str(tmp_path)
    os.environ["DARWIN_ID"] = "test-darwin-01"
    os.environ["DARWIN_SESSION_ID"] = "test-session-01"
    os.environ["DARWIN_AGENT_JWT"] = "test-jwt"
    os.environ["DARWIN_BACKEND_URL"] = "http://test-backend"
    os.environ["DARWIN_VCC_REF"] = "vcc_ref_test"
    os.environ["DARWIN_WAKE_PROMPT"] = "Test wake prompt"
    yield
    # Reset singletons between tests
    import darwin_tools.backend_client as bc
    bc.close_backend_client_sync()
    import darwin_tools.entities as ent
    if ent._conn is not None:
        ent._conn.close()
        ent._conn = None


@pytest.fixture
def backend_mock():
    with respx.mock(base_url="http://test-backend", assert_all_called=False) as router:
        router.get("/api/v1/finance/ledger/test-darwin-01").mock(
            return_value=httpx.Response(200, json={
                "current_balance": 80.00,
                "initial_budget": 100.00,
                "transactions": [{"id": "tx1", "amount": -20.00}],
            })
        )
        router.post("/api/v1/finance/ledger/update").mock(
            return_value=httpx.Response(200, json={"new_balance": 67.01, "transaction_id": "tx-new"})
        )
        router.post("/api/v1/finance/inject-payment").mock(
            return_value=httpx.Response(200, json={"injected": True})
        )
        router.post("/api/v1/audit/log").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/notifications/send").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/telemetry/entity").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/telemetry/stream-event").mock(
            return_value=httpx.Response(200, json={"event_id": "ev-1"})
        )
        router.post("/api/v1/telemetry/stream").mock(
            return_value=httpx.Response(202, json={"queued": True})
        )
        router.post("/api/v1/telemetry/llm-call").mock(
            return_value=httpx.Response(201, json={"recorded": True})
        )
        router.get("/api/v1/assets/presigned-url").mock(
            return_value=httpx.Response(200, json={
                "upload_url": "http://storage/upload",
                "public_url": "http://storage/file.pdf",
                "asset_id": "asset-1",
            })
        )
        router.put("http://storage/upload").mock(return_value=httpx.Response(200))
        router.post("/api/v1/assets/index").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/comms/send-email").mock(
            return_value=httpx.Response(200, json={"sent": True, "message_id": "msg-1"})
        )
        yield router


class FakeCtx:
    """Minimal mock of the Hermes plugin context."""
    def __init__(self):
        self.tools: dict[str, tuple] = {}
        self.hooks: dict[str, list] = {}

    def register_tool(
        self,
        name: str,
        toolset: str | None = None,
        schema: dict | None = None,
        handler=None,
        **kwargs,
    ) -> None:
        self.tools[name] = (schema, handler)

    def register_hook(self, event: str, handler) -> None:
        self.hooks.setdefault(event, []).append(handler)


@pytest.fixture
def ctx():
    return FakeCtx()

