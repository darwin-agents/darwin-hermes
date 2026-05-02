import functools
import os

from langsmith import Client
from langsmith.run_helpers import traceable

_ls_client = None


def configure_tracing(darwin_id: str, session_id: str):
    """Call once at container boot before any LLM calls."""
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = f"darwin-{darwin_id}"
    global _ls_client
    _ls_client = Client()


def trace_llm_call(name: str):
    """
    Decorator that wraps an LLM call in a LangSmith trace node.
    The trace is automatically linked to the current darwin_id + session_id
    and becomes queryable via GET /darwins/{id}/logs.
    """
    def decorator(fn):
        @functools.wraps(fn)
        @traceable(name=name, run_type="llm")
        async def wrapper(*args, **kwargs):
            result = await fn(*args, **kwargs)
            return result
        return wrapper
    return decorator
