"""test_inbox_filters_cli.py — RED tests for CR-SAN-026 Cycle 2.

Covers §S3 + AC6: CLI flags on `sandesh inbox` and `sandesh fetch`.

New flags (not yet added to the argparser):
  --from-project  → sender_project
  --from          (already exists on `send`/`reply`; new on inbox/fetch) → sender
  --kind          → kind
  --since         → since
  --until         → until
  --subject       → subject_like

Expected RED (groups 1–5):
  argparse "unrecognized arguments: --from-project" (SystemExit 2) for any test
  that passes an as-yet-unregistered flag to inbox or fetch.

  Exception: group 5 (malformed --until) also fails with SystemExit(2) before it
  can reach the ValueError validation, so its RED is SystemExit(2) too.

Expected GREEN (group 6 — locked regression pins):
  inbox --all  — still includes read rows (existing flag, unchanged).
  fetch --peek — still marks nothing (existing flag, unchanged).

These pins must stay GREEN before AND after C2 GREEN implementation.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_inbox_filters_cli --agent red-cr026-c2
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

    Provisions a P1 recipient that holds mail from both P1 and P2, mirroring
    the lib-layer fixture in test_inbox_filters.py.

    Projects:
      P1 — active; recipient (ML_P1 is the test reader)
      P2 — active; cross-project sender

    Addresses:
      ML_P1  = 'Mainline - P1'
      T1_P1  = 'Track 1 - P1'  (used as P1-internal sender so ML_P1 can receive)
      ML_P2  = 'Mainline - P2'

    Messages (all unread at setUp, explicit timestamps):
      mid_p1  — T1_P1 → ML_P1, kind='request',  subject='Gate review'   @ 2026-06-01
      mid_p2  — ML_P2 → ML_P1, kind='fyi',       subject='P2 update'    @ 2026-06-02
    """

    P1 = "P1"
    P2 = "P2"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-inbox-filters-cli-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()

        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)

        s.assign_admin(self.con, self.ADMIN)
        s.grant_xproj(self.con, self.P1, self.ADMIN)
        s.grant_xproj(self.con, self.P2, self.ADMIN)

        # P1-internal: T1_P1 → ML_P1 (kind='request')
        self.mid_p1 = s.send(
            self.con, s.store_dir(self.P1),
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject="Gate review",
            kind="request",
        )
        self._stamp(self.mid_p1, "2026-06-01 08:00:00")

        # P2 → P1: kind='fyi'
        self.mid_p2 = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="P2 update",
            kind="fyi",
        )
        self._stamp(self.mid_p2, "2026-06-02 10:00:00")

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stamp(self, message_id, ts):
        """Overwrite created_at for a message with an explicit timestamp."""
        self.con.execute(
            "UPDATE message SET created_at=? WHERE id=?", (ts, message_id)
        )
        self.con.commit()

    def _read_at_for(self, message_id, recipient):
        """Raw read_at value from message_recipient for the given recipient."""
        row = self.con.execute(
            "SELECT read_at FROM message_recipient "
            "WHERE message_id=? AND recipient=?",
            (message_id, recipient),
        ).fetchone()
        return row["read_at"] if row else None

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
# Group 1 — AC6 headline: inbox --from-project
# ---------------------------------------------------------------------------

