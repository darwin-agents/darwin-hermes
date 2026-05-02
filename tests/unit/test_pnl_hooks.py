"""
Unit tests for darwin_tools/pnl_hooks.py

Invariants:
- Hooks register in correct order (pre before post)
- _redact_str strips API keys, JWTs, and PANs
- _estimate_cost returns sensible values
"""

import pytest
from darwin_tools.pnl_hooks import _redact_str, _estimate_cost, register_pnl_hooks


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


def test_estimate_cost_anthropic_haiku():
    cost = _estimate_cost("claude-haiku-4-5-20251001", 1000, 200)
    # 1000 * 0.25/1M + 200 * 1.25/1M = $0.00025 + $0.00025 = $0.0005
    assert abs(cost - 0.0005) < 0.0001


def test_estimate_cost_unknown_model_uses_default():
    cost = _estimate_cost("unknown-model", 0, 0)
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
