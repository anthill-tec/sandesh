# CR-SAN-005 — Retire the `status`/disposition tool surface (`sandesh_actioned`)

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-001, CR-SAN-002, CR-SAN-003, CR-SAN-004
**Labels:** phase-2, mcp, cleanup
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md D7 (corrected lifecycle), §5, §10

## Context

PRD D7 corrected the messaging model: **read = being-acted-on, reply = done** — there is **no
`status`/disposition machine**. That makes the **`sandesh_actioned`** MCP tool (which sets
`message.status`) vestigial. This CR removes it from the MCP surface, taking the tool count
from **10 → 9**, and locks in that `sandesh_reply` exposes no `resolves`/`reply_all` (the
disposition-coupling the model rejects).

**Scope boundary (sized in gap-analysis):** this CR retires the **MCP tool surface** only. The
underlying Phase-1 core (`sandesh_db.set_status`, the `message.status` column, the CLI
`actioned` command, `reply(resolves=…)`, and `inbox`'s surfaced `status`) is a larger,
schema-touching change with its own blast radius (the 24 baseline tests, the CLI) — handled as
a **deferred follow-up** (see §S3), not bundled here.

## Scope

### §S1 — Remove the `sandesh_actioned` MCP tool
- Delete the `@mcp.tool() def sandesh_actioned(...)` from `app/mcp_server.py`.
- The server now exposes **exactly 9** tools: `sandesh_setup`, `sandesh_register`,
  `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`, `sandesh_reply`,
  `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread`.
- No other tool changes; `mcp` stays imported only in `mcp_server.py`.

### §S2 — Lock: `sandesh_reply` has no `resolves` / `reply_all`
- Confirm (and lock with a test) that `sandesh_reply`'s parameters are exactly
  `parent_id, from_addr, project_id, subject, body_text` — **no `resolves`, no `reply_all`**
  (already true post-CR-SAN-003; this CR makes it an explicit, tested invariant).

### §S3 — Core status retirement = DEFERRED (record, don't implement here)
Removing `message.status` / `set_status` / CLI `actioned` / `reply(resolves)` / `inbox.status`
touches the Phase-1 library + schema + the 24 stdlib tests. **Gap-analysis must enumerate the
exact callers + blast radius**; the finding is recorded under `### S3 Findings`, and the core
retirement is filed as a **follow-up CR** (or confirmed in-scope only if gap-analysis shows it
is genuinely small). Default: **defer**.

## Acceptance criteria

- [ ] **AC1** — `await mcp.list_tools()` returns **exactly 9** tools and does **not** include
      `sandesh_actioned`.
- [ ] **AC2** — `grep -n "sandesh_actioned" app/mcp_server.py` returns nothing (tool removed).
- [ ] **AC3** — `sandesh_reply`'s signature has **no** `resolves`/`reply_all` parameter
      (asserted by a test inspecting the tool's input schema / signature).
- [ ] **AC4** — `tests/test_mcp_mutating_tools.py` is updated: no `sandesh_actioned` test; any
      "exactly 10 tools" assertion becomes "exactly 9"; the file stays green.
- [ ] **AC5** — full regression green: the MCP suites + `python3 tests/test_sandesh.py`
      (existing 24) all pass; tool count is 9.
- [ ] **AC6** — `### S3 Findings` records the core-status-retirement blast radius + the
      deferred follow-up CR reference (or a justified in-scope decision).

## Estimated size
Small: delete one tool + update its tests + the count assertion + a reply-signature lock test.

## Risks / open questions
- The "exactly 10" assertion lives in `tests/test_mcp_mutating_tools.py` (CR-SAN-003) — must
  flip to 9; confirm no other test hard-codes 10.
- §S3 core retirement scope — gap-analysis decides defer vs in-scope (default defer).

## Non-goals
- Removing `message.status` / `set_status` / CLI `actioned` / `reply(resolves)` from the core
  library (deferred — §S3).
- Any change to the other 9 tools or messaging semantics beyond removing the disposition tool.
