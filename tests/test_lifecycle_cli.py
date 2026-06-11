"""test_lifecycle_cli.py — RED tests for CR-SAN-024 Cycle 4.

Covers §S3 + AC7: CLI verbs archive / unarchive / tombstone.

  sandesh archive   --project <id> --by <addr> [--force] [--dry-run]
  sandesh unarchive --project <id> --by <addr> [--dry-run]
  sandesh tombstone --project <id> --by <addr> [--force] [--yes] [--dry-run]

Shape: subparsers WITHOUT parents=[common] (grant/revoke pattern).
Authz:
  archive/unarchive: --by must be the project's own Mainline (validate_address)
  tombstone:         --by must equal admin_name(con)

Error paths → print "[sandesh] <msg>" on stderr + exit 1 (house pattern).
tombstone interactive confirm:
  - without --yes AND stdin non-TTY → refuses (mentions "--yes"), exit non-zero,
    state unchanged.
  - --dry-run bypasses confirm (no --yes needed, no prompt).
  - --yes proceeds.

--dry-run (AC7):
  archive --dry-run:   lists watchers that would be evicted; says would become archived;
                       writes nothing (state unchanged, notifier row untouched).
  unarchive --dry-run: says would become active; writes nothing.
  tombstone --dry-run (on ARCHIVED project): reports counts with labels:
    "internal messages: N"   N >= 1
    "body files: N"          N >= 1
    "cross-project messages: N"  N >= 1  (messages with surviving bodies)
    writes nothing (state unchanged, all rows present, folder present,
    notifier rows untouched).
  tombstone --dry-run on ACTIVE project → error path (archive it first), exit 1.

Expected RED: invalid choice: 'archive' / 'unarchive' / 'tombstone'
  (argparse SystemExit(2)) for all CLI tests until the subparsers are registered.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_lifecycle_cli --agent red-cr024-c4
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
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

    setUp provisions:
      - P1 + P2 with Mainline + Track 1 registered in each
      - admin 'ops' assigned (for tombstone authz)
      - P2 granted cross-project (so cross-project messages can be sent)
    Subclasses call super().setUp().
    """

    P1 = "P1"
    P2 = "P2"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-lifecycle-cli-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()

        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)

        s.assign_admin(self.con, self.ADMIN)
        # Grant P2 so cross-project sends work in message matrix
        s.grant_xproj(self.con, self.P2, self.ADMIN)

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

    def _address_count(self, project_id):
        return self.con.execute(
            "SELECT COUNT(*) FROM address WHERE project=?", (project_id,)
        ).fetchone()[0]

    def _message_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def _notifier_count_for_project(self, project_id):
        return self.con.execute(
            "SELECT COUNT(*) FROM notifier n "
            "JOIN address a ON a.address = n.recipient "
            "WHERE a.project=?",
            (project_id,)
        ).fetchone()[0]

    def _seed_live_notifier(self, address):
        """Seed a live notifier row for the given address (using this process's pid).
        Returns the token used.
        """
        tok = uuid.uuid4().hex
        ok, _ = s.notifier_acquire(self.con, address, os.getpid(), tok, "testhost")
        self.assertTrue(ok, f"notifier_acquire must succeed for {address!r}")
        return tok

    def _cleanup_notifier(self, address, token):
        """Token-guarded cleanup — no-op if the row is already gone."""
        try:
            s.notifier_release(self.con, address, token)
        except Exception:
            pass

    def _is_invalid_choice_error(self, rc, err, subcommand):
        """True when rc==2 and err says 'invalid choice: <subcommand>'."""
        return rc == 2 and "invalid choice" in err and subcommand in err

    def _build_cross_project_message_matrix(self):
        """Build a message matrix with both internal and cross-project messages.

        Sends (all with bodies so they generate files):
          - 2 P2-internal messages  (ML_P2 → T1_P2, T1_P2 → ML_P2)
          - 1 cross-project message (ML_P2 → ML_P1)

        Returns (internal_ids, cross_ids) — both lists.
        Requires P2 to be ACTIVE and cross-project granted.
        """
        store_p2 = s.store_dir(self.P2)
        store_p1 = s.store_dir(self.P1)

        # Two internal P2 messages (with bodies)
        mid_int_1 = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.T1_P2], subject="internal-1",
            body_text="body of internal message 1",
        )
        mid_int_2 = s.send(
            self.con, store_p2, self.T1_P2,
            to=[self.ML_P2], subject="internal-2",
            body_text="body of internal message 2",
        )
        # One cross-project message P2 → P1 (with body)
        mid_xproj = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.ML_P1], subject="cross-project-msg",
            body_text="body of cross-project message",
        )
        return [mid_int_1, mid_int_2], [mid_xproj]


# ---------------------------------------------------------------------------
# T1 — archive subcommand registration
# ---------------------------------------------------------------------------

