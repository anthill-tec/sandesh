"""test_projects_listing_cli.py — RED tests for CR-SAN-029.

Covers AC1–AC4: CLI `sandesh projects --all` flag.

  sandesh projects           — lists active + archived; excludes tombstoned (default unchanged)
  sandesh projects --all     — includes tombstoned rows (same 3-col view, state='tombstoned')
  Flag help text: 'include tombstoned projects (permanent markers)'

Expected RED for AC2/AC3:
  argparse "unrecognized arguments: --all" → SystemExit(2)
  (--all flag not yet registered on the projects subparser)

Expected PASS (current behaviour already correct):
  AC1 — default listing excludes tombstoned (already the current WHERE clause)
  AC4 — empty store prints '(no projects set up)', exits 0 (already works)

These pass-at-RED pins are intentional: they lock in the unchanged default behaviour
BEFORE the GREEN implementation lands.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_projects_listing_cli --agent CR-SAN-029-A-RED
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

# Repo root — resolve from this file so it works regardless of cwd.
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

    setUp provisions three projects — one per lifecycle state — plus the
    admin and a cross-project grant on the tombstoned project, mirroring the
    AC3 requirement.

    Projects:
      ActiveProj    — state='active'
      ArchivedProj  — state='archived'
      TombedProj    — state='tombstoned'  (granted, archived, then tombstoned)

    Only a Mainline address per project is strictly necessary (archive requires
    the project's own Mainline as the `by` argument; tombstone requires the
    super-admin name).
    """

    ACTIVE   = "ActiveProj"
    ARCHIVED = "ArchivedProj"
    TOMBED   = "TombedProj"
    ADMIN    = "ops"

    ML_ACTIVE   = "Mainline - ActiveProj"
    ML_ARCHIVED = "Mainline - ArchivedProj"
    ML_TOMBED   = "Mainline - TombedProj"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-projects-listing-cli-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision all three projects.
        s.setup(self.ACTIVE)
        s.setup(self.ARCHIVED)
        s.setup(self.TOMBED)

        self.con = s.connect()

        # Register a Mainline address for each project (needed for archive authz).
        s.register(self.con, self.ML_ACTIVE,   kind="mainline", project=self.ACTIVE)
        s.register(self.con, self.ML_ARCHIVED, kind="mainline", project=self.ARCHIVED)
        s.register(self.con, self.ML_TOMBED,   kind="mainline", project=self.TOMBED)

        # Assign admin (required for tombstone_project authz).
        s.assign_admin(self.con, self.ADMIN)

        # Grant TombedProj cross-project BEFORE archiving/tombstoning — AC3 requires
        # that the grant columns survive all lifecycle transitions.
        s.grant_xproj(self.con, self.TOMBED, self.ADMIN)

        # Archive ArchivedProj (wait_secs=0.1 reaps any stale notifier quickly).
        s.archive(self.con, self.ARCHIVED, self.ML_ARCHIVED, wait_secs=0.1)

        # Archive then tombstone TombedProj.
        s.archive(self.con, self.TOMBED, self.ML_TOMBED, wait_secs=0.1)
        s.tombstone_project(self.con, self.TOMBED, self.ADMIN, wait_secs=0.1)

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


# ---------------------------------------------------------------------------
# AC1 — default listing unchanged (should PASS against current code)
# ---------------------------------------------------------------------------

