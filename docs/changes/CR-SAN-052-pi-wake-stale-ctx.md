# CR-SAN-052 — sandesh-pi: the wake supervisor survives a stale extension ctx

**Type:** bugfix
**Priority:** High (a session replacement or reload while a watcher is armed can take down the whole Pi process)
**Depends on:** CR-SAN-048
**Labels:** pi, bun, wake
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.7 (D4 wake supervision — the
state machine's terminal transitions)

**Author:** Antony John
**Co-author:** Vidushi-Sandesh (Mainline — Sandesh)

## Context

`WakeSupervisor` (`integrations/pi/src/wake.ts`) runs one detached promise chain per address: `launch`
calls `deps.exec`, and the child's exit drives `onExit`, which may call `deps.sendUserMessage`,
`deps.notify`, `deps.resolve` and `deps.exec` again. In production those deps are the captured `pi`
object (`makeSupervisor` in `index.ts`). After a session replacement or reload (`ctx.newSession()`,
`ctx.fork()`, `ctx.switchSession()`, `ctx.reload()`) Pi invalidates that object: every `ExtensionAPI`
action, `exec` and `sendUserMessage` included, then throws synchronously
(`This extension ctx is stale after session replacement or reload …`, `@earendil-works/pi-coding-agent`
0.78 `extensions/loader.js` `assertActive`).

`session_shutdown` does stop the loops, but the invalidation and the child's exit race it: a child that
exits after the ctx is invalidated and before (or while) the stop lands drives `onExit` → `launch` →
`deps.exec`, which throws inside a detached chain nobody catches. The throw surfaces as an unhandled
rejection and Pi exits with `uncaughtException` (observed 2026-09-30 while dogfooding the extension).
The same escape exists for a throwing `sendUserMessage` (on mail) and a throwing `notify` (while
reporting a terminal exit).

## Scope

### §S1 — a synchronous `exec` throw is an exit-1 result (bun)

- In `WakeSupervisor.launch`, a synchronous throw from `deps.exec` is handled exactly like a rejected
  exec promise already is: `onExit(entry, gen, { code: 1, stdout: "", stderr: String(err) })`. The
  existing exit-1 path then applies unchanged — `running:false`, `lastExit:1`, one `notify(…, "error")`
  whose text contains `exit 1` and `no envelope`, no relaunch, no `sendUserMessage`.
- `start()` is unchanged. The guard sits in the shared `launch()`, so a synchronous `exec` throw at the
  very first launch takes the same exit-1 path (one error notify, `running:false`) instead of
  propagating out of `start()` — one code path, no first-launch special case (scope reconciled
  2026-09-30 at C1 GREEN; unobservable in production, where `start()` only runs from a live ctx). A
  `resolve` throw at the first launch still propagates (AC4).

**Surfaces (verified 2026-09-30):** `integrations/pi/src/wake.ts` `launch()` (l.139–159), `onExit()`
(l.161–215); `WakeDeps.exec` (l.28) returns `Promise<WakeExecResult>`.

### §S2 — a throw that escapes `onExit` halts that watcher quietly (bun)

- Any throw or rejection escaping `onExit` (a throwing `sendUserMessage`, `notify` or `resolve`, a
  rejecting `sleep`, or `exec` throwing on the relaunch after §S1's own error report) ends the watcher
  it belongs to: `running:false`, the entry marked stopped, its `AbortController` aborted, **no**
  relaunch, **no** further host call (no second `notify`, no `sendUserMessage`), nothing rethrown. The
  process observes **no** `unhandledRejection`.
- Only the affected entry halts; other addresses' watchers are untouched. `status()` keeps reporting
  the halted entry with `running:false` and its last `lastExit`. `stop()`/`stop(address)` on a halted
  entry counts it as not running (`stopped: 0` if nothing else runs).
- Halting is a supervisor-internal terminal transition (it may reuse `stop(address)`); the `WakeDeps`
  interface, the `WatcherStatus` shape and the 16-tool surface are unchanged.

### §S3 — design contract

- PRD-axi-toon §4.7 state-machine table (v1.3) carries the transition this CR implements: a host dep
  that throws (stale ctx after session replacement/reload) → halt that watcher: no relaunch, no
  rethrow, the process survives. This CR pins that row (AC7).

## Acceptance criteria

- [ ] **AC1** — unit (`src/wake_supervisor.test.ts`, new describe block reusing the file's `makeDeps` /
  `notifyEnvelope` / `flush` fixtures — no duplicate fixture builders): with `exec` resolving `exit 2`
  on its first call and **throwing synchronously** `new Error("This extension ctx is stale after session
  replacement or reload.")` on its second, after `start("Mainline - Demo", "Demo")` + flush: `exec` was
  called exactly 2 times; `status()[0]` is `{ running: false, lastExit: 1 }` (on those fields); `notify`
  was called exactly once with level `"error"` and text containing `exit 1` and `no envelope`;
  `sendUserMessage` was never called; no `unhandledRejection` was observed. The same assertions hold when
  the second call returns a **rejected** promise instead.
- [ ] **AC2** — unit: `exec` resolves `exit 0` with `unread [7]`; `sendUserMessage` throws the stale
  error. After start + flush: `sendUserMessage` called exactly once; `exec` called exactly once (no
  relaunch); `notify` never called; `status()[0].running === false`; no `unhandledRejection`.
- [ ] **AC3** — unit: `exec` resolves `exit 1`; `notify` throws the stale error. After start + flush:
  `notify` called exactly once; `exec` called exactly once; `status()[0].running === false`; no
  `unhandledRejection`.
- [ ] **AC4** — unit: `exec` resolves `exit 2` on its first call; `resolve` throws the stale error on its
  second call. After start + flush: `exec` called exactly once; `status()[0].running === false`; no
  `unhandledRejection`. A `resolve` throw on the FIRST launch still propagates out of `start()`
  (existing behaviour, pinned).
- [ ] **AC5** — isolation: two addresses started; only the first's `sendUserMessage` path throws (its
  `exec` resolves `exit 0 [7]`, the second's stays pending). After flush: `status()` reports the first
  `running:false` and the second `running:true`; `stop()` returns `{ stopped: 1 }`.
- [ ] **AC6** — integration (production wiring, `src/wake.test.ts` harness): `registerExtension` with a
  fake `pi` whose `exec` resolves `exit 2` for the first `notify` spawn and then **throws** the stale
  error synchronously; `sandesh_notify_start({address:"Mainline - Demo", project:"Demo"})` returns
  `ok:true already:false`; after flush the process observed no `unhandledRejection`, and
  `sandesh_notify_status()` decodes with the watcher `running:false`, `lastExit:1`.
- [ ] **AC7** — PRD-axi-toon §4.7 table contains a row whose Event cell contains `throws` and whose Action
  cell contains `no relaunch` (pinned in `src/docs.test.ts`). Full `bun test` green (existing wake
  suites unchanged); python gate green (`tests/test_user_guide.py` and the docs pins unaffected).

## Estimated size

Small — ~25 lines in `wake.ts`, ~6 unit tests + 1 integration test, one PRD table row + one doc pin.

## Risk

- Capturing `unhandledRejection` in tests needs a process-level listener installed for the describe
  block and removed after it; a leaked listener would mask failures in other files. Install in
  `beforeAll`, remove in `afterAll`, reset the capture array per test.
- `bun test` reports an unhandled rejection as a failing run even without the listener, so a regression
  cannot pass silently.

## Non-goals

- Detecting session replacement proactively (a `session_shutdown`-independent stop), re-arming the
  watcher after a reload, or any change to `session_start`/`session_shutdown` handlers.
- Changes to the `WakeDeps` interface, the notify exit table, the 16-tool surface, the CLI, or the MCP
  server.
