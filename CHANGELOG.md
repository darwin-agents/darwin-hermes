# Changelog

All notable changes to `darwin-agent` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- **`modal_app.py` Hermes fallback:** import `asyncio` at module scope so `asyncio.to_thread(...)` works when `hermes_agent.python_library.run_session` is missing.
- **Hermes plugin entry point:** `[project.entry-points."hermes_agent.plugins"]` now uses `darwin = "darwin_tools"` so `ep.load()` returns the package module (Hermes requires `register` on the loaded object; `darwin_tools:register` loaded only the callable and triggered “Plugin 'darwin' has no register() function”).
- **OpenRouter ``No models provided``:** `_run_session_via_aiagent` passes explicit `provider` and `model` from `$HERMES_HOME/config.yaml` (with optional `OPENROUTER_MODEL` / `OPENROUTER_DEFAULT_MODEL` env overrides), because bare `AIAgent()` leaves `model` empty and Hermes drops the routed default during client init.
- **Darwin plugin tool registration:** all `PluginContext.register_tool(...)` calls now pass Hermes’ positional `toolset` plus keyword `name` / `schema` / `handler` / `is_async`, fixing “missing 1 required positional argument: 'handler'”.
- **OpenRouter HTTP 402 on low balance:** `_run_session_via_aiagent` passes explicit `max_tokens` from `config.yaml` `model.max_tokens`, overridden by `OPENROUTER_MAX_TOKENS`, defaulting to `4096`, so providers do not reserve completion budget against an enormous implicit cap.
- **P&L plugin hooks vs Hermes `invoke_hook`:** Darwin lifecycle hooks are synchronous again — Hermes calls hook callbacks without awaiting coroutines (`invoke_hook` uses plain ``cb(**kwargs)``). Telemetry from hooks uses `BackendClient.post_sync`, `flush_queued_telemetry_sync`, and `close_backend_client_sync`, eliminating “coroutine was never awaited” warnings for `_on_session_start`, `_pre_tool_call`, `_post_tool_call`, and `_on_session_end`.

### Changed

- **Modal Volume attachment:** use one named volume `darwin-agents-state` mounted at `/mnt/darwin_state`, with each Darwin under `/mnt/darwin_state/<darwin_id>/`. Modal only allows `commit()` on volumes declared on `@app.function(volumes={...})`; per-invocation `Volume.from_name` without a mount cannot be committed. Older per-Darwin volumes (`darwin-<uuid>`) are no longer used — migrate data manually if needed.
- **`config/config.yaml.j2`:** primary model default is `anthropic/claude-haiku-4.5` with `model.max_tokens: 4096` for predictable OpenRouter spend (override via env or template vars as needed).
- **`darwin_tools/backend_client.py`:** optional lazily-created `httpx.Client` for sync hook telemetry; `async`-based tools unchanged (`post_async`, etc.).

---

## [0.3.0] — 2026-05-05

### Added

- **`darwin_tools/telemetry_queue.py`** — persists failed telemetry POSTs into `$HERMES_HOME/state.db`; `BackendClient.flush_queued_telemetry()` runs on session start/end.
- **`darwin_tools/model_policy.py`** — OpenRouter slug helpers (`model_for_tier`, `pick_cheapest_by_capability`).
- **`POST /telemetry/llm-call` idempotency** — `pnl_hooks` now sends a per-inference **`request_id`** plus optional multimodal usage fields echoed from Hermes/tokenizers.

### Changed

