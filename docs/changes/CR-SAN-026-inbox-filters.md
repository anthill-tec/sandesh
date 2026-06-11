# CR-SAN-026 — Server-side inbox/fetch filters (lib + CLI)

**Status:** PENDING
**Priority:** High (ships the headline sender-project proxy stream)
**Depends on:** — (post-Wave-6 develop)
**Labels:** wave-7, search, filters
**Wave:** Wave 7 (inbox search)
**Design reference:** docs/research/PRD-inbox-search.md (D1, D2, D7; §5 verification)

## Context

A recipient's inbox is a mixed stream since the cross-project grant: home-project traffic interleaved
with sibling-project mail. The only read filter today is recipient + `unread_only`. This CR adds the
composable server-side filters — headlined by the **sender's project** (the proxy stream for parallel
interdependent projects) — on the lib read path and the CLI. No FTS, no schema change.

## Scope

### §S1 — `inbox` filter params
- `inbox(con, recipient, unread_only=True, *, sender=None, sender_project=None, kind=None,
  since=None, until=None, subject_like=None)`:
  - `sender` — exact match on `message.from_addr`;
  - `sender_project` — the sender's project: match via `address.project` with the `_address_project`
    suffix fallback for purged rows (the read-rules seam);
  - `kind` — exact match on `message.kind`;
  - `since` / `until` — inclusive bounds on `message.created_at` (same text-timestamp format the
    column stores; accepts `YYYY-MM-DD` and `YYYY-MM-DD HH:MM:SS`);
  - `subject_like` — case-insensitive substring on `message.subject`;
  - all composable with each other and with `unread_only`; `None` = no constraint.
- The tombstone hidden-traffic rule applies BEFORE filters (hidden mail never matches anything).

### §S2 — `fetch` rides the same params
- `fetch(con, store, recipient, mark=True, *, <same filter params>)`: only the matching subset is
  rendered and (when `mark`) marked read — non-matching unread mail stays unread.

### §S3 — CLI flags
- `sandesh inbox` gains `--from-project` (headline), `--from`, `--kind`, `--since`, `--until`,
  `--subject`; `sandesh fetch` gains the same flags. Flags map 1:1 onto the §S1 params.

## Acceptance criteria

- [ ] **AC1 — sender_project filter.** With P1+P2 active/granted and a P1 recipient holding mail from
      both projects: `inbox(con, r, sender_project='P2')` returns ONLY rows whose sender's project is
      P2; `sender_project='P1'` only the P1 rows; a project with no matching mail returns `[]`.
- [ ] **AC2 — each remaining filter.** `sender` exact-matches one address; `kind='request'` returns
      only that kind (NULL kinds excluded); `since`/`until` bound `created_at` inclusively (rows
      seeded with explicit timestamps); `subject_like='gate'` matches case-insensitively
      ('Gate review' included), no regex/glob semantics.
- [ ] **AC3 — composition.** `sender_project` + `kind` + `unread_only=False` together return exactly
      the intersection; all-`None` filters reproduce today's unfiltered result row-for-row.
- [ ] **AC4 — tombstone/archive interplay.** Mail from a tombstoned project stays invisible regardless
      of filters (even `sender_project=<that id>` returns `[]`); mail from an archived project is
      filterable normally.
- [ ] **AC5 — filtered fetch marks only the subset.** `fetch(..., sender_project='P2')` renders and
      marks read ONLY the P2 mail; the recipient's P1 unread mail still appears in a subsequent
      unfiltered `fetch`.
- [ ] **AC6 — CLI.** `sandesh inbox --to <r> --from-project P2` lists only P2-sender rows (in-process
      capture); `sandesh fetch --to <r> --from-project P2` renders/marks only those; each remaining
      flag drives its param (one spot-check per flag).
- [ ] **AC7 — wake path untouched.** `unread_to` accepts no filter params and its behaviour is
      unchanged (signature + existing tests).

## Estimated size
Small-medium — one query builder in `inbox`, param threading into `fetch`, six CLI flags ×2 verbs,
a broad but mechanical AC matrix.

## Risks / open questions
- `created_at` comparisons are TEXT — pin the accepted input formats (AC2) so lexicographic compare
  is correct; reject obviously malformed values with a clean error.

## Non-goals
- FTS/search/reindex (CR-SAN-027); MCP exposure (CR-SAN-028); any change to `unread_to`/notify.
