"""Coerce OpenAI-style ``message.content`` blobs to plain strings.

OpenRouter and other proxies may return ``content`` as a nested dict or list
instead of a string.  Downstream code often does ``content[:500]``, which on a
dict raises ``KeyError(slice(None, 500, None))``.

This module is part of ``darwin_tools`` (not ``hermes-core``) so the fix
survives submodule updates without any dirty submodule edits.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def flatten_openai_content_to_str(raw: Any) -> str:
    """Recursively flatten ``content`` to a non-None string (empty if nothing usable)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = [flatten_openai_content_to_str(p) for p in raw]
        return "\n".join(p for p in parts if p)
    if isinstance(raw, dict):
        t = raw.get("text")
        if isinstance(t, str) and t.strip():
            return t
        c = raw.get("content")
        if c is not None and c is not raw:
            inner = flatten_openai_content_to_str(c)
            if inner:
                return inner
        for key in ("refusal", "message", "value"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                return v
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


def ensure_openai_message_content_str(raw: Any) -> Optional[str]:
    """Return ``str`` or ``None`` suitable for ``NormalizedResponse.content``."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    flat = flatten_openai_content_to_str(raw)
    return flat if flat else None
