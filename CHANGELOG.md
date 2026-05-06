# Changelog

All notable changes to `darwin-agent` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **Deterministic local smoke harness:** Added `scripts/smoke_local.py`, a real runtime E2E check that mints a short-lived agent JWT from backend `.env`, renders local `HERMES_HOME`, runs a fixed tool sequence (`check_ledger` -> `execute_code` -> `hibernate`), and fails hard if the session does not end via `DarwinHibernateSignal`.
- **OpenRouter-aware P&L — correct model ID + robust live pricing:** The model ID was wrong — OpenRouter uses `anthropic/claude-sonnet-4.5` (dot), not `anthropic/claude-sonnet-4-5` (hyphen). Fixed in `config.yaml.j2` and the `modal_app.py` fallback default. `pnl_hooks._estimate_cost` resolves cost purely from OpenRouter's live `/models` endpoint — no hardcoded table to maintain. `_load_openrouter_pricing()` retries with back-off (5 s → 15 s → 60 s) so a transient network hiccup at container startup doesn't permanently disable cost tracking for the session. Fetch failures log at WARNING with the retry delay so they're visible in Modal logs. The cache is keyed by the exact OpenRouter model ID and covers any model OpenRouter adds without code changes.

### Fixed

- **`make smoke-local` reliability:** Replaced brittle Makefile inline heredoc Python with `scripts/smoke_local.py`, and fixed backend startup to include `--app-dir` so local uvicorn can import `main` correctly from `darwin-backend`. The target now consistently validates real local flow before deploy.
- **Integration tests updated for JSON-string tool contract:** `tests/integration/test_hibernate_flow.py` now `json.loads(...)` tool handler results (`check_ledger`, `save_entity`, `request_approval`) to match the runtime contract where Darwin tool handlers return serialized JSON strings (required to avoid `slice(None, 500, None)` failures in Hermes).
- **Local/manual runs with OpenRouter 402 no longer spin in continuation loop:** `_run_session_via_aiagent` now detects provider hard-fail text (`HTTP 402`, `Insufficient credits`, exhausted retry markers) in `final_response` and exits immediately without injecting `_HIBERNATE_NUDGE`. This prevents repeated paid retries when the provider account has no credits.
- **Unknown tool `web_search` during fresh-start flow:** `SOUL.md` referenced `web_search`/`web_extract`, but this runtime can boot without those web tool aliases enabled, causing immediate "Unknown tool" retries. Updated SOUL to use `execute_code` for web queries/fetches as the guaranteed baseline, with `browser_navigate` as an optional upgrade when browser tools are available.
- **Agent must never stop until `hibernate()` is called — session loop + deadline guard:** `_run_session_via_aiagent` in `modal_app.py` now loops instead of returning after a single `run_conversation` call. If `run_conversation` returns normally (`finish_reason=stop` without the agent calling `hibernate()`), a hard nudge (`_HIBERNATE_NUDGE`) is injected and the conversation is resumed on the same `AIAgent` instance (preserving full context). This repeats up to `_MAX_CONTINUATIONS=15` times. `DarwinHibernateSignal` (a `BaseException`) propagates out of the loop to the normal hibernation handler — it is never swallowed.
- **Container deadline guard:** `_session_deadline` is computed from the Modal timeout at session start and passed into `_run_session_via_aiagent`. At the top of each loop iteration, if fewer than `_DEADLINE_WARN_SECS=120` seconds remain, the agent receives a `_DEADLINE_NUDGE` ("URGENT — container dies in ~N seconds, call hibernate NOW") and a final `run_conversation` call is made. This ensures state is checkpointed and a wake trigger is registered before Modal's hard kill.
- **Modal timeout raised from 900 s → 10 800 s (3 h):** 15-minute sessions are too short for autonomous multi-step work. Set via `_MODAL_TIMEOUT_SECS = 3 * 3600`. Modal's hard ceiling is 86 400 s (24 h) on paid plans — raise `_MODAL_TIMEOUT_SECS` if you need longer sessions.
- Additionally: (1) upgraded default model from `claude-haiku-4.5` → `claude-sonnet-4-5` in `config.yaml.j2` (Sonnet reliably chains autonomous tool calls; Haiku does not); (2) added a "keep executing" rule to SOUL.md that explicitly forbids text-only status reports — after `check_ledger` the agent MUST call the next tool immediately; (3) added a **Fresh-Start Protocol** section to SOUL.md with a concrete 5-step sequence for first-run sessions.
- **`terminal_tool` — config.yaml.j2 still had `terminal.backend: modal`:** Even with `TERMINAL_ENV=local` set in `modal_app.py`, the rendered `config.yaml` specified Modal as the terminal backend. Changed `config.yaml.j2` to `terminal.backend: local` (the Modal container IS the sandbox; nested Modal sandboxes would fail without credentials anyway).

- **`execute_code` / `terminal_tool` — "Modal backend selected but no credentials":** `TERMINAL_ENV=modal` in the `darwin-agent-secrets` Modal secret caused Hermes to try spawning nested Modal sandboxes from inside the already-running Modal container. Since no `~/.modal.toml` or `MODAL_TOKEN_*` credentials exist inside the sandbox, every `execute_code` call crashed. Fixed by hardcoding `os.environ["TERMINAL_ENV"] = "local"` in `modal_app.py` after the secret is loaded — the container IS the sandbox, code should run directly in it.

