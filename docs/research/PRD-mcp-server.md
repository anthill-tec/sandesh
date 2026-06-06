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
| In-process test entry | `await mcp.call_tool(name, args)` and `await mcp.list_tools()` — returns *converted* output (`Sequence[ContentBlock] | dict`), not the raw value |
| In-memory client↔server | `create_connected_server_and_client_session(...)` (`mcp.shared.memory`) — a real `ClientSession` over an in-memory transport (protocol round-trip, no subprocess) |
| Real-subprocess client | `StdioServerParameters(command=…, args=…)` + `stdio_client(...)` + `ClientSession` (top-level `mcp` exports); `session.initialize()` → `list_tools()` / `call_tool()` |
| Manual inspector | `mcp dev app/mcp_server.py` launches the MCP Inspector (browser UI) for hands-on smoke testing |

## 4. Design decisions

**D1 — Transport: stdio.** Each agent session launches its own per-session server locally
(mirrors the Model-B "one process per session" shape; no port management, no auth). HTTP
transports are out of scope; revisit only if a shared multi-client server is ever needed.

**D2 — Dependency isolation via a dedicated venv + wrapper (DECIDED).** `mcp` is the first
non-stdlib dependency. It is imported **only** by `mcp_server.py`. The CLI path
(`bin/sandesh` → `cli.py` → `sandesh_db.py` / `notify.py`) must remain importable with
**zero** third-party packages. Mechanism:
- `install.sh` creates a **dedicated venv** at `<install>/.venv` and installs the pinned
  `mcp` into it (the only place `mcp` ever lives).
- `install.sh` writes a wrapper launcher `bin/sandesh-mcp` that
  `exec "$DEST/.venv/bin/python" "$DEST/app/mcp_server.py" "$@"` — i.e. the MCP server runs
  on the **venv interpreter**, while `bin/sandesh` (the CLI) keeps running on system
  `python3`. Two launchers, two interpreters, by design.