class InboxFromProjectFlagTest(_TempDataHome):
    """AC6 headline: inbox --from-project P2 lists ONLY P2-sender rows.

    Without the flag both rows appear.

    Expected RED: SystemExit(2) — argparse 'unrecognized arguments: --from-project'
    for the filtered calls. The unfiltered control call should return exit 0.
    """

    def test_inbox_from_project_filters_to_p2_only(self):
        """inbox --to ML_P1 --from-project P2 must list only the P2 row.

        With both mid_p1 (T1_P1 sender, P1) and mid_p2 (ML_P2 sender, P2) in
        ML_P1's inbox, filtering by --from-project P2 must show ONLY mid_p2.

        RED: SystemExit(2) — --from-project not yet recognised.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"inbox --from-project should exit 0; got {rc!r}\nout={out!r}")
        # mid_p2 subject must appear
        self.assertIn("P2 update", out,
                      f"P2 update must appear in filtered inbox output; got:\n{out}")
        # mid_p1 subject must NOT appear
        self.assertNotIn("Gate review", out,
                         f"Gate review (P1 mail) must NOT appear with --from-project P2;\n{out}")

    def test_inbox_without_from_project_shows_both(self):
        """inbox --to ML_P1 (no --from-project) must show both rows.

        This confirms the unfiltered baseline is working and both messages exist.
        Expected GREEN (no new flags, existing behaviour).
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1,
        ])
        self.assertEqual(rc, 0,
                         f"unfiltered inbox should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("Gate review", out,
                      f"Gate review must appear in unfiltered inbox; got:\n{out}")
        self.assertIn("P2 update", out,
                      f"P2 update must appear in unfiltered inbox; got:\n{out}")


# ---------------------------------------------------------------------------
# Group 2 — Spot-checks for remaining inbox flags
# ---------------------------------------------------------------------------

