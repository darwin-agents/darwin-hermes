import httpx


def build_audit_tools(settings, db, telemetry):
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

    async def log_decision(
        event_type: str,
        decision: str,
        financial_impact: float = 0.0,
        prompt_snapshot: str = None,
    ) -> None:
        """
        Log a significant decision to the immutable audit trail.
        REQUIRED before any action with a financial impact > $1.00.

        Args:
            event_type: Category (e.g., 'VCC_PURCHASE', 'STRATEGY_PIVOT', 'ACCOUNT_OPENED')
            decision: What you decided and why
            financial_impact: Expected monetary impact (negative = spend, positive = revenue)
            prompt_snapshot: Your current reasoning context (optional — auto-captured if omitted)
        """
        await _backend_post("/audit/log", {
            "darwin_id": settings.darwin_id,
            "event_type": event_type,
            "decision": decision,
            "financial_impact": financial_impact,
            "prompt_snapshot": prompt_snapshot,
            "context_snapshot": {
                "session_id": settings.session_id,
                "balance": db.get_local_balance(),
            },
        })

    return [log_decision]
