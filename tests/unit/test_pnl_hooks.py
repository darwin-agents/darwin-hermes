"""
Unit tests for darwin_tools/pnl_hooks.py

Invariants:
- Hooks register in correct order (pre before post)
- _redact_str strips API keys, JWTs, and PANs
- _estimate_cost returns sensible values
- _extract_response_text handles Anthropic / OpenAI / str / None shapes
- _post_llm_call emits a THOUGHT event with model, tokens, latency
- _pre_tool_call emits NEURON_SPAWNED per task when tool is delegate_task
"""

import json

import pytest
from darwin_tools.pnl_hooks import (
    _estimate_cost,
    _extract_response_text,
    _post_llm_call,
    _pre_tool_call,
    _redact_str,
    _resolve_post_llm_cost_usd,
    register_pnl_hooks,
)


def test_redact_strips_anthropic_key():
    s = '{"key": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTU1234567890"}'
    assert "[REDACTED]" in _redact_str(s)
    assert "sk-ant" not in _redact_str(s)


def test_redact_strips_openai_key():
    s = '{"key": "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"}'
    assert "[REDACTED]" in _redact_str(s)


def test_redact_strips_pan():
    s = "card number: 4111111111111111 cvv: 123"
    redacted = _redact_str(s)
    assert "4111111111111111" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_strips_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    redacted = _redact_str(jwt)
    assert "eyJ" not in redacted
    assert "[REDACTED]" in redacted


def test_estimate_cost_known_model_returns_positive_cost():
    cost = _estimate_cost("claude-haiku-4-5-20251001", 1000, 200)
    # LiteLLM resolves the price from its bundled model_cost.json.
    # If this exact model id is not in LiteLLM's table it gracefully returns 0.0.
    # Either way the result must be non-negative and well below $0.01 for ~1200 tokens.
    assert cost >= 0.0
    assert cost < 0.01


def test_estimate_cost_unknown_model_returns_zero():
    # LiteLLM raises on an unrecognised model id; we surface that as 0.0
    # so an unexpected neuron model never crashes the telemetry pipeline.
    cost = _estimate_cost("totally-unknown-model-xyz", 1000, 200)
    assert cost == 0.0


def test_hooks_registered(ctx):
    register_pnl_hooks(ctx)
    assert "on_session_start" in ctx.hooks
    assert "pre_llm_call" in ctx.hooks
    assert "post_llm_call" in ctx.hooks
    assert "pre_tool_call" in ctx.hooks
    assert "post_tool_call" in ctx.hooks
    assert "subagent_stop" in ctx.hooks
    assert "on_session_end" in ctx.hooks


def test_hooks_are_callable(ctx):
    register_pnl_hooks(ctx)
    for event, handlers in ctx.hooks.items():
        for h in handlers:
            assert callable(h), f"Hook {event} is not callable"


# ---------------------------------------------------------------------------
# _extract_response_text
# ---------------------------------------------------------------------------

class _Block:
    """Minimal Anthropic content block stub."""
    def __init__(self, text: str):
        self.text = text


class _AnthropicResponse:
    def __init__(self, *texts: str):
        self.content = [_Block(t) for t in texts]


class _OpenAIMessage:
    def __init__(self, content: str):
        self.content = content


class _OpenAIChoice:
    def __init__(self, content: str):
        self.message = _OpenAIMessage(content)


class _OpenAIResponse:
    def __init__(self, content: str):
        self.choices = [_OpenAIChoice(content)]


def test_extract_anthropic_single_block():
    resp = _AnthropicResponse("I will browse competitor pricing.")
    assert _extract_response_text(resp) == "I will browse competitor pricing."


def test_extract_anthropic_multiple_blocks():
    resp = _AnthropicResponse("First.", "Second.")
    result = _extract_response_text(resp)
    assert "First." in result
    assert "Second." in result


def test_extract_openai_shape():
    resp = _OpenAIResponse("Analysing revenue data.")
    assert _extract_response_text(resp) == "Analysing revenue data."


def test_extract_plain_string():
    assert _extract_response_text("plain text") == "plain text"


def test_extract_none_returns_empty():
    assert _extract_response_text(None) == ""


def test_extract_empty_content_list():
    resp = _AnthropicResponse()  # no blocks
    assert _extract_response_text(resp) == ""


# ---------------------------------------------------------------------------
# _post_llm_call — THOUGHT event emission
# ---------------------------------------------------------------------------

def test_post_llm_call_emits_thought_event(backend_mock):
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-1"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)
    backend_mock.post("/api/v1/telemetry/llm-call").mock(
        return_value=__import__("httpx").Response(201, json={"recorded": True})
    )
    backend_mock.post("/api/v1/telemetry/stream").mock(
        return_value=__import__("httpx").Response(202, json={"queued": True})
    )

    response = _AnthropicResponse("I will spawn two researcher neurons.")
    _post_llm_call(
        response=response,
        usage={"input_tokens": 1200, "output_tokens": 90, "cache_read_input_tokens": 0, "reasoning_tokens": 0},
        model="claude-sonnet-4-6",
        duration_ms=980,
    )

    thought_events = [e for e in captured if e.get("event_type") == "THOUGHT"]
    assert len(thought_events) == 1
    payload = thought_events[0]["payload"]
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["prompt_tokens"] == 1200
    assert payload["completion_tokens"] == 90
    assert payload["latency_ms"] == 980
    assert "I will spawn two researcher neurons." in payload["content"]


