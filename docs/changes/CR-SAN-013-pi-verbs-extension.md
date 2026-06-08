# CR-SAN-013 — Pi extension: scaffold + verb tools (`integrations/pi/`)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-013)
**Priority:** Medium
**Depends on:** CR-SAN-008 (the installed `sandesh` CLI the extension shells to)
**Labels:** wave-4, pi, typescript, integration
**Wave:** Wave 4 (Pi integration)
**Design reference:** docs/research/PRD-pi-extension.md (PE1–PE5), docs/research/DN-pi-wake.md (the wake is CR-SAN-014)

## Context

Ship a **native Pi extension** (TypeScript) that registers the Sandesh **verbs** as first-class Pi
tools, each delegating to the installed **`sandesh` CLI** via `pi.exec(...)` (PRD PE1/PE2). This is
Pi's idiomatic integration (Pi ships "no MCP"); Claude Code / Cursor keep the MCP server
(CR-SAN-001..006/011). **The wake is a separate CR (CR-SAN-014)** — this CR is the verbs only.

**Monorepo model:** the extension is a **TypeScript subfolder under the repo root**
(`integrations/pi/`), versioned with Sandesh-core but built/tested independently. **Sandesh-core
stays pure Python** (PE3) — the extension is a thin TS shim that never imports messaging logic, only
shells to `sandesh`.

## Scope

### §S1 — Scaffold `integrations/pi/` (TS package)
- `integrations/pi/` with `package.json` (name e.g. `@anthill-tec/sandesh-pi`, `type: module`),
  `tsconfig.json`, and tests run by **`bun test`** (native TS test runner — built-in JUnit + lcov,
  zero extra deps; `bun:test` for `test`/`expect`/`mock`/`spyOn`).
