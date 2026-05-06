"""
Unit tests for darwin_tools/lifecycle.py

Invariants:
- hibernate() always raises DarwinHibernateSignal with correct fields
- request_approval returns a webhook_key and sends a notification
"""

import pytest

from darwin_tools.lifecycle import (
    DarwinHibernateSignal,
    register_lifecycle_tools,
)


@pytest.fixture
def tools(ctx):
    register_lifecycle_tools(ctx)
    return {name: handler for name, (schema, handler) in ctx.tools.items()}


# ---------------------------------------------------------------------------
# DarwinHibernateSignal construction
# ---------------------------------------------------------------------------

def test_signal_is_base_exception_not_exception():
    """DarwinHibernateSignal must NOT be caught by bare ``except Exception`` handlers
    (e.g. tools/registry.py dispatch).  It is a control-flow signal, not an error."""
    sig = DarwinHibernateSignal(reason="idle")
    assert isinstance(sig, BaseException)
    assert not isinstance(sig, Exception), (
        "DarwinHibernateSignal must inherit from BaseException, not Exception, "
        "so broad except-Exception handlers in the tool registry do not swallow it"
    )


def test_signal_not_swallowed_by_except_exception():
    """Simulate the registry dispatch pattern — signal must escape."""
    def registry_dispatch():
        try:
            raise DarwinHibernateSignal(reason="hibernating")
        except Exception:
            return "swallowed"   # this must NOT happen

    with pytest.raises(DarwinHibernateSignal):
        registry_dispatch()


def test_signal_stores_fields():
    sig = DarwinHibernateSignal(
        reason="waiting for stripe",
        wake_after_hours=2.5,
        wake_on_webhook="wh_abc",
        keep_browser_session="sess_xyz",
    )
    assert sig.reason == "waiting for stripe"
    assert sig.wake_after_hours == 2.5
    assert sig.wake_on_webhook == "wh_abc"
    assert sig.keep_browser_session == "sess_xyz"


def test_signal_optional_fields_default_none():
    sig = DarwinHibernateSignal(reason="idle")
    assert sig.wake_after_hours is None
    assert sig.wake_on_webhook is None
    assert sig.keep_browser_session is None


# ---------------------------------------------------------------------------
# hibernate tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hibernate_raises_signal(tools):
    with pytest.raises(DarwinHibernateSignal) as exc_info:
        await tools["hibernate"]({"reason": "test", "wake_after_hours": 1.0})
    assert exc_info.value.reason == "test"
    assert exc_info.value.wake_after_hours == 1.0


@pytest.mark.asyncio
async def test_hibernate_never_returns(tools):
    raised = False
    try:
        await tools["hibernate"]({"reason": "test"})
    except DarwinHibernateSignal:
        raised = True
    assert raised


@pytest.mark.asyncio
async def test_hibernate_with_webhook(tools):
    with pytest.raises(DarwinHibernateSignal) as exc_info:
        await tools["hibernate"]({
            "reason": "waiting for email",
            "wake_on_webhook": "hook_123",
            "keep_browser_session": "bb_456",
        })
    sig = exc_info.value
    assert sig.wake_on_webhook == "hook_123"
    assert sig.keep_browser_session == "bb_456"
    assert sig.wake_after_hours is None


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_approval_returns_webhook_key(tools, backend_mock):
    result = await tools["request_approval"]({
        "amount": 50.00,
        "justification": "Facebook ads for pet niche — expected 300% ROI",
    })
    assert "webhook_key" in result
    assert result["webhook_key"].startswith("approval_test-darwin-01_")


@pytest.mark.asyncio
async def test_request_approval_posts_notification(tools, backend_mock):
    import json
    sent = []
    async def capture(request, route):
        sent.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"ok": True})
    backend_mock.post("/api/v1/notifications/send").mock(side_effect=capture)

    await tools["request_approval"]({"amount": 25.00, "justification": "test"})
    assert len(sent) == 1
    assert "25.00" in sent[0]["message"]
    assert sent[0]["action_type"] == "APPROVAL"


@pytest.mark.asyncio
async def test_request_approval_includes_webhook_key_in_payload(tools, backend_mock):
    """B2 fix: webhook_key must be sent to the backend so it can route the approval."""
    import json
    sent = []
    async def capture(request, route):
        sent.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"ok": True})
    backend_mock.post("/api/v1/notifications/send").mock(side_effect=capture)

    result = await tools["request_approval"]({"amount": 30.00, "justification": "test"})
    webhook_key = result["webhook_key"]

    assert "webhook_key" in sent[0], "webhook_key missing from notification payload"
    assert sent[0]["webhook_key"] == webhook_key

