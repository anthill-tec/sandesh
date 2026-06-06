# PRD — Sandesh MCP Server

**Status:** DRAFT
**Owner:** Mainline - Sandesh
**Phase:** Phase 2
**Supersedes:** the Phase-2 sketch in `CLAUDE.md` (this PRD is now the authoritative design)

This is the design contract (WHY + WHAT) for exposing Sandesh's verbs over the Model
Context Protocol. CRs derived from it cite it via `**Design reference:**` and implement
the HOW; they do not re-derive design here.

---

## 1. Why

Today an agent uses Sandesh by **shelling out to the CLI** (`sandesh send …`,
`sandesh fetch …`). That works but:
- couples every call to argv quoting, exit-code parsing, and stdout scraping;
- gives the agent no typed tool surface (no schema, no structured results);
- makes the "what verbs exist and what do they take" contract implicit.

An **MCP server** turns the verbs into first-class, schema-described tools the agent calls
in-protocol. The library (`sandesh_db.py`) is already a clean, side-effect-free model, so
the server is a **thin adapter** — the same role `cli.py` plays for the terminal.

This is **Phase 2** on the roadmap. Phase 1 (CLI + watcher) is done and stays unchanged.

## 2. What it is (and is not)

**Is:** a second front end over `sandesh_db.py` — an MCP server (`app/mcp_server.py`)
exposing the message/addressbook verbs as MCP tools, each routed by an explicit
`project_id`. Stateless per call: resolve store → connect → call `sandesh_db.*` → return.

**Is not:**
- a replacement for the CLI — the CLI keeps working, stdlib-only;
- the **wake path** — see §6. The `notify` watcher stays a standalone background process.
- a new home for business logic — semantics stay in `sandesh_db.py`.

## 3. Verified platform facts (MCP Python SDK)

Verified against the real upstream source (`mcp` **v1.27.2**, fetched via opensrc), not
memory. These are design inputs; a CR re-confirms the pin at implementation time.

| Fact | Value |
|---|---|
| Package / pin | `mcp` (PyPI), pin `>=1.27,<2`; install extra `mcp[cli]` optional |
| Python requirement | `requires-python >=3.10` (host runs 3.14 ✓) |
| Server class | `FastMCP` from `mcp.server.fastmcp` |
| Tool registration | `@mcp.tool()` decorator on a typed (sync or async) function; name defaults to fn name, overridable via `name=` |
| Run / transport | `mcp.run(transport="stdio")`; transports are `"stdio" | "sse" | "streamable-http"`, default `stdio` |
| Structured output | auto-derived from return type annotation (`structured_output` override available) |
| Error surface | raise `ToolError` (`mcp.server.fastmcp.exceptions`) → returned to client as a tool error; unhandled exceptions also surface but lose shape |
| In-process test entry | `await mcp.call_tool(name, args)` and `await mcp.list_tools()`; full loopback via `mcp.shared.memory` |

## 4. Design decisions

**D1 — Transport: stdio.** Each agent session launches its own per-session server locally
(mirrors the Model-B "one process per session" shape; no port management, no auth). HTTP
transports are out of scope; revisit only if a shared multi-client server is ever needed.

**D2 — Dependency isolation.** `mcp` is the first non-stdlib dependency. It is imported
**only** by `mcp_server.py`. The CLI path (`bin/sandesh` → `cli.py` → `sandesh_db.py` /
`notify.py`) must remain importable with **zero** third-party packages. Therefore `mcp`
installs into an isolated environment (a venv under the install dir, or a documented
`pip --user`), used only by the MCP entrypoint — the CLI never imports it.

**D3 — Thin adapter, no new logic.** Each tool mirrors `cli.py::_ctx`: resolve store via
`sandesh_db.store_dir(project_id)`, connect via `sandesh_db.connect(store)`, call the one
mapped `sandesh_db.*` function, return its value. Body-touching tools (`send`, `reply`,
`fetch`) pass `store`; the rest pass only `con`.

**D4 — Explicit `project_id` on every tool.** No CWD/git inference (a daemon has none) —
consistent with the library's existing contract. `project_id` is a required parameter of
every tool.

**D5 — Error mapping.** Library-raised validation/authorization errors (bad address
format, unauthorized unregister, duplicate registration) are caught and re-raised as
`ToolError` with the library's message, so the client gets a clean structured error
rather than a stack trace.

**D6 — Tool set = the existing verbs, no more.** Exactly the 10 verbs below. No verb is
invented for MCP; none is dropped.

## 5. Tool surface (the WHAT)

Each tool takes `project_id` (required) plus the verb's own parameters, and delegates:

| MCP tool | delegates to | passes |
|---|---|---|
| `sandesh_setup` | `setup(project_id)` | — (provisions store) |
| `sandesh_register` | `register(con, addr, kind, display_name, by, project)` | `con` |
| `sandesh_unregister` | `unregister(con, recipient, requester, project)` | `con` |
| `sandesh_addressbook` | `addressbook(con)` | `con` |
| `sandesh_send` | `send(con, store, from_addr, to, cc, subject, kind, …)` | `con`, `store` |
| `sandesh_reply` | `reply(con, store, parent_id, from_addr, subject, body_text, …)` | `con`, `store` |
| `sandesh_inbox` | `inbox(con, recipient, unread_only)` | `con` |
| `sandesh_fetch` | `fetch(con, store, recipient, mark)` | `con`, `store` |
| `sandesh_thread` | `thread(con, msg_id)` | `con` |
| `sandesh_actioned` | `set_status(con, msg_id, "actioned")` | `con` |

## 6. The wake constraint (non-negotiable)

An MCP server **cannot re-invoke a sleeping agent turn** — only the host's background-task
mechanism can (Claude Code `run_in_background`, or Cron). Therefore the wake stays the
standalone `notify` watcher, launched by the agent as a background process; MCP replaces
the **verbs**, never the wake. `mcp_server.py` exposes **no** `notify`/watch tool and does
not import `notify.py`. This is why the liveness table is crash-safe rather than relying on
a shutdown hook (see `CLAUDE.md` "The wake mechanism").

## 7. Coexistence & migration

- CLI and MCP server are peers over the same library and the same on-disk stores; a
  message sent via CLI is fetchable via MCP and vice-versa (same SQLite store).
- No data migration — same schema, same store layout.
- Adoption is incremental: an agent can use the CLI and the MCP server simultaneously
  during transition.

## 8. Success criteria

1. An MCP client can call all 10 tools over stdio and get results equal to the
   corresponding `sandesh_db.*` / CLI outcome.
2. The CLI still runs with no third-party package installed.
3. `notify` is untouched and remains the wake path.
4. Adapter tests prove per-tool parity with the library; the existing 24 unit tests stay
   green.

## 9. Out of scope / future

- HTTP/SSE transport, multi-client shared server, auth.
- Any wake-over-MCP scheme.
- New message verbs or schema changes.
- Packaging to PyPI.

## 10. CR breakdown

| CR | Scope | Depends on |
|---|---|---|
| CR-SAN-001 | Foundation: dependency isolation + `install.sh` wiring, `FastMCP` app, `_ctx(project_id)` helper, `ToolError` mapping (D2/D3/D5), stdio entrypoint, in-process test harness, proven with `sandesh_setup` | — |
| CR-SAN-002 | Read/query tools: `sandesh_addressbook`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` + parity tests | CR-SAN-001 |
| CR-SAN-003 | Mutating tools: `sandesh_register`, `sandesh_unregister`, `sandesh_send`, `sandesh_reply`, `sandesh_actioned` + auth/validation error-mapping tests | CR-SAN-001 |
