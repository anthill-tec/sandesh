# CR-SAN-003 — MCP mutating tools & error mapping

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-001
**Labels:** phase-2, mcp, write-tools
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md §5, §4 (D3/D4/D5)

## Context

Third CR of the Phase-2 MCP server (PRD §10). Adds the five **mutating** tools and
exercises the CR-SAN-001 error mapping (D5) against the library's real validation and
authorization errors (bad address format, duplicate registration, unauthorized
unregister).

## Scope

### §S1 — Mutating tools
Register via `@mcp.tool()`, each with a required `project_id`, each delegating per PRD §5:

| tool | signature (beyond `project_id`) | delegates to | passes |
|---|---|---|---|
| `sandesh_register` | `addr: str, kind: str=None, display_name: str=None, by: str=None` | `register(con, addr, kind, display_name, by, project)` | `con` |
| `sandesh_unregister` | `recipient: str, requester: str` | `unregister(con, recipient, requester, project)` | `con` |
| `sandesh_send` | `from_addr: str, to=None, cc=None, subject: str="", kind=None, body_text=None` | `send(con, store, from_addr, to, cc, subject, kind, …)` | `con`, `store` |
| `sandesh_reply` | `parent_id: int, from_addr: str, subject=None, body_text=None` | `reply(con, store, parent_id, from_addr, subject, body_text, …)` | `con`, `store` |
| `sandesh_actioned` | `msg_id: int` | `set_status(con, msg_id, "actioned")` | `con` |

- `sandesh_send`/`sandesh_reply` pass `store` (write bodies); `register`/`unregister`/
  `actioned` pass only `con` (D3).
- `sandesh_register`/`sandesh_send`/`sandesh_reply` pass `project=project_id` so the library
  can enforce the address-format-matches-project rule (gap-analysis DRIFT-2 — `reply` too,
  for consistency).
- **No explicit commit (gap-analysis):** every write fn commits internally
  (`register`/`send`/`set_status`/`deactivate`/`notifier_tombstone`; `reply`→`send`;
  `unregister` soft-delete→`deactivate`). Tools reuse the read-tool pattern
  (`_ctx` → lib call → `try/finally con.close()`) and MUST NOT add a redundant `con.commit()`.
- **`to`/`cc` typing (gap-analysis DRIFT-1):** `send` iterates `to`/`cc` as lists
  (`for a in lst`), so a lone `str` would iterate **characters**. Type them `list[str] | None`
  and defensively coerce a single `str` → `[str]`.
- **Required params (applying CR-SAN-002's deferred VERIFY lesson):** genuinely-required
  params have **no silent defaults** — `addr` (register), `recipient`+`requester`
  (unregister), `from_addr`+`subject` (send), `parent_id`+`from_addr` (reply), `msg_id`
  (actioned) are required; only truly-optional params (`kind`, `cc`, `body_text`, …) default.

### §S2 — Error mapping verified (D5)
Library validation/authorization errors surface as `ToolError` with the original message
(the wrapper from CR-SAN-001), proven by tests against real failure cases.

## Acceptance criteria

### §S1
- [ ] **AC1** — `await mcp.list_tools()` includes `sandesh_register`, `sandesh_unregister`,
      `sandesh_send`, `sandesh_reply`, `sandesh_actioned`.
- [ ] **AC2** — after CR-SAN-001..003, `await mcp.list_tools()` returns **exactly 10** tools
      (the full PRD §5 set — no more, no fewer).
- [ ] **AC3** — `sandesh_register(project_id, addr, …)` registers the address (visible in
      `addressbook`); passes `project=project_id`.
- [ ] **AC4** — `sandesh_send(project_id, from_addr, to, subject, …)` creates a message
      (recipient sees it via `inbox`/`fetch`); passes `store` and `project=project_id`.
- [ ] **AC5** — `sandesh_reply(project_id, parent_id, from_addr, …)` creates a reply linked via
      `in_reply_to`; passes `store` and `project=project_id`.
- [ ] **AC6** — `sandesh_unregister(project_id, recipient, requester)` returns the library's
      result (e.g. `('tombstoned', pid)` for a live address, soft-delete otherwise).
- [ ] **AC7** — `sandesh_actioned(project_id, msg_id)` sets the message status to `actioned`.

### §S2
- [ ] **AC8** — registering a malformed address via `sandesh_register` raises `ToolError` (not
      an unhandled exception) carrying the library's validation message.
- [ ] **AC9** — an unauthorized `sandesh_unregister` (requester not `Mainline` and not self)
      raises `ToolError` with the library's authorization message.

### Tests
- [ ] **AC10** — parity/behavior tests for all five tools + the two error-mapping cases against
      a temp store. **Unwrap `call_tool`'s converted return** (per CR-SAN-001 §S4) before
      comparing; assert error cases raise `ToolError` (the SDK wraps tool exceptions —
      catch `ToolError`, not the raw `ValueError`/`PermissionError`).
- [ ] **AC11** — `python3 tests/test_sandesh.py` (existing 24) stays green.

## Estimated size
Small–medium: ~60–100 lines of adapters + one test module.

## Risks / open questions
- `send`/`reply` `to`/`cc` accept single string or list — keep the CLI's accepted shapes;
  document the accepted parameter types in the tool docstring.

## Non-goals
- Read tools (CR-SAN-002); foundation (CR-SAN-001).
- Any wake/`notify` tool (PRD §6).
