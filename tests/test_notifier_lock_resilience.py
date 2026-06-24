"""test_notifier_lock_resilience.py — CR-SAN-043: notifier lock-contention resilience.

Covers:
  AC1 — busy_timeout on every connection
  AC2 — is_locked_error predicate
  AC3/AC4 — _retry_locked retry/backoff + fast-fail
  AC5 — notifier_* writes survive a transient lock
  AC6/AC7 — notify.run() no-flap boundary

Discovery is broken in this repo — run targeted:
  PYTHONPATH=. .venv/bin/python tests/test_notifier_lock_resilience.py
"""

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from sandesh import notify
from sandesh import sandesh_db as s


class BusyTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-lock-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_connect_sets_busy_timeout_30000(self):
        """AC1: connect() sets PRAGMA busy_timeout=30000 on every connection, and the
        module constant BUSY_TIMEOUT_MS == 30000.

        RED: connect() sets no busy_timeout (PRAGMA busy_timeout returns 0); and/or
        sandesh_db.BUSY_TIMEOUT_MS does not exist (AttributeError).
        """
        self.assertEqual(
            s.BUSY_TIMEOUT_MS, 30000,
            "sandesh_db.BUSY_TIMEOUT_MS must be 30000",
        )
        con = s.connect()
        try:
            value = con.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(
                value, 30000,
                f"connect() must set PRAGMA busy_timeout=30000; got {value!r}",
            )
        finally:
            con.close()


