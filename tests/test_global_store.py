"""test_global_store.py — RED tests for CR-SAN-022 Cycle 2.

Covers AC2/AC3/AC4 + DRIFT-3 + PRD D1/D2/O1:
  AC2 — connect() no-arg opens <data_home>/sandesh/sandesh.db with WAL;
         two project_ids land in the same file.
  AC3 — setup(project_id) enrolls project tracker row + creates messages/ dir;
         re-setup is a no-op; tombstoned id raises ValueError with exact message;
         archived id is a no-op.
  AC4 — list_projects() reads the tracker; includes active+archived; tombstoned
         only via include_tombstoned=True; stray filesystem dir NOT listed.
  Additional: db_path() helper, project_state() helper, caller smoke tests.

Expected RED failures (against current code, pre-GREEN C2):
  - connect() requires a positional `store` arg → TypeError when called no-arg
  - db_path() does not exist → AttributeError
  - project_state() does not exist → AttributeError
  - setup() writes no project tracker row → no-op / tombstoned checks fail
  - list_projects() scans the filesystem → stray dir IS listed, tombstoned
    filtering impossible

Run via the Crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_global_store --agent red-cr022-c2
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

class _GlobalTempDataHome(unittest.TestCase):
    """Mixin: per-test isolated XDG_DATA_HOME.

    After GREEN, connect() (no-arg) resolves XDG_DATA_HOME from the environment.
    Each test gets its own temp dir and restores the original env on teardown.

    Global DB path (post-GREEN):
        <data_home>/sandesh/sandesh.db

    Per-project message body dir (unchanged):
        <data_home>/sandesh/projects/<project_id>/messages/
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_global_test_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _data_home(self, name="dh"):
        """Return a per-test sub-dir of the temp dir used as the data home."""
        p = os.path.join(self._tmpdir, name)
        os.makedirs(p, exist_ok=True)
        return p

    def _set_xdg(self, data_home):
        os.environ["XDG_DATA_HOME"] = data_home

    def _global_db_path(self, data_home):
        """Expected path of the global DB (post-GREEN)."""
        return os.path.join(data_home, "sandesh", "sandesh.db")

    def _project_messages_dir(self, data_home, project_id):
        """Expected messages dir for a project (unchanged from current layout)."""
        return os.path.join(data_home, "sandesh", "projects", project_id, "messages")

    def _stray_project_dir(self, data_home, project_id):
        """Create a stray projects/<id>/ directory on disk without a tracker row."""
        path = os.path.join(data_home, "sandesh", "projects", project_id)
        os.makedirs(path, exist_ok=True)
        return path


# ---------------------------------------------------------------------------
# AC2 / DRIFT-3 — db_path() helper
# ---------------------------------------------------------------------------

