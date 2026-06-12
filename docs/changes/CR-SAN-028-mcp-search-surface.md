# CR-SAN-028 — MCP search surface (filtered inbox params + sandesh_search)

**Status:** COMPLETED (shipped 2026-06-12 on develop)
**Priority:** Medium (agent-facing adapter over the wave's core)
**Depends on:** CR-SAN-027 (search engine)
**Labels:** wave-7, search, mcp
**Wave:** Wave 7 (inbox search)
**Design reference:** docs/research/PRD-inbox-search.md (D6 MCP part; §5 verification)

## Context

The filters (026) and the search engine (027) exist on lib + CLI. This CR makes them agent-callable:
`sandesh_inbox` grows the filter params, a new `sandesh_search` tool ships (11 → 12), and the
agent-facing docs teach the proxy-stream use case. `reindex` stays CLI/installer-only (maintenance op).

## Scope

### §S1 — `sandesh_inbox` filter params
- `sandesh_inbox` gains `sender`, `sender_project`, `kind`, `since`, `until`, `subject_like` (all
  optional, `None` = no constraint), threaded to the lib params; `sandesh_fetch` gains the same.
  Field descriptions present `sender_project` as the cross-project proxy-stream filter.

### §S2 — `sandesh_search` (the 12th tool)
- `sandesh_search(recipient, query, project_id=None, limit=20, offset=0, sender_project=None)` —
  `recipient` + `query` required; `project_id` accepted-and-unused (the lib `search()` is con-only —
  snippets come from the index, no body-file reads — so no project resolution exists; the
  `sandesh_inbox`/`sandesh_thread` pattern); `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))`;
  returns the lib result dict verbatim — `{hits: [...], total: int, limit: int, offset: int}`
  (+`reindexed` when the lazy heuristic fired) — FastMCP serializes a dict return with keys preserved
  (verified empirically); errors (`ValueError`,) → `ToolError` (the malformed-query message included);
  docstring covers FTS5 syntax, pagination, the own-mailbox boundary, that search never marks read,
  and the lazy-reindex flag.
- **No `sandesh_reindex` tool** (maintenance is CLI/installer-only).

### §S3 — agent-facing docs
- `SANDESH_INSTRUCTIONS` + `sandesh://usage` gain the search/filter story: the sender-project proxy
  stream for parallel interdependent projects, search syntax + pagination, lazy-reindex note.

### §S4 — E2E
- The stdio E2E gains one scenario: cross-project corpus → `sandesh_inbox` filtered by
  `sender_project` → `sandesh_search` round-trip (hit + snippet + total) → pagination page 2 →
  malformed query surfaces as an MCP error result.

## Acceptance criteria

- [ ] **AC1 — tool count + names.** `tools/list` returns exactly 12 tools (the 11 + `sandesh_search`);
      no tool name contains `reindex`, `tombstone`, `grant`, `revoke`, or `admin`;
      `sandesh_search` has `readOnlyHint=True`.
- [ ] **AC2 — filtered inbox via MCP.** `sandesh_inbox(recipient=r, sender_project='P2')` returns only
      P2-sender rows; the same call unfiltered returns the superset (in-process harness).
- [ ] **AC3 — search via MCP.** `sandesh_search(recipient=r, query='<seeded term>')` returns hits with
      envelope + snippet and the correct `total`; `offset` pages; results identical with and without
      an explicit `project_id` (derivation).
- [ ] **AC4 — boundary + errors.** A search for another recipient's exclusive mail returns 0 hits;
      a malformed FTS5 query surfaces as a `ToolError` (in-process) / MCP error result (stdio), not a
      crash; read-state untouched after MCP search.
- [ ] **AC5 — docs.** `SANDESH_INSTRUCTIONS` and the usage resource contain the proxy-stream +
      search/pagination content (grep markers).
- [ ] **AC6 — stdio E2E.** The §S4 scenario passes against the real `sandesh-mcp` subprocess.

## Estimated size
Small-medium — param threading, one thin tool, docs sweep, one E2E scenario.

## Risks / open questions
- (none open — the dict-return serialization was verified empirically: keys preserved verbatim in
  both text and structured content.)

## Non-goals
- Any reindex/maintenance MCP tool; Pi extension exposure (Wave 8); semantic search.
