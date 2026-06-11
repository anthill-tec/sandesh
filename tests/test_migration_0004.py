"""test_migration_0004.py — RED tests for CR-SAN-023 Cycle 1.

Covers:
  AC9  — 0004-xproj-grant migration:
           * shape after apply: project table gains xproj_granted_at + xproj_granted_by
             columns; admin table created with exact DDL (id CHECK(id=1), name NOT NULL,
             assigned_at NOT NULL DEFAULT datetime('now'))
           * single-row enforcement: second INSERT to admin raises sqlite3.IntegrityError
             (CHECK + PK)
           * pre-0004 legacy fixture (rollback trick) + preserved rows after re-apply
           * rollback: columns gone, admin table gone, project data rows preserved
           * fresh-_SCHEMA parity + harmless re-run (apply on a setup() DB succeeds;
             4 migrations applied, 0 pending)
           * snapshot equality: dump_schema() == current-schema.json on a
             fully-migrated store (FAILS now — committed snapshot lacks admin table +
             xproj columns)
  AC10 (schema part) — admin table CHECK(id=1) enforces single row at the DB level;
           INSERT (id=1) succeeds; INSERT (id=2) raises IntegrityError; duplicate
           id=1 raises IntegrityError.

All tests MUST FAIL at RED because:
  - sandesh/migrations/0004-xproj-grant.sql does not yet exist
  - sandesh_db._SCHEMA does not yet include xproj_granted_at / xproj_granted_by on
    project, nor the admin table
  - sandesh/schema/current-schema.json does not yet include admin or the new columns

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_migration_0004 --agent red-cr023-c1
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
# Shared helpers (mirroring the patterns in test_migration_0003.py)
# ---------------------------------------------------------------------------

def _pragma_table_info(db_path, table):
    """Return PRAGMA table_info rows for `table` as a sorted list of dicts.
    Keys: name, type, notnull, dflt_value, pk.  cid excluded (position-independent).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
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
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Fixture helpers — environment-managed store setup (mirror 0003 pattern)
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Mixin: setUp/tearDown for an isolated XDG_DATA_HOME temp dir."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_0004_test_")
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

    def _db_path(self, data_home):
        """Compute the global DB path (one sandesh.db per data home)."""
        return os.path.join(data_home, "sandesh", "sandesh.db")

    def _set_xdg(self, data_home):
        os.environ["XDG_DATA_HOME"] = data_home

    def _apply(self, data_home):
        """Call migrate.apply() with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.apply()

    def _status(self, data_home):
        """Call migrate.status() with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        return migrate.status()

    def _rollback(self, data_home):
        """Call migrate.rollback() with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        migrate.rollback()

    def _setup_project(self, project_id, data_home):
        """Call sandesh_db.setup(project_id) with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        sandesh_db.setup(project_id)

    def _connect(self, data_home):
        """Return an open sqlite3 conn to the global DB (XDG set)."""
        self._set_xdg(data_home)
        from sandesh import sandesh_db
        return sqlite3.connect(sandesh_db.db_path())

    def _dump_schema(self, data_home):
        """Call migrate.dump_schema() with XDG_DATA_HOME set."""
        self._set_xdg(data_home)
        from sandesh import migrate
        return migrate.dump_schema()


# ---------------------------------------------------------------------------
# Test 1 — shape after apply: xproj columns on project + admin table created
# ---------------------------------------------------------------------------

