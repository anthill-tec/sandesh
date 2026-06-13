"""test_admin_grant.py — RED tests for CR-SAN-023 Cycle 2.

Covers AC5 + AC10 (behaviour parts) + AC3/AC4 (grant-state parts):
  assign_admin(con, name)      — empty→INSERT; same→no-op; different→ValueError
  admin_name(con)              — str or None
  grant_xproj(con, pid, by)    — sets timestamps; empty admin→PermissionError;
                                  wrong by→PermissionError; unknown pid→ValueError;
                                  idempotent (second grant → no-op, timestamp unchanged)
  revoke_xproj(con, pid, by)   — NULLs both columns; same auth guards; idempotent
  xproj_granted(con, pid)      — bool reader: True after grant, False after revoke

  CLI grant/revoke subparsers (AC5):
    sandesh grant --cross-project --project P2 --by admin
    sandesh revoke --cross-project --project P2 --by admin
    -- NO parents=[common]; --cross-project is required store_true
    -- cli.main(['admin']) → argparse error (SystemExit 2)

  install.sh content (AC10 §S2b):
    - venv-python assignment block exists (inline -c or heredoc; not a `sandesh admin` CLI call)
    - the assignment block (assign_admin call) runs AFTER the consolidate block

Expected RED: AttributeError for all sandesh_db.* fns (missing); SystemExit(2)
for 'grant', 'revoke', 'admin' subcommands (not yet registered); content
assertions for install.sh (assign_admin call absent or wrong placement).

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_admin_grant --agent red-cr023-c2
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INSTALL_SH = os.path.join(_REPO_ROOT, "install.sh")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli


# ---------------------------------------------------------------------------
# Fixture base class — isolated XDG_DATA_HOME with two enrolled projects P1/P2
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.  setUp enrolls projects P1 and P2 and
    opens a shared connection. Subclasses call super().setUp()."""

    P1 = "P1"
    P2 = "P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-admingrant-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        # Provision two projects so grant/revoke tests have live tracker rows.
        s.setup(self.P1)
        s.setup(self.P2)
        self.con = s.connect()

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process, capture stdout+stderr. Returns (rc, out, err)."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _raw_project_row(self, project_id):
        return self.con.execute(
            "SELECT xproj_granted_at, xproj_granted_by FROM project WHERE project_id=?",
            (project_id,),
        ).fetchone()

    def _admin_row(self):
        return self.con.execute("SELECT id, name, assigned_at FROM admin").fetchone()

    def _admin_count(self):
        return self.con.execute("SELECT COUNT(*) FROM admin").fetchone()[0]


# ===========================================================================
# T1 — assign_admin: empty → INSERT
# ===========================================================================

class AssignAdminEmptyTableTest(_TempDataHome):
    """assign_admin(con, name) on an empty admin table must INSERT a row with
    id=1, the given name, and a non-null assigned_at.

    RED: sandesh_db.assign_admin does not exist → AttributeError at call time.
    """

    def test_assign_admin_callable(self):
        """sandesh_db.assign_admin must be a callable attribute.

        RED: AttributeError — function not yet implemented.
        """
        self.assertTrue(
            callable(getattr(s, "assign_admin", None)),
            "sandesh_db.assign_admin is not callable — implement it (GREEN).",
        )

    def test_assign_admin_inserts_row_on_empty_table(self):
        """assign_admin(con, 'ops') on an empty table must insert a row with id=1.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        row = self._admin_row()
        self.assertIsNotNone(row, "admin row must exist after assign_admin()")
        self.assertEqual(row["id"], 1, f"id must be 1; got {row['id']!r}")

    def test_assign_admin_stores_correct_name(self):
        """assign_admin(con, 'ops') must store name='ops'.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        row = self._admin_row()
        self.assertIsNotNone(row, "admin row must exist")
        self.assertEqual(row["name"], "ops", f"name must be 'ops'; got {row['name']!r}")

    def test_assign_admin_sets_assigned_at(self):
        """assign_admin must record a non-null assigned_at timestamp.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        row = self._admin_row()
        self.assertIsNotNone(row, "admin row must exist")
        self.assertIsNotNone(
            row["assigned_at"],
            "assigned_at must be non-null after assign_admin()",
        )

    def test_assign_admin_table_has_exactly_one_row(self):
        """After assign_admin, admin table must have exactly 1 row.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        self.assertEqual(
            self._admin_count(), 1,
            "admin table must have exactly 1 row after assign_admin()",
        )


# ===========================================================================
# T2 — assign_admin: same name → no-op
# ===========================================================================

