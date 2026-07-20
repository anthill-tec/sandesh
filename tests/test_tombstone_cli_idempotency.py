"""test_tombstone_cli_idempotency.py — RED tests for CR-SAN-045 Cycle 3.

Covers §S2 item 4 + AC15: CLI idempotency of `cmd_tombstone` (cli.py:313).

A second `sandesh tombstone --yes` on an already-tombstoned project must:
  - catch the library's "already tombstoned" ValueError,
  - print an already-gone/already-tombstoned line,
  - exit **0** (NOT 1).

All OTHER ValueError/PermissionError paths (unknown project, active/never-
archived project, non-admin `by`) still exit 1 — untouched by this CR.
`--dry-run` / `--yes`/confirm gate untouched.

This is CLI-only: the library still raises "already tombstoned" on a second
tombstone_project() call — test_lifecycle_tombstone.py::TombstoneIdempotenceTest
stays green and is not touched here.

Expected RED: on current code, cmd_tombstone's second call raises the
library's ValueError("project '<id>' is already tombstoned") which is caught
by the generic `except (ValueError, PermissionError, RuntimeError)` clause →
prints "[sandesh] project '<id>' is already tombstoned" to stderr and calls
sys.exit(1). The exit-code-0 assertion in
TombstoneCliSecondCallIdempotentTest.test_second_tombstone_is_idempotent_exit_zero
is the RED gate this file pins.

Run via the crucible (uses .venv interpreter):
  WORKFLOW_CYCLE_ID=3 python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_tombstone_cli_idempotency --agent CR-SAN-045-C3-RED
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions a single grammar-valid project ('Gone') with its own
    Mainline registered, and the super-admin assigned.
    """

    PROJECT = "Gone"
    ADMIN = "ops"
    ML = "Mainline - Gone"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-tombstone-cli-idem-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.PROJECT)

        self.con = s.connect()

        s.register(self.con, self.ML, kind="mainline", project=self.PROJECT)
        s.assign_admin(self.con, self.ADMIN)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process capturing stdout+stderr.
        Returns (rc, out, err).
        """
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _raw_project_state(self, project_id):
        row = self.con.execute(
            "SELECT state FROM project WHERE project_id=?", (project_id,)
        ).fetchone()
        return row["state"] if row else None


# ---------------------------------------------------------------------------
# T1 — THE RED: second CLI tombstone is idempotent (exit 0 + already-gone line)
# ---------------------------------------------------------------------------

class TombstoneCliSecondCallIdempotentTest(_TempDataHome):
    """AC15 / §S2 item 4: a second `tombstone --yes` on an already-tombstoned
    project must exit 0 and print an already-gone/already-tombstoned line.

    RED on current code: the second call surfaces the library's "already
    tombstoned" ValueError through cmd_tombstone's generic except clause →
    prints "[sandesh] project '...' is already tombstoned" on stderr and
    exits 1, not 0.
    """

    def test_second_tombstone_is_idempotent_exit_zero(self):
        """Second tombstone --yes on the now-tombstoned project must exit 0
        AND print output matching /already (gone|tombstoned)/i.

        RED: currently exits 1 (library ValueError falls through the generic
        except clause) — the exit-code assertion is the RED gate.
        """
        s.archive(self.con, self.PROJECT, self.ML, wait_secs=0.1)

        # First tombstone — the pre-existing, already-GREEN path.
        rc1, out1, err1 = self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])
        self.assertEqual(
            rc1, 0,
            f"precondition: first tombstone must exit 0; "
            f"got rc={rc1!r} out={out1!r} err={err1!r}")

        # Second tombstone — must be reported as idempotent, not an error.
        rc2, out2, err2 = self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])
        combined2 = out2 + err2
        self.assertEqual(
            rc2, 0,
            f"second (idempotent) tombstone --yes must exit 0, not treat "
            f"'already tombstoned' as an error; got rc={rc2!r} out={out2!r} "
            f"err={err2!r}")
        self.assertRegex(
            combined2, r"already (gone|tombstoned)",
            f"second tombstone output must match /already (gone|tombstoned)/i; "
            f"got out={out2!r} err={err2!r}")

    def test_second_tombstone_state_still_tombstoned(self):
        """State must remain 'tombstoned' after the idempotent second call —
        the idempotency path must not corrupt or reset the tracker state.

        Pin: passes already on current (buggy) code, since the guard raises
        before any state mutation on the second call either way.
        """
        s.archive(self.con, self.PROJECT, self.ML, wait_secs=0.1)
        self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])
        self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])

        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.PROJECT)
        self.assertEqual(
            state, "tombstoned",
            f"state must remain 'tombstoned' after idempotent second call; "
            f"got {state!r}")


# ---------------------------------------------------------------------------
# T2 — pin: project appears in no listing after the idempotent second call
# ---------------------------------------------------------------------------

class TombstoneCliListingPinTest(_TempDataHome):
    """AC15: 'The project appears in no `projects` listing.'

    Pin: passes already on current code — list_projects() already excludes
    tombstoned projects by default (unrelated to this CR's exit-code fix).
    """

    def test_project_absent_from_list_projects_after_second_call(self):
        """After both tombstone calls, list_projects() must not contain the
        project id. list_projects() returns plain strings, not rows.
        """
        s.archive(self.con, self.PROJECT, self.ML, wait_secs=0.1)
        self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])
        self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])

        projects = s.list_projects()
        self.assertNotIn(
            self.PROJECT, projects,
            f"{self.PROJECT!r} must not appear in list_projects(); got {projects!r}")
        self.assertEqual(
            projects, [],
            f"list_projects() must be empty ('Gone' was the only project and "
            f"is now tombstoned); got {projects!r}")


# ---------------------------------------------------------------------------
# T3 — regression pins: all OTHER ValueError/PermissionError paths exit 1
# ---------------------------------------------------------------------------

class TombstoneCliOtherErrorPathsStillExitOneTest(_TempDataHome):
    """Pins: unknown project / active (never-archived) project / non-admin
    `by` still exit 1 — this CR ONLY changes the 'already tombstoned' path.

    These should already pass on current code (they are the pre-existing,
    untouched behaviour); included so a GREEN implementation that widens the
    exit-0 catch too far gets caught immediately.
    """

    def test_unknown_project_exits_one(self):
        """tombstone of a never-set-up project must still exit 1."""
        rc, out, err = self._run_cli(
            ["tombstone", "--project", "Nope", "--by", self.ADMIN, "--yes"])
        self.assertEqual(
            rc, 1,
            f"unknown project must exit 1; got rc={rc!r} out={out!r} err={err!r}")
        self.assertIn(
            "[sandesh]", err,
            f"unknown-project error must use the '[sandesh]' stderr prefix; "
            f"got err={err!r}")

    def test_active_never_archived_project_exits_one(self):
        """tombstone of an ACTIVE (never-archived) project must still exit 1
        with the 'archive it first' guard message.
        """
        # self.PROJECT was never archived in this test — still 'active'.
        rc, out, err = self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--yes"])
        self.assertEqual(
            rc, 1,
            f"active project must exit 1; got rc={rc!r} out={out!r} err={err!r}")
        self.assertIn(
            "archive", err.lower(),
            f"active-project error must mention 'archive it first'; got err={err!r}")

    def test_non_admin_by_exits_one(self):
        """tombstone with a non-admin `--by` (the project's own Mainline)
        must still exit 1 (PermissionError path).
        """
        s.archive(self.con, self.PROJECT, self.ML, wait_secs=0.1)
        rc, out, err = self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ML, "--yes"])
        self.assertEqual(
            rc, 1,
            f"non-admin --by must exit 1; got rc={rc!r} out={out!r} err={err!r}")
        self.assertIn(
            "[sandesh]", err,
            f"non-admin-by error must use the '[sandesh]' stderr prefix; "
            f"got err={err!r}")
        # Negative: state must not have transitioned to tombstoned.
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.PROJECT)
        self.assertEqual(
            state, "archived",
            f"state must remain 'archived' after rejected non-admin tombstone; "
            f"got {state!r}")


# ---------------------------------------------------------------------------
# T4 — pin: --dry-run on an archived (not-yet-tombstoned) project untouched
# ---------------------------------------------------------------------------

class TombstoneCliDryRunUntouchedTest(_TempDataHome):
    """Pin: --dry-run on an archived project still works and writes nothing —
    the dry-run gate is untouched by this CR (which only changes the
    already-tombstoned --yes path).
    """

    def setUp(self):
        super().setUp()
        s.archive(self.con, self.PROJECT, self.ML, wait_secs=0.1)

    def test_dry_run_on_archived_project_exits_zero(self):
        rc, out, err = self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--dry-run"])
        self.assertEqual(
            rc, 0,
            f"--dry-run on archived project must exit 0; "
            f"got rc={rc!r} out={out!r} err={err!r}")

    def test_dry_run_on_archived_project_writes_nothing(self):
        """--dry-run must not change state — stays 'archived', never
        'tombstoned'.
        """
        self._run_cli(
            ["tombstone", "--project", self.PROJECT, "--by", self.ADMIN, "--dry-run"])
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.PROJECT)
        self.assertEqual(
            state, "archived",
            f"--dry-run must not change state; got {state!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
