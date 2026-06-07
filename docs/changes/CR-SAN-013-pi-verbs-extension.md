# CR-SAN-013 — Pi extension: scaffold + verb tools (`integrations/pi/`)

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-008 (the installed `sandesh` CLI the extension shells to)
**Labels:** phase-4, pi, typescript, integration
**Phase:** Phase 4 (Pi integration)
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
  zero extra deps; `bun:test` for `test`/`expect`/`mock`/`spyOn`). Dev-dependency on
  **`@earendil-works/pi-coding-agent`** (for `ExtensionAPI` types) and **`@earendil-works/pi-ai`**
  (for `StringEnum`) + **`typebox`** (param schemas). No runtime deps beyond what Pi provides.
- The extension entry is a single default-exported function `export default function (pi: ExtensionAPI) { … }`
  loadable from `.pi/extensions/` or `~/.pi/agent/extensions/` (packaging/listing = CR-SAN-015).
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

**Verdict: READY** — mapping table is the build contract; no blocking drift (Sandesh-core untouched).

## Acceptance criteria

- [ ] **AC1** — `integrations/pi/` exists with `package.json` (+ `tsconfig.json`); a
      `default`-exported `(pi: ExtensionAPI) => void` entry; `bun test` runs in that folder.
- [ ] **AC2** — the extension registers **exactly 9** tools named `sandesh_setup`,
      `sandesh_register`, `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`,
      `sandesh_reply`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` (asserted via a mocked
      `pi.registerTool` capturing names).
- [ ] **AC3** — each tool registers with `name`, `label`, `description`, and a TypeBox `parameters`
      schema carrying the contract fields (per the mapping table), asserted for at least:
      `sandesh_send` (`from`, `to`, `cc`, `subject`, body, `project_id`), `sandesh_reply`
      (`parent_id`, `from`), `sandesh_register` (`address`).
- [ ] **AC4** — `execute` builds the correct `sandesh` CLI argv per the mapping table (mocked
      `pi.exec`), verified for: `sandesh_send` (`--from/--to a,b/--cc/--subject/--body`, comma-joined
      lists), `sandesh_reply` (`parent_id`→`--to-msg`, no `--resolves`/`--all`), `sandesh_inbox`
      (`unread_only:false`→`--all`), `sandesh_fetch` (`mark:false`→`--peek`), `sandesh_thread`
      (`msg_id`→`--id`), and the `project_id`→`$SANDESH_PROJECT` fallback.
- [ ] **AC5** — `execute` returns an `AgentToolResult`: a non-zero `pi.exec` `code` yields a result
      surfacing `stderr` as an error; a zero `code` yields `{content:[{type:"text",text:stdout}]}`
      (asserted with a mocked exec).
- [ ] **AC6** — tool descriptions/param text reflect the usage-scenarios semantics (asserted by
      substring: `sandesh_send` description mentions To-wakes/Cc-silent; `sandesh_reply` conveys
      `parent_id` = original message id).
- [ ] **AC7** — missing-CLI handling: when `pi.exec("sandesh","--version")` fails on load, the
      extension surfaces a clear install notice and does not throw (asserted with a mocked exec).
- [ ] **AC8** — Sandesh-core untouched: `git diff` shows no changes under `sandesh/` (Python); the
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