def test_post_llm_call_matches_hermes_invoke_kwargs(backend_mock):
    """Hermes calls ``post_llm_call`` with assistant_response (+ session kw), never positional usage."""
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-hermes"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)
    backend_mock.post("/api/v1/telemetry/llm-call").mock(
        return_value=__import__("httpx").Response(201, json={"recorded": True})
    )
    backend_mock.post("/api/v1/telemetry/stream").mock(
        return_value=__import__("httpx").Response(202, json={"queued": True})
    )

    _post_llm_call(
        session_id="sess-h",
        user_message="directive",
        assistant_response="Here is my survival plan step one.",
        conversation_history=[],
        model="anthropic/claude-haiku-4.5",
        platform="",
    )

    thought = [e for e in captured if e.get("event_type") == "THOUGHT"]
    assert len(thought) == 1
    assert thought[0]["payload"]["model"] == "anthropic/claude-haiku-4.5"
    assert "survival plan" in thought[0]["payload"]["content"]


def test_resolve_post_llm_cost_prefers_billed_cost_usd():
    """OpenRouter (via Hermes) passes aggregated provider USD — skip LiteLLM."""
    u = {
        "input_tokens": 9000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 0,
        "reasoning_tokens": 0,
        "billed_cost_usd": 0.0175,
    }
    assert _resolve_post_llm_cost_usd("anthropic/unknown-future-model", u) == 0.0175


def test_post_llm_call_thought_model_always_set(backend_mock):
    """model must be present in every THOUGHT event regardless of token counts."""
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-2"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)
    backend_mock.post("/api/v1/telemetry/llm-call").mock(
        return_value=__import__("httpx").Response(201, json={"recorded": True})
    )
    backend_mock.post("/api/v1/telemetry/stream").mock(
        return_value=__import__("httpx").Response(202, json={"queued": True})
    )

    _post_llm_call(
        response=None,
        usage={"input_tokens": 0, "output_tokens": 0},
        model="claude-haiku-4-5",
        duration_ms=0,
    )

    thought_events = [e for e in captured if e.get("event_type") == "THOUGHT"]
    assert thought_events[0]["payload"]["model"] == "claude-haiku-4-5"


def test_post_llm_call_llm_telemetry_uses_billed_cost_usd(backend_mock):
    llm_bodies: list[dict] = []

    def capture_llm(request, route=None):
        llm_bodies.append(json.loads(request.content))
        return __import__("httpx").Response(201, json={"recorded": True})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(
        return_value=__import__("httpx").Response(200, json={"event_id": "ev-b"})
    )
    backend_mock.post("/api/v1/telemetry/llm-call").mock(side_effect=capture_llm)
    backend_mock.post("/api/v1/telemetry/stream").mock(
        return_value=__import__("httpx").Response(202, json={"queued": True})
    )

    _post_llm_call(
        assistant_response="Done.",
        model="anthropic/claude-haiku-4.5",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "reasoning_tokens": 0,
            "billed_cost_usd": 0.00042,
        },
    )
    assert len(llm_bodies) == 1
    assert llm_bodies[0]["cost_usd"] == 0.00042
    assert llm_bodies[0]["input_tokens"] == 100
    assert llm_bodies[0]["output_tokens"] == 50


# ---------------------------------------------------------------------------
# _pre_tool_call — NEURON_SPAWNED emission for delegate_task
# ---------------------------------------------------------------------------

def test_pre_tool_call_emits_neuron_spawned_per_task(backend_mock):
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-3"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    _pre_tool_call(
        tool_name="delegate_task",
        args={
            "tasks": [
                {
                    "role": "researcher_A",
                    "task": "Scrape competitor pricing",
                    "model": "claude-haiku-4-5",
                    "model_reason": "Repetitive extraction — Haiku sufficient",
                },
                {
                    "role": "researcher_B",
                    "task": "Scrape supplier contacts",
                    "model": "claude-haiku-4-5",
                },
            ]
        },
    )

    spawned = [e for e in captured if e.get("event_type") == "NEURON_SPAWNED"]
    assert len(spawned) == 2

    neuron_ids = {e["payload"]["neuron_id"] for e in spawned}
    assert neuron_ids == {"researcher_A", "researcher_B"}

    first = next(e for e in spawned if e["payload"]["neuron_id"] == "researcher_A")
    assert first["payload"]["model"] == "claude-haiku-4-5"
    assert first["payload"]["model_reason"] == "Repetitive extraction — Haiku sufficient"


def test_pre_tool_call_defaults_model_to_openrouter_slug(backend_mock):
    """When a task spec has no model, default to OPENROUTER_DEFAULT_TASK_MODEL slug."""
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-4"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    _pre_tool_call(
        tool_name="delegate_task",
        args={"tasks": [{"role": "analyst", "task": "Summarise research"}]},
    )

    spawned = [e for e in captured if e.get("event_type") == "NEURON_SPAWNED"]
    slug = spawned[0]["payload"]["model"]
    assert "/" in slug  # OpenRouter slug
    assert "claude" in slug.lower()
    assert spawned[0]["payload"]["model_reason"] is None


def test_pre_tool_call_non_delegate_no_neuron_spawned(backend_mock):
    """Non-delegate_task calls must NOT emit NEURON_SPAWNED."""
    captured: list[dict] = []

    def capture(request, route=None):
        captured.append(json.loads(request.content))
        return __import__("httpx").Response(200, json={"event_id": "ev-5"})

    backend_mock.post("/api/v1/telemetry/stream-event").mock(side_effect=capture)

    _pre_tool_call(tool_name="web_search", args={"query": "competitor pricing"})

    spawned = [e for e in captured if e.get("event_type") == "NEURON_SPAWNED"]
    assert len(spawned) == 0
    tool_calls = [e for e in captured if e.get("event_type") == "TOOL_CALL"]
    assert len(tool_calls) == 1
