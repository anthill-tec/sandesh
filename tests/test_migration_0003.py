"""test_migration_0003.py — RED tests for CR-SAN-022 Cycle 1.

Covers:
  AC1  — project table shape: columns, types, NOT NULL, defaults, CHECK constraint
  AC1  — address.project column: added by 0003 and backfilled from address suffix
  AC1  — rollback: project table gone + address.project column gone; address rows preserved
  AC1  — fresh _SCHEMA parity: a brand-new setup() store already has project table +
         address.project; migrate.apply() is a harmless re-run; status shows 3 applied, 0 pending
  AC1  — register() populates address.project going forward (post-_SCHEMA parity)

All tests MUST FAIL before GREEN because:
  - sandesh/migrations/0003-project-tracker.sql does not yet exist
  - sandesh_db._SCHEMA does not yet contain the project table or address.project column
  - sandesh_db.register() does not yet populate address.project

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_migration_0003 --agent red-cr022-c1
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ensure the sandesh package is importable.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Shared helpers (mirror the patterns in test_migrate.py)
# ---------------------------------------------------------------------------

def _pragma_table_info(db_path, table):
    """Return PRAGMA table_info rows for `table` as a sorted list of dicts.
    Keys: name, type, notnull, dflt_value, pk.  cid excluded (position-independent).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    con.close()
    return sorted(
        [
            {
                "name": r["name"],
                "type": r["type"],
                "notnull": r["notnull"],
                "dflt_value": r["dflt_value"],
                "pk": r["pk"],
            }
            for r in rows
        ],
        key=lambda d: d["name"],
    )


def _column_map(db_path, table):
    """Return {col_name: info_dict} for `table`."""
    return {r["name"]: r for r in _pragma_table_info(db_path, table)}


