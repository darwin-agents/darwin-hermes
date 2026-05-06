"""
Unit tests for darwin_tools/finance.py

Invariants:
- check_ledger returns correct shape with pct_remaining
- record_expense always sends a negative amount (debit)
- inject_payment never exposes PAN/CVV in args or result
"""

import pytest
import httpx
import json

from darwin_tools.finance import register_finance_tools


@pytest.fixture
def tools(ctx):
    register_finance_tools(ctx)
    return {name: handler for name, (schema, handler) in ctx.tools.items()}


# ---------------------------------------------------------------------------
# check_ledger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_ledger_accepts_registry_dispatch_kwargs(tools, backend_mock):
    """Hermes calls ``handler(args, task_id=..., user_task=...)``."""
    result = json.loads(await tools["check_ledger"]({}, task_id="task-1", user_task=None))
    assert result["balance"] == 80.00


@pytest.mark.asyncio
async def test_check_ledger_returns_correct_shape(tools, backend_mock):
    result = json.loads(await tools["check_ledger"]({}))
    assert result["balance"] == 80.00
    assert result["initial_budget"] == 100.00
    assert result["pct_remaining"] == 80.0
    assert len(result["recent_transactions"]) <= 5


@pytest.mark.asyncio
async def test_check_ledger_pct_remaining(tools, backend_mock):
    backend_mock.get("/api/v1/finance/ledger/test-darwin-01").mock(
        return_value=httpx.Response(200, json={
            "current_balance": 33.33,
            "initial_budget": 100.00,
            "transactions": [],
        })
    )
    result = json.loads(await tools["check_ledger"]({}))
    assert result["pct_remaining"] == 33.3


# ---------------------------------------------------------------------------
# record_expense — amount must always be negated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_expense_negates_positive_amount(tools, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"new_balance": 67.01, "transaction_id": "tx-1"})
    backend_mock.post("/api/v1/finance/ledger/update").mock(side_effect=capture)

    await tools["record_expense"]({"amount": 12.99, "description": "test"})
    assert bodies[0]["amount"] == -12.99


@pytest.mark.asyncio
async def test_record_expense_negates_already_negative(tools, backend_mock):
    bodies = []
    async def capture(request, route):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"new_balance": 70.00, "transaction_id": "tx-2"})
    backend_mock.post("/api/v1/finance/ledger/update").mock(side_effect=capture)

    await tools["record_expense"]({"amount": -5.00, "description": "neg test"})
    assert bodies[0]["amount"] == -5.00


# ---------------------------------------------------------------------------
# inject_payment — PAN/CVV must never leak
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_payment_response_no_pan(tools, backend_mock):
    result = json.loads(await tools["inject_payment"]({
        "browserbase_session_id": "bb_1",
        "merchant_name": "TestShop",
        "amount": 29.99,
        "pan_element": "#card",
        "cvv_element": "#cvv",
        "expiry_element": "#expiry",
    }))
    assert result.get("injected") is True
    result_str = json.dumps(result)
    import re
    assert not re.search(r"\b\d{13,19}\b", result_str), "PAN found in result"


@pytest.mark.asyncio
async def test_inject_payment_request_contains_no_card_digits(tools, backend_mock):
    import re
    calls = []
    async def capture(request, route):
        calls.append(request.content.decode())
        return httpx.Response(200, json={"injected": True})
    backend_mock.post("/api/v1/finance/inject-payment").mock(side_effect=capture)

    await tools["inject_payment"]({
        "browserbase_session_id": "bb_2",
        "merchant_name": "Shop",
        "amount": 9.99,
        "pan_element": "#p",
        "cvv_element": "#c",
        "expiry_element": "#e",
    })
    for body in calls:
        assert not re.search(r"\b\d{13,19}\b", body), f"PAN found in request: {body}"


# ---------------------------------------------------------------------------
# B1: Division-by-zero when initial_budget is 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_ledger_zero_budget_no_crash(tools, backend_mock):
    backend_mock.get("/api/v1/finance/ledger/test-darwin-01").mock(
        return_value=httpx.Response(200, json={
            "current_balance": 0.0,
            "initial_budget": 0,
            "transactions": [],
        })
    )
    result = json.loads(await tools["check_ledger"]({}))
    assert result["pct_remaining"] == 0.0
    assert result["balance"] == 0.0


# ---------------------------------------------------------------------------
# S1: inject_payment balance pre-flight check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inject_payment_rejects_insufficient_balance(tools, backend_mock):
    """inject_payment must return an error when amount > current balance."""
    backend_mock.get("/api/v1/finance/ledger/test-darwin-01").mock(
        return_value=httpx.Response(200, json={
            "current_balance": 10.00,
            "initial_budget": 100.00,
            "transactions": [],
        })
    )
    result = json.loads(await tools["inject_payment"]({
        "browserbase_session_id": "bb_3",
        "merchant_name": "ExpensiveShop",
        "amount": 50.00,
        "pan_element": "#p",
        "cvv_element": "#c",
        "expiry_element": "#e",
    }))
    assert result["error"] == "INSUFFICIENT_BALANCE"
    assert "50.00" in result["message"]