class AssignAdminSameNameNoOpTest(_TempDataHome):
    """assign_admin(con, same_name) when the same name is already stored must
    be a no-op (no error, row unchanged).

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        # Seed a first assign via raw SQL so we can test the no-op path
        # independently of the first-insert path.
        self.con.execute(
            "INSERT INTO admin (id, name) VALUES (1, 'ops')"
        )
        self.con.commit()
        self._ts_before = self.con.execute(
            "SELECT assigned_at FROM admin WHERE id=1"
        ).fetchone()["assigned_at"]

    def test_assign_admin_same_name_does_not_raise(self):
        """assign_admin(con, 'ops') with 'ops' already stored must not raise.

        RED: function absent → AttributeError.
        """
        try:
            s.assign_admin(self.con, "ops")
        except Exception as exc:
            self.fail(
                f"assign_admin(same name) raised unexpectedly: "
                f"{type(exc).__name__}: {exc}"
            )

    def test_assign_admin_same_name_leaves_row_unchanged(self):
        """Same-name re-assign must leave id=1, name='ops', and assigned_at unchanged.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        row = self._admin_row()
        self.assertIsNotNone(row, "admin row must still exist after same-name no-op")
        self.assertEqual(row["name"], "ops", "name must still be 'ops' after no-op")
        self.assertEqual(
            row["assigned_at"],
            self._ts_before,
            "assigned_at must be unchanged for a same-name no-op "
            f"(was {self._ts_before!r}, now {row['assigned_at']!r})",
        )

    def test_assign_admin_same_name_table_still_has_one_row(self):
        """Same-name no-op must not duplicate the admin row.

        RED: function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        self.assertEqual(
            self._admin_count(), 1,
            "admin table must still have exactly 1 row after same-name no-op",
        )


# ===========================================================================
# T3 — assign_admin: different name → ValueError
# ===========================================================================

class AssignAdminDifferentNameValueErrorTest(_TempDataHome):
    """assign_admin(con, different_name) when a different name is already stored
    must raise ValueError containing 'refusing to silently re-assign'; the row
    must remain unchanged.

    RED: function absent → AttributeError (which is still RED in JUnit).
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_assign_admin_different_name_raises_value_error(self):
        """assign_admin(con, 'other') when 'ops' is stored must raise ValueError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError):
            s.assign_admin(self.con, "other")

    def test_assign_admin_different_name_error_message(self):
        """The ValueError must contain 'refusing to silently re-assign'.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError) as ctx:
            s.assign_admin(self.con, "other")
        self.assertIn(
            "refusing to silently re-assign",
            str(ctx.exception),
            f"ValueError message must contain 'refusing to silently re-assign'; "
            f"got: {ctx.exception!r}",
        )

    def test_assign_admin_different_name_row_unchanged(self):
        """After a refused re-assign, the stored name must still be 'ops'.

        RED: function absent → AttributeError.
        """
        try:
            s.assign_admin(self.con, "other")
        except ValueError:
            pass
        row = self._admin_row()
        self.assertIsNotNone(row, "admin row must still exist after refused re-assign")
        self.assertEqual(
            row["name"], "ops",
            f"stored name must still be 'ops' after refused re-assign; got {row['name']!r}",
        )


# ===========================================================================
# T4 — admin_name reader
# ===========================================================================

class AdminNameReaderTest(_TempDataHome):
    """admin_name(con) must return the stored name (str) or None when table is empty.

    RED: function absent → AttributeError.
    """

    def test_admin_name_callable(self):
        """sandesh_db.admin_name must be callable.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "admin_name", None)),
            "sandesh_db.admin_name is not callable — implement it (GREEN).",
        )

    def test_admin_name_returns_none_on_empty_table(self):
        """admin_name(con) on an empty admin table must return None.

        RED: function absent → AttributeError.
        """
        result = s.admin_name(self.con)
        self.assertIsNone(
            result,
            f"admin_name(con) must return None on an empty admin table; got {result!r}",
        )

    def test_admin_name_returns_stored_name(self):
        """admin_name(con) must return the stored name after assign_admin.

        RED: function absent → AttributeError.
        """
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        result = s.admin_name(self.con)
        self.assertEqual(
            result, "ops",
            f"admin_name(con) must return 'ops'; got {result!r}",
        )

    def test_admin_name_returns_str_not_row(self):
        """admin_name(con) must return a plain str, not a sqlite3.Row.

        RED: function absent → AttributeError.
        """
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        result = s.admin_name(self.con)
        self.assertIsInstance(
            result, str,
            f"admin_name(con) must return str; got {type(result).__name__!r}",
        )


# ===========================================================================
# T5 — grant_xproj: empty admin table → PermissionError
# ===========================================================================

class GrantXprojNoAdminTest(_TempDataHome):
    """grant_xproj(con, project_id, by) with an empty admin table must raise
    PermissionError containing 'no admin assigned — re-run install.sh with $SANDESH_ADMIN'.

    RED: function absent → AttributeError.
    """

    def test_grant_xproj_callable(self):
        """sandesh_db.grant_xproj must be callable.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "grant_xproj", None)),
            "sandesh_db.grant_xproj is not callable — implement it (GREEN).",
        )

    def test_grant_xproj_empty_admin_raises_permission_error(self):
        """grant_xproj with empty admin table must raise PermissionError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.grant_xproj(self.con, self.P2, "ops")

    def test_grant_xproj_empty_admin_error_message(self):
        """The PermissionError must contain 'no admin assigned — re-run install.sh with $SANDESH_ADMIN'.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError) as ctx:
            s.grant_xproj(self.con, self.P2, "ops")
        self.assertIn(
            "no admin assigned",
            str(ctx.exception),
            f"PermissionError must contain 'no admin assigned'; got: {ctx.exception!r}",
        )
        self.assertIn(
            "$SANDESH_ADMIN",
            str(ctx.exception),
            f"PermissionError must mention '$SANDESH_ADMIN'; got: {ctx.exception!r}",
        )

    def test_grant_xproj_empty_admin_no_row_written(self):
        """grant_xproj with empty admin table must not write any grant data.

        RED: function absent → AttributeError.
        """
        try:
            s.grant_xproj(self.con, self.P2, "ops")
        except PermissionError:
            pass
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_at"],
            "xproj_granted_at must remain NULL after a rejected grant",
        )


# ===========================================================================
# T6 — grant_xproj: wrong 'by' → PermissionError
# ===========================================================================

