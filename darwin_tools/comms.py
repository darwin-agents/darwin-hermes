"""
Comms tool — darwin_tools/comms.py

send_email: delegate to the God Layer which handles provider routing
(SendGrid, Resend, Postmark — whatever the creator configured in their vault).
"""

from __future__ import annotations

import json


def register_comms_tools(ctx) -> None:
    ctx.register_tool(
        name="send_email",
        toolset="darwin",
        schema=_schema_send_email,
        handler=_handle_send_email,
        is_async=True,
    )


_schema_send_email = {
    "name": "send_email",
    "description": (
        "Send an email via the creator's configured email provider. "
        "Use for: supplier outreach, customer follow-up, automated newsletters. "
        "Raises if no email provider is configured in the creator's vault."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Email body (plain text or HTML)"},
            "from_name": {
                "type": "string",
                "description": "Display name (defaults to your agent name)",
            },
        },
        "required": ["to", "subject", "body"],
    },
}


async def _handle_send_email(params: dict, **kwargs) -> str:
    from .backend_client import get_backend_client
    client = get_backend_client()
    resp = await client.post("/comms/send-email", {
        "darwin_id": client.darwin_id,
        "to": params["to"],
        "subject": params["subject"],
        "body": params["body"],
        "from_name": params.get("from_name"),
    })
    return json.dumps(resp)
