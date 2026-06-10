-- 0002-drop-message-status — drop message.status via the SQLite 12-step rebuild.
--
-- sandesh does NOT enforce foreign keys (no PRAGMA foreign_keys in connect()),
-- so no FK toggling is needed.  The rebuilt message table still DECLARES
-- in_reply_to INTEGER REFERENCES message(id) so a migrated store's shape equals
-- a fresh _SCHEMA-without-status store (the new≡migrated invariant).
--
-- Carries the 7 surviving columns (status is dropped):
--   id, from_addr, subject, kind, in_reply_to, body_path, created_at
--
-- Rollback (sibling 0002-drop-message-status.rollback.sql) re-adds the column.

CREATE TABLE message_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    kind TEXT,
    in_reply_to INTEGER REFERENCES message(id),
    body_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO message_new (id, from_addr, subject, kind, in_reply_to, body_path, created_at)
    SELECT id, from_addr, subject, kind, in_reply_to, body_path, created_at FROM message;
DROP TABLE message;
ALTER TABLE message_new RENAME TO message;
