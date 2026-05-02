import httpx


def build_comms_tools(settings, db, telemetry):
    async def _backend_post(path: str, body: dict) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def send_email(
        to: str,
        subject: str,
        body: str,
        from_name: str = None,
    ) -> dict:
        """
        Send an email via the creator's configured email provider.
        Use for: supplier outreach, customer follow-up, automated newsletters.

        Args:
            to: Recipient email address
            subject: Email subject line
            body: Email body (plain text or HTML)
            from_name: Display name (defaults to your agent name)

        Returns: { "sent": bool, "message_id": str }
        Raises: If no email provider has been configured in the creator's vault
        """
        import time
        t0 = time.time()
        resp = await _backend_post("/comms/send-email", {
            "darwin_id": settings.darwin_id,
            "to": to,
            "subject": subject,
            "body": body,
            "from_name": from_name,
        })
        await telemetry.push_event("TOOL_RESULT", {
            "tool_name": "send_email",
            "status": "success",
            "output_preview": f"Email sent to {to} — subject: {subject}",
            "latency_ms": int((time.time() - t0) * 1000),
        })
        return resp

    return [send_email]
