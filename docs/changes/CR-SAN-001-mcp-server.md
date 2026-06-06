# CR-SAN-001 — MCP server adapter over sandesh_db

**Status:** PENDING
**Priority:** High
**Depends on:** —
**Labels:** phase-2, mcp, adapter
**Phase:** Phase 2

## Context

Phase 1 shipped the standalone CLI (`cli.py`) and the blocking watcher (`notify.py`),
both thin layers over the pure library `sandesh_db.py`. Phase 2 adds a second front end:
an **MCP server** (`app/mcp_server.py`) that exposes the same verbs as MCP tools so an
agent can call them in-protocol instead of shelling out to the CLI.

Two hard constraints from `CLAUDE.md`:
- **The MCP SDK is the first non-stdlib dependency.** Adding it is deliberate; the real
  upstream API must be verified before coding (versions/transport assumed-from-memory is
  forbidden). This drives the §S0 investigation.
- **The wake mechanism stays out of MCP.** An MCP server cannot re-invoke a sleeping
  agent turn. `notify` remains a standalone background process; this CR does NOT touch it.

The server is a **thin adapter**: each tool validates inputs, opens the project store,
calls the matching `sandesh_db.*` function, and returns its result. No business logic
lives in the server — that stays in `sandesh_db.py`. The existing CLI must keep working
unchanged.

## Scope

### §S0 — Investigation: verify the MCP Python SDK (findings, not code)
Read the actual current MCP Python SDK source/docs (not memory). Determine and record,
as a `### S0 Findings` section appended to this CR:
- the package name + the version to pin;
- the server construction API (decorator-based tool registration vs explicit registry)
  with a minimal verified code shape;
- **transport decision: stdio vs HTTP**, with the reason (default expectation: stdio for
  a locally-launched per-agent server);
- how a tool reports a structured error to the client (so adapter error mapping matches).

The S0 Findings drive the exact ACs in §S1–§S4; where this spec says "per S0 Findings",
the finding is authoritative.

### §S1 — `app/mcp_server.py` adapter module
A new module exposing **exactly these 10 tools**, each accepting `project_id` as a
required parameter and delegating to the named library function:

| MCP tool | wraps `sandesh_db.` |
|---|---|
| `sandesh_setup` | `setup(project_id)` |
| `sandesh_register` | `register(con, addr, kind, display_name, by, project)` |
| `sandesh_unregister` | `unregister(con, recipient, requester, project)` |
| `sandesh_addressbook` | `addressbook(con)` |
| `sandesh_send` | `send(con, store, from_addr, to, cc, subject, kind, ...)` |
| `sandesh_reply` | `reply(con, store, parent_id, from_addr, subject, body_text, ...)` |
| `sandesh_inbox` | `inbox(con, recipient, unread_only)` |
| `sandesh_fetch` | `fetch(con, store, recipient, mark)` |
| `sandesh_thread` | `thread(con, msg_id)` |
| `sandesh_actioned` | `set_status(con, msg_id, "actioned")` |

- Per-tool store/connection setup mirrors `cli.py::_ctx`: resolve store via
  `sandesh_db.store_dir(project_id)`, connect via `sandesh_db.connect(store)`.
- Tools whose library fn touches bodies (`send`, `reply`, `fetch`) pass `store`; pure-DB
  tools pass only `con`.
- Address-validation and authorization errors raised by the library surface to the
  client as a structured tool error (shape per S0 Findings), not an unhandled exception.

### §S2 — Wake stays excluded
`mcp_server.py` exposes **no** `notify`/watcher tool. `notify.py` is unmodified by this
CR. (Auditable: no import of `notify` in `mcp_server.py`; no tool name containing
`notify`/`watch`.)

### §S3 — Install / dependency wiring
`install.sh` provisions the MCP dependency (pinned version per S0 Findings) — via a venv
or documented pip install — and copies `mcp_server.py` alongside the rest of `app/`. The
CLI path (`bin/sandesh` → `cli.py`) keeps working with **zero** new runtime deps.

### §S4 — Adapter-layer tests
`tests/` gains tests for the adapter that drive each tool against a temp store and assert
it returns the same result the underlying `sandesh_db.*` call returns. Existing 24 tests
stay green.

## Acceptance criteria

### §S0
- [ ] `### S0 Findings` section appended to this CR naming the package, pinned version,
      verified server-construction code shape, the stdio-vs-HTTP decision + reason, and
      the structured-error mechanism.

### §S1
- [ ] `app/mcp_server.py` exists and registers exactly the 10 tools named above (no more,
      no fewer).
- [ ] Every tool signature includes a required `project_id` parameter.
- [ ] Each tool resolves the store with `sandesh_db.store_dir(project_id)` and connects
      with `sandesh_db.connect(...)` (mirrors `cli.py::_ctx`), and calls the mapped
      `sandesh_db.*` function from the table.
- [ ] `sandesh_send`/`sandesh_reply`/`sandesh_fetch` pass the resolved `store`; the other
      seven pass only `con`.
- [ ] A library-raised validation/authorization error is returned as a structured tool
      error (shape per S0 Findings), not propagated as an unhandled exception.

### §S2
- [ ] `grep -E "import .*notify|notify|watch" app/mcp_server.py` returns no tool
      registration or import of the watcher.
- [ ] `git diff` shows `app/notify.py` unchanged by this CR.

### §S3
- [ ] `install.sh` installs the pinned MCP dependency and copies `mcp_server.py`.
- [ ] Running the CLI (`sandesh ...`) requires no third-party package (stdlib-only path
      intact).

### §S4
- [ ] New adapter tests exercise all 10 tools against a temp store and assert parity with
      the direct `sandesh_db.*` result.
- [ ] `python3 tests/test_sandesh.py` (existing 24) stays green.

## Estimated size
Small–medium: one new ~150–250 line adapter module, installer edits, one new test file.
Most risk is in §S0 (external API) and the test harness for an MCP server.

## Risks / open questions
- **MCP SDK API drift** — mitigated by §S0 verifying against real source before coding.
- **Testing an MCP server in-process** — the SDK may require a client/transport harness;
  if direct tool-function invocation isn't exposed, tests may need a stdio loopback. S0
  must record the testable entry point.
- **Dependency footprint** — first non-stdlib dep; isolate it so the CLI stays clean.

## Non-goals
- No wake/`notify` tool over MCP (§S2 — locked design).
- No change to `sandesh_db.py` semantics or the CLI surface.
- No new message verbs beyond the 10 listed.
