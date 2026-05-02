import json
import modal

from config import Settings
from db.client import SQLiteClient
from darwin_tools import ALL_TOOLS
from darwin_tools.lifecycle import HibernateSignal
from darwin_tools.memory import compress_session_to_episodic
from telemetry.client import TelemetryClient
from telemetry.heartbeat import HeartbeatThread
from tracing.langsmith import configure_tracing

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(["git", "curl"])
    .run_commands("curl -Ls https://astral.sh/uv/install.sh | sh")
    .env({"PATH": "/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
    .copy_local_file("pyproject.toml", "/root/pyproject.toml")
    .run_commands("uv pip install --system -r /root/pyproject.toml")
    .run_commands(
        "git clone https://github.com/NousResearch/hermes-function-calling /root/hermes-core",
        "uv pip install --system -e /root/hermes-core",
    )
    .env({
        "PYTHONUNBUFFERED": "1",
        "LANGCHAIN_TRACING_V2": "true",
    })
)

app = modal.App("darwin-agent", image=image)


def get_volume(darwin_id: str) -> modal.Volume:
    return modal.Volume.from_name(f"darwin-{darwin_id}", create_if_missing=True)


@app.function(
    timeout=900,
    memory=2048,
    cpu=2.0,
    secrets=[modal.Secret.from_name("darwin-agent-secrets")],
)
def run_agent(
    darwin_id: str,
    session_id: str,
    wake_prompt: str,
    agent_jwt: str,
    backend_url: str,
):
    """
    The main agent entrypoint. Called by Modal when the backend spawns or wakes
    an agent. Everything that happens during one active session lives here.
    """
    import asyncio
    asyncio.run(_run_agent_async(darwin_id, session_id, wake_prompt, agent_jwt, backend_url))


async def _run_agent_async(
    darwin_id: str,
    session_id: str,
    wake_prompt: str,
    agent_jwt: str,
    backend_url: str,
):
    settings = Settings(
        darwin_id=darwin_id,
        session_id=session_id,
        agent_jwt=agent_jwt,
        backend_url=backend_url,
    )

    configure_tracing(darwin_id=darwin_id, session_id=session_id)

    volume = get_volume(darwin_id)
    with volume.mount("/data/agent_memory"):
        db = SQLiteClient("/data/agent_memory/hermes.db")
        db.run_pending_migrations()

        telemetry = TelemetryClient(
            backend_url=backend_url,
            agent_jwt=agent_jwt,
            darwin_id=darwin_id,
            session_id=session_id,
        )

        heartbeat = HeartbeatThread(
            darwin_id=darwin_id,
            agent_jwt=agent_jwt,
            backend_url=backend_url,
            telemetry=telemetry,
        )
        heartbeat.start()

        working_memory = db.build_working_memory()

        from hermes_core.agent import HermesAgent
        agent = HermesAgent(
            model=settings.llm_model,
            system_prompt=_build_system_prompt(working_memory, wake_prompt),
            tools=ALL_TOOLS(
                settings=settings,
                db=db,
                telemetry=telemetry,
            ),
        )

        await telemetry.push_event(
            event_type="AGENT_WOKE",
            payload={
                "wake_reason": wake_prompt,
                "session_id": session_id,
                "balance_at_wake": working_memory.get("balance", 0),
                "hibernated_for_hours": working_memory.get("hibernated_for_hours", 0),
            }
        )

        try:
            await agent.run()
        except HibernateSignal as sig:
            await _execute_hibernation(sig, db, telemetry, settings)
        except (SystemExit, KeyboardInterrupt):
            await _execute_emergency_termination(db, telemetry, settings)
        finally:
            heartbeat.stop()
            await telemetry.close()
            volume.commit()


def _build_system_prompt(working_memory: dict, wake_prompt: str) -> str:
    base = open("prompts/system_prompt.md").read()
    memory_block = f"""
## Current State
- Darwin ID: {working_memory.get('darwin_id', 'unknown')}
- Session ID: {working_memory.get('session_id', 'unknown')}
- Wallet Balance: ${working_memory.get('balance', 0.0):.2f}
- VCC Reference: {working_memory.get('vcc_ref', 'not set')}
- Active Deployments: {working_memory.get('active_deployments', 'None')}
- Recent Episode: {working_memory.get('last_episodic_summary', 'None — this is a fresh start.')}

## Wake-Up Trigger
{wake_prompt}
"""
    return base + memory_block


async def _execute_hibernation(
    sig: HibernateSignal,
    db: SQLiteClient,
    telemetry: TelemetryClient,
    settings: Settings,
):
    """
    Controlled shutdown. Preserves all state before the container is destroyed.
    """
    summary = None
    raw_transcript = db.get_current_session_transcript()
    if raw_transcript and len(raw_transcript) > 500:
        import anthropic
        llm_client = anthropic.AsyncAnthropic()
        summary = await compress_session_to_episodic(raw_transcript, llm_client)
        db.save_episodic_summary(summary)
        await telemetry.push_event("EPISODIC_SUMMARY_WRITTEN", {
            "session_id": settings.session_id,
            "summary": summary,
            "raw_tokens_compressed": len(raw_transcript.split()),
            "summary_tokens": len(summary.split()),
        })

    unsynced = db.get_unsynced_entities()
    for entity in unsynced:
        await telemetry.push_entity(
            entity["entity_type"],
            entity["id"],
            json.loads(entity["data"]),
            "UPDATE",
        )
        db.mark_entity_synced(entity["id"])

    wake_trigger = {}
    if sig.wake_after_hours:
        from datetime import datetime, timedelta
        wake_trigger = {
            "type": "CRON",
            "trigger_at": (datetime.utcnow() + timedelta(hours=sig.wake_after_hours)).isoformat(),
        }
    elif sig.wake_on_webhook:
        wake_trigger = {
            "type": "WEBHOOK",
            "webhook_key": sig.wake_on_webhook,
        }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                settings.backend_url.rstrip("/") + f"/api/v1/darwins/{settings.darwin_id}/hibernate",
                json={
                    "session_id": settings.session_id,
                    "reason": sig.reason,
                    "wake_trigger": wake_trigger,
                    "browserbase_session_id": sig.keep_browser_session,
                    "episodic_summary": summary or "No new activity this session.",
                },
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
    except Exception:
        pass

    await telemetry.push_event("AGENT_HIBERNATED", {
        "reason": sig.reason,
        "wake_trigger_type": wake_trigger.get("type"),
        "wake_trigger_at": wake_trigger.get("trigger_at"),
        "episodic_summary": summary,
        "browserbase_session_kept_alive": sig.keep_browser_session is not None,
    })


async def _execute_emergency_termination(
    db: SQLiteClient,
    telemetry: TelemetryClient,
    settings: Settings,
):
    """Kill signal received — flush state and exit immediately."""
    await telemetry.push_event("AGENT_KILLED", {
        "session_id": settings.session_id,
        "reason": "Kill signal received from backend",
    })
