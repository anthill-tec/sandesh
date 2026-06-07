# CR-SAN-006 — Docstring & usability enrichment + server `instructions` + `sandesh://usage` resource

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-005 (9-tool surface settled)
**Labels:** phase-2, mcp, docs, usability
**Phase:** Phase 2
**Design reference:** docs/research/PRD-mcp-server.md §10 (CR-SAN-006 row), §5 (tool surface), §6 (the two-channel wake), and `docs/usage-scenarios.md` (the authoring source)

## Context

The 9 MCP tools work but their `list_tools()` surface is *thin*: one-line docstrings, no
per-parameter descriptions, no read/destructive annotations, and the server returns **no
`instructions`**. An agent calling the server therefore cannot learn — from the protocol alone —
the things that actually govern correct use: the **address format**, **To-wakes / Cc-silent**,
`parent_id` = *the original message's id*, the **read = being-acted-on / reply = done** lifecycle,
and — critically — that the **wake is NOT a tool** (it's `run_in_background` + `sandesh notify`,
PRD §6). This CR enriches the surface from the single source of truth, `docs/usage-scenarios.md`,
so the server is self-describing.

**Pure docs/metadata change.** No business logic, no `sandesh_db` change, no new tool, no
parameter added or removed (the 9-tool contract and `sandesh_reply`'s signature stay exactly as
locked by CR-SAN-005). Only docstrings, `Annotated[..., Field(description=…)]` param metadata,
`@mcp.tool(annotations=ToolAnnotations(...))` hints, the `FastMCP(instructions=…)` argument, and
one read-only `@mcp.resource`.

## Scope

### §S1 — Per-tool docstrings (what / who / when / gotchas)
Rewrite each of the 9 tools' docstrings from its `docs/usage-scenarios.md` §5 row + the relevant
§4 scenario, conveying: **what it does**, **who calls it and when**, and **the semantic gotcha(s)**
that change behaviour. Specifically:
- `sandesh_send` — `subject` mandatory; omit body ⇒ subject-only; `to`/`cc` are **lists**;
  `to:["all-tracks"]` broadcasts (minus sender); **To wakes, Cc is silent**.
- `sandesh_reply` — threads via `in_reply_to`; `parent_id` is **the original message's id**;
  a recipient uses it to signal **completion** (often subject-only). (No `resolves`/`reply_all` —
  CR-SAN-005.)
- `sandesh_fetch` vs `sandesh_inbox` — fetch is the real read (consolidates unread to+cc, marks
  read; called right after `notify` wakes); inbox is a non-consuming triage glance.
- `sandesh_register`/`sandesh_unregister` — address format; unregister authorization
  (Mainline or self) + the tombstone-then-retry of a live watcher.
- `sandesh_setup`/`sandesh_addressbook`/`sandesh_thread` — purpose + caller per §5.

### §S2 — Per-parameter descriptions
Annotate the meaningful parameters with `Annotated[<type>, Field(description="…")]` so the
description flows into each tool's `inputSchema.properties[param].description`. At minimum:
- `from_addr`, `to`, `cc`, the unregister `recipient`/`requester`, register `addr` — **address
  format** `'<Orchestrator> - <Project>'` and the To-wakes/Cc-silent / `all-tracks` notes where
  the arg warrants it.
- `parent_id` — "the **original message's id** being replied to".
- `project_id` — "store router; falls back to `$SANDESH_PROJECT`".
- `mark` (fetch), `unread_only` (inbox), `msg_id` (thread) — their effect.

### §S3 — Read / destructive tool annotations
Add `@mcp.tool(annotations=ToolAnnotations(...))` hints reflecting each tool's nature:
- **read-only** (`readOnlyHint=True`): `sandesh_addressbook`, `sandesh_inbox`, `sandesh_thread`.
  (`sandesh_fetch` is **not** read-only by default — `mark=True` mutates read state.)
- **destructive** (`destructiveHint=True`): `sandesh_unregister` (removes/deactivates an address).
- The mutating-but-additive tools (`sandesh_setup`, `sandesh_register`, `sandesh_send`,
  `sandesh_reply`, `sandesh_fetch`) carry no `readOnlyHint`; `sandesh_setup` is idempotent
  (`idempotentHint=True`).

### §S4 — Server `instructions`
Pass `instructions=` to `FastMCP("sandesh", instructions=…)` conveying, concisely:
- **Model-B context** (Mainline + Track sessions; addresses represent orchestrators).
- The **two channels**: MCP carries the **verbs**; the **wake** is out-of-band —
  `run_in_background` launching the standalone **`sandesh notify`** process, **NOT an MCP tool**
  (PRD §6). So an agent learns the CLI/wake step it can't get from `list_tools()`.
- The **lifecycle**: read = being-acted-on, **reply = done** (no status field).

