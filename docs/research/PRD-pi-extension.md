# PRD — Sandesh as a Pi Extension

**Status:** DRAFT (design contract — CRs spin from this later; no code yet)
**Owner:** Mainline - Sandesh
**Phase:** Phase 4 (Pi integration)
**Related:** `PRD-distribution.md` (Pi is a *first-class extension* integration, not the MCP
adapter), `PRD-mcp-server.md` §6 (the wake constraint this must re-examine for Pi)

Design contract (WHY + WHAT) for integrating Sandesh with the **Pi coding agent** (pi.dev) via
a **native Pi extension**, rather than routing through an MCP adapter. CRs derived from this cite
it via `**Design reference:**`.

---

## 1. Why

Pi (Earendil Works' `pi-coding-agent`) is the team's agent platform — its `context-mode` package
is the same tooling the orchestration agents already use. Pi **deliberately ships "No MCP"**
(considers MCP context-heavy); MCP works only through community adapters that proxy tools (extra
indirection + context). Pi's *idiomatic* extensibility is its **TypeScript extension API**:
first-class tools, lifecycle hooks, shell-out, packaging. So for Pi users the right integration is
a **native Pi extension**, not the MCP server. (Claude Code / Cursor keep using the MCP server +
official registry — CR-SAN-011.)

## 2. What it is (and is not)

**Is:** a TypeScript Pi extension that **registers the Sandesh verbs as native Pi tools**, each
delegating to the installed **`sandesh` CLI** via `pi.exec(...)`. A separate front-end adapter
(like `cli.py` and `mcp_server.py`) — **Sandesh-core stays pure Python**; the extension is a thin
TS shim. Distributed via Pi's package mechanism (npm:/git:, `pi.dev/packages`).

**Is not:** a rewrite of Sandesh in TS; a change to Sandesh-core; an MCP server (Pi uses the
extension instead). It does not embed messaging logic — it shells to `sandesh`.

## 3. Pi extension capabilities (verified from pi.dev/docs/latest/extensions)

- **`pi.registerTool({name, description, parameters: Typebox, execute(id, params, signal, onUpdate, ctx)})`** — first-class tools (use `StringEnum` from `@earendil-works/pi-ai`).
- **`pi.exec("cmd", [args], {signal, timeout})`** → `{stdout, stderr, code, killed}` — shell to the `sandesh` CLI.
- **Lifecycle hooks** `pi.on(...)`: `session_start`, `before_agent_start` (inject messages / modify prompt), `tool_call`/`tool_result`, `input`, `context`, `session_shutdown`.
- **State** outside the LLM context (`pi.appendEntry` + replay on `session_start`); **UI** (`ctx.ui.notify/confirm/setStatus`); **slash commands** (`pi.registerCommand`).
- **Distribution/discovery**: `~/.pi/agent/extensions/*.ts`, project `.pi/extensions/`, or `settings.json` `packages` (`npm:@org/x`, `git:github.com/u/r@v`). Runtime deps go in `dependencies`.

## 4. Decisions

**PE1 — Native Pi extension, not the MCP adapter.** For Pi, ship a TS extension; it's first-class,
avoids the MCP-proxy context overhead, and matches Pi's "no MCP" stance.

**PE2 — Verbs via `pi.exec` to the `sandesh` CLI.** Each tool (`sandesh_send`/`fetch`/`register`/…)
runs the CLI and parses its output. Depends on `sandesh` being installed (CR-SAN-008 packaging →
`uv tool install` / `pipx` / `install.sh`). The extension documents/depends-on that prerequisite.

**PE3 — Sandesh-core stays Python-pure.** The extension is a separate TS artifact (repo subdir
`integrations/pi/` or its own package), never mixed into the Python package.

**PE4 — Tool surface mirrors the CLI/MCP verbs.** Same verbs, Typebox param schemas, and the
param descriptions/address-format guidance from `docs/usage-scenarios.md` (reuse, don't reinvent).

**PE5 — Distribute via Pi packages.** Publish as `npm:`/`git:` for `pi install`; list on
`pi.dev/packages`. (Independent of the PyPI dist, which is the `sandesh` CLI it calls.)

## 5. The WAKE — the crux — **RESOLVED: (a) native injection** (see `DN-pi-wake.md`)

> **Spike done (2026-06-07, `DN-pi-wake.md`).** A Pi extension **can wake an idle agent** from a
> background watcher — `ExtensionAPI.sendUserMessage()` ("always triggers a turn") /
> `sendMessage(…, {triggerTurn:true})`, demonstrated by the canonical `file-trigger.ts` example.
> So the wake is **native** (outcome **a**): one extension carries the verbs **and** owns the wake
> loop — no out-of-band `run_in_background`, unlike Claude Code. Recommended design **W1**: a
> detached `session_start` loop that `pi.exec`s `sandesh notify` (the doorbell) and on exit-0 calls
> `sendUserMessage(...)`. The original pre-spike analysis is kept below for context.

On Claude Code the wake **must** be the host's `run_in_background` (an MCP server can't re-invoke a
sleeping agent — PRD-mcp-server §6). Pi extensions have **`session_start`/`before_agent_start`
hooks, background capability, message injection, and an RPC/SDK mode** — which *might* let a Pi
extension run the `notify` watcher and **inject a turn when mail arrives, natively**, with no host
background-task tool. **But the decisive question is unverified:**

> Can a Pi extension **trigger/enqueue a turn on an already-idle agent** (from a background
> watcher), or — like every other host — can it only act *within* an existing turn?

This must be resolved by a **spike** (its own DN if it gets deep) before any wake design. Candidate
outcomes:
- **(a) Native injection** — if Pi's RPC/SDK/session API lets a background watcher enqueue a user
  turn, the wake becomes a clean Pi-native loop (better than `run_in_background`).
- **(b) External watcher + Pi RPC** — run `sandesh notify` externally; on exit, drive Pi via its
  RPC/SDK to inject the fetched mail.
- **(c) Manual/documented** — same model as Claude Code (background `sandesh notify` yourself).

Until the spike resolves this, the wake is **NOT designed**; the extension can ship the **verbs**
independently (PE1–PE5) and treat the wake as a follow-on.

## 6. CRs to spin from this PRD (later)

- **Pi extension scaffold + verb tools** (TS extension; `registerTool` over `sandesh` CLI; Typebox
  schemas; depends on CR-SAN-008). The verbs, no wake.
- **Pi native wake** — ~~spike~~ **done (`DN-pi-wake.md`, outcome a)**; the wake CR implements **W1**
  (a detached `session_start` loop: `pi.exec sandesh notify` → on exit-0 `sendUserMessage`). No
  separate spike needed.
- **Packaging/listing** on `pi.dev/packages` (npm:/git:).

(Numbers allocated when scheduled, against the queue HEAD.)

## 7. Non-goals / out of scope

- Any change to Sandesh-core or the Python package.
- The MCP server / official-registry path (that's for non-Pi clients — CR-SAN-011).
- Supporting Pi's HTTP-only MCP adapters (Sandesh is stdio/local; the extension shells the CLI
  directly, so no transport question for the verbs).
- Other editors' extension ecosystems (revisit per demand).
