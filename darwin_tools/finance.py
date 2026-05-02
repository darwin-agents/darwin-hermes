"""
Financial tools — darwin_tools/finance.py

check_ledger, record_expense, inject_payment.
All forward to the God Layer over HTTPS with JWT auth.
The agent never holds raw VCC PAN — only an opaque vcc_ref token.
"""

from __future__ import annotations

import os


def register_finance_tools(ctx) -> None:
    ctx.register_tool("check_ledger",   _schema_check_ledger,   _handle_check_ledger)
    ctx.register_tool("record_expense", _schema_record_expense, _handle_record_expense)
    ctx.register_tool("inject_payment", _schema_inject_payment, _handle_inject_payment)


# ---------------------------------------------------------------------------
# check_ledger
# ---------------------------------------------------------------------------

_schema_check_ledger = {
    "name": "check_ledger",
    "description": (
        "Returns your current wallet balance and recent transaction history. "
        "Call this at the start of every session before any spending decision."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


async def _handle_check_ledger(params: dict) -> dict:
    from .backend_client import get_backend_client
    client = get_backend_client()
    resp = await client.get(f"/finance/ledger/{client.darwin_id}")
    return {
        "balance": resp["current_balance"],
        "initial_budget": resp["initial_budget"],
        "pct_remaining": round(
            resp["current_balance"] / resp["initial_budget"] * 100, 1
        ),
        "recent_transactions": resp["transactions"][:5],
    }


# ---------------------------------------------------------------------------
# record_expense
# ---------------------------------------------------------------------------

_schema_record_expense = {
    "name": "record_expense",
    "description": (
        "Record a spending event AFTER a purchase has been completed. "
        "Never call before the checkout succeeds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Amount spent in USD (positive number, e.g. 12.99)",
            },
            "description": {"type": "string"},
            "type": {
                "type": "string",
                "default": "VCC_DEBIT",
                "enum": ["VCC_DEBIT", "SUBSCRIPTION", "SERVICE_FEE", "AD_SPEND"],
            },
            "external_ref": {
                "type": "string",
                "description": "Optional order ID or reference from the merchant",
            },
        },
        "required": ["amount", "description"],
    },
}


async def _handle_record_expense(params: dict) -> dict:
    from .backend_client import get_backend_client
    client = get_backend_client()
    resp = await client.post("/finance/ledger/update", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "amount": -abs(params["amount"]),   # always a debit
        "type": params.get("type", "VCC_DEBIT"),
        "description": params["description"],
        "external_ref": params.get("external_ref"),
    })
    return resp


# ---------------------------------------------------------------------------
# inject_payment
# ---------------------------------------------------------------------------

_schema_inject_payment = {
    "name": "inject_payment",
    "description": (
        "Securely inject your VCC into the active browser session to complete a purchase. "
        "You never see the raw card number — the backend handles decryption and injection. "
        "Always call log_decision() BEFORE this tool for any purchase over $1.00."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "browserbase_session_id": {
                "type": "string",
                "description": "The active Browserbase session ID",
            },
            "merchant_name": {"type": "string"},
            "amount": {"type": "number", "description": "Expected charge in USD"},
            "pan_element": {
                "type": "string",
                "description": "CSS selector or coordinate hint for the card number field",
            },
            "cvv_element": {"type": "string"},
            "expiry_element": {"type": "string"},
        },
        "required": ["browserbase_session_id", "merchant_name", "amount",
                     "pan_element", "cvv_element", "expiry_element"],
    },
}


async def _handle_inject_payment(params: dict) -> dict:
    """Agent never sees raw PAN/CVV — backend handles decryption + WebSocket injection."""
    from .backend_client import get_backend_client
    client = get_backend_client()

    # Audit-log intent before money moves
    await client.post("/audit/log", {
        "darwin_id": client.darwin_id,
        "event_type": "VCC_PURCHASE",
        "decision": f"Inject VCC at {params['merchant_name']} — ${params['amount']:.2f}",
        "financial_impact": -params["amount"],
        "context_snapshot": {"session_id": client.session_id},
    })

    return await client.post("/finance/inject-payment", {
        "darwin_id": client.darwin_id,
        "vcc_ref": os.environ.get("DARWIN_VCC_REF", ""),
        "browserbase_session_id": params["browserbase_session_id"],
        "merchant_name": params["merchant_name"],
        "expected_amount": params["amount"],
        "target_fields": {
            "pan_element": params["pan_element"],
            "cvv_element": params["cvv_element"],
            "expiry_element": params["expiry_element"],
        },
    })