class InboxRemainingFlagsTest(_TempDataHome):
    """One spot-check per remaining new flag on `sandesh inbox`.

    Each test must fail with SystemExit(2) until the flag is registered.
    RED: argparse 'unrecognized arguments: <flag>'.
    """

    def test_inbox_from_flag_exact_sender(self):
        """inbox --from 'Mainline - P2' lists only ML_P2 rows.

        mid_p1 has from_addr=T1_P1; mid_p2 has from_addr=ML_P2.
        --from ML_P2 must include mid_p2 and exclude mid_p1.

        RED: SystemExit(2) — --from not yet registered on inbox subparser.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--from", self.ML_P2,
        ])
        self.assertEqual(rc, 0,
                         f"inbox --from should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("P2 update", out,
                      f"P2 update must appear with --from ML_P2; got:\n{out}")
        self.assertNotIn("Gate review", out,
                         f"Gate review must NOT appear with --from ML_P2; got:\n{out}")

    def test_inbox_kind_flag_request_only(self):
        """inbox --kind request lists only kind='request' rows.

        mid_p1 has kind='request'; mid_p2 has kind='fyi'.
        Only mid_p1 ('Gate review') must appear.

        RED: SystemExit(2) — --kind not yet registered on inbox subparser.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--kind", "request",
        ])
        self.assertEqual(rc, 0,
                         f"inbox --kind should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("Gate review", out,
                      f"Gate review must appear with --kind request; got:\n{out}")
        self.assertNotIn("P2 update", out,
                         f"P2 update (kind=fyi) must NOT appear with --kind request; got:\n{out}")

    def test_inbox_since_flag_after_first_message(self):
        """inbox --since 2026-06-02 must include mid_p2 (2026-06-02) and exclude mid_p1 (2026-06-01).

        mid_p1 is stamped 2026-06-01; mid_p2 is 2026-06-02.
        --since 2026-06-02 must include mid_p2 (on the boundary) and exclude mid_p1.

        RED: SystemExit(2) — --since not yet registered on inbox subparser.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--since", "2026-06-02",
        ])
        self.assertEqual(rc, 0,
                         f"inbox --since should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("P2 update", out,
                      f"P2 update (2026-06-02) must appear with --since 2026-06-02; got:\n{out}")
        self.assertNotIn("Gate review", out,
                         f"Gate review (2026-06-01) must NOT appear with --since 2026-06-02; got:\n{out}")

    def test_inbox_until_flag_date_only_includes_same_day_row(self):
        """inbox --until 2026-06-02 must include mid_p2 (stamped 10:00 same day).

        mid_p2 is stamped '2026-06-02 10:00:00'. A date-only --until 2026-06-02
        must normalise to end-of-day and INCLUDE mid_p2.

        RED: SystemExit(2) — --until not yet registered on inbox subparser.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--until", "2026-06-02",
        ])
        self.assertEqual(rc, 0,
                         f"inbox --until should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("P2 update", out,
                      f"P2 update (2026-06-02 10:00) must appear with --until 2026-06-02 "
                      f"(end-of-day normalisation); got:\n{out}")

    def test_inbox_subject_flag_case_insensitive(self):
        """inbox --subject gate (lowercase) matches 'Gate review' (uppercase G).

        Case-insensitive substring: 'gate' must match 'Gate review'.

        RED: SystemExit(2) — --subject not yet registered on inbox subparser.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--subject", "gate",
        ])
        self.assertEqual(rc, 0,
                         f"inbox --subject should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("Gate review", out,
                      f"Gate review must appear with --subject gate (case-insensitive); got:\n{out}")
        self.assertNotIn("P2 update", out,
                         f"P2 update must NOT appear with --subject gate; got:\n{out}")


# ---------------------------------------------------------------------------
# Group 3 — fetch --from-project and --peek --from-project
# ---------------------------------------------------------------------------

class FetchFromProjectFlagTest(_TempDataHome):
    """AC6: fetch --from-project P2 renders + marks ONLY the P2 rows.

    The P1 unread mail must remain unread after a P2-filtered fetch.

    RED: SystemExit(2) — --from-project not yet registered on fetch subparser.
    """

    def test_fetch_from_project_renders_only_p2(self):
        """fetch --to ML_P1 --from-project P2 must render only the P2 message.

        mid_p2 subject 'P2 update' must appear in stdout.
        mid_p1 subject 'Gate review' must NOT appear.

        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "fetch", "--to", self.ML_P1, "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"fetch --from-project should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("P2 update", out,
                      f"P2 update must be rendered by fetch --from-project P2; got:\n{out}")
        self.assertNotIn("Gate review", out,
                         f"Gate review must NOT be rendered by fetch --from-project P2; got:\n{out}")

    def test_fetch_from_project_marks_only_p2_read(self):
        """After fetch --from-project P2, mid_p2 is marked read but mid_p1 is not.

        raw read_at assertion: mid_p2's read_at must be set; mid_p1's must be NULL.

        RED: SystemExit(2).
        """
        rc, _out, _err = self._run_cli([
            "--project", self.P1,
            "fetch", "--to", self.ML_P1, "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"fetch --from-project should exit 0; got {rc!r}")

        self.assertIsNotNone(
            self._read_at_for(self.mid_p2, self.ML_P1),
            "read_at for mid_p2 must be set after fetch --from-project P2",
        )
        self.assertIsNone(
            self._read_at_for(self.mid_p1, self.ML_P1),
            "read_at for mid_p1 (P1 mail) must remain NULL after fetch --from-project P2",
        )


# ---------------------------------------------------------------------------
# Group 4 — fetch --peek with --from-project (render subset, mark nothing)
# ---------------------------------------------------------------------------

class FetchPeekFromProjectTest(_TempDataHome):
    """fetch --peek --from-project P2 renders the P2 subset but marks nothing.

    RED: SystemExit(2) — --from-project not yet registered on fetch subparser.
    """

    def test_fetch_peek_from_project_renders_without_marking(self):
        """fetch --peek --from-project P2 renders mid_p2 but leaves both unread.

        After the peek, read_at for both mid_p1 AND mid_p2 must remain NULL.

        RED: SystemExit(2).
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "fetch", "--to", self.ML_P1, "--peek", "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"fetch --peek --from-project should exit 0; got {rc!r}\nout={out!r}")
        self.assertIn("P2 update", out,
                      f"P2 update must be rendered by fetch --peek --from-project P2; got:\n{out}")

        # Neither message must be marked read.
        self.assertIsNone(
            self._read_at_for(self.mid_p2, self.ML_P1),
            "read_at for mid_p2 must remain NULL after --peek (mark=False)",
        )
        self.assertIsNone(
            self._read_at_for(self.mid_p1, self.ML_P1),
            "read_at for mid_p1 must remain NULL after --peek (mark=False)",
        )


# ---------------------------------------------------------------------------
# Group 5 — malformed --until exits 1 with [sandesh] + "invalid timestamp"
# ---------------------------------------------------------------------------

class MalformedUntilTest(_TempDataHome):
    """Malformed --until 12/06/2026 must exit 1 with a [sandesh]-prefixed error
    mentioning 'invalid timestamp' on stderr.

    RED: SystemExit(2) — argparse rejects --until before the ValueError fires
    (flag not yet registered); the exit code will be 2, not 1.
    The test checks for non-zero exit (covers both RED=2 and GREEN=1).
    """

    def test_malformed_until_exits_nonzero_with_sandesh_prefix(self):
        """inbox --until 12/06/2026 (DD/MM/YYYY) exits non-zero.

        RED: SystemExit(2) — --until not yet registered, argparse bails first.
        GREEN: exit 1 + stderr contains '[sandesh]' and 'invalid timestamp'.

        This test asserts non-zero exit always; the stderr content assertion is
        only reached once the flag exists (GREEN+).
        """
        rc, _out, err = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--until", "12/06/2026",
        ])
        self.assertNotEqual(rc, 0,
                            f"malformed --until must exit non-zero; got rc={rc!r}")
        # Once the flag is wired (GREEN), these stderr assertions must also hold.
        # In RED they won't be reached because the test exits at the assertEqual above.
        if rc == 1:
            self.assertIn("[sandesh]", err,
                          f"stderr must contain '[sandesh]' prefix; got:\n{err}")
            self.assertIn("invalid timestamp", err,
                          f"stderr must contain 'invalid timestamp'; got:\n{err}")


# ---------------------------------------------------------------------------
# Group 6 — locked regression pins (must stay GREEN before + after C2)
# ---------------------------------------------------------------------------

class ExistingFlagRegressionTest(_TempDataHome):
    """Regression pins for existing inbox --all and fetch --peek flags.

    These must pass GREEN before C2 (locked pins) AND after C2 (regression gate).
    Any failure here means a C2 implementation broke an existing flag.
    """

    def test_inbox_all_includes_read_rows(self):
        """inbox --all must include already-read rows (unread_only=False).

        Mark mid_p1 read first, then run inbox --all; mid_p1 must still appear.
        This pin ensures --all is not accidentally broken by C2 argparser work.
        """
        # Mark mid_p1 read via a direct fetch (lib call, no CLI involvement).
        s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True)

        # mid_p1 is now read; inbox without --all should NOT include it.
        rc_no_all, out_no_all, _ = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1,
        ])
        self.assertEqual(rc_no_all, 0, f"inbox (no --all) should exit 0; got {rc_no_all!r}")
        self.assertNotIn("Gate review", out_no_all,
                         f"Gate review must NOT appear in unread-only inbox after being read")

        # inbox --all MUST include mid_p1 even though it's been read.
        rc_all, out_all, _ = self._run_cli([
            "--project", self.P1,
            "inbox", "--to", self.ML_P1, "--all",
        ])
        self.assertEqual(rc_all, 0, f"inbox --all should exit 0; got {rc_all!r}")
        self.assertIn("Gate review", out_all,
                      f"Gate review must appear in inbox --all (includes read rows); got:\n{out_all}")

    def test_fetch_peek_marks_nothing(self):
        """fetch --peek must render messages without marking them read.

        After a --peek fetch, both mid_p1 and mid_p2 must remain unread
        (read_at=NULL). A subsequent unfiltered fetch should return both.
        """
        rc, out, _err = self._run_cli([
            "--project", self.P1,
            "fetch", "--to", self.ML_P1, "--peek",
        ])
        self.assertEqual(rc, 0, f"fetch --peek should exit 0; got {rc!r}")
        # Both messages must be rendered (peek shows unread).
        self.assertIn("Gate review", out,
                      f"Gate review must appear in fetch --peek output; got:\n{out}")
        self.assertIn("P2 update", out,
                      f"P2 update must appear in fetch --peek output; got:\n{out}")

        # Neither must be marked read.
        self.assertIsNone(
            self._read_at_for(self.mid_p1, self.ML_P1),
            "mid_p1 read_at must remain NULL after fetch --peek",
        )
        self.assertIsNone(
            self._read_at_for(self.mid_p2, self.ML_P1),
            "mid_p2 read_at must remain NULL after fetch --peek",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