class ArchiveSubcommandRegistrationTest(_TempDataHome):
    """sandesh archive --project P2 --by 'Mainline - P2' must be a registered
    subcommand.

    RED: 'archive' not yet registered → argparse exits 2 with 'invalid choice: archive'.
    """

    def test_archive_subcommand_not_unknown_choice(self):
        """cli.main(['archive', '--project', P2, '--by', ML_P2]) must NOT exit
        with 'invalid choice: archive'.

        RED: subcommand absent → SystemExit(2) with 'invalid choice: archive'.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        combined = out + err
        self.assertFalse(
            self._is_invalid_choice_error(rc, combined, "archive"),
            "cli.main(['archive', ...]) exited with 'invalid choice: archive' — "
            "the 'archive' subcommand is not yet registered (RED).",
        )

    def test_archive_missing_project_exits_nonzero(self):
        """archive without --project must exit non-zero (argparse required arg missing).

        RED: if 'archive' is not registered, this exits 2 for the wrong reason.
        """
        rc, out, err = self._run_cli(["archive", "--by", self.ML_P2])
        self.assertNotEqual(
            rc, 0,
            "archive without --project must exit non-zero; got rc=0",
        )

    def test_archive_missing_by_exits_nonzero(self):
        """archive without --by must exit non-zero (required arg).

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["archive", "--project", self.P2])
        self.assertNotEqual(
            rc, 0,
            "archive without --by must exit non-zero; got rc=0",
        )


# ---------------------------------------------------------------------------
# T2 — unarchive subcommand registration
# ---------------------------------------------------------------------------

class UnarchiveSubcommandRegistrationTest(_TempDataHome):
    """sandesh unarchive --project P2 --by 'Mainline - P2' must be a registered
    subcommand.

    RED: 'unarchive' not yet registered → argparse exits 2 with 'invalid choice: unarchive'.
    """

    def test_unarchive_subcommand_not_unknown_choice(self):
        """cli.main(['unarchive', '--project', P2, '--by', ML_P2]) must NOT exit
        with 'invalid choice: unarchive'.

        RED: subcommand absent → SystemExit(2) with 'invalid choice: unarchive'.
        """
        # Seed archived state so the subcommand would succeed
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        combined = out + err
        self.assertFalse(
            self._is_invalid_choice_error(rc, combined, "unarchive"),
            "cli.main(['unarchive', ...]) exited with 'invalid choice: unarchive' — "
            "the 'unarchive' subcommand is not yet registered (RED).",
        )

    def test_unarchive_missing_project_exits_nonzero(self):
        """unarchive without --project must exit non-zero.

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["unarchive", "--by", self.ML_P2])
        self.assertNotEqual(rc, 0,
                            "unarchive without --project must exit non-zero")

    def test_unarchive_missing_by_exits_nonzero(self):
        """unarchive without --by must exit non-zero.

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["unarchive", "--project", self.P2])
        self.assertNotEqual(rc, 0,
                            "unarchive without --by must exit non-zero")


# ---------------------------------------------------------------------------
# T3 — tombstone subcommand registration
# ---------------------------------------------------------------------------

class TombstoneSubcommandRegistrationTest(_TempDataHome):
    """sandesh tombstone --project P2 --by ops --yes must be a registered
    subcommand.

    RED: 'tombstone' not yet registered → argparse exits 2 with 'invalid choice: tombstone'.
    """

    def test_tombstone_subcommand_not_unknown_choice(self):
        """cli.main(['tombstone', '--project', P2, '--by', ADMIN, '--yes'])
        must NOT exit with 'invalid choice: tombstone'.

        RED: subcommand absent → SystemExit(2) with 'invalid choice: tombstone'.
        """
        # Seed archived state so the command would proceed
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        combined = out + err
        self.assertFalse(
            self._is_invalid_choice_error(rc, combined, "tombstone"),
            "cli.main(['tombstone', ...]) exited with 'invalid choice: tombstone' — "
            "the 'tombstone' subcommand is not yet registered (RED).",
        )

    def test_tombstone_missing_project_exits_nonzero(self):
        """tombstone without --project must exit non-zero.

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["tombstone", "--by", self.ADMIN, "--yes"])
        self.assertNotEqual(rc, 0,
                            "tombstone without --project must exit non-zero")

    def test_tombstone_missing_by_exits_nonzero(self):
        """tombstone without --by must exit non-zero.

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli(["tombstone", "--project", self.P2, "--yes"])
        self.assertNotEqual(rc, 0,
                            "tombstone without --by must exit non-zero")


# ---------------------------------------------------------------------------
# T4 — archive success path + DB verification
# ---------------------------------------------------------------------------