class RetryHelperTest(unittest.TestCase):
    """AC2/AC3/AC4 — is_locked_error predicate + _retry_locked retry/backoff.

    Pure functions — no store needed. `sleep` is injected as a no-op so backoff adds
    no wall-clock time and the test is deterministic.

    RED: sandesh_db has no is_locked_error / _retry_locked attribute (AttributeError).
    """

    @staticmethod
    def _noop_sleep(_delay):
        return None

    def test_is_locked_error_true_for_locked(self):
        self.assertTrue(s.is_locked_error(sqlite3.OperationalError("database is locked")))

    def test_is_locked_error_false_for_other_operationalerror(self):
        self.assertFalse(s.is_locked_error(sqlite3.OperationalError("no such table: x")))

    def test_is_locked_error_false_for_non_operationalerror(self):
        self.assertFalse(s.is_locked_error(ValueError("database is locked")))

    def test_retry_locked_retries_then_succeeds(self):
        """AC3: locked twice, then succeeds → returns sentinel, fn called 3 times."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise sqlite3.OperationalError("database is locked")
            return "ok-sentinel"

        result = s._retry_locked(fn, sleep=self._noop_sleep)
        self.assertEqual(result, "ok-sentinel")
        self.assertEqual(calls["n"], 3)

    def test_retry_locked_exhausts_and_reraises(self):
        """AC4: always locked → raises OperationalError after exactly `attempts` tries."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            s._retry_locked(fn, attempts=4, sleep=self._noop_sleep)
        self.assertEqual(calls["n"], 4)

    def test_retry_locked_fastfails_non_lock_operationalerror(self):
        """AC4: a non-lock OperationalError propagates on the FIRST call (no retry)."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise sqlite3.OperationalError("no such table: notifier")

        with self.assertRaises(sqlite3.OperationalError):
            s._retry_locked(fn, attempts=5, sleep=self._noop_sleep)
        self.assertEqual(calls["n"], 1)

    def test_retry_locked_fastfails_non_operationalerror(self):
        """AC4: a non-OperationalError propagates immediately (no retry)."""
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            s._retry_locked(fn, attempts=5, sleep=self._noop_sleep)
        self.assertEqual(calls["n"], 1)


class _FlakyWriteCon:
    """Wraps a real sqlite3 connection and raises 'database is locked' on the FIRST
    write statement (INSERT/UPDATE/DELETE) it sees, then delegates everything to the
    real connection. Simulates a single transient SQLITE_BUSY without real contention.
    `_failed` records that the injected lock fired."""

    def __init__(self, real):
        self._real = real
        self._failed = False

    def execute(self, sql, *params):
        if not self._failed and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self._failed = True
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *params)

    def commit(self):
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


class NotifierWriteRetryTest(unittest.TestCase):
    """AC5 — notifier_acquire / heartbeat / release survive a single transient lock
    (their execute+commit is wrapped in _retry_locked). A short real backoff sleep is
    tolerated (one retry ≈ 50–100 ms).

    RED: the notifier writes call con.execute directly with no retry, so the injected
    OperationalError escapes the function and the test errors.
    """

    PROJ = "Nai"
    MAIN = "Mainline - Nai"
    T1 = "Track 1 - Nai"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-lockw-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        s.setup(self.PROJ)
        self.con = s.connect()
        s.register(self.con, self.MAIN, kind="mainline", project=self.PROJ)
        s.register(self.con, self.T1, kind="track", project=self.PROJ)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acquire_survives_transient_lock(self):
        flaky = _FlakyWriteCon(self.con)
        ok, reason = s.notifier_acquire(flaky, self.T1, os.getpid(), "tok", "h")
        self.assertTrue(ok)
        self.assertEqual(reason, "acquired")
        row = self.con.execute(
            "SELECT token FROM notifier WHERE recipient=?", (self.T1,)).fetchone()
        self.assertIsNotNone(row, "acquire must have inserted the row through the retry")
        self.assertEqual(row[0], "tok")
        self.assertTrue(flaky._failed, "the proxy should have injected one transient lock")

    def test_heartbeat_survives_transient_lock(self):
        s.notifier_acquire(self.con, self.T1, os.getpid(), "tok", "h")
        self.con.execute(
            "UPDATE notifier SET heartbeat_at='2000-01-01 00:00:00' WHERE recipient=?",
            (self.T1,))
        self.con.commit()
        flaky = _FlakyWriteCon(self.con)
        s.notifier_heartbeat(flaky, self.T1, "tok")
        hb = self.con.execute(
            "SELECT heartbeat_at FROM notifier WHERE recipient=?", (self.T1,)).fetchone()[0]
        self.assertNotEqual(
            hb, "2000-01-01 00:00:00",
            "heartbeat write must have executed (advanced heartbeat_at) through the retry")
        self.assertTrue(flaky._failed)

    def test_release_survives_transient_lock(self):
        s.notifier_acquire(self.con, self.T1, os.getpid(), "tok", "h")
        flaky = _FlakyWriteCon(self.con)
        s.notifier_release(flaky, self.T1, "tok")
        cnt = self.con.execute(
            "SELECT count(*) FROM notifier WHERE recipient=?", (self.T1,)).fetchone()[0]
        self.assertEqual(cnt, 0, "release must have removed the row through the retry")
        self.assertTrue(flaky._failed)

    def test_tombstone_survives_transient_lock(self):
        s.notifier_acquire(self.con, self.T1, os.getpid(), "tok", "h")
        flaky = _FlakyWriteCon(self.con)
        s.notifier_tombstone(flaky, self.T1)
        row = self.con.execute(
            "SELECT tombstone FROM notifier WHERE recipient=?", (self.T1,)).fetchone()
        self.assertTrue(row[0], "tombstone flag must be set through the retry")
        self.assertTrue(flaky._failed)

    def test_reap_if_stale_survives_transient_lock(self):
        # a dead-pid row is reapable; reap must survive a transient lock on its DELETE
        self.con.execute(
            "INSERT INTO notifier (recipient,pid,token,host) VALUES (?,?,?,?)",
            (self.T1, 2_000_000_000, "dead", "h"))
        self.con.commit()
        flaky = _FlakyWriteCon(self.con)
        reaped = s.notifier_reap_if_stale(flaky, self.T1)
        self.assertTrue(reaped, "a dead-pid row must be reaped")
        cnt = self.con.execute(
            "SELECT count(*) FROM notifier WHERE recipient=?", (self.T1,)).fetchone()[0]
        self.assertEqual(cnt, 0, "reap must have removed the row through the retry")
        self.assertTrue(flaky._failed)


class NotifyBoundaryTest(unittest.TestCase):
    """AC6/AC7 — notify.run() is resilient at its boundary: a transient 'database is
    locked' from the startup acquire or any poll-loop op is retried on the poll cadence
    (bounded by the watch deadline → exit 2), never crashing the watcher (exit 1) and
    flapping `listening`. A non-lock error still propagates.

    RED: run() calls notifier_acquire / poll ops with no lock handling, so the injected
    OperationalError escapes run() (the test expecting a clean exit code errors instead).
    """

    PROJ = "Nai"
    T1 = "Track 1 - Nai"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-notify-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        s.setup(self.PROJ)
        self.con = s.connect()
        s.register(self.con, self.T1, kind="track", project=self.PROJ)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _quiet_side_effects(self):
        """Patch out wall-clock sleep, atexit cleanup and signal handlers so run() is
        fast and leaves no process-exit residue in the test runner."""
        return (
            mock.patch.object(notify.time, "sleep", lambda *_a, **_k: None),
            mock.patch.object(notify.atexit, "register", lambda *_a, **_k: None),
            mock.patch.object(notify.signal, "signal", lambda *_a, **_k: None),
        )

    def test_run_survives_transient_lock_on_acquire(self):
        """AC6: acquire raises 'locked' once → run() retries, listens, returns 0 (mail)."""
        calls = {"acq": 0}

        def fake_acquire(_con, _addr, _pid, _token, _host):
            calls["acq"] += 1
            if calls["acq"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return (True, "acquired")

        p_sleep, p_atexit, p_signal = self._quiet_side_effects()
        with p_sleep, p_atexit, p_signal, \
                mock.patch.object(notify.sdb, "notifier_acquire", fake_acquire), \
                mock.patch.object(notify.sdb, "notifier_check", lambda *_a: "ok"), \
                mock.patch.object(notify.sdb, "notifier_heartbeat", lambda *_a: None), \
                mock.patch.object(notify.sdb, "unread_to", lambda *_a: [1]):
            rc = notify.run(self.PROJ, self.T1, timeout=60)
        self.assertEqual(rc, 0, "run() must retry the locked acquire and return 0 (mail), not crash")
        self.assertEqual(calls["acq"], 2, "the startup acquire should have been retried exactly once")

    def test_run_lock_outlives_window_returns_2(self):
        """AC7: acquire always locked + deadline already elapsed → run() returns 2, never raises."""

        def always_locked(*_a):
            raise sqlite3.OperationalError("database is locked")

        p_sleep, p_atexit, p_signal = self._quiet_side_effects()
        with p_sleep, p_atexit, p_signal, \
                mock.patch.object(notify.sdb, "notifier_acquire", always_locked):
            rc = notify.run(self.PROJ, self.T1, timeout=0)
        self.assertEqual(rc, 2, "a lock spanning the whole watch window must time out (exit 2), not exit 1")

    def test_run_propagates_non_lock_error(self):
        """AC6 (invariant): a non-lock OperationalError from a poll op is NOT swallowed."""

        def boom(*_a):
            raise sqlite3.OperationalError("no such table: notifier")

        p_sleep, p_atexit, p_signal = self._quiet_side_effects()
        with p_sleep, p_atexit, p_signal, \
                mock.patch.object(notify.sdb, "notifier_acquire", lambda *_a: (True, "acquired")), \
                mock.patch.object(notify.sdb, "notifier_check", boom):
            with self.assertRaises(sqlite3.OperationalError):
                notify.run(self.PROJ, self.T1, timeout=60)


class RealContentionTest(unittest.TestCase):
    """AC9 — the fix absorbs REAL concurrent-writer contention (not an injected lock).

    N threads, each with its OWN connect() (real WAL writer serialization + real
    PRAGMA busy_timeout + real _retry_locked), burst-start via a barrier and hammer the
    notifier table with acquire/heartbeat/release cycles. With the fix in place this
    completes with ZERO sqlite3.OperationalError.

    Non-vacuity is proven in the C5 RED step: with the fix neutralized
    (BUSY_TIMEOUT_MS=0 + LOCK_RETRY_ATTEMPTS=1) this same test surfaces
    'database is locked'.
    """

    PROJ = "Nai"
    N_THREADS = 8
    ITERS = 50

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-contend-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        s.setup(self.PROJ)
        s.connect().close()      # settle schema + migrations once before threads connect

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_writers_no_lock_errors(self):
        errors = []
        completed = []
        barrier = threading.Barrier(self.N_THREADS)

        def worker(idx):
            rec = f"Track {idx + 1} - {self.PROJ}"
            try:
                con = s.connect()
            except Exception as exc:                       # connect itself locked
                errors.append((rec, "connect", type(exc).__name__, str(exc)))
                try:
                    barrier.abort()                        # don't hang the other threads
                except Exception:
                    pass
                return
            try:
                barrier.wait(timeout=30)                   # burst-start: maximise overlap
            except threading.BrokenBarrierError:
                pass
            try:
                pid, host = os.getpid(), "h"
                for n in range(self.ITERS):
                    tok = f"{idx}-{n}"
                    s.notifier_acquire(con, rec, pid, tok, host)
                    s.notifier_heartbeat(con, rec, tok)
                    s.notifier_release(con, rec, tok)
                completed.append(self.ITERS)
            except Exception as exc:                       # noqa: BLE001 — record, don't swallow
                errors.append((rec, "workload", type(exc).__name__, str(exc)))
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(self.N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(
            errors, [],
            f"concurrent notifier writers must not surface lock errors; got {len(errors)}: {errors[:5]}")
        self.assertEqual(
            sum(completed), self.N_THREADS * self.ITERS,
            f"every writer should complete all {self.ITERS} cycles; completed={completed}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