- **Dependency declaration (verified against Pi's `docs/packages.md`):** Pi **bundles** its core
  packages for extensions — `@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`, **`typebox`**
  — so list the ones we import in **`peerDependencies` with a `"*"` range and do NOT bundle them**.
  Mirror them in **`devDependencies`** (pinned, e.g. `@earendil-works/pi-coding-agent@^0.78`) so
  `bun test` / `tsc` resolve types + `StringEnum`/`Type` locally during dev. No other runtime deps.
- **Declare the extension entry via the `pi` key** in `package.json`:
  `"pi": { "extensions": ["./src/index.ts"] }` (the package mechanism, per the `with-deps` example) —
  in addition to the `export default function (pi: ExtensionAPI) { … }` entry. (Dev loading also works
  from `.pi/extensions/` / `~/.pi/agent/extensions/`; packaging/listing = CR-SAN-015.)
- `.gitignore` the TS build artifacts (`integrations/pi/node_modules/`, `dist/`).

### §S2 — Register the Sandesh verbs as Pi tools
Use `pi.registerTool({ name, description, parameters, execute })` for each verb, mirroring the
9-tool MCP surface / CLI (PE4). Tools (Typebox `parameters`, `StringEnum` for enums):
`sandesh_setup`, `sandesh_register`, `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`,
`sandesh_reply`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread`.
- Each `execute` runs `pi.exec("sandesh", [..args..], {signal})` and returns the result.
- **Descriptions + param schemas reuse `sandesh/data/usage-scenarios.md`** (address format
  `'<Orchestrator> - <Project>'`, To-wakes/Cc-silent, `parent_id` = original message id, subject-only
  vs body, `all-tracks` broadcast) — do NOT reinvent (PE4).
- `project_id` falls back to `$SANDESH_PROJECT`; the caller's own address from `$SANDESH_ADDRESS`
  (same env contract as the CLI/MCP).

### §S3 — CLI invocation + output handling
- Build CLI args from tool params (e.g. `sandesh --project P send --from A --to B --subject S
  [--body-file …]`). Map `to`/`cc` lists, `kind`, optional body.
- Parse `pi.exec` `{stdout, stderr, code}`: non-zero `code` → surface `stderr` as a tool error;
  zero → return `stdout` (and, where the CLI output is structured enough, a parsed summary). **No
  change to the CLI** (PE3) — parse the existing human-readable output; if a verb's output is hard
  to parse, return it verbatim as the tool result (the agent reads it).

### §S4 — Prerequisite handling
- The extension depends on `sandesh` being on `$PATH` (CR-SAN-008: `uv tool install` / `pipx` /
  `install.sh` / AUR). On load, probe `pi.exec("sandesh", ["--version"])`; if missing, surface a
  clear one-time notice (`ctx.ui.notify`) naming the install options — don't crash.

### §S5 — Tests (bun test)
- Unit-test each tool's `execute` with a **mocked `pi.exec`**: assert the exact CLI argv built from
  params (incl. project/from env fallback, to/cc lists, body handling) and the result/error mapping
  (zero vs non-zero `code`).
- Assert the registered tool **surface**: the 9 tool names, and that each `parameters` schema
  carries the key fields (e.g. `send` has `to`/`cc`/`subject`; `reply` has `parent_id`).
- No wake in this CR (CR-SAN-014).

## Gap-analysis findings (2026-06-07) — verdict SPEC_UPDATE applied; now READY

Verified Pi API against `earendil-works/pi@main` (via opensrc) and the CLI surface against
`sandesh/cli.py`.

**Pi `ToolDefinition` (correction):** `pi.registerTool({ name, label, description, parameters, execute })`
— a **`label`** field is required (in addition to `name`/`description`); `parameters` is a **TypeBox**
schema (`import { Type } from "typebox"`; `Type.Object/String/Array/Optional`; `StringEnum` from
`@earendil-works/pi-ai` for `kind`); `execute(toolCallId, params, signal, onUpdate, ctx)` returns an
**`AgentToolResult`** (`{ content: [{ type: "text", text }], details? }`), not raw stdout. Devdeps:
`@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`, `typebox` (test runner is built-in `bun test`).

**Tool-param → CLI-flag mapping (the wrapper contract — DRIFT vs the loose spec):** the Pi tool
params may mirror the MCP names for the LLM, but `execute` maps to the CLI's *actual* flags:

| Pi tool | params (LLM-facing) | `sandesh` CLI argv |
|---|---|---|
| `sandesh_setup` | `project_id?` | `[--project P] setup` |
| `sandesh_register` | `address`, `kind?`, `name?`, `project_id?` | `register --address A [--kind …] [--name …]` |
| `sandesh_unregister` | `address`, `requester?`, `project_id?` | `unregister --address A [--as R]` |
| `sandesh_addressbook` | `project_id?` | `addressbook` |
| `sandesh_send` | `from`, `to[]`, `cc[]?`, `subject`, `kind?`, `body?`, `project_id?` | `send --from F --to a,b --cc c,d --subject S [--kind …] [--body …]` (**`to`/`cc` are comma-joined strings**) |
| `sandesh_reply` | `parent_id`, `from?`, `subject?`, `body?`, `project_id?` | `reply --to-msg N [--from F] [--subject …] [--body …]` (**`parent_id`→`--to-msg`**; **no** `--resolves`/`--all`) |
| `sandesh_inbox` | `recipient`, `unread_only?`, `project_id?` | `inbox --to R [--all]` (**`unread_only:false`→`--all`**, inverted) |
| `sandesh_fetch` | `recipient`, `mark?`, `project_id?` | `fetch --to R [--peek]` (**`mark:false`→`--peek`**, inverted) |
| `sandesh_thread` | `msg_id`, `project_id?` | `thread --id N` (**`msg_id`→`--id`**) |

Notes: `--project` is the shared flag (before or after the subcommand; defaults to `$SANDESH_PROJECT`).
The CLI also has `projects`, `actioned`, `notify` — **not** registered here (`actioned` is retired from
the model per D7; `notify` is the wake → CR-SAN-014; `projects` is non-essential). `reply --resolves`
/ `reply --all` exist in the CLI (deferred core, CR-SAN-012) but are **deliberately not exposed**
(matching the MCP surface).

**Re-run (2026-06-07, `/gap-analysis CR-013`) — two SPEC_UPDATEs applied (now READY):**
- **DRIFT-1 (Dim 2):** Pi **bundles** `@earendil-works/pi-coding-agent` / `@earendil-works/pi-ai` /
  `typebox` for extensions (Pi `docs/packages.md`): import them as **`peerDependencies: "*"`, do NOT
  bundle**; mirror in `devDependencies` for local `bun test`/`tsc`. (Was mis-specified as plain
  devDependencies.) Verified `StringEnum`←`@earendil-works/pi-ai`, `Type`←`typebox`, pkg
  `@earendil-works/pi-coding-agent@0.78.1`.
- **DRIFT-2 (Dim 2):** the extension entry is declared via the **`pi` key** in `package.json`
  (`"pi": { "extensions": ["./src/index.ts"] }`), per the canonical `with-deps` example — folded
  into §S1/AC1.

**Verdict: READY** — mapping table is the build contract; deps/entry corrected; no blocking drift
(Sandesh-core untouched).

## Acceptance criteria

- [x] **AC1** — `integrations/pi/` exists with `package.json` (+ `tsconfig.json`) declaring the
      `pi.extensions` entry and the Pi-bundled packages (`@earendil-works/pi-coding-agent`,
      `@earendil-works/pi-ai`, `typebox`) as `peerDependencies: "*"` (mirrored in `devDependencies`);
      a `default`-exported `(pi: ExtensionAPI) => void` entry; `bun test` runs in that folder.
- [x] **AC2** — the extension registers **exactly 9** tools named `sandesh_setup`,
      `sandesh_register`, `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`,
      `sandesh_reply`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` (asserted via a mocked
      `pi.registerTool` capturing names).
- [x] **AC3** — each tool registers with `name`, `label`, `description`, and a TypeBox `parameters`
      schema carrying the contract fields (per the mapping table), asserted for at least:
      `sandesh_send` (`from`, `to`, `cc`, `subject`, body, `project_id`), `sandesh_reply`
      (`parent_id`, `from`), `sandesh_register` (`address`).
- [x] **AC4** — `execute` builds the correct `sandesh` CLI argv per the mapping table (mocked
      `pi.exec`), verified for: `sandesh_send` (`--from/--to a,b/--cc/--subject/--body`, comma-joined
      lists), `sandesh_reply` (`parent_id`→`--to-msg`, no `--resolves`/`--all`), `sandesh_inbox`
      (`unread_only:false`→`--all`), `sandesh_fetch` (`mark:false`→`--peek`), `sandesh_thread`
      (`msg_id`→`--id`), and the `project_id`→`$SANDESH_PROJECT` fallback.
- [x] **AC5** — `execute` returns an `AgentToolResult`: a non-zero `pi.exec` `code` yields a result
      surfacing `stderr` as an error; a zero `code` yields `{content:[{type:"text",text:stdout}]}`
      (asserted with a mocked exec).
- [x] **AC6** — tool descriptions/param text reflect the usage-scenarios semantics (asserted by
      substring: `sandesh_send` description mentions To-wakes/Cc-silent; `sandesh_reply` conveys
      `parent_id` = original message id).
- [x] **AC7** — missing-CLI handling: when `pi.exec("sandesh","--version")` fails on load, the
      extension surfaces a clear install notice and does not throw (asserted with a mocked exec).
- [x] **AC8** — Sandesh-core untouched: `git diff` shows no changes under `sandesh/` (Python); the
      extension lives entirely under `integrations/pi/`; the Python suites stay green.

## Estimated size
Medium: a new TS package + 9 thin tool wrappers + `bun test` unit tests. Mechanical once the scaffold +
the first tool pattern are set.

## Risks / open questions
- **Toolchain:** TS under `integrations/pi/` tested with **`bun test`** (native; JUnit + lcov),
  driven by the **`bun-*` agents** via **`bun-crucible.py`** (register/test/regression/check/pre-merge-gate;
  `tsc --noEmit` syntax gate). Built + smoke-tested 2026-06-07.
- **CLI output parsing** — the CLI is human-readable, not JSON. Tools may return verbatim output for
  hard-to-parse verbs; a future `--json` CLI flag (separate CR, would touch Sandesh-core) could make
  this robust — out of scope here.
- **Pi API drift** — pin `@earendil-works/pi-coding-agent`; verify `registerTool`/`exec` signatures
  against the installed version at impl time (verified 2026-06 in DN-pi-wake).

## Non-goals
- The wake (CR-SAN-014) — verbs only here.
- Packaging/listing on `pi.dev/packages` (CR-SAN-015).
- Any change to Sandesh-core, the CLI, or the MCP surface.

## Implementation Notes (2026-06-07)

**First TypeScript CR — established the bun stack** (`~/.claude/scripts/bun-crucible.py` + `bun-*`
agents; `bun test` + `tsc --noEmit`; agent `context-mode`→`lean-ctx`). Three cycles, agent-dispatched.

- **Scaffold** (`96a02c2` + `3d4fa00`): `integrations/pi/` — package.json (`pi.extensions`,
  peerDependencies `"*"` for `pi-coding-agent`/`pi-ai`/`typebox` mirrored in devDeps), tsconfig
  (`@types/bun`), `.gitignore`, bun.lock. (The C0 RED agent created this; it was committed as a setup
  chore after an interrupted run — see [[interrupted-agents-leave-partial-work]].)
- **C0** — registration surface (`9105f6a` RED / `8fcdb24` + `fd78c53` GREEN): `src/index.ts`
  registers the **9** tools (TypeBox params, `label`, descriptions from usage-scenarios), stub
  `execute` typed `AgentToolResult<undefined>`. 13 tests.
- **C1** — `execute` (`2ff7e0e` RED / `abdd08a` GREEN): real per-tool `sandesh` CLI argv per the
  mapping table (`projectPrefix` env fallback, comma-joined to/cc, `--to-msg`/`--id`/`--as`, inverted
  `--all`/`--peek`) + result mapping (zero→stdout, non-zero→error text w/ stderr). 45 tests.
- **C2** — missing-CLI probe (`076f977` RED / `217a8fe` test-fix / `a0f3470` GREEN): `session_start`
  → `pi.exec("sandesh","--version")` → `ctx.ui.notify(…,"warning")` on failure, no throw, verbs
  unaffected. 15 tests. (An orchestrator-approved test-only fix added `pi.on` to the C0/C1 fakes +
  corrected a `.resolves.not.toThrow()` misuse — the GREEN agent escalated rather than bend production.)
- **VERIFY** (`CR-SAN-013-VERIFY`): 73/73, tsc clean, all AC1–AC8 PASS, 0 blocking; SF#1 (no
  `isError`) non-actionable — the Pi `AgentToolResult` type has no such field; errors surface via
  content text by design.
- **Pre-merge gate**: tsc clean; **73/73 bun tests; 100% line + 100% function coverage**; Sandesh-core
  untouched (`git diff develop..HEAD -- sandesh/` empty → Python suites unaffected).
- **Remaining (CR-SAN-014):** the native wake loop. **(CR-SAN-015):** Pi-package listing.