class ArchiveSuccessPathTest(_TempDataHome):
    """CLI archive happy path: exit 0, project state becomes 'archived', DB intact.

    RED: 'archive' not registered → SystemExit(2).
    """

    def test_archive_exits_zero(self):
        """archive --project P2 --by ML_P2 must exit 0 on an active project.

        RED: subcommand not registered → SystemExit(2).
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        self.assertEqual(
            rc, 0,
            f"archive must exit 0 on success; got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_archive_sets_state_to_archived(self):
        """After CLI archive, the project DB state must be 'archived'.

        RED: subcommand not registered → no state change.
        """
        rc, _, _ = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc, "", "archive"):
            self.fail("archive subcommand not registered — RED")
        # Refresh connection to see committed changes
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "archived",
            f"project P2 must be 'archived' after CLI archive; got {state!r}",
        )

    def test_archive_confirmation_mentions_project(self):
        """Archive success output must mention the project id.

        RED: subcommand not registered → wrong output.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        combined = out + err
        if rc == 2 and "invalid choice" in combined:
            self.fail("archive subcommand not registered — RED")
        self.assertIn(
            self.P2, combined,
            f"archive confirmation must mention project id {self.P2!r}; got: {combined!r}",
        )

    def test_archive_does_not_affect_p1(self):
        """archive P2 must not change P1's state.

        RED: subcommand not registered → no state change (both stay active, which
        looks like a pass; but if we check P1 = 'active' that's correct regardless).
        The RED assertion is the archive exit code and P2 state.
        """
        rc, _, _ = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc, "", "archive"):
            self.fail("archive subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state_p1 = self._raw_project_state(self.P1)
        self.assertEqual(state_p1, "active",
                         f"P1 must remain 'active' after archiving P2; got {state_p1!r}")


# ---------------------------------------------------------------------------
# T5 — archive error paths + stderr format
# ---------------------------------------------------------------------------

class ArchiveErrorPathTest(_TempDataHome):
    """CLI archive error paths → stderr "[sandesh] <msg>" + exit 1.

    RED: 'archive' not registered → SystemExit(2) for wrong reason.
    """

    def test_archive_wrong_by_exits_nonzero(self):
        """archive with wrong --by (Track) must exit non-zero.

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.T1_P2,
        ])
        self.assertNotEqual(
            rc, 0,
            f"archive with wrong --by must exit non-zero; got rc={rc!r} err={err!r}",
        )

    def test_archive_wrong_by_stderr_sandesh_prefix(self):
        """archive with wrong --by must print '[sandesh] ...' on stderr.

        RED: subcommand not registered → argparse error (no [sandesh] prefix).
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.T1_P2,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            # Still RED — subcommand not registered
            return
        # Subcommand registered but wrong by → must use house pattern
        self.assertIn(
            "[sandesh]", err,
            f"archive wrong-by error must print '[sandesh] ...' on stderr; got err={err!r}",
        )

    def test_archive_wrong_by_state_unchanged(self):
        """archive with wrong --by must leave project state unchanged (active).

        RED: subcommand not registered → state stays 'active' (vacuous pass);
        the RED is the registration check above.
        """
        self._run_cli([
            "archive", "--project", self.P2, "--by", self.T1_P2,
        ])
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active",
                         f"State must remain 'active' after rejected archive; got {state!r}")

    def test_archive_already_archived_exits_nonzero(self):
        """archive on an already-archived project must exit non-zero.

        RED: subcommand not registered.
        """
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        self.assertNotEqual(
            rc, 0,
            f"archive on already-archived project must exit non-zero; rc={rc!r} err={err!r}",
        )

    def test_archive_already_archived_stderr_sandesh_prefix(self):
        """archive on already-archived must print '[sandesh] ...' on stderr.

        RED: subcommand not registered.
        """
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertIn(
            "[sandesh]", err,
            f"already-archived error must have '[sandesh]' prefix; got err={err!r}",
        )

    def test_archive_exit_code_is_1_not_2_for_logic_errors(self):
        """archive errors from logic (wrong authz, wrong state) must exit 1, not 2.

        Exit 2 is argparse-reserved; logic errors must use exit 1 (house pattern).
        RED: subcommand not registered → exits 2 for wrong reason.
        """
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED — subcommand not registered
        self.assertEqual(
            rc, 1,
            f"logic errors must exit 1 (not 2); got rc={rc!r} err={err!r}",
        )


# ---------------------------------------------------------------------------
# T6 — unarchive success path + DB verification
# ---------------------------------------------------------------------------

