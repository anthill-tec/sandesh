"""test_fts_index.py — RED tests for CR-SAN-027 Cycle 1.

Covers §S1 (migration 0005 + _SCHEMA parity + dump exclusion) + §S2 send-time
indexing + AC9 tombstone text destruction.

  AC1 — index shape + gate:
    * after migrate.apply() on a fresh store, message_fts exists in
      sqlite_master and is an fts5 virtual table
    * migrate.status() shows 0005-message-fts applied, 0 pending
    * dump_schema() has NO key whose name starts with 'message_fts'
    * dump_schema() result == committed current-schema.json (unchanged snapshot)
    * rollback removes message_fts; message rows are untouched
    * fresh-_SCHEMA parity: a bare setup() store has the table
    * harmless chain re-run: apply() on a setup() store succeeds and leaves
      all five migrations applied, 0 pending

  AC2 — send-time indexing (raw SQL MATCH queries — search() not yet written):
    * send with body → message_fts row at rowid=mid whose body matches a
      body-only term
    * subject-only send → row with empty body; subject term matches
    * refused cross-project send (no grant) → zero new FTS rows

  AC9 — tombstone text destruction:
    * P2-internal message: FTS row gone after tombstone_project(P2)
    * P2→P1 surviving cross-project message: FTS row gone after tombstone
    * P1→P2 message (P2 merely received it): FTS row remains
    * a MATCH for a term unique to P2-sent bodies returns nothing after tombstone

Expected RED:
  * AC1 shape tests: 'no such table: message_fts' (migration absent; _SCHEMA absent)
  * AC1 dump/snapshot tests: dump_schema() contains message_fts* keys OR snapshot
    equality holds but status test fails because 0005 is not in applied list
  * AC2 tests: 'no such table: message_fts' on every INSERT/SELECT
  * AC9 tests: 'no such table: message_fts' on every query

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_fts_index --agent red-cr027-c1
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "sandesh", "schema", "current-schema.json")

# Ensure the sandesh pkg is importable.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _table_names(db_path):
    """Return the set of all table names (incl. virtual) from sqlite_master."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def _fts5_table_type(db_path, table_name):
    """Return the tbl_name from sqlite_master for an fts5 virtual table,
    or None if the table does not exist or is not a virtual table."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _fts_row_count(db_path):
    """Count rows in message_fts. Returns 0 if the table does not exist."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def _fts_row_for_rowid(db_path, rowid):
    """Fetch a message_fts row by rowid. Raises OperationalError if table absent."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT rowid, subject, body FROM message_fts WHERE rowid=?",
            (rowid,)
        ).fetchone()
    finally:
        con.close()


def _fts_match(db_path, query_term):
    """Return list of rowids that MATCH query_term in message_fts.
    Raises OperationalError if table absent."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT rowid FROM message_fts WHERE message_fts MATCH ?",
            (query_term,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Fixture base class — isolated XDG_DATA_HOME per test
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Mixin: setUp/tearDown for an isolated XDG_DATA_HOME temp dir.

    Provides _data_home(), _db_path(), _set_xdg(), _apply(), _status(),
    _rollback(), _setup_project(), _connect(), _dump_schema() helpers that
    mirror the test_migration_0004.py fixture pattern.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_fts_test_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _data_home(self, name="dh"):
        p = os.path.join(self._tmpdir, name)
        os.makedirs(p, exist_ok=True)
        return p

    def _db_path(self, data_home):
        return os.path.join(data_home, "sandesh", "sandesh.db")

    def _set_xdg(self, data_home):
        os.environ["XDG_DATA_HOME"] = data_home

    def _apply(self, data_home):
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.apply()

    def _status(self, data_home):
        self._set_xdg(data_home)
        from sandesh import migrate
        return migrate.status()

    def _rollback(self, data_home):
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.rollback()

    def _setup_project(self, project_id, data_home):
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        sandesh_db.setup(project_id)

    def _connect(self, data_home):
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        return sqlite3.connect(sandesh_db.db_path())

    def _dump_schema(self, data_home):
        self._set_xdg(data_home)
        from sandesh import migrate
        return migrate.dump_schema()


# ---------------------------------------------------------------------------
# AC1 — index shape + gate
# ---------------------------------------------------------------------------

class Migration0005ShapeAfterApplyTest(_TempDataHome):
    """AC1: after apply() on a fresh store, message_fts exists as an fts5
    virtual table; status() shows 0005-message-fts applied; dump_schema()
    contains NO message_fts* keys; dump_schema() == committed snapshot.

    RED: 0005-message-fts.sql does not exist → apply() leaves no message_fts
    table → every shape assertion fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("shape")
        self._pid = "ShapeTest"
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

    def test_message_fts_table_exists_in_sqlite_master(self):
        """message_fts must appear in sqlite_master after apply().

        RED: 0005 absent → table never created.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables,
            f"message_fts must exist after apply(); found tables: {sorted(tables)!r}. "
            "0005-message-fts.sql does not yet exist.",
        )

    def test_message_fts_is_fts5_virtual_table(self):
        """message_fts must be an fts5 VIRTUAL TABLE (CREATE VIRTUAL TABLE … USING fts5).

        RED: table absent → sql is None → assertion fails.
        """
        sql = _fts5_table_type(self._db, "message_fts")
        self.assertIsNotNone(
            sql,
            "message_fts not found in sqlite_master (0005 not yet applied)",
        )
        sql_upper = sql.upper()
        self.assertIn(
            "VIRTUAL",
            sql_upper,
            f"message_fts DDL must contain VIRTUAL; got: {sql!r}",
        )
        self.assertIn(
            "FTS5",
            sql_upper,
            f"message_fts DDL must reference fts5; got: {sql!r}",
        )

    def test_message_fts_has_subject_column(self):
        """message_fts must index the 'subject' column (from the DDL text).

        RED: table absent.
        """
        sql = _fts5_table_type(self._db, "message_fts")
        self.assertIsNotNone(sql, "message_fts not found in sqlite_master")
        self.assertIn(
            "subject",
            sql.lower(),
            f"message_fts DDL must name 'subject' column; got: {sql!r}",
        )

    def test_message_fts_has_body_column(self):
        """message_fts must index the 'body' column (from the DDL text).

        RED: table absent.
        """
        sql = _fts5_table_type(self._db, "message_fts")
        self.assertIsNotNone(sql, "message_fts not found in sqlite_master")
        self.assertIn(
            "body",
            sql.lower(),
            f"message_fts DDL must name 'body' column; got: {sql!r}",
        )

    def test_status_shows_0005_applied(self):
        """After apply(), 0005-message-fts must appear in applied ids.

        RED: 0005 absent → it never appears in applied.
        """
        applied, pending = self._status(self._dh)
        self.assertIn(
            "0005-message-fts",
            applied,
            f"0005-message-fts must be in applied after apply(); got applied={applied!r}",
        )

    def test_status_shows_zero_pending(self):
        """After apply(), 0 pending migrations.

        RED: 0005 absent → it appears in pending, count > 0.
        """
        applied, pending = self._status(self._dh)
        self.assertEqual(
            len(pending), 0,
            f"0 pending expected after apply(); got pending={pending!r}",
        )

    def test_status_shows_five_migrations_applied(self):
        """After apply(), exactly 5 migrations (0001–0005) must be applied.

        RED: 0005 absent → only 4 applied.
        """
        applied, pending = self._status(self._dh)
        for mid in (
            "0001-baseline",
            "0002-drop-message-status",
            "0003-project-tracker",
            "0004-xproj-grant",
            "0005-message-fts",
        ):
            self.assertIn(
                mid, applied,
                f"{mid} must be in applied after apply(); got applied={applied!r}",
            )
        self.assertEqual(
            len(applied), 5,
            f"Exactly 5 migrations must be applied; got applied={applied!r}",
        )

    def test_dump_schema_excludes_message_fts_keys(self):
        """dump_schema() must NOT contain any key starting with 'message_fts'.

        The FTS index is derived/regenerable data — it is excluded from the
        schema-of-record. The _live_shape exclusion tuple must gain the
        'message_fts' prefix.

        RED: _live_shape exclusion is missing → message_fts* keys appear in the
        dump.  (This test also fails at RED because the table doesn't exist yet,
        but the test specifically pins the exclusion requirement.)
        """
        schema = self._dump_schema(self._dh)
        tables = schema.get("tables", {})
        fts_keys = [k for k in tables if k.startswith("message_fts")]
        self.assertEqual(
            fts_keys, [],
            f"dump_schema() must exclude all message_fts* tables; found: {fts_keys!r}. "
            "Add 'message_fts' to the _live_shape startswith exclusion tuple.",
        )

    def test_dump_schema_equals_committed_snapshot(self):
        """dump_schema() must equal the committed current-schema.json exactly.

        The snapshot is UNCHANGED by C1 (message_fts is excluded from the
        dump). This test pins both the exclusion rule AND the parity contract.

        RED failure modes:
          1. message_fts* leaks into the dump → tables sets differ.
          2. 0005 absent → status/shape tests fail earlier; but if somehow the
             table is absent AND no leakage, the snapshot equality might pass
             vacuously — the status sub-test above catches that.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"current-schema.json not found at {_SNAPSHOT_PATH}",
        )
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            committed = json.load(fh)

        live = self._dump_schema(self._dh)

        committed_tables = set(committed.get("tables", {}).keys())
        live_tables = set(live.get("tables", {}).keys())
        self.assertEqual(
            committed_tables, live_tables,
            f"Table sets differ between dump_schema() and current-schema.json.\n"
            f"  committed: {sorted(committed_tables)}\n"
            f"  live:      {sorted(live_tables)}\n"
            "If 'message_fts' appears in live_tables: the exclusion prefix is missing. "
            "The committed snapshot must NOT be regenerated for C1.",
        )
        self.assertEqual(
            live, committed,
            "dump_schema() does not equal current-schema.json (post-0005 dump must "
            "exclude message_fts so the snapshot stays unchanged).",
        )


class Migration0005RollbackTest(_TempDataHome):
    """AC1 rollback: after apply() + rollback(), message_fts is gone;
    message rows are untouched.

    RED: 0005 absent → apply() never creates message_fts → rollback rolls
    back 0004 instead → pre-condition assertion on message_fts fires first.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("rollback")
        self._pid = "RollbackTest"
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

        # Seed one message row so we can assert it survives rollback.
        con = sqlite3.connect(self._db)
        try:
            con.execute(
                "INSERT INTO message (from_addr, subject) VALUES ('Mainline - RollbackTest', 'hello')"
            )
            con.commit()
            self._seed_mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            con.close()

    def test_message_fts_exists_before_rollback(self):
        """Pre-condition: message_fts must exist after apply() (guards the rollback test).

        RED: 0005 absent → table never created → this pre-condition fires.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables,
            "message_fts must exist after apply() — pre-condition for rollback test; "
            "0005-message-fts.sql not yet written.",
        )

    def test_rollback_removes_message_fts(self):
        """After apply-then-rollback, message_fts must not exist.

        RED: pre-condition above fires first; if 0005 somehow created it,
        the rollback SQL must also be present and correct.
        """
        # Pre-condition guard (hard assert, no skip).
        tables_before = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables_before,
            "message_fts must exist after apply() (pre-condition); 0005 not yet written.",
        )
        self._rollback(self._dh)
        tables_after = _table_names(self._db)
        self.assertNotIn(
            "message_fts",
            tables_after,
            f"message_fts must be gone after rollback(); "
            f"found tables: {sorted(tables_after)!r}",
        )

    def test_rollback_leaves_message_rows_intact(self):
        """Message rows must survive the apply+rollback cycle (rollback only drops FTS).

        RED: 0005 absent → rollback rolls back 0004 → message table structure
        may be unchanged (but no harm to messages — this is a data-safety pin).
        """
        # Pre-condition.
        tables_before = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables_before,
            "message_fts must exist after apply() (pre-condition); 0005 not written.",
        )
        self._rollback(self._dh)
        con = sqlite3.connect(self._db)
        try:
            row = con.execute(
                "SELECT id, subject FROM message WHERE id=?", (self._seed_mid,)
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(
            row,
            f"Message row id={self._seed_mid} must survive rollback; "
            "rollback must only DROP the FTS table.",
        )
        self.assertEqual(
            row[1], "hello",
            f"Message subject must be 'hello' after rollback; got {row[1]!r}",
        )

    def test_rollback_leaves_0005_pending(self):
        """After rollback, 0005-message-fts must be pending.

        RED: 0005 absent → it never appears in pending or applied.
        """
        # Pre-condition.
        tables_before = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables_before,
            "message_fts must exist after apply() (pre-condition); 0005 not written.",
        )
        self._rollback(self._dh)
        applied, pending = self._status(self._dh)
        self.assertNotIn(
            "0005-message-fts",
            applied,
            f"0005-message-fts must NOT be in applied after rollback; got applied={applied!r}",
        )
        self.assertIn(
            "0005-message-fts",
            pending,
            f"0005-message-fts must be in pending after rollback; got pending={pending!r}",
        )

    def test_rollback_keeps_earlier_migrations_applied(self):
        """After rollback of 0005, 0001–0004 must still be applied.

        RED: if rollback rolls back the wrong step (e.g. 0004), this fails.
        """
        tables_before = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables_before,
            "pre-condition: message_fts must exist (0005 not yet written)",
        )
        self._rollback(self._dh)
        applied, _ = self._status(self._dh)
        for mid in (
            "0001-baseline",
            "0002-drop-message-status",
            "0003-project-tracker",
            "0004-xproj-grant",
        ):
            self.assertIn(
                mid, applied,
                f"{mid} must still be applied after rollback() of 0005; "
                f"got applied={applied!r}",
            )


class Migration0005FreshSchemaParity(_TempDataHome):
    """AC1 fresh-_SCHEMA parity + harmless re-run.

    sandesh_db.setup() must create a DB whose _SCHEMA already includes
    message_fts (fresh-DB parity). Then migrate.apply() on that fresh setup()
    store must be a harmless re-run: status() shows all 5 applied, 0 pending.

    RED: _SCHEMA does not include message_fts → fresh store lacks it →
    every assertion below fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("parity")
        self._pid = "ParityTest"
        # Provision a fresh store via setup() — does NOT go through migrations.
        self._setup_project(self._pid, self._dh)
        self._db = self._db_path(self._dh)

    def test_fresh_setup_creates_message_fts_table(self):
        """sandesh_db.setup() must create message_fts (via _SCHEMA parity).

        RED: _SCHEMA has no message_fts CREATE statement.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "message_fts",
            tables,
            f"message_fts must exist in a fresh setup() store (_SCHEMA parity); "
            f"found: {sorted(tables)!r}. "
            "_SCHEMA does not yet contain the CREATE VIRTUAL TABLE message_fts statement.",
        )

    def test_fresh_setup_message_fts_is_fts5(self):
        """The message_fts created by setup() must be an fts5 virtual table.

        RED: table absent → sql is None.
        """
        sql = _fts5_table_type(self._db, "message_fts")
        self.assertIsNotNone(
            sql,
            "message_fts not found after setup() (_SCHEMA parity missing)",
        )
        self.assertIn(
            "fts5",
            sql.lower(),
            f"message_fts from setup() must use fts5; got DDL: {sql!r}",
        )

    def test_apply_on_fresh_setup_store_does_not_raise(self):
        """migrate.apply() on a fresh setup() store must not raise (harmless re-run).

        RED: 0005 absent → apply() leaves 0005 pending; but this test checks
        for exception, which only fires if apply() raises.  The status test
        below catches the pending case.
        """
        try:
            self._apply(self._dh)
        except Exception as exc:
            self.fail(
                f"migrate.apply() on a fresh setup() store raised "
                f"{type(exc).__name__}: {exc}\n"
                "Expected: harmless re-run with all 5 applied, 0 pending.",
            )

    def test_apply_on_fresh_setup_store_shows_five_applied_zero_pending(self):
        """After apply() on a fresh setup() store, 5 applied, 0 pending.

        RED: 0005 absent → only 4 applied.
        """
        self._apply(self._dh)
        applied, pending = self._status(self._dh)
        for mid in (
            "0001-baseline",
            "0002-drop-message-status",
            "0003-project-tracker",
            "0004-xproj-grant",
            "0005-message-fts",
        ):
            self.assertIn(
                mid, applied,
                f"{mid} must be in applied after apply() on fresh store; "
                f"got applied={applied!r}",
            )
        self.assertEqual(
            len(pending), 0,
            f"0 pending expected; got pending={pending!r}",
        )
        self.assertEqual(
            len(applied), 5,
            f"Exactly 5 migrations applied; got applied={applied!r}",
        )


# ---------------------------------------------------------------------------
# AC2 — send-time indexing
# ---------------------------------------------------------------------------

class SendTimeIndexingTest(_TempDataHome):
    """AC2: send() inserts into message_fts atomically.

    * send with body → a message_fts row at rowid=mid with matching body text
    * subject-only send → row with empty body; subject term matches
    * refused cross-project send (no grant) → zero new FTS rows

    Raw SQL MATCH queries used throughout — search() does not exist yet.

    RED: 'no such table: message_fts' on every INSERT/SELECT via the MATCH.
    """

    P1 = "P1send"
    P2 = "P2send"
    ML_P1 = "Mainline - P1send"
    ML_P2 = "Mainline - P2send"
    T1_P1 = "Track 1 - P1send"

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("send_idx")
        self._set_xdg(self._dh)

        from sandesh import sandesh_db as s
        self._s = s

        s.setup(self.P1)
        s.setup(self.P2)
        self._con = s.connect()
        self._store = s.store_dir(self.P1)

        s.register(self._con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self._con, self.T1_P1, kind="track", project=self.P1)
        s.register(self._con, self.ML_P2, kind="mainline", project=self.P2)

    def tearDown(self):
        self._con.close()
        super().tearDown()

    def test_send_with_body_creates_fts_row(self):
        """send() with body_text must insert a row into message_fts at rowid=mid.

        RED: 'no such table: message_fts' (migration + _SCHEMA absent).
        """
        mid = self._s.send(
            self._con, self._store,
            from_addr=self.ML_P1,
            to=[self.T1_P1],
            subject="deployment complete",
            body_text="zephyr migration succeeded at midnight",
            project=self.P1,
        )
        row = _fts_row_for_rowid(self._db_path(self._dh), mid)
        self.assertIsNotNone(
            row,
            f"message_fts must have a row at rowid={mid} after send(); "
            "table absent (0005 not yet implemented).",
        )
        self.assertEqual(
            row["subject"], "deployment complete",
            f"FTS row subject must be 'deployment complete'; got {row['subject']!r}",
        )
        # Body term is unique — verify it is stored in the FTS body column.
        self.assertIn(
            "zephyr",
            (row["body"] or "").lower(),
            f"FTS row body must contain 'zephyr'; got {row['body']!r}",
        )

    def test_send_with_body_fts_row_matches_body_only_term(self):
        """A body-only term (absent from subject) must match via MATCH query.

        RED: 'no such table: message_fts'.
        """
        mid = self._s.send(
            self._con, self._store,
            from_addr=self.ML_P1,
            to=[self.T1_P1],
            subject="status update",
            body_text="kaleidoscope indexing pipeline finished",
            project=self.P1,
        )
        db = self._db_path(self._dh)
        matching_rowids = _fts_match(db, "kaleidoscope")
        self.assertIn(
            mid, matching_rowids,
            f"MATCH 'kaleidoscope' must find rowid={mid}; got matching rowids={matching_rowids!r}. "
            "'kaleidoscope' is body-only (not in subject) — verifies body column is indexed.",
        )
        # Negative: a made-up term not in the message must NOT match.
        no_match = _fts_match(db, "xyzzyabsentterm")
        self.assertEqual(
            no_match, [],
            f"MATCH for absent term must return empty; got {no_match!r}",
        )

    def test_subject_only_send_creates_fts_row_with_empty_body(self):
        """A subject-only send (no body_text) must create a message_fts row
        with empty/NULL body but the subject populated.

        RED: 'no such table: message_fts'.
        """
        mid = self._s.send(
            self._con, self._store,
            from_addr=self.ML_P1,
            to=[self.T1_P1],
            subject="luminous subject-only message",
            body_text=None,
            project=self.P1,
        )
        row = _fts_row_for_rowid(self._db_path(self._dh), mid)
        self.assertIsNotNone(
            row,
            f"message_fts must have a row at rowid={mid} for a subject-only send; "
            "table absent (0005 not yet implemented).",
        )
        # Body column must be empty string (not None — FTS5 stores empty text).
        body_val = row["body"] or ""
        self.assertEqual(
            body_val, "",
            f"FTS row body must be empty for a subject-only send; got {body_val!r}",
        )

    def test_subject_only_send_fts_row_matches_subject_term(self):
        """A subject-only term must match via MATCH after a subject-only send.

        RED: 'no such table: message_fts'.
        """
        mid = self._s.send(
            self._con, self._store,
            from_addr=self.ML_P1,
            to=[self.T1_P1],
            subject="luminous subject-only message",
            body_text=None,
            project=self.P1,
        )
        db = self._db_path(self._dh)
        matching_rowids = _fts_match(db, "luminous")
        self.assertIn(
            mid, matching_rowids,
            f"MATCH 'luminous' must find rowid={mid} (subject-only message); "
            f"got {matching_rowids!r}",
        )

    def test_refused_cross_project_send_leaves_zero_new_fts_rows(self):
        """A refused send (cross-project, no grant) must leave zero new FTS rows.

        This verifies atomicity: the FTS insert must be inside the same
        transaction as the message row. A refused send raises ValueError before
        any commit — the FTS table must not gain a row.

        RED: 'no such table: message_fts' on the COUNT query (table absent).
        """
        db = self._db_path(self._dh)
        # Count before.
        count_before = _fts_row_count(db)

        # Attempt a cross-project send WITHOUT a grant — must be refused.
        with self.assertRaises(ValueError) as ctx:
            self._s.send(
                self._con, self._store,
                from_addr=self.ML_P1,
                to=[self.ML_P2],          # ML_P2 is in P2 — cross-project
                subject="unauthorized cross-project message",
                body_text="should not appear in index",
                project=self.P1,
            )
        self.assertIn(
            "cross-project", str(ctx.exception).lower(),
            f"Refused send must mention 'cross-project'; got: {ctx.exception!r}",
        )

        count_after = _fts_row_count(db)
        self.assertEqual(
            count_before, count_after,
            f"Refused send must not add FTS rows; before={count_before}, after={count_after}. "
            "The FTS INSERT must be inside the same transaction as the message row.",
        )

    def test_send_fts_insert_is_atomic_with_message_row(self):
        """The FTS row count must equal the message row count (one-to-one).

        After N sends, both message and message_fts must have N rows. This pins
        the atomic invariant without simulating a rollback.

        RED: message_fts absent → fts count = 0, message count = N → fails.
        """
        db = self._db_path(self._dh)
        N = 3
        for i in range(N):
            self._s.send(
                self._con, self._store,
                from_addr=self.ML_P1,
                to=[self.T1_P1],
                subject=f"atomic test {i}",
                body_text=f"body content uniqueterm{i}",
                project=self.P1,
            )

        fts_count = _fts_row_count(db)
        con = sqlite3.connect(db)
        try:
            msg_count = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        finally:
            con.close()

        self.assertEqual(
            fts_count, msg_count,
            f"message_fts row count ({fts_count}) must equal message row count ({msg_count}). "
            "Each send must insert exactly one FTS row atomically.",
        )


# ---------------------------------------------------------------------------
# AC9 — tombstone text destruction
# ---------------------------------------------------------------------------

class TombstoneTextDestructionTest(_TempDataHome):
    """AC9: tombstone_project(P2) must delete message_fts rows for all messages
    SENT BY P2 addresses (internal AND surviving cross-project), while rows for
    messages P2 merely RECEIVED (sent by live projects) remain.

    Fixture (mirrors test_lifecycle_tombstone.py pattern):
      P1: Mainline-P1, Track1-P1  (the live survivor)
      P2: Mainline-P2, Track1-P2  (to be tombstoned)

    Three messages:
      M_internal: Mainline-P2 → Track1-P2 (P2-internal; will be purged by tombstone)
      M_cross:    Mainline-P2 → Mainline-P1 (cross-project, P2 granted; row survives
                  in message table but FTS row must be deleted)
      M_p1_sent:  Mainline-P1 → Track1-P1 (P2 received this? No — P1 internal;
                  but P2 address not a recipient. We use a P1→P2 message to test
                  the "P2 merely received it" branch.)
      M_recv:     Mainline-P1 → Mainline-P2 (P2 is recipient; FTS row stays)

    RED: 'no such table: message_fts' on every query.
    """

    P1 = "P1ts"
    P2 = "P2ts"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1ts"
    T1_P1 = "Track 1 - P1ts"
    ML_P2 = "Mainline - P2ts"
    T1_P2 = "Track 1 - P2ts"

    # Unique body terms per message (for MATCH verification).
    INTERNAL_TERM = "xylographytombstone"
    CROSS_TERM = "apheliondestroyterm"
    RECV_TERM = "bioluminescencestays"

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("tombstone_fts")
        self._set_xdg(self._dh)

        from sandesh import sandesh_db as s
        self._s = s

        s.setup(self.P1)
        s.setup(self.P2)
        self._con = s.connect()
        self._store_p1 = s.store_dir(self.P1)
        self._store_p2 = s.store_dir(self.P2)

        s.register(self._con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self._con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self._con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self._con, self.T1_P2, kind="track",    project=self.P2)

        s.assign_admin(self._con, self.ADMIN)
        # Grant BOTH projects so cross-project sends work in the fixture.
        s.grant_xproj(self._con, self.P2, self.ADMIN)
        s.grant_xproj(self._con, self.P1, self.ADMIN)

        # M_internal: P2-internal (Mainline-P2 → Track1-P2)
        self._mid_internal = s.send(
            self._con, self._store_p2,
            from_addr=self.ML_P2,
            to=[self.T1_P2],
            subject="internal p2 dispatch",
            body_text=f"body contains {self.INTERNAL_TERM} uniquely",
            project=self.P2,
        )

        # M_cross: P2→P1 cross-project (row survives tombstone; FTS row must die)
        self._mid_cross = s.send(
            self._con, self._store_p2,
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="cross project dispatch from p2",
            body_text=f"body contains {self.CROSS_TERM} uniquely",
            project=self.P2,
        )

        # M_recv: P1 → P2 (P2 is recipient; sent by a LIVE project)
        self._mid_recv = s.send(
            self._con, self._store_p1,
            from_addr=self.ML_P1,
            to=[self.ML_P2],
            subject="p1 sending to p2",
            body_text=f"body contains {self.RECV_TERM} uniquely",
            project=self.P1,
        )

        # Archive P2 (required before tombstone_project).
        s.archive(self._con, self.P2, self.ML_P2, wait_secs=0.1)

    def tearDown(self):
        self._con.close()
        super().tearDown()

    def _fts_rowids(self):
        """Return the set of rowids currently in message_fts."""
        db = self._db_path(self._dh)
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT rowid FROM message_fts").fetchall()
            return {r[0] for r in rows}
        finally:
            con.close()

    def test_fts_rows_exist_before_tombstone(self):
        """Pre-condition: all three messages must have FTS rows before tombstone.

        RED: 'no such table: message_fts' (0005 + _SCHEMA absent).
        """
        db = self._db_path(self._dh)
        # This will raise OperationalError if the table is absent — that IS the RED.
        rowids = self._fts_rowids()
        for mid, label in [
            (self._mid_internal, "M_internal"),
            (self._mid_cross, "M_cross"),
            (self._mid_recv, "M_recv"),
        ]:
            self.assertIn(
                mid, rowids,
                f"message_fts must have a row for {label} (mid={mid}) before tombstone; "
                f"found rowids={rowids!r}. "
                "0005-message-fts.sql and _SCHEMA update not yet implemented.",
            )

    def test_tombstone_deletes_p2_internal_message_fts_row(self):
        """After tombstone_project(P2), the FTS row for M_internal must be gone.

        M_internal was sent BY a P2 address → its FTS text must be destroyed.

        RED: 'no such table: message_fts'.
        """
        # Pre-condition hard assert.
        before = self._fts_rowids()
        self.assertIn(
            self._mid_internal, before,
            f"M_internal (mid={self._mid_internal}) FTS row must exist before tombstone "
            f"(pre-condition); found={before!r}",
        )

        self._s.tombstone_project(self._con, self.P2, self.ADMIN)

        after = self._fts_rowids()
        self.assertNotIn(
            self._mid_internal, after,
            f"FTS row for M_internal (mid={self._mid_internal}) must be gone after "
            f"tombstone_project(P2); still present rowids={after!r}",
        )

    def test_tombstone_deletes_p2_cross_project_message_fts_row(self):
        """After tombstone_project(P2), the FTS row for M_cross (surviving cross-project
        message) must also be gone — body file died with P2's folder.

        RED: 'no such table: message_fts'.
        """
        before = self._fts_rowids()
        self.assertIn(
            self._mid_cross, before,
            f"M_cross (mid={self._mid_cross}) FTS row must exist before tombstone "
            f"(pre-condition); found={before!r}",
        )

        self._s.tombstone_project(self._con, self.P2, self.ADMIN)

        after = self._fts_rowids()
        self.assertNotIn(
            self._mid_cross, after,
            f"FTS row for M_cross (mid={self._mid_cross}) must be gone after "
            f"tombstone_project(P2) — the cross-project message row survives in 'message' "
            f"but its FTS text copy must be destroyed; rowids still present={after!r}",
        )

    def test_tombstone_keeps_p1_sent_message_fts_row(self):
        """After tombstone_project(P2), the FTS row for M_recv (sent BY P1, received
        by P2) must REMAIN — the content belongs to the sender's project (P1).

        RED: 'no such table: message_fts'.
        """
        before = self._fts_rowids()
        self.assertIn(
            self._mid_recv, before,
            f"M_recv (mid={self._mid_recv}) FTS row must exist before tombstone "
            f"(pre-condition); found={before!r}",
        )

        self._s.tombstone_project(self._con, self.P2, self.ADMIN)

        after = self._fts_rowids()
        self.assertIn(
            self._mid_recv, after,
            f"FTS row for M_recv (mid={self._mid_recv}) must REMAIN after tombstone_project(P2); "
            f"P1 sent it — P2 merely received it, so the text is P1's property. "
            f"rowids present={after!r}",
        )

    def test_tombstone_match_for_p2_sent_body_term_returns_nothing(self):
        """After tombstone_project(P2), a MATCH for a term unique to P2-sent bodies
        must return no hits.

        This is the search-path smoke-test: even if raw rowid deletion were wrong,
        the FTS index itself must not return results for destroyed text.

        RED: 'no such table: message_fts'.
        """
        db = self._db_path(self._dh)
        # Pre-condition: both P2-sent terms must match before tombstone.
        before_internal = _fts_match(db, self.INTERNAL_TERM)
        before_cross = _fts_match(db, self.CROSS_TERM)
        self.assertIn(
            self._mid_internal, before_internal,
            f"MATCH '{self.INTERNAL_TERM}' must find M_internal before tombstone "
            f"(pre-condition); got {before_internal!r}",
        )
        self.assertIn(
            self._mid_cross, before_cross,
            f"MATCH '{self.CROSS_TERM}' must find M_cross before tombstone "
            f"(pre-condition); got {before_cross!r}",
        )

        self._s.tombstone_project(self._con, self.P2, self.ADMIN)

        # After tombstone: P2-sent terms must return nothing.
        after_internal = _fts_match(db, self.INTERNAL_TERM)
        self.assertEqual(
            after_internal, [],
            f"MATCH '{self.INTERNAL_TERM}' must return [] after tombstone_project(P2); "
            f"got {after_internal!r}",
        )
        after_cross = _fts_match(db, self.CROSS_TERM)
        self.assertEqual(
            after_cross, [],
            f"MATCH '{self.CROSS_TERM}' must return [] after tombstone_project(P2); "
            f"got {after_cross!r}",
        )

    def test_tombstone_match_for_p1_sent_body_term_still_returns_hit(self):
        """After tombstone_project(P2), a MATCH for M_recv's unique body term must
        still return a hit — that text belongs to P1 and must survive.

        RED: 'no such table: message_fts'.
        """
        db = self._db_path(self._dh)
        self._s.tombstone_project(self._con, self.P2, self.ADMIN)

        after_recv = _fts_match(db, self.RECV_TERM)
        self.assertIn(
            self._mid_recv, after_recv,
            f"MATCH '{self.RECV_TERM}' must still find M_recv after tombstone_project(P2); "
            f"got {after_recv!r}",
        )

    def test_tombstone_fts_deletion_is_computed_before_address_purge(self):
        """tombstone_project must compute the P2-sent FTS rowids BEFORE purging P2's
        address rows (DRIFT-2 ordering).

        We verify this indirectly: if the deletion happened AFTER the address purge,
        the message→address join that classifies 'sent by P2' would fail (no address
        rows) and the FTS rows would survive. The DRIFT-2 contract is proved by the
        FTS rows being gone — not by inspecting internals.

        RED: 'no such table: message_fts'; or if the ordering is wrong, the FTS row
        for M_cross (whose sender row is purged before the FTS delete in a wrong
        implementation) might survive.
        """
        self._s.tombstone_project(self._con, self.P2, self.ADMIN)
        after = self._fts_rowids()

        # Both P2-sent messages' FTS rows must be gone (proving the ordering works).
        self.assertNotIn(
            self._mid_internal, after,
            f"FTS row for M_internal must be gone (DRIFT-2 ordering test); "
            f"rowids present={after!r}",
        )
        self.assertNotIn(
            self._mid_cross, after,
            f"FTS row for M_cross must be gone (DRIFT-2 ordering test); "
            f"rowids present={after!r}",
        )
        # P1-sent FTS row must still be present.
        self.assertIn(
            self._mid_recv, after,
            f"FTS row for M_recv (P1-sent) must remain (DRIFT-2 ordering test); "
            f"rowids present={after!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