class DefaultListingTest(_TempDataHome):
    """AC1 — `sandesh projects` (no flag) excludes tombstoned rows.

    This pin locks in the unchanged default behaviour. It must pass BOTH before
    (RED phase) and after (GREEN phase) the --all flag is added.
    """

    def test_default_listing_exits_zero(self):
        """sandesh projects exits 0 with active/archived projects present."""
        rc, _out, _err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects should exit 0; got rc={rc!r}")

    def test_default_listing_contains_active_project(self):
        """sandesh projects output must contain the active project id."""
        _rc, out, _err = self._run_cli(["projects"])
        self.assertIn(
            self.ACTIVE, out,
            f"active project '{self.ACTIVE}' must appear in default listing; got:\n{out}",
        )

    def test_default_listing_contains_archived_project(self):
        """sandesh projects output must contain the archived project id."""
        _rc, out, _err = self._run_cli(["projects"])
        self.assertIn(
            self.ARCHIVED, out,
            f"archived project '{self.ARCHIVED}' must appear in default listing; got:\n{out}",
        )

    def test_default_listing_excludes_tombstoned_project(self):
        """sandesh projects output must NOT contain the tombstoned project id.

        AC1 spec: output does NOT contain the tombstoned id.
        """
        _rc, out, _err = self._run_cli(["projects"])
        self.assertNotIn(
            self.TOMBED, out,
            f"tombstoned project '{self.TOMBED}' must NOT appear in default listing; got:\n{out}",
        )

    def test_default_listing_shows_correct_state_for_active(self):
        """Active row in default listing shows state 'active' ON the ActiveProj line.

        AC7 (CR-SAN-030): line-anchored assertion — find the line containing
        the project id, then assert the state cell on THAT line.
        """
        _rc, out, _err = self._run_cli(["projects"])
        active_lines = [ln for ln in out.splitlines() if self.ACTIVE in ln]
        self.assertTrue(
            len(active_lines) >= 1,
            f"Expected at least one output line containing '{self.ACTIVE}'; got:\n{out}",
        )
        self.assertTrue(
            any("active" in ln for ln in active_lines),
            f"None of the '{self.ACTIVE}' lines contains state 'active'; "
            f"lines: {active_lines!r}\nfull output:\n{out}",
        )

    def test_default_listing_shows_correct_state_for_archived(self):
        """Archived row in default listing shows state 'archived' ON the ArchivedProj line.

        AC7 (CR-SAN-030): line-anchored assertion — find the line containing
        the project id, then assert the state cell on THAT line.
        """
        _rc, out, _err = self._run_cli(["projects"])
        archived_lines = [ln for ln in out.splitlines() if self.ARCHIVED in ln]
        self.assertTrue(
            len(archived_lines) >= 1,
            f"Expected at least one output line containing '{self.ARCHIVED}'; got:\n{out}",
        )
        self.assertTrue(
            any("archived" in ln for ln in archived_lines),
            f"None of the '{self.ARCHIVED}' lines contains state 'archived'; "
            f"lines: {archived_lines!r}\nfull output:\n{out}",
        )


# ---------------------------------------------------------------------------
# AC2 — `--all` includes tombstoned rows (FAIL until flag is added)
# ---------------------------------------------------------------------------

class AllFlagIncludesTombstonedTest(_TempDataHome):
    """AC2 — `sandesh projects --all` includes all three states.

    Expected RED: SystemExit(2) — argparse 'unrecognised arguments: --all'
    because the flag is not yet registered on the projects subparser.
    """

    def test_all_flag_exits_zero(self):
        """sandesh projects --all must exit 0.

        RED: SystemExit(2) — unrecognised flag.
        """
        rc, _out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")

    def test_all_flag_contains_active_project(self):
        """sandesh projects --all output must contain the active project id.

        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        self.assertIn(
            self.ACTIVE, out,
            f"active project '{self.ACTIVE}' must appear in --all listing; got:\n{out}",
        )

    def test_all_flag_contains_archived_project(self):
        """sandesh projects --all output must contain the archived project id.

        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        self.assertIn(
            self.ARCHIVED, out,
            f"archived project '{self.ARCHIVED}' must appear in --all listing; got:\n{out}",
        )

    def test_all_flag_contains_tombstoned_project(self):
        """sandesh projects --all output must contain the tombstoned project id.

        AC2 spec: output contains all three ids.
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        self.assertIn(
            self.TOMBED, out,
            f"tombstoned project '{self.TOMBED}' must appear in --all listing; got:\n{out}",
        )

    def test_all_flag_tombstoned_row_shows_tombstoned_state(self):
        """The tombstoned project's STATE column must contain 'tombstoned' under --all.

        AC2 spec: "the tombstoned project's row contains the word 'tombstoned' in the
        STATE column".
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        # The word 'tombstoned' must appear in the output for the tombstoned project.
        self.assertIn(
            "tombstoned", out,
            f"'tombstoned' must appear as a STATE value under --all; got:\n{out}",
        )

    def test_all_flag_tombstoned_project_row_present_with_state(self):
        """The tombstoned project row must show both its id and 'tombstoned' state.

        Asserts the two values co-exist in output (membership check per project).
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        # Both project id and state word must appear — not just one.
        self.assertIn(self.TOMBED, out,
                      f"'{self.TOMBED}' missing from --all output;\n{out}")
        self.assertIn("tombstoned", out,
                      f"'tombstoned' state missing from --all output;\n{out}")


# ---------------------------------------------------------------------------
# AC3 — verbatim grant cell: ✓ survives lifecycle transitions (FAIL until flag)
# ---------------------------------------------------------------------------

class GrantCellSurvivesTransitionsTest(_TempDataHome):
    """AC3 — a granted-then-archived-then-tombstoned project still shows ✓ under --all.

    Lifecycle transitions never clear xproj_granted_at, so CROSS-PROJECT shows ✓.
    RED: SystemExit(2) — --all not yet registered.
    """

    def test_tombstoned_granted_project_shows_checkmark(self):
        """CROSS-PROJECT cell for TombedProj must render '✓' under --all.

        AC3 spec: "a project granted cross-project then archived then tombstoned
        renders ✓ in CROSS-PROJECT under --all (grant columns untouched by
        transitions)".
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        # The tombstoned project must appear with a ✓ grant marker.
        self.assertIn(
            self.TOMBED, out,
            f"'{self.TOMBED}' must appear in --all output; got:\n{out}",
        )
        self.assertIn(
            "✓", out,
            f"'✓' (cross-project granted) must appear in --all output for "
            f"TombedProj; got:\n{out}",
        )

    def test_tombstoned_granted_project_row_contains_both_id_and_checkmark(self):
        """The same output line(s) must contain both TombedProj id and ✓ marker.

        Checks the two AC3 values appear together — not just anywhere in the output.
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        # Scan individual lines: at least one line must contain both the tombstoned
        # project id and the ✓ character.
        lines_with_tombed = [ln for ln in out.splitlines() if self.TOMBED in ln]
        self.assertTrue(
            len(lines_with_tombed) >= 1,
            f"Expected at least one output line containing '{self.TOMBED}'; "
            f"got output:\n{out}",
        )
        self.assertTrue(
            any("✓" in ln for ln in lines_with_tombed),
            f"None of the '{self.TOMBED}' lines contains '✓'; lines:\n"
            + "\n".join(lines_with_tombed),
        )

    def test_non_granted_active_project_shows_dash(self):
        """ActiveProj (not granted) must render '-' in CROSS-PROJECT under --all.

        Verifies the ✓/- cell logic applies correctly to both cases.
        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0, f"projects --all must exit 0; got rc={rc!r}")
        lines_with_active = [ln for ln in out.splitlines() if self.ACTIVE in ln]
        self.assertTrue(
            len(lines_with_active) >= 1,
            f"Expected at least one output line containing '{self.ACTIVE}'; "
            f"got output:\n{out}",
        )
        self.assertTrue(
            any("-" in ln for ln in lines_with_active),
            f"None of the '{self.ACTIVE}' lines contains '-'; lines:\n"
            + "\n".join(lines_with_active),
        )