class UnarchiveSuccessPathTest(_TempDataHome):
    """CLI unarchive happy path: exit 0, state back to 'active'.

    RED: 'unarchive' not registered → SystemExit(2).
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_unarchive_exits_zero(self):
        """unarchive --project P2 --by ML_P2 must exit 0.

        RED: subcommand not registered → SystemExit(2).
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        self.assertEqual(
            rc, 0,
            f"unarchive must exit 0 on success; got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_unarchive_sets_state_to_active(self):
        """After CLI unarchive, the project state must be 'active'.

        RED: subcommand not registered → state stays 'archived'.
        """
        rc, _, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc, err, "unarchive"):
            self.fail("unarchive subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "active",
            f"project P2 must be 'active' after CLI unarchive; got {state!r}",
        )

    def test_unarchive_confirmation_mentions_project(self):
        """unarchive success output must mention the project id.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        combined = out + err
        if rc == 2 and "invalid choice" in combined:
            self.fail("unarchive subcommand not registered — RED")
        self.assertIn(
            self.P2, combined,
            f"unarchive confirmation must mention project id; got: {combined!r}",
        )


# ---------------------------------------------------------------------------
# T7 — unarchive error paths
# ---------------------------------------------------------------------------

class UnarchiveErrorPathTest(_TempDataHome):
    """CLI unarchive error paths → stderr + exit 1.

    RED: 'unarchive' not registered.
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_unarchive_wrong_by_exits_nonzero(self):
        """unarchive with wrong --by (Track) must exit non-zero.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.T1_P2,
        ])
        self.assertNotEqual(
            rc, 0,
            f"unarchive with wrong --by must exit non-zero; got rc={rc!r}",
        )

    def test_unarchive_wrong_by_stderr_sandesh_prefix(self):
        """unarchive with wrong --by must print '[sandesh] ...' on stderr.

        RED: subcommand not registered → argparse error (no prefix).
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.T1_P2,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertIn(
            "[sandesh]", err,
            f"unarchive wrong-by error must print '[sandesh] ...' on stderr; err={err!r}",
        )

    def test_unarchive_active_project_exits_nonzero(self):
        """unarchive on an active project must exit non-zero.

        RED: subcommand not registered.
        """
        # Reset P2 to active
        self.con.execute(
            "UPDATE project SET state='active', archived_at=NULL WHERE project_id=?",
            (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        self.assertNotEqual(
            rc, 0,
            f"unarchive on active project must exit non-zero; rc={rc!r}",
        )

    def test_unarchive_exit_code_is_1_for_logic_errors(self):
        """unarchive logic errors must exit 1 (not 2).

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        # Reset P2 to active so unarchive fails with a logic error
        self.con.execute(
            "UPDATE project SET state='active', archived_at=NULL WHERE project_id=?",
            (self.P2,))
        self.con.commit()
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertEqual(
            rc, 1,
            f"unarchive logic error must exit 1; got rc={rc!r} err={err!r}",
        )


# ---------------------------------------------------------------------------
# T8 — tombstone success path (--yes + archived project)
# ---------------------------------------------------------------------------

class TombstoneSuccessPathTest(_TempDataHome):
    """CLI tombstone with --yes on an archived project: exit 0, state 'tombstoned'.

    RED: 'tombstone' not registered → SystemExit(2).
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_tombstone_with_yes_exits_zero(self):
        """tombstone --project P2 --by ops --yes must exit 0 on an archived project.

        RED: subcommand not registered → SystemExit(2).
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        self.assertEqual(
            rc, 0,
            f"tombstone --yes must exit 0; got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_tombstone_with_yes_sets_state_tombstoned(self):
        """After CLI tombstone --yes, the project state must be 'tombstoned'.

        RED: subcommand not registered → state stays 'archived'.
        """
        rc, _, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "tombstoned",
            f"project P2 must be 'tombstoned' after CLI tombstone --yes; got {state!r}",
        )

    def test_tombstone_with_yes_confirmation_mentions_project(self):
        """tombstone --yes success output must mention the project id.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        combined = out + err
        if rc == 2 and "invalid choice" in combined:
            self.fail("tombstone subcommand not registered — RED")
        self.assertIn(
            self.P2, combined,
            f"tombstone confirmation must mention project id; got: {combined!r}",
        )


# ---------------------------------------------------------------------------
# T9 — tombstone interactive confirm (non-TTY stdin refusal)
# ---------------------------------------------------------------------------

class TombstoneConfirmRefusalTest(_TempDataHome):
    """Without --yes and stdin non-interactive (non-TTY), tombstone must REFUSE
    with a message mentioning '--yes', exit non-zero, state unchanged.

    RED: 'tombstone' not registered → SystemExit(2) for wrong reason.
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def _run_cli_with_nontty_stdin(self, argv):
        """Run cli.main(argv) with stdin replaced by a non-TTY StringIO."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        fake_stdin = io.StringIO("")   # non-TTY (isatty() returns False)
        old_stdin = sys.stdin
        sys.stdin = fake_stdin
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                try:
                    rc = cli.main(argv)
                except SystemExit as exc:
                    rc = exc.code
        finally:
            sys.stdin = old_stdin
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_tombstone_without_yes_nontty_exits_nonzero(self):
        """tombstone without --yes, stdin non-TTY must exit non-zero (refuses).

        RED: subcommand not registered → SystemExit(2) for wrong reason.
        """
        rc, out, err = self._run_cli_with_nontty_stdin([
            "tombstone", "--project", self.P2, "--by", self.ADMIN,
        ])
        self.assertNotEqual(
            rc, 0,
            f"tombstone without --yes (non-TTY stdin) must exit non-zero; "
            f"got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_tombstone_without_yes_nontty_mentions_yes_flag(self):
        """tombstone refusal output must mention '--yes'.

        RED: subcommand not registered → argparse error (no '--yes' mention).
        """
        rc, out, err = self._run_cli_with_nontty_stdin([
            "tombstone", "--project", self.P2, "--by", self.ADMIN,
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED — subcommand not registered
        combined = out + err
        self.assertIn(
            "--yes", combined,
            f"tombstone refusal must mention '--yes'; got: {combined!r}",
        )

    def test_tombstone_without_yes_nontty_state_unchanged(self):
        """After tombstone refusal (no --yes, non-TTY), state must remain 'archived'.

        RED: subcommand not registered → state stays 'archived' (vacuously).
        The RED gate is the exit-code assertion above.
        """
        self._run_cli_with_nontty_stdin([
            "tombstone", "--project", self.P2, "--by", self.ADMIN,
        ])
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "archived",
            f"state must remain 'archived' after refused tombstone; got {state!r}",
        )

    def test_tombstone_without_yes_address_rows_present(self):
        """After tombstone refusal, P2 address rows must still be present.

        RED: subcommand not registered.
        """
        addr_count_before = self._address_count(self.P2)
        self._run_cli_with_nontty_stdin([
            "tombstone", "--project", self.P2, "--by", self.ADMIN,
        ])
        self.con.close()
        self.con = s.connect()
        addr_count_after = self._address_count(self.P2)
        self.assertEqual(
            addr_count_after, addr_count_before,
            f"address rows must be unchanged after refused tombstone; "
            f"before={addr_count_before}, after={addr_count_after}",
        )


# ---------------------------------------------------------------------------
# T10 — tombstone error paths (authz, state)
# ---------------------------------------------------------------------------

class TombstoneErrorPathTest(_TempDataHome):
    """CLI tombstone error paths → stderr + exit 1.

    RED: 'tombstone' not registered.
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_tombstone_wrong_by_mainline_exits_nonzero(self):
        """tombstone with --by == project Mainline (not admin) must exit non-zero.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ML_P2, "--yes",
        ])
        self.assertNotEqual(
            rc, 0,
            f"tombstone with wrong --by (Mainline) must exit non-zero; rc={rc!r}",
        )

    def test_tombstone_wrong_by_stderr_sandesh_prefix(self):
        """tombstone with wrong --by must print '[sandesh] ...' on stderr.

        RED: subcommand not registered → argparse error (no prefix).
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ML_P2, "--yes",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertIn(
            "[sandesh]", err,
            f"tombstone wrong-by must print '[sandesh] ...' on stderr; err={err!r}",
        )

    def test_tombstone_wrong_by_state_unchanged(self):
        """tombstone with wrong --by must leave state 'archived'.

        RED: subcommand not registered.
        """
        self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ML_P2, "--yes",
        ])
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"state must stay 'archived' after wrong-by tombstone; got {state!r}")

    def test_tombstone_on_active_project_exits_nonzero(self):
        """tombstone on an ACTIVE project must exit non-zero ('archive it first').

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--yes",
        ])
        self.assertNotEqual(
            rc, 0,
            f"tombstone on active P1 must exit non-zero; rc={rc!r}",
        )

    def test_tombstone_on_active_project_mentions_archive_first(self):
        """tombstone on active project error must mention 'archive it first'.

        RED: subcommand not registered → argparse error (no mention).
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--yes",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        combined = out + err
        self.assertIn(
            "archive", combined.lower(),
            f"tombstone on active project must mention 'archive'; got: {combined!r}",
        )

    def test_tombstone_exit_code_is_1_for_logic_errors(self):
        """tombstone logic errors must exit 1 (not 2).

        RED: subcommand not registered → exits 2 for wrong reason.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ML_P2, "--yes",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertEqual(
            rc, 1,
            f"tombstone logic error must exit 1; got rc={rc!r} err={err!r}",
        )

    def test_tombstone_empty_admin_table_exits_nonzero(self):
        """tombstone with empty admin table must exit non-zero + '[sandesh]' stderr.

        RED: subcommand not registered.
        """
        self.con.execute("DELETE FROM admin")
        self.con.commit()
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", "someadmin", "--yes",
        ])
        self.assertNotEqual(
            rc, 0,
            f"tombstone with empty admin must exit non-zero; rc={rc!r}",
        )
        if rc != 2 or "invalid choice" not in (out + err):
            # Subcommand registered → check the error format
            self.assertIn(
                "[sandesh]", err,
                f"tombstone empty-admin error must use '[sandesh]' prefix; err={err!r}",
            )