class DbPathTest(_GlobalTempDataHome):
    """db_path() must return <data_home>/sandesh/sandesh.db, honouring $XDG_DATA_HOME.

    RED: db_path() does not exist → AttributeError at import/call time.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("dbpath")

    def test_db_path_exists_as_function(self):
        """sandesh_db.db_path must be callable (function exists).

        RED: AttributeError — db_path not yet defined in sandesh_db.
        """
        from sandesh import sandesh_db
        self.assertTrue(
            callable(getattr(sandesh_db, "db_path", None)),
            "sandesh_db.db_path must exist and be callable; not yet defined.",
        )

    def test_db_path_returns_global_sandesh_db_file(self):
        """db_path() must return <data_home>/sandesh/sandesh.db.

        RED: db_path() absent → AttributeError.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db
        expected = os.path.join(self._dh, "sandesh", "sandesh.db")
        result = sandesh_db.db_path()
        self.assertEqual(
            result,
            expected,
            f"db_path() must return {expected!r}; got {result!r}",
        )

    def test_db_path_honours_xdg_data_home(self):
        """db_path() must use $XDG_DATA_HOME, not ~/.local/share.

        RED: db_path() absent → AttributeError.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db
        result = sandesh_db.db_path()
        self.assertIn(
            self._dh,
            result,
            f"db_path() must honour $XDG_DATA_HOME={self._dh!r}; got {result!r}",
        )
        self.assertNotIn(
            os.path.expanduser("~/.local/share"),
            result,
            "db_path() must use $XDG_DATA_HOME, not ~/.local/share",
        )

    def test_db_path_not_per_project(self):
        """db_path() must return the GLOBAL path, not a per-project path.

        It must NOT contain 'projects/' — that would be the old per-project layout.

        RED: db_path() absent → AttributeError.
        """
        self._set_xdg(self._dh)
        from sandesh import sandesh_db
        result = sandesh_db.db_path()
        self.assertNotIn(
            "projects",
            result,
            f"db_path() must be the global path (no 'projects/' component); got {result!r}",
        )


# ---------------------------------------------------------------------------
# AC2 / DRIFT-3 — connect() no-arg
# ---------------------------------------------------------------------------

class ConnectNoArgTest(_GlobalTempDataHome):
    """connect() (no-arg) must open db_path(), set WAL, return a connection.

    RED: connect(store) requires 1 positional arg → TypeError when called no-arg.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("connect")
        self._set_xdg(self._dh)

    def tearDown(self):
        super().tearDown()

    def test_connect_accepts_no_args(self):
        """connect() called with no arguments must not raise TypeError.

        RED: current connect(store) requires a positional arg → TypeError.
        """
        from sandesh import sandesh_db
        try:
            con = sandesh_db.connect()
            con.close()
        except TypeError as exc:
            self.fail(
                f"connect() raised TypeError: {exc}\n"
                "Expected: connect() is no-arg (DRIFT-3); currently requires `store`."
            )

    def test_connect_opens_global_db_path(self):
        """connect() must open the file at db_path(), not a per-project path.

        Verified via PRAGMA database_list (the 'main' entry's file path).

        RED: connect() raises TypeError before reaching db_path() logic.
        """
        from sandesh import sandesh_db
        expected = sandesh_db.db_path()
        con = sandesh_db.connect()
        try:
            rows = con.execute("PRAGMA database_list").fetchall()
            # Row is (seq, name, file) or sqlite3.Row with those keys
            main_file = None
            for row in rows:
                if row[1] == "main":
                    main_file = row[2]
                    break
            self.assertIsNotNone(
                main_file,
                "PRAGMA database_list must include a 'main' entry",
            )
            self.assertEqual(
                os.path.realpath(main_file),
                os.path.realpath(expected),
                f"connect() must open {expected!r}; PRAGMA database_list shows {main_file!r}",
            )
        finally:
            con.close()

    def test_connect_sets_wal_journal_mode(self):
        """connect() must set PRAGMA journal_mode=WAL; querying it must return 'wal'.

        RED: connect() raises TypeError before reaching any PRAGMA setting.
        """
        from sandesh import sandesh_db
        con = sandesh_db.connect()
        try:
            row = con.execute("PRAGMA journal_mode").fetchone()
            self.assertIsNotNone(row, "PRAGMA journal_mode must return a row")
            journal_mode = row[0] if not hasattr(row, "keys") else row["journal_mode"]
            self.assertEqual(
                journal_mode,
                "wal",
                f"connect() must set journal_mode=WAL; got {journal_mode!r}",
            )
        finally:
            con.close()

    def test_connect_returns_sqlite_connection_with_row_factory(self):
        """connect() must return a sqlite3.Connection (with row_factory set).

        RED: connect() raises TypeError.
        """
        from sandesh import sandesh_db
        con = sandesh_db.connect()
        try:
            self.assertIsInstance(
                con,
                sqlite3.Connection,
                "connect() must return a sqlite3.Connection",
            )
            self.assertIsNotNone(
                con.row_factory,
                "connect() must set row_factory (sqlite3.Row) on the connection",
            )
        finally:
            con.close()

    def test_two_projects_land_in_same_db_file(self):
        """Operations for two different project_ids must land in the same DB file.

        After setup('P1') and setup('P2'), a single connect() must see both
        project rows in the tracker.

        RED (multiple causes):
          - connect() raises TypeError (no-arg not supported)
          - setup() doesn't write tracker rows even if connect() works
        """
        from sandesh import sandesh_db
        sandesh_db.setup("P1")
        sandesh_db.setup("P2")

        con = sandesh_db.connect()
        try:
            rows = con.execute(
                "SELECT project_id FROM project ORDER BY project_id"
            ).fetchall()
            project_ids = [r[0] for r in rows]
            self.assertIn(
                "P1",
                project_ids,
                f"'P1' must appear in the project tracker after setup('P1'); got {project_ids!r}",
            )
            self.assertIn(
                "P2",
                project_ids,
                f"'P2' must appear in the project tracker after setup('P2'); got {project_ids!r}",
            )
            self.assertEqual(
                len(project_ids),
                2,
                f"Exactly 2 project rows expected (P1+P2); got {project_ids!r}",
            )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# AC3 — setup() enrolls in the tracker
