# CR-SAN-027 — FTS5 search engine (index, search/reindex, CLI)

**Status:** COMPLETED (shipped 2026-06-12 on develop)
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
  message_fts USING fts5(subject, body)` — a **PLAIN** fts5 table (settled empirically: contentless
  returns NULL snippets and cannot DELETE; external-content is impossible since bodies live on disk,
  not in a table column); rows keyed to `message.id` via rowid. The text copies it stores are derived
  data; the canonical body stays the file.
- `_SCHEMA` parity (fresh DBs create it; harmless re-run).
- **The schema dump EXCLUDES the FTS family**: `_live_shape`'s enumeration skips `message_fts` and its
  `message_fts_*` shadow tables (a derived, regenerable index — not schema-of-record): add the
  `message_fts` prefix to the exclusion tuple (`migrate.py` `_live_shape`, the `startswith` predicate).
  **The committed snapshot is therefore UNCHANGED** — equality must still hold (no regeneration).

### §S2 — index maintenance
- `send` inserts into the index (subject + body text; subject-only messages index the subject with
  empty body) before its single end-of-call commit — atomic with the message row.
- **Tombstone destroys the text copies (PRD-global-store T1 interplay):** `tombstone_project`
  additionally deletes the `message_fts` rows of ALL messages **sent by** the tombstoned project —
  internal AND surviving cross-project ones (their body files die with the folder; the index must not
  retain the text). Computed by sender before the address-row purge (the DRIFT-2 ordering). Messages
  the project merely RECEIVED keep their index rows (the content belongs to the sender's project).
- `reindex(con)` (lib): rebuilds the whole index from `message` rows + body files (missing body file →
  subject-only entry); idempotent; returns the indexed count.
- `sandesh reindex` CLI verb (plumbing, no args beyond the global pattern); `install.sh` runs it once
  after the consolidate block.
- **Lazy auto-reindex:** `search` detects an EMPTY index alongside a non-empty `message` table →
  triggers one `reindex` before querying (never when the index is merely sparse). `sandesh_db` stays
  print-free: the search RESULT carries a `reindexed: True` flag when the heuristic fired; the CLI
  prints a one-line notice on seeing it.

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
    (sqlite raises `OperationalError`, e.g. `unterminated string`) is caught and re-raised as a clean
    `ValueError` carrying the sqlite message.
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
- [ ] **AC9 — tombstone destroys index text.** After `tombstone_project(P2)`: zero `message_fts` rows
      remain for messages sent by P2 addresses (internal AND the surviving cross-project ones —
      asserted raw); index rows for messages P2 merely received (sent by live projects) remain; a
      search by a live recipient for a term that appeared only in P2-sent bodies returns no hits.

## Estimated size
Medium-large — one migration + dump-exclusion change, send/searching/reindex lib work, two CLI verbs,
and the widest AC matrix of the wave.

## Risks / open questions
- bm25 ordering assertions must use a deliberately skewed corpus to be deterministic.

## Non-goals
- MCP exposure of search (CR-SAN-028); semantic search (deferred per the PRD); searching other
  recipients' mail (never); any `unread_to`/notify change.
