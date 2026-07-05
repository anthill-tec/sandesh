# CR-SAN-044 — Rename the MCP `body_text` field to `body` (send & reply)

**Status:** READY (gap-analysis clean after 2 spec self-fixes: §S3 full 4-file consumer enumeration; §S4 drop the erroneous PRD edit) → implementing on release/0.3.4
**Priority:** Low (developer-experience / naming consistency — no behavioural change)
**Depends on:** — (renames two MCP tool params; underlying `sandesh_db.send`/`reply` unchanged)
**Labels:** dx, mcp, naming, breaking-param
**Wave:** post-0.3.3 → patch release **0.3.4**
**Design reference:** owner request — the MCP param name `body_text` is misleading because every other
surface calls it **body**: the Pi extension already exposes `body` (`integrations/pi/src/index.ts:537`,
`:349`, `:357`), the CLI uses the `--body` flag (`sandesh/cli.py` `_read_body`), the Pi README's tool
table says "optional body", and the MCP `body_text` param's own description reads "Optional message
body". The Python MCP server is the lone outlier. This CR brings it into parity. Straight rename
(no `body_text` alias) — locked with owner: MCP clients read the tool schema at connect-time, so an
upgraded server simply exposes `body`; real-world breakage is near-nil.

## Context
`sandesh/mcp_server.py` exposes two mutating tools whose optional-body parameter is named `body_text`:
- `sandesh_send` — `body_text: Annotated[str | None, Field(description="Optional message body. …")]`
  (`mcp_server.py:527`), passed through as `sandesh_db.send(…, body_text=body_text, …)` (`:555`).
- `sandesh_reply` — `body_text: Annotated[str | None, Field(description="Optional reply body; …")]`
  (`mcp_server.py:585`), passed positionally as `sandesh_db.reply(con, store, parent_id, from_addr,
  subject, body_text, project=project)` (`:604`).

The underlying library functions `sandesh_db.send(…, body_text=None, …)` (`sandesh_db.py:471`) and
`sandesh_db.reply(…, body_text=None, …)` (`:524`) keep their **internal** `body_text` kwarg — it is an
implementation detail not exposed to agents, and renaming it would ripple through `cli.py` and dozens of
library-level tests for no user-visible gain. The CLI `--body`/`--body-file` flags are already correct.
So the change is confined to the two MCP tool signatures + their pass-through call sites.

The `sandesh_reply` param set is a locked invariant from CR-SAN-005 (exactly five params: `parent_id,
from_addr, project_id, subject, body_text` — no `resolves`, no `reply_all`), asserted by
`tests/test_mcp_surface.py::test_ac1_sandesh_reply_signature_has_exact_five_params`. That lock set is
updated in place: `body_text` → `body` (still five params; the `no resolves / no reply_all` guarantees
are untouched).

No schema change (no DB touched); PyPI/Python version is tag-derived (hatch-vcs). Ships as patch **0.3.4**.

## Scope
- **§S1 — `sandesh_send` param rename.** In `mcp_server.py`, rename the `sandesh_send` tool parameter
  `body_text` → `body` (`:527`), keeping its `Field(description=…)` text verbatim. Update the pass-through
  to `sandesh_db.send(con, store, from_addr, to, cc, subject, kind, body_text=body, project=project)`
  (`:555`) — the library kwarg stays `body_text`, its value now comes from the renamed `body` param.
- **§S2 — `sandesh_reply` param rename.** In `mcp_server.py`, rename the `sandesh_reply` tool parameter
  `body_text` → `body` (`:585`), keeping its `Field(description=…)` text verbatim. Update the positional
  pass-through to `sandesh_db.reply(con, store, parent_id, from_addr, subject, body, project=project)`
  (`:604`).
