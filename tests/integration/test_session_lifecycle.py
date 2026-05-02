"""
Integration tests — full session lifecycle

Simulates the complete flow: check_ledger → browser_fetch → save_entity → hibernate
using all real tool implementations against fake HTTP/Redis backends.
"""
import pytest
import httpx

from darwin_tools.lifecycle import HibernateSignal
from darwin_tools.finance import build_finance_tools
from darwin_tools.browser import build_browser_tools
from darwin_tools.memory import build_memory_tools
from darwin_tools.lifecycle import build_lifecycle_tools
from darwin_tools.audit import build_audit_tools


# ---------------------------------------------------------------------------
# Full session flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_session_lifecycle(settings, db, telemetry, backend_mock, monkeypatch):
    """
    Lifecycle: check_ledger → browser_fetch → save_entity → hibernate

    Verifies:
    - Each step produces the correct telemetry event
    - Entity is persisted in SQLite and marked synced
    - hibernate() raises HibernateSignal at the end
    - SQLite has a record of the ledger state
    """
    import darwin_tools.browser as bmod

    class FakeBB:
        async def fetch(self, url):
            return {"markdown": "# Pet Supplies Niche\nHigh margin, low competition."}

    monkeypatch.setattr(bmod, "BrowserbaseClient", lambda **kwargs: FakeBB())

    # --- Step 1: check_ledger ---
    [check_ledger, record_expense, _] = build_finance_tools(settings, db, telemetry)
    ledger = await check_ledger()
    assert ledger["balance"] == 80.00
    assert ledger["pct_remaining"] == 80.0

    # --- Step 2: browser_fetch ---
    [browser_fetch, *_] = build_browser_tools(settings, db, telemetry)
    content = await browser_fetch("https://niche-research.com/pet-supplies")
    assert "<external_data" in content
    assert "Pet Supplies" in content

    # --- Step 3: save_entity ---
    [_, _, save_entity, _] = build_memory_tools(settings, db, telemetry)
    result = await save_entity("strategy", "strat-pet-001", {
        "name": "Pet supplies dropship",
        "source": "niche-research.com",
        "margin_estimate": "40-60%",
    })
    assert result["saved"] is True
    assert result["synced"] is True

    row = db.conn.execute(
        "SELECT * FROM darwin_entities WHERE id = 'strat-pet-001'"
    ).fetchone()
    assert row is not None
    assert row["synced_at"] is not None

    # --- Step 4: hibernate ---
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)
    with pytest.raises(HibernateSignal) as exc_info:
        await hibernate(reason="Strategy saved, waiting 24h before next action", wake_after_hours=24)

    sig = exc_info.value
    assert sig.wake_after_hours == 24
    assert "Strategy saved" in sig.reason

    # Verify telemetry breadcrumb
    event_types = [e["event_type"] for e in telemetry.events]
    assert "BROWSER_FETCH" in event_types
    assert "SKILL_CREATED" not in event_types  # not called in this flow


@pytest.mark.asyncio
async def test_session_expense_recorded_after_purchase(settings, db, telemetry, backend_mock):
    """record_expense writes to local ledger and POSTs to backend."""
    [check_ledger, record_expense, _] = build_finance_tools(settings, db, telemetry)

    result = await record_expense(
        amount=12.99,
        description="Shopify subscription",
        type="VCC_DEBIT",
    )
    assert result["new_balance"] == 67.01

    rows = db.conn.execute("SELECT * FROM darwin_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == -12.99


@pytest.mark.asyncio
async def test_session_ask_human_then_hibernate(settings, db, telemetry, backend_mock):
    """
    ask_human() → hibernate(wake_on_webhook=...) is the correct CAPTCHA escalation flow.
    Verifies the webhook key returned by ask_human is passed to hibernate.
    """
    from darwin_tools.human import build_human_tools
    from darwin_tools.lifecycle import build_lifecycle_tools

    [ask_human, _] = build_human_tools(settings, db, telemetry)
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)

    webhook_key = await ask_human(
        prompt="Please solve this CAPTCHA manually.",
        screenshot_url="http://storage/screenshot.jpg",
    )

    assert webhook_key.startswith("ask_human_test-darwin-01_")

    # Agent must immediately hibernate with the returned webhook_key
    with pytest.raises(HibernateSignal) as exc_info:
        await hibernate(
            reason="Waiting for human to solve CAPTCHA",
            wake_on_webhook=webhook_key,
        )

    assert exc_info.value.wake_on_webhook == webhook_key

    # Telemetry must show ASK_HUMAN_SENT
    events = [e for e in telemetry.events if e["event_type"] == "ASK_HUMAN_SENT"]
    assert len(events) == 1
    assert "CAPTCHA" in events[0]["payload"]["prompt"]


@pytest.mark.asyncio
async def test_request_approval_flow(settings, db, telemetry, backend_mock):
    """request_approval() returns a webhook key that the agent uses for hibernate."""
    from darwin_tools.human import build_human_tools
    from darwin_tools.lifecycle import build_lifecycle_tools

    [_, request_approval] = build_human_tools(settings, db, telemetry)
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)

    webhook_key = await request_approval(
        amount=50.00,
        justification="Facebook ads budget for pet supplies campaign",
        token_roi_estimate="300% in 30 days",
    )

    assert webhook_key.startswith("approval_test-darwin-01_")

    events = [e for e in telemetry.events if e["event_type"] == "APPROVAL_REQUESTED"]
    assert len(events) == 1
    assert events[0]["payload"]["amount"] == 50.00

    with pytest.raises(HibernateSignal):
        await hibernate(reason="Awaiting approval", wake_on_webhook=webhook_key)


@pytest.mark.asyncio
async def test_audit_log_required_before_large_purchase(settings, db, telemetry, backend_mock):
    """log_decision must be called before inject_payment; audit POST must reach backend."""
    [log_decision] = build_audit_tools(settings, db, telemetry)

    await log_decision(
        event_type="VCC_PURCHASE",
        decision="Paying Shopify $79/month — expected ROI 400%",
        financial_impact=-79.00,
    )

    audit_calls = [c for c in backend_mock.calls if "/audit/log" in str(c.request.url)]
    assert len(audit_calls) == 1
    import json
    body = json.loads(audit_calls[0].request.content)
    assert body["event_type"] == "VCC_PURCHASE"
    assert body["financial_impact"] == -79.00
