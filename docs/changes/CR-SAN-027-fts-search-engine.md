# CR-SAN-027 — FTS5 search engine (index, search/reindex, CLI)

**Status:** PENDING
**Priority:** High (the wave's recall capability)
**Depends on:** CR-SAN-026 (filter layer; `sender_project` composition)
**Labels:** wave-7, search, fts, migration, installer
**Wave:** Wave 7 (inbox search)
**Design reference:** docs/research/PRD-inbox-search.md (D3, D4, D5, D6 CLI part; §5 verification)

## Context

Filters (CR-SAN-026) slice the inbox by metadata; recall over CONTENT ("did anyone mention the
migration gate?") needs full-text search. SQLite FTS5 is compiled into all target interpreters
(verified) — keyword search costs zero new dependencies. This CR ships the index, its maintenance
(send-time + explicit/lazy reindex), and the `search`/`reindex` lib + CLI surfaces.

## Scope

### §S1 — the index (migration + parity + dump exclusion)
- Migration `0005-message-fts.sql` (+ rollback dropping it): `CREATE VIRTUAL TABLE IF NOT EXISTS
  message_fts USING fts5(subject, body, ...)` — content-mode (external-content vs plain) settled at
  gap-analysis from real FTS5 trade-offs; rows keyed to `message.id` (rowid).
- `_SCHEMA` parity (fresh DBs create it; harmless re-run).
- **The schema dump EXCLUDES the FTS family**: `_live_shape`'s enumeration skips `message_fts` and its
  `message_fts_*` shadow tables (a derived, regenerable index — not schema-of-record). Committed
  snapshot regenerated; the CI snapshot-sync gate stays green.

### §S2 — index maintenance
- `send` inserts into the index (subject + body text; subject-only messages index the subject with
  empty body) in the same transaction as the message row.
- `reindex(con)` (lib): rebuilds the whole index from `message` rows + body files (missing body file →
  subject-only entry); idempotent; returns the indexed count.
- `sandesh reindex` CLI verb (plumbing, no args beyond the global pattern); `install.sh` runs it once
  after the consolidate block.
- **Lazy auto-reindex:** `search` detects an EMPTY index alongside a non-empty `message` table →
  triggers one `reindex` before querying (never when the index is merely sparse).

### §S3 — `search()`
- `search(con, recipient, query, *, limit=20, offset=0, sender_project=None)`:
  - FTS5 `MATCH` over **the caller's own mail only** (messages with a `message_recipient` row for
    `recipient`, to + cc, read or unread) — for ALL orchestrators including Mainline; no crossing
    inbox boundaries;
  - bm25 ranking; each hit carries the message envelope fields + a `snippet()` highlight;
  - returns a paginated result: the hit page + the **total match count** (`limit` default 20,
    `offset` for paging);
  - read-state untouched; the tombstone hidden-traffic rule applies; `sender_project` composes;
  - FTS5 query syntax passes through (`"quoted phrases"`, `AND`/`OR`/`NOT`); a malformed query
    raises a clean `ValueError` (no sqlite traceback).
- `sandesh search <query> --to <addr> [--from-project P] [--limit N] [--offset N]` CLI verb rendering
  hits with snippets + a `total` line.

## Acceptance criteria

- [ ] **AC1 — index shape + gate.** After `migrate --all`, `message_fts` exists; `migrate
      --dump-schema` contains NO table whose name starts with `message_fts`; dump equals the
      regenerated committed snapshot (CI gate green); `0005` rolls back cleanly (index gone, messages
      untouched); fresh-`_SCHEMA` parity + harmless re-run hold.
- [ ] **AC2 — send-time indexing.** After `send` with a body, searching a body-only word finds the
      message; a subject-only message is found by a subject word; the FTS insert is atomic with the
      message row (a refused send indexes nothing).
- [ ] **AC3 — search semantics.** Seeded corpus: bm25 returns the better match first (a message with
      the term in subject+body ranks above body-only); each hit includes the envelope (id, from,
      subject, created_at) + a snippet containing the matched term; `read_at` values are unchanged
      after search.
- [ ] **AC4 — own-mailbox boundary.** A recipient's search NEVER returns a message they are not a
      recipient of — including a Mainline searching while OTHER addresses in its project hold matching
      mail (exact AC: Mainline's result count excludes those).
- [ ] **AC5 — pagination.** 25 matching messages: `limit=20, offset=0` returns 20 hits + `total=25`;
      `offset=20` returns the remaining 5; `offset>=total` returns an empty page with `total=25`.
- [ ] **AC6 — reindex paths.** `reindex` over a fixture with pre-FTS history (raw-inserted message
      rows + body files, empty index) indexes all and search finds them; second run is idempotent
      (same count, no duplicates); `install.sh` contains the reindex invocation after the consolidate
      block; the **lazy** path: empty index + non-empty store → first `search` call returns the same
      results as post-explicit-reindex; a SPARSE index (one indexed, one raw-inserted) does NOT
      trigger it.
- [ ] **AC7 — filters + hidden traffic.** `search(..., sender_project='P2')` returns only P2-sender
      hits; matches from a tombstoned project's senders never surface.
- [ ] **AC8 — malformed query.** An invalid FTS5 expression (e.g. unbalanced quote) raises
      `ValueError` containing a readable message; the CLI exits 1 with `[sandesh]` prefix.

## Estimated size
Medium-large — one migration + dump-exclusion change, send/searching/reindex lib work, two CLI verbs,
and the widest AC matrix of the wave.

## Risks / open questions
- FTS5 content-mode choice (external-content saves space but complicates reindex/rollback; plain
  duplicates text) — settle at gap-analysis from upstream docs, don't assume.
- bm25 ordering assertions must use a deliberately skewed corpus to be deterministic.

## Non-goals
- MCP exposure of search (CR-SAN-028); semantic search (deferred per the PRD); searching other
  recipients' mail (never); any `unread_to`/notify change.