- **Modal runtime async + Hermes compatibility:** `modal_app.py` now uses async Volume interfaces (`listdir.aio`, `read_file.aio`, `remove_file.aio`, `batch_upload.aio`) to avoid async warnings on Modal v1.x, and falls back to `run_agent.AIAgent` when `hermes_agent.python_library.run_session` is unavailable in newer Hermes submodule revisions.
- **Modal async persistence:** session snapshot commit now uses `await volume.commit.aio()` to avoid blocking Modal API calls inside async runtime.
- **`config/config.yaml.j2`** — primary `model.provider: openrouter`; default model slug remains configurable (Hermes resolves via `OPENROUTER_API_KEY`).
- **`prompts/SOUL.md`** — model routing table now documents **OpenRouter slugs** and marketing/multimodal guidance.
- **Modal SDK compatibility:** `modal_app.py` now uses `Image.add_local_dir()` / `Image.add_local_file()` (replacing removed `copy_local_dir` / `copy_local_file`) for Modal client v1.x deploys.
- **Modal build fix:** local sources added to the image now use `copy=True` so subsequent build steps (`uv pip install -e /root/hermes-core[all]`, `uv pip install -e /root`) are valid under Modal v1.x image build rules.
- **Modal runtime compatibility:** replaced removed `Volume.mount()` flow with explicit restore/snapshot helpers using `Volume.listdir()` + `read_file()` + `batch_upload().put_directory(...)`, so per-Darwin state persists correctly on Modal v1.x.
- **`darwin_tools/backend_client.post_async`** — treats non-2xx HTTP as failure and **enqueues** for retry instead of silently dropping payloads.
- **Delegate neuron default model** — `OPENROUTER_DEFAULT_TASK_MODEL` (default `anthropic/claude-haiku-4.5`).

---

## [0.2.1] — 2026-05-02

Targeted improvements following post-refactor review.

### Added

- `tests/unit/test_audit_comms_files.py` — unit tests for `log_decision`, `send_email`, `upload_file` (7 tests; previous suite had no coverage for these three tools)
- `tests/integration/test_hibernate_flow.py` — end-to-end integration tests: full session lifecycle, approval→hibernate flow, JWT revocation kill signal, smoke check of all 9 tools

### Changed

- `modal_app.py` — `run_agent()` now accepts `vcc_ref` as a spawn-time argument. VCC is per-Darwin; it was wrong to assume it comes from a static Modal Secret.
- `darwin_tools/pnl_hooks._post_llm_call` — now posts to `/telemetry/llm-call` (per-call cost record for Token ROI dashboard + Mental Replay) **and** `/telemetry/stream` (rolling burn-rate chart). Previously only used the metrics stream, so individual LLM call costs were invisible.
- `darwin_tools/pnl_hooks` — added `_model_to_provider()` helper; `provider` field now included in every `/telemetry/llm-call` payload.
- `darwin_tools/backend_client` — added `_check_revocation()`: any `401`/`403` from the God Layer raises `SystemExit`, triggering `volume.commit()` before the container dies. This is the v0.1 kill-signal mechanism (JWT revocation), replacing the old Redis poll.
- `darwin_tools/entities` — `_conn` is now a module-level cached singleton instead of opening a new `sqlite3.connect()` on every `save_entity` call.
- `config/config.yaml.j2` — added `langfuse.project`, `langfuse.session_name` (tagged with `darwin_id::session_id`), and `langfuse.propagate_attributes: true` so the Mental Replay panel groups traces per agent.
- `.gitignore` — added `config/config.yaml` (the rendered per-session output) and `*.egg-info/`.

---

## [0.2.0] — 2026-05-02

Major architecture correction following a deep audit of Hermes Agent's actual capabilities. The previous implementation was re-building from scratch what Hermes already ships natively. This release aligns the codebase with the v0.1 engineering spec.

### Changed — architecture

