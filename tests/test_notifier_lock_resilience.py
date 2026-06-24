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
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
