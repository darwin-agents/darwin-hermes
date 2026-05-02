import httpx


def build_finance_tools(settings, db, telemetry):
    async def _backend_get(path: str, params: dict = None) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

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

    async def check_ledger() -> dict:
        """
        Returns your current wallet balance and recent transaction history.
        Call this before any spending decision.

        Returns: { "balance": float, "initial_budget": float, "pct_remaining": float,
                    "recent_transactions": list }
        """
        resp = await _backend_get(f"/finance/ledger/{settings.darwin_id}")
        return {
            "balance": resp["current_balance"],
            "initial_budget": resp["initial_budget"],
            "pct_remaining": round(resp["current_balance"] / resp["initial_budget"] * 100, 1),
            "recent_transactions": resp["transactions"][:5],
        }

    async def record_expense(
        amount: float,
        description: str,
        type: str = "VCC_DEBIT",
        external_ref: str = None,
    ) -> dict:
        """
        Record a spending event after a purchase has been made.
        Call this AFTER a successful checkout, not before.

        Args:
            amount: Positive number representing the amount spent (e.g., 12.99)
            description: What was purchased
            type: Transaction type — defaults to 'VCC_DEBIT'
            external_ref: Optional order ID or reference from the merchant

        Returns: { "new_balance": float, "transaction_id": str }
        Raises: InsufficientFundsError if balance would go negative
        """
        resp = await _backend_post("/finance/ledger/update", {
            "darwin_id": settings.darwin_id,
            "session_id": settings.session_id,
            "amount": -abs(amount),
            "type": type,
            "description": description,
            "external_ref": external_ref,
        })
        db.record_local_expense(-abs(amount), resp["new_balance"], type, description, external_ref)
        return resp

    async def inject_payment(
        browserbase_session_id: str,
        merchant_name: str,
        amount: float,
        pan_element: str,
        cvv_element: str,
        expiry_element: str,
    ) -> dict:
        """
        Securely inject your VCC into the active browser session to complete a purchase.
        You never see the raw card number — the backend handles decryption and injection directly.

        Args:
            browserbase_session_id: The active Browserbase session ID
            merchant_name: Name of the merchant (for audit log)
            amount: Expected charge amount (for pre-authorisation check)
            pan_element: CSS selector or coordinate description for the card number field
            cvv_element: CSS selector or coordinate description for the CVV field
            expiry_element: CSS selector or coordinate description for the expiry field

        Returns: { "injected": true } on success
        """
        from darwin_tools.audit import build_audit_tools
        audit_tools = build_audit_tools(settings, db, telemetry)
        log_decision = audit_tools[0]

        await log_decision(
            event_type="VCC_PURCHASE",
            decision=f"Inject VCC for purchase at {merchant_name} — ${amount:.2f}",
            financial_impact=-amount,
        )
        resp = await _backend_post("/finance/inject-payment", {
            "darwin_id": settings.darwin_id,
            "vcc_ref": settings.vcc_ref,
            "browserbase_session_id": browserbase_session_id,
            "target_fields": {
                "pan_element": pan_element,
                "cvv_element": cvv_element,
                "expiry_element": expiry_element,
            }
        })
        return resp

    return [check_ledger, record_expense, inject_payment]