class Migration0004ShapeAfterApplyTest(_TempDataHome):
    """AC9: after apply() on a fresh temp store the project table must have
    xproj_granted_at (TEXT, nullable, NULL default) and xproj_granted_by (TEXT,
    nullable), and the admin table must exist with exactly id (INTEGER PK),
    name (TEXT NOT NULL), assigned_at (TEXT NOT NULL DEFAULT datetime('now')).
    status() shows 0004-xproj-grant applied, 0 pending.

    RED: 0004-xproj-grant.sql does not exist → apply() leaves neither the
    columns nor the admin table → every assertion fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("shape")
        self._pid = "ShapeTest"
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

    # --- project.xproj_granted_at ---

    def test_project_has_xproj_granted_at_column(self):
        """project.xproj_granted_at must exist after apply().

        RED: 0004 absent → column not added.
        """
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_at",
            cols,
            "project table must have xproj_granted_at after 0004 is applied; "
            "0004-xproj-grant.sql does not yet exist.",
        )

    def test_xproj_granted_at_is_text_type(self):
        """project.xproj_granted_at must be TEXT.

        RED: column absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("xproj_granted_at", cols,
                      "project.xproj_granted_at column missing after apply()")
        c = cols["xproj_granted_at"]
        self.assertEqual(
            c["type"].upper(), "TEXT",
            f"xproj_granted_at must be TEXT; got {c['type']!r}",
        )

    def test_xproj_granted_at_is_nullable(self):
        """project.xproj_granted_at must be nullable (notnull=0, NULL default).

        NULL = not granted (spec: NULL = not granted).
        RED: column absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("xproj_granted_at", cols,
                      "project.xproj_granted_at column missing after apply()")
        c = cols["xproj_granted_at"]
        self.assertEqual(
            c["notnull"], 0,
            f"xproj_granted_at must be nullable (notnull=0); got notnull={c['notnull']}",
        )
        self.assertIsNone(
            c["dflt_value"],
            f"xproj_granted_at default must be NULL; got dflt_value={c['dflt_value']!r}",
        )

    # --- project.xproj_granted_by ---

    def test_project_has_xproj_granted_by_column(self):
        """project.xproj_granted_by must exist after apply().

        RED: 0004 absent → column not added.
        """
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_by",
            cols,
            "project table must have xproj_granted_by after 0004 is applied; "
            "0004-xproj-grant.sql does not yet exist.",
        )

    def test_xproj_granted_by_is_text_type(self):
        """project.xproj_granted_by must be TEXT.

        RED: column absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("xproj_granted_by", cols,
                      "project.xproj_granted_by column missing after apply()")
        c = cols["xproj_granted_by"]
        self.assertEqual(
            c["type"].upper(), "TEXT",
            f"xproj_granted_by must be TEXT; got {c['type']!r}",
        )

    def test_xproj_granted_by_is_nullable(self):
        """project.xproj_granted_by must be nullable (notnull=0, NULL default).

        RED: column absent.
        """
        cols = _column_map(self._db, "project")
        self.assertIn("xproj_granted_by", cols,
                      "project.xproj_granted_by column missing after apply()")
        c = cols["xproj_granted_by"]
        self.assertEqual(
            c["notnull"], 0,
            f"xproj_granted_by must be nullable (notnull=0); got notnull={c['notnull']}",
        )
        self.assertIsNone(
            c["dflt_value"],
            f"xproj_granted_by default must be NULL; got dflt_value={c['dflt_value']!r}",
        )

    # --- admin table existence ---

    def test_admin_table_exists_after_apply(self):
        """admin table must exist after apply().

        RED: 0004 absent → table not created.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "admin",
            tables,
            f"admin table must exist after apply(); found tables: {sorted(tables)!r}",
        )

    # --- admin table column: id ---

    def test_admin_table_has_id_column_integer_pk(self):
        """admin.id must be INTEGER PRIMARY KEY (pk=1).

        RED: table absent.
        """
        cols = _column_map(self._db, "admin")
        self.assertIn("id", cols,
                      "admin table must have an 'id' column")
        c = cols["id"]
        self.assertEqual(
            c["type"].upper(), "INTEGER",
            f"admin.id must be INTEGER; got {c['type']!r}",
        )
        self.assertEqual(
            c["pk"], 1,
            f"admin.id must be PRIMARY KEY (pk=1); got pk={c['pk']}",
        )

    # --- admin table column: name ---

    def test_admin_table_has_name_column_text_notnull(self):
        """admin.name must be TEXT NOT NULL.

        RED: table absent.
        """
        cols = _column_map(self._db, "admin")
        self.assertIn("name", cols,
                      "admin table must have a 'name' column")
        c = cols["name"]
        self.assertEqual(
            c["type"].upper(), "TEXT",
            f"admin.name must be TEXT; got {c['type']!r}",
        )
        self.assertEqual(
            c["notnull"], 1,
            f"admin.name must be NOT NULL (notnull=1); got notnull={c['notnull']}",
        )

    # --- admin table column: assigned_at ---

    def test_admin_table_has_assigned_at_column_text_notnull_default_datetime(self):
        """admin.assigned_at must be TEXT NOT NULL DEFAULT (datetime('now')).

        RED: table absent.
        """
        cols = _column_map(self._db, "admin")
        self.assertIn("assigned_at", cols,
                      "admin table must have an 'assigned_at' column")
        c = cols["assigned_at"]
        self.assertEqual(
            c["type"].upper(), "TEXT",
            f"admin.assigned_at must be TEXT; got {c['type']!r}",
        )
        self.assertEqual(
            c["notnull"], 1,
            f"admin.assigned_at must be NOT NULL (notnull=1); got notnull={c['notnull']}",
        )
        self.assertIsNotNone(
            c["dflt_value"],
            "admin.assigned_at must have a DEFAULT (datetime('now')); dflt_value is NULL",
        )
        self.assertIn(
            "datetime",
            (c["dflt_value"] or "").lower(),
            f"admin.assigned_at DEFAULT must reference datetime('now'); "
            f"got dflt_value={c['dflt_value']!r}",
        )

    # --- admin table: exactly 3 columns ---

    def test_admin_table_has_exactly_three_columns(self):
        """admin table must have exactly 3 columns: id, name, assigned_at.

        RED: table absent; also guards against accidental extras.
        """
        cols = _column_map(self._db, "admin")
        expected = {"id", "name", "assigned_at"}
        self.assertEqual(
            set(cols.keys()),
            expected,
            f"admin table must have exactly {expected}; got {set(cols.keys())!r}",
        )

    # --- status after apply ---

    def test_status_shows_0004_applied_zero_pending(self):
        """After apply(), 0004-xproj-grant must be in applied, 0 pending.

        RED: 0004 file absent → 0004-xproj-grant never in applied.
        """
        applied, pending = self._status(self._dh)
        self.assertIn(
            "0004-xproj-grant",
            applied,
            f"0004-xproj-grant must be in applied after apply(); got applied={applied!r}",
        )
        self.assertEqual(
            len(pending), 0,
            f"pending must be 0 after apply(); got pending={pending!r}",
        )

    def test_status_shows_all_four_applied(self):
        """After apply(), all four migrations must be applied.

        RED: 0004 absent → only 3 applied.
        """
        applied, pending = self._status(self._dh)
        for mid in ("0001-baseline", "0002-drop-message-status",
                    "0003-project-tracker", "0004-xproj-grant"):
            self.assertIn(
                mid, applied,
                f"{mid} must be in applied after apply(); got applied={applied!r}",
            )
        self.assertEqual(
            len(applied), 4,
            f"Exactly 4 migrations must be applied; got applied={applied!r}",
        )


# ---------------------------------------------------------------------------
# Test 2 — single-row enforcement: CHECK(id=1) + PK
# ---------------------------------------------------------------------------

class Migration0004AdminSingleRowEnforcementTest(_TempDataHome):
    """AC10 (schema part): the admin table enforces exactly one row via
    CHECK(id=1) and PRIMARY KEY. A second INSERT with id=2 must raise
    sqlite3.IntegrityError; a duplicate id=1 must also raise.

    RED: admin table absent → all inserts fail with OperationalError (wrong exception).
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("single_row")
        self._pid = "SingleRowTest"
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

    def test_admin_insert_id1_succeeds(self):
        """INSERT INTO admin (id, name) VALUES (1, 'ops') must succeed.

        RED: admin table absent → OperationalError.
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
            con.commit()
            row = con.execute("SELECT id, name FROM admin WHERE id=1").fetchone()
            self.assertIsNotNone(row, "admin row (id=1) must exist after INSERT")
            self.assertEqual(row[0], 1, f"id must be 1; got {row[0]!r}")
            self.assertEqual(row[1], "ops", f"name must be 'ops'; got {row[1]!r}")
        finally:
            con.close()

    def test_admin_second_insert_id2_raises_integrity_error(self):
        """A second INSERT with id=2 must raise sqlite3.IntegrityError (CHECK(id=1)).

        The CHECK constraint `CHECK (id = 1)` rejects any id other than 1.
        RED: admin table absent → INSERT raises OperationalError (wrong type).
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
            con.commit()
            with self.assertRaises(
                sqlite3.IntegrityError,
                msg="INSERT (id=2) must raise sqlite3.IntegrityError due to CHECK(id=1)",
            ):
                con.execute("INSERT INTO admin (id, name) VALUES (2, 'other')")
                con.commit()
        finally:
            con.close()

    def test_admin_duplicate_id1_raises_integrity_error(self):
        """A second INSERT with id=1 (same PK) must raise sqlite3.IntegrityError.

        RED: admin table absent → INSERT raises OperationalError (wrong type).
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
            con.commit()
            with self.assertRaises(
                sqlite3.IntegrityError,
                msg="Duplicate INSERT (id=1) must raise sqlite3.IntegrityError (PK)",
            ):
                con.execute("INSERT INTO admin (id, name) VALUES (1, 'second')")
                con.commit()
        finally:
            con.close()

    def test_admin_row_count_is_one_after_single_insert(self):
        """After a single INSERT, admin must have exactly 1 row.

        RED: table absent.
        """
        con = sqlite3.connect(self._db)
        try:
            con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
            con.commit()
            count = con.execute("SELECT COUNT(*) FROM admin").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(
            count, 1,
            f"admin must have exactly 1 row after one INSERT; got {count}",
        )

    def test_admin_check_constraint_message_is_integrity_not_operational(self):
        """Inserting id=2 raises IntegrityError specifically (not OperationalError).

        Verifies the error class precisely — OperationalError means the table
        doesn't exist; IntegrityError means the CHECK fired correctly.
        RED: table absent → OperationalError, assertRaises(IntegrityError) fails.
        """
        con = sqlite3.connect(self._db)
        try:
            raised_type = None
            try:
                con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
                con.commit()
                con.execute("INSERT INTO admin (id, name) VALUES (2, 'bad')")
                con.commit()
            except sqlite3.IntegrityError:
                raised_type = sqlite3.IntegrityError
            except sqlite3.OperationalError:
                raised_type = sqlite3.OperationalError
        finally:
            con.close()
        self.assertIs(
            raised_type,
            sqlite3.IntegrityError,
            f"Expected sqlite3.IntegrityError from CHECK(id=1) violation; "
            f"got {raised_type!r}. "
            "If OperationalError: the admin table doesn't exist (0004 not applied). "
            "If None: the CHECK constraint is missing from the DDL.",
        )


# ---------------------------------------------------------------------------
# Test 3 — pre-0004 legacy fixture + preserved rows after re-apply
# ---------------------------------------------------------------------------

class Migration0004LegacyFixturePreservedRowsTest(_TempDataHome):
    """AC9: build a pre-0004 legacy store via the rollback trick:
      1. setup() + apply() (full chain including 0004).
      2. rollback() once → 0004 undone; hard-assert columns + admin table are gone.
      3. Seed a project row with state='active' + an address row.
      4. re-apply() → xproj columns present, existing project row intact
         (state preserved), xproj_granted_at IS NULL.

    RED: 0004 absent → apply() leaves no columns/admin; rollback removes the
    wrong step; assertions fail immediately.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("legacy")
        self._pid = "LegacyTest"

        # Step 1: full provisioning — setup() + apply() including 0004.
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

        # Step 2: roll back exactly one step (0004) to get a pre-0004 project table.
        self._rollback(self._dh)

        # Step 3 (hard pre-condition check — NO skip, hard assert):
        # xproj columns must be gone after rollback; admin table must be gone.
        cols_after_rollback = _column_map(self._db, "project")
        self.assertNotIn(
            "xproj_granted_at",
            cols_after_rollback,
            "FIXTURE BUG: xproj_granted_at still present in project table after "
            "rollback(). The 0004 rollback SQL did not remove the column — fix the rollback.",
        )
        self.assertNotIn(
            "xproj_granted_by",
            cols_after_rollback,
            "FIXTURE BUG: xproj_granted_by still present in project table after "
            "rollback(). The 0004 rollback SQL did not remove the column — fix the rollback.",
        )
        tables_after_rollback = _table_names(self._db)
        self.assertNotIn(
            "admin",
            tables_after_rollback,
            "FIXTURE BUG: admin table still present after rollback(). "
            "The 0004 rollback SQL must DROP the admin table.",
        )

        # Step 4: seed a project row with state='active' and an address row into
        # the pre-0004 store (direct SQL — avoids register() for fixture isolation).
        with sqlite3.connect(self._db) as con:
            # project table already has P1 from setup(); we add a second one directly.
            con.execute(
                "INSERT OR IGNORE INTO project (project_id, state) VALUES (?, 'active')",
                ("P_legacy",),
            )
            con.execute(
                "INSERT OR IGNORE INTO address (address, kind, project) VALUES (?,?,?)",
                ("Mainline - P_legacy", "mainline", "P_legacy"),
            )
            con.commit()

    def test_xproj_granted_at_present_after_reapply(self):
        """After re-apply(), xproj_granted_at must be in the project table.

        RED: 0004 absent → column never added.
        """
        self._apply(self._dh)
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_at",
            cols,
            "xproj_granted_at must be in project table after re-apply(); "
            "0004-xproj-grant.sql not yet written.",
        )

    def test_xproj_granted_by_present_after_reapply(self):
        """After re-apply(), xproj_granted_by must be in the project table.

        RED: 0004 absent → column never added.
        """
        self._apply(self._dh)
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_by",
            cols,
            "xproj_granted_by must be in project table after re-apply(); "
            "0004-xproj-grant.sql not yet written.",
        )

    def test_existing_project_row_preserved_after_reapply(self):
        """The pre-0004 project row (state='active') must survive re-apply.

        RED: migration absent → re-apply has no effect; but the row should still
        be there — this test confirms data is not wiped by the 12-step rebuild.
        """
        self._apply(self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT state FROM project WHERE project_id=?", ("P_legacy",)
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(
            row,
            "P_legacy project row must still exist after re-apply() (data not wiped by rebuild)",
        )
        self.assertEqual(
            row["state"],
            "active",
            f"P_legacy.state must be 'active' after re-apply; got {row['state']!r}",
        )

    def test_xproj_granted_at_is_null_for_existing_row_after_reapply(self):
        """After re-apply(), the existing project row's xproj_granted_at must be NULL.

        NULL = not granted (the migration adds a nullable column; existing rows
        get NULL by default).
        RED: column absent → SELECT fails.
        """
        self._apply(self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT xproj_granted_at FROM project WHERE project_id=?",
                ("P_legacy",),
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(
            row,
            "P_legacy project row must exist after re-apply()",
        )
        self.assertIsNone(
            row["xproj_granted_at"],
            f"xproj_granted_at must be NULL for existing rows after re-apply; "
            f"got {row['xproj_granted_at']!r}",
        )

    def test_address_row_preserved_after_reapply(self):
        """The address row seeded before re-apply must survive the migration.

        RED: migration absent → address table unchanged; row should survive.
        This is a cross-check that the 12-step rebuild (if used for project)
        does NOT accidentally wipe the address table.
        """
        self._apply(self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT address FROM address WHERE address=?",
                ("Mainline - P_legacy",),
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(
            row,
            "The seeded address row 'Mainline - P_legacy' must survive re-apply()",
        )


# ---------------------------------------------------------------------------
# Test 4 — rollback: columns gone, admin gone, project data rows preserved
# ---------------------------------------------------------------------------

class Migration0004RollbackTest(_TempDataHome):
    """AC9: after apply() + rollback(), the project table must not have
    xproj_granted_at or xproj_granted_by, the admin table must be gone,
    but project data rows (with their state) must be preserved.

    RED: 0004 absent → apply() does not add the columns/admin → rollback()
    rolls back 0003 instead (wrong step) → shape assertions that require the
    columns to be present first fail immediately.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("rollback")
        self._pid = "RollbackTest"
        # Setup + apply full chain.
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)
        self._db = self._db_path(self._dh)

        # Seed a project row and an address row for preservation checks.
        with sqlite3.connect(self._db) as con:
            con.execute(
                "INSERT OR IGNORE INTO project (project_id, state) VALUES (?,?)",
                ("Rollback_P", "active"),
            )
            con.execute(
                "INSERT OR IGNORE INTO address (address, kind, project) VALUES (?,?,?)",
                ("Track 1 - RollbackTest", "track", "RollbackTest"),
            )
            con.commit()

        # Seed xproj columns + an admin row so rollback has something to remove.
        with sqlite3.connect(self._db) as con:
            con.execute(
                "UPDATE project SET xproj_granted_at='2026-01-01', xproj_granted_by='ops' "
                "WHERE project_id=?",
                (self._pid,),
            )
            con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
            con.commit()

    def test_rollback_removes_xproj_granted_at_from_project(self):
        """After apply-then-rollback, project.xproj_granted_at must not exist.

        RED: 0004 never applied → column never added → apply pre-condition fires first
        (verified in setUp's seed step — if the column is absent, the seed UPDATE
        is a no-op, and later assertions catch the real RED state).
        """
        # Pre-condition: column must exist after apply.
        cols_after_apply = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_at",
            cols_after_apply,
            "xproj_granted_at must exist after apply() (pre-condition for rollback test); "
            "0004 not yet written.",
        )
        self._rollback(self._dh)
        cols_after_rollback = _column_map(self._db, "project")
        self.assertNotIn(
            "xproj_granted_at",
            cols_after_rollback,
            f"xproj_granted_at must be gone after rollback(); "
            f"columns present: {set(cols_after_rollback.keys())!r}",
        )

    def test_rollback_removes_xproj_granted_by_from_project(self):
        """After apply-then-rollback, project.xproj_granted_by must not exist.

        RED: 0004 absent.
        """
        cols_after_apply = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_by",
            cols_after_apply,
            "xproj_granted_by must exist after apply() (pre-condition); 0004 not written.",
        )
        self._rollback(self._dh)
        cols_after_rollback = _column_map(self._db, "project")
        self.assertNotIn(
            "xproj_granted_by",
            cols_after_rollback,
            f"xproj_granted_by must be gone after rollback(); "
            f"columns present: {set(cols_after_rollback.keys())!r}",
        )

    def test_rollback_drops_admin_table(self):
        """After apply-then-rollback, the admin table must not exist.

        RED: admin table absent from the start → assertion fires on the
        apply pre-condition check below.
        """
        tables_after_apply = _table_names(self._db)
        self.assertIn(
            "admin",
            tables_after_apply,
            "admin table must exist after apply() (pre-condition); 0004 not written.",
        )
        self._rollback(self._dh)
        tables_after_rollback = _table_names(self._db)
        self.assertNotIn(
            "admin",
            tables_after_rollback,
            f"admin table must be gone after rollback(); "
            f"found tables: {sorted(tables_after_rollback)!r}",
        )

    def test_rollback_preserves_project_rows(self):
        """Project data rows must survive the apply+rollback cycle (count + state).

        RED: 0004 absent → rollback rolls back 0003 instead → project table
        disappears → this assertion fails.
        """
        self._rollback(self._dh)
        con = sqlite3.connect(self._db)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT project_id, state FROM project ORDER BY project_id"
            ).fetchall()
        finally:
            con.close()
        project_ids = [r["project_id"] for r in rows]
        self.assertIn(
            self._pid,
            project_ids,
            f"project row '{self._pid}' must survive rollback; got {project_ids!r}",
        )
        # Verify state is preserved (not reset).
        row = next((r for r in rows if r["project_id"] == self._pid), None)
        self.assertIsNotNone(row)
        self.assertEqual(
            row["state"], "active",
            f"project '{self._pid}' state must still be 'active' after rollback; "
            f"got {row['state']!r}",
        )

    def test_rollback_leaves_0004_pending(self):
        """After rollback, status() must show 0004-xproj-grant as pending.

        RED: 0004 file absent → status() never sees it at all.
        """
        self._rollback(self._dh)
        applied, pending = self._status(self._dh)
        self.assertNotIn(
            "0004-xproj-grant",
            applied,
            f"0004-xproj-grant must NOT be in applied after rollback; got applied={applied!r}",
        )
        self.assertIn(
            "0004-xproj-grant",
            pending,
            f"0004-xproj-grant must be in pending after rollback; got pending={pending!r}",
        )

    def test_rollback_keeps_earlier_migrations_applied(self):
        """After rollback of 0004, 0001–0003 must still be applied.

        RED: 0004 absent → rollback undoes 0003 instead → 0003 moves to pending.
        """
        self._rollback(self._dh)
        applied, _ = self._status(self._dh)
        for mid in ("0001-baseline", "0002-drop-message-status", "0003-project-tracker"):
            self.assertIn(
                mid, applied,
                f"{mid} must still be in applied after rollback() of 0004; "
                f"got applied={applied!r}",
            )


# ---------------------------------------------------------------------------
# Test 5 — fresh-_SCHEMA parity + harmless re-run
# ---------------------------------------------------------------------------

class Migration0004FreshSchemaParity(_TempDataHome):
    """AC9: after GREEN, sandesh_db.setup() must create a DB whose _SCHEMA already
    contains xproj_granted_at + xproj_granted_by on project, and the admin table
    (fresh-DB parity). Then migrate.apply() on that fresh setup() store must be a
    harmless re-run:
      - 0001 marked (adoption glue), 0002/0003/0004 applied as IF NOT EXISTS → no err
      - status() shows all four applied, 0 pending.

    RED: _SCHEMA does not include the xproj columns or admin table → fresh store
    lacks both → every assertion below fails.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("parity")
        self._pid = "ParityTest"
        # Provision a fresh store via setup() — does NOT go through migrations.
        self._setup_project(self._pid, self._dh)
        self._db = self._db_path(self._dh)

    def test_fresh_setup_creates_xproj_granted_at_column(self):
        """sandesh_db.setup() must create project.xproj_granted_at (via _SCHEMA).

        RED: _SCHEMA has no xproj_granted_at column on project.
        """
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_at",
            cols,
            "project.xproj_granted_at must exist in a fresh setup() store (_SCHEMA parity); "
            "_SCHEMA does not yet contain the xproj_granted_at column.",
        )

    def test_fresh_setup_creates_xproj_granted_by_column(self):
        """sandesh_db.setup() must create project.xproj_granted_by (via _SCHEMA).

        RED: _SCHEMA has no xproj_granted_by column on project.
        """
        cols = _column_map(self._db, "project")
        self.assertIn(
            "xproj_granted_by",
            cols,
            "project.xproj_granted_by must exist in a fresh setup() store (_SCHEMA parity); "
            "_SCHEMA does not yet contain the xproj_granted_by column.",
        )

    def test_fresh_setup_creates_admin_table(self):
        """sandesh_db.setup() must create the admin table (via _SCHEMA).

        RED: _SCHEMA has no admin table.
        """
        tables = _table_names(self._db)
        self.assertIn(
            "admin",
            tables,
            "admin table must exist in a fresh setup() store (_SCHEMA parity); "
            "_SCHEMA does not yet contain CREATE TABLE admin.",
        )

    def test_apply_on_fresh_setup_store_does_not_raise(self):
        """migrate.apply() on a fresh setup() store must not raise.

        The adoption glue marks 0001; 0002/0003/0004 use IF NOT EXISTS → harmless re-run.
        RED: 0004 file absent → apply() raises or leaves 0004 pending.
        """
        try:
            self._apply(self._dh)
        except Exception as exc:
            self.fail(
                f"migrate.apply() on a fresh setup() store raised {type(exc).__name__}: {exc}\n"
                "Expected: harmless re-run (0001 marked, 0002/0003/0004 IF NOT EXISTS).",
            )

    def test_apply_on_fresh_setup_store_shows_four_applied_zero_pending(self):
        """After apply() on a fresh setup() store, status() must show all 4 applied, 0 pending.

        RED: 0004 absent → status shows only 3 applied or raises.
        """
        self._apply(self._dh)
        applied, pending = self._status(self._dh)
        for mid in ("0001-baseline", "0002-drop-message-status",
                    "0003-project-tracker", "0004-xproj-grant"):
            self.assertIn(
                mid, applied,
                f"{mid} must be in applied after apply() on fresh store; "
                f"got applied={applied!r}",
            )
        self.assertEqual(
            len(pending), 0,
            f"pending must be 0 after apply() on fresh store; got pending={pending!r}",
        )
        self.assertEqual(
            len(applied), 4,
            f"Exactly 4 migrations must be applied (0001..0004); got {applied!r}",
        )


# ---------------------------------------------------------------------------
# Test 6 — snapshot equality: dump_schema() == current-schema.json
# ---------------------------------------------------------------------------

class Migration0004SnapshotEqualityTest(_TempDataHome):
    """AC9: dump_schema() on a fully-migrated store must equal the committed
    sandesh/schema/current-schema.json snapshot.

    FAILS at RED because current-schema.json lacks:
      - project.xproj_granted_at
      - project.xproj_granted_by
      - the admin table

    These three targeted sub-tests assert the EXACT absence so the failure
    message is actionable. The deep-equality test catches anything else.
    GREEN regenerates the snapshot so it matches the post-0004 schema.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("snapshot")
        self._pid = "SnapTest"
        self._setup_project(self._pid, self._dh)
        self._apply(self._dh)

    def test_committed_snapshot_contains_admin_table(self):
        """current-schema.json must contain the admin table.

        FAILS at RED because the committed snapshot was generated before 0004
        and does not yet include admin. GREEN regenerates the snapshot.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"current-schema.json not found at {_SNAPSHOT_PATH}",
        )
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            committed = json.load(fh)
        tables = set(committed.get("tables", {}).keys())
        self.assertIn(
            "admin",
            tables,
            "current-schema.json must contain the 'admin' table; it does not (pre-0004 snapshot). "
            "GREEN must regenerate the snapshot with `sandesh migrate --dump-schema` on a "
            "fully-migrated store and commit the result.",
        )

    def test_committed_snapshot_contains_xproj_granted_at(self):
        """current-schema.json must contain project.xproj_granted_at.

        FAILS at RED — snapshot pre-dates 0004.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"current-schema.json not found at {_SNAPSHOT_PATH}",
        )
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            committed = json.load(fh)
        project_cols = committed.get("tables", {}).get("project", {}).get("columns", {})
        self.assertIn(
            "xproj_granted_at",
            project_cols,
            "current-schema.json must contain project.xproj_granted_at; "
            "it does not (pre-0004 snapshot). "
            "GREEN must regenerate the snapshot.",
        )

    def test_committed_snapshot_contains_xproj_granted_by(self):
        """current-schema.json must contain project.xproj_granted_by.

        FAILS at RED — snapshot pre-dates 0004.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"current-schema.json not found at {_SNAPSHOT_PATH}",
        )
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            committed = json.load(fh)
        project_cols = committed.get("tables", {}).get("project", {}).get("columns", {})
        self.assertIn(
            "xproj_granted_by",
            project_cols,
            "current-schema.json must contain project.xproj_granted_by; "
            "it does not (pre-0004 snapshot). "
            "GREEN must regenerate the snapshot.",
        )

    def test_dump_schema_equals_committed_snapshot(self):
        """dump_schema() == current-schema.json on a fully-migrated fresh store.

        RED failure modes:
          1. current-schema.json lacks admin + xproj columns (pre-0004 snapshot).
          2. dump_schema() returns correct live shape but snapshot is stale.
        GREEN regenerates current-schema.json.
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
            f"  committed tables: {sorted(committed_tables)}\n"
            f"  live tables:      {sorted(live_tables)}\n"
            "GREEN must regenerate current-schema.json to include admin and the "
            "xproj columns on project.",
        )
        self.assertEqual(
            live, committed,
            "dump_schema() output does not match current-schema.json.\n"
            "GREEN must regenerate the snapshot with `sandesh migrate --dump-schema` "
            "on a fully-migrated store and commit the result.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
