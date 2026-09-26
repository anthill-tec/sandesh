"""notify.py — Sandesh's per-session mailbox watcher.

Blocks (no agent turns) until the watched address has unread **'to'** mail, then
returns its message ids. An orchestrator launches it in the background (via its
host's background-task mechanism — the only thing that re-invokes the agent on
completion) and, on wake, calls `sandesh fetch`.

One per session, per address. Self-dedups via the `notifier` liveness table.
Registers alive on start, heartbeats each poll, removes its row on clean exit; a
SIGKILL/crash leaves a stale row the next start reaps. Cooperative eviction: a
`tombstone` flag (set by Mainline or a self-unregister) is seen on the next poll
and the watcher shuts itself down. Cc mail does NOT wake it (only role='to').

Poll interval: $SANDESH_POLL_SECONDS (default 10, floor 3).

EXIT CODES
  0  unread 'to' mail — ids printed (resume → fetch)
  2  timed out
  3  tombstoned (evicted by Mainline / self) — do NOT relaunch
  4  evicted (another notifier took the address over)
  5  dedup — another notifier already live for this address (did not start)
  1  usage / config error
  128+N  terminated by signal N (SIGTERM → 143, SIGINT → 130)

MACHINE MODE (CR-SAN-047 §S5): `run(..., fmt="toon"|"json")` sends every progress
line to stderr and writes exactly ONE final AXI envelope
`{verb:"notify", ok, exit, address, project, unread[N]}` to stdout on every exit
path above — including the signal handlers — guarded so atexit/signal paths cannot
double-emit. `ok` is true for 0/2/5, false for 1/3/4/signal (with `error`).
`fmt="human"` (the default; what `main()` uses) is byte-identical to before.
"""

import argparse
import atexit
import os
import signal
import socket
import sqlite3
import sys
import time
import uuid

from sandesh import axi
from sandesh import sandesh_db as sdb

DEFAULT_TIMEOUT_SECS = 14400  # 4h

_OK_EXITS = (0, 2, 5)  # mail / timeout / dedup are normal outcomes → ok:true


def _say(msg, machine):
    """One progress/outcome line: stdout in human mode, stderr in machine mode
    (machine stdout is reserved for the single final envelope)."""
    print(msg, file=sys.stderr if machine else sys.stdout)


def _finish(fmt, out, project_id, address, code, unread=None, error=None, warnings=None):
    """Write the final notify envelope for `code` to `out` (the stdout current
    when `run()` was entered) and flush it. Human mode writes nothing. The
    caller's `done` closure holds the once-only guard."""
    if fmt == "human":
        return
    fields = {"exit": code, "address": address, "project": project_id,
              "unread": sorted(unread or [])}
    if error is not None:
        fields["error"] = error
    env = axi.Envelope("notify", code in _OK_EXITS, fields,
                       context={"project": project_id, "address": address},
                       warnings=warnings)
    axi.emit(env, fmt, out)
    out.flush()


def run(project_id, address, timeout=DEFAULT_TIMEOUT_SECS, fmt="human"):
    """Block until `address` has unread 'to' mail in `project_id`. Returns an exit code.

    `fmt` is `human` (default) or `toon`/`json` — see MACHINE MODE above.
    """
    machine = fmt != "human"
    out = sys.stdout  # captured at entry: the envelope's destination even if stdout is redirected later
    emitted = [False]  # once-only guard shared by the return paths and the signal handlers

    def say(msg):
        _say(msg, machine)

    def done(code, **kw):
        if not emitted[0]:
            emitted[0] = True
            _finish(fmt, out, project_id, address, code, **kw)
        return code

    def on_signal(signum, _frame):
        code = 128 + signum
        done(code, error=f"terminated by {signal.Signals(signum).name} ({code})")
        sys.exit(code)  # atexit (notifier_release) still runs after the envelope

    con = sdb.connect()
    try:
        sdb.validate_address(address, project_id)
    except ValueError as exc:
        sys.stderr.write(f"[notify] ERROR: {exc}\n")
        return done(1, error=str(exc))
    if not sdb.is_active(con, address):
        error = (f"{address!r} is not registered in {project_id!r} — "
                 f"`sandesh register --project {project_id} --address {address!r}` first.")
        sys.stderr.write(f"[notify] ERROR: {error}\n")
        return done(1, error=error)

    token, pid, host = uuid.uuid4().hex, os.getpid(), socket.gethostname()
    interval = sdb.poll_interval()
    deadline = time.monotonic() + timeout

    # CR-SAN-043: be resilient to transient DB lock contention. A 'database is locked'
    # (SQLITE_BUSY surviving PRAGMA busy_timeout under heavy co-tenant load) is retried on
    # the poll cadence — bounded by the watch deadline — instead of crashing the watcher
    # (exit 1) and flapping `listening`. Non-lock errors still propagate unchanged.
    while True:
        try:
            ok, reason = sdb.notifier_acquire(con, address, pid, token, host)
            break
        except sqlite3.OperationalError as exc:
            if not sdb.is_locked_error(exc):
                raise
            if time.monotonic() >= deadline:
                say("[notify] timed out waiting for the DB write lock to acquire.")
                return done(2)
            say(f"[notify] DB busy ({exc}); staying up, retrying acquire in {interval}s")
            time.sleep(interval)
    if not ok:
        say(f"[notify] {reason} — not starting (dedup).")
        return done(5, warnings=[reason])

    atexit.register(lambda: sdb.notifier_release(con, address, token))  # token-guarded
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    say(f"[notify] watching {address} in {project_id}  (pid {pid}, interval {interval}s, timeout {timeout}s)")
    polls = 0
    while True:
        try:
            state = sdb.notifier_check(con, address, token)
            if state == "tombstoned":
                error = "tombstoned — shutting down (evicted)."
                say(f"[notify] {error}")
                return done(3, error=error)
            if state == "evicted":
                error = f"evicted — another notifier took over {address!r}."
                say(f"[notify] {error}")
                return done(4, error=error)
            sdb.notifier_heartbeat(con, address, token)
            ids = sdb.unread_to(con, address)
        except sqlite3.OperationalError as exc:
            if not sdb.is_locked_error(exc):
                raise
            if time.monotonic() >= deadline:
                say(f"[notify] {time.strftime('%H:%M:%S')} timed out (DB busy, {polls} polls).")
                return done(2)
            say(f"[notify] DB busy ({exc}); staying up, recheck in {interval}s")
            time.sleep(interval)
            continue

        polls += 1
        stamp = time.strftime("%H:%M:%S")
        if ids:
            say(f"[notify] {stamp} ✉ {len(ids)} unread 'to' message(s): {ids}")
            say(f"[notify] WAKE — fetch with: sandesh fetch --project {project_id} --to {address!r}")
            return done(0, unread=ids)
        if time.monotonic() >= deadline:
            say(f"[notify] {stamp} timed out ({polls} polls).")
            return done(2)
        say(f"[notify] {stamp} no 'to' mail — next check in {interval}s")
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Block until Sandesh 'to' mail arrives for an address.")
    ap.add_argument("--project", required=False, help="project id (or $SANDESH_PROJECT)")
    ap.add_argument("--to", required=True, metavar="ADDRESS")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECS)
    args = ap.parse_args(argv)
    project = args.project or os.environ.get("SANDESH_PROJECT")
    if not project:
        sys.exit("[notify] ERROR: pass --project <id> (or set $SANDESH_PROJECT).")
    return run(project, args.to, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
