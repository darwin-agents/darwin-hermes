"""
Lifecycle tools — darwin_tools/lifecycle.py

hibernate: the only tool that's truly Darwin-shaped.
  Hermes has no native pause-and-resume — cron is for scheduled jobs,
  not pause-the-container-and-wake-on-webhook.

request_approval: distinct from Hermes' built-in approvals.mode:smart
  because that is for command-line danger (rm -rf /), not financial auth.
  We own the "approve $30 purchase" flow.

ask_human is NOT here — we use Hermes' built-in `clarify` tool.
"""

from __future__ import annotations


class DarwinHibernateSignal(Exception):
    """Raised by hibernate() — caught by modal_app.py for clean shutdown."""

    def __init__(
        self,
        reason: str,
        wake_after_hours: float | None = None,
        wake_on_webhook: str | None = None,
        keep_browser_session: str | None = None,
    ):
        self.reason = reason
        self.wake_after_hours = wake_after_hours
        self.wake_on_webhook = wake_on_webhook
        self.keep_browser_session = keep_browser_session


def register_lifecycle_tools(ctx) -> None:
    ctx.register_tool("hibernate",         _schema_hibernate,         _handle_hibernate)
    ctx.register_tool("request_approval",  _schema_request_approval,  _handle_request_approval)


# ---------------------------------------------------------------------------
# hibernate
# ---------------------------------------------------------------------------

_schema_hibernate = {
    "name": "hibernate",
    "description": (
        "Suspend yourself to save compute costs while waiting for an external event. "
        "TERMINAL ACTION — your container will be destroyed. "
        "Your $HERMES_HOME state (skills, memory, sessions, state.db) is preserved on the Modal Volume. "
        "Call this whenever you are waiting for: an email reply, a Stripe payment, "
        "a human response via clarify(), or have no immediate work to do."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why you are hibernating — injected into your next wake-up prompt",
            },
            "wake_after_hours": {
                "type": "number",
                "description": "Wake after this many hours regardless of other events",
            },
            "wake_on_webhook": {
                "type": "string",
                "description": "Webhook key that will trigger wake-up (from clarify or request_approval)",
            },
            "keep_browser_session": {
                "type": "string",
                "description": "Browserbase session ID to keep alive across sleep",
            },
        },
        "required": ["reason"],
    },
}


async def _handle_hibernate(params: dict) -> None:
    raise DarwinHibernateSignal(
        reason=params["reason"],
        wake_after_hours=params.get("wake_after_hours"),
        wake_on_webhook=params.get("wake_on_webhook"),
        keep_browser_session=params.get("keep_browser_session"),
    )


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------

_schema_request_approval = {
    "name": "request_approval",
    "description": (
        "Request explicit approval from your human creator before making a large purchase. "
        "Call this for any single expense exceeding 20% of your remaining balance. "
        "IMPORTANT: After calling this, immediately call hibernate(wake_on_webhook=<returned key>). "
        "You will wake up with the human's decision in your wake-up prompt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "Amount in USD you want to spend"},
            "justification": {
                "type": "string",
                "description": "Why this expense is necessary and what ROI you expect",
            },
            "token_roi_estimate": {
                "type": "string",
                "description": "Your ROI estimate (e.g. '240% in 3 months')",
            },
        },
        "required": ["amount", "justification"],
    },
}


async def _handle_request_approval(params: dict) -> dict:
    """
    Returns: { "webhook_key": str }
    The agent must immediately call hibernate(wake_on_webhook=webhook_key).
    """
    import time
    from .backend_client import get_backend_client
    client = get_backend_client()

    webhook_key = f"approval_{client.darwin_id}_{int(time.time())}"
    amount = params["amount"]
    justification = params["justification"]
    roi = params.get("token_roi_estimate", "not estimated")

    await client.post("/notifications/send", {
        "darwin_id": client.darwin_id,
        "message": (
            f"Approval needed: ${amount:.2f}\n"
            f"{justification}\n"
            f"Expected ROI: {roi}"
        ),
        "action_type": "APPROVAL",
        "options": ["Approve", "Deny"],
        "webhook_key": webhook_key,
    })
    return {"webhook_key": webhook_key}
