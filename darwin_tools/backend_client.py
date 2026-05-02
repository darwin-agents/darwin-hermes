"""
Signed HTTPS client to the FastAPI God Layer.

All darwin_tools modules call get_backend_client() to get a singleton
that is configured from the per-session env vars injected by modal_app.py.
"""

from __future__ import annotations

import os
import httpx

_client: "BackendClient | None" = None


def get_backend_client() -> "BackendClient":
    global _client
    if _client is None:
        _client = BackendClient(
            base_url=os.environ["DARWIN_BACKEND_URL"],
            agent_jwt=os.environ["DARWIN_AGENT_JWT"],
            darwin_id=os.environ["DARWIN_ID"],
            session_id=os.environ["DARWIN_SESSION_ID"],
        )
    return _client


class BackendClient:
    def __init__(
        self,
        base_url: str,
        agent_jwt: str,
        darwin_id: str,
        session_id: str,
    ):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.darwin_id = darwin_id
        self.session_id = session_id
        self._headers = {
            "Authorization": f"Bearer {agent_jwt}",
            "Content-Type": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                self.base + path,
                params=params,
                headers=self._headers,
            )
            self._check_revocation(resp)
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                self.base + path,
                json=body,
                headers=self._headers,
            )
            self._check_revocation(resp)
            resp.raise_for_status()
            return resp.json()

    async def post_async(self, path: str, body: dict) -> None:
        """Fire-and-forget — swallows errors so telemetry never blocks the agent."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self.base + path,
                    json=body,
                    headers=self._headers,
                )
                self._check_revocation(resp)
        except SystemExit:
            raise   # propagate kill signal
        except Exception:
            pass

    async def delete(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                self.base + path,
                headers=self._headers,
            )
            self._check_revocation(resp)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _check_revocation(resp: httpx.Response) -> None:
        """
        If the God Layer returns 401 or 403, the agent JWT has been revoked.
        Raise SystemExit so modal_app.py exits cleanly and volume.commit() runs.
        This is the v0.1 kill-signal mechanism — no Redis needed.
        """
        if resp.status_code in (401, 403):
            raise SystemExit(f"JWT revoked by God Layer (HTTP {resp.status_code}) — shutting down")
