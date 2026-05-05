"""
Darwin Agent plugin — registers all proprietary tools and hooks with Hermes.

Hermes discovers this via:
  [project.entry-points."hermes_agent.plugins"]
  darwin = "darwin_tools"

``importlib`` loads the **package**; Hermes then calls ``register(ctx)`` on that module.
(Do not use ``darwin_tools:register`` — that loads only the callable and breaks discovery.)

The rule: we never import from hermes-core internals.
The only surface we touch is the documented ctx API:
  ctx.register_tool(name=..., toolset="darwin", schema=..., handler=..., is_async=True),
  ctx.register_hook(), ctx.register_command()
"""

from __future__ import annotations

import os
import logging
from typing import Any, Callable, Protocol, runtime_checkable

from .finance import register_finance_tools
from .lifecycle import register_lifecycle_tools
from .files import register_files_tools
from .comms import register_comms_tools
from .audit import register_audit_tools
from .entities import register_entities_tools
from .pnl_hooks import register_pnl_hooks

__all__ = ["register", "HermesPluginContext"]

log = logging.getLogger(__name__)

# Required env vars declared in plugin.yaml — validated at registration time
_REQUIRED_ENV = ["DARWIN_BACKEND_URL", "DARWIN_AGENT_JWT", "DARWIN_ID", "DARWIN_SESSION_ID"]


@runtime_checkable
class HermesPluginContext(Protocol):
    """Type stub for the Hermes plugin context API."""

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        **kwargs: Any,
    ) -> None: ...
    def register_hook(self, event: str, handler: Callable) -> None: ...


def register(ctx: HermesPluginContext) -> None:
    """Hermes calls this once at session start before the first LLM turn."""
    # Validate required env vars early — fail fast with a clear message
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"darwin_tools plugin: missing required env vars: {', '.join(missing)}. "
            f"Ensure modal_app.py sets them before Hermes calls register()."
        )

    register_finance_tools(ctx)
    register_lifecycle_tools(ctx)
    register_files_tools(ctx)
    register_comms_tools(ctx)
    register_audit_tools(ctx)
    register_entities_tools(ctx)
    # Hooks last — so tool registration logging is clean
    register_pnl_hooks(ctx)
    log.info("darwin_tools plugin registered: 9 tools, 7 hooks")
