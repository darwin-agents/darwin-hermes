"""
Unit tests for pnl_hooks — session lifecycle hooks and _model_to_provider.

Fills the gaps identified in the audit:
  T1: _on_session_start → AGENT_WOKE event + wake_prompt from env
  T2: _on_session_end → SESSION_ENDED event + running totals
  T3: _subagent_stop → NEURON_COMPLETED / NEURON_FAILED events
  T4: _model_to_provider → provider detection logic
"""

import json
import os
import pytest

from darwin_tools.pnl_hooks import (
    _model_to_provider,
    _on_session_end,
    _on_session_start,
    _post_llm_call,
    _subagent_stop,
    _redact_str,
)


# ---------------------------------------------------------------------------
# T4: _model_to_provider
# ---------------------------------------------------------------------------

class TestModelToProvider:
    def test_anthropic_native(self):
        assert _model_to_provider("claude-opus-4-7") == "anthropic"
        assert _model_to_provider("claude-sonnet-4-6") == "anthropic"
        assert _model_to_provider("claude-haiku-4-5") == "anthropic"

    def test_openai_native(self):
        assert _model_to_provider("gpt-4o") == "openai"
        assert _model_to_provider("gpt-4o-mini") == "openai"
        assert _model_to_provider("o1-preview") == "openai"
        assert _model_to_provider("o3-mini") == "openai"
        assert _model_to_provider("o4-mini") == "openai"

    def test_openrouter_prefixed_known(self):
        assert _model_to_provider("anthropic/claude-opus-4-7") == "anthropic"
        assert _model_to_provider("google/gemini-3-flash") == "google"
        assert _model_to_provider("openai/gpt-4o") == "openai"
        assert _model_to_provider("meta/llama-3.1-70b") == "meta"
        assert _model_to_provider("mistral/mixtral-8x7b") == "mistral"
        assert _model_to_provider("cohere/command-r-plus") == "cohere"

    def test_openrouter_prefixed_unknown(self):
        assert _model_to_provider("perplexity/sonar-medium") == "openrouter"
        assert _model_to_provider("deepseek/deepseek-chat") == "openrouter"

    def test_unknown_bare_model(self):
        assert _model_to_provider("llama-3.1-70b") == "openrouter"
        assert _model_to_provider("some-custom-model") == "openrouter"


# ---------------------------------------------------------------------------
# T1: _on_session_start — AGENT_WOKE event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_session_start_emits_agent_woke(backend_mock):
    captured: list[dict] = []

    async def capture(request, route):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-start"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    await _on_session_start()

    woke = [e for e in captured if e.get("event_type") == "AGENT_WOKE"]
    assert len(woke) == 1
    assert woke[0]["darwin_id"] == "test-darwin-01"
    assert woke[0]["session_id"] == "test-session-01"


@pytest.mark.asyncio
async def test_on_session_start_reads_wake_prompt_from_env(backend_mock):
    """wake_reason must come from DARWIN_WAKE_PROMPT env var, not kwargs."""
    captured: list[dict] = []

    async def capture(request, route):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-start"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    # The env var is set in conftest.py to "Test wake prompt"
    await _on_session_start()

    woke = [e for e in captured if e.get("event_type") == "AGENT_WOKE"]
    assert woke[0]["payload"]["wake_reason"] == "Test wake prompt"


@pytest.mark.asyncio
async def test_on_session_start_resets_running_totals(backend_mock):
    """Session start must zero the running totals."""
    import darwin_tools.pnl_hooks as hooks

    # Simulate accumulated state from a previous test
    hooks._token_running_total = 99999
    hooks._cost_running_total = 99.99

    await _on_session_start()

    assert hooks._token_running_total == 0
    assert hooks._cost_running_total == 0.0


# ---------------------------------------------------------------------------
# T2: _on_session_end — SESSION_ENDED event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_session_end_emits_session_ended(backend_mock):
    captured: list[dict] = []

    async def capture(request, route):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-end"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    # Simulate session state
    import darwin_tools.pnl_hooks as hooks
    import time
    hooks._session_start = time.time() - 120  # 2 minutes ago
    hooks._token_running_total = 5000
    hooks._cost_running_total = 0.042

    await _on_session_end()

    ended = [e for e in captured if e.get("event_type") == "SESSION_ENDED"]
    assert len(ended) == 1
    payload = ended[0]["payload"]
    assert payload["total_tokens"] == 5000
    assert payload["total_cost_usd"] == 0.042
    assert payload["duration_seconds"] >= 119  # ~120s, allow small jitter


@pytest.mark.asyncio
async def test_on_session_end_does_not_crash_on_backend_failure(backend_mock):
    """SESSION_ENDED uses post_async — backend failures must not raise."""
    backend_mock.post("/api/v1/telemetry/stream-event").mock(
        side_effect=__import__("httpx").ConnectError("backend down")
    )

    import darwin_tools.pnl_hooks as hooks
    import time
    hooks._session_start = time.time()

    # Must not raise
    await _on_session_end()


# ---------------------------------------------------------------------------
# T3: _subagent_stop — NEURON_COMPLETED / NEURON_FAILED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subagent_stop_completed(backend_mock):
    captured: list[dict] = []

    async def capture(request, route):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-sub"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    await _subagent_stop(
        parent_session_id="sess-parent",
        child_role="researcher_A",
        child_status="completed",
        duration_ms=3400,
    )

    events = [e for e in captured if e.get("event_type") == "NEURON_COMPLETED"]
    assert len(events) == 1
    assert events[0]["payload"]["child_role"] == "researcher_A"
    assert events[0]["payload"]["duration_ms"] == 3400


@pytest.mark.asyncio
async def test_subagent_stop_failed(backend_mock):
    captured: list[dict] = []

    async def capture(request, route):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-sub"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    await _subagent_stop(
        parent_session_id="sess-parent",
        child_role="scraper_B",
        child_status="failed",
        duration_ms=1200,
    )

    events = [e for e in captured if e.get("event_type") == "NEURON_FAILED"]
    assert len(events) == 1
    assert events[0]["payload"]["child_role"] == "scraper_B"


# ---------------------------------------------------------------------------
# S2: Extended redaction patterns
# ---------------------------------------------------------------------------

def test_redact_browserbase_key():
    s = 'key: bb_live_abc123def456ghi789'
    assert "bb_live_" not in _redact_str(s)
    assert "[REDACTED]" in _redact_str(s)


def test_redact_browser_use_key_live_prefix():
    s = 'key: bu_live_xyz987wvu654tsr321'
    assert "bu_live_" not in _redact_str(s)
    assert "[REDACTED]" in _redact_str(s)


def test_redact_browser_use_key_real_format():
    """Real Browser-use keys use bu_<segment>_<token>, not bu_live_."""
    s = 'key: bu_y1j0_zYMRPYKYc3v3VEp77Hj5MgquWYgx1wKOB670Ts'
    assert "bu_y1j0_" not in _redact_str(s)
    assert "[REDACTED]" in _redact_str(s)


def test_redact_modal_token():
    s = 'token: ak-abcdefghij1234567890'
    assert "ak-abcdefghij" not in _redact_str(s)
    assert "[REDACTED]" in _redact_str(s)


def test_redact_langfuse_key():
    s = 'key: pk-lf-abc123def456ghi789'
    assert "pk-lf-" not in _redact_str(s)
    assert "[REDACTED]" in _redact_str(s)
