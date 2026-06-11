# CR-SAN-025 — MCP surface update (archive/unarchive tools, cross-project docs)

**Status:** PENDING
**Priority:** Medium (agent-facing polish over the wave's core)
**Depends on:** CR-SAN-024 (lifecycle ops)
**Labels:** wave-6, global-store, mcp
**Wave:** Wave 6 (global store)
**Design reference:** docs/research/PRD-global-store.md (D9, D11 boundary; §6 verification)

## Context

The core (CR-SAN-022..024) gives Sandesh a global store, gated cross-project messaging, and the lifecycle.
This CR brings the MCP surface in line: the **reversible** lifecycle pair becomes agent-callable
(`sandesh_archive`/`sandesh_unarchive` — 9 → 11 tools), the destructive/admin ops (**tombstone, grant,
revoke**) are deliberately **absent**, and the tool docs teach agents the new cross-project semantics.

## Scope

### §S1 — the two new tools (D9)
- `sandesh_archive(project_id, by, force=False)` and `sandesh_unarchive(project_id, by)` in
  `mcp_server.py`, thin adapters over the CR-SAN-024 core ops. **`project_id` is REQUIRED** (no
  `$SANDESH_PROJECT` fallback on lifecycle ops — they must not use `_resolve_project`). Annotations:
  `sandesh_archive` is reversible — `destructiveHint=False` + `idempotentHint=False`. Error mapping:
  the house `(ValueError, PermissionError)` → `ToolError` pattern **plus `RuntimeError`** (the
  wedged-watcher eviction refusal from `archive` raises it — must surface as a `ToolError`, not a raw
  traceback). `sandesh_archive`'s docstring notes the call may block up to ~2× the poll interval while
  watchers are cooperatively evicted, and that `force=True` reaps a non-cooperating watcher.
- **No `sandesh_tombstone`, no grant/revoke tools — ever** (D9/D11: backend-admin, CLI-only).

### §S2 — `project_id` derivation on existing verbs
- `project_id` is already optional on every tool (explicit arg → `$SANDESH_PROJECT`); the change is
  the **fallback chain when BOTH are absent** — instead of erroring, derive the context per tool:
  `send`/`reply` from `from_addr`'s `<Project>` part; `fetch` from `recipient`'s; `inbox`/`thread`
  need no project at all (recipient-/id-keyed queries on the global DB — absent project is simply
  unused). An explicit/env project still wins, and the existing sender-must-match-context validation
  is unchanged. `setup`/`addressbook`/`register` keep today's explicit-or-env requirement.

### §S3 — docs the agent reads
- Tool docstrings + the server `instructions` + the `sandesh://usage` resource updated for: cross-project
  sending (and its admin grant — "if you get `not approved for project`, a human must run `sandesh grant`"),
  archived/tombstoned error meanings, the archive/unarchive tools, hidden-tombstoned-traffic semantics.

### §S4 — E2E
- The stdio E2E extended with one cross-project + archive scenario: setup P1+P2 (grant via the CLI in the
  test harness), cross-project send → fetch, `sandesh_archive` → further send fails with the archived
  error, `sandesh_unarchive` → send succeeds again.

## Acceptance criteria

- [ ] **AC1 — tool count + names.** `tools/list` returns exactly 11 tools: the existing 9 plus
      `sandesh_archive` and `sandesh_unarchive`; no tool name contains `tombstone`, `grant`, or `revoke`.
- [ ] **AC2 — archive/unarchive round-trip via MCP.** `sandesh_archive(project_id='P2', by='Mainline - P2')`
      flips the tracker to `archived` (verified in the DB); a subsequent `sandesh_send` to a P2 address
      raises a `ToolError` whose message contains `archived`; `sandesh_unarchive` restores sends.
- [ ] **AC3 — required project_id.** Calling `sandesh_archive` without `project_id` is a schema/validation
      error even with `$SANDESH_PROJECT` set (the env fallback must NOT apply to lifecycle tools).
- [ ] **AC4 — authz mapped.** `sandesh_archive(by='Track 1 - P2')` (not Mainline) surfaces the rejection
      as a `ToolError` (not a crash) with the authz message.
- [ ] **AC5 — derivation.** With neither `project_id` nor $SANDESH_PROJECT set: `sandesh_send` works (context derived from `from_addr`), `sandesh_fetch` works (from `recipient`), `sandesh_inbox`/`sandesh_thread` work (no project needed);
      `sandesh_setup` still requires/uses it as today.
- [ ] **AC6 — docs updated.** The server `instructions` and the `sandesh://usage` resource mention
      cross-project sending, the admin grant error, and the archive lifecycle (content/grep markers).
- [ ] **AC7 — stdio E2E.** The §S4 scenario passes against the real `sandesh-mcp` subprocess.

## Estimated size
Small-medium — two thin tools, docstring/docs sweep, one E2E scenario; the core logic all ships in 022–024.

## Risks / open questions
- Tool-schema compatibility: making `project_id` optional changes published schemas — pre-1.0 acceptable,
  but the bundled usage doc + README examples must be updated in the same CR.
- `by` on the MCP tools relies on honor-system self-identification (as `register`/`unregister` do today) —
  document it, don't oversell it as security.

## Non-goals
- Tombstone/grant/revoke via MCP (never — D9/D11).
- Pi extension updates (a follow-on CR if/when the Pi tools need the new verbs).
- Any change to the wake path (`notify` remains a background process).