# ---------------------------------------------------------------------------
# AC4 — empty store: --all on no tracker rows → '(no projects set up)', exit 0
# ---------------------------------------------------------------------------

class EmptyStoreTest(unittest.TestCase):
    """AC4 — `sandesh projects --all` on an empty tracker prints friendly message.

    Uses a completely fresh temp store — no projects enrolled at all.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-projects-listing-empty-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        # Initialise the schema without enrolling any project.
        con = s.connect()
        con.close()

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_empty_store_default_exits_zero(self):
        """sandesh projects on empty store exits 0 (existing behaviour)."""
        rc, _out, _err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects on empty store should exit 0; got rc={rc!r}")

    def test_empty_store_default_prints_no_projects_message(self):
        """sandesh projects on empty store prints '(no projects set up)'.

        This pin must pass before AND after GREEN (locked regression).
        """
        _rc, out, _err = self._run_cli(["projects"])
        self.assertIn(
            "(no projects set up)", out,
            f"Expected '(no projects set up)' for empty store; got:\n{out}",
        )

    def test_empty_store_all_flag_exits_zero(self):
        """sandesh projects --all on empty store exits 0.

        AC4 spec: exits 0.
        RED: SystemExit(2) — --all not yet registered.
        """
        rc, _out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0,
                         f"projects --all on empty store must exit 0; got rc={rc!r}")

    def test_empty_store_all_flag_prints_no_projects_message(self):
        """sandesh projects --all on empty store prints '(no projects set up)'.

        AC4 spec: "prints '(no projects set up)' and exits 0".
        RED: SystemExit(2) — --all not yet registered.
        """
        rc, out, _err = self._run_cli(["projects", "--all"])
        self.assertEqual(rc, 0,
                         f"projects --all on empty store must exit 0; got rc={rc!r}")
        self.assertIn(
            "(no projects set up)", out,
            f"Expected '(no projects set up)' for empty store with --all; got:\n{out}",
        )


# ---------------------------------------------------------------------------
# Help text pin — flag help string locked to spec
# ---------------------------------------------------------------------------

class HelpTextTest(unittest.TestCase):
    """The --all flag help text must match the spec verbatim.

    AC spec: Flag help text: 'include tombstoned projects (permanent markers)'.

    RED: argparse exits 0 with --help but --all not in the output (flag absent).
    """

    def _run_cli(self, argv):
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_projects_help_contains_all_flag_description(self):
        """sandesh projects --help must mention 'include tombstoned projects (permanent markers)'.

        RED: the string is absent until the flag is added.
        """
        _rc, out, err = self._run_cli(["projects", "--help"])
        combined = out + err
        self.assertIn(
            "include tombstoned projects (permanent markers)", combined,
            f"--help must contain the spec-exact flag description; got:\n{combined}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
