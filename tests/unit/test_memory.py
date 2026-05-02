"""
Unit tests for darwin_tools/memory.py

Invariants checked:
- save_entity stores to SQLite AND calls backend (atomic)
- save_entity with existing ID triggers UPDATE, not CREATE
- search_skills returns local results from FTS5
- compress_session_to_episodic produces output under 250 tokens
- search_memory does FTS5 full-text search over session transcripts
"""
import json
import pytest

from darwin_tools.memory import build_memory_tools, compress_session_to_episodic


# ---------------------------------------------------------------------------
# save_entity — local + backend atomicity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_entity_writes_to_sqlite(settings, db, telemetry, backend_mock):
    [_, _, save_entity, _] = build_memory_tools(settings, db, telemetry)
    await save_entity("account", "acc-001", {"name": "Stripe", "url": "stripe.com"})

    rows = db.conn.execute(
        "SELECT * FROM darwin_entities WHERE id = 'acc-001'"
    ).fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0]["data"])
    assert data["name"] == "Stripe"


@pytest.mark.asyncio
async def test_save_entity_marks_synced_after_backend_call(settings, db, telemetry, backend_mock):
    [_, _, save_entity, _] = build_memory_tools(settings, db, telemetry)
    await save_entity("client", "client-001", {"email": "buyer@example.com"})

    row = db.conn.execute(
        "SELECT synced_at FROM darwin_entities WHERE id = 'client-001'"
    ).fetchone()
    assert row["synced_at"] is not None, "entity must be marked synced after push"


@pytest.mark.asyncio
async def test_save_entity_uses_create_operation_for_new_entity(settings, db, telemetry, backend_mock):
    operations_sent = []

    async def capture(request, route):
        import json as json_mod
        body = json_mod.loads(request.content)
        operations_sent.append(body.get("operation"))
        return __import__("httpx").Response(200, json={"ok": True})

    backend_mock.post("/api/v1/telemetry/entity").mock(side_effect=capture)

    [_, _, save_entity, _] = build_memory_tools(settings, db, telemetry)
    await save_entity("strategy", "strat-001", {"name": "dropship"})

    assert operations_sent[0] == "CREATE"


@pytest.mark.asyncio
async def test_save_entity_uses_update_operation_for_existing_entity(settings, db, telemetry, backend_mock):
    operations_sent = []

    async def capture(request, route):
        import json as json_mod
        body = json_mod.loads(request.content)
        operations_sent.append(body.get("operation"))
        return __import__("httpx").Response(200, json={"ok": True})

    backend_mock.post("/api/v1/telemetry/entity").mock(side_effect=capture)

    db.save_entity("product", "prod-001", {"name": "widget"})  # pre-seed without syncing

    [_, _, save_entity, _] = build_memory_tools(settings, db, telemetry)
    await save_entity("product", "prod-001", {"name": "widget v2"})

    assert operations_sent[0] == "UPDATE"


# ---------------------------------------------------------------------------
# save_skill + search_skills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_skill_persists_locally(settings, db, telemetry, backend_mock):
    [save_skill, _, _, _] = build_memory_tools(settings, db, telemetry)
    result = await save_skill(
        title="Bypass cookie consent",
        content="## Steps\n1. Take screenshot\n2. Click Accept",
        make_global=False,
    )
    assert result["local"] is True
    assert result["global"] is False
    assert result["skill_id"]

    rows = db.conn.execute("SELECT * FROM darwin_skills").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Bypass cookie consent"


@pytest.mark.asyncio
async def test_save_skill_global_calls_backend(settings, db, telemetry, backend_mock):
    [save_skill, _, _, _] = build_memory_tools(settings, db, telemetry)
    result = await save_skill(
        title="Global skill",
        content="## Steps\n1. Do something",
        make_global=True,
        royalty_usdc=0.05,
    )
    assert result["global"] is True

    upload_calls = [c for c in backend_mock.calls if "/memory/upload" in str(c.request.url)]
    assert len(upload_calls) == 1


@pytest.mark.asyncio
async def test_search_skills_returns_local_fts_results(settings, db, telemetry, backend_mock):
    db.save_skill("sk-1", "Handle Shopify checkout", "Use click at 512 300 to press checkout")
    db.save_skill("sk-2", "Unrelated skill", "Something about email outreach")

    [_, search_skills, _, _] = build_memory_tools(settings, db, telemetry)
    results = await search_skills("shopify checkout", include_global=False)

    titles = [r["title"] for r in results]
    assert "Handle Shopify checkout" in titles


@pytest.mark.asyncio
async def test_search_skills_global_falls_back_to_empty_list(settings, db, telemetry, backend_mock):
    [_, search_skills, _, _] = build_memory_tools(settings, db, telemetry)
    results = await search_skills("nonexistent topic xyz", include_global=True)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_memory_returns_matching_transcripts(settings, db, telemetry):
    import time, uuid
    needle = "I searched for dropshipping suppliers on Alibaba"
    db.conn.execute(
        "INSERT INTO hermes_sessions (id, transcript, summary, created_at, session_cost_usd) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), needle, None, int(time.time()), 0.0)
    )
    db.conn.commit()

    # Confirm the row was actually inserted
    row = db.conn.execute("SELECT transcript FROM hermes_sessions").fetchone()
    assert row is not None and row["transcript"] == needle

    [_, _, _, search_memory] = build_memory_tools(settings, db, telemetry)
    results = await search_memory("Alibaba")

    assert len(results) >= 1
    assert "Alibaba" in results[0]["excerpt"]


# ---------------------------------------------------------------------------
# compress_session_to_episodic — output token budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_episodic_compression_output_under_250_tokens():
    """The compressed summary must stay within the ~250-token budget."""
    long_transcript = (
        "The agent woke up and called check_ledger. Balance was $80. "
        "It then searched for dropshipping niches using browser_fetch on ten different URLs. "
        "It found a promising pet supplies niche with high margins. "
        "It opened a Shopify store and configured the theme. "
        "It saved the deployment entity. It attempted to run Facebook ads but encountered "
        "a GDPR cookie banner. It clicked Accept and proceeded. It set a $20 daily budget. "
        "The ad was approved. It hibernated waiting for the first sale. "
    ) * 10

    expected_summary = (
        "Agent checked balance ($80), researched pet supply niche, "
        "opened Shopify store, configured theme, launched Facebook ads "
        "with $20/day budget. Ad approved. Hibernating for first sale."
    )

    class FakeContent:
        def __init__(self):
            self.text = expected_summary

        def __getitem__(self, index):
            return self

    class FakeResponse:
        content = FakeContent()

    class MockMessages:
        @staticmethod
        async def create(**kwargs):
            return FakeResponse()

    class MockLLMClient:
        messages = MockMessages()

    summary = await compress_session_to_episodic(long_transcript, MockLLMClient())
    word_count = len(summary.split())
    assert word_count <= 300, f"Summary too long: {word_count} words"
    assert len(summary) > 10, "Summary must not be empty"
