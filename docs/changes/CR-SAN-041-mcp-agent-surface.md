# CR-SAN-041 — MCP agent surface: enable-listening directive + 5 lifecycle `/` prompts

**Status:** IN_PROGRESS
**Priority:** High (the agent can't discover the wake; users have no `/` entry points)
**Depends on:** —
**Labels:** mcp, dx, usability, patch-0.3.1
**Wave:** patch 0.3.1
**Design reference:** `sandesh/mcp_server.py` (current `instructions` + the 12 tools); the FastMCP
**prompt** API (verify the exact `@mcp.prompt` signature/return type in gap-analysis — "read the
real upstream API first"); `notify.py` EXIT CODES (the wake the instructions point at).

## Context
An agent operates Sandesh through the MCP. `notify` (the wake) is **not** a tool, so the agent
can't discover it — and the rich loop lives in the opt-in `sandesh://usage` resource, which an agent
introspecting "what can this MCP do?" never reads. So the single most important operating
instruction — *enable listening or you'll never be woken* — is effectively invisible. Separately,
the human operator has no `/`-menu entry points; the `/` menu surfaces MCP **prompts**, of which we
have none.

## Scope
- **§S1 — imperative enable-listening directive in `instructions`.** Strengthen `SANDESH_INSTRUCTIONS`
  (delivered on every connect) with a prominent, imperative block: *to receive messages you MUST
  enable listening* — right after registering, launch `sandesh notify --to "<your address>"` via the
  host's background-task tool; when it exits, mail arrived → call `sandesh_fetch`, act, relaunch;
  sending needs no listener; the full loop + exit codes are in the `sandesh://usage` resource. Not
  buried prose — a can't-miss directive.
- **§S2 — tool-description pointers.** Echo a one-line "after registering, enable listening with a
  background `sandesh notify`" note in the **`register`** tool description (and a brief pointer in
  `setup`) so it surfaces exactly when the agent provisions.
- **§S3 — 5 lifecycle `/` prompts.** Add `@mcp.prompt` functions for **`setup`, `register`,
  `unregister`, `archive`, `unarchive`** (→ `/mcp__sandesh__<name>` in Claude Code) — the
  human-initiated session-lifecycle verbs. Each takes typed args matching the underlying tool's key
  params and returns a templated turn instructing the model to call that **same tool** (the tools are
  unchanged + still agent-callable; prompts are additive). The **`register`** prompt also tells the
  agent to enable listening after registering (§S1). **No prompt** for the 7 agent-only tools
  (`addressbook`/`send`/`reply`/`inbox`/`fetch`/`thread`/`search`); none for tombstone/grant/revoke
  (not exposed at all).

## Acceptance criteria
- [ ] **AC1 — enable-listening directive.** `SANDESH_INSTRUCTIONS` contains an imperative directive
      that names `sandesh notify`, the background-task launch, and the on-exit `sandesh_fetch` +
      relaunch loop (grep markers).
- [ ] **AC2 — tool pointers.** The `register` tool description (and `setup`) mention enabling
      listening via a background `sandesh notify`.
- [ ] **AC3 — exactly 5 prompts.** The server registers exactly 5 prompts named `setup`, `register`,
      `unregister`, `archive`, `unarchive` (assert via the FastMCP prompt registry / `prompts/list`);
      each prompt's rendered content references its matching tool and accepts the expected args.
- [ ] **AC4 — register prompt nudges listening.** The `register` prompt's rendered text instructs
      the agent to enable listening (background `sandesh notify`) after registering.
- [ ] **AC5 — boundary.** No prompt exists for `send`/`reply`/`fetch`/`inbox`/`thread`/`search`/
      `addressbook` or for tombstone/grant/revoke; the **12 tools** are unchanged (still registered).
- [ ] **AC6 — real MCP.** Over the stdio server (or in-process FastMCP), `prompts/list` returns the
      5 prompts and `prompts/get` renders one with args; tools/list still returns 12.

## Estimated size
Small-medium — `instructions` string + 2 tool-description lines + 5 small prompt functions; FastMCP
introspection tests.

## Risks / open questions
- Confirm the FastMCP `@mcp.prompt` API (decorator form, arg typing, return type) before RED — done
  in gap-analysis.

## Non-goals
- Prompts for the agent-only verbs or the admin-only verbs; changing any tool; the human USER_GUIDE
  (CR-SAN-040 documents these); Pi-side prompt templates (Pi has its own mechanism).
