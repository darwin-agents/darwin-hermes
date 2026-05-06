"""
Runtime patches for hermes-core applied at plugin registration time.

darwin_tools owns these fixes so the hermes-core submodule can stay pinned
to upstream with zero dirty edits.  Patches are applied once via
``apply_all()`` called from ``darwin_tools.__init__.register(ctx)``.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def apply_all() -> None:
    """Apply every hermes-core patch.  Safe to call more than once."""
    _patch_chat_completions_content_normalization()


# ---------------------------------------------------------------------------
# Patch 1: ChatCompletionsTransport.normalize_response
#
# OpenRouter (and other proxies) may return message.content as a dict or list
# instead of a plain string.  Upstream hermes-core passes it through as-is,
# which makes downstream code like ``content[:500]`` raise
# ``KeyError: slice(None, 500, None)`` on dict objects.
#
# We wrap normalize_response to coerce content to str|None after the fact.
# ---------------------------------------------------------------------------

_PATCH_1_APPLIED = False


def _patch_chat_completions_content_normalization() -> None:
    global _PATCH_1_APPLIED
    if _PATCH_1_APPLIED:
        return
    try:
        from agent.transports.chat_completions import ChatCompletionsTransport
        from darwin_tools.chat_message_content import ensure_openai_message_content_str

        _orig = ChatCompletionsTransport.normalize_response

        def _patched_normalize(self, response, **kwargs):
            result = _orig(self, response, **kwargs)
            result.content = ensure_openai_message_content_str(result.content)
            return result

        ChatCompletionsTransport.normalize_response = _patched_normalize
        _PATCH_1_APPLIED = True
        log.debug("hermes_patches: ChatCompletionsTransport.normalize_response patched")
    except Exception as exc:
        log.warning("hermes_patches: could not patch normalize_response: %s", exc)
