import uuid
import httpx


def build_memory_tools(settings, db, telemetry):
    async def _backend_post(path: str, body: dict) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def save_skill(
        title: str,
        content: str,
        make_global: bool = True,
        royalty_usdc: float = 0.02,
    ) -> dict:
        """
        Compile a successful workflow into a reusable Skill Document.
        Call this whenever you have successfully solved a novel, repeatable problem.
        The skill will be saved locally and optionally uploaded to the shared repository.

        Args:
            title: Short descriptive title (e.g., "Bypass Shopify 2026 cookie consent")
            content: Full markdown skill document with steps, preconditions, and notes
            make_global: If True, upload to shared repository for all agents to use
            royalty_usdc: USDC earned each time another agent uses this skill

        Returns: { "skill_id": str, "local": true, "global": bool }
        """
        skill_id = str(uuid.uuid4())
        db.save_skill(skill_id, title, content)
        result = {"skill_id": skill_id, "local": True, "global": False}
        if make_global:
            await _backend_post("/memory/upload", {
                "darwin_id": settings.darwin_id,
                "title": title,
                "content": content,
                "royalty_usdc": royalty_usdc,
            })
            result["global"] = True
        await telemetry.push_event("SKILL_CREATED", {
            "skill_id": skill_id,
            "title": title,
            "content_preview": content[:150],
            "royalty_usdc": royalty_usdc,
        })
        return result

    async def search_skills(query: str, include_global: bool = True) -> list[dict]:
        """
        Search for existing skill documents before attempting a new approach.
        Always call this when you encounter a repeatable technical challenge.

        Searches local skills first (free), then the global shared repository (cheap).

        Args:
            query: Natural language description of what you need to do
            include_global: Also search the global skill repository

        Returns: List of matching skills ordered by relevance, each with title and content
        """
        local = db.search_skills_local(query, limit=3)
        results = [{"source": "local", **s} for s in local]

        if include_global:
            resp = await _backend_post("/memory/query", {
                "darwin_id": settings.darwin_id,
                "query": query,
                "top_k": 3,
            })
            for skill in resp.get("skills", []):
                results.append({"source": "global", **skill})

        return results

    async def save_entity(
        entity_type: str,
        entity_id: str,
        data: dict,
    ) -> dict:
        """
        Save a business entity to your local database and sync it to the frontend.
        Use this whenever you create or update something meaningful:
        - A supplier account you have opened: entity_type='account'
        - A client or lead you have found: entity_type='client' or 'lead'
        - A business strategy you are running: entity_type='strategy'
        - A product you have listed: entity_type='product'
        - A deployment you have launched: entity_type='deployment'

        The entity will be visible in the frontend's "Entities" panel immediately.

        Args:
            entity_type: Free-form category (use consistent names across sessions)
            entity_id: Your chosen unique identifier for this entity
            data: Any JSON-serialisable dict describing this entity

        Returns: { "saved": true, "synced": bool }
        """
        operation = "UPDATE" if db.entity_exists(entity_id) else "CREATE"
        db.save_entity(entity_type, entity_id, data)
        await _backend_post("/telemetry/entity", {
            "darwin_id": settings.darwin_id,
            "session_id": settings.session_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "data": data,
            "operation": operation,
        })
        db.mark_entity_synced(entity_id)
        return {"saved": True, "synced": True}

    async def search_memory(query: str) -> list[dict]:
        """
        Search your historical session transcripts for past events, decisions, or data.
        Use this when you need to recall something from a session older than the current
        episodic summary.

        This is expensive — only call it when you cannot find the information in your
        current context.

        Args:
            query: What you are looking for (natural language)

        Returns: List of matching transcript excerpts with timestamps
        """
        rows = db.conn.execute(
            "SELECT * FROM hermes_sessions WHERE transcript LIKE ? ORDER BY created_at DESC LIMIT 5",
            (f"%{query}%",)
        ).fetchall()
        return [
            {
                "session_id": r["id"],
                "excerpt": r["transcript"][:500] if r["transcript"] else "",
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    return [save_skill, search_skills, save_entity, search_memory]


async def compress_session_to_episodic(raw_transcript: str, llm_client) -> str:
    """
    Compress the full session transcript to a ~200-token narrative.
    This becomes the 'last_episodic_summary' injected at the next wake-up.
    """
    response = await llm_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=250,
        messages=[{
            "role": "user",
            "content": (
                "Compress this session transcript into a single concise paragraph (max 200 tokens).\n"
                "Focus on: what was attempted, what succeeded, what failed, what is pending.\n"
                "Do NOT include raw HTML, code, or API responses. Plain English only.\n\n"
                f"TRANSCRIPT:\n{raw_transcript[-8000:]}"
            ),
        }]
    )
    return response.content[0].text
