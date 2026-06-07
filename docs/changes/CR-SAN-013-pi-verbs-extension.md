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
  `tsconfig.json`, and a test runner (**vitest**). Dev-dependency on
  **`@earendil-works/pi-coding-agent`** (for `ExtensionAPI` types) and **`@earendil-works/pi-ai`**
  (for `StringEnum` / Typebox param schemas). No runtime deps beyond what Pi provides.
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

### §S5 — Tests (vitest)
- Unit-test each tool's `execute` with a **mocked `pi.exec`**: assert the exact CLI argv built from
  params (incl. project/from env fallback, to/cc lists, body handling) and the result/error mapping
  (zero vs non-zero `code`).
- Assert the registered tool **surface**: the 9 tool names, and that each `parameters` schema
  carries the key fields (e.g. `send` has `to`/`cc`/`subject`; `reply` has `parent_id`).
- No wake in this CR (CR-SAN-014).

## Acceptance criteria

- [ ] **AC1** — `integrations/pi/` exists with `package.json` (+ `tsconfig.json`, vitest config); a
      `default`-exported `(pi: ExtensionAPI) => void` entry; `npm test` (vitest) runs in that folder.
- [ ] **AC2** — the extension registers **exactly 9** tools named `sandesh_setup`,
      `sandesh_register`, `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`,
      `sandesh_reply`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` (asserted via a mocked
      `pi.registerTool` capturing names).
- [ ] **AC3** — each tool's `parameters` (Typebox) carries the contract fields, asserted for at
      least: `sandesh_send` (`from`, `to`, `cc`, `subject`, optional body, `project_id`),
      `sandesh_reply` (`parent_id`, `from`), `sandesh_register` (`address`).
- [ ] **AC4** — `execute` builds the correct `sandesh` CLI argv (mocked `pi.exec`): verified for
      `sandesh_send` (from/to/cc/subject/body) and `sandesh_fetch` (recipient/mark), incl. the
      `project_id`→`$SANDESH_PROJECT` fallback.
- [ ] **AC5** — error mapping: a non-zero `pi.exec` `code` makes the tool report an error carrying
      `stderr`; a zero `code` returns `stdout` (asserted with a mocked exec).
- [ ] **AC6** — tool descriptions/param text reflect the usage-scenarios semantics (asserted by
      substring: `sandesh_send` description mentions To-wakes/Cc-silent; `sandesh_reply` conveys
      `parent_id` = original message id).
- [ ] **AC7** — missing-CLI handling: when `pi.exec("sandesh","--version")` fails on load, the
      extension surfaces a clear install notice and does not throw (asserted with a mocked exec).
- [ ] **AC8** — Sandesh-core untouched: `git diff` shows no changes under `sandesh/` (Python); the
      extension lives entirely under `integrations/pi/`; the Python suites stay green.

## Estimated size
Medium: a new TS package + 9 thin tool wrappers + vitest unit tests. Mechanical once the scaffold +
the first tool pattern are set.

## Risks / open questions
- **Toolchain:** TS + vitest under `integrations/pi/`, driven by the `vscode-*` TS agents
  (no bun/TS crucible script exists; `crucible-report-vscode` ingests vitest JUnit+lcov). Confirm at
  gap-analysis (vitest vs bun test).
- **CLI output parsing** — the CLI is human-readable, not JSON. Tools may return verbatim output for
  hard-to-parse verbs; a future `--json` CLI flag (separate CR, would touch Sandesh-core) could make
  this robust — out of scope here.
- **Pi API drift** — pin `@earendil-works/pi-coding-agent`; verify `registerTool`/`exec` signatures
  against the installed version at impl time (verified 2026-06 in DN-pi-wake).

## Non-goals
- The wake (CR-SAN-014) — verbs only here.
- Packaging/listing on `pi.dev/packages` (CR-SAN-015).
- Any change to Sandesh-core, the CLI, or the MCP surface.
