# CR-SAN-004 — MCP E2E: protocol + real-subprocess smoke tests

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-001, CR-SAN-002, CR-SAN-003
**Labels:** phase-2, mcp, e2e, testing
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md §8a (T2/T3), §7a

## Context

CR-SAN-001..003 prove each tool with **T1** in-process tests (`FastMCP.call_tool`), which
never cross the MCP protocol boundary. This CR adds the two higher tiers from PRD §8a — the
MCP analog of "unit tests + Playwright browser E2E":
- **T2 — Integration:** a real `ClientSession` talking to the server over an **in-memory**
  transport, exercising protocol serialization, structured output, and `ToolError` over the
  wire.
- **T3 — E2E smoke:** spawn the **installed `sandesh-mcp` wrapper** as a real subprocess
  over **stdio** and drive it with a `ClientSession` — proving the deployment artifact
  (venv + wrapper + transport), not just the Python module.

Plus documentation of the manual smoke path (`mcp dev`) and client registration (§7a).

## Scope

### §S1 — T2 in-memory client↔server tests
- Use `create_connected_server_and_client_session` (`mcp.shared.memory`) to connect a real
  `ClientSession` to the Sandesh `FastMCP` app over an in-memory transport.
- Drive a representative round trip: `initialize()` → `list_tools()` → a `call_tool` for at
  least one read and one mutating tool; assert results via the client's structured
  result (`result.content` / `result.structuredContent`), and assert a validation failure
  comes back as an error result (not an exception).

### §S2 — T3 real-subprocess stdio smoke test
- Use `StdioServerParameters(command="sandesh-mcp")` (the installed wrapper; skip the test
  with a clear message if the wrapper/venv is absent) + `stdio_client` + `ClientSession`.
- Against a temp `XDG_DATA_HOME`, run a full round trip over real stdio:
  `sandesh_setup` → `sandesh_register` (sender+recipient) → `sandesh_send` →
  `sandesh_fetch`, and assert the sent message is returned through the protocol boundary.

### §S3 — Test gating (D2 integrity)
- T2/T3 import the `mcp` client, so they run **only** when the venv is available; they are
  skipped (not failed) otherwise. The stdlib-only path `python3 tests/test_sandesh.py`
  remains free of any `mcp` import.

### §S4 — Docs
- README/CLAUDE notes: `mcp dev app/mcp_server.py` (Inspector) for manual smoke, and the
  `claude mcp add sandesh … -- sandesh-mcp` registration with scope guidance (PRD §7a).

## Acceptance criteria

### §S1
- [ ] A T2 test connects a `ClientSession` via `create_connected_server_and_client_session`,
      calls `initialize()` then `list_tools()`, and sees **exactly 10** tools (PRD §5).
- [ ] A T2 test calls one read and one mutating tool through the client and asserts the
      result matches the seeded library state.
- [ ] A T2 test triggers a validation failure (e.g. malformed address) and asserts the
      client receives an error result carrying the library message.

### §S2
- [ ] A T3 test spawns `sandesh-mcp` via `StdioServerParameters`+`stdio_client`, runs
      `setup → register → send → fetch` over stdio, and asserts the fetched message body/
      subject matches what was sent.
- [ ] The T3 test skips with a clear reason when `sandesh-mcp`/the venv is not installed.

### §S3
- [ ] `grep -L "import mcp" tests/test_sandesh.py` confirms the stdlib test file imports no
      `mcp`; running it needs no third-party package.
- [ ] T2/T3 tests are skipped (not errored) when the venv is absent.

### §S4
- [ ] README documents `mcp dev` manual smoke and the `claude mcp add` registration with
      user-vs-project scope guidance.

## Estimated size
Medium: two test modules (in-memory + subprocess) + doc edits.

## Risks / open questions
- Subprocess stdio tests are async and process-spawning — keep them robust (timeouts, temp
  `XDG_DATA_HOME`, clean teardown) and skippable so CI without the venv stays green.
- The in-memory helper API is in `mcp.shared.memory`; confirm its exact signature at RED
  time against the pinned SDK.

## Non-goals
- HTTP/SSE transport E2E (PRD §9 — out of scope).
- Any new tool or library change.
