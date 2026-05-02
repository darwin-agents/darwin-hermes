"""
Darwin Agent plugin — registers all proprietary tools and hooks with Hermes.

Hermes discovers this via:
  [project.entry-points."hermes_agent.plugins"]
  darwin = "darwin_tools:register"

and calls register(ctx) at session start.

The rule: we never import from hermes-core internals.
The only surface we touch is the documented ctx API:
  ctx.register_tool(), ctx.register_hook(), ctx.register_command()
"""

from .finance import register_finance_tools
from .lifecycle import register_lifecycle_tools
from .files import register_files_tools
from .comms import register_comms_tools
from .audit import register_audit_tools
from .entities import register_entities_tools
from .pnl_hooks import register_pnl_hooks


def register(ctx) -> None:
    """Hermes calls this once at session start before the first LLM turn."""
    register_finance_tools(ctx)
    register_lifecycle_tools(ctx)
    register_files_tools(ctx)
    register_comms_tools(ctx)
    register_audit_tools(ctx)
    register_entities_tools(ctx)
    # Hooks last — so tool registration logging is clean
    register_pnl_hooks(ctx)
