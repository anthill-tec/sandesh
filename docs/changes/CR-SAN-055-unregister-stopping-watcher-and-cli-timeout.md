# CR-SAN-055 — unregister completes against a stopping watcher; the Pi extension's CLI calls time out

**Type:** bugfix
**Priority:** High (a shutdown's `notify_stop` + `unregister` can leave the address active — Model B, #1464)
**Depends on:** CR-SAN-024, CR-SAN-043, CR-SAN-053
**Labels:** cli, pi, notify, unregister, hotfix
**Wave:** 19 · **Release:** 0.4.3
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.7 (wake supervision)

**Author:** Antony John
**Co-author:** Vidushi-Sandesh (Mainline — Sandesh)

## Context

Model B's Mainline shut down by calling `sandesh_notify_stop` and `sandesh_unregister` for its own address in one
Pi tool batch. The batch never returned (a stall before either tool executed, outside Sandesh), and the address
was left `active`. Two Sandesh-side gaps sit on that path:

- **The stop/unregister race.** `unregister` of an address whose watcher is live writes the tombstone flag and
  returns `('tombstoned', pid)` at once, leaving the address active. A watcher that was just sent SIGTERM is
  still "live" (fresh heartbeat, pid alive) for the few milliseconds before its `atexit` releases the row, so a
  concurrent stop + unregister leaves the address active even though the watcher is gone a moment later. A
  healthy watcher sees the tombstone on its next poll and exits 3, but the caller must still run `unregister`
  again.
- **No timeout on the extension's CLI calls.** Every sandesh-pi verb runs the CLI through `pi.exec` with the
  tool's abort signal and no timeout, so a CLI child that never exits holds the tool call (and its batch)
  indefinitely. Pi's own `exec` reports a timed-out (signal-killed) child as exit `0` with `killed: true`, and
  its SIGKILL fallback never fires (upstream earendil-works/pi#7864).

## Scope

### §S1 — `unregister` waits, bounded, for a live watcher to go offline (library + CLI + MCP)

- `sandesh_db.unregister(con, recipient, requester, project=None, *, wait_secs=None)`: when the recipient's
  notifier is live, it writes the tombstone flag (as today), then waits up to `wait_secs` (default one poll
  interval, `poll_interval()`) for the notifier to go offline (`notifier_live()` returns `None`: row released,
  stale, or pid gone).
  - Offline within the wait → reap a stale row, deactivate the address, return `('unregistered', None)`.
  - Still live after the wait → return `('tombstoned', pid)`, address left active (today's result).
- The bounded wait is the per-address loop that `_evict_project_notifiers` (archive / tombstone) already runs;
  it is shared, not duplicated.
- CLI `unregister` (human and machine mode) and MCP `sandesh_unregister` gain no new parameters; they inherit the
  wait. Their descriptions say a live watcher is waited for up to one poll interval, and that `tombstoned` means
  it outlived that wait.

**Surfaces (verified 2026-10-16):** `sandesh/sandesh_db.py` `unregister` (l.1023–1035), `notifier_live`
(l.929–941), `_evict_project_notifiers` (l.1070–), `poll_interval` (l.1040–); `sandesh/cli.py` `cmd_unregister`
(l.356–), `axi_unregister` (l.1128–1139); `sandesh/mcp_server.py` `sandesh_unregister` docstring (l.~532) and
instructions text (l.~784).

### §S2 — sandesh-pi `sandesh_unregister` stops this session's own watcher first (bun)

- When this registration's supervisor has a running watcher for `params.address`, `sandesh_unregister` stops it
  as a requested stop (`{ requested: true }`, quiet) before running the CLI. The CLI's bounded wait (§S1) then
  sees the dying watcher go offline and completes the unregister.
- An address this session does not watch is passed straight to the CLI, as today.

**Surfaces (verified 2026-10-16):** `integrations/pi/src/index.ts` `sandesh_unregister` (l.771–792),
`stopWatchers` (l.473–); `integrations/pi/src/wake.ts` `WakeSupervisor.stop` (l.157–192).

### §S3 — every sandesh-pi CLI verb call has a timeout (bun)

- `runSandeshText` passes `timeout: CLI_TIMEOUT_MS` to `pi.exec`. **PROPOSED — pending decision:**
  `CLI_TIMEOUT_MS = 120_000` (above the CLI's own worst case: one 30 s `busy_timeout` wait per statement, the
  `_retry_locked` backoff, and §S1's one-poll wait).
- A result with `killed: true` is a timeout regardless of its `code` (Pi reports a signal-killed child as `0`):
  the tool fails with an `Error` whose message names the verb and `timed out after <n>s` (the same failure path
  as a CLI crash).
- The wake watcher (`sandesh notify`, spawned by `WakeSupervisor`) is long-running by design and gets no timeout.
- Test seam: `index.ts` exports `__setCliTimeoutMs(ms)`; `resetExtensionState()` restores the default.

**Surfaces (verified 2026-10-16):** `integrations/pi/src/index.ts` `runSandeshText` (l.387–399), `runSandesh`
(l.407–414).

### §S4 — docs

- `CLAUDE.md` locked semantic #8 states the bounded wait: `unregister` of a live address tombstones, waits up to
  one poll interval for the watcher to exit, then soft-deletes; `tombstoned` only when the watcher outlives the
  wait.
- `docs/USER_GUIDE.md`, `sandesh/data/usage-scenarios.md` and `integrations/pi/README.md` (the
  `sandesh_unregister` row) describe the wait and the Pi tool stopping the session's own watcher; the
  recommended shutdown order stays "stop the watcher, then unregister".

## Acceptance criteria

All Python criteria run against a temp store (`XDG_DATA_HOME` in the process / subprocess env).

- [ ] **AC1** — library: a live notifier row whose holder releases it ~0.2 s after the tombstone is written
  (a real `sandesh notify` subprocess sent SIGTERM, or a thread deleting the row) → `unregister(...,
  wait_secs=2)` returns `('unregistered', None)`; the address is inactive; no notifier row remains.
- [ ] **AC2** — library: a live notifier that stays live → `unregister(..., wait_secs=0.3)` returns
  `('tombstoned', pid)` after ≥ 0.3 s and < 2 s; the address stays active; the notifier's tombstone flag is set.
- [ ] **AC3** — CLI integration (real processes): start `sandesh notify` for `Mainline - Demo`; send it SIGTERM
  and immediately run `sandesh --format toon unregister --address "Mainline - Demo" --as "Mainline - Demo"` →
  exit 0, `result: unregistered`, the address inactive. Repeated 5 times with 0–200 ms offsets, all pass.
- [ ] **AC4** — CLI integration: with `SANDESH_POLL_SECONDS=3`, a healthy running `sandesh notify` for the
  address → `unregister` returns `result: unregistered` within 8 s, and the notify process exits 3.
- [ ] **AC5** — Pi integration (production `registerExtension`, fake `pi`): with this session's watcher running
  for `Mainline - Demo`, `sandesh_unregister({address:"Mainline - Demo", requester:"Mainline - Demo"})` aborts
  the watcher's exec signal before the CLI `unregister` exec is called (recorded call order), posts no UI
  `notify`, leaves the watcher `running:false`, and returns the CLI envelope. For an address with no watcher in
  this session, no watcher is touched.
- [ ] **AC6** — Pi unit/integration: a verb tool (e.g. `sandesh_inbox`) calls `pi.exec` with
  `timeout === CLI_TIMEOUT_MS`; the wake watcher's `notify` exec has no `timeout`. A fake exec returning
  `{ killed: true, code: 0, stdout: "" }` makes the tool fail with a message containing the verb and
  `timed out`.
- [ ] **AC7** — docs pins: the Pi README `sandesh_unregister` row mentions stopping the session's own watcher;
  `docs/USER_GUIDE.md` and `usage-scenarios.md` mention the bounded wait. Full Python gate and full `bun test`
  green.

**VERIFY audit (not a test):** `unregister` and `_evict_project_notifiers` share one bounded-wait helper;
`runSandeshText` is the only verb exec path and always passes the timeout; `sandesh_unregister` calls the
supervisor's requested stop.

## Estimated size

Small — ~40 lines across `sandesh_db.py`, `index.ts`; ~10 tests; short doc edits.

## Risk

- `unregister` of a live, healthy watcher now blocks up to one poll interval (default 10 s) instead of returning
  at once; it then completes the unregister instead of leaving the caller to retry.
- Existing tests that unregister an address with a live notifier row would each wait the default interval;
  they pass `wait_secs` (library) or `SANDESH_POLL_SECONDS` (CLI/MCP).

## Non-goals

- The stall Model B observed before either tool ran (a Pi `beforeToolCall` hook); Pi's own `exec` defects
  (#7864) beyond reading `killed`; any change to the notify exit table or the wake supervisor's restart rules.
