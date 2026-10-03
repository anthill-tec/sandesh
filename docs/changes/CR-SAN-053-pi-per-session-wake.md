# CR-SAN-053 — sandesh-pi: per-session wake supervisor, loud unrequested stops, an honest `notify_start`

**Type:** bugfix
**Priority:** Critical (any sub-agent silently disables the parent session's wake — reported by Crucible, #1416)
**Depends on:** CR-SAN-048, CR-SAN-052
**Labels:** pi, bun, wake, hotfix
**Wave:** 17 · **Release:** 0.4.1
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.7 (D4 wake supervision — state machine + tools)

**Author:** Antony John
**Co-author:** Vidushi-Sandesh (Mainline — Sandesh)

## Context

A Pi sub-agent session loads the same `@anthill-tec/sandesh-pi` module instance as its parent and calls
`registerExtension()` again. Two pieces of module-level state are shared across those registrations:

- `let supervisor` — `registerExtension()` first calls `resetExtensionState()`, which runs
  `supervisor?.stop()` on the **previous registration's** supervisor, i.e. the parent session's. The parent
  watcher ends `running:false, lastExit:null`, no notice is posted, and mail to the address wakes nobody.
  The parent's tools keep closing over the parent's (now stopped) `sup`, so `sandesh_notify_status` reports
  the stopped entry.
- `let latestUi` — every tool, the command and `session_start` overwrite it, and the supervisor's `notify`
  dep routes through it. After a sub-agent ran, the parent's watcher notices go to the sub-agent's UI (gone
  or stale), not the parent's.

Separately, `sandesh_notify_start` returns `ok:true` with the watcher `running:true` even when the child
exits at once (e.g. exit 1 because the address is inactive): the snapshot is taken before the child has
run, and the exit-1 report is a `ctx.ui.notify` toast the agent never sees.

## Scope

### §S1 — each registration owns its supervisor and its UI route (bun)

- `registerExtension()` no longer stops anything. It creates its own `WakeSupervisor` and its own UI route;
  the module-level `supervisor` and `latestUi` variables are removed.
- The UI route is per registration: a closure variable set by that registration's tools, its
  `/sandesh-watcher` command and its `session_start` handler. The supervisor's `notify` dep reads that
  registration's route only.
- `session_shutdown` stops only the supervisor of the registration it belongs to. A sub-agent's
  registration, tools, command and shutdown never stop, replace or read the parent's watchers.
- `resetExtensionState()` remains as the test seam: it stops every supervisor created since the last reset
  (a module-level registry used **only** by this seam) and resets the binary resolution. Production code
  never calls it. `registerExtension()` still resets the binary resolution (re-probe at `session_start`).

**Surfaces (verified 2026-10-03):** `integrations/pi/src/index.ts` `latestUi` (l.49), `supervisor` (l.52),
`resetExtensionState()` (l.59–62), `makeSupervisor()` (l.65–74), `registerExtension()` (l.685–689),
`session_shutdown` (l.1322–1324); `latestUi = ctx.ui` sites (l.1067, 1091, 1139, 1176, 1197, 1246).

### §S2 — a stop nobody asked for is loud (bun)

- `WakeSupervisor.stop(address?: string, opts?: { requested?: boolean })`. When `opts?.requested` is not
  `true`, every entry it stops posts exactly one `notify(…, "warning")` whose text contains the entry's
  address and `sandesh_notify_start`.
- The requested stops pass `{ requested: true }` and stay quiet: `sandesh_notify_stop`,
  `/sandesh-watcher stop`, `session_shutdown`, and the `resetExtensionState()` test seam.
- `halt()` (CR-SAN-052, stale host deps) is unchanged and stays quiet — its host deps are unusable.

### §S3 — `sandesh_notify_start` reports a watcher that dies at start (bun)

- `WakeSupervisor` gains `settle(address: string, ms: number): Promise<WatcherStatus | undefined>`: it
  resolves once the address's current child has exited and its exit has been handled, or after `ms`
  (via `deps.sleep`), whichever comes first, with the entry's snapshot at that moment.
- `WatcherStatus` gains `lastError: string | null` — the envelope `error` (or `no envelope`) of the last
  terminal exit, `null` otherwise. It is not a selectable watcher column; the default columns are unchanged.
- `sandesh_notify_start`, after a successful `start()` with `already:false`, awaits
  `settle(address, START_SETTLE_MS)` with `START_SETTLE_MS = 2000`. If the snapshot is `running:false`
  with a non-null `lastExit`, it returns an error envelope (`ok:false`) whose `error` contains the address,
  `exit <code>` and the `lastError` text, with `help[]` naming `sandesh_addressbook` (check `status`) and
  `sandesh_register`. Otherwise it returns the existing `ok:true` envelope. `already:true` returns at once.
- A watcher still `running` after the window (no exit yet, an exit-0/exit-2 relaunch, or the exit-5 retry
  wait) is reported `ok:true` as today.

**Surfaces (verified 2026-10-03):** `integrations/pi/src/wake.ts` `WatcherStatus` (l.44–53), `start()`
(l.102–138), `stop()` (l.151–162), `launch()` (l.168–211), `onExit()` (l.223–287);
`index.ts` `sandesh_notify_start` `execute` (l.1090–1122).

### §S4 — design contract and docs

- PRD-axi-toon §4.7 gains (Change Control 1.5): watchers are owned per registration — a registration
  never stops or replaces another's, and sub-agent sessions are registrations; an unrequested `stop`
  surfaces a warning; `start` reports an exit inside the settle window as an error.
- `integrations/pi/README.md` §Wake states the per-session ownership, and the `sandesh_notify_start` row
  states the exit-at-start error.

## Acceptance criteria

- [ ] **AC1** — integration (`src/wake.test.ts` harness, production `registerExtension`): register the
  extension with fake `pi` A, then with fake `pi` B (same module). A's `sandesh_notify_start({address:
  "Mainline - Demo", project:"Demo"})` (A's `exec` pending) → `ok:true`. Then B is registered and B's
  `session_shutdown` handler runs. A's `sandesh_notify_status()` decodes with `Mainline - Demo`
  `running:true`; A's exec `signal.aborted === false`; B's `sandesh_notify_status()` returns no watchers.
- [ ] **AC2** — integration: as AC1, but registering B happens **before** A's watcher exits with
  `exit 1` (envelope `error: "x"`). A's UI `notify` is called exactly once with level `"error"`; B's UI
  `notify` is never called.
- [ ] **AC3** — integration: A's `session_shutdown` stops A's watcher (`running:false`) and posts no
  `notify`; A's `sandesh_notify_stop({address:"Mainline - Demo"})` and `/sandesh-watcher stop` likewise
  post no `notify`.
- [ ] **AC4** — unit (`src/wake_supervisor.test.ts`, reusing its `makeDeps` / `flush` fixtures): two
  watchers running; `stop()` with no `opts` → `{ stopped: 2 }` and exactly 2 `notify` calls, each
  `"warning"`, one text containing each address, both containing `sandesh_notify_start`.
  `stop(undefined, { requested: true })` on two running watchers → no `notify`.
- [ ] **AC5** — unit: `exec` resolves `exit 1` with an envelope `error: "address 'Mainline - Demo' is not
  registered"`; after `start` + `await settle("Mainline - Demo", 2000)` the snapshot is
  `{ running: false, lastExit: 1, lastError: "address 'Mainline - Demo' is not registered" }`, and
  `sleep` was not awaited to completion (the exit won the race). With `exec` pending, `settle` resolves
  after the injected `sleep(2000)` with `running: true`.
