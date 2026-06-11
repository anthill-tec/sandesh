"""test_xproj_send.py — RED tests for CR-SAN-023 Cycle 3.

Covers §S1 + §S2 enforcement + §S3 + AC1/AC2/AC6 (+ completes AC3/AC4):

  send / reply:
    - grant-gated cross-project round-trip (AC1)
    - deny without grant — exact error message (AC2)
    - grant is project-inherited: all addresses of granted project may send (AC3)
    - revocation is project-wide (AC4)
    - tracker-state errors: archived / tombstoned / unknown from both directions (AC6)
    - mixed-recipient atomicity: any failing recipient aborts entire send (§S2/§S3)

  register:
    - project with no tracker row → "unknown project '<id>'" (§S3 DRIFT-6)
    - archived project → "is archived" (§S3)
    - tombstoned project → "is tombstoned" (§S3)

Expected RED:
  - AC1 round-trip fails because the OLD 022 error fires (grant check not yet in send).
  - AC2 exact-message assert fails (different error text from 022).
  - State-error tests fail (no state checks in send/register yet).
  - Register state checks fail (register currently succeeds with no tracker row).
  Locked behaviour (in-project sends, zero-row guards on valid paths) may already pass —
  these are noted as expected passes.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_xproj_send --agent red-cr023-c3
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

from sandesh import sandesh_db as s


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions:
      - P1, P2 (via setup) with Mainline + Track 1 registered in each
      - admin 'ops' assigned
      - P3 (archived) and P4 (tombstoned) for state-error tests — addresses
        registered BEFORE the state flip so they are valid addressbook rows
    Subclasses call super().setUp().
    """

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"   # will be flipped to archived
    P4 = "P4"   # will be flipped to tombstoned

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"
    ML_P3 = "Mainline - P3"
    ML_P4 = "Mainline - P4"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-xproj-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision all four projects and register addresses before any state flips.
        s.setup(self.P1)
        s.setup(self.P2)
        s.setup(self.P3)
        s.setup(self.P4)

        self.con = s.connect()

        # Register addresses while every project is still active.
        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)
        s.register(self.con, self.ML_P3, kind="mainline", project=self.P3)
        s.register(self.con, self.ML_P4, kind="mainline", project=self.P4)

        # Assign admin before tests run.
        s.assign_admin(self.con, "ops")

        # Flip P3 → archived, P4 → tombstoned via raw SQL so we bypass the
        # lifecycle-verb gate (those verbs come in CR-SAN-024).
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P3,))
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P4,))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, project_id):
        return s.store_dir(project_id)

    def _message_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def _recipient_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message_recipient").fetchone()[0]


# ---------------------------------------------------------------------------
# AC1 — granted cross-project round-trip
# ---------------------------------------------------------------------------