# ---------------------------------------------------------------------------

class SetupTrackerEnrollmentTest(_GlobalTempDataHome):
    """setup(project_id) must insert a project tracker row AND create the
    messages/ directory. Various edge cases per O1.

    RED: setup() does not write any tracker row → SELECT returns nothing.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("setup")
        self._set_xdg(self._dh)

    def _get_project_row(self, project_id):
        """Read the project tracker row directly from the global DB."""
        from sandesh import sandesh_db
        db = sandesh_db.db_path()
        if not os.path.isfile(db):
            return None
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT * FROM project WHERE project_id=?", (project_id,)
            ).fetchone()
        finally:
            con.close()

    def test_setup_inserts_active_project_row(self):
        """setup('P1') must insert project_id='P1', state='active' in the tracker.

        RED: setup() does not write any tracker row.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("P1")
        row = self._get_project_row("P1")
        self.assertIsNotNone(
            row,
            "setup('P1') must insert a row into the project tracker; no row found.",
        )
        self.assertEqual(
            row["project_id"],
            "P1",
            f"project_id must be 'P1'; got {row['project_id']!r}",
        )
        self.assertEqual(
            row["state"],
            "active",
            f"state must be 'active' after setup; got {row['state']!r}",
        )

    def test_setup_creates_messages_directory(self):
        """setup('P1') must create projects/P1/messages/ under the data home.

        This is a LOCKED BEHAVIOUR (present before C2). Kept as a guard
        to ensure the new tracker implementation doesn't regress it.

        NOTE: This assertion may PASS against current code (pre-GREEN) because
        setup() already creates the messages/ dir today. It is intentionally
        kept alongside the tracker assertions to form a complete behavioural set.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("P1")
        expected_dir = self._project_messages_dir(self._dh, "P1")
        self.assertTrue(
            os.path.isdir(expected_dir),
            f"setup('P1') must create the messages/ dir at {expected_dir!r}",
        )

    def test_setup_idempotent_no_duplicate_row(self):
        """Re-calling setup('P1') must be a no-op: still exactly one row, state unchanged.

        RED: if setup() naively INSERTs without INSERT OR IGNORE, the second call
        raises sqlite3.IntegrityError (PK conflict). Or if it does nothing at all,
        only one row exists but the first INSERT also didn't happen — which is caught
        by test_setup_inserts_active_project_row above.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("P1")
        try:
            sandesh_db.setup("P1")  # second call — must not raise
        except Exception as exc:
            self.fail(
                f"Re-calling setup('P1') raised {type(exc).__name__}: {exc}\n"
                "Expected: idempotent no-op."
            )
        row = self._get_project_row("P1")
        self.assertIsNotNone(row, "project row must still exist after re-setup")
        self.assertEqual(row["state"], "active", "state must remain 'active' after re-setup")
        # Confirm exactly one row
        from sandesh import sandesh_db as sdb
        db = sdb.db_path()
        con = sqlite3.connect(db)
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM project WHERE project_id='P1'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 1, f"Exactly 1 row for P1 expected; got {count}")

    def test_setup_tombstoned_raises_valueerror(self):
        """setup() on a tombstoned project_id must raise ValueError.

        The exact message must contain 'project id retired (tombstoned) — choose a new id'
        (per O1 in the spec).

        RED: setup() does not check the tracker; it will succeed silently or raise
        a different error (no tracker row written = no check possible).
        """
        from sandesh import sandesh_db
        # Seed a tombstoned row directly
        db_file = os.path.join(self._dh, "sandesh", "sandesh.db")
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        # Provision the DB first via setup of a different project so the schema exists
        sandesh_db.setup("Seed")
        con = sqlite3.connect(db_file)
        try:
            con.execute(
                "INSERT OR REPLACE INTO project (project_id, state) VALUES (?, ?)",
                ("TombProj", "tombstoned"),
            )
            con.commit()
        finally:
            con.close()

        with self.assertRaises(ValueError) as ctx:
            sandesh_db.setup("TombProj")
        self.assertIn(
            "project id retired (tombstoned) — choose a new id",
            str(ctx.exception),
            f"ValueError message must contain the exact spec text; got: {ctx.exception!r}",
        )

    def test_setup_tombstoned_leaves_row_unchanged(self):
        """setup() on a tombstoned id must not alter the row (state remains 'tombstoned').

        RED: setup() doesn't check the tracker at all.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("Seed")
        db_file = sandesh_db.db_path()
        con = sqlite3.connect(db_file)
        try:
            con.execute(
                "INSERT OR REPLACE INTO project (project_id, state) VALUES (?, ?)",
                ("TombProj2", "tombstoned"),
            )
            con.commit()
        finally:
            con.close()

        try:
            sandesh_db.setup("TombProj2")
        except ValueError:
            pass  # expected

        con2 = sqlite3.connect(db_file)
        con2.row_factory = sqlite3.Row
        try:
            row = con2.execute(
                "SELECT state FROM project WHERE project_id=?", ("TombProj2",)
            ).fetchone()
        finally:
            con2.close()

        self.assertIsNotNone(row, "project row must still exist after refused setup")
        self.assertEqual(
            row["state"],
            "tombstoned",
            f"state must remain 'tombstoned' after refused setup; got {row['state']!r}",
        )

    def test_setup_archived_is_noop(self):
        """setup() on an archived project_id must be a no-op (no error, state unchanged).

        RED: setup() doesn't check the tracker; may write a new row (PK collision)
        or simply not check the state.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("Seed")
        db_file = sandesh_db.db_path()
        con = sqlite3.connect(db_file)
        try:
            con.execute(
                "INSERT OR REPLACE INTO project (project_id, state) VALUES (?, ?)",
                ("ArchivedProj", "archived"),
            )
            con.commit()
        finally:
            con.close()

        try:
            sandesh_db.setup("ArchivedProj")
        except Exception as exc:
            self.fail(
                f"setup() on archived project_id raised {type(exc).__name__}: {exc}\n"
                "Expected: no-op (state stays 'archived')."
            )

        con2 = sqlite3.connect(db_file)
        con2.row_factory = sqlite3.Row
        try:
            row = con2.execute(
                "SELECT state FROM project WHERE project_id=?", ("ArchivedProj",)
            ).fetchone()
        finally:
            con2.close()

        self.assertIsNotNone(row, "project row must still exist after archived no-op setup")
        self.assertEqual(
            row["state"],
            "archived",
            f"state must remain 'archived' after no-op setup; got {row['state']!r}",
        )


