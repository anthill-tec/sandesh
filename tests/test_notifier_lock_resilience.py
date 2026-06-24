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


if __name__ == "__main__":
    unittest.main(verbosity=2)
