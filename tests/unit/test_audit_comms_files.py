"""
Unit tests for darwin_tools/audit.py, comms.py, files.py

Invariants:
- log_decision posts structured payload to /audit/log
- send_email delegates to /comms/send-email
- upload_file requests presigned URL, uploads directly to storage, then indexes
"""

import json
import os
import tempfile
import pytest

from darwin_tools.audit import register_audit_tools
from darwin_tools.comms import register_comms_tools
from darwin_tools.files import register_files_tools


@pytest.fixture
def audit(ctx):
    register_audit_tools(ctx)
    return ctx.tools["log_decision"][1]


@pytest.fixture
def email(ctx):
    register_comms_tools(ctx)
    return ctx.tools["send_email"][1]


@pytest.fixture
def upload(ctx):
    register_files_tools(ctx)
    return ctx.tools["upload_file"][1]


# ---------------------------------------------------------------------------
# log_decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_decision_posts_to_audit_endpoint(audit, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"ok": True})
    backend_mock.post("/api/v1/audit/log").mock(side_effect=capture)

    result = json.loads(await audit({
        "event_type": "VCC_PURCHASE",
        "decision": "Paying $20 for Shopify subscription",
        "financial_impact": -20.0,
    }))
    assert result["logged"] is True
    assert len(bodies) == 1
    assert bodies[0]["event_type"] == "VCC_PURCHASE"
    assert bodies[0]["financial_impact"] == -20.0
    assert "darwin_id" in bodies[0]


@pytest.mark.asyncio
async def test_log_decision_includes_session_context(audit, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"ok": True})
    backend_mock.post("/api/v1/audit/log").mock(side_effect=capture)

    await audit({"event_type": "STRATEGY_PIVOT", "decision": "Switching to dropship"})
    assert "context_snapshot" in bodies[0]
    assert "session_id" in bodies[0]["context_snapshot"]


@pytest.mark.asyncio
async def test_log_decision_financial_impact_defaults_zero(audit, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"ok": True})
    backend_mock.post("/api/v1/audit/log").mock(side_effect=capture)

    await audit({"event_type": "ACCOUNT_OPENED", "decision": "Opened Gmail"})
    assert bodies[0]["financial_impact"] == 0.0


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_email_delegates_to_backend(email, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"sent": True, "message_id": "msg-1"})
    backend_mock.post("/api/v1/comms/send-email").mock(side_effect=capture)

    result = json.loads(await email({
        "to": "supplier@example.com",
        "subject": "Wholesale inquiry",
        "body": "Hi, I am interested in your products.",
    }))
    assert result["sent"] is True
    assert len(bodies) == 1
    assert bodies[0]["to"] == "supplier@example.com"
    assert bodies[0]["darwin_id"] == "test-darwin-01"


@pytest.mark.asyncio
async def test_send_email_passes_from_name(email, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"sent": True, "message_id": "msg-2"})
    backend_mock.post("/api/v1/comms/send-email").mock(side_effect=capture)

    await email({
        "to": "buyer@shop.com",
        "subject": "Order update",
        "body": "Your order shipped.",
        "from_name": "Pet Supplies Co",
    })
    assert bodies[0]["from_name"] == "Pet Supplies Co"


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_file_returns_asset_url(upload, backend_mock):
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(b"col1,col2\nval1,val2\n")
        path = f.name

    try:
        result = json.loads(await upload({
            "filepath": path,
            "description": "Supplier price comparison",
            "content_type": "text/csv",
        }))
        assert result["asset_id"] == "asset-1"
        assert "storage" in result["public_url"]
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_upload_file_never_sends_content_through_backend(upload, backend_mock):
    """File content must go directly to storage, not through the backend."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake pdf content")
        path = f.name

    backend_bodies = []
    import httpx as hx

    async def capture_backend(request, route):
        backend_bodies.append({"path": str(request.url.path), "size": len(request.content)})
        return hx.Response(200, json={"ok": True})

    backend_mock.post("/api/v1/assets/index").mock(side_effect=capture_backend)

    try:
        await upload({
            "filepath": path,
            "description": "Report",
            "content_type": "application/pdf",
        })
    finally:
        os.unlink(path)

    # The /assets/index call to the backend should be tiny (metadata only — no file bytes)
    index_call = next(b for b in backend_bodies if "index" in b["path"])
    assert index_call["size"] < 500, "File content leaked through backend"
