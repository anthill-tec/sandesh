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
- **§S1 — decoder dependency (D5, gated).** Add `@toon-format/toon` to `dependencies` in
  `integrations/pi/package.json` and a `src/toon.ts` wrapper `decodeEnvelope(text): AxiEnvelope`
  (typed: `verb`, `ok`, `context`, `warnings: string[]`, `help?: string[]`, `error?: string`, plus a
  `fields: Record<string, unknown>` for the rest). **Gate:** the AC1 install proof must pass; if
  `pi install npm:` does not install dependencies, replace §S1 with `src/toon.ts` implementing a
  ≤60-line parser for scalars + `key[N]: a,b` inline arrays + one nesting level (enough for the notify
  envelope and `context`), and record the choice in this spec's `### S1 Findings`.
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
  once and inject it as compact session context via the harness's context seam (≤6 lines incl. `help[2]`);
  when unset inject nothing and emit no warning. Failure of the probe never breaks session start (existing
  guard).
- **§S3 — supervision state machine (PRD §4.7 table).** New `src/wake.ts` exporting
  `WakeSupervisor` (per-address entries: child handle, `startedAt`, `lastExit`, `lastIds: number[]`,
  `timeoutExits: number[]` (timestamps, 60 s window), `stopped`). `start(address, project)` refuses a
  running address (returns its status); spawns `sandesh --project P --format toon notify --to A` via
  `pi.exec` with an `AbortSignal`; on exit decodes the final envelope and applies: **0** → same ids as
  `lastIds` ⇒ no wake, relaunch after 30 s; different ⇒ `pi.sendUserMessage("Unread Sandesh mail for
  <A>: <ids>. Call sandesh_fetch for it.", {deliverAs:"followUp"})`, remember ids, relaunch now;
  **2** → relaunch silently, push timestamp; third within 60 s ⇒ `ctx.ui.notify(warning)` once per
  burst; **5** ⇒ stop silently ("already running"); **1/3/4/signal** ⇒ stop + `ctx.ui.notify` with
  code + `error`. `stop(address?)` aborts (SIGTERM; SIGKILL after 2 s) and clears; no address = all.
  `status()` returns the table. Clock and sleep injectable (`__setWakeClock`, `__setWakeSleepFn`).
- **§S4 — tools + command.** `sandesh_notify_start(address?, project?)` (defaults from
  `$SANDESH_ADDRESS`/`$SANDESH_PROJECT`; error naming both if unresolved), `sandesh_notify_status()`,
  `sandesh_notify_stop(address?)` — results are TOON envelopes built in-extension
  (`verb: notify_start|notify_status|notify_stop`, same shape as PRD §4.1, `watchers[N]{address,
  project,running,pid,startedAt,lastExit,lastIds,timeoutExits}`). Slash command `/sandesh-watcher
  status|stop [address]`.
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
- **AC1** — Install proof (smoke, real binaries): `npm pack` the package, `pi install` it from the
  tarball path into a temp Pi home; assert `node_modules/@toon-format/toon` exists under the installed
  package (or the fallback parser is compiled in, per §S1 gate) and `pi` lists the 16 tools (12 verbs + `sandesh_status` + 3 notify).
- **AC2** — Every verb tool's result text decodes via `decodeEnvelope` to `verb == <cli verb>` and
  `ok == true` on the happy path (fake `pi.exec` returning CR-047 fixture envelopes) and is byte-identical to
  the fixture stdout; each invocation's argv contains `--format` `toon`; `fields:["a","b"]` → `--fields a,b`,
  `full:true` → `--full`, `limit:20` → `--limit 20`; omitted knobs add no flag.
- **AC3** — Errors as results: `pi.exec` exit 1 with an `ok:false` envelope on stdout → the tool RETURNS that
  text (no throw) and it decodes to `ok:false` with `error` and `help[]`; exit 1 with empty/undecodable stdout
  → throws with verb + code + stderr (unchanged). `sandesh_unregister` with `result: tombstoned` → returned, not
  thrown.
- **AC3b** — Ambient context + home: with both identity vars set, `session_start` injects exactly one context
  block that decodes to the home envelope (`address`, `listening`, `unread`, `help[2]`) and is ≤ 6 lines;
  with either var unset nothing is injected and no warning is shown; `sandesh_status()` returns the same
  envelope plus `watcher: running|stopped` reflecting the supervisor.
- **AC4** — Supervision, exit 0: ids `[12,13]` → one `sendUserMessage` naming `12, 13` with
  `deliverAs:"followUp"` and an immediate relaunch; a second exit 0 with `[12,13]` → **no** message and
  the relaunch is delayed 30 s (injected clock); a third with `[14]` → message + immediate relaunch.
- **AC5** — Exit 2: relaunched silently; the third exit 2 within 60 s → exactly one `ctx.ui.notify`
  warning; a fourth outside the window → none.
- **AC6** — Exit 5 → loop stops, no message, no notify, no relaunch. Exit 1, 3, 4 and a signal (code
  `null`, `signalCode: "SIGTERM"`) → loop stops and one `ctx.ui.notify` carries the code and the
  envelope's `error`.
- **AC7** — One per address: `sandesh_notify_start` twice for the same address → second returns
  `ok: true` with `already: true` and spawns nothing; two different addresses run concurrently;
  `sandesh_notify_stop()` with no address stops both (children aborted); `status` reflects each
  transition.
- **AC8** — Arming: with both identity vars and no `SANDESH_AUTOSTART`, `session_start` spawns no
  `notify` and emits the info line; with `SANDESH_AUTOSTART=1` it spawns exactly one; with the vars
  unset and `SANDESH_AUTOSTART=1` it emits the error naming both vars and spawns none.
  `session_shutdown` aborts every running child.
- **AC9** — `wake.test.ts`/`wake_lifecycle.test.ts` are rewritten against the supervisor; the old
  `wakeLoop` symbol is gone (`grep -c "function wakeLoop" src/index.ts` = 0); full `bun test` green;
  `tsc --noEmit` clean; `npm pack --dry-run` lists `LICENSE, README.md, package.json, src/index.ts,
  src/wake.ts, src/toon.ts` (the `files` whitelist is extended accordingly) and no `*.test.ts`.
- **AC10** — Real-binary smoke (extends CR-SAN-019's): with `sandesh` 0.4.0-dev on PATH, `start` →
  a message sent to the address → the supervisor's `sendUserMessage` fires naming the id → `stop`
  exits the child; the whole run under 30 s.

## Estimated size
Medium-large — a new supervisor module with an injectable clock, tool/command wiring, rewrite of two
wake test files, the install-proof smoke, README/USER_GUIDE updates.

## Risks / open questions
- §S1 gate: whether `pi install npm:` installs dependencies is unknown until AC1 runs — the fallback is
  specified so the CR cannot stall on it.
- Model B's skills change is theirs; the tool names above are what we announce on the thread.

## Non-goals
- No MCP change; no CLI change (all CLI work is CR-SAN-047); no change to `cc` semantics or polling.
