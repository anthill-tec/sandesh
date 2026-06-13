"""test_lifecycle_e2e.py — E2E capstone for CR-SAN-024 Cycle 5.

AC10: Temp XDG_DATA_HOME, real subprocess watcher, cross-project wake (exit 0),
archive cooperative eviction (exit 3).

Scenario:
  1. Temp XDG_DATA_HOME; SANDESH_POLL_SECONDS=3 (floor) for fast polls.
     Drive CLI as real subprocesses: sys.executable -m sandesh.cli ... with
     PYTHONPATH=<repo> and the temp env.
  2. Setup P1 + P2; register Mainline - P1 (P1) and Mainline - P2 (P2);
     assign admin via the library; grant P2 xproj access.
  3. Popen the watcher:
       ... -m sandesh.cli notify --project P1 --to 'Mainline - P1' --timeout 60
     Poll (≤10s, 0.2s steps) until the notifier row appears (acquired).
  4. Cross-project send P2→P1 via subprocess.
     Poll (≤15s) for the watcher to EXIT; assert returncode 0 and stdout
     contains the wake marker.
  5. Relaunch the watcher; poll until re-acquired.
  6. archive --project P1 --by 'Mainline - P1' --force via subprocess.
     Assert exit 0. Poll (≤15s) for the watcher to exit; assert returncode 3.
  7. Teardown: kill any still-running Popen; dump watcher stdout on any
     timeout failure.

Exit codes:
  0  unread 'to' mail (wake)
  3  tombstoned/evicted (cooperative eviction)

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_lifecycle_e2e --agent red-cr024-c5
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# Repo root — resolve from this file's location so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")

# The interpreter to use for subprocesses: prefer the venv python (has
# the package installed editable); fall back to the current interpreter
# if the venv is absent (CI may build differently).
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

# Add the repo to sys.path so the in-process library calls (sandesh_db, etc.)
# can be imported without a venv install.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as sdb


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------

def _poll_until(condition_fn, *, timeout_secs, step_secs=0.2,
                desc="condition", timeout_detail_fn=None):
    """Busy-poll `condition_fn()` until it returns truthy or `timeout_secs` elapses.

    Raises AssertionError on timeout with a descriptive message that includes
    any additional detail from `timeout_detail_fn()` (called at timeout time).
    """
    deadline = time.monotonic() + timeout_secs
    while True:
        if condition_fn():
            return
        if time.monotonic() >= deadline:
            detail = ""
            if timeout_detail_fn is not None:
                try:
                    detail = "\n" + str(timeout_detail_fn())
                except Exception as exc:  # noqa: BLE001
                    detail = f"\n(detail collection failed: {exc})"
            raise AssertionError(
                f"Timed out after {timeout_secs}s waiting for: {desc}{detail}"
            )
        time.sleep(step_secs)



def _proc_exited(proc):
    """True iff `proc` has exited (poll() is not None)."""
    return proc.poll() is not None


# ---------------------------------------------------------------------------
# CLI subprocess helper
# ---------------------------------------------------------------------------

def _run_cli(argv, *, env, timeout=30, capture=True):
    """Run [_SUBPROCESS_PYTHON, -m, sandesh.cli, *argv] with `env`.

    Returns a CompletedProcess. `capture=True` captures stdout/stderr.
    """
    cmd = [_SUBPROCESS_PYTHON, "-m", "sandesh.cli"] + list(argv)
    return subprocess.run(
        cmd,
        env=env,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _build_env(tmp_xdg, extra=None):
    """Subprocess environment: os.environ + PYTHONPATH=repo + XDG_DATA_HOME=tmp.

    Passes through the full parent env (needed for locale, home dir, etc.)
    but overrides XDG_DATA_HOME so subprocesses write to the temp store, and
    sets PYTHONPATH so `sandesh.cli` imports from the repo.
    """
    env = {**os.environ}
    env["XDG_DATA_HOME"] = tmp_xdg
    env["PYTHONPATH"] = _REPO_ROOT
    env["SANDESH_POLL_SECONDS"] = "3"  # floor interval → fast polls
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# E2E test class
# ---------------------------------------------------------------------------

class LifecycleE2ECapstoneTest(unittest.TestCase):
    """AC10 — E2E capstone: real-subprocess watcher, cross-project wake + archive eviction.

    EXPECTED TO PASS: all C1–C4 production code is shipped; this capstone
    verifies the integrated behaviour end-to-end.
    """

    P1 = "P1"
    P2 = "P2"
    ML_P1 = "Mainline - P1"
    ML_P2 = "Mainline - P2"
    ADMIN = "ops"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-e2e-lifecycle-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        # Override XDG_DATA_HOME for this process too (library calls in-process).
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ["SANDESH_POLL_SECONDS"] = "3"

        self._watchers = []  # running Popen handles — killed in tearDown guard
        self.env = _build_env(self.tmp)

    def tearDown(self):
        # Safety net: kill any still-running watcher processes.
        for proc in self._watchers:
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # best-effort
        self._watchers.clear()

        # Restore env.
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        os.environ.pop("SANDESH_POLL_SECONDS", None)

        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Internal setup helpers
    # ------------------------------------------------------------------

    def _provision(self):
        """Setup P1 + P2, register their Mainlines, assign admin, grant P2."""
        sdb.setup(self.P1)
        sdb.setup(self.P2)
        con = sdb.connect()
        try:
            sdb.register(con, self.ML_P1, kind="mainline", project=self.P1)
            sdb.register(con, self.ML_P2, kind="mainline", project=self.P2)
            sdb.assign_admin(con, self.ADMIN)
            sdb.grant_xproj(con, self.P2, self.ADMIN)
        finally:
            con.close()

    def _launch_watcher(self):
        """Popen a watcher for ML_P1 in P1 (timeout 60s, SANDESH_POLL_SECONDS=3).

        Returns the Popen handle; appends it to self._watchers for tearDown safety.
        """
        cmd = [
            _SUBPROCESS_PYTHON, "-m", "sandesh.cli",
            "notify",
            "--project", self.P1,
            "--to", self.ML_P1,
            "--timeout", "60",
        ]
        proc = subprocess.Popen(
            cmd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._watchers.append(proc)
        return proc

    def _watcher_stdout(self, proc):
        """Read all available watcher stdout without blocking (proc must have exited)."""
        try:
            return proc.stdout.read() if proc.stdout else ""
        except Exception:  # noqa: BLE001
            return "(stdout unreadable)"

    def _poll_acquired(self, timeout=10):
        """Poll until ML_P1's notifier row is live (acquired). Hard-fails on timeout.

        Opens a fresh DB connection per tick: the watcher is a separate process
        writing via WAL, so a single long-lived connection would not see its writes
        until it checkpoints. A fresh connect() always reads the latest committed
        state.
        """
        def _check():
            c = sdb.connect()
            try:
                return sdb.notifier_live(c, self.ML_P1) is not None
            finally:
                c.close()

        _poll_until(
            _check,
            timeout_secs=timeout,
            desc=f"notifier row for {self.ML_P1!r} to become live (acquired)",
        )

    def _poll_exited(self, proc, timeout=15):
        """Poll until `proc` has exited. On timeout, dump stdout and hard-fail."""
        def _detail():
            # Drain non-blocking stdout for diagnostics.
            if proc.stdout:
                import select
                rlist, _, _ = select.select([proc.stdout], [], [], 0)
                if rlist:
                    return "watcher stdout so far:\n" + proc.stdout.read(4096)
            return "(no stdout available)"

        _poll_until(
            lambda: proc.poll() is not None,
            timeout_secs=timeout,
            desc="watcher process to exit",
            timeout_detail_fn=_detail,
        )

    # ------------------------------------------------------------------
    # The capstone test
    # ------------------------------------------------------------------

    def test_ac10_cross_project_wake_then_archive_eviction(self):
        """AC10 — full E2E capstone.

        Phase A: Cross-project wake (exit 0).
          1. Provision P1 + P2; register Mainlines; assign admin; grant P2.
          2. Launch watcher for ML_P1; poll until acquired (≤10s).
          3. Cross-project send P2→P1 (subprocess).
          4. Poll for watcher exit (≤15s); assert returncode == 0 (mail wake).
          5. Assert watcher stdout contains the WAKE marker.

        Phase B: Relaunch and archive eviction (exit 3).
          6. Relaunch watcher; poll until re-acquired (≤10s).
          7. archive --project P1 --by 'Mainline - P1' --force (subprocess).
          8. Assert archive subprocess exits 0.
          9. Poll for watcher exit (≤15s); assert returncode == 3 (evicted).
        """
        # ---- Phase A: provision ----------------------------------------
        self._provision()

        # NOTE: All DB reads here use fresh connections opened per-check.
        # The watcher is a separate process writing via SQLite WAL; a single
        # long-lived connection would not see those writes until a checkpoint.
        # _poll_acquired() already does this internally; the explicit checks
        # below also use short-lived connections for the same reason.

        # ---- Phase A: launch watcher and wait for acquire ---------------
        watcher_a = self._launch_watcher()
        self._poll_acquired(timeout=10)

        # Verify the notifier row is genuinely live and has the right PID.
        con_check = sdb.connect()
        try:
            live_row = sdb.notifier_live(con_check, self.ML_P1)
        finally:
            con_check.close()

        self.assertIsNotNone(
            live_row,
            f"notifier_live({self.ML_P1!r}) must return a row after acquire",
        )
        self.assertEqual(
            live_row["pid"], watcher_a.pid,
            f"notifier pid {live_row['pid']} must match the Popen pid {watcher_a.pid}",
        )

        # ---- Phase A: cross-project send P2 → P1 -----------------------
        send_result = _run_cli(
            [
                "send",
                "--project", self.P2,
                "--from", self.ML_P2,
                "--to", self.ML_P1,
                "--subject", "ping from P2",
            ],
            env=self.env,
        )
        self.assertEqual(
            send_result.returncode, 0,
            f"cross-project send must exit 0; got {send_result.returncode}\n"
            f"stdout: {send_result.stdout!r}\n"
            f"stderr: {send_result.stderr!r}",
        )

        # ---- Phase A: poll for watcher exit (mail wake) -----------------
        self._poll_exited(watcher_a, timeout=15)

        rc_a = watcher_a.returncode
        stdout_a = self._watcher_stdout(watcher_a)
        self._watchers.remove(watcher_a)

        self.assertEqual(
            rc_a, 0,
            f"watcher must exit 0 (mail wake) after cross-project send; "
            f"got rc={rc_a}\nwatcher stdout:\n{stdout_a}",
        )
        # notify.run() prints "WAKE" when it exits with unread 'to' mail.
        self.assertIn(
            "WAKE",
            stdout_a,
            f"watcher stdout must contain 'WAKE' on exit 0; "
            f"got:\n{stdout_a!r}",
        )

        # Consume the unread mail so the Phase B watcher doesn't wake immediately.
        # The watcher only signals "you have mail"; the agent then calls fetch to
        # mark the messages read. Without this drain, the Phase B watcher would
        # find the same unread mail and exit 0 instantly instead of staying alive
        # to be tombstoned by the archive command.
        fetch_result = _run_cli(
            ["fetch", "--project", self.P1, "--to", self.ML_P1],
            env=self.env,
        )
        self.assertEqual(
            fetch_result.returncode, 0,
            f"fetch after wake must exit 0; got {fetch_result.returncode}\n"
            f"stdout: {fetch_result.stdout!r}\nstderr: {fetch_result.stderr!r}",
        )

        # ---- Phase B: relaunch watcher ----------------------------------
        watcher_b = self._launch_watcher()
        self._poll_acquired(timeout=10)

        # Confirm the new row has the relaunched process's pid.
        con_check2 = sdb.connect()
        try:
            live_row_b = sdb.notifier_live(con_check2, self.ML_P1)
        finally:
            con_check2.close()

        self.assertIsNotNone(
            live_row_b,
            "notifier row must be live after re-launch",
        )
        self.assertEqual(
            live_row_b["pid"], watcher_b.pid,
            f"re-launched watcher pid {watcher_b.pid} must match notifier row pid "
            f"{live_row_b['pid']}",
        )

        # ---- Phase B: archive --project P1 --by 'Mainline - P1' --force -
        archive_result = _run_cli(
            [
                "archive",
                "--project", self.P1,
                "--by", self.ML_P1,
                "--force",
            ],
            env=self.env,
        )
        self.assertEqual(
            archive_result.returncode, 0,
            f"archive must exit 0; got {archive_result.returncode}\n"
            f"stdout: {archive_result.stdout!r}\n"
            f"stderr: {archive_result.stderr!r}",
        )

        # Confirm the state transition was applied (fresh connection, post-archive).
        con_check3 = sdb.connect()
        try:
            state = sdb.project_state(con_check3, self.P1)
        finally:
            con_check3.close()

        self.assertEqual(
            state, "archived",
            f"P1 must be 'archived' after archive command; got {state!r}",
        )

        # ---- Phase B: poll for watcher exit (evicted/tombstoned exit 3) --
        self._poll_exited(watcher_b, timeout=15)

        rc_b = watcher_b.returncode
        stdout_b = self._watcher_stdout(watcher_b)
        self._watchers.remove(watcher_b)

        self.assertEqual(
            rc_b, 3,
            f"watcher must exit 3 (tombstoned/evicted) after archive --force; "
            f"got rc={rc_b}\nwatcher stdout:\n{stdout_b}",
        )
        # notify.run() prints "tombstoned" on exit 3.
        self.assertIn(
            "tombstoned",
            stdout_b.lower(),
            f"watcher stdout must mention tombstoned on exit 3; "
            f"got:\n{stdout_b!r}",
        )

    # ------------------------------------------------------------------
    # Guard: venv python must be available (skip gracefully if not)
    # ------------------------------------------------------------------

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(_SUBPROCESS_PYTHON):
            raise unittest.SkipTest(
                f"subprocess python not found at {_SUBPROCESS_PYTHON!r}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
