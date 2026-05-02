# darwin-agent

The cognitive engine of the Darwin Agents platform. Runs inside ephemeral [Modal](https://modal.com) containers, reasons with [Hermes](https://github.com/NousResearch/hermes-function-calling), browses the web via [Browserbase](https://browserbase.com), and communicates with the `darwin-backend` (God Layer) for financial reality and persistent identity.

## Architecture overview

```
darwin-backend  ──── spawns ────►  Modal container
                                        │
                                   modal_app.py  (boot sequence)
                                        │
                          ┌─────────────┼──────────────┐
                          ▼             ▼              ▼
                    HermesAgent   HeartbeatThread  TelemetryClient
                    (reasoning)   (Redis / kill)   (/telemetry/*)
                          │
                   darwin_tools/  (20 tools injected at runtime)
                          │
                   /data/agent_memory/hermes.db  (Modal Volume — persists across hibernation)
```

**Ownership boundary**

| Owned by the agent | Owned by the backend |
| --- | --- |
| Reasoning, strategy, tool use | Master ledger, VCC decryption |
| Web navigation | Hibernation scheduling |
| Local SQLite (entities, skills, ledger shadow) | Canonical event log |
| Skill compilation | Everything the frontend sees |

The agent communicates with the backend through two categories of calls only:

1. `/finance` endpoints — ledger reads/writes, payment injection
2. `/telemetry` endpoints — every cognitive event and metabolic metric

## Repository structure

```
darwin-agent/
├── hermes-core/                    # git submodule — Nous Research Hermes (never modified)
│
├── darwin_tools/                   # proprietary tool definitions injected at runtime
│   ├── __init__.py                 # ALL_TOOLS factory
│   ├── finance.py                  # check_ledger, record_expense, inject_payment
│   ├── lifecycle.py                # hibernate, check_kill_signal, HibernateSignal
│   ├── human.py                    # ask_human, request_approval
│   ├── browser.py                  # browser_fetch, browser_session, take_screenshot,
│   │                               #   click_element, type_text
│   ├── memory.py                   # save_skill, search_skills, save_entity,
│   │                               #   search_memory, compress_session_to_episodic
│   ├── files.py                    # upload_file
│   ├── comms.py                    # send_email
│   ├── neurons.py                  # spawn_neuron
│   └── audit.py                    # log_decision
│
├── prompts/
│   ├── system_prompt.md            # base DNA — survival directive, P&L imperative
│   └── working_memory.md.jinja     # template for working memory injection at wake-up
│
├── db/
│   ├── schema.sql                  # Hermes SQLite schema + Darwin extensions
│   ├── migrations/                 # numbered migration scripts applied at wake-up
│   └── client.py                   # SQLite connection wrapper with FTS5 helpers
│
├── telemetry/
│   ├── client.py                   # HTTP client for /telemetry/* backend calls
│   └── heartbeat.py                # background heartbeat thread (Redis + kill signal)
│
├── tracing/
│   └── langsmith.py                # LangSmith trace wrappers
│
├── neurons/
│   └── runner.py                   # asyncio task runner for parallel sub-agents
│
├── config.py                       # pydantic-settings — reads env vars injected by Modal
├── modal_app.py                    # Modal @app.function() entrypoint — the boot sequence
├── pyproject.toml
└── uv.lock
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — all package management goes through uv
- [Modal CLI](https://modal.com/docs/guide) — `pip install modal` then `modal setup`
- Python 3.12+

## Local setup

```bash
git clone <repo>
cd darwin-agent
git submodule update --init --recursive   # pull hermes-core

uv sync                                   # install all dependencies from uv.lock
uv pip install -e hermes-core             # install Hermes as an editable local package
```

Copy `.env.example` → `.env` and fill in the required values:

```bash
cp .env.example .env
```

## Environment variables

| Variable | Source | Description |
| --- | --- | --- |
| `DARWIN_ID` | spawn-time arg | This agent's UUID |
| `SESSION_ID` | spawn-time arg | Current session UUID |
| `AGENT_JWT` | spawn-time arg | Short-lived JWT for backend auth |
| `BACKEND_URL` | spawn-time arg | God Layer base URL |
| `LLM_MODEL` | spawn-time arg | Model slug (e.g. `claude-sonnet-4-6`) |
| `VCC_REF` | spawn-time arg | Reference token for this agent's VCC |
| `REDIS_URL` | Modal Secret | Heartbeat and kill signal |
| `BROWSERBASE_API_KEY` | Modal Secret | Browserbase API key |
| `BROWSERBASE_PROJECT_ID` | Modal Secret | Browserbase project ID |
| `LANGSMITH_API_KEY` | Modal Secret | LangSmith tracing |
| `ANTHROPIC_API_KEY` | Modal Secret | Primary LLM provider |
| `OPENAI_API_KEY` | Modal Secret | Fallback LLM + embeddings |

Create the Modal secret once:

```bash
modal secret create darwin-agent-secrets \
  REDIS_URL=redis://... \
  BROWSERBASE_API_KEY=... \
  BROWSERBASE_PROJECT_ID=... \
  LANGSMITH_API_KEY=... \
  ANTHROPIC_API_KEY=... \
  OPENAI_API_KEY=...
```

## Deployment

```bash
modal deploy modal_app.py
```

The backend calls `run_agent.remote(darwin_id=..., session_id=..., ...)` to spawn a container.

## Memory architecture

Three tiers, ruthlessly token-efficient:

| Tier | Storage | Budget | Loaded at wake-up |
| --- | --- | --- | --- |
| **Working memory** | `hermes_memory` (SQLite) | ≤ 500 tokens | Always — injected into system prompt |
| **Episodic memory** | `hermes_sessions.summary` (SQLite) | ~200 tokens per session | Always — last session summary only |
| **Procedural memory** | `darwin_skills` FTS5 (SQLite) | Unlimited | On demand via `search_skills()` |

Raw session transcripts are stored in `hermes_sessions.transcript` and accessible only via `search_memory()`.

## Tools

| Tool | Module | Cost | Purpose |
| --- | --- | --- | --- |
| `check_ledger` | finance | ~$0.000 | Read balance + recent transactions |
| `record_expense` | finance | ~$0.000 | Record a debit after a purchase |
| `inject_payment` | finance | ~$0.000 | VCC injection via backend (agent never sees PAN) |
| `hibernate` | lifecycle | ~$0.000 | Suspend container; preserve state |
| `check_kill_signal` | lifecycle | ~$0.000 | Redis kill signal poll |
| `ask_human` | human | free | Escalate 2FA / CAPTCHA to creator |
| `request_approval` | human | free | Get approval before large purchase |
| `browser_fetch` | browser | ~$0.001 | Lightweight text-only page fetch |
| `browser_session` | browser | ~$0.050 | Full stealth browser session |
| `take_screenshot` | browser | ~$0.020 | Capture viewport for VLM analysis |
| `click_element` | browser | ~$0.001 | Click at pixel coordinates |
| `type_text` | browser | ~$0.001 | Type into an input field |
| `save_skill` | memory | ~$0.001 | Compile a workflow into a skill doc |
| `search_skills` | memory | ~$0.001 | FTS5 + global semantic skill search |
| `save_entity` | memory | ~$0.001 | Persist a business entity + sync to frontend |
| `search_memory` | memory | ~$0.001 | FTS5 search over historical transcripts |
| `upload_file` | files | ~$0.001 | Upload a generated file to storage |
| `send_email` | comms | ~$0.001 | Send email via creator's provider |
| `spawn_neuron` | neurons | varies | Launch a parallel sub-agent |
| `log_decision` | audit | ~$0.000 | Immutable audit trail entry |

## Session lifecycle

```
Backend spawns container
    └─► modal_app.py boots
            ├─ Modal Volume mounts at /data/agent_memory
            ├─ SQLiteClient opens hermes.db + runs pending migrations
            ├─ TelemetryClient initialised
            ├─ HeartbeatThread starts (Redis heartbeat + kill signal poll every 15s)
            ├─ Working memory loaded → system prompt built
            ├─ HermesAgent initialised with ALL_TOOLS
            ├─ AGENT_WOKE event pushed
            └─ agent.run() → LLM reasons and calls tools

    Agent calls hibernate()
            ├─ Episodic compression (full transcript → ~200-token summary)
            ├─ Unsynced entities flushed to backend
            ├─ Wake trigger registered with backend (CRON or WEBHOOK)
            ├─ AGENT_HIBERNATED event pushed
            └─ volume.commit() → container exits → billing stops
```

## Prompt injection defence

| Layer | Mechanism |
| --- | --- |
| VCC blindfold | Agent holds only `vcc_ref` token, never the PAN |
| Lexical quarantine | All web content wrapped in `<external_data>` tags; system prompt forbids obeying instructions inside them |
| Tool schema enforcement | Hermes registry allows only registered tools — no arbitrary HTTP calls |
| Amnesia protocol | Ephemeral containers; any compromised session dies with the container |

## Adding a migration

Create a new file in `db/migrations/` named `NNN_description.sql` where `NNN` is the next integer. It is applied automatically on the next container boot if not already tracked in `darwin_migrations`.

```bash
touch db/migrations/002_add_revenue_table.sql
```

## Adding a tool

1. Implement the async function in the appropriate `darwin_tools/` module, following the `build_*_tools(settings, db, telemetry)` factory pattern.
2. Add it to the list returned by that factory.
3. Import and call the factory in `darwin_tools/__init__.py` → `ALL_TOOLS()`.
4. The tool is now available to Hermes at the next container boot.
