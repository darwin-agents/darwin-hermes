"""
Unit tests — plugin registration

Verifies that register(ctx) wires up all 9 Darwin tools and all 7 hooks
without requiring the real Hermes runtime.
"""

import pytest
from darwin_tools import register

EXPECTED_TOOLS = {
    "check_ledger",
    "record_expense",
    "inject_payment",
    "hibernate",
    "request_approval",
    "save_entity",
    "upload_file",
    "send_email",
    "log_decision",
}

EXPECTED_HOOKS = {
    "on_session_start",
    "pre_llm_call",
    "post_llm_call",
    "pre_tool_call",
    "post_tool_call",
    "subagent_stop",
    "on_session_end",
}


def test_all_tools_registered(ctx):
    register(ctx)
    assert set(ctx.tools.keys()) == EXPECTED_TOOLS


def test_all_hooks_registered(ctx):
    register(ctx)
    assert set(ctx.hooks.keys()) == EXPECTED_HOOKS


def test_each_tool_has_schema_and_handler(ctx):
    register(ctx)
    for name, (schema, handler) in ctx.tools.items():
        assert isinstance(schema, dict), f"{name} missing schema"
        assert "name" in schema, f"{name} schema missing 'name'"
        assert "description" in schema, f"{name} schema missing 'description'"
        assert callable(handler), f"{name} handler is not callable"


def test_each_hook_has_callable_handlers(ctx):
    register(ctx)
    for event, handlers in ctx.hooks.items():
        for h in handlers:
            assert callable(h), f"Hook {event} has non-callable handler"


def test_tool_schemas_have_required_fields(ctx):
    register(ctx)
    for name, (schema, _) in ctx.tools.items():
        params = schema.get("parameters", {})
        assert params.get("type") == "object", f"{name}: parameters.type must be 'object'"
        assert "properties" in params, f"{name}: parameters missing 'properties'"