### §S5 — `sandesh://usage` resource (read-only)
Add `@mcp.resource("sandesh://usage")` returning the contents of `docs/usage-scenarios.md` on
demand (text/markdown), so a client can pull the full scenarios doc when it wants depth beyond the
docstrings. Resolve the doc path relative to the repo/package (robust to the installed layout);
if the file is absent, return a short explanatory stub rather than raising.

## Gap-analysis findings (2026-06-07) — verdict READY

- **Dim 1 (Spec vs PRD):** spec covers every item in PRD §10's CR-SAN-006 row (docstrings, param
  descriptions, required params, read/destructive annotations, server `instructions`, optional
  `sandesh://usage` resource). No gap.
- **Dim 2 (Spec vs Code):** matches `develop` — 9 tools; `sandesh_fetch(...mark=True)` mutates
  read-state (correctly **not** read-only); `addressbook`/`inbox`/`thread` are pure reads; `send`
  has `to`/`cc`; `reply` has `parent_id`. No drift.
- **Dim 3 (Code vs PRD):** the authoring source `docs/usage-scenarios.md` already reflects D7
  (read=acting, reply=done) and the wake-not-a-tool boundary (§6). No drift.
- **API verified** against the installed `mcp`: `Annotated[T, Field(description=…)]` →
  inputSchema descriptions; `ToolAnnotations(readOnlyHint/destructiveHint/idempotentHint)` via
  `@mcp.tool(annotations=…)`; `FastMCP(instructions=…)` → `m.instructions`; `@mcp.resource` +
  `list_resources()`/`read_resource()`. No existing test hard-codes instructions/resources, so the
  additive enrichment won't break the suite.
- **DRIFT-1 (non-blocking):** `@mcp.resource` defaults `mimeType` to `text/plain`; §S5 wants
  markdown — GREEN passes `mime_type="text/markdown"` (AC6 doesn't assert mime).

## Acceptance criteria

- [ ] **AC1** — `await mcp.list_tools()` still returns **exactly 9** tools (unchanged contract);
      `sandesh_reply`'s parameters are still `parent_id, from_addr, project_id, subject, body_text`
      (CR-SAN-005 invariant preserved).
- [ ] **AC2** — Every tool's `inputSchema.properties` carries a non-empty `description` for the
      key params named in §S2 — asserted for at least: `sandesh_send.to`, `sandesh_send.cc`,
      `sandesh_reply.parent_id`, `sandesh_register.addr`, and `project_id` on a representative tool.
- [ ] **AC3** — `sandesh_send`'s and `sandesh_reply`'s docstrings mention the **To-wakes/Cc-silent**
      and **`parent_id` = original message id** semantics respectively (asserted by substring on the
      tool's `description` returned from `list_tools()`).
- [ ] **AC4** — `ToolAnnotations` are set as in §S3: `sandesh_addressbook`/`sandesh_inbox`/
      `sandesh_thread` have `readOnlyHint == True`; `sandesh_unregister` has
      `destructiveHint == True`; `sandesh_fetch` does **not** have `readOnlyHint == True`
      (asserted via each tool's `.annotations` from `list_tools()`).
- [ ] **AC5** — the server exposes non-empty `instructions` that mention the **wake is not a tool**
      / `notify` and the **reply = done** lifecycle (asserted via the FastMCP `instructions`).
- [ ] **AC6** — `await mcp.list_resources()` includes a resource with URI `sandesh://usage`, and
      reading it returns the `docs/usage-scenarios.md` content (or, if the doc is missing, a
      non-empty stub) — asserted by `read_resource("sandesh://usage")`.
- [ ] **AC7** — full regression green: the MCP suites + `python3 tests/test_sandesh.py` (the 24
      stdlib baseline) all pass; `mcp` stays imported only in `app/mcp_server.py`.

## Estimated size
Small–medium: metadata-only edits to `app/mcp_server.py` (docstrings + `Annotated`/`Field` +
`ToolAnnotations` + `instructions=` + one `@mcp.resource`) and one new test module asserting the
enriched surface. No `sandesh_db` change.

## Risks / open questions
- **Resource path resolution** — `docs/usage-scenarios.md` lives in the repo; the installed
  layout (`~/.local/share/sandesh/app/`) may not ship `docs/`. Resolve robustly and fall back to a
  stub (AC6) rather than failing the resource read; packaging (CR-SAN-008) can later bundle the doc.
- **Annotation surface drift** — `ToolAnnotations` field names are SDK-versioned; verified against
  the installed `mcp` (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`/`title`).

## Non-goals
- Adding/removing/renaming any tool or parameter (the 9-tool + `sandesh_reply` contract is locked).
- Any `sandesh_db` / CLI / `notify` change.
- Bundling `docs/usage-scenarios.md` into the installed package (that's packaging — CR-SAN-008).
