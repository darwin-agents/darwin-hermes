import time


class HibernateSignal(Exception):
    def __init__(
        self,
        reason: str,
        wake_after_hours: float = None,
        wake_on_webhook: str = None,
        keep_browser_session: str = None,
    ):
        self.reason = reason
        self.wake_after_hours = wake_after_hours
        self.wake_on_webhook = wake_on_webhook
        self.keep_browser_session = keep_browser_session


def build_lifecycle_tools(settings, db, telemetry):
    async def hibernate(
        reason: str,
        wake_after_hours: float = None,
        wake_on_webhook: str = None,
        keep_browser_session: str = None,
    ) -> None:
        """
        Suspend yourself to save compute costs while waiting for an external event.
        THIS IS A TERMINAL ACTION — your container will be destroyed after this call.
        Your SQLite database and all memory will be preserved and reloaded when you wake.

        You MUST call this when:
        - Waiting for an email reply (wake_on_webhook with your email listener key)
        - Waiting for a Stripe payment (wake_on_webhook with your Stripe listener key)
        - Waiting for human input via ask_human (wake_after_hours=0.1 as fallback timeout)
        - No immediate work to do (wake_after_hours for a scheduled check-in)

        Args:
            reason: Why you are hibernating (injected into your wake-up prompt)
            wake_after_hours: Wake up after this many hours regardless of other events
            wake_on_webhook: Webhook key that will trigger your wake-up
            keep_browser_session: Browserbase session ID to keep alive across the sleep

        This function does not return. It raises HibernateSignal caught by the boot loop.
        """
        raise HibernateSignal(
            reason=reason,
            wake_after_hours=wake_after_hours,
            wake_on_webhook=wake_on_webhook,
            keep_browser_session=keep_browser_session,
        )

    async def check_kill_signal() -> bool:
        """
        Check whether the backend has issued a kill signal for this container.
        This is called automatically by the heartbeat loop every 15 seconds.
        If True is returned, the agent must raise SystemExit immediately.

        Returns: True if kill signal is present (agent must stop), False otherwise
        """
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        signal = await r.get(f"kill:{settings.darwin_id}")
        await r.aclose()
        return signal is not None

    return [hibernate, check_kill_signal]