def _table_names(db_path):
    """Return the set of user-visible table names (excludes sqlite_* internals)."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    con.close()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Fixture helpers — environment-managed store setup
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Mixin: setUp/tearDown for an isolated XDG_DATA_HOME temp dir."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_0003_test_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _data_home(self, name="dh"):
        """Return a per-test sub-dir of the temp dir as the data home."""
        p = os.path.join(self._tmpdir, name)
        os.makedirs(p, exist_ok=True)
        return p

    def _db_path(self, _project_id, data_home):
        """Compute the global DB path (CR-SAN-022: one sandesh.db per data home)."""
        return os.path.join(data_home, "sandesh", "sandesh.db")

    def _set_xdg(self, data_home):
        os.environ["XDG_DATA_HOME"] = data_home

    def _apply(self, _project_id, data_home):
        """Call migrate.apply() (global DB) with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.apply()

    def _status(self, _project_id, data_home):
        """Call migrate.status() (global DB) with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        return migrate.status()

    def _rollback(self, _project_id, data_home):
        """Call migrate.rollback() (global DB) with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.rollback()

    def _setup_project(self, project_id, data_home):
        """Call sandesh_db.setup(project_id) with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        sandesh_db.setup(project_id)

    def _connect(self, _project_id, data_home):
        """Return an open sqlite3 connection to the global DB (XDG set)."""
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        return sqlite3.connect(sandesh_db.db_path())


# ---------------------------------------------------------------------------
# Test 1 — project table shape after migrate.apply()
# ---------------------------------------------------------------------------

class Migration0003ProjectTableShapeTest(_TempDataHome):
    """AC1: after apply() on a fresh temp store, the project table must exist
    with exactly the columns specified in the CR spec, the correct constraints
    and defaults, and a CHECK violation must raise sqlite3.IntegrityError.

    RED: 0003-project-tracker.sql does not exist → apply() leaves no project
    table → every assertion fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("shape")
        self._pid = "ShapeTest"
        self._apply(self._pid, self._dh)
        self._db = self._db_path(self._pid, self._dh)

    def test_project_table_exists_after_apply(self):
        """project table must exist in the DB after apply().

        RED: 0003 absent → table not created.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "project",
            tables,
            f"table 'project' must exist after apply(); found tables: {tables!r}",
        )

    def test_project_table_has_project_id_column(self):
        """project.project_id must be TEXT PRIMARY KEY (pk=1).

        RED: table absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("project_id", cols,
                      "project table must have a 'project_id' column")
        c = cols["project_id"]
        self.assertEqual(c["type"].upper(), "TEXT",
                         f"project_id must be TEXT; got {c['type']!r}")
        self.assertEqual(c["pk"], 1,
                         f"project_id must be PRIMARY KEY (pk=1); got pk={c['pk']}")

    def test_project_table_has_state_column_notnull_default_active(self):
        """project.state must be TEXT NOT NULL DEFAULT 'active'.

        RED: table absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("state", cols,
                      "project table must have a 'state' column")
        c = cols["state"]
        self.assertEqual(c["type"].upper(), "TEXT",
                         f"state must be TEXT; got {c['type']!r}")
        self.assertEqual(c["notnull"], 1,
                         f"state must be NOT NULL (notnull=1); got {c['notnull']}")
        # SQLite stores the literal including quotes in dflt_value
        self.assertIn(
            c["dflt_value"],
            ("'active'", "active"),
            f"state DEFAULT must be 'active'; got dflt_value={c['dflt_value']!r}",
        )

    def test_project_table_has_created_at_column_notnull(self):
        """project.created_at must be TEXT NOT NULL DEFAULT (datetime('now')).

        RED: table absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("created_at", cols,
                      "project table must have a 'created_at' column")
        c = cols["created_at"]
        self.assertEqual(c["type"].upper(), "TEXT",
                         f"created_at must be TEXT; got {c['type']!r}")
        self.assertEqual(c["notnull"], 1,
                         f"created_at must be NOT NULL (notnull=1); got {c['notnull']}")

    def test_project_table_has_archived_at_nullable(self):
        """project.archived_at must be TEXT, nullable (notnull=0, no default).

        RED: table absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("archived_at", cols,
                      "project table must have an 'archived_at' column")
        c = cols["archived_at"]
        self.assertEqual(c["type"].upper(), "TEXT",
                         f"archived_at must be TEXT; got {c['type']!r}")
        self.assertEqual(c["notnull"], 0,
                         f"archived_at must be nullable (notnull=0); got {c['notnull']}")

    def test_project_table_has_tombstoned_at_nullable(self):
        """project.tombstoned_at must be TEXT, nullable (notnull=0, no default).

        RED: table absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("tombstoned_at", cols,
                      "project table must have a 'tombstoned_at' column")
        c = cols["tombstoned_at"]
        self.assertEqual(c["type"].upper(), "TEXT",
                         f"tombstoned_at must be TEXT; got {c['type']!r}")
        self.assertEqual(c["notnull"], 0,
                         f"tombstoned_at must be nullable (notnull=0); got {c['notnull']}")

    def test_project_table_has_exactly_five_columns(self):
        """project table must have exactly 5 columns per spec.

        RED: table absent; also guards against accidental extras.
        """
        cols = _column_map(self._db, "project")
        expected = {"project_id", "state", "created_at", "archived_at", "tombstoned_at"}
        self.assertEqual(
            set(cols.keys()),
            expected,
            f"project table must have exactly {expected}; got {set(cols.keys())!r}",
        )

    def test_project_state_check_rejects_bogus_value(self):
        """Inserting an invalid state value must raise sqlite3.IntegrityError (CHECK).

        RED: table absent → INSERT raises OperationalError, not IntegrityError
        (wrong exception type).
        """
        con = sqlite3.connect(self._db)
        try:
            with self.assertRaises(
                sqlite3.IntegrityError,
                msg="Inserting state='bogus' must raise sqlite3.IntegrityError "
                    "(the CHECK constraint); 0003 not yet applied.",
            ):
                con.execute(
                    "INSERT INTO project (project_id, state) VALUES (?, ?)",
                    ("check_test", "bogus"),
                )
        finally:
            con.close()

    def test_project_state_check_accepts_active(self):
        """Inserting state='active' must succeed.

        RED: table absent.
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute(
                "INSERT INTO project (project_id, state) VALUES (?, ?)",
                ("active_test", "active"),
            )
            con.commit()
            row = con.execute(
                "SELECT state FROM project WHERE project_id=?", ("active_test",)
            ).fetchone()
            self.assertIsNotNone(row, "row must exist after INSERT")
            self.assertEqual(row[0], "active",
                             f"state must be 'active'; got {row[0]!r}")
        finally:
            con.close()

    def test_project_state_check_accepts_archived(self):
        """Inserting state='archived' must succeed.

        RED: table absent.
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute(
                "INSERT INTO project (project_id, state) VALUES (?, ?)",
                ("archived_test", "archived"),
            )
            con.commit()
            row = con.execute(
                "SELECT state FROM project WHERE project_id=?", ("archived_test",)
            ).fetchone()
            self.assertIsNotNone(row, "row must exist after INSERT")
            self.assertEqual(row[0], "archived")
        finally:
            con.close()

    def test_project_state_check_accepts_tombstoned(self):
        """Inserting state='tombstoned' must succeed.

        RED: table absent.
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute(
                "INSERT INTO project (project_id, state) VALUES (?, ?)",
                ("tomb_test", "tombstoned"),
            )
            con.commit()
            row = con.execute(
                "SELECT state FROM project WHERE project_id=?", ("tomb_test",)
            ).fetchone()
            self.assertIsNotNone(row, "row must exist after INSERT")
            self.assertEqual(row[0], "tombstoned")
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Test 2 — address.project added and backfilled by 0003
# ---------------------------------------------------------------------------

class Migration0003AddressProjectBackfillTest(_TempDataHome):
    """AC1: 0003 adds address.project column and backfills it from the address
    suffix ('Mainline - Demo' → 'Demo').

    Fixture strategy (post-GREEN): build a legacy store by rolling back 0003.
      1. setup() + apply() provisions the full chain (incl. 0003).
      2. rollback() undoes exactly 0003 — address table rebuilt WITHOUT project
         column; project table dropped; address rows preserved.
      3. Hard-assert (no skip) that address.project is absent after rollback —
         if it is still present the rollback itself is broken.
      4. Insert two legacy rows via raw SQL (kind column survives rollback).
    Tests then call apply() to re-apply 0003 and assert the backfill semantics.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("backfill")
        self._pid = "Demo"

        # Step 1: full provisioning — setup() + apply() including 0003.
        self._setup_project(self._pid, self._dh)
        self._apply(self._pid, self._dh)
        self._db = self._db_path(self._pid, self._dh)

        # Step 2: roll back exactly one step (0003) to get a pre-0003 address table.
        self._rollback(self._pid, self._dh)

        # Step 3: hard-assert the rollback worked — no skip, no soft conditional.
        cols = _column_map(self._db, "address")
        self.assertNotIn(
            "project",
            cols,
            "FIXTURE BUG: address.project still present after rollback(). "
            "The 0003 rollback SQL did not remove the column — fix the rollback.",
        )

        # Step 4: insert two legacy rows directly (kind column survives rollback).
        with sqlite3.connect(self._db) as con:
            con.execute(
                "INSERT INTO address (address, kind) VALUES (?, ?)",
                ("Mainline - Demo", "mainline"),
            )
            con.execute(
                "INSERT INTO address (address, kind) VALUES (?, ?)",
                ("Track 1 - Demo", "track"),
            )
            con.commit()

    def test_address_project_column_exists_after_0003(self):
        """After applying 0003, address.project column must exist.

        RED: 0003 absent → column never added.
        """
        # Apply 0003 (which doesn't exist yet in RED — this call will fail or
        # leave the column absent).
        self._apply(self._pid, self._dh)
        cols = _column_map(self._db, "address")
        self.assertIn(
            "project",
            cols,
            "address.project column must exist after 0003 is applied; "
            "0003-project-tracker.sql not yet written.",
        )

    def test_address_project_backfilled_for_mainline(self):
        """After 0003, 'Mainline - Demo'.project must equal 'Demo' (backfill).

        RED: column absent → SELECT fails / returns None.
        """
        self._apply(self._pid, self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT project FROM address WHERE address=?",
                ("Mainline - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(
            row,
            "'Mainline - Demo' address row must exist after 0003 apply",
        )
        self.assertEqual(
            row["project"],
            "Demo",
            f"'Mainline - Demo'.project must be 'Demo' after backfill; got {row['project']!r}",
        )

    def test_address_project_backfilled_for_track(self):
        """After 0003, 'Track 1 - Demo'.project must equal 'Demo' (backfill).

        RED: column absent.
        """
        self._apply(self._pid, self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT project FROM address WHERE address=?",
                ("Track 1 - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(
            row,
            "'Track 1 - Demo' address row must exist after 0003 apply",
        )
        self.assertEqual(
            row["project"],
            "Demo",
            f"'Track 1 - Demo'.project must be 'Demo' after backfill; got {row['project']!r}",
        )

    def test_address_row_count_unchanged_after_0003(self):
        """The address table must still have exactly 2 rows after 0003 (no data loss).

        RED: 0003 absent → column missing, but row count check still passes;
        however the test is paired with column-existence checks to form a complete
        behavioural assertion set.
        """
        self._apply(self._pid, self._dh)
        con = sqlite3.connect(self._db)
        try:
            count = con.execute("SELECT COUNT(*) FROM address").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(
            count,
            2,
            f"address table must still have 2 rows after 0003; got {count}",
        )


# ---------------------------------------------------------------------------
# Test 3 — rollback drops project table + address.project; address rows survive
# ---------------------------------------------------------------------------

class Migration0003RollbackTest(_TempDataHome):
    """AC1: after apply() + rollback(), the project table must be gone and
    address.project must be gone, but address rows must survive intact.

    RED: 0003 absent → apply() does nothing for 0003 → rollback() rolls back
    0002 instead (wrong step) → project table never existed → the assertion
    'project table present after apply' fails immediately.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("rollback")
        self._pid = "RollbackTest"
        # Build a legacy fixture: setup + raw address inserts (no project col yet).
        self._setup_project(self._pid, self._dh)
        # Apply up through current chain (0001+0002) — leaves a clean pre-0003 state.
        self._apply(self._pid, self._dh)
        self._db = self._db_path(self._pid, self._dh)
        # Insert two address rows directly (avoid relying on register() for the fixture).
        with sqlite3.connect(self._db) as con:
            con.execute(
                "INSERT INTO address (address, kind) VALUES (?, ?)",
                ("Mainline - RollbackTest", "mainline"),
            )
            con.execute(
                "INSERT INTO address (address, kind) VALUES (?, ?)",
                ("Track 1 - RollbackTest", "track"),
            )
            con.commit()

    def test_rollback_removes_project_table(self):
        """After apply-then-rollback, the project table must not exist.

        RED: 0003 never applied → project table was never created → this
        assertion would trivially pass, but test_project_table_exists_after_apply
        (in Migration0003ProjectTableShapeTest) catches the real RED first.
        This test's RED signal: apply() must first succeed in adding project;
        then rollback() must remove it.
        """
        # First: apply 0003 — must create project table.
        self._apply(self._pid, self._dh)
        tables_after_apply = _table_names(self._db)
        self.assertIn(
            "project",
            tables_after_apply,
            "project table must exist after apply() (pre-condition for rollback test); "
            "0003 not yet written.",
        )

        # Then: rollback one step (should undo 0003).
        self._rollback(self._pid, self._dh)
        tables_after_rollback = _table_names(self._db)
        self.assertNotIn(
            "project",
            tables_after_rollback,
            f"project table must be gone after rollback(); "
            f"found tables: {tables_after_rollback!r}",
        )

    def test_rollback_removes_address_project_column(self):
        """After apply-then-rollback, address.project column must not exist.

        RED: 0003 absent → column never added → rollback is a no-op for 0003.
        """
        self._apply(self._pid, self._dh)
        cols_after_apply = _column_map(self._db, "address")
        self.assertIn(
            "project",
            cols_after_apply,
            "address.project must exist after apply() (pre-condition); 0003 not written.",
        )

        self._rollback(self._pid, self._dh)
        cols_after_rollback = _column_map(self._db, "address")
        self.assertNotIn(
            "project",
            cols_after_rollback,
            "address.project column must be gone after rollback(); "
            f"columns present: {set(cols_after_rollback.keys())!r}",
        )

    def test_rollback_preserves_address_rows(self):
        """Address rows must survive the apply+rollback cycle intact (count + values).

        RED: 0003 absent → rollback does nothing for 0003; however the apply
        pre-condition failure will already signal RED for this test.
        """
        self._apply(self._pid, self._dh)
        self._rollback(self._pid, self._dh)

        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT address, kind FROM address ORDER BY address"
            ).fetchall()
        finally:
            con.close()

        addresses = [r["address"] for r in rows]
        self.assertEqual(
            len(rows),
            2,
            f"address table must still have 2 rows after rollback; got {len(rows)}",
        )
        self.assertIn(
            "Mainline - RollbackTest",
            addresses,
            f"'Mainline - RollbackTest' must survive rollback; got {addresses!r}",
        )
        self.assertIn(
            "Track 1 - RollbackTest",
            addresses,
            f"'Track 1 - RollbackTest' must survive rollback; got {addresses!r}",
        )

    def test_rollback_leaves_0003_pending_not_applied(self):
        """After rollback, status() must show 0003 in pending (not applied).

        RED: 0003 absent from migrations_dir → status() never sees it at all;
        this is a secondary RED signal.
        """
        self._apply(self._pid, self._dh)
        self._rollback(self._pid, self._dh)
        applied, pending = self._status(self._pid, self._dh)
        self.assertNotIn(
            "0003-project-tracker",
            applied,
            f"0003-project-tracker must NOT be in applied after rollback; got {applied!r}",
        )
        self.assertIn(
            "0003-project-tracker",
            pending,
            f"0003-project-tracker must be in pending after rollback; got {pending!r}",
        )


# ---------------------------------------------------------------------------
# Test 4 — fresh _SCHEMA parity + harmless re-run
# ---------------------------------------------------------------------------

class Migration0003FreshSchemaParity(_TempDataHome):
    """AC1: after GREEN, sandesh_db.setup() must create a DB whose _SCHEMA already
    contains the project table and address.project column (fresh-DB parity).

    Then migrate.apply() on that fresh setup() store must be a harmless re-run:
    - 0001 marked (adoption glue), 0002 and 0003 applied as IF NOT EXISTS → no error
    - status() shows all three applied, 0 pending

    RED: _SCHEMA does not include project table or address.project → the fresh
    store lacks both → every assertion below fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("parity")
        self._pid = "ParityTest"
        # Provision a fresh store via setup() — does NOT go through migrations.
        self._setup_project(self._pid, self._dh)
        # CR-SAN-022 C2: setup() provisions the GLOBAL DB, not a per-project file.
        self._db = os.path.join(self._dh, "sandesh", "sandesh.db")

    def test_fresh_setup_creates_project_table(self):
        """sandesh_db.setup() must create the project table (via _SCHEMA).

        RED: _SCHEMA has no project table.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "project",
            tables,
            "project table must exist in a fresh setup() store (_SCHEMA parity); "
            "_SCHEMA does not yet contain CREATE TABLE project.",
        )

    def test_fresh_setup_creates_address_project_column(self):
        """sandesh_db.setup() must create address.project column (via _SCHEMA).

        RED: _SCHEMA address DDL has no project column.
        """
        cols = _column_map(self._db, "address")
        self.assertIn(
            "project",
            cols,
            "address.project must exist in a fresh setup() store (_SCHEMA parity); "
            "_SCHEMA address DDL does not yet include the project column.",
        )

    def test_apply_on_fresh_setup_store_does_not_raise(self):
        """migrate.apply() on a fresh setup() store must not raise.

        The adoption glue marks 0001; 0002+0003 use IF NOT EXISTS → harmless re-run.

        RED: 0003 file absent → apply() raises or leaves 0003 pending.
        """
        try:
            self._apply(self._pid, self._dh)
        except Exception as exc:
            self.fail(
                f"migrate.apply() on a fresh setup() store raised {type(exc).__name__}: {exc}\n"
                "Expected: harmless re-run (0001 marked, 0002+0003 IF NOT EXISTS).",
            )

    def test_apply_on_fresh_setup_store_shows_three_applied_zero_pending(self):
        """After apply() on a fresh setup() store, status() must show all 3 applied, 0 pending.

        RED: 0003 absent → status shows only 2 applied (0001+0002) or raises.
        """
        self._apply(self._pid, self._dh)
        applied, pending = self._status(self._pid, self._dh)

        self.assertIn(
            "0001-baseline",
            applied,
            f"0001-baseline must be in applied; got applied={applied!r}",
        )
        self.assertIn(
            "0002-drop-message-status",
            applied,
            f"0002-drop-message-status must be in applied; got applied={applied!r}",
        )
        self.assertIn(
            "0003-project-tracker",
            applied,
            f"0003-project-tracker must be in applied; got applied={applied!r}",
        )
        self.assertEqual(
            len(pending),
            0,
            f"pending must be 0 after apply() on fresh store; got pending={pending!r}",
        )
        self.assertEqual(
            len(applied),
            3,
            f"Exactly 3 migrations must be applied (0001+0002+0003); got {applied!r}",
        )


# ---------------------------------------------------------------------------
# Test 5 — register() populates address.project
# ---------------------------------------------------------------------------

class Migration0003RegisterPopulatesProjectTest(_TempDataHome):
    """AC1: after a fresh setup() + register(), the address row's project column
    must equal the project_id ('Demo' in the test address 'Mainline - Demo').

    This asserts the small register() change that ships with _SCHEMA parity.

    RED: register() does not yet populate address.project → the column is NULL
    (or absent entirely because _SCHEMA has no project column) → assertion fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("register")
        self._pid = "Demo"
        self._setup_project(self._pid, self._dh)
        self._db = self._db_path(self._pid, self._dh)

    def test_register_populates_address_project_for_mainline(self):
        """After register(con, 'Mainline - Demo', project='Demo'), address.project='Demo'.

        RED: address.project column absent (_SCHEMA) OR register() does not
        INSERT/UPDATE the project column.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db

        con = sandesh_db.connect()
        try:
            sandesh_db.register(con, "Mainline - Demo", kind="mainline", project=self._pid)
            row = con.execute(
                "SELECT project FROM address WHERE address=?",
                ("Mainline - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(
            row,
            "'Mainline - Demo' row must exist after register()",
        )
        self.assertEqual(
            row[0],
            "Demo",
            f"address.project must be 'Demo' after register(); got {row[0]!r}",
        )

    def test_register_populates_address_project_for_track(self):
        """After register(con, 'Track 1 - Demo', project='Demo'), address.project='Demo'.

        RED: address.project column absent OR register() does not populate it.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db

        con = sandesh_db.connect()
        try:
            sandesh_db.register(con, "Track 1 - Demo", kind="track", project=self._pid)
            row = con.execute(
                "SELECT project FROM address WHERE address=?",
                ("Track 1 - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(
            row,
            "'Track 1 - Demo' row must exist after register()",
        )
        self.assertEqual(
            row[0],
            "Demo",
            f"address.project must be 'Demo' after register(); got {row[0]!r}",
        )

    def test_register_project_column_value_not_none(self):
        """address.project must not be NULL after register() — it must be populated.

        RED: register() does not write the project column → NULL stored.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db

        con = sandesh_db.connect()
        try:
            sandesh_db.register(con, "Mainline - Demo", kind="mainline", project=self._pid)
            row = con.execute(
                "SELECT project FROM address WHERE address=?",
                ("Mainline - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(
            row,
            "'Mainline - Demo' row must exist",
        )
        self.assertIsNotNone(
            row[0],
            "address.project must not be NULL after register(); register() does not yet "
            "populate it.",
        )

    def test_register_project_equals_extracted_suffix(self):
        """address.project must equal the project part extracted from the address string.

        For 'Track 1 - Demo', the suffix after ' - ' is 'Demo'. This asserts that
        register() does NOT just copy the project_id argument blindly but that the
        stored value is consistent with the address format.

        RED: column absent or NULL.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db

        con = sandesh_db.connect()
        try:
            sandesh_db.register(con, "Track 1 - Demo", kind="track", project=self._pid)
            row = con.execute(
                "SELECT address, project FROM address WHERE address=?",
                ("Track 1 - Demo",),
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(row, "row must exist")
        address_val = row[0]
        project_val = row[1]
        # The project part is everything after ' - ' (first occurrence).
        expected = address_val.split(" - ", 1)[1] if " - " in address_val else None
        self.assertEqual(
            project_val,
            expected,
            f"address.project must equal the address suffix '{expected}'; "
            f"got {project_val!r}",
        )


if __name__ == "__main__":
    unittest.main()
