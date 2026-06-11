-- 0001-baseline — reproduces sandesh_db._SCHEMA exactly (pre-0002).
--
-- A store provisioned by applying this migration from empty is byte-for-table
-- identical (PRAGMA table_info) to one created by sandesh_db.setup(). The four
-- CREATE TABLE statements below are a VERBATIM copy of sandesh_db._SCHEMA —
-- including message.status, which 0002 will later drop. Keep them in sync with
-- _SCHEMA character-for-character (column defs, defaults, PK constraints); there
-- are no standalone CREATE INDEX statements because _SCHEMA has none.
--
-- No rollback step: 0001 is the baseline; rolling it back would drop the whole
-- store, which the engine never does.

CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,                 -- '<Orchestrator> - <Project>'  (unique → rejects dupes)
    kind          TEXT,                             -- 'mainline' | 'track'
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,    -- soft-delete (history-safe)
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,                      -- always present (the minimal content)
    kind        TEXT,                               -- request | directive | fyi | reply
    status      TEXT NOT NULL DEFAULT 'open',       -- open | actioned | closed
    in_reply_to INTEGER REFERENCES message(id),     -- thread link (NULL = top-level)
    body_path   TEXT,                               -- NULL = subject-only; else FULL path to messages/msg-<id>.md
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',          -- 'to' (wakes) | 'cc' (silent)
    read_at    TEXT,                                -- NULL = unread (per recipient!)
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,                  -- one live poller per address (dedup key)
    pid          INTEGER,
    token        TEXT,                              -- uuid per launch (guards PID reuse)
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE     -- 1 = shutdown requested (cooperative eviction)
);
