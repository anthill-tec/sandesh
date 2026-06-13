"""test_consolidate_skip.py — RED tests for CR-SAN-033.

Covers: consolidate() skips non-store DB files (yoyo-stub, corrupt/zero-byte,
plain-text garbage) without aborting the scan; real stores in the same run
still consolidate normally; CLI prints the skip notice; idempotent re-skip.

AC1 — yoyo-stub skip
AC2 — corrupt/non-SQLite skip (zero-byte + plain-text garbage)
AC3 — regression (existing test_consolidation.py stays green — run separately)
AC4 — CLI notice: exit 0, stdout "skipped x: not a legacy store" + "file left untouched"
AC5 — idempotent skip: two consecutive runs, same skip, zero rows from stub

Expected RED: AC1/AC2/AC4/AC5 tests FAIL because current consolidate()
calls _consolidate_store on every sandesh.db it finds — including the
yoyo-stub — and raises sqlite3.OperationalError: no such table: address,
aborting the scan.

Run:
    PYTHONPATH=. .venv/bin/python tests/test_consolidate_skip.py
"""

import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli


# ---------------------------------------------------------------------------
# Yoyo-stub DDL — exactly the tables that appear in the live debris file.
# No 'address' table (nor message/message_recipient/notifier/project).
# ---------------------------------------------------------------------------
_YOYO_STUB_DDL = """
CREATE TABLE IF NOT EXISTS _yoyo_migration (
    migration_hash TEXT,
    migration_id   TEXT,
    applied_at_utc TIMESTAMP
);
CREATE TABLE IF NOT EXISTS _yoyo_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_hash TEXT,
    migration_id   TEXT,
    operation      TEXT,
    username       TEXT,
    hostname       TEXT,
    comment        TEXT,
    created        TIMESTAMP
);
CREATE TABLE IF NOT EXISTS _yoyo_version (
    version INTEGER
);
CREATE TABLE IF NOT EXISTS yoyo_lock (
    locked      INTEGER DEFAULT 1,
    ctime       TIMESTAMP,
    pid         INTEGER
);
"""

# ---------------------------------------------------------------------------
# Legacy DDL for a real store (pre-0003: no address.project column, no status)
# Used to build a sibling real store so AC1 verifies the scan continues.
# ---------------------------------------------------------------------------
_LEGACY_DDL_REAL = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    kind        TEXT,
    in_reply_to INTEGER REFERENCES message(id),
    body_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',
    read_at    TEXT,
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,
    pid          INTEGER,
    token        TEXT,
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_db_path(data_home, project_id):
    return os.path.join(data_home, "sandesh", "projects", project_id, "sandesh.db")


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _build_yoyo_stub(data_home, project_id="x"):
    """Create a projects/<project_id>/sandesh.db that has ONLY yoyo tables."""
    db_path = _project_db_path(data_home, project_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(_YOYO_STUB_DDL)
    con.commit()
    con.close()
    return db_path


def _build_real_legacy_store(data_home, project_id="Real"):
    """Create a minimal real legacy store (has address table + one address + one message)."""
    db_path = _project_db_path(data_home, project_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(_LEGACY_DDL_REAL)
    con.execute(
        "INSERT INTO address (address, kind, active) VALUES (?,?,?)",
        (f"Mainline - {project_id}", "mainline", 1),
    )
    con.execute(
        "INSERT INTO message (from_addr, subject, kind) VALUES (?,?,?)",
        (f"Mainline - {project_id}", f"Hello from {project_id}", "fyi"),
    )
    msg_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role) VALUES (?,?,?)",
        (msg_id, f"Mainline - {project_id}", "to"),
    )
    con.commit()
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# Base fixture
# ---------------------------------------------------------------------------

