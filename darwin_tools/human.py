import time
import httpx


def build_human_tools(settings, db, telemetry):
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

    async def ask_human(
        prompt: str,
        context: str = None,
        screenshot_url: str = None,
        timeout_minutes: int = 5,
    ) -> str:
        """
        Send a message to your human creator and wait for their reply.
        IMPORTANT: After calling this, you MUST immediately call hibernate().
        The human's response will be injected into your next wake-up prompt.

        Use this when you encounter:
        - A 2FA SMS code or TOTP requirement you cannot solve
        - A CAPTCHA you have failed more than twice
        - A decision requiring legal or financial authority you do not have

        Args:
            prompt: Clear, specific question for the human
            context: Additional context to help the human understand the situation
            screenshot_url: URL of a screenshot showing what you see (from take_screenshot)
            timeout_minutes: How long to wait before assuming the human is unavailable

        Returns: The webhook_key to pass to hibernate(wake_on_webhook=...) so your container
                 wakes up when the human replies.
        """
        webhook_key = f"ask_human_{settings.darwin_id}_{int(time.time())}"
        await _backend_post("/notifications/send", {
            "darwin_id": settings.darwin_id,
            "message": prompt,
            "screenshot_url": screenshot_url,
            "action_type": "ASK_HUMAN",
        })
        await telemetry.push_event("ASK_HUMAN_SENT", {
            "prompt": prompt,
            "screenshot_url": screenshot_url,
            "timeout_seconds": timeout_minutes * 60,
            "type": "general",
        })
        return webhook_key

    async def request_approval(
        amount: float,
        justification: str,
        token_roi_estimate: str = None,
    ) -> str:
        """
        Request explicit approval from your human creator before making a large purchase.
        Call this for any single expense exceeding 20% of your remaining balance.

        IMPORTANT: After calling this, you MUST immediately call hibernate().
        You will wake up with the human's decision (True/False) in your wake-up prompt.

        Args:
            amount: The amount you want to spend
            justification: Why this expense is necessary and profitable
            token_roi_estimate: Your estimate of the ROI (e.g., "240% in 3 months")

        Returns: webhook_key to pass to hibernate(wake_on_webhook=...)
        """
        webhook_key = f"approval_{settings.darwin_id}_{int(time.time())}"
        await _backend_post("/notifications/send", {
            "darwin_id": settings.darwin_id,
            "message": f"Approval needed: ${amount:.2f}\n{justification}",
            "action_type": "APPROVAL",
            "options": ["Approve", "Deny"],
        })
        await telemetry.push_event("APPROVAL_REQUESTED", {
            "amount": amount,
            "justification": justification,
            "token_roi_estimate": token_roi_estimate,
        })
        return webhook_key

    return [ask_human, request_approval]
