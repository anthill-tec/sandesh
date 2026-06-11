# PRD — Inbox filters & search (FTS5)

**Status:** AGREED (design contract — owner resolved O1–O3, 2026-06-12)
**Owner:** Mainline - Sandesh
**Wave:** Wave 7 (inbox search)
**Related:** PRD-global-store (D1 single DB, D6 read rules, D11 cross-project grant — the feature that
makes sender-project filtering meaningful); `sandesh_db.inbox/fetch/thread` (the read surface);
`address.project` + `_address_project` (the project-resolution seam); the migration subsystem
(PRD-db-migration — the FTS index ships as a migration).

Design contract (WHY + WHAT) for **server-side inbox filtering** — headlined by filtering on the
**sender's project** — and **keyword search** over subjects + bodies via SQLite **FTS5** (stdlib,
zero new dependencies). CRs cite this via `**Design reference:**`.

---

## 1. Why

The Wave 6 cross-project grant makes a recipient's inbox a MIXED stream: home-project traffic
interleaved with mail from sibling projects. The motivating case (owner, 2026-06-12): **interdependent
projects executed in parallel — design decisions in one must be informed by the other.** The receiver
needs to filter their inbox **by the sender's project as a proxy stream** ("everything from project X")
to reconstruct the inter-project design conversation. Today the ONLY read filter is recipient +
unread/all (`inbox()`); there is no server-side filter for sender, sender-project, kind, time, or
subject — and no search at all. As histories grow across long-running projects, agents also need recall
("did anyone mention the migration gate?") over subjects AND bodies.

## 2. What it is (and is not)

**Is:**
1. **Server-side filters on the inbox read path** — `sender_project` (the headline), plus `sender`,
   `kind`, `since`/`until` (on `created_at`), and `subject_like` — composable with the existing
   `unread_only`, across lib + CLI + MCP.
2. **Keyword search** over subject + body text via an **FTS5 index** (`message_fts` virtual table) —
   ranked results (bm25) with snippets, scoped to the CALLER's own mail (to + cc), never altering
   read-state. Availability verified empirically on all target interpreters (system 3.53 / dev venv /
   installed venv) — pure stdlib, no new dependency.
3. **Index maintenance**: populated at `send` time (the body text is in memory then); an explicit
   **reindex** path backfills legacy/consolidated stores.

**Is not:** semantic/embedding search (**assessed and DEFERRED** — a heavy dependency for marginal gain
at agent-scale volumes; FTS5 covers real recall needs; if it ever earns its way in, it is an optional
`[search-semantic]` extra in the `[mcp]`/`[migrate]` isolation pattern — only if FTS5 recall proves
insufficient in practice); a mail-client UI; message mutation of any kind (read-only feature); a change
to wake semantics (`notify`/`unread_to` are untouched); cross-recipient snooping (you search/filter
YOUR mailbox — same boundary as `inbox`).

## 3. Decisions

**D1 — Filters extend `inbox()` (and ride into `fetch`).** New keyword params on
`inbox(con, recipient, unread_only=True, *, sender=None, sender_project=None, kind=None, since=None,
until=None, subject_like=None)` — each adds a WHERE clause; all composable. `fetch` gains the same
params (filtered consolidation: only the matching subset is rendered/marked read). The tombstone
read-rules (PRD-global-store D6) apply BEFORE filters — hidden traffic stays hidden regardless.

**D2 — `sender_project` is the headline filter and resolves via the existing seam.** Matching uses
`address.project` (joined) with the `_address_project` suffix fallback for purged rows — identical
mechanics to the D6 read filter, so archived senders filter fine and tombstoned senders are already
invisible. The CLI/MCP docs present it as the "project proxy stream" for parallel interdependent
projects.

**D3 — FTS5 index = a DERIVED index, not schema-of-record.** Migration `0005-message-fts.sql` creates
`message_fts` (fts5: `subject`, `body`, `content=''` external-content or plain — decided at
gap-analysis after reading real FTS5 trade-offs) + its rollback drops it. **The schema snapshot/dump
EXCLUDES the FTS family** (`message_fts` + its `message_fts_*` shadow tables): `_live_shape`'s
enumeration adds the exclusion — the index is regenerable, not schema-of-record (same reasoning as
excluding `_yoyo_migration`). `_SCHEMA` parity: fresh DBs create it too (`IF NOT EXISTS`).

