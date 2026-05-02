---
name: episodic-self-check
description: What to write to MEMORY.md before hibernating each session
tags: [memory, hibernation, self-management]
version: "1.0"
---

# Skill: Episodic Self-Check Before Hibernation

## Trigger

Use before EVERY `hibernate()` call. This is mandatory — your future self
depends on a clear session summary in MEMORY.md.

## Why

Hermes' context compressor handles the raw conversation history automatically.
But `MEMORY.md` is your personal durable notes store — it persists forever,
survives container destruction, and is injected into every future session.
Use it for facts you'll need next time. The raw transcript is not enough.

## What to Write

Call `memory(action="add", target="memory", content="...")` with:

**Always include:**
- What was the primary goal this session?
- What was accomplished (specific, measurable)?
- What failed and why (so future-you doesn't repeat the mistake)?
- What is pending and what's the next step?
- Any new account/service credentials reference (NOT the actual credentials — just that they exist and where they're stored)
- Current strategy status (e.g., "Shopify store live at petsupply-niche.com — awaiting first sale")

**Include if relevant:**
- Patterns observed (e.g., "Alibaba blocks headless browsers after 3 requests — use web_extract instead")
- Supplier/client relationship status
- Any time-sensitive items (e.g., "Domain renewal due 2026-06-15")

## Format

Keep it under 400 chars per entry. Be terse and factual. Use bullet points.

## Steps

1. Before calling hibernate(), review what happened this session
2. Identify 2-5 durable facts worth keeping
3. `memory(action="add", target="memory", content="<facts>")` 
4. Then call `hibernate(reason="<reason>", ...)`

## Notes

- Do NOT write raw API responses, HTML, or JSON into MEMORY.md
- Do NOT write things Hermes will remember automatically (session history)
- DO write business facts, lessons learned, and pending items
