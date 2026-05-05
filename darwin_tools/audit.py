"""
Audit tool — darwin_tools/audit.py

log_decision: append to the immutable audit trail.
Required before any action with financial_impact > $1.00.

Note: the pre_tool_call hook in pnl_hooks.py already auto-emits TOOL_CALL
events for every tool call. log_decision is reserved for explicit financial
audit-log entries that require a structured payload.
"""

from __future__ import annotations


def register_audit_tools(ctx) -> None:
    ctx.register_tool(
        name="log_decision",
        toolset="darwin",
        schema=_schema_log_decision,
        handler=_handle_log_decision,
        is_async=True,
    )


_schema_log_decision = {
    "name": "log_decision",
    "description": (
        "Log a significant decision to the immutable audit trail. "
        "REQUIRED before any action with a financial impact > $1.00."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "event_type": {
                "type": "string",
                "description": "Category: VCC_PURCHASE | STRATEGY_PIVOT | ACCOUNT_OPENED | AD_LAUNCH",
            },
            "decision": {"type": "string", "description": "What you decided and why"},
            "financial_impact": {
                "type": "number",
                "description": "Expected monetary impact (negative = spend, positive = revenue)",
                "default": 0.0,
            },
            "prompt_snapshot": {
                "type": "string",
                "description": "Your current reasoning context (optional)",
            },
        },
        "required": ["event_type", "decision"],
    },
}


async def _handle_log_decision(params: dict, **kwargs) -> dict:
    from .backend_client import get_backend_client
    client = get_backend_client()

    await client.post("/audit/log", {
        "darwin_id": client.darwin_id,
        "event_type": params["event_type"],
        "decision": params["decision"],
        "financial_impact": params.get("financial_impact", 0.0),
        "prompt_snapshot": params.get("prompt_snapshot"),
        "context_snapshot": {"session_id": client.session_id},
    })
    return {"logged": True}