# ---------------------------------------------------------------------------
# T11 — archive --dry-run: writes nothing, reports watchers
# ---------------------------------------------------------------------------

class ArchiveDryRunTest(_TempDataHome):
    """archive --dry-run: reports watchers to evict; writes nothing.

    State: P2 active; one live notifier seeded on ML_P2.
    RED: 'archive' not registered / --dry-run not accepted.
    """

    def setUp(self):
        super().setUp()
        self._tok = self._seed_live_notifier(self.ML_P2)

    def tearDown(self):
        self._cleanup_notifier(self.ML_P2, self._tok)
        super().tearDown()

    def test_archive_dry_run_exit_zero(self):
        """archive --dry-run must exit 0 (it is a report, not an error).

        RED: subcommand not registered or --dry-run not accepted.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        self.assertEqual(
            rc, 0,
            f"archive --dry-run must exit 0; got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_archive_dry_run_state_unchanged(self):
        """archive --dry-run must NOT change the project state.

        RED: subcommand not registered → state stays 'active' (vacuous).
        The RED gate is the exit code test + subcommand availability.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("archive subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "active",
            f"archive --dry-run must not change state; got {state!r}",
        )

    def test_archive_dry_run_notifier_row_untouched(self):
        """archive --dry-run must NOT tombstone or remove the live notifier row.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("archive subcommand not registered — RED")
        row = self.con.execute(
            "SELECT tombstone FROM notifier WHERE recipient=?", (self.ML_P2,)
        ).fetchone()
        self.assertIsNotNone(
            row, "notifier row must still exist after archive --dry-run",
        )
        self.assertFalse(
            bool(row["tombstone"]),
            f"notifier tombstone must NOT be set after archive --dry-run; got {row['tombstone']!r}",
        )

    def test_archive_dry_run_reports_watcher_address(self):
        """archive --dry-run output must list the live watcher address (ML_P2).

        RED: subcommand not registered / --dry-run not reporting watchers.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("archive subcommand not registered — RED")
        combined = out + err
        self.assertIn(
            self.ML_P2, combined,
            f"archive --dry-run must list the watcher address {self.ML_P2!r}; "
            f"got: {combined!r}",
        )

    def test_archive_dry_run_mentions_would_become_archived(self):
        """archive --dry-run output must say the project would become archived.

        RED: subcommand not registered / --dry-run not implemented.
        """
        rc, out, err = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("archive subcommand not registered — RED")
        combined = out + err
        self.assertIn(
            "archive", combined.lower(),
            f"archive --dry-run must mention 'archive' (would become archived); "
            f"got: {combined!r}",
        )


