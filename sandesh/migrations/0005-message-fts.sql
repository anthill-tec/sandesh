-- 0005-message-fts — the full-text search index over message content.
--
-- Creates `message_fts`, a PLAIN fts5 virtual table indexing `subject` and
-- `body` (CR-SAN-027 §S1). Plain — not contentless, not external-content —
-- settled empirically: contentless fts5 returns NULL snippets and cannot
-- DELETE, and external-content is impossible because bodies live on disk,
-- not in a table column. Rows are keyed to `message.id` via rowid; the text
-- copies the index stores are derived data — the canonical body stays the
-- file.
--
-- MECHANISM NOTE (harmless-re-run requirement): a fresh sandesh_db._SCHEMA
-- store ALREADY has the table (fresh-DB parity), so `IF NOT EXISTS` makes
-- this step idempotent whichever shape the store starts in. The schema dump
-- excludes the whole message_fts family (a derived, regenerable index — not
-- schema-of-record), so the committed snapshot is unchanged by this step.
--
-- Rollback (sibling 0005-message-fts.rollback.sql) drops the index — message
-- rows and body files are untouched (the index is regenerable via reindex).

CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(subject, body);