**D4 — Indexing at send; reindex via BOTH explicit and lazy paths.** `send` inserts (subject + body
text — it has both in memory; subject-only messages index the subject with empty body).
**`sandesh reindex`** (CLI, plumbing verb) rebuilds the whole index from `message` rows + body files
(legacy/consolidated stores; also the repair path), and `install.sh` runs it once after `consolidate`
(idempotent). ADDITIONALLY, **`search` lazily auto-reindexes** when it detects an empty index over a
non-empty `message` table (the friendlier path for stores that predate the index and never re-ran the
installer) — a one-shot heuristic, logged, never run when the index is merely sparse.

**D5 — `search()` semantics.** `search(con, recipient, query, *, limit=20, offset=0,
sender_project=None, ...)`: FTS5 `MATCH` over **the caller's own mail ONLY (to + cc, read or unread) —
for ALL orchestrators including Mainline; no crossing inbox boundaries**. bm25 order; each hit = the
message envelope + a `snippet()` highlight; the result carries the **total match count** and is
**paginated** (`limit` default 20 + `offset`; agents page until offset+page ≥ total). Read-state NEVER
touched; the hidden-tombstoned-traffic rule applies; the D1 filters composable where they make sense
(at minimum `sender_project`). Query syntax = FTS5's (document that `"quoted phrases"`,
`AND`/`OR`/`NOT` work; malformed queries → a clean error, not a traceback).

**D6 — Surfaces.** Lib: the extended `inbox`/`fetch` + `search` + `reindex`. CLI: `sandesh inbox`
gains `--from-project` (headline) / `--from` / `--kind` / `--since` / `--until` / `--subject`;
new `sandesh search <query> --to <addr> [filters]`; plumbing `sandesh reindex`. MCP: `sandesh_inbox`
gains the filter params; new **`sandesh_search`** tool (12th; `readOnlyHint=True`); `reindex` is
CLI/installer-only (maintenance — the migrate/D8 precedent). Docstrings/instructions teach the
proxy-stream use case.

**D7 — `thread` gets the lens too (cheap).** `thread` output entries already carry `from_addr`; the
CLI/MCP layer may filter/annotate by sender project — no core change beyond what D1 provides. (Kept
minimal; a full thread-search is out of scope.)

## 4. Architecture / layout

```
sandesh/
  migrations/0005-message-fts.sql (+ .rollback)   # the fts5 virtual table (IF NOT EXISTS)
  sandesh_db.py    inbox/fetch filter params; search(); reindex(); send() indexes; _SCHEMA parity;
                   _live_shape excludes message_fts* (derived index)
  cli.py           inbox/search/reindex verbs + flags
  mcp_server.py    sandesh_inbox params; sandesh_search (12th tool, readOnlyHint)
  install.sh       reindex after consolidate
```

## 5. Verification (informs the CRs' ACs)

Deterministic lib/CLI tests per filter + composition (incl. tombstone-hidden + archived-visible
contrast); FTS: indexing at send, subject-only messages, reindex over a consolidated fixture, the
lazy auto-reindex heuristic (fires once on empty-index + non-empty store; never on a sparse index),
bm25 ordering sanity, snippet presence, pagination (limit/offset/total), the own-mailbox boundary
(a Mainline cannot search another recipient's mail), malformed-query error, read-state untouched;
snapshot-sync gate
green WITH the FTS exclusion (dump == committed snapshot on a fully-migrated store); MCP in-process +
one stdio E2E scenario; the `projects`/`migrate` regression suites untouched.

## 6. Open questions — RESOLVED by owner review (2026-06-12)

- **O1 — reindex trigger: BOTH** — explicit CLI + installer hook AND the lazy empty-index
  auto-reindex (folded into D4).
- **O2 — search scope: own mailbox for ALL orchestrators** — no crossing inbox boundaries, Mainline
  included (folded into D5).
- **O3 — results: PAGINATED** — `limit` (default 20) + `offset` + total match count (folded into D5).