class GrantXprojWrongByTest(_TempDataHome):
    """grant_xproj with by ≠ stored admin name must raise PermissionError containing
    'only the Sandesh admin may grant/revoke cross-project access'.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_grant_xproj_wrong_by_raises_permission_error(self):
        """grant_xproj(con, P2, 'wrong') must raise PermissionError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.grant_xproj(self.con, self.P2, "wrong")

    def test_grant_xproj_wrong_by_error_message(self):
        """PermissionError must contain 'only the Sandesh admin may grant/revoke'.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError) as ctx:
            s.grant_xproj(self.con, self.P2, "wrong")
        self.assertIn(
            "only the Sandesh admin may grant/revoke cross-project access",
            str(ctx.exception),
            f"PermissionError must contain exact phrase; got: {ctx.exception!r}",
        )

    def test_grant_xproj_wrong_by_no_row_written(self):
        """grant_xproj with wrong by must not write any grant data.

        RED: function absent → AttributeError.
        """
        try:
            s.grant_xproj(self.con, self.P2, "wrong")
        except PermissionError:
            pass
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_at"],
            "xproj_granted_at must remain NULL after wrong-by grant rejection",
        )


# ===========================================================================
# T7 — grant_xproj: unknown project → ValueError
# ===========================================================================

class GrantXprojUnknownProjectTest(_TempDataHome):
    """grant_xproj(con, unknown_id, by) must raise ValueError containing
    "unknown project '<id>'".

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_grant_xproj_unknown_project_raises_value_error(self):
        """grant_xproj(con, 'NoSuchProject', 'ops') must raise ValueError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError):
            s.grant_xproj(self.con, "NoSuchProject", "ops")

    def test_grant_xproj_unknown_project_error_message(self):
        """ValueError must contain "unknown project 'NoSuchProject'".

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError) as ctx:
            s.grant_xproj(self.con, "NoSuchProject", "ops")
        self.assertIn(
            "unknown project",
            str(ctx.exception),
            f"ValueError must contain 'unknown project'; got: {ctx.exception!r}",
        )
        self.assertIn(
            "NoSuchProject",
            str(ctx.exception),
            f"ValueError must contain the bad project id; got: {ctx.exception!r}",
        )


# ===========================================================================
# T8 — grant_xproj: happy path sets timestamps + by
# ===========================================================================

class GrantXprojHappyPathTest(_TempDataHome):
    """grant_xproj(con, P2, 'ops') with correct admin must set xproj_granted_at
    (non-null datetime string) and xproj_granted_by='ops' on the project row.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_grant_xproj_sets_granted_at_nonnull(self):
        """grant_xproj must set xproj_granted_at to a non-null value.

        RED: function absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertIsNotNone(
            row["xproj_granted_at"],
            "xproj_granted_at must be non-null after grant_xproj()",
        )

    def test_grant_xproj_sets_granted_by(self):
        """grant_xproj must record the admin identity in xproj_granted_by.

        RED: function absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertEqual(
            row["xproj_granted_by"], "ops",
            f"xproj_granted_by must be 'ops'; got {row['xproj_granted_by']!r}",
        )

    def test_grant_xproj_does_not_affect_other_project(self):
        """grant_xproj(P2) must not alter P1's xproj columns.

        RED: function absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        p1_row = self._raw_project_row(self.P1)
        self.assertIsNone(
            p1_row["xproj_granted_at"],
            "P1's xproj_granted_at must remain NULL after granting P2",
        )

    def test_xproj_granted_returns_true_after_grant(self):
        """xproj_granted(con, P2) must return True after grant_xproj.

        RED: xproj_granted absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        result = s.xproj_granted(self.con, self.P2)
        self.assertTrue(
            result,
            f"xproj_granted(con, P2) must be True after grant; got {result!r}",
        )

    def test_xproj_granted_returns_false_before_grant(self):
        """xproj_granted(con, P2) must return False before any grant.

        RED: xproj_granted absent → AttributeError.
        """
        result = s.xproj_granted(self.con, self.P2)
        self.assertFalse(
            result,
            f"xproj_granted(con, P2) must be False before grant; got {result!r}",
        )


# ===========================================================================
# T9 — grant_xproj: idempotent (second grant → no-op, timestamp unchanged)
# ===========================================================================

class GrantXprojIdempotentTest(_TempDataHome):
    """A second call to grant_xproj must be a no-op: no error raised, and the
    timestamp stored by the first call must not be overwritten.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        s.grant_xproj(self.con, self.P2, "ops")
        self._ts_first = self._raw_project_row(self.P2)["xproj_granted_at"]

    def test_second_grant_does_not_raise(self):
        """A second grant_xproj call must not raise.

        RED: function absent → AttributeError.
        """
        try:
            s.grant_xproj(self.con, self.P2, "ops")
        except Exception as exc:
            self.fail(
                f"Second grant_xproj raised unexpectedly: {type(exc).__name__}: {exc}"
            )

    def test_second_grant_timestamp_unchanged(self):
        """A second grant must NOT update xproj_granted_at (idempotent).

        RED: function absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        ts_second = self._raw_project_row(self.P2)["xproj_granted_at"]
        self.assertEqual(
            ts_second,
            self._ts_first,
            f"Second grant must not update timestamp: "
            f"first={self._ts_first!r}, second={ts_second!r}",
        )

    def test_second_grant_by_unchanged(self):
        """A second grant must not overwrite xproj_granted_by.

        RED: function absent → AttributeError.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertEqual(
            row["xproj_granted_by"], "ops",
            f"xproj_granted_by must still be 'ops' after second grant; "
            f"got {row['xproj_granted_by']!r}",
        )


