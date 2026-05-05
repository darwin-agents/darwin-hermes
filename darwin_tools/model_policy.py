"""
OpenRouter-centric model tiers — Hermes/agent picks IDs; Darwin provides env-driven defaults.

All IDs should be OpenRouter slugs (`provider/model`).
"""

from __future__ import annotations

import os

_TIER_FALLBACK = {
    "routine": "google/gemini-2.0-flash-001",
    "standard": "anthropic/claude-3.5-sonnet",
    "heavy": "anthropic/claude-3.5-sonnet",
    "premium": "anthropic/claude-sonnet-4-20250514",
}


def model_for_tier(tier: str) -> str:
    """
    Resolve an OpenRouter model id for ROUTINE/STANDARD/HEAVY/PREMIUM-style tiers.

    Overrides:
      ``OPENROUTER_TIER_ROUTINE``, ``OPENROUTER_TIER_STANDARD``,
      ``OPENROUTER_TIER_HEAVY``, ``OPENROUTER_TIER_PREMIUM``
    """
    t = tier.strip().lower()
    env_key = {
        "routine": "OPENROUTER_TIER_ROUTINE",
        "standard": "OPENROUTER_TIER_STANDARD",
        "heavy": "OPENROUTER_TIER_HEAVY",
        "premium": "OPENROUTER_TIER_PREMIUM",
    }.get(t, "OPENROUTER_TIER_STANDARD")
    slug = os.environ.get(env_key) or _TIER_FALLBACK.get(t) or _TIER_FALLBACK["standard"]
    return slug


def pick_cheapest_by_capability(capability: str) -> str:
    """
    Heuristic cheapest OpenRouter-compatible slug for multimodal workloads.

    ``capability`` ∈ {chat, embedding, image, audio, video} — aligns with LiteLLM tables when present.

    Embedding calls should bypass chat models; embeddings are routed on the backend, not here.

    Override with ``OPENROUTER_CAPABILITY_MODEL_CHAT`` etc.
    """
    cap = capability.strip().lower()
    env = os.environ.get(f"OPENROUTER_CAPABILITY_MODEL_{cap.upper().replace('-', '_')}")
    if env:
        return env

    return {
        "chat": "google/gemini-2.0-flash-001",
        "image": "openai/gpt-4o-mini",
        "audio": "google/gemini-2.0-flash-001",
        "video": "google/gemini-2.0-flash-001",
    }.get(cap, _TIER_FALLBACK["standard"])