- [ ] **AC6** — integration: fake `pi` whose `exec` resolves `exit 1` with that envelope;
  `sandesh_notify_start` returns `ok:false`, `error` containing `Mainline - Demo`, `exit 1` and
  `is not registered`, and `help[]` containing `sandesh_addressbook` and `sandesh_register`. With `exec`
  pending it returns `ok:true` `already:false` and the watcher `running:true`.
- [ ] **AC7** — caller-existence: `grep -n "requested: true" integrations/pi/src/index.ts` returns ≥3
  non-test lines (notify_stop, the command, session_shutdown); `grep -n "\.settle(" integrations/pi/src/index.ts`
  returns ≥1; `grep -nE "^let (supervisor|latestUi)\b" integrations/pi/src/index.ts` returns nothing.
- [ ] **AC8** — docs pins (`src/docs.test.ts`): PRD-axi-toon §4.7 contains `per registration` and
  `unrequested`; the README §Wake contains `sub-agent`. Full `bun test` green; python gate green.

## Estimated size

Small–medium — ~60 lines across `index.ts`/`wake.ts`, ~8 tests, a PRD row + Change Control line, two
README sentences.

## Risk

- The settle window adds up to 2 s to a successful `sandesh_notify_start`. Acceptable for a call made once
  per session; the window ends early on any exit.
- Tests that relied on `registerExtension()` stopping the previous registration's watchers must call
  `resetExtensionState()` instead; the seam still stops every registration's supervisor.

## Non-goals

- Agent-visible wake for a dead watcher (a `sendUserMessage` on exit 1/3/4); detecting a sub-agent session
  as such; any change to the notify exit table, the CLI, the MCP server or the 16-tool surface beyond the
  `notify_start` error result.
