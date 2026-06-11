-- Rollback for 0004-xproj-grant — removes the xproj grant columns from the
-- project tracker via the SQLite 12-step rebuild (house pattern from
-- 0002/0003), carrying the 5 surviving project columns so all rows (and their
-- state) are preserved, and drops the admin table. No FK toggling needed
-- (sandesh never enables PRAGMA foreign_keys).
CREATE TABLE project_old (
    project_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','archived','tombstoned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at   TEXT,
    tombstoned_at TEXT
);
INSERT INTO project_old (project_id, state, created_at, archived_at, tombstoned_at)
    SELECT project_id, state, created_at, archived_at, tombstoned_at FROM project;
DROP TABLE project;
ALTER TABLE project_old RENAME TO project;
DROP TABLE IF EXISTS admin;
