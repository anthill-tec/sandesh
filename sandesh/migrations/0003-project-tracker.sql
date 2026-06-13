-- 0003-project-tracker — the global project tracker table + address.project.
--
-- Creates the `project` tracker (CR-SAN-022 §S1: project_id PK, state with the
-- active/archived/tombstoned CHECK, created_at default now, archived_at /
-- tombstoned_at timestamps) and adds the `project` column to `address`,
-- backfilled from the address suffix ('Mainline - Demo' → 'Demo') so every
-- project-scoped query is an indexed exact match, not string parsing.
--
-- MECHANISM NOTE (harmless-re-run requirement): a fresh sandesh_db._SCHEMA
-- store ALREADY has address.project (fresh-DB parity), and `ALTER TABLE ADD
-- COLUMN` errors when the column exists. So instead of ALTER, this step adds
-- the column via the house SQLite 12-step rebuild pattern (same as
-- 0002-drop-message-status): rebuild `address` with `project` as its last
-- column, carry the 6 pre-0003 columns verbatim, then backfill `project` with
-- the spec's exact UPDATE semantics. The rebuild is idempotent whichever shape
-- `address` starts in (legacy 6-column or fresh 7-column) — the backfill
-- recomputes the same value register() stored (the address format guarantees
-- suffix == project). sandesh does NOT enforce foreign keys (no PRAGMA
-- foreign_keys in connect()), so no FK toggling is needed.
--
-- Rollback (sibling 0003-project-tracker.rollback.sql) drops the project table
-- and rebuilds address without the column, preserving rows.

CREATE TABLE IF NOT EXISTS project (
    project_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','archived','tombstoned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at   TEXT,
    tombstoned_at TEXT
);
CREATE TABLE address_new (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT,
    project       TEXT
);
INSERT INTO address_new (address, kind, display_name, active, registered_at, registered_by)
    SELECT address, kind, display_name, active, registered_at, registered_by FROM address;
DROP TABLE address;
ALTER TABLE address_new RENAME TO address;
UPDATE address SET project = substr(address, instr(address,' - ')+3);
