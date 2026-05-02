import httpx
import time


class TelemetryClient:
    def __init__(self, backend_url: str, agent_jwt: str, darwin_id: str, session_id: str):
        self.base = backend_url.rstrip("/") + "/api/v1"
        self.headers = {
            "Authorization": f"Bearer {agent_jwt}",
            "Content-Type": "application/json",
        }
        self.darwin_id = darwin_id
        self.session_id = session_id
        self._http = httpx.AsyncClient(timeout=10.0)

    async def push_event(
        self,
        event_type: str,
        payload: dict,
        neuron_id: str = None,
        parent_event_id: str = None,
        token_cost: float = 0.0,
        fiat_cost: float = 0.0,
    ) -> str | None:
        """
        Push a cognitive event to the backend → Supabase Realtime → Infinite Canvas.
        Fire-and-forget — does not block the agent if the backend is slow.
        """
        body = {
            "darwin_id": self.darwin_id,
            "session_id": self.session_id,
            "neuron_id": neuron_id,
            "parent_event_id": parent_event_id,
            "event_type": event_type,
            "payload": payload,
            "timestamp_ms": int(time.time() * 1000),
            "token_cost": token_cost,
            "fiat_cost": fiat_cost,
        }
        try:
            resp = await self._http.post(
                f"{self.base}/telemetry/stream-event",
                json=body,
                headers=self.headers,
            )
            return resp.json().get("event_id")
        except Exception:
            return None

    async def push_metrics(self, metrics: dict) -> None:
        """Push a metabolic metrics batch — called by the heartbeat thread."""
        try:
            await self._http.post(
                f"{self.base}/telemetry/stream",
                json={
                    "darwin_id": self.darwin_id,
                    "session_id": self.session_id,
                    "metrics": metrics,
                },
                headers=self.headers,
            )
        except Exception:
            pass

    async def push_entity(
        self,
        entity_type: str,
        entity_id: str,
        data: dict,
        operation: str,
    ) -> None:
        try:
            await self._http.post(
                f"{self.base}/telemetry/entity",
                json={
                    "darwin_id": self.darwin_id,
                    "session_id": self.session_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "data": data,
                    "operation": operation,
                },
                headers=self.headers,
            )
        except Exception:
            pass

    async def close(self):
        await self._http.aclose()