- **§S3 — test updates (the RED flip).** Flip **only the MCP call-arg form** `"body_text"` (the dict key
  passed to `mcp.call_tool(...)`) → `"body"`. The **direct library-kwarg form** `sdb.send(…, body_text=…)` /
  `sdb.reply(…, body_text=…)` in these same files **STAYS** (it calls the library, whose param is unchanged) —
  do NOT blanket-replace. Full enumeration (gap-analysis dimension 6 — 10 call-arg sites across **four** files):
  - `tests/test_mcp_surface.py:83` — the `sandesh_reply` five-param lock set `expected_params`
    (`body_text` → `body`) and its docstring.
  - `tests/test_mcp_mutating_tools.py` — the four `call_tool(...)` invocations (`:275`, `:339`, `:430`,
    `:490`) → `"body"`; align the affected test method names/docstrings.
  - `tests/test_mcp_project_derivation.py` — the two `call_tool(...)` invocations (`:189`, `:300`) → `"body"`.
    (Leave `:387` `body_text=` — that is a direct `sdb.send(...)` library call.)
  - `tests/test_mcp_e2e.py` — the three `call_tool(...)` invocations (`:174`, `:372`, `:566`) → `"body"`.
    (Leave `:588`, `:842`, `:852` `body_text=` — direct `sdb.send(...)` library calls.)
- **§S4 — docs.** Add a one-line note to `docs/changes/README.md`'s CR queue. **No PRD change** — gap analysis
  confirmed `PRD-mcp-server.md:136-137` document the underlying `send()`/`reply()` **library** signature
  (which keeps `body_text`, correctly), not the MCP field; editing them would make the PRD contradict the
  code. `sandesh/data/usage-scenarios.md` already documents the MCP params as `body?` (`:296-297`) — no
  change. Historical CR-SAN-003/005/006 keep their point-in-time `body_text` wording (records, not touched).
  No README/CLAUDE.md gotcha needed (no behavioural/architectural change).

## Acceptance criteria
- **AC1 — `sandesh_send` exposes `body`, not `body_text`.** The `sandesh_send` tool's parameter set is
  exactly `{from_addr, project_id, to, cc, subject, kind, body}`; `"body_text"` is **absent**; the
  `body` param's `inputSchema` description is present and non-empty.
- **AC2 — `sandesh_reply` five-param lock updated to `body`.** The `sandesh_reply` tool's parameter set
  is exactly `{parent_id, from_addr, project_id, subject, body}` — `"body_text"` absent, `"resolves"`
  absent, `"reply_all"` absent (CR-SAN-005 invariant preserved with `body_text`→`body`).
- **AC3 — send with `body` stores + is retrievable; old name is rejected.** `mcp.call_tool("sandesh_send",
  {…, "body": "This is the report body.\n"})` returns an int id and `fetch` reads that body back
  verbatim. Calling `mcp.call_tool("sandesh_send", {…, "body_text": "x"})` **raises** (the removed param
  is not accepted — confirms the straight, non-aliased rename).
- **AC4 — reply with `body` stores a body file.** `mcp.call_tool("sandesh_reply", {…, "body":
  "ACK — done.\n"})` returns an int id and writes a body file whose content round-trips via `fetch`.
- **AC5 — subject-only unaffected.** `mcp.call_tool("sandesh_send", {…})` with **no** `body` still
  produces a subject-only message (`body_path` NULL, no body file) — omission semantics unchanged.
- **AC6 — no regression + scope.** The full `tests/` suite is green. The only production file changed is
  `sandesh/mcp_server.py`; `sandesh/sandesh_db.py`, `sandesh/cli.py`, and `integrations/pi/**` are
  untouched. No schema/migration change; `schema/current-schema.json` untouched and the CI schema-snapshot
  gate still passes.

## Estimated size
Trivial — two parameter renames + two pass-through call sites in `mcp_server.py`; test-field flips in two
test files; one PRD reference line. No new tests beyond the flipped assertions (the rename is exercised by
the existing behavioural tests, now keyed on `body`).

## Risks / open questions
- **Breaking MCP param.** Accepted (owner-locked): straight rename, no `body_text` alias. Justification —
  MCP tool schemas are discovered at connect-time, so a client of the 0.3.4 server sees `body`; there is no
  serialized/cached client that would keep sending `body_text`. AC3's "old name rejected" assertion pins
  the decision.
- **PRD row ambiguity.** `PRD-mcp-server.md` documents BOTH the MCP field and the underlying library
  signature. Only the MCP-field references change; the library-signature rows (which still say `body_text`,
  correctly) are left as-is — checked during §S4.

## Non-goals
- Renaming the underlying `sandesh_db.send`/`reply` `body_text` kwarg, or the CLI internals — the request
  is the MCP field only; the library kwarg is an internal detail with no agent-facing surface.
- Any `body_text`→`body` backward-compat alias / deprecation shim (explicitly declined by owner).
- Any change to the Pi extension (already exposes `body`) or to message body storage semantics.
