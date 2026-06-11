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
"""

import argparse
import atexit
import os
import signal
import socket
import sys
import time
import uuid

from sandesh import sandesh_db as sdb

DEFAULT_TIMEOUT_SECS = 14400  # 4h


def run(project_id, address, timeout=DEFAULT_TIMEOUT_SECS):
    """Block until `address` has unread 'to' mail in `project_id`. Returns an exit code."""
    con = sdb.connect()
    try:
        sdb.validate_address(address, project_id)
    except ValueError as exc:
        sys.stderr.write(f"[notify] ERROR: {exc}\n")
        return 1
    if not sdb.is_active(con, address):
        sys.stderr.write(f"[notify] ERROR: {address!r} is not registered in {project_id!r} — "
                         f"`sandesh register --project {project_id} --address {address!r}` first.\n")
        return 1

    token, pid, host = uuid.uuid4().hex, os.getpid(), socket.gethostname()
    ok, reason = sdb.notifier_acquire(con, address, pid, token, host)
    if not ok:
        print(f"[notify] {reason} — not starting (dedup).")
        return 5

    atexit.register(lambda: sdb.notifier_release(con, address, token))  # token-guarded
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(128 + signal.SIGINT))

    interval = sdb.poll_interval()
    print(f"[notify] watching {address} in {project_id}  (pid {pid}, interval {interval}s, timeout {timeout}s)")
    deadline = time.monotonic() + timeout
    polls = 0
    while True:
        state = sdb.notifier_check(con, address, token)
        if state == "tombstoned":
            print("[notify] tombstoned — shutting down (evicted).")
            return 3
        if state == "evicted":
            print(f"[notify] evicted — another notifier took over {address!r}.")
            return 4
        sdb.notifier_heartbeat(con, address, token)

        ids = sdb.unread_to(con, address)
        polls += 1
        stamp = time.strftime("%H:%M:%S")
        if ids:
            print(f"[notify] {stamp} ✉ {len(ids)} unread 'to' message(s): {ids}")
            print(f"[notify] WAKE — fetch with: sandesh fetch --project {project_id} --to {address!r}")
            return 0
        if time.monotonic() >= deadline:
            print(f"[notify] {stamp} timed out ({polls} polls).")
            return 2
        print(f"[notify] {stamp} no 'to' mail — next check in {interval}s")
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
