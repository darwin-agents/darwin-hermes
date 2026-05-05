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

Hermes calls ``invoke_hook()`` synchronously (callbacks are not awaited).  Hook
handlers must be plain ``def`` and use ``BackendClient.post_sync`` / flush helpers,
not ``async def``.
"""

from __future__ import annotations

import logging
import os
import re
import json
import time
import uuid

log = logging.getLogger(__name__)

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


def _on_session_start(**kwargs):
    global _session_start, _token_running_total, _cost_running_total
    _session_start = time.time()
    _token_running_total = 0
    _cost_running_total = 0.0

    from .backend_client import get_backend_client
    client = get_backend_client()
    try:
        client.flush_queued_telemetry_sync()
    except Exception:
        pass
    wake_prompt = os.environ.get("DARWIN_WAKE_PROMPT", "")
    client.post_sync("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "AGENT_WOKE",
        "payload": {
            "wake_reason": wake_prompt or None,
            "session_id": client.session_id,
        },
        "timestamp_ms": int(time.time() * 1000),
    })


def _pre_llm_call(messages, model, **kwargs):
    """Stamp the start so post_llm_call can measure latency."""
    pass  # Langfuse handles real tracing; we just need the post hook


def _post_llm_call(response, usage, model, duration_ms=0, turn_id=None, **kwargs):
    """Record token burn to both the LLM cost tracker and the metrics stream."""
    global _token_running_total, _cost_running_total

    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    reasoning = usage.get("reasoning_tokens", 0)
    cost = _estimate_cost(model, in_tok, out_tok, cache_read_tokens=cache_read)
    provider = _model_to_provider(model)
    request_id = str(usage.get("request_id") or uuid.uuid4())
    in_mod = usage.get("input_modalities") or []
    out_mod = usage.get("output_modalities") or []
    if not isinstance(in_mod, list):
        in_mod = []
    if not isinstance(out_mod, list):
        out_mod = []
    image_n = int(usage.get("image_output_count", 0) or 0)
    audio_s = float(usage.get("audio_output_seconds", 0) or 0)
    video_s = float(usage.get("video_output_seconds", 0) or 0)

    _token_running_total += in_tok + out_tok
    _cost_running_total += cost

    from .backend_client import get_backend_client
    client = get_backend_client()

    # 1. THOUGHT canvas event — surfaces every cognitive turn on the Infinite Canvas
    content = _extract_response_text(response)
    client.post_sync("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "THOUGHT",
        "payload": {
            "content": content[:2000],
            "model": model,
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "latency_ms": duration_ms,
        },
        "token_cost": in_tok + out_tok,
        "fiat_cost": cost,
        "timestamp_ms": int(time.time() * 1000),
    })

    # 2. Per-LLM-call record — powers Token ROI dashboard + Mental Replay cost overlay
    client.post_sync("/telemetry/llm-call", {
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
        "request_id": request_id,
        "input_modalities": in_mod,
        "output_modalities": out_mod,
        "image_output_count": image_n,
        "audio_output_seconds": audio_s,
        "video_output_seconds": video_s,
        "timestamp_ms": int(time.time() * 1000),
    })

    # 3. Rolling metrics batch — powers the burn-rate time series chart
    client.post_sync("/telemetry/stream", {
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


def _pre_tool_call(tool_name, args, **kwargs):
    from .backend_client import get_backend_client
    client = get_backend_client()

    # Emit NEURON_SPAWNED for each task in a delegate_task call
    if tool_name == "delegate_task":
        tasks = args.get("tasks") or []
        for task in tasks:
            neuron_id = task.get("role") or task.get("neuron_id") or task.get("name", "neuron")
            client.post_sync("/telemetry/stream-event", {
                "darwin_id": client.darwin_id,
                "session_id": client.session_id,
                "event_type": "NEURON_SPAWNED",
                "neuron_id": neuron_id,
                "payload": {
                    "neuron_id": neuron_id,
                    "task": str(task.get("task") or task.get("directive") or "")[:500],
                    "parent_event_id": "",
                    "model": task.get(
                        "model",
                        os.environ.get("OPENROUTER_DEFAULT_TASK_MODEL", "anthropic/claude-haiku-4.5"),
                    ),
                    "model_reason": task.get("model_reason"),
                },
                "timestamp_ms": int(time.time() * 1000),
            })

    client.post_sync("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": "TOOL_CALL",
        "payload": {
            "tool_name": tool_name,
            "args_preview": _redact(args)[:300],
        },
        "timestamp_ms": int(time.time() * 1000),
    })


def _post_tool_call(tool_name, result, duration_ms, **kwargs):
    from .backend_client import get_backend_client
    client = get_backend_client()
    client.post_sync("/telemetry/stream-event", {
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


def _subagent_stop(parent_session_id, child_role, child_status,
                   duration_ms, **kwargs):
    """Translate Hermes subagent_stop into a Darwin canvas event."""
    from .backend_client import get_backend_client
    client = get_backend_client()
    event_type = "NEURON_COMPLETED" if child_status == "completed" else "NEURON_FAILED"
    client.post_sync("/telemetry/stream-event", {
        "darwin_id": client.darwin_id,
        "session_id": client.session_id,
        "event_type": event_type,
        "payload": {
            "child_role": child_role,
            "duration_ms": duration_ms,
        },
        "timestamp_ms": int(time.time() * 1000),
    })


def _on_session_end(**kwargs):
    """Final flush before container exit."""
    from .backend_client import get_backend_client, close_backend_client_sync
    client = get_backend_client()
    try:
        client.flush_queued_telemetry_sync()
    except Exception:
        pass
    # Fire-and-forget semantics — a telemetry failure must not prevent volume.commit()
    client.post_sync("/telemetry/stream-event", {
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
    close_backend_client_sync()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model_to_provider(model: str) -> str:
    """
    Return a provider label for telemetry.
    Handles both native ids ('claude-opus-4-7') and OpenRouter-style
    prefixed ids ('anthropic/claude-opus-4-7', 'google/gemini-3-flash').
    """
    if "/" in model:
        prefix = model.split("/")[0].lower()
        if prefix in ("anthropic", "openai", "google", "meta", "mistral", "cohere"):
            return prefix
        return "openrouter"
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    return "openrouter"


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """
    Resolve per-token pricing from LiteLLM's bundled model_cost.json.
    LiteLLM updates its price table with each release — no hardcoded rates here.
    Returns 0.0 for unknown models rather than silently using a wrong default.
    """
    try:
        import litellm
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            cache_read_input_tokens=cache_read_tokens,
        )
        return prompt_cost + completion_cost
    except Exception:
        log.warning("Could not resolve cost for model %r — reporting $0.00", model)
        return 0.0


def _redact(args: dict) -> str:
    return _redact_str(json.dumps(args, default=str))


def _redact_str(s: str) -> str:
    # Anthropic keys
    s = re.sub(r"sk-ant-[A-Za-z0-9\-_]{20,}", "[REDACTED]", s)
    # OpenAI / OpenRouter keys
    s = re.sub(r"sk-(?:proj-|or-)[A-Za-z0-9\-_]{20,}", "[REDACTED]", s)
    s = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", s)
    # JWTs
    s = re.sub(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+", "[REDACTED]", s)
    # PANs (13-19 digit numbers)
    s = re.sub(r"\b\d{13,19}\b", "[REDACTED]", s)
    # Browserbase API keys
    s = re.sub(r"bb_live_[A-Za-z0-9\-_]{10,}", "[REDACTED]", s)
    # Browser-use API keys (format: bu_<segment>_<token> or bu_live_<token>)
    s = re.sub(r"bu_[A-Za-z0-9\-_]{14,}", "[REDACTED]", s)
    # Modal tokens
    s = re.sub(r"a[ks]-[A-Za-z0-9]{10,}", "[REDACTED]", s)
    # Langfuse keys
    s = re.sub(r"(?:pk|sk)-lf-[A-Za-z0-9\-_]{10,}", "[REDACTED]", s)
    return s


def _extract_response_text(response) -> str:
    """Extract plain text from an LLM response object (Anthropic / OpenAI shapes)."""
    if response is None:
        return ""
    # Anthropic: response.content is a list of content blocks
    if hasattr(response, "content") and isinstance(response.content, list):
        parts = []
        for block in response.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return " ".join(parts)
    # OpenAI: response.choices[0].message.content
    if hasattr(response, "choices") and response.choices:
        msg = response.choices[0].message
        if hasattr(msg, "content") and msg.content:
            return msg.content
    # Plain string fallback
    if isinstance(response, str):
        return response
    return str(response)[:500]


def _get_memory_mb() -> int:
    try:
        import psutil, os
        return int(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024)
    except Exception:
        return 0
