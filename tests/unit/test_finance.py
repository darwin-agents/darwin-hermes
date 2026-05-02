"""
Unit tests for darwin_tools/finance.py

Invariants checked:
- record_expense always sends a negative amount to the backend (debit)
- check_ledger returns correctly shaped dict with pct_remaining
- inject_payment never exposes PAN/CVV in event payloads or return values
"""
import pytest
import respx
import httpx

from darwin_tools.finance import build_finance_tools


# ---------------------------------------------------------------------------
# check_ledger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_ledger_returns_correct_shape(settings, db, telemetry, backend_mock):
    [check_ledger, _, _] = build_finance_tools(settings, db, telemetry)
    result = await check_ledger()

    assert result["balance"] == 80.00
    assert result["initial_budget"] == 100.00
    assert result["pct_remaining"] == 80.0
    assert isinstance(result["recent_transactions"], list)
    assert len(result["recent_transactions"]) <= 5


@pytest.mark.asyncio
async def test_check_ledger_pct_remaining_calculation(settings, db, telemetry, backend_mock):
    backend_mock.get("/api/v1/finance/ledger/test-darwin-01").mock(
        return_value=httpx.Response(200, json={
            "current_balance": 33.33,
            "initial_budget": 100.00,
            "transactions": [],
        })
    )
    [check_ledger, _, _] = build_finance_tools(settings, db, telemetry)
    result = await check_ledger()
    assert result["pct_remaining"] == 33.3


# ---------------------------------------------------------------------------
# record_expense — critical invariant: amount must always be negated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_expense_negates_positive_amount(settings, db, telemetry, backend_mock):
    posted_bodies = []

    async def capture_body(request, route):
        import json
        posted_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"new_balance": 67.01, "transaction_id": "tx-1"})

    backend_mock.post("/api/v1/finance/ledger/update").mock(side_effect=capture_body)

    [_, record_expense, _] = build_finance_tools(settings, db, telemetry)
    await record_expense(amount=12.99, description="Test purchase")

    assert len(posted_bodies) == 1
    assert posted_bodies[0]["amount"] == -12.99, "amount must be negative (debit)"


@pytest.mark.asyncio
async def test_record_expense_negates_already_negative_amount(settings, db, telemetry, backend_mock):
    """Passing a negative amount must still result in a debit (abs enforced)."""
    posted_bodies = []

    async def capture_body(request, route):
        import json
        posted_bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"new_balance": 67.01, "transaction_id": "tx-2"})

    backend_mock.post("/api/v1/finance/ledger/update").mock(side_effect=capture_body)

    [_, record_expense, _] = build_finance_tools(settings, db, telemetry)
    await record_expense(amount=-5.00, description="Negative input test")

    assert posted_bodies[0]["amount"] == -5.00


@pytest.mark.asyncio
async def test_record_expense_writes_to_local_ledger(settings, db, telemetry, backend_mock):
    [_, record_expense, _] = build_finance_tools(settings, db, telemetry)
    await record_expense(amount=10.00, description="local ledger test")

    rows = db.conn.execute("SELECT * FROM darwin_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == -10.00
    assert rows[0]["description"] == "local ledger test"


# ---------------------------------------------------------------------------
# inject_payment — PAN/CVV must never leak
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_payment_response_has_no_pan_cvv(settings, db, telemetry, backend_mock):
    [_, _, inject_payment] = build_finance_tools(settings, db, telemetry)
    result = await inject_payment(
        browserbase_session_id="bb_sess_1",
        merchant_name="TestShop",
        amount=29.99,
        pan_element="#card-number",
        cvv_element="#cvv",
        expiry_element="#expiry",
    )
    result_str = str(result)
    assert "pan" not in result_str.lower() or result_str == str({"injected": True})
    assert "cvv" not in result_str.lower() or result_str == str({"injected": True})
    assert result.get("injected") is True


@pytest.mark.asyncio
async def test_inject_payment_audit_event_has_no_raw_card_data(settings, db, telemetry, backend_mock):
    [_, _, inject_payment] = build_finance_tools(settings, db, telemetry)
    await inject_payment(
        browserbase_session_id="bb_sess_2",
        merchant_name="AnotherShop",
        amount=9.99,
        pan_element="#cc",
        cvv_element="#sec",
        expiry_element="#exp",
    )
    # Check nothing from any audit log posted to backend contains a card number pattern
    import re
    card_pattern = re.compile(r"\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}")
    for call in backend_mock.calls:
        body = call.request.content.decode("utf-8")
        assert not card_pattern.search(body), f"PAN found in request body: {body}"