class XprojGrantedRoundTripTest(_TempDataHome):
    """AC1: after grant, P2 can send cross-project to P1; inbox + fetch return it.
    Also: reply back from P1 (ungranted) must FAIL with the AC2 error naming P1;
    then grant P1 → reply succeeds and thread is linked.

    RED: send currently rejects with the OLD 022 error before grant logic exists.
    """

    def test_granted_send_succeeds_and_appears_in_inbox(self):
        """After grant_xproj(P2), send from P2 to P1 succeeds; message in P1's inbox.

        RED: the 022 cross-project hard refusal fires before the grant check is
        implemented — send raises with the OLD message text.
        """
        s.grant_xproj(self.con, self.P2, "ops")

        mid = s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="hello from P2",
        )
        self.assertIsNotNone(mid, "send must return a message id")

        items = s.inbox(self.con, self.ML_P1)
        self.assertEqual(len(items), 1, f"ML_P1 inbox should have 1 item; got {len(items)}")
        self.assertEqual(items[0]["subject"], "hello from P2")

    def test_granted_send_fetch_returns_correct_from(self):
        """fetch returns the message with from=ML_P2 after a granted cross-project send.

        RED: same as above — send raises before fetch is reached.
        """
        s.grant_xproj(self.con, self.P2, "ops")

        s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="hello from P2",
        )
        items = s.fetch(self.con, self._store(self.P1), self.ML_P1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["from"], self.ML_P2,
                         f"from must be {self.ML_P2!r}; got {items[0]['from']!r}")

    def test_reply_from_ungranted_p1_to_p2_fails_with_correct_error(self):
        """A reply from ungranted P1 back to P2 (cross-project) must fail with the
        AC2 error naming P1.

        RED: before grant-logic, the reply fails with the 022 error OR a different
        error (no state checks) — not the exact AC2 text naming P1.
        """
        s.grant_xproj(self.con, self.P2, "ops")

        mid = s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="hello from P2",
        )
        # Mark read so we can reply.
        s.mark_read(self.con, self.ML_P1, [mid])

        with self.assertRaises(ValueError) as ctx:
            s.reply(
                self.con,
                self._store(self.P1),
                parent_id=mid,
                from_addr=self.ML_P1,
                subject="Re: hello from P2",
                project=self.P1,
            )
        self.assertIn(
            "cross-project sending not approved for project 'P1'",
            str(ctx.exception),
            f"error must mention P1 as the ungranted sender; got: {ctx.exception}",
        )

    def test_reply_from_granted_p1_succeeds_and_threads(self):
        """After granting P1 as well, the reply succeeds and in_reply_to is linked.

        RED: send + reply both need the grant-gated path to function.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        s.grant_xproj(self.con, self.P1, "ops")

        mid = s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="hello from P2",
        )
        s.mark_read(self.con, self.ML_P1, [mid])

        reply_mid = s.reply(
            self.con,
            self._store(self.P1),
            parent_id=mid,
            from_addr=self.ML_P1,
            project=self.P1,
        )
        self.assertIsNotNone(reply_mid, "reply must return a message id")

        # Verify thread linkage.
        chain = s.thread(self.con, reply_mid)
        self.assertEqual(
            len(chain), 2,
            f"thread must have 2 messages (parent + reply); got {len(chain)}",
        )
        self.assertEqual(chain[0]["id"], mid)
        self.assertEqual(chain[1]["id"], reply_mid)


# ---------------------------------------------------------------------------
# AC2 — deny without grant (exact error + zero rows + in-project still works)
# ---------------------------------------------------------------------------

class XprojDenyWithoutGrantTest(_TempDataHome):
    """AC2: cross-project send from ungranted P2 must raise with the exact error
    and leave zero rows; in-project sends are unaffected.

    RED: the 022 placeholder error is still present → exact-text assertion fails.
    """

    EXPECTED_GRANT_ERROR = (
        "cross-project sending not approved for project 'P2' — ask the Sandesh admin"
    )

    def test_cross_project_send_denied_exact_error(self):
        """Ungranted P2 cross-project send raises ValueError with exact AC2 message.

        RED: 022 error text differs — assert fails.
        """
        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con,
                self._store(self.P2),
                from_addr=self.ML_P2,
                to=[self.ML_P1],
                subject="should fail",
            )
        self.assertEqual(
            str(ctx.exception),
            self.EXPECTED_GRANT_ERROR,
            f"exact error mismatch; got: {ctx.exception!r}",
        )

    def test_cross_project_send_denied_writes_no_rows(self):
        """Failed cross-project send must write zero message + recipient rows.

        Partially RED: zero-row guard may pass but exact error text still fails.
        """
        before_msg = self._message_count()
        before_rec = self._recipient_count()

        with self.assertRaises(ValueError):
            s.send(
                self.con,
                self._store(self.P2),
                from_addr=self.ML_P2,
                to=[self.ML_P1],
                subject="should fail",
            )
        self.assertEqual(self._message_count(), before_msg,
                         "message row count must not increase on denied send")
        self.assertEqual(self._recipient_count(), before_rec,
                         "recipient row count must not increase on denied send")

    def test_in_project_send_still_succeeds_when_ungranted(self):
        """In-project P2 send (P2→P2) must succeed regardless of grant state.

        Locked: this was enforced before C3; may already pass.
        """
        mid = s.send(
            self.con,
            self._store(self.P2),
            from_addr=self.ML_P2,
            to=[self.T1_P2],
            subject="in-project ok",
        )
        self.assertIsNotNone(mid, "in-project send must succeed even if xproj ungranted")
        items = s.inbox(self.con, self.T1_P2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["subject"], "in-project ok")

    def test_old_022_error_text_absent_from_source(self):
        """The old CR-SAN-022 placeholder error must not appear in sandesh_db.py.

        RED: the string is still present in the current code.
        """
        db_path = os.path.join(_REPO_ROOT, "sandesh", "sandesh_db.py")
        with open(db_path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "cross-project sending is not enabled (CR-SAN-023)",
            source,
            "The 022 placeholder error must be removed from sandesh_db.py "
            "and replaced with the grant-gated check (CR-SAN-023 C3).",
        )


# ---------------------------------------------------------------------------
# AC3 — grant is project-inherited (all addresses of granted project may send)
# ---------------------------------------------------------------------------

class XprojGrantInheritanceTest(_TempDataHome):
    """AC3: after grant_xproj(P2), BOTH ML_P2 and T1_P2 can send cross-project —
    no per-address state; re-granting is a no-op (timestamp unchanged).

    RED: send still rejects with 022 error.
    """

    def test_both_p2_addresses_can_send_cross_project_after_grant(self):
        """ML_P2 AND T1_P2 both succeed after a single grant to P2.

        RED: grant logic not in send.
        """
        s.grant_xproj(self.con, self.P2, "ops")

        mid1 = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.ML_P1], subject="from mainline P2",
        )
        mid2 = s.send(
            self.con, self._store(self.P2),
            from_addr=self.T1_P2, to=[self.ML_P1], subject="from track P2",
        )

        self.assertIsNotNone(mid1, "ML_P2 send must succeed")
        self.assertIsNotNone(mid2, "T1_P2 send must succeed")

        items = s.inbox(self.con, self.ML_P1)
        self.assertEqual(len(items), 2,
                         f"ML_P1 must receive 2 messages; got {len(items)}")
        subjects = {it["subject"] for it in items}
        self.assertEqual(subjects, {"from mainline P2", "from track P2"})

    def test_regrant_is_noop_timestamp_unchanged(self):
        """Re-granting P2 is idempotent: the original xproj_granted_at is preserved.

        RED: grant logic not yet in send (this may pass in isolation since it only
        tests grant_xproj idempotency, but is paired with a send assertion).
        """
        s.grant_xproj(self.con, self.P2, "ops")
        row_before = self.con.execute(
            "SELECT xproj_granted_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        first_ts = row_before["xproj_granted_at"]
        self.assertIsNotNone(first_ts, "first grant must set xproj_granted_at")

        # Re-grant — must be no-op.
        s.grant_xproj(self.con, self.P2, "ops")
        row_after = self.con.execute(
            "SELECT xproj_granted_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        self.assertEqual(row_after["xproj_granted_at"], first_ts,
                         "re-grant must NOT update xproj_granted_at")

        # The grant must allow sending after idempotent re-grant.
        mid = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.ML_P1], subject="after regrant",
        )
        self.assertIsNotNone(mid)


# ---------------------------------------------------------------------------
# AC4 — revocation is project-wide
# ---------------------------------------------------------------------------

class XprojRevocationTest(_TempDataHome):
    """AC4: grant → send ok → revoke → same send fails with AC2 error; in-project unaffected.

    RED: grant-gated path not in send → first send fails.
    """

    EXPECTED_GRANT_ERROR = (
        "cross-project sending not approved for project 'P2' — ask the Sandesh admin"
    )

    def test_send_succeeds_after_grant_fails_after_revoke(self):
        """grant→send ok→revoke→send fails with exact AC2 error.

        RED: send fails with 022 error even before revoke.
        """
        s.grant_xproj(self.con, self.P2, "ops")

        # Post-grant: cross-project send must succeed.
        mid = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.ML_P1], subject="pre-revoke",
        )
        self.assertIsNotNone(mid, "send after grant must succeed")

        # Revoke.
        s.revoke_xproj(self.con, self.P2, "ops")

        # Post-revoke: same send must fail with the exact AC2 error.
        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P2),
                from_addr=self.ML_P2, to=[self.ML_P1], subject="post-revoke",
            )
        self.assertEqual(
            str(ctx.exception),
            self.EXPECTED_GRANT_ERROR,
            f"post-revoke error mismatch; got: {ctx.exception!r}",
        )

    def test_in_project_send_unaffected_by_revoke(self):
        """In-project P2 send is unaffected by revocation.

        Locked: same behaviour as pre-C3; may already pass.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        s.revoke_xproj(self.con, self.P2, "ops")

        mid = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.T1_P2], subject="inproject after revoke",
        )
        self.assertIsNotNone(mid, "in-project send must succeed even after revoke")
        items = s.inbox(self.con, self.T1_P2)
        self.assertEqual(len(items), 1)

    def test_revoke_writes_zero_rows_on_failed_send(self):
        """After revoke, failed cross-project send writes zero rows.

        RED: depends on grant-gated send path.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.ML_P1], subject="before revoke",
        )
        s.revoke_xproj(self.con, self.P2, "ops")

        before_msg = self._message_count()
        before_rec = self._recipient_count()

        with self.assertRaises(ValueError):
            s.send(
                self.con, self._store(self.P2),
                from_addr=self.ML_P2, to=[self.ML_P1], subject="after revoke",
            )

        self.assertEqual(self._message_count(), before_msg,
                         "no message row should be added on revoked send")
        self.assertEqual(self._recipient_count(), before_rec,
                         "no recipient row should be added on revoked send")


# ---------------------------------------------------------------------------
# AC6 — tracker-state errors (both directions, both states)
# ---------------------------------------------------------------------------

class XprojSenderStateTest(_TempDataHome):
    """AC6 sender-side: send FROM an archived or tombstoned sender → state errors.
    Also: sender whose project row has been DELETED → 'unknown project'.

    RED: no sender-side state checks exist in send().
    """

    def test_send_from_archived_project_fails(self):
        """send from ML_P3 (archived project) raises ValueError containing
        "project 'P3' is archived".

        RED: no sender-state check in send() — may raise the 022 cross-project
        error or succeed silently.
        """
        # P3 is archived (set in setUp).
        # P1 must be granted to reach the state check (if order matters).
        s.grant_xproj(self.con, self.P1, "ops")

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P3),
                from_addr=self.ML_P3, to=[self.ML_P1], subject="from archived",
            )
        self.assertIn("project 'P3' is archived", str(ctx.exception),
                      f"expected archived error; got: {ctx.exception!r}")

    def test_send_from_tombstoned_project_fails(self):
        """send from ML_P4 (tombstoned project) raises ValueError containing
        "project 'P4' is tombstoned".

        RED: no sender-state check.
        """
        s.grant_xproj(self.con, self.P1, "ops")

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P4),
                from_addr=self.ML_P4, to=[self.ML_P1], subject="from tombstoned",
            )
        self.assertIn("project 'P4' is tombstoned", str(ctx.exception),
                      f"expected tombstoned error; got: {ctx.exception!r}")

    def test_send_from_unknown_project_fails(self):
        """send from an address whose project row has been deleted raises
        ValueError containing "unknown project".

        RED: no sender-state check in send().
        """
        # Delete the P2 project tracker row to simulate 'unknown'.
        self.con.execute("DELETE FROM project WHERE project_id=?", (self.P2,))
        self.con.commit()

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P2),
                from_addr=self.ML_P2, to=[self.ML_P1], subject="from unknown",
            )
        self.assertIn("unknown project", str(ctx.exception),
                      f"expected unknown-project error; got: {ctx.exception!r}")

    def test_sender_state_errors_write_no_rows(self):
        """Each sender-state error must leave zero new rows (atomicity).

        RED: errors don't exist yet → rows may be written or wrong error raised.
        """
        before_msg = self._message_count()
        before_rec = self._recipient_count()

        # P3 archived send attempt.
        with self.assertRaises((ValueError, Exception)):
            s.send(
                self.con, self._store(self.P3),
                from_addr=self.ML_P3, to=[self.ML_P1], subject="archived",
            )
        # P4 tombstoned send attempt.
        with self.assertRaises((ValueError, Exception)):
            s.send(
                self.con, self._store(self.P4),
                from_addr=self.ML_P4, to=[self.ML_P1], subject="tombstoned",
            )

        self.assertEqual(self._message_count(), before_msg,
                         "no message rows after failed state-checked sends")
        self.assertEqual(self._recipient_count(), before_rec,
                         "no recipient rows after failed state-checked sends")


class XprojRecipientStateTest(_TempDataHome):
    """AC6 recipient-side: send from granted P1 TO an archived or tombstoned project
    or an address whose project row was deleted → distinct state errors.

    RED: no recipient-project-state check exists — only the grant check fires,
    so the archived/tombstoned/unknown recipient errors do not exist yet.
    """

    def test_send_to_archived_project_recipient_fails(self):
        """send from granted P1 to ML_P3 (archived) raises ValueError containing
        "project 'P3' is archived".

        RED: no recipient-state check.
        """
        s.grant_xproj(self.con, self.P1, "ops")

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P3], subject="to archived",
            )
        self.assertIn("project 'P3' is archived", str(ctx.exception),
                      f"expected archived error for P3 recipient; got: {ctx.exception!r}")

    def test_send_to_tombstoned_project_recipient_fails(self):
        """send from granted P1 to ML_P4 (tombstoned) raises ValueError containing
        "project 'P4' is tombstoned".

        RED: no recipient-state check.
        """
        s.grant_xproj(self.con, self.P1, "ops")

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P4], subject="to tombstoned",
            )
        self.assertIn("project 'P4' is tombstoned", str(ctx.exception),
                      f"expected tombstoned error for P4 recipient; got: {ctx.exception!r}")

    def test_send_to_address_in_deleted_project_fails(self):
        """send from granted P1 to ML_P2 whose project row was deleted → unknown project.

        RED: no recipient-state check in send().
        """
        s.grant_xproj(self.con, self.P1, "ops")
        # Delete the P2 tracker row.
        self.con.execute("DELETE FROM project WHERE project_id=?", (self.P2,))
        self.con.commit()

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P2], subject="to unknown",
            )
        self.assertIn("unknown project", str(ctx.exception),
                      f"expected unknown-project error; got: {ctx.exception!r}")

    def test_recipient_state_errors_distinct_from_grant_error(self):
        """The archived/tombstoned errors are distinct from the grant error — a granted
        sender hitting an archived recipient gets the state error, not the grant error.

        RED: state checks don't exist; only the grant or 022 error fires.
        """
        s.grant_xproj(self.con, self.P1, "ops")

        grant_text = "not approved"

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P3], subject="to archived",
            )
        error_text = str(ctx.exception)
        self.assertNotIn(grant_text, error_text,
                         f"archived-recipient error must NOT be the grant error; got: {error_text!r}")
        self.assertIn("archived", error_text,
                      f"error must indicate archived state; got: {error_text!r}")

    def test_recipient_state_errors_write_no_rows(self):
        """Each recipient-state error must write zero rows.

        RED: checks don't exist.
        """
        s.grant_xproj(self.con, self.P1, "ops")
        before_msg = self._message_count()
        before_rec = self._recipient_count()

        with self.assertRaises((ValueError, Exception)):
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P3], subject="to archived",
            )
        with self.assertRaises((ValueError, Exception)):
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1, to=[self.ML_P4], subject="to tombstoned",
            )

        self.assertEqual(self._message_count(), before_msg)
        self.assertEqual(self._recipient_count(), before_rec)


# ---------------------------------------------------------------------------
# Mixed-recipient atomicity
# ---------------------------------------------------------------------------

class XprojMixedAtomicityTest(_TempDataHome):
    """§S2/§S3: a mixed to-list with one bad recipient aborts the entire send —
    not even the valid in-project recipient gets delivered.

    RED: the bad-recipient state check doesn't exist; OR the valid recipient IS
    delivered before the check fires (pre-insert checks would pass this atomicity
    requirement; the old code also passes it — this confirms locked behaviour while
    the new state check is the real RED).
    """

    def test_mixed_list_archived_recipient_aborts_all(self):
        """to=[T1_P1 (valid in-project), ML_P3 (archived)] → error, nothing delivered.

        The valid in-project T1_P1 must receive NOTHING (atomicity).
        RED: no archived-recipient check → ML_P3 may be flagged as inactive or
        allowed; T1_P1 might get the message. State check error is what's missing.
        """
        s.grant_xproj(self.con, self.P1, "ops")
        before_msg = self._message_count()
        before_rec = self._recipient_count()

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1,
                to=[self.T1_P1, self.ML_P3],
                subject="mixed list",
            )

        error_text = str(ctx.exception)
        self.assertIn("P3", error_text,
                      f"error must name the bad project P3; got: {error_text!r}")
        self.assertEqual(self._message_count(), before_msg,
                         "no message row must be written on mixed-list failure")
        self.assertEqual(self._recipient_count(), before_rec,
                         "no recipient row must be written on mixed-list failure")

        # T1_P1 (the valid in-project recipient) must be undelivered.
        self.assertEqual(len(s.inbox(self.con, self.T1_P1)), 0,
                         "T1_P1 must receive nothing when send is aborted")

    def test_mixed_list_tombstoned_recipient_aborts_all(self):
        """to=[T1_P1, ML_P4 (tombstoned)] → error, T1_P1 receives nothing.

        RED: tombstoned-recipient check absent.
        """
        s.grant_xproj(self.con, self.P1, "ops")
        before_msg = self._message_count()
        before_rec = self._recipient_count()

        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self._store(self.P1),
                from_addr=self.ML_P1,
                to=[self.T1_P1, self.ML_P4],
                subject="mixed list tombstoned",
            )

        self.assertIn("P4", str(ctx.exception))
        self.assertEqual(self._message_count(), before_msg)
        self.assertEqual(self._recipient_count(), before_rec)
        self.assertEqual(len(s.inbox(self.con, self.T1_P1)), 0)


# ---------------------------------------------------------------------------
# Register state checks (§S3 DRIFT-6)
# ---------------------------------------------------------------------------

class RegisterStateCheckTest(_TempDataHome):
    """§S3 register: register into a project with no tracker row → 'unknown project';
    archived → 'is archived'; tombstoned → 'is tombstoned'.

    RED: register currently succeeds even with no tracker row (DRIFT-6 — today it
    silently inserts without checking the project tracker).
    """

    def test_register_into_never_setup_project_fails(self):
        """register into project 'Ghost' (no tracker row) raises ValueError containing
        "unknown project 'Ghost'".

        RED: register does not check the project tracker today.
        """
        with self.assertRaises(ValueError) as ctx:
            s.register(self.con, "Mainline - Ghost", kind="mainline", project="Ghost")
        self.assertIn("unknown project 'Ghost'", str(ctx.exception),
                      f"expected unknown-project error; got: {ctx.exception!r}")

    def test_register_into_archived_project_fails(self):
        """register into archived P3 raises ValueError containing "is archived".

        RED: register does not check project state.
        """
        with self.assertRaises(ValueError) as ctx:
            s.register(self.con, "Track 2 - P3", kind="track", project=self.P3)
        self.assertIn("is archived", str(ctx.exception),
                      f"expected archived error; got: {ctx.exception!r}")

    def test_register_into_tombstoned_project_fails(self):
        """register into tombstoned P4 raises ValueError containing "is tombstoned".

        RED: register does not check project state.
        """
        with self.assertRaises(ValueError) as ctx:
            s.register(self.con, "Track 2 - P4", kind="track", project=self.P4)
        self.assertIn("is tombstoned", str(ctx.exception),
                      f"expected tombstoned error; got: {ctx.exception!r}")

    def test_register_into_unknown_project_writes_no_address_row(self):
        """Failed register into 'Ghost' must write no address row.

        RED: today register succeeds (no check) → row IS written.
        """
        before = self.con.execute("SELECT COUNT(*) FROM address").fetchone()[0]
        with self.assertRaises((ValueError, Exception)):
            s.register(self.con, "Mainline - Ghost", kind="mainline", project="Ghost")
        after = self.con.execute("SELECT COUNT(*) FROM address").fetchone()[0]
        self.assertEqual(after, before,
                         "register into unknown project must not create an address row")

    def test_register_into_active_project_still_works(self):
        """register into active P1 for a NEW address works fine.

        Locked: this verifies we haven't broken the happy path.
        """
        s.register(self.con, "Track 2 - P1", kind="track", project=self.P1)
        self.assertTrue(s.is_active(self.con, "Track 2 - P1"),
                        "newly registered Track 2 - P1 must be active")


# ---------------------------------------------------------------------------
# In-project send is never touched by the grant check
# ---------------------------------------------------------------------------

class XprojInProjectUnaffectedTest(_TempDataHome):
    """§S2: in-project sends are NEVER affected by the grant.
    This is the locked-behaviour set — expected to already pass.
    """

    def test_in_project_send_from_ungranted_project_succeeds(self):
        """P2 (ungranted) can send to T1_P2 (same project) without any grant.

        Locked: expected to pass before and after C3.
        """
        mid = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.T1_P2], subject="in project",
        )
        self.assertIsNotNone(mid)
        items = s.inbox(self.con, self.T1_P2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["subject"], "in project")

    def test_in_project_send_from_granted_project_still_works(self):
        """P2 (granted) can still send in-project to T1_P2.

        Locked: granting must not break in-project sends.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        mid = s.send(
            self.con, self._store(self.P2),
            from_addr=self.ML_P2, to=[self.T1_P2], subject="in project granted",
        )
        self.assertIsNotNone(mid)


if __name__ == "__main__":
    unittest.main()