- **Submodule corrected**: `hermes-function-calling` → `NousResearch/hermes-agent` (the actual production agent runtime, not the function-calling utility library)
- **Plugin system adopted**: Darwin tools now live in a proper Hermes plugin (`plugin.yaml` + `register(ctx)`) discovered via pip entry points. Hermes calls `register(ctx)` at session start — no manual wiring.
- **`modal_app.py` simplified**: now calls `hermes_agent.python_library.run_session()`. Hermes handles the system prompt, tool loop, memory, compression, and session lifecycle. We only bootstrap env vars and handle `DarwinHibernateSignal`.
- **LangSmith → Langfuse**: observability handled by enabling the bundled `observability/langfuse` Hermes plugin. `tracing/langsmith.py` removed.
- **Telemetry → hooks**: `telemetry/client.py` and `telemetry/heartbeat.py` replaced by `darwin_tools/pnl_hooks.py`. Hooks fire automatically on every Hermes event — no background thread, no Redis kill-signal poll.
- **Config**: `config.yaml` → `config/config.yaml.j2` (Jinja2 template rendered per session into `$HERMES_HOME/config.yaml`). Hermes reads it natively.

### Added

- `darwin_tools/plugin.yaml` — Hermes plugin manifest
- `darwin_tools/backend_client.py` — singleton signed HTTPS client to the God Layer
- `darwin_tools/pnl_hooks.py` — P&L telemetry: 7 hooks covering every LLM call, tool call, subagent stop, and session end
- `darwin_tools/entities.py` — `save_entity` tool + `darwin_entities` table creation in Hermes' `state.db`
- `config/config.yaml.j2` — full Hermes config template (Modal terminal, Browserbase browser, compression, delegation, code execution, approvals, security, Langfuse)
- `darwin_skills/shopify-checkout-2026/SKILL.md` — starter procedural skill
- `darwin_skills/stripe-2fa-handler/SKILL.md`
- `darwin_skills/browser-screenshot-loop/SKILL.md`
- `darwin_skills/episodic-self-check/SKILL.md`

### Removed — replaced by Hermes built-ins

| Removed | Replaced by |
|---|---|
| `darwin_tools/browser.py` | `browser_navigate`, `browser_snapshot`, `browser_vision`, `browser_click` (Hermes built-ins via `browser.backend: browserbase`) |
| `darwin_tools/memory.py` | `skill_manage`, `skill_view`, `skills_list`, `session_search`, Hermes context compressor |
| `darwin_tools/neurons.py` + `neurons/runner.py` | `delegate_task` (Hermes built-in) |
| `darwin_tools/human.py` (ask_human) | `clarify` (Hermes built-in) |
| `darwin_tools/cron.py` | `cronjob` (Hermes built-in) |
| `darwin_tools/delegate.py` | `delegate_task` (Hermes built-in) |
| `darwin_tools/code_runner.py` | `execute_code` (Hermes built-in) |
| `darwin_tools/hooks.py` + `darwin_tools/circuit_breaker.py` | `pnl_hooks.py` + Hermes `max_turns` + `approvals.mode: smart` |
| `telemetry/client.py`, `telemetry/heartbeat.py` | Plugin hooks + Hermes gateway |
| `telemetry/compressor.py` | Hermes native context compressor |
| `telemetry/provider_router.py` | Hermes native provider routing |
| `telemetry/redaction.py` | `security.redact_secrets: true` in Hermes config |
| `tracing/langsmith.py` | Hermes `observability/langfuse` plugin |
| `db/schema.sql`, `db/client.py`, `db/migrations/` | Hermes `state.db` (we add only `darwin_entities` from inside the plugin) |
| `prompts/working_memory.md.jinja` | Hermes builds the system prompt from SOUL.md + MEMORY.md + USER.md natively |
| `config.yaml` | `config/config.yaml.j2` (Jinja template rendered per session) |

### Simplified

- `pyproject.toml` — 5 dependencies instead of 14 (Hermes bundles the rest)
- `config.py` — 5 fields instead of 14 (most config lives in `config.yaml.j2`)
- `.env.example` — Langfuse keys replace LangSmith; no `REDIS_URL`

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

[Unreleased]: https://github.com/darwin-agents/darwin-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/darwin-agents/darwin-agent/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/darwin-agents/darwin-agent/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/darwin-agents/darwin-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/darwin-agents/darwin-agent/releases/tag/v0.1.0
