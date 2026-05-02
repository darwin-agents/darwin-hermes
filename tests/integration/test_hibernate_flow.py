"""
Integration test — full hibernate flow

Simulates: check_ledger → save_entity → request_approval → hibernate
Verifies the whole plugin wires up correctly and the signal propagates.
"""

import pytest
import httpx
import json

from darwin_tools import register
from darwin_tools.lifecycle import DarwinHibernateSignal


@pytest.fixture
def all_tools(ctx):
    register(ctx)
    return {name: handler for name, (schema, handler) in ctx.tools.items()}


@pytest.mark.asyncio
async def test_full_session_lifecycle(all_tools, backend_mock):
    """
    check_ledger → save_entity → hibernate
    The full happy path for a session that does work and then sleeps.
    """
    # 1. check_ledger
    ledger = await all_tools["check_ledger"]({})
    assert ledger["balance"] == 80.00
    assert ledger["pct_remaining"] == 80.0

    # 2. log_decision before spending
    await all_tools["log_decision"]({
        "event_type": "STRATEGY_PIVOT",
        "decision": "Starting pet supply dropship strategy",
        "financial_impact": 0.0,
    })

    # 3. save_entity
    entity_result = await all_tools["save_entity"]({
        "entity_type": "strategy",
        "entity_id": "strat-pet-001",
        "data": {"name": "Pet supplies dropship", "margin": "40-60%"},
    })
    assert entity_result["saved"] is True

    # 4. hibernate
    with pytest.raises(DarwinHibernateSignal) as exc_info:
        await all_tools["hibernate"]({
            "reason": "Strategy saved. Waiting 24h before next action.",
            "wake_after_hours": 24,
        })

    sig = exc_info.value
    assert sig.wake_after_hours == 24
    assert "Strategy saved" in sig.reason


@pytest.mark.asyncio
async def test_approval_then_hibernate_flow(all_tools, backend_mock):
    """request_approval → hibernate(wake_on_webhook=...) is the correct large-purchase flow."""
    # 1. Request approval for a large purchase
    approval = await all_tools["request_approval"]({
        "amount": 50.00,
        "justification": "Facebook ads campaign — expected 300% ROI in 30 days",
        "token_roi_estimate": "300%",
    })
    webhook_key = approval["webhook_key"]
    assert webhook_key.startswith("approval_test-darwin-01_")

    # 2. Immediately hibernate waiting for the human's response
    with pytest.raises(DarwinHibernateSignal) as exc_info:
        await all_tools["hibernate"]({
            "reason": "Awaiting approval for $50 Facebook ads budget",
            "wake_on_webhook": webhook_key,
        })

    sig = exc_info.value
    assert sig.wake_on_webhook == webhook_key
    assert sig.wake_after_hours is None


@pytest.mark.asyncio
async def test_jwt_revocation_kills_agent(all_tools, backend_mock):
    """When the God Layer returns 401, the agent must raise SystemExit cleanly."""
    backend_mock.get("/api/v1/finance/ledger/test-darwin-01").mock(
        return_value=httpx.Response(401, json={"error": "jwt_revoked"})
    )
    with pytest.raises(SystemExit):
        await all_tools["check_ledger"]({})


@pytest.mark.asyncio
async def test_all_nine_tools_callable(all_tools):
    """Smoke test — all 9 registered tools are present and callable."""
    expected = {
        "check_ledger", "record_expense", "inject_payment",
        "hibernate", "request_approval",
        "save_entity", "upload_file", "send_email", "log_decision",
    }
    assert expected.issubset(set(all_tools.keys()))
    for name in expected:
        assert callable(all_tools[name]), f"{name} is not callable"
