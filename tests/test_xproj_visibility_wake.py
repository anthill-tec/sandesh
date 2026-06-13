"""test_xproj_visibility_wake.py — RED tests for CR-SAN-023 Cycle 4.

Covers §S4 + §S5 + DRIFT-5 (AC7 / AC8 / AC11):

  AC7 — all-tracks stays in-project under grant conditions (§S4 pin)
    send(from='Mainline - P1', to=['all-tracks']) with BOTH P1 and P2 granted must
    create recipient rows ONLY for P1's active addresses minus the sender; zero P2 rows.
    (Behaviour was locked in 022; this cycle pins it under the new grant-active world.)

  AC8 — cross-project wake semantics (§S5 pin)
    A cross-project `to` recipient appears in unread_to() (the notify/wake filter);
    a cross-project `cc` recipient does NOT appear in unread_to() (silent delivery).
    Both appear in their respective inbox()s.
    (Also largely locked post-C3; pinned here under grant conditions as a spec contract.)

  AC11 — `projects` listing visibility (DRIFT-5 — the genuinely-failing piece)
    cli.main(['projects']) output must have a header containing PROJECT, STATE,
    CROSS-PROJECT; a granted project's row shows '✓'; an ungranted project's row shows '-';
    states render as 'active'; the old bare-names format is gone.
    (cmd_projects currently prints bare names via list_projects() — this MUST FAIL RED.)

Expected RED:
  AC7 + AC8: likely arrive GREEN post-C3 (grant machinery + send relaxation in place).
    These are PINNING tests — they lock the behaviour contract under grant conditions.
    A pass is expected and is noted in each test docstring. If they FAIL, GREEN is broken.
  AC11: MUST FAIL RED — cmd_projects currently prints bare project names (cli.py:63-66),
    not the 3-column table. This is the genuinely-new piece that GREEN must implement.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_xproj_visibility_wake --agent red-cr023-c4
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

    setUp provisions:
      - P1, P2 enrolled via setup()
      - Mainline + Track 1 registered in each project
      - admin 'ops' assigned
      - BOTH P1 and P2 granted cross-project access

    Subclasses call super().setUp().
    """

    P1 = "P1"
    P2 = "P2"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-xproj-visibility-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision both projects.
        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()

        # Register Mainline + Track 1 in each project.
        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)

        # Assign admin and grant BOTH projects cross-project access.
        s.assign_admin(self.con, "ops")
        s.grant_xproj(self.con, self.P1, "ops")
        s.grant_xproj(self.con, self.P2, "ops")

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, project_id):
        return s.store_dir(project_id)

    def _recipient_rows_for_message(self, message_id):
        """Return all (recipient, role) pairs for a given message_id."""
        rows = self.con.execute(
            "SELECT recipient, role FROM message_recipient WHERE message_id=?",
            (message_id,),
        ).fetchall()
        return [(r["recipient"], r["role"]) for r in rows]

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


# ---------------------------------------------------------------------------
# AC7 — all-tracks scope stays in-project under grant conditions (§S4 pin)
# ---------------------------------------------------------------------------

