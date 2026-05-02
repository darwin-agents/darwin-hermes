-- Hermes working memory (markdown files serialised to DB)
CREATE TABLE IF NOT EXISTS hermes_memory (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER
);

-- Hermes conversation history (raw session transcripts for episodic compression)
CREATE TABLE IF NOT EXISTS hermes_sessions (
    id TEXT PRIMARY KEY,
    transcript TEXT,
    summary TEXT,
    created_at INTEGER,
    session_cost_usd REAL
);

-- Skill Documents (procedural memory)
CREATE VIRTUAL TABLE IF NOT EXISTS darwin_skills USING fts5(
    id UNINDEXED,
    title,
    content,
    created_at UNINDEXED,
    usage_count UNINDEXED,
    global_skill_id UNINDEXED
);

-- Agent-created business entities
CREATE TABLE IF NOT EXISTS darwin_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at INTEGER,
    updated_at INTEGER,
    synced_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON darwin_entities(entity_type);

-- Financial ledger (local shadow of the backend master ledger)
CREATE TABLE IF NOT EXISTS darwin_ledger (
    id TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    external_ref TEXT,
    created_at INTEGER
);

-- Hibernate schedule log
CREATE TABLE IF NOT EXISTS darwin_hibernate_log (
    id TEXT PRIMARY KEY,
    reason TEXT,
    wake_trigger_type TEXT,
    webhook_key TEXT,
    episodic_summary TEXT,
    hibernated_at INTEGER,
    woke_at INTEGER
);

-- Pending migrations tracker
CREATE TABLE IF NOT EXISTS darwin_migrations (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER
);