- **`slice(None, 500, None)` root cause — tool handlers returning raw dicts:** ALL `darwin_tools` tool handlers (`audit`, `comms`, `entities`, `files`, `finance`, `lifecycle`) were returning Python `dict` objects instead of JSON strings. Hermes's `registry.dispatch()` passes the return value straight to `display.py` and `tool_guardrails.py` which do `result[:500]` — and `dict[:500]` raises `KeyError(slice(None, 500, None))`. Fixed by wrapping every `return {...}` and every `return await client.post(...)` / `return resp` with `json.dumps(...)`. This was the real source of 40+ failing deploys.
- **`DarwinHibernateSignal` swallowed by tool registry:** changed base class from `Exception` to `BaseException` so `tools/registry.py`'s `except Exception` handler no longer catches it. Previously the hibernate tool logged `ERROR [tools.registry] Tool hibernate dispatch error` and returned `{"error": "..."}` to the agent — meaning the hibernate cleanup code in `modal_app.py` (register wake trigger, push `AGENT_HIBERNATED` event) was never reached.
- **`slice(None, 500, None)` content normalization moved to `darwin_tools`:** `chat_message_content.py` and `hermes_patches.py` are now part of `darwin_tools`, not dirty edits inside the `hermes-core` submodule. `hermes_patches.apply_all()` monkey-patches `ChatCompletionsTransport.normalize_response` at plugin registration time — the submodule is clean upstream and survives `git submodule update` without losing the fix.
- **P&L cost estimation fallback source:** ``pnl_hooks._estimate_cost`` now uses the ``hermes-core`` pricing helper (``agent/usage_pricing.py``) backed by OpenRouter models pricing/cache instead of LiteLLM-specific lookups.
- **Hermes (submodule) OpenAI-compatible path:** nested ``message.content`` blobs (dict/list from OpenRouter) are coerced via ``agent/chat_message_content.py`` — at **``ChatCompletionsTransport.normalize_response``** (source of truth) and again in ``run_agent`` after normalize. Previously ``content`` could stay a dict, so ``content[:500]`` raised ``KeyError: slice(None, 500, None)`` and printed as “Error during OpenAI-compatible API call #N: slice(None, 500, None)”. These are local uncommitted edits inside the submodule working tree (chat_message_content.py is untracked; chat_completions.py and run_agent.py are dirty). Redeploy Modal to pick them up — there is nothing to pull.
- **`check_ledger`:** tolerate a God Layer JSON body missing `initial_budget` / `transactions` (defensive parsing). The canonical fix is **`LedgerResponse`** on the backend including `initial_budget` so the field is no longer stripped at serialization.
- **`modal_app.py` Hermes fallback:** import `asyncio` at module scope so `asyncio.to_thread(...)` works when `hermes_agent.python_library.run_session` is missing.
- **Hermes plugin entry point:** `[project.entry-points."hermes_agent.plugins"]` now uses `darwin = "darwin_tools"` so `ep.load()` returns the package module (Hermes requires `register` on the loaded object; `darwin_tools:register` loaded only the callable and triggered “Plugin 'darwin' has no register() function”).
- **OpenRouter ``No models provided``:** `_run_session_via_aiagent` passes explicit `provider` and `model` from `$HERMES_HOME/config.yaml` (with optional `OPENROUTER_MODEL` / `OPENROUTER_DEFAULT_MODEL` env overrides), because bare `AIAgent()` leaves `model` empty and Hermes drops the routed default during client init.
- **Darwin plugin tool registration:** all `PluginContext.register_tool(...)` calls now pass Hermes’ positional `toolset` plus keyword `name` / `schema` / `handler` / `is_async`, fixing “missing 1 required positional argument: 'handler'”.
- **OpenRouter HTTP 402 on low balance:** `_run_session_via_aiagent` passes explicit `max_tokens` from `config.yaml` `model.max_tokens`, overridden by `OPENROUTER_MAX_TOKENS`, defaulting to `4096`, so providers do not reserve completion budget against an enormous implicit cap.
- **P&L plugin hooks vs Hermes `invoke_hook`:** Darwin lifecycle hooks are synchronous again — Hermes calls hook callbacks without awaiting coroutines (`invoke_hook` uses plain ``cb(**kwargs)``). Telemetry from hooks uses `BackendClient.post_sync`, `flush_queued_telemetry_sync`, and `close_backend_client_sync`, eliminating “coroutine was never awaited” warnings for `_on_session_start`, `_pre_tool_call`, `_post_tool_call`, and `_on_session_end`.
- **Canvas THOUGHT telemetry (`post_llm_call`):** `_pre_llm_call` / `_post_llm_call` now accept **keyword-only** Hermes payloads (`assistant_response`, `model`, …). The old positional `(response, usage, model)` signatures caused a silent `TypeError` inside `invoke_hook`, so cognitive events never reached `/telemetry/stream-event` and the Infinite Canvas stayed empty despite a successful Modal run.
- **Darwin tool handlers vs `registry.dispatch`:** all `darwin_tools` async handlers now accept ``**kwargs`` so Hermes can pass `task_id`, `user_task`, `session_id`, etc. (`entry.handler(args, **kwargs)`). Without this, tools such as `check_ledger` failed with “unexpected keyword argument 'task_id'”.
- **God Layer HTTP hardening:** longer default timeouts (read **60s**) and retries on transient ``httpx`` timeouts for ledger/API calls; ``normalize_backend_base_url`` prepends ``https://`` when spawn omits the scheme; **401/403** responses log the response body before ``SystemExit`` so Fly ``AGENT_JWT_SECRET`` drift is diagnosable in Modal logs.
- **Modal observability:** `run_agent` configures stderr logging at INFO and logs spawn metadata plus a `run_conversation` summary; set `DARWIN_HERMES_VERBOSE=1` in the Modal secret for full Hermes progress (`quiet_mode` off).

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