class AllTracksInProjectScopeTest(_TempDataHome):
    """AC7: send(to=['all-tracks']) from a granted project expands ONLY to that
    project's active addresses minus the sender — never other projects, even when
    both projects hold the cross-project grant.

    PINNING TEST: this behaviour was locked in CR-SAN-022 (_expand_recipients uses
    active_addresses(con, sender_project)). C4 pins it under the grant-active world.
    If this test arrives GREEN, that is expected and correct — the pin is the contract.
    """

    def test_all_tracks_recipient_rows_only_for_sender_project(self):
        """all-tracks from ML_P1 creates exactly one recipient row: T1_P1.

        With P1 and P2 both granted and both having active addresses (ML_P1, T1_P1,
        ML_P2, T1_P2), a broadcast from ML_P1 must expand only to T1_P1 (ML_P1 is
        excluded as sender). Zero P2 rows.

        PIN: _expand_recipients is project-scoped; the grant does not widen broadcast.
        """
        mid = s.send(
            self.con,
            self._store(self.P1),
            from_addr=self.ML_P1,
            to=["all-tracks"],
            subject="broadcast from P1",
        )
        self.assertIsNotNone(mid, "send must return a message id")

        rows = self._recipient_rows_for_message(mid)
        recipients = [r for r, _ in rows]

        # Exactly one row: T1_P1
        self.assertEqual(
            len(rows), 1,
            f"all-tracks from ML_P1 must produce exactly 1 recipient row (T1_P1); "
            f"got {len(rows)}: {recipients!r}",
        )
        self.assertIn(
            self.T1_P1, recipients,
            f"T1_P1 must be in the recipient rows; got {recipients!r}",
        )

    def test_all_tracks_zero_p2_recipient_rows(self):
        """all-tracks from ML_P1 must create ZERO rows for any P2 address.

        Even with P2 fully granted, the broadcast scope is project-local.
        PIN: grant widens point-to-point sends, not broadcasts.
        """
        mid = s.send(
            self.con,
            self._store(self.P1),
            from_addr=self.ML_P1,
            to=["all-tracks"],
            subject="broadcast must not reach P2",
        )
        rows = self._recipient_rows_for_message(mid)
        p2_rows = [(r, role) for r, role in rows if r in (self.ML_P2, self.T1_P2)]

        self.assertEqual(
            len(p2_rows), 0,
            f"all-tracks from ML_P1 must produce ZERO P2 recipient rows; "
            f"got {len(p2_rows)}: {p2_rows!r}",
        )

    def test_all_tracks_sender_excluded_from_recipients(self):
        """The sender (ML_P1) must not appear in its own broadcast's recipient rows.

        PIN: _expand_recipients filters sender regardless of grant state.
        """
        mid = s.send(
            self.con,
            self._store(self.P1),
            from_addr=self.ML_P1,
            to=["all-tracks"],
            subject="sender must be excluded",
        )
        rows = self._recipient_rows_for_message(mid)
        recipients = [r for r, _ in rows]

        self.assertNotIn(
            self.ML_P1, recipients,
            f"sender ML_P1 must not appear in recipient rows; got {recipients!r}",
        )


# ---------------------------------------------------------------------------
# AC8 — cross-project wake semantics (§S5 pin)
# ---------------------------------------------------------------------------