# ===========================================================================
# T10 — revoke_xproj callable + empty admin → PermissionError
# ===========================================================================

class RevokeXprojCallableTest(_TempDataHome):
    """revoke_xproj is callable; with empty admin table raises PermissionError
    containing the same 'no admin assigned' message as grant.

    RED: function absent → AttributeError.
    """

    def test_revoke_xproj_callable(self):
        """sandesh_db.revoke_xproj must be callable.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "revoke_xproj", None)),
            "sandesh_db.revoke_xproj is not callable — implement it (GREEN).",
        )

    def test_revoke_xproj_empty_admin_raises_permission_error(self):
        """revoke_xproj with empty admin table must raise PermissionError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.revoke_xproj(self.con, self.P2, "ops")

    def test_revoke_xproj_empty_admin_error_message(self):
        """The PermissionError must mention 'no admin assigned'.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError) as ctx:
            s.revoke_xproj(self.con, self.P2, "ops")
        self.assertIn(
            "no admin assigned",
            str(ctx.exception),
            f"PermissionError must say 'no admin assigned'; got {ctx.exception!r}",
        )


# ===========================================================================
# T11 — revoke_xproj: wrong 'by' → PermissionError
# ===========================================================================

class RevokeXprojWrongByTest(_TempDataHome):
    """revoke_xproj with by ≠ stored admin must raise PermissionError.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        # Pre-grant so revoke has something to refuse
        self.con.execute(
            "UPDATE project SET xproj_granted_at='2026-01-01', xproj_granted_by='ops' "
            "WHERE project_id=?",
            (self.P2,),
        )
        self.con.commit()

    def test_revoke_xproj_wrong_by_raises_permission_error(self):
        """revoke_xproj(con, P2, 'wrong') must raise PermissionError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.revoke_xproj(self.con, self.P2, "wrong")

    def test_revoke_xproj_wrong_by_error_message(self):
        """PermissionError must say 'only the Sandesh admin may grant/revoke'.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(PermissionError) as ctx:
            s.revoke_xproj(self.con, self.P2, "wrong")
        self.assertIn(
            "only the Sandesh admin may grant/revoke cross-project access",
            str(ctx.exception),
            f"PermissionError must contain exact phrase; got: {ctx.exception!r}",
        )

    def test_revoke_xproj_wrong_by_columns_unchanged(self):
        """After wrong-by rejection, xproj_granted_at must remain set.

        RED: function absent → AttributeError.
        """
        try:
            s.revoke_xproj(self.con, self.P2, "wrong")
        except PermissionError:
            pass
        row = self._raw_project_row(self.P2)
        self.assertIsNotNone(
            row["xproj_granted_at"],
            "xproj_granted_at must remain set after wrong-by revoke rejection",
        )


# ===========================================================================
# T12 — revoke_xproj: unknown project → ValueError
# ===========================================================================

class RevokeXprojUnknownProjectTest(_TempDataHome):
    """revoke_xproj(con, unknown, by) must raise ValueError containing
    "unknown project '<id>'".

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_revoke_xproj_unknown_project_raises_value_error(self):
        """revoke_xproj(con, 'Phantom', 'ops') must raise ValueError.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError):
            s.revoke_xproj(self.con, "Phantom", "ops")

    def test_revoke_xproj_unknown_project_error_message(self):
        """ValueError must name the unknown project id.

        RED: function absent → AttributeError.
        """
        with self.assertRaises(ValueError) as ctx:
            s.revoke_xproj(self.con, "Phantom", "ops")
        self.assertIn(
            "unknown project",
            str(ctx.exception),
            f"ValueError must say 'unknown project'; got {ctx.exception!r}",
        )
        self.assertIn(
            "Phantom",
            str(ctx.exception),
            f"ValueError must name the project id; got {ctx.exception!r}",
        )


# ===========================================================================
# T13 — revoke_xproj: happy path nulls both columns
# ===========================================================================

