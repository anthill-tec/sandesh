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

### §S1 — Dependency isolation: venv + wrapper (D2)
- `install.sh` creates a **dedicated venv** at `<DEST>/.venv` and installs the pinned
  `mcp` (`>=1.27,<2`) into it — the only place `mcp` ever lives.
- `install.sh` writes a wrapper launcher `bin/sandesh-mcp` that
  `exec "$DEST/.venv/bin/python" "$DEST/app/mcp_server.py" "$@"`, and symlinks it onto PATH
  next to `sandesh`. The MCP server runs on the **venv interpreter**; `bin/sandesh` (CLI)
  keeps running on system `python3`.
  - Note: `install.sh` already copies every `app/*.py` via its `cp "$SRC/app/"*.py` glob,
    so `mcp_server.py` is copied automatically — no copy-logic change needed.
- The CLI path (`bin/sandesh` → `cli.py` → `sandesh_db.py`/`notify.py`) imports **no**
  third-party package. `mcp` is imported only by `mcp_server.py`.

### §S2 — `app/mcp_server.py` skeleton (D1/D3/D4/D5)
- Constructs a `FastMCP("sandesh")` application.
- `_ctx(project_id=None)` helper: resolve `project_id` → falls back to
  `os.environ["SANDESH_PROJECT"]` if `project_id` is falsy, raising a clear error if
  neither is set (D4); `store = sandesh_db.store_dir(project_id)`;
  `con = sandesh_db.connect(store)`; returns what a tool needs (mirrors `cli.py::_ctx`).
- An error-mapping wrapper: a library-raised `ValueError`/`PermissionError` is re-raised as
  `mcp.server.fastmcp.exceptions.ToolError` carrying the original message.
- A `main()` entrypoint that runs `mcp.run(transport="stdio")`.

### §S3 — First tool: `sandesh_setup`
- `sandesh_setup(project_id: str = None)` registered via `@mcp.tool()`; resolves the
  project via `_ctx`'s `$SANDESH_PROJECT` fallback (D4); delegates to
  `sandesh_db.setup(<resolved project>)`; returns the provisioned store path (string).

### §S4 — In-process test harness
- A test module driving the server in-process via `await mcp.call_tool(...)` /
  `await mcp.list_tools()` (async wrapped with `asyncio.run` from a `unittest` test),
  against a temp store (override `XDG_DATA_HOME` to a temp dir as the existing tests do).
- **`call_tool` return shape (applies to all parity ACs, this CR and §S2/§S3 of
  CR-SAN-002/003):** `FastMCP.call_tool(...)` returns *converted* output
  (`Sequence[ContentBlock] | dict`, `convert_result=True`), **not** the raw return value —
  a `str` return arrives as a `TextContent` block (read `.text`); a dict/list return
  arrives as `structuredContent`. "Result equals `sandesh_db.X(...)`" ACs mean the
  **unwrapped** content equals the library value (unwrap the block / read
  `structuredContent`, or assert against the registered tool function directly).

## Acceptance criteria

### §S1
- [ ] `install.sh` creates `<DEST>/.venv` and installs `mcp` pinned `>=1.27,<2` into it.
- [ ] `install.sh` writes `bin/sandesh-mcp` that execs the venv python on
      `app/mcp_server.py`, and symlinks it onto PATH; running it starts the server.
- [ ] `python3 -c "import cli"` (or running `bin/sandesh`) succeeds with **no** third-party
      package available — only `mcp_server.py` imports `mcp`.

### §S2
- [ ] `app/mcp_server.py` constructs `FastMCP("sandesh")`.
- [ ] `_ctx` resolves the store via `sandesh_db.store_dir(<project>)` and connects via
      `sandesh_db.connect(store)`, where `<project>` is the passed `project_id` or, if
      omitted, `$SANDESH_PROJECT`; a clear error is raised when neither is set (D4).
- [ ] A library `ValueError`/`PermissionError` raised inside a tool surfaces to the client
      as `ToolError` with the original message (not an unhandled traceback).
- [ ] `main()` calls `mcp.run(transport="stdio")`.

### §S3
- [ ] `await mcp.list_tools()` includes a tool named `sandesh_setup`.
- [ ] Calling `sandesh_setup` with a `project_id` provisions the store (path exists) and
      returns the store path; the **unwrapped** tool result (per §S4 — `TextContent.text`)
      equals `sandesh_db.store_dir(project_id)`.
- [ ] Calling `sandesh_setup` with no `project_id` but `$SANDESH_PROJECT` set provisions
      that project's store (D4 fallback).

### §S4
- [ ] A test calls `sandesh_setup` in-process via `mcp.call_tool` against a temp store and
      asserts the store was created.
- [ ] `python3 tests/test_sandesh.py` (existing 24) stays green.

## Estimated size
Small: ~80–120 line module + installer edits + one test module.

## Risks / open questions
- venv creation in `install.sh` must be reproducible (use `python3 -m venv`; pin via
  `pip install "mcp>=1.27,<2"`); record the exact commands in Implementation Notes.
- Async tool invocation from `unittest` — wrap with `asyncio.run`.

## Non-goals
- The other 9 tools (CR-SAN-002 / CR-SAN-003); E2E/protocol tests (CR-SAN-004).
- Any wake/`notify` tool (PRD §6) or HTTP transport (PRD §9).
- Changes to `sandesh_db.py` semantics or the CLI surface.
