# CR-SAN-001 — MCP server foundation & dependency isolation

**Status:** PENDING
**Priority:** High
**Depends on:** —
**Labels:** phase-2, mcp, foundation
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md §3, §4 (D1/D2/D3/D5), §6

## Context

First CR of the Phase-2 MCP server (PRD §10). Establishes the adapter foundation every
later tool builds on: the pinned, **isolated** `mcp` dependency (PRD D2 — CLI stays
stdlib-only), the `FastMCP` application, the per-call store/connection helper mirroring
`cli.py::_ctx` (D3), the `ToolError` mapping for library errors (D5), the stdio entrypoint
(D1), and the in-process test harness. It is proven end-to-end with one tool,
`sandesh_setup` (simplest — no recipients, no body).

## Scope

### §S1 — Dependency isolation + install wiring (D2)
- `mcp` (PyPI), pinned `>=1.27,<2`, installed into an environment isolated from the CLI
  (venv under the install dir, or documented `pip --user`); `install.sh` provisions it and
  copies `app/mcp_server.py`.
- The CLI path (`bin/sandesh` → `cli.py` → `sandesh_db.py`/`notify.py`) imports **no**
  third-party package. `mcp` is imported only by `mcp_server.py`.

### §S2 — `app/mcp_server.py` skeleton (D1/D3/D5)
- Constructs a `FastMCP("sandesh")` application.
- `_ctx(project_id)` helper: `store = sandesh_db.store_dir(project_id)`;
  `con = sandesh_db.connect(store)`; returns what a tool needs (mirrors `cli.py::_ctx`).
- An error-mapping wrapper: a library-raised `ValueError`/`PermissionError` (or the
  library's validation/authorization errors) is re-raised as
  `mcp.server.fastmcp.exceptions.ToolError` carrying the original message.
- A `main()` entrypoint that runs `mcp.run(transport="stdio")`.

### §S3 — First tool: `sandesh_setup`
- `sandesh_setup(project_id: str)` registered via `@mcp.tool()`; delegates to
  `sandesh_db.setup(project_id)`; returns the provisioned store path (string).

### §S4 — In-process test harness
- A test module driving the server in-process via `await mcp.call_tool(...)` /
  `await mcp.list_tools()` (async wrapped with `asyncio.run` from a `unittest` test),
  against a temp store (override data home as the existing tests do).

## Acceptance criteria

### §S1
- [ ] `install.sh` installs `mcp` pinned `>=1.27,<2` into an environment isolated from the
      CLI and copies `app/mcp_server.py`.
- [ ] `python3 -c "import cli"` (or running `bin/sandesh`) succeeds with **no** third-party
      package available — only `mcp_server.py` imports `mcp`.

### §S2
- [ ] `app/mcp_server.py` constructs `FastMCP("sandesh")`.
- [ ] `_ctx(project_id)` resolves the store via `sandesh_db.store_dir(project_id)` and
      connects via `sandesh_db.connect(store)`.
- [ ] A library `ValueError`/`PermissionError` raised inside a tool surfaces to the client
      as `ToolError` with the original message (not an unhandled traceback).
- [ ] `main()` calls `mcp.run(transport="stdio")`.

### §S3
- [ ] `await mcp.list_tools()` includes a tool named `sandesh_setup`.
- [ ] Calling `sandesh_setup` with a `project_id` provisions the store (path exists) and
      returns the store path; result equals `sandesh_db.store_dir(project_id)`.

### §S4
- [ ] A test calls `sandesh_setup` in-process via `mcp.call_tool` against a temp store and
      asserts the store was created.
- [ ] `python3 tests/test_sandesh.py` (existing 24) stays green.

## Estimated size
Small: ~80–120 line module + installer edits + one test module.

## Risks / open questions
- Isolated-env install ergonomics (venv vs `pip --user`) — pick the one that keeps the CLI
  clean and is reproducible; record the choice in Implementation Notes.
- Async tool invocation from `unittest` — wrap with `asyncio.run`.

## Non-goals
- The other 9 tools (CR-SAN-002 / CR-SAN-003).
- Any wake/`notify` tool (PRD §6) or HTTP transport (PRD §9).
- Changes to `sandesh_db.py` semantics or the CLI surface.