class RevokeXprojHappyPathTest(_TempDataHome):
    """revoke_xproj(con, P2, 'ops') on a granted project must NULL both
    xproj_granted_at and xproj_granted_by, and xproj_granted returns False.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        # Pre-grant via raw SQL so the revoke has something to clear.
        self.con.execute(
            "UPDATE project SET xproj_granted_at='2026-01-01 10:00:00', xproj_granted_by='ops' "
            "WHERE project_id=?",
            (self.P2,),
        )
        self.con.commit()

    def test_revoke_xproj_nulls_granted_at(self):
        """revoke_xproj must NULL xproj_granted_at.

        RED: function absent → AttributeError.
        """
        s.revoke_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_at"],
            f"xproj_granted_at must be NULL after revoke; got {row['xproj_granted_at']!r}",
        )

    def test_revoke_xproj_nulls_granted_by(self):
        """revoke_xproj must NULL xproj_granted_by.

        RED: function absent → AttributeError.
        """
        s.revoke_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_by"],
            f"xproj_granted_by must be NULL after revoke; got {row['xproj_granted_by']!r}",
        )

    def test_xproj_granted_returns_false_after_revoke(self):
        """xproj_granted(con, P2) must return False after revoke.

        RED: xproj_granted absent → AttributeError.
        """
        s.revoke_xproj(self.con, self.P2, "ops")
        result = s.xproj_granted(self.con, self.P2)
        self.assertFalse(
            result,
            f"xproj_granted(con, P2) must be False after revoke; got {result!r}",
        )

    def test_revoke_xproj_does_not_affect_other_project(self):
        """revoke_xproj(P2) must not alter P1's xproj columns.

        RED: function absent → AttributeError.
        """
        # Pre-grant P1 too via raw SQL
        self.con.execute(
            "UPDATE project SET xproj_granted_at='2026-01-01 09:00:00', xproj_granted_by='ops' "
            "WHERE project_id=?",
            (self.P1,),
        )
        self.con.commit()
        s.revoke_xproj(self.con, self.P2, "ops")
        p1_row = self._raw_project_row(self.P1)
        self.assertIsNotNone(
            p1_row["xproj_granted_at"],
            "P1's xproj_granted_at must remain set after revoking P2",
        )


# ===========================================================================
# T14 — revoke_xproj: idempotent on ungranted project
# ===========================================================================

class RevokeXprojIdempotentTest(_TempDataHome):
    """revoke_xproj on an already-ungranted project must be a no-op (no error).

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_revoke_xproj_ungranted_does_not_raise(self):
        """revoke_xproj(con, P2, 'ops') on an ungranted P2 must not raise.

        RED: function absent → AttributeError.
        """
        try:
            s.revoke_xproj(self.con, self.P2, "ops")
        except Exception as exc:
            self.fail(
                f"revoke_xproj on ungranted project raised unexpectedly: "
                f"{type(exc).__name__}: {exc}"
            )

    def test_revoke_xproj_ungranted_columns_remain_null(self):
        """After revoke on an ungranted project, both columns remain NULL.

        RED: function absent → AttributeError.
        """
        s.revoke_xproj(self.con, self.P2, "ops")
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_at"],
            "xproj_granted_at must remain NULL after idempotent revoke",
        )
        self.assertIsNone(
            row["xproj_granted_by"],
            "xproj_granted_by must remain NULL after idempotent revoke",
        )


# ===========================================================================
# T15 — xproj_granted: bool reader
# ===========================================================================

class XprojGrantedBoolReaderTest(_TempDataHome):
    """xproj_granted(con, pid) must return a bool: True if xproj_granted_at is
    non-null, False if null.

    RED: function absent → AttributeError.
    """

    def test_xproj_granted_callable(self):
        """sandesh_db.xproj_granted must be callable.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "xproj_granted", None)),
            "sandesh_db.xproj_granted is not callable — implement it (GREEN).",
        )

    def test_xproj_granted_false_for_ungranted_project(self):
        """xproj_granted(con, P1) must return False (no grant on P1).

        RED: function absent → AttributeError.
        """
        result = s.xproj_granted(self.con, self.P1)
        self.assertFalse(
            result,
            f"xproj_granted(con, P1) must be False for ungranted project; got {result!r}",
        )

    def test_xproj_granted_true_for_granted_project(self):
        """xproj_granted(con, P2) must return True after setting xproj_granted_at.

        RED: function absent → AttributeError.
        """
        # Set via raw SQL (independent of grant_xproj)
        self.con.execute(
            "UPDATE project SET xproj_granted_at='2026-01-01', xproj_granted_by='ops' "
            "WHERE project_id=?",
            (self.P2,),
        )
        self.con.commit()
        result = s.xproj_granted(self.con, self.P2)
        self.assertTrue(
            result,
            f"xproj_granted(con, P2) must be True for granted project; got {result!r}",
        )

    def test_xproj_granted_returns_bool_type(self):
        """xproj_granted must return a bool (not truthy int or str).

        RED: function absent → AttributeError.
        """
        result = s.xproj_granted(self.con, self.P1)
        self.assertIsInstance(
            result, bool,
            f"xproj_granted must return bool; got {type(result).__name__!r}",
        )


# ===========================================================================
# T16 — grant/revoke round-trip: xproj_granted flips correctly
# ===========================================================================

class GrantRevokeRoundTripTest(_TempDataHome):
    """Full round-trip: grant → granted; revoke → not granted; grant again → granted.
    Uses assign_admin + grant_xproj + revoke_xproj + xproj_granted together.

    RED: any missing function → AttributeError.
    """

    def test_grant_revoke_grant_round_trip(self):
        """xproj_granted(P2) flips True → False → True over grant/revoke/grant.

        RED: any function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        self.assertFalse(s.xproj_granted(self.con, self.P2), "should start ungranted")

        s.grant_xproj(self.con, self.P2, "ops")
        self.assertTrue(s.xproj_granted(self.con, self.P2), "should be granted after grant")

        s.revoke_xproj(self.con, self.P2, "ops")
        self.assertFalse(s.xproj_granted(self.con, self.P2), "should be revoked after revoke")

        s.grant_xproj(self.con, self.P2, "ops")
        self.assertTrue(s.xproj_granted(self.con, self.P2), "should be re-granted after second grant")

    def test_p1_unaffected_during_p2_grant_revoke_cycle(self):
        """P1's grant state must remain False throughout P2's grant/revoke cycle.

        RED: any function absent → AttributeError.
        """
        s.assign_admin(self.con, "ops")
        s.grant_xproj(self.con, self.P2, "ops")
        self.assertFalse(
            s.xproj_granted(self.con, self.P1),
            "P1 must remain ungranted while P2 is granted",
        )
        s.revoke_xproj(self.con, self.P2, "ops")
        self.assertFalse(
            s.xproj_granted(self.con, self.P1),
            "P1 must remain ungranted while P2 is revoked",
        )


