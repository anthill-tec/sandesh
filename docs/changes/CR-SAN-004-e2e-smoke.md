# CR-SAN-004 — MCP E2E: protocol + real-subprocess smoke tests

**Status:** COMPLETED (shipped 2026-06-06 on feature/CR-SAN-004)
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
- Spawn the server as a **real subprocess over stdio** and drive it with a `ClientSession`.
  **Command resolution (gap-analysis DRIFT-1 — must be CI-runnable):** prefer the **repo venv
  python on `app/mcp_server.py`** —
  `StdioServerParameters(command="<repo>/.venv/bin/python", args=["<repo>/app/mcp_server.py"], env={…XDG_DATA_HOME…})`
  — so T3 actually runs without a global install. (The installed `sandesh-mcp` wrapper's
  exec-correctness is already covered by `test_install` in CR-SAN-001; if `sandesh-mcp` is on
  PATH it MAY be used as an alternative.) **Skip with a clear reason only if the venv/`mcp`
  is genuinely absent.**
- Against a temp `XDG_DATA_HOME` (passed via the subprocess `env`), run a full round trip
  over real stdio: `sandesh_setup` → `sandesh_register` (sender+recipient) → `sandesh_send`
  → `sandesh_fetch`, and assert the sent message is returned through the protocol boundary.

> **Structural note (gap-analysis):** this CR adds **no new production behavior** — T2/T3 are
> E2E/characterization tests of the existing 10 tools, plus docs (§S4). They are EXPECTED to
> pass against the current implementation; a failure indicates a real protocol/serialization
> or transport bug → fix in `app/mcp_server.py` (GREEN). The classic RED→GREEN gate applies
> only in that bug case.

### §S3 — Test gating (D2 integrity)
- T2/T3 import the `mcp` client, so they run **only** when the venv is available; they are
  skipped (not failed) otherwise. The stdlib-only path `python3 tests/test_sandesh.py`
  remains free of any `mcp` import.

### §S4 — Docs
- README/CLAUDE notes: `mcp dev app/mcp_server.py` (Inspector) for manual smoke, and the
  `claude mcp add sandesh … -- sandesh-mcp` registration with scope guidance (PRD §7a).

## Acceptance criteria

### §S1
- [x] **AC1** — a T2 test connects a `ClientSession` via
      `create_connected_server_and_client_session`, calls `initialize()` then `list_tools()`,
      and sees **exactly 10** tools (PRD §5).
- [x] **AC2** — a T2 test calls one read and one mutating tool through the client and asserts
      the result matches the seeded library state.
- [x] **AC3** — a T2 test triggers a validation failure (e.g. malformed address) and asserts
      the client receives an error result carrying the library message.

### §S2
- [x] **AC4** — a T3 test spawns the server as a real subprocess over stdio (via the repo
      venv python on `app/mcp_server.py`, or the installed `sandesh-mcp` if present) with
      `StdioServerParameters`+`stdio_client`+`ClientSession`, runs `setup → register → send →
      fetch` over stdio, and asserts the fetched message body/subject matches what was sent.
- [x] **AC5** — the T3 test skips with a clear reason only when the venv/`mcp` is genuinely
      absent (it does NOT require a global `sandesh-mcp` install to run).

### §S3
- [x] **AC6** — `grep -L "import mcp" tests/test_sandesh.py` confirms the stdlib test file
      imports no `mcp`; running it needs no third-party package.
- [x] **AC7** — T2/T3 tests are skipped (not errored) when the venv is absent.

### §S4
- [x] **AC8** — README documents `mcp dev` manual smoke and the `claude mcp add` registration
      with user-vs-project scope guidance.

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

## Implementation Notes (2026-06-06)

One cycle (C0, E2E tests) + an orchestrator docs step. **Final Phase-2 CR.** No production code changed (`app/` diff empty) — pure test-coverage + docs.

- **C0** (`637ef96`) — `tests/test_mcp_e2e.py` (8 tests). **T2** (5): real `ClientSession` over `create_connected_server_and_client_session` (in-memory) — `list_tools`=10, read+mutating round-trips, validation→`isError` result. **T3** (3): real subprocess over stdio via `.venv/bin/python app/mcp_server.py` + `stdio_client`+`ClientSession` — `setup→register→send→fetch` round-trip + list_tools + error path. Gated `@skipUnless(HAS_MCP and venv present)` so it skips (not errors) without the venv. Confirmed the real-client result shape: `CallToolResult.structuredContent = {"result": <value>}`, errors via `isError`.
- **§S4 docs** (`dc8fde5`) — README MCP server section: `claude mcp add` registration (user/project scope), the 10 tools, `mcp dev` manual smoke; Roadmap → MCP DONE.
- **VERIFY** (`CR-SAN-004-VERIFY`): 8/8 green, T3 confirmed RAN (3 real subprocess spawns, not skipped), `app/` diff empty, stdlib path mcp-free, all AC1–AC8 PASS, 0 blocking.
- **Pre-merge gate**: 97/97 green; py_compile clean; coverage 52.0% lines / 64.2% funcs.
- **Phase 2 COMPLETE** — all 4 CRs (SAN-001..004) shipped: 10-tool MCP stdio server, venv isolation, T1 in-process + T2 in-memory + T3 subprocess E2E, docs.
