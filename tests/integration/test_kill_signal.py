"""
Integration tests — kill signal and heartbeat (telemetry/heartbeat.py)

Invariants checked:
- check_kill_signal() returns True when kill:{darwin_id} key exists in Redis
- HeartbeatThread writes heartbeat key to Redis
- HeartbeatThread respects stop() cleanly
- record_tokens() accumulates token counts between flushes
"""
import time
import threading
import pytest
import fakeredis
import fakeredis.aioredis

from darwin_tools.lifecycle import build_lifecycle_tools
from telemetry.heartbeat import HeartbeatThread


# ---------------------------------------------------------------------------
# check_kill_signal via lifecycle tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kill_signal_absent_returns_false(settings, db, telemetry, monkeypatch):
    fake_redis = fakeredis.aioredis.FakeRedis()

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda url: fake_redis)

    [_, check_kill_signal] = build_lifecycle_tools(settings, db, telemetry)
    assert await check_kill_signal() is False


@pytest.mark.asyncio
async def test_kill_signal_present_returns_true(settings, db, telemetry, monkeypatch):
    fake_redis = fakeredis.aioredis.FakeRedis()
    await fake_redis.set("kill:test-darwin-01", "1")

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda url: fake_redis)

    [_, check_kill_signal] = build_lifecycle_tools(settings, db, telemetry)
    assert await check_kill_signal() is True


# ---------------------------------------------------------------------------
# HeartbeatThread
# ---------------------------------------------------------------------------

def test_heartbeat_writes_key_to_redis(settings, telemetry, monkeypatch):
    """HeartbeatThread must write heartbeat:{darwin_id} with a 30s TTL."""
    fake_redis = fakeredis.FakeRedis()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setattr("redis.from_url", lambda url: fake_redis)

    hb = HeartbeatThread(
        darwin_id=settings.darwin_id,
        agent_jwt=settings.agent_jwt,
        backend_url=settings.backend_url,
        telemetry=telemetry,
    )
    hb.start()
    time.sleep(0.3)
    hb.stop()
    hb.join(timeout=2)

    key = f"heartbeat:{settings.darwin_id}".encode()
    value = fake_redis.get(key)
    assert value is not None, "heartbeat key must be written to Redis"


def test_heartbeat_stops_cleanly(settings, telemetry, monkeypatch):
    """stop() must terminate the thread within 2 seconds."""
    fake_redis = fakeredis.FakeRedis()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setattr("redis.from_url", lambda url: fake_redis)

    hb = HeartbeatThread(
        darwin_id=settings.darwin_id,
        agent_jwt=settings.agent_jwt,
        backend_url=settings.backend_url,
        telemetry=telemetry,
    )
    hb.start()
    time.sleep(0.1)
    hb.stop()
    hb.join(timeout=2)

    assert not hb.is_alive(), "HeartbeatThread must terminate after stop()"


def test_heartbeat_record_tokens_accumulates(settings, telemetry, monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setattr("redis.from_url", lambda url: fake_redis)

    hb = HeartbeatThread(
        darwin_id=settings.darwin_id,
        agent_jwt=settings.agent_jwt,
        backend_url=settings.backend_url,
        telemetry=telemetry,
    )

    hb.record_tokens(input_tokens=500, output_tokens=250, cost=0.003)
    hb.record_tokens(input_tokens=100, output_tokens=50, cost=0.001)

    assert hb._tokens_this_session == 900
    assert abs(hb._cost_this_session - 0.004) < 1e-9


def test_heartbeat_record_failed_request_increments(settings, telemetry, monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setattr("redis.from_url", lambda url: fake_redis)

    hb = HeartbeatThread(
        darwin_id=settings.darwin_id,
        agent_jwt=settings.agent_jwt,
        backend_url=settings.backend_url,
        telemetry=telemetry,
    )

    hb.record_failed_request()
    hb.record_failed_request()
    assert hb._failed_requests == 2