# ===========================================================================
# T17 — CLI: grant subcommand registration + required flags
# ===========================================================================

class CliGrantSubcommandTest(_TempDataHome):
    """sandesh grant --cross-project --project P2 --by admin must:
    - be registered (not SystemExit 2 due to unknown subcommand)
    - require --cross-project flag (store_true, required)
    - NOT inherit parents=[common] (its --project is the TARGET)

    RED: 'grant' subcommand not registered → cli.main(['grant', ...]) exits with
    argparse error (SystemExit 2 / 'invalid choice: grant').
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_grant_subcommand_not_unknown_subcommand_error(self):
        """cli.main(['grant', '--cross-project', '--project', 'P2', '--by', 'ops'])
        must NOT exit with code 2 as 'invalid choice: grant'.

        RED: 'grant' not registered → argparse exits 2 with 'invalid choice'.
        """
        rc, out, err = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        # If it exits 2 AND the error mentions 'invalid choice: grant', that is RED.
        combined = out + err
        if rc == 2 and "invalid choice" in combined and "grant" in combined:
            self.fail(
                "cli.main(['grant', ...]) got argparse 'invalid choice: grant' — "
                "the 'grant' subcommand is not yet registered (RED)."
            )

    def test_grant_without_cross_project_flag_exits_nonzero(self):
        """sandesh grant --project P2 --by ops (missing --cross-project) must exit non-zero.

        --cross-project is required; omitting it must cause a CLI error.
        RED: subcommand not registered at all → SystemExit 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["grant", "--project", self.P2, "--by", "ops"])
        self.assertNotEqual(
            rc, 0,
            "cli.main(['grant', '--project', P2, '--by', ops]) (no --cross-project) "
            "must exit non-zero; --cross-project is required.",
        )

    def test_grant_with_correct_args_exits_zero_and_confirms_project(self):
        """sandesh grant --cross-project --project P2 --by ops must exit 0 and
        print a confirmation containing the project id.

        RED: subcommand not registered → SystemExit 2.
        """
        rc, out, err = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        self.assertEqual(
            rc, 0,
            f"cli.main(['grant', '--cross-project', '--project', P2, '--by', ops]) "
            f"must exit 0; got rc={rc!r}. out={out!r} err={err!r}",
        )
        combined = out + err
        self.assertIn(
            self.P2,
            combined,
            f"grant confirmation must mention the project id {self.P2!r}; "
            f"got: {combined!r}",
        )

    def test_grant_sets_grant_on_project_row(self):
        """After a successful CLI grant, xproj_granted_at must be non-null in the DB.

        RED: subcommand not registered → no write happens.
        """
        rc, _, _ = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        if rc != 0:
            self.skipTest("grant subcommand not yet registered — skipping DB state check")
        # Refresh the connection to see the written data.
        self.con.close()
        self.con = s.connect()
        row = self._raw_project_row(self.P2)
        self.assertIsNotNone(
            row["xproj_granted_at"],
            "xproj_granted_at must be non-null after a successful CLI grant",
        )


# ===========================================================================
# T18 — CLI: revoke subcommand registration + flags
# ===========================================================================