class _SkipBase(unittest.TestCase):
    """Isolated XDG_DATA_HOME per test; global DB initialised (schema only)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-skip-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.con = s.connect()   # creates the global DB with current schema

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, buf.getvalue()

    def _msg_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def _addr_count(self):
        return self.con.execute("SELECT COUNT(*) FROM address").fetchone()[0]

    def _recip_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message_recipient").fetchone()[0]


# ===========================================================================
# AC1 — yoyo-stub skip + sibling real store consolidates normally
# ===========================================================================

class AC1YoyoStubSkipTest(_SkipBase):
    """AC1: projects/x/sandesh.db containing only yoyo tables must be skipped.

    The summary entry for 'x' must have skipped=True + non-empty reason.
    The file must be byte-identical (not renamed, not modified).
    A REAL legacy store in 'Real' dir consolidates normally in the same run.
    """

    def setUp(self):
        super().setUp()
        self._stub_path = _build_yoyo_stub(self.tmp, "x")
        self._stub_hash = _file_sha256(self._stub_path)
        self._real_path = _build_real_legacy_store(self.tmp, "Real")
        # close the connection opened in _SkipBase.setUp — consolidate() opens its own
        self.con.close()
        self.con = s.connect()

    def test_stub_summary_entry_has_skipped_true(self):
        """consolidate() summary for project 'x' must contain skipped=True."""
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        self.assertIn("x", by_id,
                      f"No summary entry for project 'x'. Full summary: {summaries}")
        entry = by_id["x"]
        self.assertIn("skipped", entry,
                      f"Summary entry for 'x' has no 'skipped' key: {entry}")
        self.assertTrue(entry["skipped"],
                        f"Expected skipped=True for yoyo-stub, got: {entry}")

    def test_stub_summary_has_non_empty_reason(self):
        """Summary entry for the stub must have a non-empty 'reason' string."""
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        self.assertIn("x", by_id)
        entry = by_id["x"]
        self.assertIn("reason", entry,
                      f"Summary entry for 'x' missing 'reason' key: {entry}")
        self.assertTrue(
            bool(entry.get("reason")),
            f"reason must be a non-empty string, got: {entry.get('reason')!r}",
        )

    def test_stub_file_byte_identical_after_consolidate(self):
        """The yoyo-stub file must not be renamed or modified (byte-identical)."""
        s.consolidate()
        self.assertTrue(
            os.path.isfile(self._stub_path),
            f"Stub file was removed or renamed: {self._stub_path!r} — must be left untouched",
        )
        after_hash = _file_sha256(self._stub_path)
        self.assertEqual(
            after_hash, self._stub_hash,
            "Stub file content changed — consolidate must not touch non-store files",
        )
        pre_global = self._stub_path + ".pre-global"
        self.assertFalse(
            os.path.exists(pre_global),
            f"Stub was renamed to .pre-global — it must be left as-is: {pre_global!r}",
        )

    def test_real_store_consolidates_normally_in_same_run(self):
        """A real legacy store in a sibling dir must consolidate normally
        (address and message rows imported, .pre-global created)."""
        s.consolidate()
        # The real store's address must be in the global DB
        addr = self.con.execute(
            "SELECT address FROM address WHERE address=?",
            ("Mainline - Real",),
        ).fetchone()
        self.assertIsNotNone(
            addr,
            "Mainline - Real was not imported — real store must consolidate normally",
        )
        # And the original sandesh.db must be renamed
        pre_global = self._real_path + ".pre-global"
        self.assertTrue(
            os.path.isfile(pre_global),
            f"Real store was not renamed to .pre-global: {pre_global!r}",
        )
        self.assertFalse(
            os.path.isfile(self._real_path),
            f"Real store's original sandesh.db still exists: {self._real_path!r}",
        )

    def test_no_rows_imported_from_stub(self):
        """Zero address and message rows must come from the yoyo-stub project 'x'."""
        s.consolidate()
        # The stub has no address or message rows — global count must reflect only Real
        addr_count = self._addr_count()
        msg_count = self._msg_count()
        # Real store has 1 address + 1 message; stub contributes 0
        self.assertEqual(addr_count, 1,
                         f"Expected 1 address (from Real only), got {addr_count}")
        self.assertEqual(msg_count, 1,
                         f"Expected 1 message (from Real only), got {msg_count}")

    def test_scan_does_not_abort_on_stub(self):
        """consolidate() must not raise any exception when a yoyo-stub is present."""
        try:
            s.consolidate()
        except Exception as exc:
            self.fail(
                f"consolidate() raised {type(exc).__name__}: {exc} — "
                f"the scan must not abort on a non-store DB file"
            )


# ===========================================================================
# AC2 — corrupt/non-SQLite skip: zero-byte + plain-text garbage
# ===========================================================================

class AC2CorruptSkipTest(_SkipBase):
    """AC2: zero-byte and plain-text-garbage sandesh.db files must be skipped,
    no exception escapes, both get a summary entry with skipped=True."""

    def _make_zero_byte(self, project_id="zero"):
        path = _project_db_path(self.tmp, project_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()   # zero bytes
        return path

    def _make_text_garbage(self, project_id="garbage"):
        path = _project_db_path(self.tmp, project_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("this is not a sqlite database\nit is plain text garbage\n")
        return path

    def test_zero_byte_does_not_raise(self):
        """consolidate() must not raise any exception over a zero-byte sandesh.db."""
        self._make_zero_byte("zero")
        try:
            s.consolidate()
        except Exception as exc:
            self.fail(
                f"consolidate() raised {type(exc).__name__}: {exc} on a zero-byte file"
            )

    def test_zero_byte_summary_has_skipped_true(self):
        """Zero-byte file must produce a summary entry with skipped=True."""
        self._make_zero_byte("zero")
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        self.assertIn("zero", by_id,
                      f"No summary entry for 'zero'. Summaries: {summaries}")
        entry = by_id["zero"]
        self.assertTrue(
            entry.get("skipped"),
            f"Expected skipped=True for zero-byte file, got: {entry}",
        )

    def test_zero_byte_summary_has_reason(self):
        """Zero-byte file must include a non-empty reason in its summary entry."""
        self._make_zero_byte("zero")
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        entry = by_id.get("zero", {})
        self.assertTrue(
            bool(entry.get("reason")),
            f"Expected non-empty reason for zero-byte skip, got: {entry.get('reason')!r}",
        )

    def test_zero_byte_file_left_untouched(self):
        """Zero-byte file must not be renamed or modified."""
        path = self._make_zero_byte("zero")
        s.consolidate()
        self.assertTrue(os.path.isfile(path),
                        "Zero-byte file was removed — must be left untouched")
        self.assertEqual(os.path.getsize(path), 0,
                         "Zero-byte file was modified — must be left untouched")
        self.assertFalse(os.path.exists(path + ".pre-global"),
                         "Zero-byte file was renamed to .pre-global — must not be renamed")

    def test_text_garbage_does_not_raise(self):
        """consolidate() must not raise any exception over a plain-text garbage file."""
        self._make_text_garbage("garbage")
        try:
            s.consolidate()
        except Exception as exc:
            self.fail(
                f"consolidate() raised {type(exc).__name__}: {exc} on a text-garbage file"
            )

    def test_text_garbage_summary_has_skipped_true(self):
        """Plain-text garbage file must produce a summary entry with skipped=True."""
        self._make_text_garbage("garbage")
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        self.assertIn("garbage", by_id,
                      f"No summary entry for 'garbage'. Summaries: {summaries}")
        entry = by_id["garbage"]
        self.assertTrue(
            entry.get("skipped"),
            f"Expected skipped=True for text-garbage file, got: {entry}",
        )

    def test_text_garbage_summary_has_reason(self):
        """Plain-text garbage file must include a non-empty reason."""
        self._make_text_garbage("garbage")
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        entry = by_id.get("garbage", {})
        self.assertTrue(
            bool(entry.get("reason")),
            f"Expected non-empty reason for text-garbage skip, got: {entry.get('reason')!r}",
        )

    def test_text_garbage_file_left_untouched(self):
        """Plain-text garbage file must not be renamed or modified."""
        path = self._make_text_garbage("garbage")
        original_hash = _file_sha256(path)
        s.consolidate()
        self.assertTrue(os.path.isfile(path),
                        "Garbage file was removed — must be left untouched")
        self.assertEqual(
            _file_sha256(path), original_hash,
            "Garbage file was modified — must be left untouched",
        )
        self.assertFalse(os.path.exists(path + ".pre-global"),
                         "Garbage file was renamed to .pre-global — must not be renamed")

    def test_both_corrupt_files_skipped_no_exception(self):
        """Both zero-byte and text-garbage in the same run: both skipped, no exception."""
        self._make_zero_byte("zero")
        self._make_text_garbage("garbage")
        try:
            summaries = s.consolidate()
        except Exception as exc:
            self.fail(
                f"consolidate() raised {type(exc).__name__}: {exc} with two corrupt files"
            )
        by_id = {e["project_id"]: e for e in summaries}
        for pid in ("zero", "garbage"):
            self.assertIn(pid, by_id, f"No entry for '{pid}' in summary: {summaries}")
            self.assertTrue(by_id[pid].get("skipped"),
                            f"Expected skipped=True for '{pid}': {by_id[pid]}")


# ===========================================================================
# AC4 — CLI notice: exit 0, correct stdout lines
# ===========================================================================

class AC4CliNoticeTest(_SkipBase):
    """AC4: `sandesh consolidate` with a yoyo-stub present must:
    - exit 0
    - print a line containing 'skipped x: not a legacy store'
    - print a line containing 'file left untouched'
    """

    def setUp(self):
        super().setUp()
        _build_yoyo_stub(self.tmp, "x")

    def test_cli_exits_zero_with_stub_present(self):
        """CLI consolidate must exit 0 even when a yoyo-stub is present."""
        rc, output = self._run_cli(["consolidate"])
        self.assertEqual(
            rc, 0,
            f"Expected exit 0, got {rc!r}. Output: {output!r}",
        )

    def test_cli_stdout_contains_skipped_notice(self):
        """CLI stdout must contain 'skipped x: not a legacy store'."""
        _, output = self._run_cli(["consolidate"])
        self.assertIn(
            "skipped x: not a legacy store",
            output,
            f"Expected 'skipped x: not a legacy store' in stdout. Got: {output!r}",
        )

    def test_cli_stdout_contains_file_left_untouched(self):
        """CLI stdout must contain 'file left untouched'."""
        _, output = self._run_cli(["consolidate"])
        self.assertIn(
            "file left untouched",
            output,
            f"Expected 'file left untouched' in stdout. Got: {output!r}",
        )

    def test_cli_no_exception_with_stub(self):
        """CLI consolidate must not propagate any exception when a stub is present."""
        try:
            self._run_cli(["consolidate"])
        except Exception as exc:
            self.fail(
                f"cli.main(['consolidate']) raised {type(exc).__name__}: {exc} "
                f"with a yoyo-stub present"
            )

    def test_cli_stub_and_real_store_together(self):
        """CLI with both a yoyo-stub and a real legacy store: exit 0,
        skip notice for stub, consolidated notice for real store."""
        _build_real_legacy_store(self.tmp, "Real")
        rc, output = self._run_cli(["consolidate"])
        self.assertEqual(rc, 0, f"Expected exit 0, got {rc!r}. Output: {output!r}")
        self.assertIn(
            "skipped x: not a legacy store", output,
            f"Missing skip notice for stub in output: {output!r}",
        )
        self.assertIn(
            "file left untouched", output,
            f"Missing 'file left untouched' in output: {output!r}",
        )
        # Real store must also be mentioned
        self.assertIn(
            "Real", output,
            f"Real store not mentioned in output: {output!r}",
        )


# ===========================================================================
# AC5 — idempotent skip: two consecutive runs over the same stub
# ===========================================================================

class AC5IdempotentSkipTest(_SkipBase):
    """AC5: two consecutive consolidate() runs over the same yoyo-stub must:
    - both produce a skip entry (skipped=True) for that project
    - import zero rows from the stub either time
    - leave the file untouched both times
    """

    def setUp(self):
        super().setUp()
        self._stub_path = _build_yoyo_stub(self.tmp, "x")
        self._stub_hash = _file_sha256(self._stub_path)

    def test_first_run_skips(self):
        """First consolidate() run skips the stub."""
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}
        self.assertIn("x", by_id, f"No summary entry for 'x' on first run: {summaries}")
        self.assertTrue(
            by_id["x"].get("skipped"),
            f"First run: expected skipped=True for stub, got: {by_id['x']}",
        )

    def test_second_run_also_skips(self):
        """Second consecutive consolidate() run also skips the stub (idempotent)."""
        s.consolidate()   # first run
        summaries2 = s.consolidate()   # second run
        by_id = {e["project_id"]: e for e in summaries2}
        self.assertIn("x", by_id,
                      f"No summary entry for 'x' on second run: {summaries2}")
        self.assertTrue(
            by_id["x"].get("skipped"),
            f"Second run: expected skipped=True for stub, got: {by_id['x']}",
        )

    def test_zero_rows_imported_after_two_runs(self):
        """Zero address + message rows must be imported across both runs."""
        s.consolidate()   # first run
        addr_after_1 = self._addr_count()
        msg_after_1 = self._msg_count()

        s.consolidate()   # second run
        addr_after_2 = self._addr_count()
        msg_after_2 = self._msg_count()

        # Stub has no real rows — both runs contribute 0
        self.assertEqual(addr_after_1, 0,
                         f"After first run: expected 0 addresses from stub, got {addr_after_1}")
        self.assertEqual(msg_after_1, 0,
                         f"After first run: expected 0 messages from stub, got {msg_after_1}")
        self.assertEqual(addr_after_2, addr_after_1,
                         "Address count changed between first and second run (stub import?)")
        self.assertEqual(msg_after_2, msg_after_1,
                         "Message count changed between first and second run (stub import?)")

    def test_stub_file_untouched_after_two_runs(self):
        """Stub file must be byte-identical after both runs (never renamed)."""
        s.consolidate()
        s.consolidate()
        self.assertTrue(
            os.path.isfile(self._stub_path),
            f"Stub file absent after two runs: {self._stub_path!r}",
        )
        after_hash = _file_sha256(self._stub_path)
        self.assertEqual(
            after_hash, self._stub_hash,
            "Stub file was modified across runs — must be left untouched",
        )
        pre_global = self._stub_path + ".pre-global"
        self.assertFalse(
            os.path.exists(pre_global),
            f"Stub was renamed to .pre-global after two runs — must not be renamed",
        )

    def test_skip_reason_consistent_across_runs(self):
        """The 'reason' field must be non-empty and consistent across both runs."""
        summaries1 = s.consolidate()
        summaries2 = s.consolidate()
        by_id1 = {e["project_id"]: e for e in summaries1}
        by_id2 = {e["project_id"]: e for e in summaries2}
        reason1 = by_id1.get("x", {}).get("reason", "")
        reason2 = by_id2.get("x", {}).get("reason", "")
        self.assertTrue(bool(reason1),
                        f"First run 'reason' is empty: {reason1!r}")
        self.assertTrue(bool(reason2),
                        f"Second run 'reason' is empty: {reason2!r}")
        self.assertEqual(reason1, reason2,
                         f"Reason changed between runs: {reason1!r} → {reason2!r}")


# ===========================================================================
# Extra: scan-ordering — stub between two real stores; all consolidate
# ===========================================================================

class ScanOrderTest(_SkipBase):
    """Verifies the scan never aborts mid-way: a stub sandwiched between two
    real stores (alpha < stub < zeta alphabetically) leaves both real stores
    consolidated and the stub skipped."""

    def setUp(self):
        super().setUp()
        _build_real_legacy_store(self.tmp, "alpha")
        _build_yoyo_stub(self.tmp, "mmm")           # sorted between alpha and zeta
        _build_real_legacy_store(self.tmp, "zeta")

    def test_both_real_stores_consolidated_despite_stub(self):
        """Both 'alpha' and 'zeta' must consolidate; 'mmm' must be skipped."""
        summaries = s.consolidate()
        by_id = {e["project_id"]: e for e in summaries}

        # stub must be skipped
        self.assertIn("mmm", by_id, f"No entry for 'mmm'. Summaries: {summaries}")
        self.assertTrue(by_id["mmm"].get("skipped"),
                        f"Expected mmm skipped, got: {by_id['mmm']}")

        # both real stores must be imported (not skipped)
        for pid in ("alpha", "zeta"):
            self.assertIn(pid, by_id, f"No entry for {pid!r}")
            entry = by_id[pid]
            self.assertFalse(
                entry.get("skipped", False),
                f"Real store '{pid}' was unexpectedly skipped: {entry}",
            )
            self.assertEqual(
                entry.get("messages_imported"), 1,
                f"Expected 1 message from '{pid}', got: {entry}",
            )

    def test_global_db_has_two_addresses_not_three(self):
        """Global DB must have exactly 2 addresses (one per real store, none from stub)."""
        s.consolidate()
        count = self._addr_count()
        self.assertEqual(count, 2,
                         f"Expected 2 addresses (alpha + zeta), got {count}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