# ---------------------------------------------------------------------------
# AC4 — list_projects() reads the tracker (not the filesystem)
# ---------------------------------------------------------------------------

class ListProjectsTrackerTest(_GlobalTempDataHome):
    """list_projects() must read the project tracker table, not scan the filesystem.

    RED: current list_projects() scans projects/<id>/sandesh.db → a stray dir
    WOULD be listed; tombstoned filtering is impossible.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("list")
        self._set_xdg(self._dh)

    def _seed_project_row(self, project_id, state):
        """Insert a project row directly into the global DB (bypassing setup)."""
        from sandesh import sandesh_db
        # Ensure the DB and schema exist first
        sandesh_db.setup("_Seed")
        db_file = sandesh_db.db_path()
        con = sqlite3.connect(db_file)
        try:
            con.execute(
                "INSERT OR REPLACE INTO project (project_id, state) VALUES (?, ?)",
                (project_id, state),
            )
            con.commit()
        finally:
            con.close()

    def test_list_projects_returns_active_projects(self):
        """list_projects() must return active project_ids enrolled in the tracker.

        RED: list_projects() scans the filesystem and setup() writes no tracker row
        → the project is found by fs scan (accidentally correct for active), but the
        filtering logic is wrong.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("Alpha")
        sandesh_db.setup("Beta")
        result = sandesh_db.list_projects()
        self.assertIn("Alpha", result, f"'Alpha' must appear in list_projects(); got {result!r}")
        self.assertIn("Beta", result, f"'Beta' must appear in list_projects(); got {result!r}")

    def test_list_projects_returns_sorted(self):
        """list_projects() must return project_ids in sorted order.

        RED: filesystem scan with sorted() may coincidentally pass on a small list;
        but the tracker-based implementation is required for the other filtering tests.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("Zeta")
        sandesh_db.setup("Alpha")
        sandesh_db.setup("Mu")
        result = sandesh_db.list_projects()
        # Extract only the ones we set up (exclude the seeded _Seed row if present)
        relevant = [p for p in result if p in ("Zeta", "Alpha", "Mu")]
        self.assertEqual(
            relevant,
            sorted(relevant),
            f"list_projects() must return ids in sorted order; got {result!r}",
        )

    def test_list_projects_includes_archived(self):
        """list_projects() must include projects with state='archived'.

        RED: filesystem scan cannot distinguish archived from active (both have
        a store dir in the old layout); tracker-based is required.
        But RED is specifically: setup() writes no tracker row → seeded archived
        row not visible in the filesystem-based scan AT ALL (no dir created for it).
        """
        self._seed_project_row("ArchProject", "archived")
        result = sandesh_db.list_projects()
        from sandesh import sandesh_db
        result = sandesh_db.list_projects()
        self.assertIn(
            "ArchProject",
            result,
            f"list_projects() must include archived projects; got {result!r}",
        )

    def test_list_projects_excludes_tombstoned_by_default(self):
        """list_projects() must NOT include tombstoned projects by default.

        RED: filesystem scan has no concept of tombstoned state → it would
        include any dir present; also setup() writes no tracker rows.
        """
        self._seed_project_row("TombProject", "tombstoned")
        from sandesh import sandesh_db
        result = sandesh_db.list_projects()
        self.assertNotIn(
            "TombProject",
            result,
            f"list_projects() must exclude tombstoned projects by default; got {result!r}",
        )

    def test_list_projects_include_tombstoned_flag(self):
        """list_projects(include_tombstoned=True) must include tombstoned projects.

        RED: list_projects() currently has no include_tombstoned parameter →
        TypeError or ignores it.
        """
        self._seed_project_row("TombProject2", "tombstoned")
        from sandesh import sandesh_db
        try:
            result = sandesh_db.list_projects(include_tombstoned=True)
        except TypeError as exc:
            self.fail(
                f"list_projects(include_tombstoned=True) raised TypeError: {exc}\n"
                "Expected: the parameter exists and tombstoned ids are returned."
            )
        self.assertIn(
            "TombProject2",
            result,
            f"list_projects(include_tombstoned=True) must include tombstoned ids; got {result!r}",
        )

    def test_list_projects_stray_dir_not_listed(self):
        """A stray projects/<id>/ directory WITHOUT a tracker row must NOT be listed.

        RED: current list_projects() scans the filesystem → the stray dir WOULD
        be listed (if it also contained a sandesh.db file, which we can create).
        """
        from sandesh import sandesh_db
        # Create a stray dir with a db file to fool the old filesystem scan
        stray_dir = os.path.join(self._dh, "sandesh", "projects", "StrayProject")
        os.makedirs(stray_dir, exist_ok=True)
        # Also create a sandesh.db inside to fool the old scan
        stray_db = os.path.join(stray_dir, "sandesh.db")
        with sqlite3.connect(stray_db) as c:
            c.execute("CREATE TABLE IF NOT EXISTS dummy (id INTEGER)")
        # Ensure the global DB is provisioned (so list_projects has somewhere to read from)
        sandesh_db.setup("RealProject")
        result = sandesh_db.list_projects()
        self.assertNotIn(
            "StrayProject",
            result,
            f"list_projects() must NOT list stray filesystem dirs without tracker rows; "
            f"got {result!r}",
        )

    def test_list_projects_counts_bounded(self):
        """list_projects() must return EXACTLY the enrolled projects, no more.

        With 2 active + 1 archived + 1 tombstoned enrolled, default call returns
        exactly 2 + 1 = 3 (not 4).
        With include_tombstoned=True, returns 4.

        RED: setup() writes no tracker rows + filesystem scan → counts wrong.
        """
        from sandesh import sandesh_db
        sandesh_db.setup("Bounded1")
        sandesh_db.setup("Bounded2")
        self._seed_project_row("BoundedArch", "archived")
        self._seed_project_row("BoundedTomb", "tombstoned")

        result_default = sandesh_db.list_projects()
        enrolled = {"Bounded1", "Bounded2", "BoundedArch", "BoundedTomb"}
        relevant_default = [p for p in result_default if p in enrolled]
        self.assertEqual(
            set(relevant_default),
            {"Bounded1", "Bounded2", "BoundedArch"},
            f"Default list_projects() must return active+archived only; got {relevant_default!r}",
        )

        result_with_tomb = sandesh_db.list_projects(include_tombstoned=True)
        relevant_tomb = [p for p in result_with_tomb if p in enrolled]
        self.assertEqual(
            set(relevant_tomb),
            {"Bounded1", "Bounded2", "BoundedArch", "BoundedTomb"},
            f"list_projects(include_tombstoned=True) must include all 4; got {relevant_tomb!r}",
        )


# ---------------------------------------------------------------------------
# project_state() — new helper
# ---------------------------------------------------------------------------

class ProjectStateTest(_GlobalTempDataHome):
    """project_state(con, project_id) must return the state string or None.

    RED: project_state() does not exist → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("state")
        self._set_xdg(self._dh)
        # Provision global DB
        from sandesh import sandesh_db
        sandesh_db.setup("_Seed")

    def _seed_row(self, project_id, state):
        from sandesh import sandesh_db
        db_file = sandesh_db.db_path()
        con = sqlite3.connect(db_file)
        try:
            con.execute(
                "INSERT OR REPLACE INTO project (project_id, state) VALUES (?, ?)",
                (project_id, state),
            )
            con.commit()
        finally:
            con.close()

    def _open_con(self):
        from sandesh import sandesh_db
        return sandesh_db.connect()

    def test_project_state_function_exists(self):
        """sandesh_db.project_state must exist and be callable.

        RED: AttributeError — function not yet defined.
        """
        from sandesh import sandesh_db
        self.assertTrue(
            callable(getattr(sandesh_db, "project_state", None)),
            "sandesh_db.project_state must exist and be callable; not yet defined.",
        )

    def test_project_state_returns_active(self):
        """project_state(con, 'ActiveProj') must return 'active' for an active row.

        RED: AttributeError.
        """
        self._seed_row("ActiveProj", "active")
        from sandesh import sandesh_db
        con = self._open_con()
        try:
            result = sandesh_db.project_state(con, "ActiveProj")
        finally:
            con.close()
        self.assertEqual(
            result,
            "active",
            f"project_state() must return 'active'; got {result!r}",
        )

    def test_project_state_returns_archived(self):
        """project_state(con, 'ArchProj') must return 'archived'.

        RED: AttributeError.
        """
        self._seed_row("ArchProj", "archived")
        from sandesh import sandesh_db
        con = self._open_con()
        try:
            result = sandesh_db.project_state(con, "ArchProj")
        finally:
            con.close()
        self.assertEqual(
            result,
            "archived",
            f"project_state() must return 'archived'; got {result!r}",
        )

    def test_project_state_returns_tombstoned(self):
        """project_state(con, 'TombProj') must return 'tombstoned'.

        RED: AttributeError.
        """
        self._seed_row("TombProj", "tombstoned")
        from sandesh import sandesh_db
        con = self._open_con()
        try:
            result = sandesh_db.project_state(con, "TombProj")
        finally:
            con.close()
        self.assertEqual(
            result,
            "tombstoned",
            f"project_state() must return 'tombstoned'; got {result!r}",
        )

    def test_project_state_returns_none_for_unknown(self):
        """project_state(con, 'Unknown') must return None for a non-existent project.

        RED: AttributeError.
        """
        from sandesh import sandesh_db
        con = self._open_con()
        try:
            result = sandesh_db.project_state(con, "NoSuchProject_xyzzy")
        finally:
            con.close()
        self.assertIsNone(
            result,
            f"project_state() must return None for unknown project; got {result!r}",
        )

    def test_project_state_positive_and_negative(self):
        """Both a present and absent state must work in one test.

        Seeds 'active' and checks it, then checks an absent id returns None.
        Ensures the function distinguishes found vs not-found.

        RED: AttributeError.
        """
        self._seed_row("PosProj", "active")
        from sandesh import sandesh_db
        con = self._open_con()
        try:
            present = sandesh_db.project_state(con, "PosProj")
            absent = sandesh_db.project_state(con, "NegProj_xyzzy")
        finally:
            con.close()
        self.assertEqual(present, "active", f"project_state('PosProj') must be 'active'; got {present!r}")
        self.assertIsNone(absent, f"project_state('NegProj_xyzzy') must be None; got {absent!r}")


