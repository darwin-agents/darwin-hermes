"""
Unit tests for darwin_tools/lifecycle.py

Invariants checked:
- hibernate() always raises HibernateSignal, never returns
- HibernateSignal carries all fields passed by the caller
- check_kill_signal() returns False when no key is present
- check_kill_signal() returns True when kill:{darwin_id} key exists in Redis
"""
import pytest
import fakeredis.aioredis

from darwin_tools.lifecycle import HibernateSignal, build_lifecycle_tools


# ---------------------------------------------------------------------------
# HibernateSignal construction
# ---------------------------------------------------------------------------

def test_hibernate_signal_stores_all_fields():
    sig = HibernateSignal(
        reason="waiting for stripe",
        wake_after_hours=2.5,
        wake_on_webhook="wh_abc",
        keep_browser_session="sess_xyz",
    )
    assert sig.reason == "waiting for stripe"
    assert sig.wake_after_hours == 2.5
    assert sig.wake_on_webhook == "wh_abc"
    assert sig.keep_browser_session == "sess_xyz"


def test_hibernate_signal_optional_fields_default_none():
    sig = HibernateSignal(reason="idle")
    assert sig.wake_after_hours is None
    assert sig.wake_on_webhook is None
    assert sig.keep_browser_session is None


# ---------------------------------------------------------------------------
# hibernate() tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hibernate_raises_signal(settings, db, telemetry):
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)

    with pytest.raises(HibernateSignal) as exc_info:
        await hibernate(reason="test shutdown", wake_after_hours=1.0)

    sig = exc_info.value
    assert sig.reason == "test shutdown"
    assert sig.wake_after_hours == 1.0
    assert sig.wake_on_webhook is None


@pytest.mark.asyncio
async def test_hibernate_never_returns(settings, db, telemetry):
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)

    raised = False
    try:
        await hibernate(reason="test")
    except HibernateSignal:
        raised = True

    assert raised, "hibernate() must always raise HibernateSignal"


@pytest.mark.asyncio
async def test_hibernate_with_webhook_key(settings, db, telemetry):
    [hibernate, _] = build_lifecycle_tools(settings, db, telemetry)

    with pytest.raises(HibernateSignal) as exc_info:
        await hibernate(
            reason="waiting for email",
            wake_on_webhook="hook_email_123",
            keep_browser_session="bb_sess_456",
        )

    sig = exc_info.value
    assert sig.wake_on_webhook == "hook_email_123"
    assert sig.keep_browser_session == "bb_sess_456"
    assert sig.wake_after_hours is None


# ---------------------------------------------------------------------------
# check_kill_signal()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_kill_signal_returns_false_when_no_key(settings, db, telemetry, monkeypatch):
    fake_redis = fakeredis.aioredis.FakeRedis()

    async def fake_from_url(url):
        return fake_redis

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", fake_from_url)

    [_, check_kill_signal] = build_lifecycle_tools(settings, db, telemetry)
    result = await check_kill_signal()
    assert result is False


@pytest.mark.asyncio
async def test_check_kill_signal_returns_true_when_key_present(settings, db, telemetry, monkeypatch):
    fake_redis = fakeredis.aioredis.FakeRedis()
    await fake_redis.set(f"kill:{settings.darwin_id}", "1")

    async def fake_from_url(url):
        return fake_redis

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", fake_from_url)

    [_, check_kill_signal] = build_lifecycle_tools(settings, db, telemetry)
    result = await check_kill_signal()
    assert result is True
