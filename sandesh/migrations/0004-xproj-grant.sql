-- 0004-xproj-grant — cross-project grant columns + the admin table.
--
-- Adds the CR-SAN-023 §S2 grant metadata to the `project` tracker row:
-- `xproj_granted_at` (TEXT, NULL = not granted) and `xproj_granted_by` (TEXT,
-- the admin identity) — and creates the single-row `admin` table (gap-analysis
-- DEC-C: the admin is NOT an address; it must never be messageable/
-- registrable/listable, so it gets a dedicated table, CHECK (id = 1)).
--
-- MECHANISM NOTE (harmless-re-run requirement): a fresh sandesh_db._SCHEMA
-- store ALREADY has both xproj columns (fresh-DB parity), and `ALTER TABLE
-- ADD COLUMN` errors when the column exists. So instead of ALTER, this step
-- adds the columns via the house SQLite 12-step rebuild pattern (same as
-- 0002/0003): rebuild `project` with the two new columns appended, carry the
-- 5 pre-0004 columns verbatim (rows + state preserved; new columns default to
-- NULL = not granted). The rebuild is idempotent whichever shape `project`
-- starts in (legacy 5-column or fresh 7-column). sandesh does NOT enforce
-- foreign keys (no PRAGMA foreign_keys in connect()), so no FK toggling is
-- needed.
--
-- Rollback (sibling 0004-xproj-grant.rollback.sql) rebuilds project without
-- the two columns, preserving rows, and drops the admin table.

CREATE TABLE project_new (
    project_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','archived','tombstoned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at   TEXT,
    tombstoned_at TEXT,
    xproj_granted_at TEXT,
    xproj_granted_by TEXT
);
INSERT INTO project_new (project_id, state, created_at, archived_at, tombstoned_at)
    SELECT project_id, state, created_at, archived_at, tombstoned_at FROM project;
DROP TABLE project;
ALTER TABLE project_new RENAME TO project;
CREATE TABLE IF NOT EXISTS admin (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    name        TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
