-- Rollback for 0005-message-fts — drops the FTS index. The index is derived,
-- regenerable data (rebuild via `sandesh reindex`); message rows and body
-- files are untouched. Dropping the fts5 virtual table also removes its
-- message_fts_* shadow tables.
DROP TABLE IF EXISTS message_fts;
