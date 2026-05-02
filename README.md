# darwin-agent

The cognitive engine of the Darwin Agents platform. Runs inside ephemeral [Modal](https://modal.com) containers, powered by [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT), browses the web via [Browserbase](https://browserbase.com), and communicates with the `darwin-backend` (God Layer) for financial reality and persistent identity.

> **Architecture principle:** Hermes already implements ~90% of the cognitive infrastructure. Darwin's proprietary work is the financial tools, the FastAPI God Layer, and the swarm UI. Nothing else.

---

## What Darwin owns vs what Hermes provides

| Layer | Owner |
|---|---|
| Reasoning loop, memory, context compression, skill learning | **Hermes** |
| Browser control (Browserbase), vision, web search | **Hermes** |
| Sub-agent delegation (`delegate_task`), code execution | **Hermes** |
| Telegram gateway, `clarify` (ask_human), approval flow | **Hermes** |
| Langfuse observability (Mental Replay) | **Hermes** |
| Financial tools — ledger, VCC, payment injection | **Darwin** |
| `hibernate` (pause-and-resume) and `request_approval` | **Darwin** |
| `save_entity` — sync business objects to the frontend | **Darwin** |
| P&L telemetry hooks → God Layer | **Darwin** |
| FastAPI God Layer, Supabase, VCC vault | **Darwin** |
| Swarm-level Vue/Nuxt dashboard | **Darwin** |

---

## Repository structure

```
darwin-agent/
├── hermes-core/                    # git submodule — NousResearch/hermes-agent (NEVER modified)
│
├── darwin_tools/                   # our Hermes plugin — single integration point
│   ├── plugin.yaml                 # Hermes plugin manifest
│   ├── __init__.py                 # register(ctx) — wires tools + hooks
│   ├── finance.py                  # check_ledger, record_expense, inject_payment
│   ├── lifecycle.py                # hibernate, request_approval
│   ├── files.py                    # upload_file
│   ├── comms.py                    # send_email
│   ├── audit.py                    # log_decision
│   ├── entities.py                 # save_entity (+ darwin_entities table in state.db)
│   ├── pnl_hooks.py                # pre/post LLM + tool hooks → /telemetry
│   └── backend_client.py           # signed HTTPS client to the God Layer
│
├── darwin_skills/                  # read-only starter skills (mounted via skills.external_dirs)
│   ├── shopify-checkout-2026/
│   ├── stripe-2fa-handler/
│   ├── browser-screenshot-loop/
│   └── episodic-self-check/
│
├── prompts/
│   └── SOUL.md                     # agent DNA — copied to $HERMES_HOME/SOUL.md at boot
│
├── config/
│   └── config.yaml.j2              # Hermes config template rendered per session
│
├── modal_app.py                    # Modal entrypoint — boot sequence
├── pyproject.toml                  # hermes-agent dep + darwin entry point
└── uv.lock
```

**Everything Hermes-native** — `state.db`, `MEMORY.md`, `USER.md`, `skills/`, `sessions/`, `logs/`, `mcp-tokens/`, `cron/` — lives under `$HERMES_HOME` (`/data/hermes_home` on the Modal Volume). We never reimplement those.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- [Modal CLI](https://modal.com/docs/guide) — `modal setup`
- Python ≥ 3.11

## Local setup

```bash
git clone <repo>
cd darwin-agent
git submodule update --init --recursive   # pull NousResearch/hermes-agent

uv sync                                   # install from uv.lock
uv pip install -e hermes-core[all]        # install Hermes from submodule
```

Copy `.env.example` → `.env`:

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, DARWIN_BACKEND_URL, etc.
```

---

## How Darwin tools integrate with Hermes

The `darwin_tools/` package is a Hermes plugin, discovered automatically via the entry point:

```toml
[project.entry-points."hermes_agent.plugins"]
darwin = "darwin_tools:register"
```

Hermes calls `register(ctx)` at session start. All 9 proprietary tools and 7 hooks wire up through `ctx.register_tool()` and `ctx.register_hook()`. The agent then uses them alongside Hermes' built-in tools with the same syntax.

---

## Proprietary tools (9)

| Tool | What it does |
|---|---|
| `check_ledger` | Read balance + recent transactions from the God Layer |
| `record_expense` | Debit the master ledger after a purchase |
| `inject_payment` | VCC injection via backend WebSocket — agent never sees PAN |
| `hibernate` | Pause container; preserve `$HERMES_HOME` on Modal Volume |
| `request_approval` | Send Telegram approval request; returns webhook key for `hibernate` |
| `save_entity` | Persist business object to `state.db` + sync to God Layer |
| `upload_file` | Upload generated files (CSV, PDF) to storage; index for frontend |
| `send_email` | Send via creator's configured email provider |
| `log_decision` | Append to immutable audit trail (required before financial actions > $1) |

## Hermes built-ins we use (no code on our side)

| Built-in | Replaces |
|---|---|
| `browser_navigate`, `browser_snapshot`, `browser_vision`, `browser_click` | browser.py |
| `web_search`, `web_extract` | browser_fetch |
| `skill_manage`, `skill_view`, `skills_list` | save_skill, search_skills |
| `session_search` | search_memory |
| `delegate_task` | NeuronRunner, spawn_neuron |
| `execute_code` | code_runner.py |
| `clarify` | ask_human |
| `memory` | MEMORY.md management |
| `cronjob` | schedule_cron |

---

## Session lifecycle

```
God Layer spawns Modal container
    └─► modal_app.py boots
            ├─ Volume mounts at /data — $HERMES_HOME = /data/hermes_home
            ├─ SOUL.md copied to $HERMES_HOME if missing
            ├─ config.yaml rendered from template
            ├─ Per-session env vars set (darwin_id, agent_jwt, backend_url…)
            └─ hermes_agent.python_library.run_session() called

    Hermes builds system prompt:
            1. SOUL.md (Darwin DNA)
            2. Tool-aware behavior guidance
            3. MEMORY.md + USER.md (working memory)
            4. Skills index
            5. Timestamp

    Darwin plugin registered → tools + hooks live
    on_session_start hook → AGENT_WOKE event pushed

    Agent runs (check_ledger first, then work…)
            ├─ Every LLM call: post_llm_call hook → /telemetry/stream
            └─ Every tool call: pre/post_tool_call hooks → canvas events

    Agent calls hibernate()
            ├─ DarwinHibernateSignal raised
            ├─ Unsynced darwin_entities flushed to backend
            ├─ Wake trigger registered (CRON or WEBHOOK)
            ├─ AGENT_HIBERNATED canvas event pushed
            └─ volume.commit() → container exits → billing stops
```

---

## Deployment

```bash
modal deploy modal_app.py
```

Create the Modal secret once:

```bash
modal secret create darwin-agent-secrets \
  ANTHROPIC_API_KEY=... \
  OPENROUTER_API_KEY=... \
  AGENT_JWT_SECRET=... \
  BROWSERBASE_API_KEY=... \
  BROWSERBASE_PROJECT_ID=... \
  MODAL_TOKEN_ID=... \
  MODAL_TOKEN_SECRET=... \
  HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-... \
  HERMES_LANGFUSE_SECRET_KEY=sk-lf-... \
  HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

---

## Memory architecture

| Tier | Storage | Loaded at wake-up |
|---|---|---|
| Working | `$HERMES_HOME/SOUL.md` + `MEMORY.md` + `USER.md` | Always — Hermes builds system prompt |
| Episodic | Compressed conversation history in `state.db` | Auto-managed by Hermes context compressor |
| Procedural | `$HERMES_HOME/skills/` + `darwin_skills/` | On demand via `skill_view` |

---

## Upgrading Hermes

```bash
git submodule update --remote hermes-core
uv sync
# Run integration tests
# If something breaks, pin to last stable tag:
git -C hermes-core checkout <stable-tag>
```

## Adding a Darwin tool

1. Add a handler + schema in the appropriate `darwin_tools/` module
2. Register with `ctx.register_tool(name, schema, handler)` in that module's `register_*_tools(ctx)` function
3. Import and call the function in `darwin_tools/__init__.py` → `register(ctx)`
