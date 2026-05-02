# Changelog

All notable changes to `darwin-agent` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [0.1.0] — 2026-05-02

Initial implementation of the Phase 1 MVP spec.

### Added

**Entrypoint**
- `modal_app.py` — Modal `@app.function()` boot sequence: volume mount, migrations, heartbeat start, system prompt assembly, `HermesAgent` initialisation, `AGENT_WOKE` event, and controlled `HibernateSignal` / `SystemExit` handlers
- `_execute_hibernation()` — episodic compression, entity sync flush, wake-trigger registration with backend, `AGENT_HIBERNATED` event
- `_execute_emergency_termination()` — graceful flush on Redis kill signal

**Configuration**
- `config.py` — `pydantic-settings` `Settings` model reading all Modal-injected env vars (`darwin_id`, `session_id`, `agent_jwt`, `backend_url`, `redis_url`, `browserbase_*`, `langsmith_*`, `anthropic_*`, `openai_*`, `llm_model`, `vcc_ref`)

**Database layer** (`db/`)
- `schema.sql` — Hermes core tables (`hermes_memory`, `hermes_sessions`) + Darwin extension tables: `darwin_skills` (FTS5), `darwin_entities`, `darwin_ledger`, `darwin_hibernate_log`, `darwin_migrations`
- `client.py` — `SQLiteClient` with schema bootstrap, migration runner, working-memory loader, episodic summary writer, entity CRUD + sync tracking, FTS5 skill search, local ledger recording
- `migrations/001_initial.sql` — baseline migration record

**Tools** (`darwin_tools/`) — 20 tools across 9 modules, all following the `build_*_tools(settings, db, telemetry)` factory pattern:

| Module | Tools |
| --- | --- |
| `finance.py` | `check_ledger`, `record_expense`, `inject_payment` |
| `lifecycle.py` | `hibernate`, `check_kill_signal`, `HibernateSignal` |
| `human.py` | `ask_human`, `request_approval` |
| `browser.py` | `browser_fetch`, `browser_session`, `take_screenshot`, `click_element`, `type_text` |
| `memory.py` | `save_skill`, `search_skills`, `save_entity`, `search_memory`, `compress_session_to_episodic` |
| `files.py` | `upload_file` |
| `comms.py` | `send_email` |
| `neurons.py` | `spawn_neuron` |
| `audit.py` | `log_decision` |

- `__init__.py` — `ALL_TOOLS(settings, db, telemetry)` factory assembling all 20 tools into a flat list for Hermes injection

**Prompts** (`prompts/`)
- `system_prompt.md` — agent DNA: prime directive, P&L imperative, 7 survival rules, tool cost guide, lexical quarantine rules
- `working_memory.md.jinja` — Jinja2 template rendering balance, VCC ref, active deployments, wake prompt, and last episodic summary into the system prompt (≤ 500-token budget)

**Telemetry** (`telemetry/`)
- `client.py` — `TelemetryClient`: async `push_event()` (fire-and-forget canvas events), `push_metrics()` (heartbeat metabolic batch), `push_entity()` (entity sync)
- `heartbeat.py` — `HeartbeatThread`: daemon thread writing Redis `heartbeat:{darwin_id}` key (30s TTL) every 15s, polling kill signal, pushing metrics batch

**Tracing** (`tracing/`)
- `langsmith.py` — `configure_tracing()` sets `LANGCHAIN_TRACING_V2` + project name; `@trace_llm_call(name)` decorator wraps direct LLM calls in LangSmith trace nodes

**Neurons** (`neurons/`)
- `runner.py` — `NeuronRunner`: asyncio task pool with `spawn()`, `gather()` (emits `SYNAPTIC_INTEGRATION` event), and `cancel_all()`

**Package management**
- `pyproject.toml` — uv project file with all runtime dependencies
- `uv.lock` — pinned lockfile (74 packages)
- Modal image build uses `uv pip install --system` throughout; `requirements.txt` removed

[Unreleased]: https://github.com/darwin-agents/darwin-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/darwin-agents/darwin-agent/releases/tag/v0.1.0