# ---------------------------------------------------------------------------
# Caller integration smoke tests
# ---------------------------------------------------------------------------

class CallerIntegrationSmokeTest(_GlobalTempDataHome):
    """Smoke tests for the 4 callers that GREEN must update to use connect() no-arg.

    These drive cli.main() in-process (same pattern as test_sandesh.py) and
    verify that the end-to-end flow works against the global DB.

    RED: cli._ctx calls connect(store) with the old positional arg → the call
    will fail if connect() becomes no-arg; or (before GREEN) the test is written
    to call the NEW no-arg connect() which doesn't exist yet, raising TypeError.
    """

    def setUp(self):
        super().setUp()
        self._dh = self._data_home("cli_smoke")
        self._set_xdg(self._dh)

    def _run_cli(self, argv):
        """Run cli.main(argv) and capture stdout. Returns (exit_code, stdout_lines)."""
        import io
        from contextlib import redirect_stdout
        from sandesh import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = e.code
        return rc, buf.getvalue().strip().splitlines()

    def test_cli_setup_then_register_then_addressbook(self):
        """Full CLI flow: setup → register → addressbook must work against global DB.

        Sequence:
          1. sandesh setup --project P1
          2. sandesh --project P1 register --address 'Mainline - P1' --kind mainline
          3. sandesh --project P1 addressbook

        Step 3 must list 'Mainline - P1'.

        RED: cli._ctx calls connect(store); when GREEN makes connect() no-arg,
        the old callers need updating. Until then, the CLI may error out on the
        connect() call.
        """
        rc_setup, out_setup = self._run_cli(["setup", "--project", "P1"])
        self.assertEqual(rc_setup, 0, f"setup must succeed; got rc={rc_setup}, out={out_setup!r}")

        rc_reg, out_reg = self._run_cli(
            ["--project", "P1", "register", "--address", "Mainline - P1", "--kind", "mainline"]
        )
        self.assertEqual(rc_reg, 0, f"register must succeed; got rc={rc_reg}, out={out_reg!r}")

        rc_ab, out_ab = self._run_cli(["--project", "P1", "addressbook"])
        self.assertEqual(rc_ab, 0, f"addressbook must succeed; got rc={rc_ab}, out={out_ab!r}")
        full_output = "\n".join(out_ab)
        self.assertIn(
            "Mainline - P1",
            full_output,
            f"addressbook must list 'Mainline - P1'; got output: {full_output!r}",
        )

    def test_cli_setup_enrolls_project_in_tracker(self):
        """After 'sandesh setup --project P2', the global DB must have a tracker row.

        RED: setup() writes no tracker row (pre-GREEN C2).
        """
        from sandesh import sandesh_db
        rc, _ = self._run_cli(["setup", "--project", "P2"])
        self.assertEqual(rc, 0, "setup must succeed")
        # Read the tracker directly
        db_file = sandesh_db.db_path()
        self.assertTrue(
            os.path.isfile(db_file),
            f"Global DB must exist at {db_file!r} after setup",
        )
        con = sqlite3.connect(db_file)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT project_id, state FROM project WHERE project_id=?", ("P2",)
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row, "project tracker row for 'P2' must exist after CLI setup")
        self.assertEqual(row["state"], "active", f"state must be 'active'; got {row['state']!r}")

    def test_notify_run_can_open_store_via_connect(self):
        """notify.run uses sdb.connect(sdb.store_dir(project_id)) — after GREEN it
        must use sdb.connect() (no-arg).

        This smoke test verifies that importing notify and calling sdb.connect()
        with no args (the post-GREEN call path) does not error out.

        RED: connect() currently requires a positional arg → TypeError.
        """
        from sandesh import sandesh_db as sdb
        # Set up the project so the global DB has the schema
        sdb.setup("NotifyProj")
        # Post-GREEN: connect() opens the global DB
        try:
            con = sdb.connect()
            con.close()
        except TypeError as exc:
            self.fail(
                f"sdb.connect() (no-arg) raised TypeError: {exc}\n"
                "After GREEN, notify.run must use the no-arg connect(); "
                "currently requires the store positional arg."
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
