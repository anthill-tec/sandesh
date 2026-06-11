-- Rollback for 0002-drop-message-status — restores the message.status column
-- (AC6).  A simple ADD COLUMN suffices: the rebuilt message table is otherwise
-- unchanged, so re-adding status TEXT NOT NULL DEFAULT 'open' returns the table
-- to its pre-0002 shape (all existing rows default to 'open').

ALTER TABLE message ADD COLUMN status TEXT NOT NULL DEFAULT 'open';