class CrossProjectWakeSemanticsTest(_TempDataHome):
    """AC8: a cross-project `to` recipient's message id appears in unread_to()
    (the notify/wake filter); a cross-project `cc` recipient's does NOT (silent
    delivery). Both appear in inbox().

    PINNING TEST: wake semantics (to-wakes/cc-silent) are locked from the
    original design (CLAUDE.md §1). C4 pins them across project boundaries under
    grant conditions. If these tests arrive GREEN post-C3, that is expected.
    """

    def setUp(self):
        super().setUp()
        # P2 sends to ML_P1 (To) and cc T1_P1 (Cc) — both cross-project.
        self.mid = s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            cc=[self.T1_P1],
            subject="cross-project wake test",
        )

    def test_xproj_to_recipient_appears_in_unread_to(self):
        """The 'to' cross-project recipient (ML_P1) must appear in unread_to().

        unread_to() is the notify/wake filter. A cross-project 'to' must wake the
        recipient just as an in-project 'to' does.
        PIN: unread_to uses role='to' AND read_at IS NULL — project boundary is irrelevant.
        """
        unread = s.unread_to(self.con, self.ML_P1)

        self.assertIn(
            self.mid, unread,
            f"message id {self.mid} must appear in unread_to(ML_P1); "
            f"got {unread!r}",
        )

    def test_xproj_cc_recipient_not_in_unread_to(self):
        """The 'cc' cross-project recipient (T1_P1) must NOT appear in unread_to().

        Cc is silent — delivered and readable via inbox/fetch, but never wakes.
        This holds across project boundaries as well as within a project.
        PIN: cc-silent is a design invariant (CLAUDE.md §1).
        """
        unread = s.unread_to(self.con, self.T1_P1)

        self.assertNotIn(
            self.mid, unread,
            f"message id {self.mid} must NOT appear in unread_to(T1_P1) — "
            f"T1_P1 is a cc recipient; cc is silent. Got {unread!r}",
        )

    def test_xproj_to_recipient_appears_in_inbox(self):
        """The 'to' cross-project recipient (ML_P1) must appear in inbox().

        Both to and cc recipients are delivered; inbox must show the message.
        """
        inbox_rows = s.inbox(self.con, self.ML_P1)
        inbox_ids = [r["id"] for r in inbox_rows]

        self.assertIn(
            self.mid, inbox_ids,
            f"message id {self.mid} must appear in inbox(ML_P1); "
            f"got ids {inbox_ids!r}",
        )
        # Verify the role is 'to'
        matching = [r for r in inbox_rows if r["id"] == self.mid]
        self.assertEqual(
            matching[0]["role"], "to",
            f"ML_P1's role for message {self.mid} must be 'to'; "
            f"got {matching[0]['role']!r}",
        )

    def test_xproj_cc_recipient_appears_in_inbox(self):
        """The 'cc' cross-project recipient (T1_P1) must appear in inbox().

        Cc is delivered (readable via fetch/inbox), just never triggers wake.
        """
        inbox_rows = s.inbox(self.con, self.T1_P1)
        inbox_ids = [r["id"] for r in inbox_rows]

        self.assertIn(
            self.mid, inbox_ids,
            f"message id {self.mid} must appear in inbox(T1_P1); "
            f"got ids {inbox_ids!r}",
        )
        # Verify the role is 'cc'
        matching = [r for r in inbox_rows if r["id"] == self.mid]
        self.assertEqual(
            matching[0]["role"], "cc",
            f"T1_P1's role for message {self.mid} must be 'cc'; "
            f"got {matching[0]['role']!r}",
        )

    def test_xproj_to_recipient_read_at_independent_of_cc(self):
        """Reading (fetching) by the To recipient must NOT mark the Cc recipient as read.

        Per-recipient read is a design invariant: read_at lives on message_recipient,
        not message. A cross-project cc stays unread for T1_P1 after ML_P1 fetches.
        PIN: verifies the per-recipient isolation holds across project boundaries.
        """
        # ML_P1 fetches (marks read for itself).
        s.fetch(self.con, self._store(self.P1), self.ML_P1, mark=True)

        # T1_P1 (cc, cross-project) must still show the message as unread.
        inbox_rows = s.inbox(self.con, self.T1_P1, unread_only=True)
        inbox_ids = [r["id"] for r in inbox_rows]

        self.assertIn(
            self.mid, inbox_ids,
            f"After ML_P1 fetches, message {self.mid} must still be unread for T1_P1 "
            f"(per-recipient read isolation across project boundary); "
            f"got unread ids for T1_P1: {inbox_ids!r}",
        )


# ---------------------------------------------------------------------------
# AC11 — projects listing visibility (DRIFT-5 — genuinely-failing new piece)
# ---------------------------------------------------------------------------

