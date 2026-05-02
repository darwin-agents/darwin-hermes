import threading
import time
import asyncio


class HeartbeatThread(threading.Thread):
    def __init__(self, darwin_id: str, agent_jwt: str, backend_url: str, telemetry):
        super().__init__(daemon=True)
        self.darwin_id = darwin_id
        self.agent_jwt = agent_jwt
        self.backend_url = backend_url
        self.telemetry = telemetry
        self._stop_event = threading.Event()
        self._session_start = time.time()
        self._tokens_this_session = 0
        self._cost_this_session = 0.0
        self._failed_requests = 0

    def run(self):
        import redis
        from config import Settings

        # settings are already in scope via env vars at this point
        import os
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        r = redis.from_url(redis_url)

        while not self._stop_event.is_set():
            try:
                r.set(f"heartbeat:{self.darwin_id}", str(time.time()), ex=30)

                if r.get(f"kill:{self.darwin_id}"):
                    import _thread
                    _thread.interrupt_main()
                    break

                asyncio.run(self.telemetry.push_metrics({
                    "tokens_burned_last_30s": self._tokens_this_session,
                    "llm_cost_last_30s": self._cost_this_session,
                    "compute_cost_last_30s": 0.002,
                    "failed_http_requests_last_30s": self._failed_requests,
                    "memory_util_mb": self._get_memory_mb(),
                    "current_task": "Active",
                }))

                self._tokens_this_session = 0
                self._cost_this_session = 0.0
                self._failed_requests = 0

            except Exception:
                pass

            self._stop_event.wait(15)

        r.close()

    def record_tokens(self, input_tokens: int, output_tokens: int, cost: float):
        self._tokens_this_session += input_tokens + output_tokens
        self._cost_this_session += cost

    def record_failed_request(self):
        self._failed_requests += 1

    def stop(self):
        self._stop_event.set()

    def _get_memory_mb(self) -> int:
        try:
            import psutil
            import os
            return int(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024)
        except Exception:
            return 0
