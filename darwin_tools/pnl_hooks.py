"""
P&L Hooks — darwin_tools/pnl_hooks.py

Registered with Hermes via ctx.register_hook() in __init__.register().
Hermes fires these on every cognitive event — we forward the metrics to
the God Layer for the Token ROI dashboard and Infinite Canvas.

Why this replaces the old HeartbeatThread + TelemetryClient:
  - Hermes' Modal terminal backend already hard-times containers at 15 min.
  - The God Layer detects crashes by absence of post_llm_call events for >120s.
  - Kill signals work via JWT revocation — next backend call fails.
  - No background thread needed; no Redis poll needed.
"""

from __future__ import annotations

import re
import json
import time

_session_start: float | None = None
_token_running_total = 0
_cost_running_total = 0.0


def register_pnl_hooks(ctx) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call",     _pre_llm_call)
    ctx.register_hook("post_llm_call",    _post_llm_call)
    ctx.register_hook("pre_tool_call",    _pre_tool_call)
    ctx.register_hook("post_tool_call",   _post_tool_call)
    ctx.register_hook("subagent_stop",    _subagent_stop)
    ctx.register_hook("on_session_end",   _on_session_end)


async def _on_session_start(**kwargs):
    global _session_start, _token_running_total, _cost_running_total
    _session_start = time.time()
    _token_running_total = 0
    _cost_running_total = 0.0

    from .backend_client import get_backend_client
    client = get_backend_client()
    await client.post_async("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "AGENT_WOKE",
        "payload": {
            "wake_reason": kwargs.get("wake_prompt"),
            "session_id": client.session_id,
        },
        "timestamp_ms": int(time.time() * 1000),
    })


async def _pre_llm_call(messages, model, **kwargs):
    """Stamp the start so post_llm_call can measure latency."""
    pass  # Langfuse handles real tracing; we just need the post hook


async def _post_llm_call(response, usage, model, duration_ms=0, turn_id=None, **kwargs):
    """Record token burn to both the LLM cost tracker and the metrics stream."""
    global _token_running_total, _cost_running_total

    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    reasoning = usage.get("reasoning_tokens", 0)
    cost = _estimate_cost(model, in_tok + cache_read // 10, out_tok)
    provider = _model_to_provider(model)

    _token_running_total += in_tok + out_tok
    _cost_running_total += cost

    from .backend_client import get_backend_client
    client = get_backend_client()

    # 1. Per-LLM-call record — powers Token ROI dashboard + Mental Replay cost overlay
    await client.post_async("/telemetry/llm-call", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "model": model,
        "provider": provider,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cache_read_tokens": cache_read,
        "reasoning_tokens": reasoning,
        "cost_usd": cost,
        "duration_ms": duration_ms,
        "turn_id": turn_id,
        "timestamp_ms": int(time.time() * 1000),
    })

    # 2. Rolling metrics batch — powers the burn-rate time series chart
    await client.post_async("/telemetry/stream", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "metrics": {
            "tokens_burned_last_30s": in_tok + out_tok,
            "llm_cost_last_30s": cost,
            "compute_cost_last_30s": 0.002,
            "failed_http_requests_last_30s": 0,
            "memory_util_mb": _get_memory_mb(),
            "current_task": "Active",
        },
    })


async def _pre_tool_call(tool_name, args, **kwargs):
    from .backend_client import get_backend_client
    client = get_backend_client()
    await client.post_async("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "TOOL_CALL",
        "payload": {
            "tool_name": tool_name,
            "args_preview": _redact(args)[:300],
        },
        "timestamp_ms": int(time.time() * 1000),
    })


async def _post_tool_call(tool_name, result, duration_ms, **kwargs):
    from .backend_client import get_backend_client
    client = get_backend_client()
    await client.post_async("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "TOOL_RESULT",
        "payload": {
            "tool_name": tool_name,
            "result_preview": _redact_str(str(result))[:300],
            "duration_ms": duration_ms,
        },
        "timestamp_ms": int(time.time() * 1000),
    })


async def _subagent_stop(parent_session_id, child_role, child_status,
                         duration_ms, **kwargs):
    """Translate Hermes subagent_stop into a Darwin canvas event."""
    from .backend_client import get_backend_client
    client = get_backend_client()
    event_type = "NEURON_COMPLETED" if child_status == "completed" else "NEURON_FAILED"
    await client.post_async("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": event_type,
        "payload": {
            "child_role": child_role,
            "duration_ms": duration_ms,
        },
        "timestamp_ms": int(time.time() * 1000),
    })


async def _on_session_end(**kwargs):
    """Final flush before container exit."""
    from .backend_client import get_backend_client
    client = get_backend_client()
    await client.post("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "SESSION_ENDED",
        "payload": {
            "duration_seconds": round(time.time() - (_session_start or time.time()), 1),
            "total_tokens": _token_running_total,
            "total_cost_usd": round(_cost_running_total, 6),
        },
        "timestamp_ms": int(time.time() * 1000),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_COSTS = {
    "claude-opus-4-7":           (15.00, 75.00),
    "claude-sonnet-4-6":         (3.00,  15.00),
    "claude-haiku-4-5-20251001": (0.25,  1.25),
    "gpt-4o":                    (2.50,  10.00),
    "gpt-4o-mini":               (0.15,  0.60),
}


def _model_to_provider(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gpt") or model.startswith("o3") or model.startswith("o1"):
        return "openai"
    return "openrouter"


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp_rate, out_rate = _MODEL_COSTS.get(model, (3.00, 15.00))
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


def _redact(args: dict) -> str:
    return _redact_str(json.dumps(args, default=str))


def _redact_str(s: str) -> str:
    s = re.sub(r"sk-ant-[A-Za-z0-9\-_]{20,}", "[REDACTED]", s)
    s = re.sub(r"sk-(?:proj-|or-)[A-Za-z0-9\-_]{20,}", "[REDACTED]", s)
    s = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", s)
    s = re.sub(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+", "[REDACTED]", s)
    s = re.sub(r"\b\d{13,19}\b", "[REDACTED]", s)   # PAN
    return s


def _get_memory_mb() -> int:
    try:
        import psutil, os
        return int(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024)
    except Exception:
        return 0