class ProjectsListingVisibilityTest(_TempDataHome):
    """AC11: cli.main(['projects']) must output a 3-column table with header
    PROJECT  STATE  CROSS-PROJECT; granted projects show '✓'; ungranted show '-';
    state renders as 'active'. The old bare-names format must be gone.

    MUST FAIL RED: cmd_projects currently calls list_projects() and prints bare
    project names (cli.py lines ~63-66). There is no column header, no state
    column, and no CROSS-PROJECT column. GREEN must implement the richer query.
    """

    def test_projects_output_has_header_with_project_column(self):
        """cli.main(['projects']) output must contain 'PROJECT' in a header line.

        RED: current output is bare project names ('P1\\nP2') — no header at all.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            "PROJECT", out,
            f"'projects' output must contain column header 'PROJECT'; "
            f"got output:\n{out!r}",
        )

    def test_projects_output_has_state_column_header(self):
        """cli.main(['projects']) output must contain 'STATE' in a header line.

        RED: current output has no STATE column.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            "STATE", out,
            f"'projects' output must contain column header 'STATE'; "
            f"got output:\n{out!r}",
        )

    def test_projects_output_has_cross_project_column_header(self):
        """cli.main(['projects']) output must contain 'CROSS-PROJECT' in a header.

        RED: current output has no CROSS-PROJECT column.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            "CROSS-PROJECT", out,
            f"'projects' output must contain column header 'CROSS-PROJECT'; "
            f"got output:\n{out!r}",
        )

    def test_granted_project_shows_checkmark(self):
        """A granted project's row must contain '✓' in the CROSS-PROJECT column.

        Both P1 and P2 are granted in the fixture; their rows must show '✓'.
        RED: current output does not contain '✓'.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            "✓", out,
            f"Granted project row must show '✓' in CROSS-PROJECT column; "
            f"got output:\n{out!r}",
        )

    def test_ungranted_project_shows_dash(self):
        """An ungranted project's row must show '-' in the CROSS-PROJECT column.

        Revoke P2's grant so one project is granted (P1) and one is not (P2),
        then verify the ungranted row shows '-'.
        RED: current output does not have the column at all.
        """
        # Revoke P2's grant so it becomes ungranted.
        s.revoke_xproj(self.con, self.P2, "ops")

        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        # The output must contain '-' for the ungranted project's CROSS-PROJECT cell.
        # We check that '-' appears somewhere in a data row (not just in a separator).
        # Since P1 shows '✓' and P2 shows '-', both must be present.
        self.assertIn(
            "✓", out,
            f"Granted project P1 must show '✓'; got output:\n{out!r}",
        )
        self.assertIn(
            "-", out,
            f"Ungranted project P2 must show '-' in CROSS-PROJECT column; "
            f"got output:\n{out!r}",
        )

    def test_project_state_renders_as_active(self):
        """Active project rows must show 'active' in the STATE column.

        Both P1 and P2 are active; their rows must contain 'active'.
        RED: current bare-names output does not contain 'active'.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            "active", out,
            f"Active project rows must contain 'active' in STATE column; "
            f"got output:\n{out!r}",
        )

    def test_both_project_ids_appear_in_output(self):
        """Both P1 and P2 must appear in the projects listing output.

        This is the minimum correctness check: the command must still list all projects.
        (This may pass even in RED because bare names are printed — preserved as a
        non-regression anchor: whatever GREEN implements must not drop any project.)
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        self.assertIn(
            self.P1, out,
            f"P1 must appear in projects output; got:\n{out!r}",
        )
        self.assertIn(
            self.P2, out,
            f"P2 must appear in projects output; got:\n{out!r}",
        )

    def test_old_bare_names_format_is_replaced(self):
        """The output must NOT be just bare newline-separated project ids.

        The old format was: '\\n'.join(project_ids) — no header, no state, no columns.
        AC11 mandates the richer 3-column format. This test fails if the output is
        ONLY bare ids with nothing else.
        RED: cmd_projects currently produces exactly this bare format.
        """
        rc, out, err = self._run_cli(["projects"])
        self.assertEqual(rc, 0, f"projects command must exit 0; got rc={rc!r} err={err!r}")

        # The output must contain more than just the project ids on separate lines.
        # If stripping the project ids and newlines leaves nothing, it's the bare format.
        stripped = out
        for pid in (self.P1, self.P2):
            stripped = stripped.replace(pid, "")
        stripped = stripped.replace("\n", "").replace("(no projects set up)", "").strip()

        self.assertGreater(
            len(stripped), 0,
            f"'projects' output must contain more than bare project ids; "
            f"the 3-column header (PROJECT  STATE  CROSS-PROJECT) must be present. "
            f"Got output:\n{out!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
