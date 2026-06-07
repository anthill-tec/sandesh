# CR-SAN-005 — Retire the `status`/disposition tool surface (`sandesh_actioned`)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-005)
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

### S3 Findings (gap-analysis 2026-06-07)
Core-status blast radius — **confirmed substantial → DEFER to a follow-up CR**
(candidate **CR-SAN-012 — core `status`/`message.status` retirement**):
- `sandesh_db.set_status` (line 280) is called by **`reply(resolves=True)`** (line 276) **and**
  the CLI **`cmd_actioned`** (`cli.py:199`) — not just the MCP tool.
- **`message.status` is read by `inbox`** (`sandesh_db.py:291` SELECT includes `m.status`), so
  the column surfaces in inbox output.
- CLI exposes **`actioned`** (`cli.py:275-278`) and **`reply --resolves`** (`cli.py:258,138`).
- **The 24 baseline tests depend on it**: `tests/test_sandesh.py:167-168` asserts
  `SELECT status … == "actioned"` after a resolve.
Therefore removing the core status machine needs library + CLI + **schema** changes and would
break the Phase-1 baseline suite — out of scope for this CR. **CR-SAN-005 retires only the MCP
`sandesh_actioned` tool** (§S1); the core machine stays (dormant from the MCP surface) until the
deferred follow-up.

## Acceptance criteria

- [x] **AC1** — `await mcp.list_tools()` returns **exactly 9** tools and does **not** include
      `sandesh_actioned`.
- [x] **AC2** — `grep -n "sandesh_actioned" app/mcp_server.py` returns nothing (tool removed).
- [x] **AC3** — `sandesh_reply`'s signature has **no** `resolves`/`reply_all` parameter
      (asserted by a test inspecting the tool's input schema / signature).
- [x] **AC4** — the tool-count/`sandesh_actioned` assertions are updated in **both** test files
      (gap-analysis DRIFT-1): `tests/test_mcp_mutating_tools.py` (remove the `assertIn("sandesh_actioned")`,
      flip `test_list_tools_returns_exactly_ten_tools` 10→9, remove the `sandesh_actioned` behavior
      test ~lines 595–637, fix docstrings) **and** `tests/test_mcp_e2e.py` (the `len(names)==10`
      at ~line 103 → 9); both files stay green.
- [x] **AC5** — full regression green: the MCP suites + `python3 tests/test_sandesh.py`
      (existing 24) all pass; tool count is 9.
- [x] **AC6** — `### S3 Findings` records the core-status-retirement blast radius + the
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

## Implementation Notes (2026-06-07)

One cycle (C0), agent-dispatched, then VERIFY → pre-merge.

- **C0** — RED (`9129ab0`): flipped both test files to the 9-tool contract (`tests/test_mcp_mutating_tools.py` + `tests/test_mcp_e2e.py` — count 10→9, `assertNotIn sandesh_actioned`, removed the actioned behavior tests) + added `sandesh_reply`-has-no-`resolves`/`reply_all` lock tests. GREEN (`9797a65`): deleted the `@mcp.tool sandesh_actioned` block from `app/mcp_server.py`. **Tool count 10 → 9.**
- **Core status machine untouched** (deferred per §S3 → candidate **CR-SAN-012**): `sandesh_db.set_status` / `message.status` / CLI `actioned` / `reply(resolves)` remain (dormant from the MCP surface; still exercised by the 24 stdlib baseline). `git diff develop -- app/sandesh_db.py app/cli.py app/notify.py` is empty.
- **VERIFY** (`CR-SAN-005-VERIFY`): 67/67 green, all AC1–AC6 PASS, 0 findings, boundaries clean.
- **Pre-merge gate**: 97/97 green; py_compile clean; coverage 51.5% lines / 63.6% funcs.
- MCP surface now matches D7 (read=acting, reply=done; no disposition tool).