- The user points their MCP client at `sandesh-mcp` (see §7a). The CLI never sees `mcp`.
(`pip --user` was the alternative; rejected — it pollutes the global environment and
couples the CLI's interpreter to a third-party package.)

**D3 — Thin adapter, no new logic.** Each tool mirrors `cli.py::_ctx`: resolve store via
`sandesh_db.store_dir(project_id)`, connect via `sandesh_db.connect(store)`, call the one
mapped `sandesh_db.*` function, return its value. Body-touching tools (`send`, `reply`,
`fetch`) pass `store`; the rest pass only `con`.

**D4 — `project_id` on every tool, with a `$SANDESH_PROJECT` fallback.** No CWD/git
inference (a daemon has none) — consistent with the library's existing contract. Every tool
takes a `project_id` parameter; if it is omitted, the server falls back to the
`$SANDESH_PROJECT` environment variable (the same precedence the CLI already uses), and
errors if neither is present. This lets a client register a per-project server once
(`claude mcp add … --env SANDESH_PROJECT=<id>`, see §7a) so the agent can call tools
without repeating `project_id`, while still allowing an explicit override per call.

**D5 — Error mapping.** Library-raised validation/authorization errors (bad address
format, unauthorized unregister, duplicate registration) are caught and re-raised as
`ToolError` with the library's message, so the client gets a clean structured error
rather than a stack trace.

**D6 — Tool set = the existing message/addressbook verbs.** The server exposes the library's
verbs, no invented ones. CR-SAN-001..004 shipped **10** tools; **CR-SAN-005 retires
`sandesh_actioned`** (see D7) → a **9-tool** surface. (A future sender-side read-status query
may be added — §9.)

**D7 — Message lifecycle: read = being-acted-on, reply = done; NO `status`/disposition field
(corrected 2026-06-07; source: `docs/usage-scenarios.md`).** The earlier `status`
(`open`/`actioned`/`closed`) disposition machine was over-engineered. The real lifecycle:

> a message lands **unread** → the recipient `fetch`es it (**read** = received and now being
> acted on; `fetch` only ever returns unread, so it's consumed once) → when the work is done
> the recipient **replies** (often subject-only — the subject states what was done) and the
> sender's `notify` watcher wakes on that reply.

So the per-recipient **read flag + the reply** carry the whole lifecycle. Consequences for the
tool surface:
- **`sandesh_reply` exposes no `resolves`/`reply_all`** — a reply is just a reply (the
  completion signal); it flips no status.
- **`sandesh_actioned` (and the `message.status` field) is vestigial** — shipped in CR-SAN-003,
  **retired in CR-SAN-005**.
- The sender can *observe* a sent message's read state ("being acted on"); exposing that as a
  tool is an open item (§9), not yet designed.
- **Atomic send (notifier-race safety):** `send` writes the body file *before* it commits the
  DB rows, and the notifier (a separate connection) only sees committed rows — so a woken
  recipient never `fetch`es a message whose body file isn't on disk yet; an error aborts with
  no partial/hanging state.

## 5. Tool surface (the WHAT)

Each tool takes `project_id` (optional — falls back to `$SANDESH_PROJECT`, see D4) plus
the verb's own parameters, and delegates:

| MCP tool | delegates to | passes |
|---|---|---|
| `sandesh_setup` | `setup(project_id)` | — (provisions store) |
| `sandesh_register` | `register(con, addr, kind, display_name, by, project)` | `con` |
| `sandesh_unregister` | `unregister(con, recipient, requester, project)` | `con` |
| `sandesh_addressbook` | `addressbook(con)` | `con` |
| `sandesh_send` | `send(con, store, from_addr, to, cc, subject, kind, body_text, project)` (`to`/`cc` = `list[str]`) | `con`, `store` |
| `sandesh_reply` | `reply(con, store, parent_id, from_addr, subject, body_text, project)` — **no `resolves`/`reply_all`** (D7) | `con`, `store` |
| `sandesh_inbox` | `inbox(con, recipient, unread_only)` | `con` |
| `sandesh_fetch` | `fetch(con, store, recipient, mark)` | `con`, `store` |
| `sandesh_thread` | `thread(con, msg_id)` | `con` |
| ~~`sandesh_actioned`~~ | shipped CR-SAN-003; **RETIRED in CR-SAN-005** — no `status`/disposition field (D7) | — |

`sandesh_inbox`/`sandesh_thread` normalize `sqlite3.Row` → `dict` (the rest already return
dicts). Completion is signalled by `sandesh_reply`, not a status flip — see D7 and
`docs/usage-scenarios.md` for the who/when/why behind each tool.

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

## 7a. Deployment & client registration (DECIDED)

A stdio MCP server is **a command the client spawns** — "deploying" means giving the client
a runnable command (`command` + `args` + `env`). For Sandesh that command is the
`bin/sandesh-mcp` wrapper from D2.

**Install →** `./install.sh` (creates the venv + `sandesh-mcp` wrapper, symlinks it onto
PATH alongside `sandesh`). **Register with Claude Code:**
```bash
# user scope (all your projects); bake a default project via env so tools can omit project_id
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
```
which writes (Claude Code uses `~/.claude.json` for user/local scope, `.mcp.json` in the
repo for project scope):
```json
{ "mcpServers": {
    "sandesh": { "type": "stdio", "command": "sandesh-mcp", "args": [],
                 "env": { "SANDESH_PROJECT": "<id>" } } } }
```

**Scope guidance (default = user scope).** Register **user-scoped** for personal use across
projects. A team that wants Sandesh wired into a specific repo commits a project-scoped
`.mcp.json` (`--scope project`); Claude Code prompts each member to approve it on first use.
`install.sh` does **not** auto-edit any client config — it prints the `claude mcp add`
command for the user to run (least-surprise; no silent edits to `~/.claude.json`).

**Manage:** `claude mcp list` / `claude mcp get sandesh` / `claude mcp remove sandesh`;
the in-session `/mcp` panel shows status + exposed tools.

(Claude **Desktop** uses a separate `claude_desktop_config.json` with the same
`command`/`args`/`env` shape — the SDK's own `mcp install` targets Desktop only. Supporting
Desktop is a doc note, not extra code.)

## 8. Success criteria

1. An MCP client can call every exposed tool over stdio and get results equal to the
   corresponding `sandesh_db.*` / CLI outcome (10 tools in CR-SAN-001..004; **9 after
   CR-SAN-005** retires `sandesh_actioned`, per D7).
2. The CLI still runs with no third-party package installed.
3. `notify` is untouched and remains the wake path.
4. Adapter tests prove per-tool parity with the library; the existing 24 unit tests stay
   green.

## 8a. Testing strategy — three tiers (DECIDED)

The MCP analog of "unit tests + Playwright browser E2E": progressively more realistic, each
tier catching what the cheaper one cannot.

| Tier | Mechanism | Exercises | Where |
|---|---|---|---|
| **T1 — Unit / parity** (in-process) | `FastMCP.call_tool(name, args)` / `list_tools()` | tool registration + adapter delegates correctly to `sandesh_db.*` (unwrap converted output) | CR-SAN-001..003 |
| **T2 — Integration** (in-memory client↔server) | `create_connected_server_and_client_session` + real `ClientSession` | the real MCP **protocol** round-trip (serialization, structured output, `ToolError` over the wire) without a subprocess | CR-SAN-004 |
| **T3 — E2E smoke** (real subprocess over stdio) | `StdioServerParameters(command="sandesh-mcp")` + `stdio_client` + `ClientSession.initialize()` → `list_tools()` / `call_tool()` | the **installed wrapper + venv + transport** end-to-end — the Playwright-equivalent of launching the real browser | CR-SAN-004 |

- **T3 is the dedicated smoke test:** it spawns the actual `sandesh-mcp` wrapper (built by
  `install.sh`), proving the deployment artifact works — not just the Python module. It runs
  against a temp `XDG_DATA_HOME`, does a `sandesh_setup` → `sandesh_send` → `sandesh_fetch`
  round trip over real stdio, and asserts the message survives the protocol boundary.
- **Manual smoke:** `mcp dev app/mcp_server.py` opens the MCP Inspector (browser UI) for
  hands-on exploration — the human equivalent of T3.
- T2/T3 require the `mcp` venv (they import the client); they are gated to run only when the
  venv exists, so the stdlib-only CLI test path (`python3 tests/test_sandesh.py`) is never
  coupled to `mcp`.

## 9. Out of scope / future

- HTTP/SSE transport, multi-client shared server, auth.
- Any wake-over-MCP scheme.
- New message verbs or schema changes.
- Packaging to PyPI.
- **Sender-side read-status query** (a tool letting a sender see whether/when a recipient has
  read a message it sent — "is it being acted on?"). The data exists (`read_at` per recipient)
  but no tool exposes it today; the model relies on the **completion reply** as the primary
  signal (D7). A small read-status tool is a candidate future addition, not yet designed.

## 10. CR breakdown

| CR | Scope | Depends on |
|---|---|---|
| CR-SAN-001 | Foundation: venv + `sandesh-mcp` wrapper in `install.sh` (D2), `FastMCP` app, `_ctx` with `project_id`/`$SANDESH_PROJECT` fallback (D3/D4), `ToolError` mapping (D5), stdio entrypoint, T1 in-process harness, proven with `sandesh_setup` | — |
| CR-SAN-002 | Read/query tools: `sandesh_addressbook`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` + T1 parity tests | CR-SAN-001 |
| CR-SAN-003 | Mutating tools: `sandesh_register`, `sandesh_unregister`, `sandesh_send`, `sandesh_reply`, `sandesh_actioned` + auth/validation error-mapping tests | CR-SAN-001 |
| CR-SAN-004 | E2E: T2 in-memory client↔server tests + T3 real-subprocess stdio smoke test over the installed `sandesh-mcp` wrapper; document `mcp dev` + `claude mcp add` registration (§7a) | CR-SAN-001, 002, 003 |
| CR-SAN-005 | **Retire the `status`/disposition model (D7):** remove `sandesh_actioned` (→ 9 tools) and stop using `message.status`; confirm `sandesh_reply` exposes no `resolves`/`reply_all`. Behavior + tests. | CR-SAN-001..004 |
| CR-SAN-006 | **Docstring & usability enrichment** from `docs/usage-scenarios.md`: per-tool descriptions (what/who/when/gotchas), param descriptions (address format, `parent_id` = the original message's id, To-wakes/Cc-silent), required params, optional read/destructive annotations, server-level description (Model-B context + `notify` is not a tool). Docs/quality. | CR-SAN-005 |
