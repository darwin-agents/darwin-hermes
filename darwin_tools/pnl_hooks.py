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

import threading

# ---------------------------------------------------------------------------
# OpenRouter pricing cache
# Populated lazily from GET https://openrouter.ai/api/v1/models.
# Retried with exponential back-off so a transient failure at session start
# doesn't permanently disable cost tracking for the whole run.
# ---------------------------------------------------------------------------
_OR_PRICING: dict[str, dict] = {}
_OR_PRICING_LOCK = threading.Lock()
_OR_PRICING_FETCHED = False        # True once a successful fetch has populated the cache
_OR_PRICING_NEXT_RETRY: float = 0  # monotonic timestamp; 0 = try immediately

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


def _pre_llm_call(**kwargs):
    """Hermes fires ``pre_llm_call`` with keyword args only (see ``run_agent``)."""
    pass


def _resolve_post_llm_cost_usd(model: str, usage: dict) -> float:
    """Prefer billed provider USD, then OpenRouter pricing estimate."""
    if "billed_cost_usd" in usage:
        try:
            return max(0.0, float(usage["billed_cost_usd"]))
        except (TypeError, ValueError):
            pass
    if "cost" in usage and usage["cost"] is not None:
        try:
            return max(0.0, float(usage["cost"]))
        except (TypeError, ValueError):
            pass
    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    return _estimate_cost(model, in_tok, out_tok, cache_read_tokens=cache_read)


def _post_llm_call(**kwargs):
    """
    Record token burn + canvas THOUGHT events.

    Hermes ``post_llm_call`` passes ``assistant_response``, ``conversation_history``,
    ``model``, and (since OpenRouter billing integration) a per-turn ``usage`` dict
    with token buckets plus optional ``billed_cost_usd`` from provider ``usage.cost``.
    Older tests may still pass ``response=`` / ``usage=``; both shapes are accepted.
    """
    global _token_running_total, _cost_running_total

    model = (kwargs.get("model") or "").strip()
    assistant_response = kwargs.get("assistant_response")
    legacy_response = kwargs.get("response")
    raw_text = assistant_response if assistant_response is not None else legacy_response

    usage = kwargs.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    duration_ms = int(kwargs.get("duration_ms") or 0)
    turn_id = kwargs.get("turn_id")

    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    reasoning = int(usage.get("reasoning_tokens", 0) or 0)

    cost = _resolve_post_llm_cost_usd(model, usage)
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
    content = _extract_response_text(raw_text)
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


def _load_openrouter_pricing() -> dict[str, dict]:
    """
    Return the OpenRouter model→pricing dict, fetching/retrying as needed.

    Strategy:
    - On success the cache is populated once and reused for the rest of the session.
    - On failure we wait _OR_RETRY_DELAYS[attempt] seconds before the next attempt,
      so a transient network hiccup at container startup doesn't permanently disable
      cost tracking.  After all retries are exhausted we log at WARNING every call.
    """
    import time as _time

    # Retry delays (seconds): 5 s → 15 s → 60 s → give up
    _OR_RETRY_DELAYS = (5, 15, 60)

    global _OR_PRICING, _OR_PRICING_FETCHED, _OR_PRICING_NEXT_RETRY
    with _OR_PRICING_LOCK:
        if _OR_PRICING_FETCHED:
            return _OR_PRICING
        now = _time.monotonic()
        if now < _OR_PRICING_NEXT_RETRY:
            return _OR_PRICING  # in back-off window — return empty cache for now
        try:
            import httpx
            base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = httpx.get(f"{base}/models", headers=headers, timeout=10.0)
            resp.raise_for_status()
            fetched: dict[str, dict] = {}
            for entry in resp.json().get("data", []):
                mid = entry.get("id", "")
                pricing = entry.get("pricing")
                if mid and isinstance(pricing, dict):
                    fetched[mid] = pricing
            if not fetched:
                raise ValueError("OpenRouter /models returned 0 entries")
            _OR_PRICING.update(fetched)
            _OR_PRICING_FETCHED = True
            log.info("OpenRouter pricing loaded for %d models", len(_OR_PRICING))
        except Exception as exc:
            # Work out which retry slot we're in by counting prior attempts via
            # how far _OR_PRICING_NEXT_RETRY has advanced from 0.
            elapsed = now  # monotonic time since process start ≈ time since first attempt
            delay_idx = sum(1 for d in _OR_RETRY_DELAYS if elapsed > sum(_OR_RETRY_DELAYS[:_OR_RETRY_DELAYS.index(d) + 1]))
            delay = _OR_RETRY_DELAYS[min(delay_idx, len(_OR_RETRY_DELAYS) - 1)]
            _OR_PRICING_NEXT_RETRY = now + delay
            log.warning(
                "OpenRouter /models fetch failed — %s  (retrying in %ds)",
                exc,
                delay,
            )
    return _OR_PRICING


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """
    Estimate cost in USD for one LLM call using live OpenRouter pricing.

    Pricing keys returned by OpenRouter (USD per token, as strings):
      prompt            — input tokens
      completion        — output tokens
      input_cache_read  — cache-hit input tokens (Anthropic prompt caching)

    Reports $0.00 (with a warning) only when the model is genuinely absent
    from OpenRouter's catalogue after all retries have been exhausted.
    """
    pricing = _load_openrouter_pricing().get(model)
    if not pricing:
        log.warning("Could not resolve cost for model %r — reporting $0.00", model)
        return 0.0
    try:
        prompt_price      = float(pricing.get("prompt", 0) or 0)
        completion_price  = float(pricing.get("completion", 0) or 0)
        cache_read_price  = float(pricing.get("input_cache_read", 0) or 0)
        cost = (
            input_tokens        * prompt_price
            + output_tokens     * completion_price
            + cache_read_tokens * cache_read_price
        )
        return max(0.0, cost)
    except (TypeError, ValueError) as exc:
        log.debug("Pricing calculation failed for %r — %s", model, exc)
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
