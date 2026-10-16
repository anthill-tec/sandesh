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
  indefinitely. Pi's own `exec` reports a killed child as exit `0` with `killed: true` — whether it was killed by
  a timeout or by the caller's abort signal — and its SIGKILL fallback never fires (upstream
  earendil-works/pi#7864).

## Scope

### §S1 — `unregister` waits, bounded, for a live watcher to go offline (library + CLI + MCP)

- `sandesh_db.unregister(con, recipient, requester, project=None, *, wait_secs=None)`: when the recipient's
  notifier is live, it writes the tombstone flag (as today), then waits up to `wait_secs` for the notifier to go
  offline (`notifier_live()` returns `None`: row released, stale, or pid gone). The default is **two poll
  intervals**, `2 * poll_interval()` (owner ruling 2026-10-16) — the same default as the archive/tombstone
  eviction, because a healthy watcher re-checks only a full interval after its last check, so a one-interval
  wait can expire just before it exits.
  - Offline within the wait → reap a stale row, deactivate the address, return `('unregistered', None)`.
  - Still live after the wait → return `('tombstoned', pid)`, address left active (today's result).
- The bounded wait is the per-address loop that `_evict_project_notifiers` (archive / tombstone) already runs;
  `unregister` and `_evict_project_notifiers` share one helper for it, not two copies.
- CLI `unregister` (human and machine mode) and MCP `sandesh_unregister` gain no new parameters; they inherit the
  wait. Every text that tells a caller what `tombstoned` means says the wait already happened — a live watcher is
  waited for up to two poll intervals, and `tombstoned` means it outlived that wait — and none says "it stops
  within one poll; re-run once offline": the CLI human-mode message, the MCP tool docstring, and the MCP
  `unregister` prompt text.

**Surfaces (verified 2026-10-16):** `sandesh/sandesh_db.py` `unregister` (l.1023–1035), `notifier_live`
(l.929–941), `_evict_project_notifiers` (l.1070–), `poll_interval` (l.1040–); `sandesh/cli.py` `_print_unregister`
(l.347–353, the "re-run once `addressbook` shows it offline" message), `cmd_unregister` (l.356–),
`axi_unregister` (l.1128–1139); `sandesh/mcp_server.py` `sandesh_unregister` docstring (l.~532) and the
`unregister` prompt text (l.~779–787).

### §S2 — sandesh-pi `sandesh_unregister` stops this session's own watcher first (bun)

- When this registration's supervisor has a running watcher for `params.address`, `sandesh_unregister` stops it
  as a requested stop (`{ requested: true }`, quiet) before running the CLI. The CLI's bounded wait (§S1) then
  sees the dying watcher go offline and completes the unregister.
- An address this session does not watch is passed straight to the CLI, as today.

**Surfaces (verified 2026-10-16):** `integrations/pi/src/index.ts` `sandesh_unregister` (l.771–792),
`stopWatchers` (l.473–); `integrations/pi/src/wake.ts` `WakeSupervisor.stop` (l.157–192).

### §S3 — every sandesh-pi CLI call has a timeout, except the wake watcher (bun)

- `CLI_TIMEOUT_MS = 240_000` (owner ruling 2026-10-16). It sits above the CLI's own documented worst case: a
  single locked write can retry `LOCK_RETRY_ATTEMPTS` (6) times, each blocking up to `BUSY_TIMEOUT_MS` (30 s) —
  180 s — plus §S1's wait of two poll intervals (20 s at the default).
- Every `pi.exec` of the `sandesh` CLI passes `timeout: CLI_TIMEOUT_MS`: the verb path `runSandeshText`, and the
  session-start calls — the `--version` probes and `init --check` (owner ruling 2026-10-16: a stuck CLI must not
  hang session start either).
- A result with `killed: true` was killed by Pi; its `code` is meaningless (Pi reports a signal-killed child as
  `0`). Which kill it was is told apart by the abort signal passed in:
  - **the caller's signal is aborted** (the turn or tool call was cancelled) → a verb tool fails with an `Error`
    whose message names the verb and says it was `aborted` — never "timed out";
  - **the caller's signal is not aborted** (or none was passed) → the timeout fired: a verb tool fails with an
    `Error` whose message names the verb and `timed out after <n>s` (the same failure path as a CLI crash); a
    timed-out `--version` probe counts as CLI-unreachable; a timed-out `init --check` counts as a failed check
    (the provision nudge fires), never as provisioned.
- The wake watcher (`sandesh notify`, spawned by `WakeSupervisor`) is long-running by design and gets no timeout.
- Test seam: `index.ts` exports `__setCliTimeoutMs(ms)`; `resetExtensionState()` restores the default.

**Surfaces (verified 2026-10-16):** `integrations/pi/src/index.ts` `runSandeshText` (l.387–399), `runSandesh`
(l.407–414), the `--version` probes (l.227, l.237), `init --check` (l.1356–1362, which today treats any
`code === 0` as passed).

### §S4 — docs

- `CLAUDE.md` locked semantic #8 states the bounded wait: `unregister` of a live address tombstones, waits up to
  two poll intervals for the watcher to exit, then soft-deletes; `tombstoned` only when the watcher outlives the
  wait.
- `docs/USER_GUIDE.md`, `sandesh/data/usage-scenarios.md` and `integrations/pi/README.md` (the
  `sandesh_unregister` row) describe the wait and the Pi tool stopping the session's own watcher; the
  recommended shutdown order stays "stop the watcher, then unregister".

## Acceptance criteria

All Python criteria run against a temp store (`XDG_DATA_HOME` in the process / subprocess env).

- [ ] **AC1** — library: a live notifier row whose holder releases it ~0.2 s after the tombstone is written
  (a real `sandesh notify` subprocess sent SIGTERM, or a thread deleting the row) → `unregister(...,
  wait_secs=2)` returns `('unregistered', None)`; the address is inactive; no notifier row remains. With
  `wait_secs` omitted and `SANDESH_POLL_SECONDS=3`, a notifier that never goes offline makes `unregister` wait
  ≥ 6 s before returning `('tombstoned', pid)` (the default is two poll intervals).
- [ ] **AC2** — library: a live notifier that stays live → `unregister(..., wait_secs=0.3)` returns
  `('tombstoned', pid)` after ≥ 0.3 s and < 2 s; the address stays active; the notifier's tombstone flag is set.
- [ ] **AC3** — CLI integration (real processes): start `sandesh notify` for `Mainline - Demo` and wait until the
  address is listening (`addressbook --format toon --fields address,listening` shows `listening: true`); then
  send it SIGTERM and run `sandesh --format toon unregister --address "Mainline - Demo" --as "Mainline - Demo"`
  0–200 ms later → exit 0, `result: unregistered`, the address inactive. Repeated 5 times (re-registering the
  address between runs), each confirming the watcher was listening before the SIGTERM; all pass.
- [ ] **AC4** — CLI integration: with `SANDESH_POLL_SECONDS=3`, a healthy running `sandesh notify` for the
  address, confirmed listening → `unregister` returns `result: unregistered` within 8 s, and the notify process
  exits 3.
- [ ] **AC5** — Pi integration (production `registerExtension`, fake `pi`): with this session's watcher running
  for `Mainline - Demo`, `sandesh_unregister({address:"Mainline - Demo", requester:"Mainline - Demo"})` aborts
  the watcher's exec signal before the CLI `unregister` exec is called (recorded call order), posts no UI
  `notify`, leaves the watcher `running:false`, and returns the CLI envelope. For an address with no watcher in
  this session, no watcher is touched.
- [ ] **AC6** — Pi integration (production `registerExtension`, fake `pi`):
  - a verb tool (e.g. `sandesh_inbox`) calls `pi.exec` with `timeout === CLI_TIMEOUT_MS` (240 000);
  - the session-start `--version` probe and `init --check` calls carry the same `timeout`;
  - the wake watcher's `notify` exec has no `timeout`;
  - a fake exec returning `{ killed: true, code: 0, stdout: "" }` for a verb, with the tool's signal **not**
    aborted → the tool fails with a message containing the verb and `timed out`;
  - the same fake exec with the tool's signal **aborted** → the tool fails with a message containing the verb
    and `aborted`, and not containing `timed out`;
  - `{ killed: true, code: 0 }` for the `--version` probe → the CLI-unreachable path; for `init --check` → the
    provision nudge fires (a failed check), not the provisioned path.
- [ ] **AC7** — docs pins: the Pi README `sandesh_unregister` row mentions stopping the session's own watcher;
  `docs/USER_GUIDE.md` and `usage-scenarios.md` mention the bounded wait; the MCP `sandesh_unregister`
  docstring, the MCP `unregister` prompt text and the CLI human-mode `tombstoned` message describe the wait as
  already done (none contains "re-run once"); `CLAUDE.md` locked semantic #8 states the wait. Full Python gate
  and full `bun test` green.

**Approved existing-test edits:** `tests/test_sandesh.py` (l.~218), `tests/test_axi_verbs.py`
(`test_unregister_live_notifier_ok_true_result_tombstoned_exit_3_unchanged`, l.~633) and
`tests/test_mcp_mutating_tools.py` (`test_unregister_with_live_notifier_returns_tombstoned`, l.~614) hold their
notifier with the test's own pid, so it never goes offline and `tombstoned` stays the answer. Each bounds the new
wait (`wait_secs` in the library call; `SANDESH_POLL_SECONDS=3` for the CLI and MCP paths) and keeps its
assertions. A test that asserts the old human-mode `tombstoned` message text is updated to the new wording.

**VERIFY audit (not a test):** `unregister` and `_evict_project_notifiers` share one bounded-wait helper; every
`pi.exec` of the CLI except the wake watcher passes the timeout; `sandesh_unregister` calls the supervisor's
requested stop.

## Estimated size

Small — ~50 lines across `sandesh_db.py`, `cli.py`, `mcp_server.py`, `index.ts`; ~12 tests; short doc edits.

## Risk

- `unregister` of a live, healthy watcher now blocks up to two poll intervals (default 20 s) instead of returning
  at once; it then completes the unregister instead of leaving the caller to retry.
- Existing tests that unregister an address with a live notifier row would each wait the default; they pass
  `wait_secs` (library) or `SANDESH_POLL_SECONDS` (CLI/MCP).
- A stuck CLI call now fails after 4 minutes instead of never.

## Non-goals

- The stall Model B observed before either tool ran (a Pi `beforeToolCall` hook); Pi's own `exec` defects
  (#7864) beyond reading `killed` and the caller's abort signal; any change to the notify exit table or the wake
  supervisor's restart rules.