class CliRevokeSubcommandTest(_TempDataHome):
    """sandesh revoke --cross-project --project P2 --by admin mirrors grant.
    Same shape: no parents=[common]; --cross-project required.

    RED: 'revoke' subcommand not registered → SystemExit 2.
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()
        # Pre-grant P2 via raw SQL so revoke has something to act on.
        self.con.execute(
            "UPDATE project SET xproj_granted_at='2026-01-01', xproj_granted_by='ops' "
            "WHERE project_id=?",
            (self.P2,),
        )
        self.con.commit()

    def test_revoke_subcommand_not_unknown_subcommand_error(self):
        """cli.main(['revoke', '--cross-project', '--project', 'P2', '--by', 'ops'])
        must NOT exit with 'invalid choice: revoke'.

        RED: 'revoke' not registered → argparse exits 2 with 'invalid choice'.
        """
        rc, out, err = self._run_cli([
            "revoke", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        combined = out + err
        if rc == 2 and "invalid choice" in combined and "revoke" in combined:
            self.fail(
                "cli.main(['revoke', ...]) got 'invalid choice: revoke' — "
                "the 'revoke' subcommand is not yet registered (RED)."
            )

    def test_revoke_without_cross_project_flag_exits_nonzero(self):
        """sandesh revoke --project P2 --by ops (missing --cross-project) must exit non-zero.

        RED: subcommand not registered → SystemExit 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["revoke", "--project", self.P2, "--by", "ops"])
        self.assertNotEqual(
            rc, 0,
            "cli.main(['revoke', '--project', P2, '--by', ops]) without --cross-project "
            "must exit non-zero; --cross-project is required.",
        )

    def test_revoke_with_correct_args_exits_zero_and_confirms_project(self):
        """sandesh revoke --cross-project --project P2 --by ops must exit 0 and
        print a confirmation containing the project id.

        RED: subcommand not registered → SystemExit 2.
        """
        rc, out, err = self._run_cli([
            "revoke", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        self.assertEqual(
            rc, 0,
            f"cli.main(['revoke', '--cross-project', '--project', P2, '--by', ops]) "
            f"must exit 0; got rc={rc!r}. out={out!r} err={err!r}",
        )
        combined = out + err
        self.assertIn(
            self.P2,
            combined,
            f"revoke confirmation must mention the project id {self.P2!r}; "
            f"got: {combined!r}",
        )

    def test_revoke_clears_grant_on_project_row(self):
        """After a successful CLI revoke, xproj_granted_at must be NULL in the DB.

        RED: subcommand not registered → no write happens.
        """
        rc, _, _ = self._run_cli([
            "revoke", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        if rc != 0:
            self.skipTest("revoke subcommand not yet registered — skipping DB state check")
        self.con.close()
        self.con = s.connect()
        row = self._raw_project_row(self.P2)
        self.assertIsNone(
            row["xproj_granted_at"],
            "xproj_granted_at must be NULL after a successful CLI revoke",
        )


# ===========================================================================
# T19 — CLI: 'admin' subcommand must NOT exist (AC10)
# ===========================================================================

class CliAdminSubcommandAbsentTest(_TempDataHome):
    """cli.main(['admin']) must produce an argparse error (SystemExit 2).

    The spec mandates NO 'admin' CLI subcommand exists — assignment is only via
    install.sh inline python.

    This test is GREEN-on-arrival (the subcommand doesn't exist yet), and must
    REMAIN GREEN after all implementation work is done.
    """

    def test_admin_subcommand_absent_exits_2(self):
        """cli.main(['admin']) must exit 2 (argparse unknown command error).

        Spec: no 'sandesh admin' subcommand exists (PRD O3 boundary).
        """
        rc, out, err = self._run_cli(["admin"])
        self.assertEqual(
            rc, 2,
            f"cli.main(['admin']) must exit 2 (unknown subcommand); "
            f"got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_admin_subcommand_not_in_help_output(self):
        """'admin' must not appear as a subcommand in 'sandesh --help'.

        RED if it somehow gets added — PRD O3: admin surface must not be agent-reachable.
        """
        rc, out, err = self._run_cli(["--help"])
        combined = out + err
        # 'admin' should not appear as a listed subcommand
        # We check that if it appears it's not in a subcommand position.
        # Use a conservative check: 'admin' as a standalone word in subcommand listing.
        import re
        # Look for 'admin' as a subcommand in usage/choices (not as part of another word)
        self.assertNotRegex(
            combined,
            r"\{[^}]*\badmin\b[^}]*\}",
            "'admin' must not appear in the argparse subcommand choices listing",
        )


# ===========================================================================
# T20 — CLI: grant/revoke wrong-by errors exit non-zero with correct messages
# ===========================================================================

class CliGrantRevokeErrorPathTest(_TempDataHome):
    """CLI grant/revoke propagate PermissionError messages and exit non-zero.

    RED: subcommands not registered → SystemExit 2 (wrong exit, wrong reason).
    """

    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO admin (id, name) VALUES (1, 'ops')")
        self.con.commit()

    def test_cli_grant_wrong_by_exits_nonzero(self):
        """sandesh grant --cross-project --project P2 --by wrongadmin must exit non-zero.

        RED: subcommand absent → exits 2 but for wrong reason.
        """
        rc, out, err = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "wrongadmin",
        ])
        self.assertNotEqual(
            rc, 0,
            "CLI grant with wrong --by must exit non-zero; "
            f"got rc={rc!r}, out={out!r}, err={err!r}",
        )

    def test_cli_grant_wrong_by_error_message(self):
        """sandesh grant with wrong --by must output a message about admin-only.

        RED: subcommand absent → exits with argparse message, not the permission msg.
        """
        rc, out, err = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "wrongadmin",
        ])
        combined = out + err
        if rc != 0 and "invalid choice" not in combined:
            # Subcommand is registered; check the permission message
            self.assertTrue(
                "only the Sandesh admin" in combined or "admin" in combined.lower(),
                f"CLI grant wrong-by output must reference admin-only restriction; "
                f"got: {combined!r}",
            )

    def test_cli_revoke_wrong_by_exits_nonzero(self):
        """sandesh revoke --cross-project --project P2 --by wrongadmin must exit non-zero.

        RED: subcommand absent → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli([
            "revoke", "--cross-project", "--project", self.P2, "--by", "wrongadmin",
        ])
        self.assertNotEqual(
            rc, 0,
            "CLI revoke with wrong --by must exit non-zero; "
            f"got rc={rc!r}, out={out!r}, err={err!r}",
        )

    def test_cli_grant_empty_admin_table_exits_nonzero(self):
        """sandesh grant --cross-project --project P2 --by ops with NO admin
        row in the DB must exit non-zero.

        RED: subcommand absent → exits 2 for wrong reason.
        """
        # Remove the admin row seeded in setUp
        self.con.execute("DELETE FROM admin")
        self.con.commit()
        rc, out, err = self._run_cli([
            "grant", "--cross-project", "--project", self.P2, "--by", "ops",
        ])
        self.assertNotEqual(
            rc, 0,
            "CLI grant with empty admin table must exit non-zero; "
            f"got rc={rc!r}, out={out!r}, err={err!r}",
        )


# ===========================================================================
# T21 — install.sh content: inline python admin assignment
# ===========================================================================

class InstallShAdminAssignmentTest(unittest.TestCase):
    """install.sh must assign the admin via an inline python interpreter call
    (not a 'sandesh admin' CLI invocation) when $SANDESH_ADMIN is set.

    The inline call must appear AFTER the consolidate block.

    RED: the inline python assignment does not yet exist in install.sh.
    """

    @classmethod
    def setUpClass(cls):
        with open(_INSTALL_SH, encoding="utf-8") as fh:
            cls._content = fh.read()
        cls._lines = cls._content.splitlines()

    def _line_index(self, pattern):
        """Return the 0-based index of the first line matching pattern, or -1."""
        for i, line in enumerate(self._lines):
            if pattern in line:
                return i
        return -1

    def test_install_sh_contains_sandesh_admin_env_variable_check(self):
        """install.sh must reference $SANDESH_ADMIN to conditionally assign.

        RED: no $SANDESH_ADMIN reference in install.sh yet.
        """
        idx = self._line_index("SANDESH_ADMIN")
        self.assertGreater(
            idx, -1,
            "install.sh must reference $SANDESH_ADMIN to drive the admin assignment; "
            "no such reference found. GREEN must add the admin assignment block.",
        )

    def test_install_sh_has_inline_python_for_admin_assignment(self):
        """install.sh must contain an embedded venv-python invocation that calls assign_admin.

        The embedded program (inline -c string OR heredoc) must import sandesh_db (or
        sandesh.sandesh_db) and call assign_admin — NOT call `sandesh admin` (which must
        not exist as a CLI verb).  Either form is accepted:
          "$VENV/bin/python" -c "... assign_admin ..."
          "$VENV/bin/python" - <<'PY'\n... assign_admin ...\nPY

        RED: no venv-python assignment block referencing assign_admin in install.sh yet.
        """
        # Contract: a python invocation exists AND assign_admin appears in the file
        # (in the heredoc body or on the same line as -c).  We verify both independently:
        # (1) a line that invokes python via the venv binary or bare python3,
        # (2) assign_admin is called somewhere in the file content.
        has_python_invocation = any(
            'python' in line
            for line in self._lines
        )
        has_assign_admin = 'assign_admin' in self._content
        self.assertTrue(
            has_python_invocation and has_assign_admin,
            "install.sh must contain a venv-python invocation (via -c or heredoc) that "
            "references assign_admin.  Either 'python' invocation or 'assign_admin' call "
            "is missing.  GREEN must add the $SANDESH_ADMIN admin assignment block "
            "(per PRD O3: no CLI admin verb; -c and heredoc forms are both acceptable).",
        )

    def test_install_sh_reads_admin_name_from_env_inside_python(self):
        """install.sh must read the admin name from the environment INSIDE python.

        PRD-global-store O3 (injection safety): the admin name must never be
        shell-interpolated into the embedded python code — the program reads it
        itself via os.environ["SANDESH_ADMIN"]. This pins the exact env-read
        form alongside the mechanism-agnostic checks above.
        """
        self.assertIn(
            'os.environ["SANDESH_ADMIN"]',
            self._content,
            "install.sh must read the admin name via os.environ[\"SANDESH_ADMIN\"] "
            "INSIDE the embedded python program (PRD O3 injection safety) — "
            "shell-interpolating $SANDESH_ADMIN into the code is forbidden.",
        )

    def test_install_sh_does_not_call_sandesh_admin_cli(self):
        """install.sh must NOT invoke 'sandesh admin' as a CLI subcommand.

        PRD O3: admin assignment must NOT be an agent-reachable CLI surface.
        RED (this is a GREEN-guard): if 'sandesh admin' call exists, it violates PRD O3.
        """
        has_cli_admin = any(
            'sandesh' in line and 'admin' in line
            and not line.strip().startswith('#')
            and 'assign_admin' not in line
            and 'SANDESH_ADMIN' not in line
            for line in self._lines
        )
        self.assertFalse(
            has_cli_admin,
            "install.sh contains a 'sandesh admin' CLI invocation — this is FORBIDDEN "
            "(PRD O3: admin assignment must not be an agent-reachable CLI verb). "
            "Use the inline python -c approach instead.",
        )

    def test_install_sh_admin_assignment_after_consolidate_block(self):
        """The admin assignment block (assign_admin call) must appear AFTER the consolidate block.

        install.sh order: migrate → consolidate → admin assignment.
        Mechanism-agnostic: the assign_admin call may be on an inline -c line or inside
        a heredoc body — we locate it by finding the line that contains 'assign_admin'
        (the call site in either form).
        RED: no assign_admin reference present yet → index is -1.
        """
        consolidate_idx = self._line_index("consolidate")
        # Find the first line that contains 'assign_admin' — works for both
        # inline (-c "... assign_admin ...") and heredoc body ("s.assign_admin(...)").
        assign_admin_idx = self._line_index("assign_admin")

        self.assertGreater(
            assign_admin_idx, -1,
            "install.sh has no 'assign_admin' call. "
            "GREEN must add the admin assignment block after the consolidate block.",
        )
        self.assertGreater(
            consolidate_idx, -1,
            "install.sh has no 'consolidate' invocation — cannot verify ordering.",
        )
        self.assertGreater(
            assign_admin_idx, consolidate_idx,
            f"Admin assignment (line {assign_admin_idx + 1}) must appear AFTER "
            f"consolidate (line {consolidate_idx + 1}).",
        )

    def test_install_sh_admin_assignment_is_conditional_on_env_var(self):
        """The admin assignment block must be conditional (guarded by $SANDESH_ADMIN being set).

        When $SANDESH_ADMIN is unset the assignment is skipped with a notice.
        RED: no assignment block present yet.
        """
        # Find the line range of the SANDESH_ADMIN conditional block
        has_conditional = any(
            'SANDESH_ADMIN' in line
            and any(kw in line for kw in ('-n', '-z', 'if', '${'))
            for line in self._lines
        )
        self.assertTrue(
            has_conditional,
            "install.sh must have a conditional guard on $SANDESH_ADMIN "
            "(skip the admin assignment when the variable is unset). "
            "RED: no such conditional found.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