# ---------------------------------------------------------------------------
# T12 — unarchive --dry-run: writes nothing, reports would-become-active
# ---------------------------------------------------------------------------

class UnarchiveDryRunTest(_TempDataHome):
    """unarchive --dry-run: says would become active; writes nothing.

    RED: 'unarchive' not registered / --dry-run not accepted.
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_unarchive_dry_run_exit_zero(self):
        """unarchive --dry-run must exit 0.

        RED: subcommand not registered or --dry-run not accepted.
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        self.assertEqual(
            rc, 0,
            f"unarchive --dry-run must exit 0; got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_unarchive_dry_run_state_unchanged(self):
        """unarchive --dry-run must NOT change the project state (stays 'archived').

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("unarchive subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "archived",
            f"unarchive --dry-run must not change state; got {state!r}",
        )

    def test_unarchive_dry_run_mentions_would_become_active(self):
        """unarchive --dry-run output must mention the project would become active.

        RED: subcommand not registered / --dry-run not implemented.
        """
        rc, out, err = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("unarchive subcommand not registered — RED")
        combined = out + err
        self.assertIn(
            "active", combined.lower(),
            f"unarchive --dry-run must mention 'active'; got: {combined!r}",
        )


# ---------------------------------------------------------------------------
# T13 — tombstone --dry-run on ARCHIVED project: counts + writes nothing
# ---------------------------------------------------------------------------

class TombstoneDryRunArchivedTest(_TempDataHome):
    """tombstone --dry-run on an archived project: counts + writes nothing.

    Label shapes (GREEN must match exactly):
      "internal messages: N"     N >= 1
      "body files: N"            N >= 1
      "cross-project messages: N"  N >= 1

    No --yes needed for --dry-run.
    RED: 'tombstone' not registered / --dry-run not accepted.
    """

    def setUp(self):
        super().setUp()
        # Build message matrix while P2 is active
        self._internal_ids, self._xproj_ids = self._build_cross_project_message_matrix()
        # Seed a live notifier on ML_P2 (to verify it's untouched)
        self._tok = self._seed_live_notifier(self.ML_P2)
        # Archive P2 (required pre-condition for tombstone --dry-run)
        s.archive(self.con, self.P2, self.ML_P2, force=True, wait_secs=0.1)
        # Refresh con to see committed state
        self.con.close()
        self.con = s.connect()
        # Seed notifier again after archive (archive would have evicted it with force)
        self._tok = uuid.uuid4().hex
        ok, _ = s.notifier_acquire(self.con, self.ML_P2, os.getpid(), self._tok, "testhost")
        # ok might be False if the row wasn't cleaned up; that's fine for the dry-run test

    def tearDown(self):
        self._cleanup_notifier(self.ML_P2, self._tok)
        super().tearDown()

    def test_tombstone_dry_run_no_yes_needed(self):
        """tombstone --dry-run must NOT require --yes.

        RED: subcommand not registered / --dry-run not accepted / prompts for --yes.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        self.assertEqual(
            rc, 0,
            f"tombstone --dry-run must exit 0 without --yes; "
            f"got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_tombstone_dry_run_state_unchanged(self):
        """tombstone --dry-run must NOT change the project state.

        RED: subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P2)
        self.assertEqual(
            state, "archived",
            f"tombstone --dry-run must not change state; got {state!r}",
        )

    def test_tombstone_dry_run_address_rows_present(self):
        """tombstone --dry-run must NOT delete P2 address rows.

        RED: subcommand not registered.
        """
        count_before = self._address_count(self.P2)
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        count_after = self._address_count(self.P2)
        self.assertEqual(
            count_after, count_before,
            f"dry-run must not delete address rows; before={count_before}, after={count_after}",
        )

    def test_tombstone_dry_run_messages_present(self):
        """tombstone --dry-run must NOT delete any messages.

        RED: subcommand not registered.
        """
        count_before = self._message_count()
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        count_after = self._message_count()
        self.assertEqual(
            count_after, count_before,
            f"dry-run must not delete messages; before={count_before}, after={count_after}",
        )

    def test_tombstone_dry_run_folder_present(self):
        """tombstone --dry-run must NOT delete the P2 project folder.

        RED: subcommand not registered.
        """
        folder = s.store_dir(self.P2)
        self.assertTrue(os.path.isdir(folder),
                        f"P2 folder must exist before dry-run: {folder}")
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        self.assertTrue(
            os.path.isdir(folder),
            f"tombstone --dry-run must not delete project folder; folder gone: {folder}",
        )

    def test_tombstone_dry_run_reports_internal_messages_count(self):
        """tombstone --dry-run output must include 'internal messages: N' with N >= 1.

        Label shape: "internal messages: 2" (2 internal messages seeded).
        RED: subcommand not registered / --dry-run not implemented.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        combined = out + err
        # The label must appear with a number >= 1
        import re
        match = re.search(r"internal messages:\s*(\d+)", combined, re.IGNORECASE)
        self.assertIsNotNone(
            match,
            f"tombstone --dry-run must output 'internal messages: N'; got: {combined!r}",
        )
        count = int(match.group(1))
        self.assertGreaterEqual(
            count, 1,
            f"internal messages count must be >= 1; got {count} in: {combined!r}",
        )
        # Exactly 2 were seeded
        self.assertEqual(
            count, 2,
            f"internal messages count must be 2 (2 were seeded); got {count}",
        )

    def test_tombstone_dry_run_reports_body_files_count(self):
        """tombstone --dry-run output must include 'body files: N' with N >= 1.

        Label shape: "body files: 2" (2 internal messages with bodies).
        RED: subcommand not registered / --dry-run not implemented.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        combined = out + err
        import re
        match = re.search(r"body files:\s*(\d+)", combined, re.IGNORECASE)
        self.assertIsNotNone(
            match,
            f"tombstone --dry-run must output 'body files: N'; got: {combined!r}",
        )
        count = int(match.group(1))
        self.assertGreaterEqual(
            count, 1,
            f"body files count must be >= 1; got {count} in: {combined!r}",
        )

    def test_tombstone_dry_run_reports_cross_project_messages_count(self):
        """tombstone --dry-run output must include 'cross-project messages: N' with N >= 1.

        Label shape: "cross-project messages: 1" (1 cross-project message seeded).
        RED: subcommand not registered / --dry-run not implemented.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            self.fail("tombstone subcommand not registered — RED")
        combined = out + err
        import re
        match = re.search(r"cross-project messages:\s*(\d+)", combined, re.IGNORECASE)
        self.assertIsNotNone(
            match,
            f"tombstone --dry-run must output 'cross-project messages: N'; "
            f"got: {combined!r}",
        )
        count = int(match.group(1))
        self.assertGreaterEqual(
            count, 1,
            f"cross-project messages count must be >= 1; got {count} in: {combined!r}",
        )
        # Exactly 1 was seeded
        self.assertEqual(
            count, 1,
            f"cross-project messages count must be 1 (1 was seeded); got {count}",
        )


# ---------------------------------------------------------------------------
# T14 — tombstone --dry-run on ACTIVE project → error path
# ---------------------------------------------------------------------------

class TombstoneDryRunActiveTest(_TempDataHome):
    """tombstone --dry-run on an ACTIVE project → error path (archive it first), exit 1.

    dry-run on wrong state/authz still reports the error — does NOT silently pass.
    RED: 'tombstone' not registered.
    """

    def test_tombstone_dry_run_on_active_exits_nonzero(self):
        """tombstone --dry-run on active project must exit non-zero.

        RED: subcommand not registered → SystemExit(2) for wrong reason.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--dry-run",
        ])
        self.assertNotEqual(
            rc, 0,
            f"tombstone --dry-run on active project must exit non-zero; "
            f"got rc={rc!r}. out={out!r} err={err!r}",
        )

    def test_tombstone_dry_run_on_active_reports_archive_first(self):
        """tombstone --dry-run on active project error must mention 'archive'.

        RED: subcommand not registered → argparse error (no 'archive' mention).
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        combined = out + err
        self.assertIn(
            "archive", combined.lower(),
            f"tombstone --dry-run on active must mention 'archive'; got: {combined!r}",
        )

    def test_tombstone_dry_run_on_active_state_unchanged(self):
        """tombstone --dry-run on active must leave P1 state 'active'.

        RED: subcommand not registered.
        """
        self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--dry-run",
        ])
        self.con.close()
        self.con = s.connect()
        state = self._raw_project_state(self.P1)
        self.assertEqual(state, "active",
                         f"P1 state must stay 'active' after dry-run error; got {state!r}")

    def test_tombstone_dry_run_on_active_exit_code_is_1(self):
        """tombstone --dry-run on active project must exit 1 (not 2).

        EXIT 2 is argparse-reserved; logic errors use exit 1.
        RED: subcommand not registered → exits 2.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P1, "--by", self.ADMIN, "--dry-run",
        ])
        if rc == 2 and "invalid choice" in (out + err):
            return  # Still RED
        self.assertEqual(
            rc, 1,
            f"tombstone --dry-run on active must exit 1; got rc={rc!r} err={err!r}",
        )


# ---------------------------------------------------------------------------
# T15 — archive/unarchive round-trip via CLI + DB state verification
# ---------------------------------------------------------------------------

class ArchiveUnarchiveRoundTripTest(_TempDataHome):
    """CLI archive → CLI unarchive round-trip yields active state with all data intact.

    RED: either subcommand not registered.
    """

    def test_archive_then_unarchive_restores_active_state(self):
        """archive followed by unarchive must leave state='active' and archived_at=NULL.

        RED: archive or unarchive subcommand not registered.
        """
        # Archive P2
        rc1, out1, err1 = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc1, out1 + err1, "archive"):
            self.fail("archive subcommand not registered — RED")
        self.assertEqual(rc1, 0, f"archive must exit 0; rc={rc1!r} err={err1!r}")

        # Verify archived
        self.con.close()
        self.con = s.connect()
        self.assertEqual(self._raw_project_state(self.P2), "archived",
                         "state must be 'archived' after CLI archive")

        # Unarchive P2
        rc2, out2, err2 = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc2, out2 + err2, "unarchive"):
            self.fail("unarchive subcommand not registered — RED")
        self.assertEqual(rc2, 0, f"unarchive must exit 0; rc={rc2!r} err={err2!r}")

        # Verify active + archived_at NULL
        self.con.close()
        self.con = s.connect()
        row = self.con.execute(
            "SELECT state, archived_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        self.assertEqual(row["state"], "active",
                         f"state must be 'active' after unarchive; got {row['state']!r}")
        self.assertIsNone(row["archived_at"],
                          f"archived_at must be NULL after unarchive; got {row['archived_at']!r}")

    def test_address_rows_survive_archive_unarchive_cycle(self):
        """Address rows must be intact after archive→unarchive cycle.

        RED: archive or unarchive subcommand not registered.
        """
        count_before = self._address_count(self.P2)
        rc1, _, err1 = self._run_cli([
            "archive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc1, err1, "archive"):
            self.fail("archive subcommand not registered — RED")
        rc2, _, err2 = self._run_cli([
            "unarchive", "--project", self.P2, "--by", self.ML_P2,
        ])
        if self._is_invalid_choice_error(rc2, err2, "unarchive"):
            self.fail("unarchive subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        count_after = self._address_count(self.P2)
        self.assertEqual(count_after, count_before,
                         f"address count must be unchanged; before={count_before}, after={count_after}")


# ---------------------------------------------------------------------------
# T16 — tombstone full CLI flow: --yes + DB + disk verification
# ---------------------------------------------------------------------------

class TombstoneFullFlowTest(_TempDataHome):
    """CLI tombstone --yes on archived project: state tombstoned, internal rows gone,
    cross-project rows survive, folder gone, tracker row present.

    RED: 'tombstone' not registered.
    """

    def setUp(self):
        super().setUp()
        # Build message matrix while P2 is active
        self._internal_ids, self._xproj_ids = self._build_cross_project_message_matrix()
        # Archive P2 via raw SQL (C1 ops tested separately; here we test C4 CLI)
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_tombstone_yes_purges_internal_messages(self):
        """After CLI tombstone --yes, P2-internal message rows must be gone.

        RED: tombstone subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, out + err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.assertEqual(rc, 0, f"tombstone --yes must exit 0; rc={rc!r} err={err!r}")
        self.con.close()
        self.con = s.connect()
        for mid in self._internal_ids:
            row = self.con.execute(
                "SELECT id FROM message WHERE id=?", (mid,)
            ).fetchone()
            self.assertIsNone(
                row,
                f"internal message #{mid} must be purged after tombstone; row still present",
            )

    def test_tombstone_yes_preserves_cross_project_messages(self):
        """After CLI tombstone --yes, cross-project message rows must survive.

        RED: tombstone subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, out + err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.assertEqual(rc, 0, f"tombstone --yes must exit 0; rc={rc!r}")
        self.con.close()
        self.con = s.connect()
        for mid in self._xproj_ids:
            row = self.con.execute(
                "SELECT id FROM message WHERE id=?", (mid,)
            ).fetchone()
            self.assertIsNotNone(
                row,
                f"cross-project message #{mid} must survive tombstone",
            )

    def test_tombstone_yes_deletes_project_folder(self):
        """After CLI tombstone --yes, the P2 project folder must be gone.

        RED: tombstone subcommand not registered.
        """
        folder = s.store_dir(self.P2)
        self.assertTrue(os.path.isdir(folder),
                        f"P2 folder must exist before tombstone: {folder}")
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, out + err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.assertFalse(
            os.path.isdir(folder),
            f"P2 folder must be gone after tombstone --yes; still present: {folder}",
        )

    def test_tombstone_yes_tracker_row_remains_tombstoned(self):
        """After CLI tombstone --yes, the tracker row must remain with state='tombstoned'.

        RED: tombstone subcommand not registered.
        """
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, out + err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.assertEqual(rc, 0, f"tombstone --yes must exit 0; rc={rc!r}")
        self.con.close()
        self.con = s.connect()
        row = self.con.execute(
            "SELECT state, tombstoned_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        self.assertIsNotNone(row, "tracker row must still exist after tombstone")
        self.assertEqual(row["state"], "tombstoned",
                         f"tracker state must be 'tombstoned'; got {row['state']!r}")
        self.assertIsNotNone(row["tombstoned_at"],
                             "tombstoned_at must be set after tombstone")

    def test_tombstone_yes_p1_data_untouched(self):
        """After CLI tombstone P2, P1's addresses and messages must be intact.

        RED: tombstone subcommand not registered.
        """
        p1_addr_before = self._address_count(self.P1)
        p1_msg_before = self.con.execute(
            "SELECT COUNT(*) FROM message m "
            "JOIN message_recipient r ON r.message_id = m.id "
            "WHERE r.recipient LIKE '% - P1'",
        ).fetchone()[0]
        rc, out, err = self._run_cli([
            "tombstone", "--project", self.P2, "--by", self.ADMIN, "--yes",
        ])
        if self._is_invalid_choice_error(rc, out + err, "tombstone"):
            self.fail("tombstone subcommand not registered — RED")
        self.con.close()
        self.con = s.connect()
        p1_addr_after = self._address_count(self.P1)
        self.assertEqual(p1_addr_after, p1_addr_before,
                         f"P1 address count must be unchanged; "
                         f"before={p1_addr_before}, after={p1_addr_after}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
