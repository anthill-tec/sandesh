# CR-SAN-002 — MCP read/query tools

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-001
**Labels:** phase-2, mcp, read-tools
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md §5, §4 (D3/D4)

## Context

Second CR of the Phase-2 MCP server (PRD §10). Adds the four **read/query** tools on the
CR-SAN-001 foundation (`_ctx`, error mapping, test harness). No mutation; all delegate to
pure-DB or body-reading library functions.

## Scope

### §S1 — Read tools
Register via `@mcp.tool()`, each with a required `project_id`, each delegating per PRD §5:

| tool | signature (beyond `project_id`) | delegates to | passes |
|---|---|---|---|
| `sandesh_addressbook` | — | `addressbook(con)` | `con` |
| `sandesh_inbox` | `recipient: str, unread_only: bool = True` | `inbox(con, recipient, unread_only)` | `con` |
| `sandesh_fetch` | `recipient: str, mark: bool = True` | `fetch(con, store, recipient, mark)` | `con`, `store` |
| `sandesh_thread` | `msg_id: int` | `thread(con, msg_id)` | `con` |

- `sandesh_fetch` passes `store` (reads bodies); the other three pass only `con` (D3).
- **Serialization (gap-analysis 2026-06-06):** `addressbook` and `fetch` already return
  `list[dict]`; **`inbox` and `thread` return `list[sqlite3.Row]`**, which is NOT
  JSON-serializable by FastMCP structured output — so `sandesh_inbox` and `sandesh_thread`
  MUST normalize each `Row` → plain `dict` (`dict(row)`) before returning. "Same value"
  in AC3/AC5 means the same data as dicts.
- **Connection hygiene:** the server is long-lived and `_ctx` opens a fresh connection per
  call (the library never closes it) — each tool MUST close its connection after use
  (e.g. `try/finally con.close()`), so connections don't leak across calls.
- **Error mapping (consistency with CR-SAN-001 AC6):** each tool reuses the
  `ValueError`/`PermissionError` → `ToolError` mapping so a no-project call (`_ctx` raises
  `ValueError`) returns a clean `ToolError` to the client, not a raw traceback.

## Acceptance criteria

### §S1
- [ ] **AC1** — `await mcp.list_tools()` includes `sandesh_addressbook`, `sandesh_inbox`,
      `sandesh_fetch`, `sandesh_thread`.
- [ ] **AC2** — `sandesh_addressbook(project_id)` returns the same value as `addressbook(con)`.
- [ ] **AC3** — `sandesh_inbox(project_id, recipient, unread_only)` returns `inbox(con,
      recipient, unread_only)` **normalized to `list[dict]`** (`Row`→`dict`); `unread_only`
      defaults to `True`.
- [ ] **AC4** — `sandesh_fetch(project_id, recipient, mark)` returns the same value as
      `fetch(con, store, recipient, mark)`, passes `store`, and `mark` defaults to `True`.
- [ ] **AC5** — `sandesh_thread(project_id, msg_id)` returns `thread(con, msg_id)`
      **normalized to `list[dict]`** (`Row`→`dict`).

### Tests
- [ ] **AC6** — parity tests for all four tools against a temp store (seed via the library, call
      the tool, assert equality with the direct `sandesh_db.*` result).
      **Unwrap `call_tool`'s converted return** (per CR-SAN-001 §S4: `TextContent.text`
      for scalars, `structuredContent` for dict/list) before comparing — it does not
      return the raw library value. Normalize sqlite `Row` → plain dict on both sides.
- [ ] **AC7** — `python3 tests/test_sandesh.py` (existing 24) stays green.

## Estimated size
Small: ~40–70 lines of adapters + one test module.

## Risks / open questions
- Result serialization shape (rows → dicts) must round-trip through MCP structured output;
  if the library returns sqlite `Row` objects, the adapter normalizes to plain dicts/lists.

## Non-goals
- Mutating tools (CR-SAN-003); foundation (CR-SAN-001).
