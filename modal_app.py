"""
darwin-agent — Modal entrypoint

Boot sequence:
  1. Mount shared Modal Volume at /mnt/darwin_state — $HERMES_HOME = /mnt/darwin_state/<darwin_id>
  2. Copy SOUL.md to $HERMES_HOME if not present (preserves agent-edited overrides)
  3. Render config.yaml from the Jinja2 template (picks up per-session model, backends)
  4. Set per-session env vars so darwin_tools can find the backend + JWT
  5. Hand off to Hermes' python_library.run_session()
     → Hermes discovers our plugin via entry points, calls register(ctx)
     → Tools, hooks, slash commands all wire up automatically
     → SOUL.md + MEMORY.md + USER.md build the system prompt
     → on_session_start hook fires → AGENT_WOKE canvas event
     → wake_prompt is fed as the user message
     → Agent runs until hibernate() is called or the 900s timeout fires

On DarwinHibernateSignal:
  1. Sync any unsynced darwin_entities to the backend
  2. Register wake trigger (CRON or WEBHOOK) with the God Layer
  3. Push AGENT_HIBERNATED canvas event
  4. volume.commit() in the outer finally block persists $HERMES_HOME durably

Modal requires the Volume handle used with ``commit()`` to be attached on the function via
``@app.function(volumes={...})``. Per-invocation ``Volume.from_name`` without a mount cannot be
committed; we therefore use one named volume and isolate each Darwin under a subdirectory.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import modal

# Single Modal Volume for all Darwins (one subdirectory per darwin_id). Created on first deploy/run.
DARWIN_STATE_VOLUME = modal.Volume.from_name("darwin-agents-state", create_if_missing=True)
STATE_MOUNT = "/mnt/darwin_state"


# ---------------------------------------------------------------------------
# Modal image
# ---------------------------------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(["git", "curl", "ripgrep", "ffmpeg"])
    .run_commands("curl -Ls https://astral.sh/uv/install.sh | sh")
    .env({"PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin"})
    .add_local_dir("hermes-core",    "/root/hermes-core", copy=True)
    .add_local_dir("darwin_tools",   "/root/darwin_tools", copy=True)
    .add_local_dir("darwin_skills",  "/root/darwin_skills", copy=True)
    .add_local_file("prompts/SOUL.md",           "/root/SOUL.md", copy=True)
    .add_local_file("config/config.yaml.j2",     "/root/config.yaml.j2", copy=True)
    .add_local_file("pyproject.toml",            "/root/pyproject.toml", copy=True)
    .run_commands(
        "uv pip install --system -e /root/hermes-core[all]",
        "uv pip install --system -e /root",   # registers darwin_tools entry point
    )
    .env({
        "PYTHONUNBUFFERED": "1",
    })
)

app = modal.App("darwin-agent", image=image)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

@app.function(
    timeout=900,
    memory=2048,
    cpu=2.0,
    volumes={STATE_MOUNT: DARWIN_STATE_VOLUME},
    secrets=[modal.Secret.from_name("darwin-agent-secrets")],
)
def run_agent(
    darwin_id: str,
    session_id: str,
    wake_prompt: str,
    agent_jwt: str,
    backend_url: str,
    vcc_ref: str = "",
):
    """
    Boot sequence. Called by the God Layer on spawn or wake-up.
    vcc_ref is the per-Darwin opaque token for VCC injection — never the raw PAN.
    Modal mounts the Volume → we bootstrap env → Hermes takes over.
    """
    # Validate required args early with clear errors
    for name, val in [("darwin_id", darwin_id), ("session_id", session_id),
                      ("agent_jwt", agent_jwt), ("backend_url", backend_url)]:
        if not val:
            raise ValueError(f"run_agent: required argument '{name}' is empty")

    asyncio.run(_run_agent_async(darwin_id, session_id, wake_prompt, agent_jwt, backend_url, vcc_ref))


async def _run_agent_async(
    darwin_id: str,
    session_id: str,
    wake_prompt: str,
    agent_jwt: str,
    backend_url: str,
    vcc_ref: str = "",
) -> None:
    hermes_home = Path(STATE_MOUNT) / darwin_id
    hermes_home.mkdir(parents=True, exist_ok=True)

    # 1. Copy SOUL.md only if not already present
    #    (preserve any identity edits the agent made in a previous session)
    soul_path = hermes_home / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(Path("/root/SOUL.md").read_text())

    # 2. Render config.yaml from the Jinja2 template
    await _render_config(hermes_home, darwin_id, session_id, agent_jwt, backend_url)

    # 3. Per-session env vars — read by darwin_tools at register time
    os.environ["HERMES_HOME"]        = str(hermes_home)
    os.environ["DARWIN_ID"]          = darwin_id
    os.environ["DARWIN_SESSION_ID"]  = session_id
    os.environ["DARWIN_AGENT_JWT"]   = agent_jwt
    os.environ["DARWIN_BACKEND_URL"] = backend_url
    os.environ["DARWIN_WAKE_PROMPT"] = wake_prompt
    # VCC_REF is per-Darwin — passed as a spawn-time arg, NOT a static Modal Secret
    os.environ["DARWIN_VCC_REF"] = vcc_ref

    # 4. Hand off to Hermes
    from darwin_tools.lifecycle import DarwinHibernateSignal

    try:
        try:
            # Legacy Hermes API (older submodule revisions).
            from hermes_agent.python_library import run_session  # type: ignore[import-not-found]

            await run_session(
                home=str(hermes_home),
                user_message=wake_prompt,
            )
        except ModuleNotFoundError:
            # Newer Hermes revisions expose AIAgent in run_agent.py.
            await asyncio.to_thread(_run_session_via_aiagent, wake_prompt)
    except DarwinHibernateSignal as sig:
        await _execute_hibernation(
            sig, hermes_home, darwin_id, session_id, agent_jwt, backend_url
        )
    finally:
        await DARWIN_STATE_VOLUME.commit.aio()


def _run_session_via_aiagent(user_message: str) -> None:
    """
    Hermes compatibility path for new submodule versions where
    ``hermes_agent.python_library.run_session`` no longer exists.

    Library-mode ``AIAgent()`` defaults ``model`` to empty; Hermes' init path
    discards the routed default model when constructing the HTTP client, which
    yields OpenRouter ``400 No models provided``. We therefore pass explicit
    ``provider``, ``model``, and ``max_tokens`` from rendered ``config.yaml`` (with optional
    ``OPENROUTER_*`` env overrides). A bounded ``max_tokens`` avoids OpenRouter HTTP 402 when
    the provider reserves spend against a huge completion budget.
    """
    from run_agent import AIAgent  # type: ignore[import-not-found]

    hermes_home = Path(os.environ.get("HERMES_HOME", ""))
    provider = "openrouter"
    model_id = (
        (os.environ.get("OPENROUTER_MODEL") or "").strip()
        or (os.environ.get("OPENROUTER_DEFAULT_MODEL") or "").strip()
        or "anthropic/claude-haiku-4.5"
    )
    max_iterations = 90
    max_tokens = None

    cfg_path = hermes_home / "config.yaml"
    if cfg_path.is_file():
        try:
            import yaml

            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                m = data.get("model")
                if isinstance(m, dict):
                    prov = (m.get("provider") or "").strip()
                    if prov:
                        provider = prov.lower()
                    md = (m.get("default") or "").strip()
                    if md:
                        model_id = md
                    mtoks = m.get("max_tokens")
                    if mtoks is not None:
                        max_tokens = int(mtoks)
                agent_cfg = data.get("agent")
                if isinstance(agent_cfg, dict):
                    mt = agent_cfg.get("max_turns")
                    if mt is not None:
                        max_iterations = int(mt)
        except Exception:
            pass

    env_mt = (os.environ.get("OPENROUTER_MAX_TOKENS") or "").strip()
    if env_mt:
        max_tokens = int(env_mt)
    if max_tokens is None:
        max_tokens = 4096

    agent = AIAgent(
        provider=provider,
        model=model_id,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        quiet_mode=True,
    )
    agent.run_conversation(user_message)


async def _render_config(
    hermes_home: Path,
    darwin_id: str,
    session_id: str,
    agent_jwt: str,
    backend_url: str,
) -> None:
    from jinja2 import Template
    tpl = Template(Path("/root/config.yaml.j2").read_text())
    rendered = tpl.render(
        darwin_id=darwin_id,
        session_id=session_id,
        backend_url=backend_url,
    )
    (hermes_home / "config.yaml").write_text(rendered)


# ---------------------------------------------------------------------------
# Hibernation sequence
# ---------------------------------------------------------------------------

async def _execute_hibernation(
    sig,
    hermes_home: Path,
    darwin_id: str,
    session_id: str,
    agent_jwt: str,
    backend_url: str,
) -> None:
    """
    Controlled shutdown. Hermes has already:
      - Auto-compressed the conversation on context pressure
      - Persisted MEMORY.md, USER.md, skills/ to $HERMES_HOME

    Our job: sync unsynced entities, register wake trigger, push canvas event.
    We do NOT call compress_session_to_episodic() — Hermes handles that.
    We do NOT stop a heartbeat thread — there isn't one.
    """
    import json
    import sqlite3
    import httpx
    from datetime import datetime, timezone, timedelta

    base = backend_url.rstrip("/") + "/api/v1"
    headers = {"Authorization": f"Bearer {agent_jwt}"}

    # Step 1: Sync any unsynced darwin_entities — best effort with retry
    db_path = hermes_home / "state.db"
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT id, entity_type, data FROM darwin_entities WHERE synced_at IS NULL"
        ).fetchall()
        async with httpx.AsyncClient(timeout=5.0) as client:
            for entity_id, etype, data in rows:
                for attempt in range(3):
                    try:
                        await client.post(
                            f"{base}/telemetry/entity",
                            json={
                                "darwin_id": darwin_id,
                                "session_id": session_id,
                                "entity_type": etype,
                                "entity_id": entity_id,
                                "data": json.loads(data),
                                "operation": "UPSERT",
                            },
                            headers=headers,
                        )
                        conn.execute(
                            "UPDATE darwin_entities SET synced_at = ? WHERE id = ?",
                            (int(datetime.now(timezone.utc).timestamp()), entity_id),
                        )
                        conn.commit()
                        break  # success — move to next entity
                    except Exception:
                        if attempt == 2:
                            pass  # retried on next session
        conn.close()

    # Step 2: Register wake trigger (with retry)
    wake_trigger: dict = {}
    if sig.wake_after_hours:
        wake_trigger = {
            "type": "CRON",
            "trigger_at": (
                datetime.now(timezone.utc) + timedelta(hours=sig.wake_after_hours)
            ).isoformat(),
        }
    elif sig.wake_on_webhook:
        wake_trigger = {"type": "WEBHOOK", "webhook_key": sig.wake_on_webhook}

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(3):
            try:
                await client.post(
                    f"{base}/darwins/{darwin_id}/hibernate",
                    json={
                        "session_id": session_id,
                        "reason": sig.reason,
                        "wake_trigger": wake_trigger,
                        "browserbase_session_id": sig.keep_browser_session,
                    },
                    headers=headers,
                )
                break
            except Exception:
                if attempt == 2:
                    pass  # best effort

        # Step 3: Final canvas event
        await client.post(
            f"{base}/telemetry/stream-event",
            json={
                "darwin_id": darwin_id,
                "session_id": session_id,
                "event_type": "AGENT_HIBERNATED",
                "payload": {
                    "reason": sig.reason,
                    "wake_trigger_type": wake_trigger.get("type"),
                    "wake_trigger_at": wake_trigger.get("trigger_at"),
                    "browserbase_kept_alive": sig.keep_browser_session is not None,
                },
                "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            },
            headers=headers,
        )
