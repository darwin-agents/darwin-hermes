"""
db/ — vestigial package directory.

All Darwin-specific persistence is handled by darwin_tools/entities.py which
opens $HERMES_HOME/state.db directly via sqlite3 and creates the darwin_entities
table on first write. Hermes manages the rest of state.db natively.

There is no separate database client here — that was the v0 design, removed
when we adopted Hermes as the storage layer.
"""
