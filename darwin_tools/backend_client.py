"""
Signed HTTPS client to the FastAPI God Layer.

All darwin_tools modules call get_backend_client() to get a singleton
that is configured from the per-session env vars injected by modal_app.py.

The singleton holds a persistent httpx.AsyncClient for connection pooling —
a single TCP connection is reused across all ~50+ backend calls per session.
Call close() from the on_session_end hook to release the connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

import httpx

from darwin_tools.telemetry_queue import enqueue, flush_pending, flush_pending_sync

log = logging.getLogger(__name__)

_client: "BackendClient | None" = None

# Split timeouts: fast connect, generous read (backend may do DB work).
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
_TIMEOUT_FIRE_AND_FORGET = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)


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


async def close_backend_client() -> None:
    """Shut down the persistent HTTP connection. Safe to call multiple times."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


_CLOSE_TIMEOUT_SEC = 30.0


def close_backend_client_sync() -> None:
    """Close the singleton client from synchronous code (Hermes plugin hooks).

    Hooks run outside ``asyncio`` — they cannot ``await`` async closers. When the
    process already has a running loop, completion runs in a helper thread.
    """
    global _client
    if _client is None:
        return
    c = _client
    _client = None

    async def _run() -> None:
        await c.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run())
        return

    outcome: dict[str, BaseException] = {}
    done = threading.Event()

    def _thread_runner() -> None:
        try:
            asyncio.run(_run())
        except BaseException as exc:
            outcome["exc"] = exc
        finally:
            done.set()

    threading.Thread(
        target=_thread_runner,
        name="darwin-backend-close",
        daemon=True,
    ).start()
    if not done.wait(timeout=_CLOSE_TIMEOUT_SEC):
        log.warning(
            "close_backend_client_sync timed out after %.0fs", _CLOSE_TIMEOUT_SEC
        )
    elif "exc" in outcome:
        log.warning("close_backend_client_sync failed: %s", outcome["exc"])


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
        # Persistent client — one TCP connection pool for the container's lifetime
        self._http = httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers=self._headers,
        )
        # Hermes invokes plugin hooks synchronously — telemetry uses this instead of AsyncClient.
        self._http_sync: httpx.Client | None = None

    def _sync_client(self) -> httpx.Client:
        if self._http_sync is None:
            self._http_sync = httpx.Client(timeout=_TIMEOUT, headers=self._headers)
        return self._http_sync

    def post_sync(self, path: str, body: dict) -> None:
        """Fire-and-forget telemetry from sync hooks (same semantics as ``post_async``)."""
        try:
            resp = self._sync_client().post(
                self.base + path,
                json=body,
                timeout=_TIMEOUT_FIRE_AND_FORGET,
            )
            self._check_revocation(resp)
            resp.raise_for_status()
        except SystemExit:
            raise
        except Exception:
            enqueue(path, body)

    def post_sync_deliver(self, path: str, body: dict) -> None:
        """Strict sync POST for queue replay — raises on failure."""
        resp = self._sync_client().post(
            self.base + path,
            json=body,
            timeout=_TIMEOUT,
        )
        self._check_revocation(resp)
        resp.raise_for_status()

    def flush_queued_telemetry_sync(self) -> int:
        return flush_pending_sync(self)

    async def close(self) -> None:
        """Release the underlying connection pool."""
        if self._http_sync is not None:
            try:
                self._http_sync.close()
            except Exception:
                pass
            self._http_sync = None
        await self._http.aclose()

    async def get(self, path: str, params: dict | None = None) -> dict:
        resp = await self._http.get(self.base + path, params=params)
        self._check_revocation(resp)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, body: dict) -> dict:
        resp = await self._http.post(self.base + path, json=body)
        self._check_revocation(resp)
        resp.raise_for_status()
        return resp.json()

    async def post_async(self, path: str, body: dict) -> None:
        """Fire-and-forget — enqueue on failure for later flush."""
        try:
            resp = await self._http.post(self.base + path, json=body, timeout=_TIMEOUT_FIRE_AND_FORGET)
            self._check_revocation(resp)
            resp.raise_for_status()
        except SystemExit:
            raise
        except Exception:
            enqueue(path, body)

    async def flush_queued_telemetry(self) -> int:
        """Retry queued telemetry items (session start/end)."""
        return await flush_pending(self)

    async def delete(self, path: str) -> dict:
        resp = await self._http.delete(self.base + path)
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
