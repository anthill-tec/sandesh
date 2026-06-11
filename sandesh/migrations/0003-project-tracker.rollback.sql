-- Rollback for 0003-project-tracker — drops the project tracker table and
-- removes address.project via the SQLite 12-step rebuild (house pattern from
-- 0002-drop-message-status), carrying the 6 surviving address columns so all
-- rows are preserved. No FK toggling needed (sandesh never enables
-- PRAGMA foreign_keys).

DROP TABLE IF EXISTS project;
CREATE TABLE address_old (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
INSERT INTO address_old (address, kind, display_name, active, registered_at, registered_by)
    SELECT address, kind, display_name, active, registered_at, registered_by FROM address;
DROP TABLE address;
ALTER TABLE address_old RENAME TO address;
