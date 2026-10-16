"""test_unregister_wait.py — RED tests for CR-SAN-055 cycle 250 §S1, AC1-AC4:
`unregister` waits, bounded, for a live watcher to go offline.

docs/changes/CR-SAN-055-unregister-stopping-watcher-and-cli-timeout.md §S1, AC1-AC4

Target contract: `sandesh_db.unregister(con, recipient, requester, project=None, *,
wait_secs=None)`. Today `unregister()` takes NO `wait_secs` keyword at all — every
call below that passes it raises `TypeError` immediately (a valid RED: the kwarg
doesn't exist yet). With `wait_secs` omitted, the library default (per the spec) is
`2 * poll_interval()`; today's `unregister()` does not wait at all when the kwarg
is omitted either, so the "default wait" test below also RED-fails (wrong timing,
not a harness error).

AC3's race ("a watcher that was just sent SIGTERM is still 'live' ... for the few
milliseconds before its atexit releases the row") does not reproduce naturally over
a brand-new CLI subprocess round-trip on this machine (Python interpreter start-up
for the second `sandesh` process takes longer than the watcher's SIGTERM-handler +
atexit, so by the time `unregister` actually checks, the watcher is already gone —
confirmed empirically: 0/10 in a timing probe). To exercise the EXACT race window
the AC describes — and get a deterministic RED — the test freezes the real watcher
with SIGSTOP right after it is confirmed listening, THEN sends the real SIGTERM (so
it is queued, not yet handled) and runs the real `unregister` CLI immediately (the
"0ms" edge of the AC's "0-200ms" band): at that instant the row is still live
(fresh heartbeat, pid alive, process merely stopped) — exactly AC3's race. A
background timer sends SIGCONT ~0.3s later so the watcher can process the queued
SIGTERM and exit, well inside any wait budget a correct GREEN would use (the
library default is `2 * poll_interval()` >= 6s). Real `sandesh notify` / `sandesh
unregister` processes and real signals throughout — no library internals mocked.

Run targeted (Crucible client resolves the venv interpreter):
  PYTHONPATH=. .venv/bin/python tests/test_unregister_wait.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from sandesh import _toon
from sandesh import sandesh_db as sdb

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

PROJ = "Demo"
MAINLINE = "Mainline - Demo"
TRACK1 = "Track 1 - Demo"


def _build_env(tmp_xdg, extra=None):
    env = {**os.environ}
    env["XDG_DATA_HOME"] = tmp_xdg
    env["PYTHONPATH"] = _REPO_ROOT
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# AC1/AC2 — library: unregister(..., wait_secs=...) bounded wait semantics.
# --------------------------------------------------------------------------- #

class UnregisterLibraryWaitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-unregister-wait-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_poll = os.environ.get("SANDESH_POLL_SECONDS")
        os.environ["XDG_DATA_HOME"] = self.tmp
        sdb.setup(PROJ)
        self.con = sdb.connect()
        sdb.register(self.con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(self.con, TRACK1, kind="track", project=PROJ)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        if self._prev_poll is None:
            os.environ.pop("SANDESH_POLL_SECONDS", None)
        else:
            os.environ["SANDESH_POLL_SECONDS"] = self._prev_poll
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_notifier_released_during_wait_returns_unregistered(self):
        """AC1: a live notifier whose holder releases it ~0.2s after the tombstone
        is written (a background thread deleting the row, simulating the watcher's
        atexit) -> unregister(..., wait_secs=2) returns ('unregistered', None); the
        address goes inactive; no notifier row remains."""
        sdb.notifier_acquire(self.con, TRACK1, os.getpid(), "tok-a", "host")

        def _release_after_delay():
            time.sleep(0.2)
            con2 = sdb.connect()
            try:
                sdb.notifier_release(con2, TRACK1, "tok-a")
            finally:
                con2.close()

        t = threading.Thread(target=_release_after_delay)
        t.start()
        try:
            verdict, pid = sdb.unregister(self.con, TRACK1, requester=MAINLINE,
                                           project=PROJ, wait_secs=2)
        finally:
            t.join(timeout=5)

        self.assertEqual(verdict, "unregistered")
        self.assertIsNone(pid)
        self.assertFalse(sdb.is_active(self.con, TRACK1))
        self.assertIsNone(sdb.notifier_live(self.con, TRACK1))
        row = self.con.execute(
            "SELECT * FROM notifier WHERE recipient=?", (TRACK1,)).fetchone()
        self.assertIsNone(row, "no notifier row must remain after a clean release")

    def test_default_wait_is_two_poll_intervals_with_poll_seconds_3(self):
        """AC1: wait_secs omitted + SANDESH_POLL_SECONDS=3 -> a notifier that
        never goes offline (held by this test process's own pid) makes
        unregister wait >= 6s (2 * poll_interval) before returning
        ('tombstoned', pid) — the default is two poll intervals."""
        os.environ["SANDESH_POLL_SECONDS"] = "3"
        sdb.notifier_acquire(self.con, TRACK1, os.getpid(), "tok-b", "host")

        start = time.monotonic()
        verdict, pid = sdb.unregister(self.con, TRACK1, requester=MAINLINE, project=PROJ)
        elapsed = time.monotonic() - start

        self.assertEqual(verdict, "tombstoned")
        self.assertEqual(pid, os.getpid())
        self.assertGreaterEqual(
            elapsed, 6.0,
            f"default wait must be >= 2 * poll_interval (6s with SANDESH_POLL_SECONDS=3); "
            f"got {elapsed}s")
        self.assertTrue(sdb.is_active(self.con, TRACK1))

    def test_live_notifier_that_stays_live_returns_tombstoned_after_bounded_wait(self):
        """AC2: a live notifier that stays live -> unregister(..., wait_secs=0.3)
        returns ('tombstoned', pid) after >= 0.3s and < 2s; the address stays
        active; the notifier's tombstone flag is set."""
        sdb.notifier_acquire(self.con, TRACK1, os.getpid(), "tok-c", "host")

        start = time.monotonic()
        verdict, pid = sdb.unregister(self.con, TRACK1, requester=MAINLINE,
                                       project=PROJ, wait_secs=0.3)
        elapsed = time.monotonic() - start

        self.assertEqual(verdict, "tombstoned")
        self.assertEqual(pid, os.getpid())
        self.assertGreaterEqual(elapsed, 0.3,
                                 f"must wait at least wait_secs (0.3s); got {elapsed}s")
        self.assertLess(elapsed, 2.0,
                         f"must not wait past wait_secs + a small margin; got {elapsed}s")
        self.assertTrue(sdb.is_active(self.con, TRACK1))
        row = self.con.execute(
            "SELECT tombstone FROM notifier WHERE recipient=?", (TRACK1,)).fetchone()
        self.assertEqual(row["tombstone"], 1, "the notifier's tombstone flag must be set")


# --------------------------------------------------------------------------- #
# AC3/AC4 — CLI integration: real `sandesh notify` + `sandesh unregister`
# subprocesses, real signals.
# --------------------------------------------------------------------------- #

class UnregisterCliIntegrationTest(unittest.TestCase):
    PROJ = "Demo"
    ADDR = "Mainline - Demo"

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(_SUBPROCESS_PYTHON):
            raise unittest.SkipTest(f"subprocess python not found at {_SUBPROCESS_PYTHON!r}")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-unregister-cli-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        sdb.setup(self.PROJ)
        self._register()
        self.env = _build_env(self.tmp)
        self._procs = []

    def tearDown(self):
        for p in self._procs:
            if p.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    p.send_signal(signal.SIGCONT)  # in case it was left SIGSTOPped
                p.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(timeout=5)
        self._procs.clear()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _register(self):
        con = sdb.connect()
        try:
            sdb.register(con, self.ADDR, kind="mainline", project=self.PROJ)
        finally:
            con.close()

    def _start_notify(self, env=None):
        proc = subprocess.Popen(
            [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "notify", "--project", self.PROJ,
             "--to", self.ADDR, "--timeout", "60"],
            env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._procs.append(proc)
        return proc

    def _reap(self, proc, timeout=10):
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if proc in self._procs:
            self._procs.remove(proc)

    def _wait_listening(self, timeout=10, env=None):
        """Poll via the real `addressbook` CLI (AC3's literal confirmation path)
        until `listening: true` for self.ADDR, or raise."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            proc = subprocess.run(
                [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "--format", "toon",
                 "addressbook", "--project", self.PROJ, "--fields", "address,listening"],
                env=env or self.env, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                axi = _toon.decode(proc.stdout)["axi"]
                rows = {r["address"]: r["listening"] for r in axi.get("participants", [])}
                if rows.get(self.ADDR) is True:
                    return
            time.sleep(0.2)
        raise AssertionError(f"{self.ADDR!r} never showed listening:true via addressbook")

    def _run_unregister(self, env=None, timeout=25):
        return subprocess.run(
            [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "--format", "toon",
             "unregister", "--project", self.PROJ, "--address", self.ADDR,
             "--as", self.ADDR],
            env=env or self.env, capture_output=True, text=True, timeout=timeout,
        )

    def _send_after(self, proc, sig, delay):
        def _go():
            time.sleep(delay)
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)
        t = threading.Thread(target=_go, daemon=True)
        t.start()
        return t

    def test_sigterm_then_quick_unregister_succeeds_across_five_runs(self):
        """AC3: start `sandesh notify`, confirm listening via the real addressbook
        CLI, SIGTERM it, run the real `unregister` CLI 0-200ms later -> exit 0,
        result:unregistered, address inactive. Repeated 5 times (re-registering the
        address between runs), each confirming the watcher was listening before the
        SIGTERM; all pass.

        The exact few-ms race window is forced deterministically with SIGSTOP (see
        module docstring) so the test does not depend on this machine's process
        start-up speed; the real SIGTERM is still sent and the real `unregister`
        CLI still runs immediately after (the "0ms" edge of the AC's band)."""
        for i in range(5):
            if i > 0:
                self._register()
            proc = self._start_notify()
            self._wait_listening(timeout=10)

            proc.send_signal(signal.SIGSTOP)   # freeze: pid alive, heartbeat fresh
            proc.send_signal(signal.SIGTERM)   # queued, not yet handled while stopped
            resume_thread = self._send_after(proc, signal.SIGCONT, 0.3)
            try:
                result = self._run_unregister()
            finally:
                resume_thread.join(timeout=5)

            self.assertEqual(result.returncode, 0,
                              f"run {i}: out={result.stdout!r} err={result.stderr!r}")
            axi = _toon.decode(result.stdout)["axi"]
            self.assertIs(axi["ok"], True, f"run {i}: axi={axi!r}")
            self.assertEqual(axi["result"], "unregistered", f"run {i}: axi={axi!r}")

            con = sdb.connect()
            try:
                self.assertFalse(sdb.is_active(con, self.ADDR),
                                  f"run {i}: address must be inactive")
            finally:
                con.close()

            self._reap(proc, timeout=10)

    def test_healthy_watcher_tombstone_wait_unregisters_and_watcher_exits_3(self):
        """AC4: with SANDESH_POLL_SECONDS=3, a healthy running `sandesh notify`
        (confirmed listening, never signalled) -> `unregister` returns
        result:unregistered within 8s, and the notify process exits 3 (its own
        cooperative-tombstone self-termination)."""
        env = _build_env(self.tmp, {"SANDESH_POLL_SECONDS": "3"})
        proc = self._start_notify(env=env)
        self._wait_listening(timeout=10, env=env)

        start = time.monotonic()
        result = self._run_unregister(env=env, timeout=15)
        elapsed = time.monotonic() - start

        self.assertEqual(result.returncode, 0, f"out={result.stdout!r} err={result.stderr!r}")
        axi = _toon.decode(result.stdout)["axi"]
        self.assertIs(axi["ok"], True, f"axi={axi!r}")
        self.assertEqual(axi["result"], "unregistered", f"axi={axi!r}")
        self.assertLess(elapsed, 8.0, f"unregister must complete within 8s; took {elapsed}s")

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            self.fail("notify watcher did not exit after the cooperative tombstone")
        if proc in self._procs:
            self._procs.remove(proc)
        self.assertEqual(
            proc.returncode, 3,
            "the healthy watcher must self-terminate with exit 3 (tombstoned), "
            f"got {proc.returncode}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
