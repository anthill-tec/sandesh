# CR-SAN-048 — sandesh-pi: AXI pass-through tools, ambient context + home view, supervised wake (`sandesh_notify_start/status/stop`)

**Status:** PENDING
**Priority:** High (Model B's #2 request; lets Model B retire its own watcher)
**Depends on:** CR-SAN-047
**Labels:** pi, bun, wake, tools, npm
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.0 (AXI principles; P6/P7/P8 are the
extension's share), §4.6 (pass-through, errors as results, ambient context, home view, D5 decoder), §4.7
(supervision state machine, D4 arming), §4.8 (release)

## Context
`@anthill-tec/sandesh-pi` 0.3.6 shells the CLI and returns human text; its wake loop auto-arms at
`session_start` on `$SANDESH_ADDRESS`+`$SANDESH_PROJECT`, wakes with a generic message, has no id
de-duplication, no timeout cap, and no start/status/stop surface (`integrations/pi/src/index.ts`
`wakeLoop`, `session_start` handler). Model B's contract (PRD §4.7) is adopted verbatim; CR-SAN-047's
`--format toon` envelopes are the input. bun stack (`integrations/pi/`, `bun-*` agents,
`bun-crucible.py`).

## Scope
- **§S1 — decoder dependency (D5, settled).** Add `@toon-format/toon` (`^4.1.1`, ESM, zero deps — the reference
  implementation) to `dependencies` in `integrations/pi/package.json` and a `src/toon.ts` wrapper
  `decodeEnvelope(text): AxiEnvelope` (typed: `verb`, `ok`, `context`, `warnings: string[]`, `help?: string[]`,
  `error?: string`, plus `fields: Record<string, unknown>` for the rest). Gap-analysis settled the old gate: Pi
  installs extensions with a real `npm install` (`~/.pi/agent/npm/package.json` + lockfile, transitive deps
  present), so a runtime dependency IS installed — no fallback parser.
- **§S1b — CLI version gate.** `MIN_CLI_VERSION` → `[0, 4, 0]` (every tool now passes `--format toon`, which a
  CLI < 0.4.0 rejects with exit 2); the too-old notice names `0.4.0`. `version_gate.test.ts` updated.
- **§S2 — AXI pass-through tools (P1/P2/P3/P6).** `runSandesh` adds `--format toon` to every invocation;
  each of the 12 verb tools returns the CLI's stdout (the AXI envelope text) as its result text, untouched.
  Tool parameters gain the AXI knobs where the verb has them: `fields?: string[]` (→ `--fields`), `full?:
  boolean` (→ `--full`, fetch/thread), `limit?: number` (→ `--limit`, inbox/search); tool descriptions are the
  verb's concise reference (P10). **Errors are results:** a non-zero exit whose stdout decodes to an `ok:false`
  envelope is RETURNED as the result (not thrown) so the agent reads `error` + `help[]`; only an undecodable
  stdout (CLI missing, crash) throws with verb + code + stderr as today. `sandesh_unregister` reads
  `result: tombstoned|absent` from the envelope. `sandesh_status()` (new, no args) returns the CLI home view
  (`sandesh status --format toon`) plus `watcher: running|stopped` appended by the extension (P8).
- **§S2b — ambient context (P7).** On `session_start`, when both identity vars are set, run the home view
  once (`sandesh --format toon status`), strip the `bin:` and `description:` lines, and inject it via `pi.sendMessage({customType: "sandesh-status",
  content: <envelope text>, display: true}, {triggerTurn: false})` — a context message, not a user turn
  (≤ 12 lines incl. `help[2]`); when unset inject nothing and emit no warning. Failure of the probe never
  breaks session start (existing guard). The wake keeps `pi.sendUserMessage(…, {deliverAs: "followUp"})`.
- **§S3 — supervision state machine (PRD §4.7 table).** New `src/wake.ts` exporting
  `WakeSupervisor` (per-address entries: child handle, `startedAt`, `lastExit`, `lastIds: number[]`,
  `timeoutExits: number[]` (timestamps, 60 s window), `stopped`). `start(address, project)` refuses a
  running address (returns its status); spawns `sandesh --project P --format toon notify --to A` via
  `pi.exec` with an `AbortSignal`; on exit decodes the final envelope and applies: **0** → same ids as
  `lastIds` ⇒ no wake, relaunch after 30 s; different ⇒ `pi.sendUserMessage("Unread Sandesh mail for
  <A>: <ids>. Call sandesh_fetch for it.", {deliverAs:"followUp"})`, remember ids, relaunch now;
  **2** → relaunch silently, push timestamp; third within 60 s ⇒ `ctx.ui.notify(warning)` once per
  burst; **5** ⇒ relaunch once after 30 s, silently (a second consecutive 5 stops the loop silently); **1/3/4/signal** ⇒ stop + `ctx.ui.notify` with
  code + `error`. `stop(address?)` aborts the child's AbortSignal (Pi's `exec` sends SIGTERM, then SIGKILL after its 5 s grace) and clears; no address = all.
  `status()` returns the table. Clock and sleep injectable (`__setWakeClock`, `__setWakeSleepFn`).
- **§S4 — tools + command.** `sandesh_notify_start(address?, project?)` (defaults from
  `$SANDESH_ADDRESS`/`$SANDESH_PROJECT`; error naming both if unresolved), `sandesh_notify_status(project?)`,
  `sandesh_notify_stop(address?, project?)` — results are TOON envelopes built in-extension
  (`verb: notify_start|notify_status|notify_stop`, same shape as PRD §4.1; `project` scopes status/aggregate
  stop, and unscoped aggregates error when watchers span projects; P2 default columns
  `watchers[N]{address,running,lastExit}` — the full status set via a `fields` knob is
  deferred to the register). Slash command `/sandesh-watcher status [--project <id>] | stop [address] [--project <id>]`.
- **§S5 — arming (D4).** `session_start`: probe/nudge unchanged; the wake loop is armed **only** when
  `SANDESH_AUTOSTART=1` and both identity vars are set (then it calls the same `start`). Otherwise a
  one-line `ctx.ui.notify` info says wake is tool-started (`sandesh_notify_start`). The old
  `MISSING_ENV_NOTICE` warning is replaced by that info line. `session_shutdown` → `stop()` all. The
  old `wakeLoop` and its module-level state are deleted; `__resetWakeState` resets the supervisor.
- **§S6 — docs + manifests.** `integrations/pi/README.md` tool table (12 → 16 tools, AXI envelope
  results + `fields/full/limit` knobs, ambient context, arming change); `docs/USER_GUIDE.md` §Pi (0.4.0 behaviour change: set `SANDESH_AUTOSTART=1`
  or call `sandesh_notify_start`); `server.json`/`package.json` versions via `release.sh set-version`
  at release time (not in this CR).

## Acceptance criteria
- **AC1** — Dependency proof: `package.json` `dependencies["@toon-format/toon"]` is `^4.1.1`; `npm pack` the
  package into a temp dir and `npm install <tarball>` there (a throwaway prefix, never `~/.pi`) → `node_modules/
  @toon-format/toon/package.json` exists; `src/toon.ts` imports from it and `decodeEnvelope` round-trips a
  CR-047 fixture envelope. The extension registers exactly 16 tools (12 verbs + `sandesh_status` + 3 notify).
- **AC1b** — Version gate: `MIN_CLI_VERSION` is `[0,4,0]`; a fake `--version` of `0.3.6` → the too-old notice
  naming `0.4.0`; `0.4.0` passes.
- **AC2** — Every verb tool's result text decodes via `decodeEnvelope` to `verb == <cli verb>` and
  `ok == true` on the happy path (fake `pi.exec` returning CR-047 fixture envelopes) and is byte-identical to
  the fixture stdout; each invocation's argv contains `--format` `toon`; `fields:["a","b"]` → `--fields a,b`,
  `full:true` → `--full`, `limit:20` → `--limit 20`; omitted knobs add no flag.
- **AC3** — Errors as results: `pi.exec` exit 1 with an `ok:false` envelope on stdout → the tool RETURNS that
  text (no throw) and it decodes to `ok:false` with `error` and `help[]`; exit 1 with empty/undecodable stdout
  → throws with verb + code + stderr (unchanged). `sandesh_unregister` with `result: tombstoned` → returned, not
  thrown.
- **AC3b** — Ambient context + home: with both identity vars set, `session_start` injects exactly one context
  block that decodes to the home envelope (`address`, `listening`, `unread`, `help[2]`), contains no `bin:`
  or `description:` line, and is ≤ 12 lines — asserted against the REAL `status` envelope shape (not a
  hand-written fixture);
  with either var unset nothing is injected and no warning is shown; `sandesh_status()` returns the same
  envelope plus `watcher: running|stopped` reflecting the supervisor.
- **AC4** — Supervision, exit 0: ids `[12,13]` → one `sendUserMessage` naming `12, 13` with
  `deliverAs:"followUp"` and an immediate relaunch; a second exit 0 with `[12,13]` → **no** message and
  the relaunch is delayed 30 s (injected clock); a third with `[14]` → message + immediate relaunch.
- **AC5** — Exit 2: relaunched silently; the third exit 2 within 60 s → exactly one `ctx.ui.notify`
  warning; a fourth outside the window → none.
- **AC6** — Exit 5 → no message, no notify; the loop relaunches once after 30 s (injected clock) and stays `running`; a second consecutive exit 5 stops the loop silently. Exit 1, 3, 4 and a signal (code
  `null`, `signalCode: "SIGTERM"`) → loop stops and one `ctx.ui.notify` carries the code and the
  envelope's `error`.
- **AC7b** — `sandesh_notify_status(project?)` and addressless `sandesh_notify_stop(project?)` require an explicit
  project filter when watchers span projects; the filter scopes returned rows and stop effects. Without a filter,
  a mixed-project aggregate returns an actionable `ok:false` error without stopping watchers. A rejected `exec` promise is handled as an undecodable exit 1
  (one error notify, no relaunch) — tested in `wake_supervisor.test.ts`.
- **AC7** — One per address: `sandesh_notify_start` twice for the same address → second returns
  `ok: true` with `already: true` and spawns nothing; two different addresses run concurrently;
  `sandesh_notify_stop({project: P})` stops only watchers in P; an unscoped stop across multiple
  projects returns an error and leaves all children running; `status` reflects each transition.
- **AC8** — Arming: with both identity vars and no `SANDESH_AUTOSTART`, `session_start` spawns no
  `notify` and emits the info line; with `SANDESH_AUTOSTART=1` it spawns exactly one; with the vars
  unset and `SANDESH_AUTOSTART=1` it emits the error naming both vars and spawns none.
  `session_shutdown` aborts every running child.
- **AC9** — `wake.test.ts`/`wake_lifecycle.test.ts` are rewritten against the supervisor; the old
  `wakeLoop` symbol is gone (`grep -c "function wakeLoop" src/index.ts` = 0); full `bun test` green;
  `tsc --noEmit` clean; `npm pack --dry-run` lists `LICENSE, README.md, package.json, src/index.ts,
  src/wake.ts, src/toon.ts` (the `files` whitelist is extended accordingly) and no `*.test.ts`.
- **AC10** — Real-binary smoke (extends CR-SAN-019's `smoke.test.ts`): the binary is resolved from
  `$SANDESH_BIN`, else `<repo>/.venv/bin/sandesh`, else PATH; the suite SKIPS (not fails) when the resolved
  binary lacks `--format` (probe `--help`). Every spawn carries a temp `XDG_DATA_HOME` in `env` (the real
  store is never touched — CR-SAN-049 rule). Flow: `start` → a message sent to the address → the supervisor's
  `sendUserMessage` fires naming the id → `stop` exits the child; the whole run under 30 s.

## Estimated size
Medium-large — a new supervisor module with an injectable clock, tool/command wiring, rewrite of two
wake test files, the install-proof smoke, README/USER_GUIDE updates.

## Risks / open questions
- `@toon-format/toon` is the extension's first runtime dependency; Pi's `npm install` resolves it (verified
  at gap-analysis against `~/.pi/agent/npm`).
- Model B's skills change is theirs; the tool names above are what we announce on the thread.

## Non-goals
- No MCP change; no CLI change (all CLI work is CR-SAN-047); no change to `cc` semantics or polling.
