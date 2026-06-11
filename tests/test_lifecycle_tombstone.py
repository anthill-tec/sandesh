"""test_lifecycle_tombstone.py — RED tests for CR-SAN-024 Cycle 2.

Covers §S1 (tombstone_project) + AC1/AC4/AC6/AC8/AC9 + AC11 tail.

  tombstone_project(con, project_id, by, *, force=False, wait_secs=None)
    — archived→tombstoned; admin-only `by`; ordered purge of internal rows;
      deletes projects/<id>/ folder entirely; tombstoned_at set; tracker row
      preserved; xproj_granted_* columns untouched (AC11 tail).

State guards (exact messages):
  active      → ValueError containing "archive it first"
  tombstoned  → ValueError containing "already tombstoned"
  unknown id  → ValueError containing "unknown project '<id>'"

Authz guards:
  empty admin table → PermissionError containing "no admin assigned"
  by == project's own Mainline → PermissionError
  by == stored admin → proceeds (state permitting)

Purge (DRIFT-2 ordering):
  1. internal message rows + message_recipient rows GONE
  2. cross-project message rows PRESENT; their recipient rows (incl. P2-address
     rows on surviving messages) PRESENT
  3. P2's address rows GONE; P2's notifier rows GONE
  4. projects/P2/ directory gone from disk entirely (bodies included)
  5. tracker row remains state='tombstoned', tombstoned_at non-NULL,
     xproj_granted_* columns still populated if granted pre-archive (raw assert)
  P1's data completely untouched (addresses, messages, bodies — counts).

Multi-recipient edge cases:
  - one P2-internal message whose recipients are BOTH P2 addresses → purged
  - one P2→[P2-track, P1-mainline] message (cross-project, P2 granted) →
    classified CROSS (survives); assert both behaviours

AC9 pin: setup('P2') afterwards raises containing "retired (tombstoned)".
Idempotence-ish: second tombstone_project on P2 → "already tombstoned" error.

Expected RED:
  AttributeError: module 'sandesh.sandesh_db' has no attribute 'tombstone_project'
  (callable pre-check pattern used so no vacuous assertRaises passes)

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_lifecycle_tombstone --agent red-cr024-c2
"""

import os
import shutil
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s


# ---------------------------------------------------------------------------
# Fixture base class — reused across all test classes
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions:
      - P1 + P2 with Mainline + Track 1 registered in each
      - admin 'ops' assigned
      - P2 granted cross-project sending (so cross-project fixture messages work)
      - P2 ARCHIVED (the required pre-condition for tombstone_project)

    The message matrix is built by subclasses or individual tests that need it.
    """

    P1 = "P1"
    P2 = "P2"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-tombstone-test-")
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

        # Grant P2 for cross-project sends, then archive P2 (the C1-shipped
        # precondition required before tombstone_project can proceed).
        s.grant_xproj(self.con, self.P2, self.ADMIN)
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- raw helpers ----

    def _project_row(self, project_id):
        return self.con.execute(
            "SELECT * FROM project WHERE project_id=?", (project_id,)
        ).fetchone()

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

    def _recipient_count_for_message(self, message_id):
        return self.con.execute(
            "SELECT COUNT(*) FROM message_recipient WHERE message_id=?",
            (message_id,)
        ).fetchone()[0]

    def _notifier_count(self, project_id):
        """Number of notifier rows whose recipient belongs to project_id."""
        return self.con.execute(
            "SELECT COUNT(*) FROM notifier n "
            "JOIN address a ON a.address = n.recipient "
            "WHERE a.project=?",
            (project_id,)
        ).fetchone()[0]

    def _p2_folder_exists(self):
        folder = s.store_dir(self.P2)
        return os.path.isdir(folder)

    def _assert_tombstone_project_exists(self):
        """Callable pre-check: AttributeError if not yet implemented (RED gate).
        Call this at the top of any test that would otherwise be vacuous via
        assertRaises catching AttributeError instead of the expected exception."""
        self.assertTrue(
            callable(getattr(s, "tombstone_project", None)),
            "sandesh_db.tombstone_project is not callable — "
            "implement it (GREEN). This is the expected RED failure.",
        )


# ---------------------------------------------------------------------------
# T1 — tombstone_project callable
# ---------------------------------------------------------------------------

class TombstoneProjectCallableTest(_TempDataHome):
    """tombstone_project must be a callable attribute of sandesh_db.

    RED: AttributeError — not yet implemented.
    """

    def test_tombstone_project_callable(self):
        """sandesh_db.tombstone_project must be a callable attribute.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "tombstone_project", None)),
            "sandesh_db.tombstone_project is not callable — implement it (GREEN).",
        )


# ---------------------------------------------------------------------------
# T2 — state guard: active project → "archive it first"
# ---------------------------------------------------------------------------

class TombstoneStateGuardActiveTest(_TempDataHome):
    """tombstone_project on an ACTIVE project must raise ValueError containing
    'archive it first' and change nothing.

    AC1: tombstone_project on an active project errors with "archive it first".
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def setUp(self):
        super().setUp()
        # Undo the archive so P2 is active again (testing the guard path)
        self.con.execute(
            "UPDATE project SET state='active', archived_at=NULL WHERE project_id=?",
            (self.P2,))
        self.con.commit()

    def test_active_project_raises_value_error(self):
        """tombstone_project on active P2 must raise ValueError.

        RED: callable pre-check → AttributeError (not yet implemented).
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError):
            s.tombstone_project(self.con, self.P2, self.ADMIN)

    def test_active_project_error_contains_archive_it_first(self):
        """ValueError for active project must contain 'archive it first'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, self.P2, self.ADMIN)
        self.assertIn(
            "archive it first", str(ctx.exception),
            f"Error must contain 'archive it first'; got: {ctx.exception!r}",
        )

    def test_active_project_state_unchanged(self):
        """After raising on active, state must remain 'active'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        try:
            s.tombstone_project(self.con, self.P2, self.ADMIN)
        except (ValueError, AttributeError):
            pass
        self.assertEqual(
            self._raw_project_state(self.P2), "active",
            "State must remain 'active' after guard fires on active project",
        )


# ---------------------------------------------------------------------------
# T3 — state guard: already tombstoned → "already tombstoned"
# ---------------------------------------------------------------------------

class TombstoneStateGuardTombstonedTest(_TempDataHome):
    """tombstone_project on an already-tombstoned project must raise ValueError
    containing 'already tombstoned'.

    AC1 + idempotence-ish: second call → "already tombstoned" error.
    RED: AttributeError — not yet implemented.
    """

    def setUp(self):
        super().setUp()
        # Seed tombstoned state via raw SQL
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_already_tombstoned_raises_value_error(self):
        """tombstone_project on already-tombstoned P2 must raise ValueError.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError):
            s.tombstone_project(self.con, self.P2, self.ADMIN)

    def test_already_tombstoned_error_message(self):
        """ValueError must contain 'already tombstoned'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, self.P2, self.ADMIN)
        self.assertIn(
            "already tombstoned", str(ctx.exception),
            f"Error must contain 'already tombstoned'; got: {ctx.exception!r}",
        )

    def test_already_tombstoned_project_named_in_error(self):
        """ValueError must name the project id.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, self.P2, self.ADMIN)
        self.assertIn(
            self.P2, str(ctx.exception),
            f"Error must name the project; got: {ctx.exception!r}",
        )


# ---------------------------------------------------------------------------
# T4 — state guard: unknown project
# ---------------------------------------------------------------------------

class TombstoneStateGuardUnknownTest(_TempDataHome):
    """tombstone_project on an unknown project_id must raise ValueError containing
    "unknown project '<id>'".

    RED: AttributeError — not yet implemented.
    """

    def test_unknown_project_raises_value_error(self):
        """tombstone_project('NoSuchProject', ...) must raise ValueError.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError):
            s.tombstone_project(self.con, "NoSuchProject", self.ADMIN)

    def test_unknown_project_error_message(self):
        """ValueError must contain "unknown project 'NoSuchProject'".

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, "NoSuchProject", self.ADMIN)
        self.assertIn(
            "unknown project", str(ctx.exception),
            f"Error must contain 'unknown project'; got: {ctx.exception!r}",
        )
        self.assertIn(
            "NoSuchProject", str(ctx.exception),
            f"Error must name the unknown project id; got: {ctx.exception!r}",
        )


# ---------------------------------------------------------------------------
# T5 — authz guard: empty admin table → "no admin assigned"
# ---------------------------------------------------------------------------

class TombstoneAuthzEmptyAdminTest(unittest.TestCase):
    """tombstone_project with an empty admin table must raise PermissionError
    containing 'no admin assigned'.

    AC8: empty admin table → clear 'no admin assigned' error.
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-tombstone-noadmin-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup("P2")
        self.con = s.connect()

        s.register(self.con, "Mainline - P2", kind="mainline", project="P2")
        s.register(self.con, "Track 1 - P2",  kind="track",    project="P2")

        # NO assign_admin call — admin table stays empty.
        # Archive P2 via raw SQL (we can't use the real archive() without admin
        # because the setUp skips admin assignment; raw SQL is correct here since
        # we're testing the tombstone_project admin check, not the archive path).
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id='P2'")
        self.con.commit()

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_admin_raises_permission_error(self):
        """tombstone_project with no admin assigned must raise PermissionError.

        RED: callable pre-check → AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "tombstone_project", None)),
            "sandesh_db.tombstone_project must be callable (GREEN).",
        )
        with self.assertRaises(PermissionError):
            s.tombstone_project(self.con, "P2", "anyone")

    def test_empty_admin_error_contains_no_admin_assigned(self):
        """PermissionError must contain 'no admin assigned'.

        RED: callable pre-check → AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "tombstone_project", None)),
            "sandesh_db.tombstone_project must be callable (GREEN).",
        )
        with self.assertRaises(PermissionError) as ctx:
            s.tombstone_project(self.con, "P2", "anyone")
        self.assertIn(
            "no admin assigned", str(ctx.exception),
            f"Error must contain 'no admin assigned'; got: {ctx.exception!r}",
        )


# ---------------------------------------------------------------------------
# T6 — authz guard: by == project's Mainline → rejected
# ---------------------------------------------------------------------------

class TombstoneAuthzMainlineRejectedTest(_TempDataHome):
    """tombstone_project with by == the project's own Mainline must be rejected
    with PermissionError (only the super-admin may tombstone).

    AC6: tombstone with by == the project's Mainline is rejected.
    RED: AttributeError — not yet implemented.
    """

    def test_by_mainline_raises_permission_error(self):
        """tombstone_project(P2, by='Mainline - P2') must raise PermissionError.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(PermissionError):
            s.tombstone_project(self.con, self.P2, self.ML_P2)

    def test_by_mainline_error_message(self):
        """PermissionError when by==Mainline must be informative (not empty).

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(PermissionError) as ctx:
            s.tombstone_project(self.con, self.P2, self.ML_P2)
        self.assertTrue(
            len(str(ctx.exception)) > 0,
            "PermissionError message must not be empty",
        )

    def test_by_mainline_state_unchanged(self):
        """After rejection (by==Mainline), state must remain 'archived'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        try:
            s.tombstone_project(self.con, self.P2, self.ML_P2)
        except (PermissionError, AttributeError):
            pass
        self.assertEqual(
            self._raw_project_state(self.P2), "archived",
            "State must remain 'archived' after PermissionError (by==Mainline)",
        )

    def test_by_wrong_admin_raises_permission_error(self):
        """tombstone_project with a non-admin identity must raise PermissionError.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        with self.assertRaises(PermissionError):
            s.tombstone_project(self.con, self.P2, "not-the-admin")


# ---------------------------------------------------------------------------
# T7 — happy path: state transition + tombstoned_at set
# ---------------------------------------------------------------------------

class TombstoneStateMachineTest(_TempDataHome):
    """tombstone_project(con, P2, 'ops') must flip archived→tombstoned and set
    tombstoned_at to a non-NULL value.

    AC1: tombstone_project on an archived project flips to tombstoned (+tombstoned_at).
    RED: AttributeError — not yet implemented.
    """

    def test_tombstone_sets_state_to_tombstoned(self):
        """tombstone_project must set state='tombstoned'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state(self.P2), "tombstoned",
            "state must be 'tombstoned' after tombstone_project()",
        )

    def test_tombstone_sets_tombstoned_at_nonnull(self):
        """tombstone_project must set tombstoned_at to a non-NULL value.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        row = self._project_row(self.P2)
        self.assertIsNotNone(
            row["tombstoned_at"],
            "tombstoned_at must be non-NULL after tombstone_project()",
        )

    def test_tombstone_does_not_affect_p1_state(self):
        """tombstone_project(P2) must not alter P1's state.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state(self.P1), "active",
            "P1 state must remain 'active' after tombstoning P2",
        )


# ---------------------------------------------------------------------------
# T8 — idempotence-ish: second tombstone → "already tombstoned"
# ---------------------------------------------------------------------------

class TombstoneIdempotenceTest(_TempDataHome):
    """A second tombstone_project call on an already-tombstoned project must raise
    ValueError containing 'already tombstoned' — no partial work.

    RED: AttributeError — not yet implemented.
    """

    def test_second_tombstone_raises_already_tombstoned(self):
        """Second tombstone_project must raise ValueError 'already tombstoned'.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        # First call (the actual tombstone)
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        # Second call must raise
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertIn(
            "already tombstoned", str(ctx.exception),
            f"Second tombstone must say 'already tombstoned'; got: {ctx.exception!r}",
        )


# ---------------------------------------------------------------------------
# T9 — AC11 tail: xproj_granted_* columns survive tombstone (raw assert)
# ---------------------------------------------------------------------------

class TombstoneGrantColumnsUntouchedTest(_TempDataHome):
    """After tombstone_project, the xproj_granted_at and xproj_granted_by columns
    must still be populated on the tracker row (the permanent marker rule).

    AC11 tail: after tombstone the xproj_granted_* columns are still populated
    on the marker row (asserted raw).
    RED: AttributeError — not yet implemented.
    """

    def test_xproj_granted_at_survives_tombstone(self):
        """xproj_granted_at must remain non-NULL on the tracker row after tombstone.

        RED: callable pre-check → AttributeError.
        setUp already calls grant_xproj(P2) before archive, so the columns are set.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        row = self._project_row(self.P2)
        self.assertIsNotNone(
            row["xproj_granted_at"],
            "xproj_granted_at must remain populated on the tombstoned tracker row",
        )

    def test_xproj_granted_by_survives_tombstone(self):
        """xproj_granted_by must remain 'ops' on the tracker row after tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        row = self._project_row(self.P2)
        self.assertEqual(
            row["xproj_granted_by"], self.ADMIN,
            f"xproj_granted_by must be {self.ADMIN!r} after tombstone; "
            f"got {row['xproj_granted_by']!r}",
        )

    def test_created_at_and_grant_columns_untouched(self):
        """created_at must be unchanged; xproj columns populated (full raw check).

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        before = self._project_row(self.P2)
        created_at_before = before["created_at"]
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        after = self._project_row(self.P2)
        self.assertEqual(
            after["created_at"], created_at_before,
            "created_at must not change after tombstone",
        )
        self.assertIsNotNone(
            after["xproj_granted_at"],
            "xproj_granted_at must remain set after tombstone",
        )
        self.assertEqual(
            after["xproj_granted_by"], self.ADMIN,
            "xproj_granted_by must remain 'ops' after tombstone",
        )


# ---------------------------------------------------------------------------
# T10 — AC9 pin: setup('P2') after tombstone raises "retired (tombstoned)"
# ---------------------------------------------------------------------------

class TombstoneSetupRetiredTest(_TempDataHome):
    """After tombstone_project(P2), setup('P2') must raise ValueError containing
    'retired (tombstoned)'.

    AC9: setup('P2') afterwards raises containing 'retired (tombstoned)'.
    RED: AttributeError — not yet implemented.
    """

    def test_setup_raises_after_tombstone(self):
        """setup('P2') after tombstone must raise ValueError.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        with self.assertRaises(ValueError):
            s.setup(self.P2)

    def test_setup_error_contains_retired_tombstoned(self):
        """ValueError from setup must contain 'retired (tombstoned)'.

        RED: callable pre-check → AttributeError.
        (setup already has this guard; test pins it works post-tombstone.)
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        with self.assertRaises(ValueError) as ctx:
            s.setup(self.P2)
        self.assertIn(
            "retired (tombstoned)", str(ctx.exception),
            f"setup() error must contain 'retired (tombstoned)'; "
            f"got: {ctx.exception!r}",
        )


# ---------------------------------------------------------------------------
# T11 — purge fixture base: build the full message matrix
# ---------------------------------------------------------------------------

class _PurgeFixture(_TempDataHome):
    """Extends _TempDataHome with the full message matrix required by AC4/DRIFT-2.

    Message matrix built in setUp:
      msg_int1   — P2-internal, from=ML_P2, to=[T1_P2] (simple internal)
      msg_int2   — P2-internal, from=ML_P2, to=[ML_P2, T1_P2] (BOTH P2 addresses)
      msg_reply  — P2-internal reply to msg_int1 (reply chain)
      msg_int_body — P2-internal with a body file (body lives in P2's folder)
      msg_p1_to_p2 — cross-project P1→P2 (granted via setUp's grant_xproj on P2)
      msg_p2_to_p1 — cross-project P2→P1 with a body file in P2's folder
      msg_mixed   — P2→[T1_P2, ML_P1] — mixed recipients; classified CROSS because
                    it has a cross-project recipient (P1-mainline) — survives

    P1 also gets a P1-internal message (msg_p1_internal) to verify P1 data is
    completely untouched after tombstoning P2.

    NOTE: grant_xproj(P2) was already called in _TempDataHome.setUp (so P2 can
    send to P1); grant_xproj(P1) is needed for P1→P2 direction.
    """

    def setUp(self):
        super().setUp()  # P2 is now archived at this point

        # Unarchive P2 temporarily so we can send messages involving P2 addresses.
        # (Real messages need active state for send() to proceed.)
        self.con.execute(
            "UPDATE project SET state='active', archived_at=NULL WHERE project_id=?",
            (self.P2,))
        self.con.commit()

        # Grant P1 cross-project so P1→P2 direction works.
        s.grant_xproj(self.con, self.P1, self.ADMIN)

        store_p1 = s.store_dir(self.P1)
        store_p2 = s.store_dir(self.P2)

        # --- P2-internal messages ---

        # Simple internal: ML_P2 → T1_P2
        self.msg_int1 = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.T1_P2], subject="internal-1",
        )

        # Both P2 addresses as recipients: ML_P2 → [ML_P2, T1_P2]
        # send() drops the sender from recipients, so effectively → [T1_P2]
        # but this is still a purely P2-internal message.
        self.msg_int2 = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.T1_P2], cc=[self.ML_P2],
            subject="internal-both-p2-recipients",
        )
        # Mark ML_P2 as recipient via raw insert (the send() dropped it as sender).
        # We want both P2 address rows to be message_recipient rows on this message.
        self.con.execute(
            "INSERT OR IGNORE INTO message_recipient (message_id, recipient, role) "
            "VALUES (?, ?, 'to')",
            (self.msg_int2, self.ML_P2))
        self.con.commit()

        # Reply chain: reply to msg_int1 from T1_P2
        self.msg_reply = s.reply(
            self.con, store_p2, self.msg_int1, self.T1_P2,
            subject="Re: internal-1",
        )

        # P2-internal with a body file
        self.msg_int_body = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.T1_P2], subject="internal-with-body",
            body_text="P2 internal body content",
        )
        # Capture the body path for later file-gone assertion
        row = self.con.execute(
            "SELECT body_path FROM message WHERE id=?", (self.msg_int_body,)
        ).fetchone()
        self.int_body_path = row["body_path"]

        # --- Cross-project messages ---

        # P1→P2 (granted)
        self.msg_p1_to_p2 = s.send(
            self.con, store_p1, self.ML_P1,
            to=[self.ML_P2], subject="cross-p1-to-p2",
        )

        # P2→P1 with a body file (body lives in P2's folder — will be deleted)
        self.msg_p2_to_p1 = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.ML_P1], subject="cross-p2-to-p1",
            body_text="P2→P1 cross-project body",
        )
        row2 = self.con.execute(
            "SELECT body_path FROM message WHERE id=?", (self.msg_p2_to_p1,)
        ).fetchone()
        self.cross_body_path = row2["body_path"]

        # Mixed: P2→[T1_P2, ML_P1] — has a cross-project recipient → classified CROSS
        self.msg_mixed = s.send(
            self.con, store_p2, self.ML_P2,
            to=[self.T1_P2, self.ML_P1],
            subject="mixed-p2-internal-and-p1",
        )

        # --- P1-internal message (must be completely untouched) ---
        self.msg_p1_internal = s.send(
            self.con, store_p1, self.ML_P1,
            to=[self.T1_P1], subject="p1-internal-safe",
        )

        # Re-archive P2 (back to the required tombstone_project precondition)
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

        # Counts before tombstone for P1 cross-checks
        self.p1_address_count_before = self._address_count(self.P1)
        self.p1_message_count_before = self.con.execute(
            "SELECT COUNT(*) FROM message WHERE from_addr LIKE '% - P1' "
            "OR id IN (SELECT message_id FROM message_recipient WHERE recipient LIKE '% - P1')"
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# T12 — AC4: internal message rows GONE after tombstone
# ---------------------------------------------------------------------------

class TombstonePurgeInternalMessagesTest(_PurgeFixture):
    """After tombstone_project(P2), all P2-internal message rows and their
    message_recipient rows must be GONE.

    DRIFT-2 ordering — point 1 + 2.
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def _run_tombstone(self):
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

    def test_internal_message_1_row_gone(self):
        """msg_int1 row must be gone from the message table after tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_int1,)
        ).fetchone()
        self.assertIsNone(
            row, f"P2-internal message {self.msg_int1} must be purged"
        )

    def test_internal_message_1_recipients_gone(self):
        """message_recipient rows for msg_int1 must be gone after tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._recipient_count_for_message(self.msg_int1)
        self.assertEqual(
            count, 0,
            f"message_recipient rows for internal msg {self.msg_int1} must be purged; "
            f"got {count}",
        )

    def test_internal_message_2_row_gone(self):
        """msg_int2 (both-P2-recipients) must be purged.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_int2,)
        ).fetchone()
        self.assertIsNone(
            row, f"P2-internal message {self.msg_int2} (both-P2-addr) must be purged"
        )

    def test_internal_message_2_recipients_gone(self):
        """message_recipient rows for msg_int2 (including the manually-inserted
        ML_P2 row) must all be gone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._recipient_count_for_message(self.msg_int2)
        self.assertEqual(
            count, 0,
            f"All message_recipient rows for internal msg {self.msg_int2} must be purged; "
            f"got {count}",
        )

    def test_reply_chain_message_gone(self):
        """msg_reply (P2-internal reply) must be purged.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_reply,)
        ).fetchone()
        self.assertIsNone(
            row, f"P2-internal reply message {self.msg_reply} must be purged"
        )

    def test_internal_body_message_row_gone(self):
        """msg_int_body (P2-internal with body) must be purged.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_int_body,)
        ).fetchone()
        self.assertIsNone(
            row, f"P2-internal body message {self.msg_int_body} must be purged"
        )

    def test_internal_body_file_gone(self):
        """The body file for the P2-internal message must be gone from disk.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        body_path = self.int_body_path
        self.assertIsNotNone(body_path, "msg_int_body must have a body_path")
        self.assertTrue(
            os.path.isfile(body_path),
            f"body file must exist before tombstone: {body_path}",
        )
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertFalse(
            os.path.isfile(body_path),
            f"body file must be gone after tombstone (folder deleted): {body_path}",
        )


# ---------------------------------------------------------------------------
# T13 — AC4: cross-project message rows PRESENT after tombstone
# ---------------------------------------------------------------------------

class TombstonePurgeCrossProjectMessagesTest(_PurgeFixture):
    """After tombstone_project(P2), cross-project message rows AND their
    message_recipient rows (including P2-address recipient rows on surviving
    messages) must SURVIVE.

    DRIFT-2 ordering — point 2 (audit + thread anchoring, PRD D6).
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def _run_tombstone(self):
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

    def test_p1_to_p2_message_survives(self):
        """P1→P2 cross-project message row must survive tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_p1_to_p2,)
        ).fetchone()
        self.assertIsNotNone(
            row, f"P1→P2 cross-project message {self.msg_p1_to_p2} must survive tombstone"
        )

    def test_p1_to_p2_recipient_rows_survive(self):
        """message_recipient rows for the P1→P2 message must survive.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._recipient_count_for_message(self.msg_p1_to_p2)
        self.assertGreater(
            count, 0,
            f"message_recipient rows for P1→P2 message {self.msg_p1_to_p2} must survive; "
            f"got {count}",
        )

    def test_p2_to_p1_message_survives(self):
        """P2→P1 cross-project message row must survive tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_p2_to_p1,)
        ).fetchone()
        self.assertIsNotNone(
            row, f"P2→P1 cross-project message {self.msg_p2_to_p1} must survive tombstone"
        )

    def test_p2_to_p1_recipient_rows_survive(self):
        """message_recipient rows for P2→P1 message must survive (P2 sent, P1 received).

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._recipient_count_for_message(self.msg_p2_to_p1)
        self.assertGreater(
            count, 0,
            f"message_recipient rows for P2→P1 message {self.msg_p2_to_p1} must survive; "
            f"got {count}",
        )

    def test_p2_to_p1_body_row_survives_but_file_gone(self):
        """The P2→P1 message row survives (audit) but its body FILE is gone
        (the P2 folder was deleted). Row body_path still present; file absent.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        cross_body_path = self.cross_body_path
        self.assertIsNotNone(cross_body_path, "P2→P1 message must have a body_path")
        self.assertTrue(
            os.path.isfile(cross_body_path),
            f"cross-project body file must exist before tombstone: {cross_body_path}",
        )
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        # Row survives
        row = self.con.execute(
            "SELECT body_path FROM message WHERE id=?", (self.msg_p2_to_p1,)
        ).fetchone()
        self.assertIsNotNone(
            row, "P2→P1 message row must survive tombstone",
        )
        # File gone (the P2 folder was deleted in its entirety)
        self.assertFalse(
            os.path.isfile(cross_body_path),
            f"body FILE must be gone (P2 folder deleted) even though the row survives: "
            f"{cross_body_path}",
        )

    def test_mixed_message_survives(self):
        """msg_mixed (P2→[T1_P2, ML_P1]) is CROSS → message row survives.

        The presence of ML_P1 as a recipient makes it cross-project; it must not
        be purged even though it also has a P2-address recipient row.
        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_mixed,)
        ).fetchone()
        self.assertIsNotNone(
            row, f"Mixed (cross-project) message {self.msg_mixed} must survive tombstone"
        )

    def test_mixed_message_recipient_rows_survive(self):
        """message_recipient rows for msg_mixed (both T1_P2 and ML_P1 rows) survive.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._recipient_count_for_message(self.msg_mixed)
        self.assertGreater(
            count, 0,
            f"message_recipient rows for mixed message {self.msg_mixed} must survive; "
            f"got {count}",
        )

    def test_p2_address_recipient_row_on_p1_to_p2_survives(self):
        """The P2-address recipient row on the P1→P2 surviving message must survive
        (audit — PRD D6: cross-project recipient rows on surviving messages kept).

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        # The P1→P2 message has ML_P2 as a recipient; that row must still exist.
        row = self.con.execute(
            "SELECT * FROM message_recipient WHERE message_id=? AND recipient=?",
            (self.msg_p1_to_p2, self.ML_P2)
        ).fetchone()
        self.assertIsNotNone(
            row,
            f"P2-address recipient row on surviving P1→P2 message must remain "
            f"(audit); message_id={self.msg_p1_to_p2}, recipient={self.ML_P2!r}",
        )


# ---------------------------------------------------------------------------
# T14 — AC4: P2 address + notifier rows GONE
# ---------------------------------------------------------------------------

class TombstonePurgeAddressNotifierRowsTest(_PurgeFixture):
    """After tombstone_project(P2):
      - P2's address rows must be gone (DRIFT-2 step 3)
      - P2's notifier rows must be gone
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def _run_tombstone(self):
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

    def test_p2_address_rows_gone(self):
        """All address rows for project P2 must be gone after tombstone.

        RED: callable pre-check → AttributeError.
        """
        count_before = self._address_count(self.P2)
        self.assertGreater(count_before, 0, "P2 must have address rows before tombstone")
        self._run_tombstone()
        count_after = self._address_count(self.P2)
        self.assertEqual(
            count_after, 0,
            f"P2 address rows must be gone after tombstone; got {count_after}",
        )

    def test_p2_ml_address_gone(self):
        """The 'Mainline - P2' address row must be gone specifically.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT address FROM address WHERE address=?", (self.ML_P2,)
        ).fetchone()
        self.assertIsNone(
            row, f"'{self.ML_P2}' address row must be purged after tombstone"
        )

    def test_p2_track_address_gone(self):
        """The 'Track 1 - P2' address row must be gone specifically.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT address FROM address WHERE address=?", (self.T1_P2,)
        ).fetchone()
        self.assertIsNone(
            row, f"'{self.T1_P2}' address row must be purged after tombstone"
        )

    def test_p2_notifier_rows_gone(self):
        """Any notifier rows for P2 addresses must be gone after tombstone.

        setUp has no live notifiers, so this tests the sweep handles zero rows
        gracefully and the post-tombstone count is exactly 0.
        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        count = self._notifier_count(self.P2)
        self.assertEqual(
            count, 0,
            f"P2 notifier rows must be gone after tombstone; got {count}",
        )


# ---------------------------------------------------------------------------
# T15 — AC4: projects/P2/ folder entirely deleted from disk
# ---------------------------------------------------------------------------

class TombstonePurgeFolderDeleteTest(_PurgeFixture):
    """After tombstone_project(P2), the projects/P2/ directory must be entirely
    gone from disk.

    AC4: `projects/P2/` (incl. all bodies) gone from disk.
    RED: AttributeError — tombstone_project not yet implemented.
    """

    def test_p2_folder_exists_before_tombstone(self):
        """Pre-condition: P2's folder must exist before tombstone.

        This is a fixture-validity check, not a RED/GREEN gate.
        """
        self.assertTrue(
            self._p2_folder_exists(),
            f"P2 folder must exist before tombstone: {s.store_dir(self.P2)}",
        )

    def test_p2_folder_gone_after_tombstone(self):
        """projects/P2/ must be entirely absent from disk after tombstone_project.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertFalse(
            self._p2_folder_exists(),
            f"projects/P2/ must be gone after tombstone; "
            f"still exists at {s.store_dir(self.P2)}",
        )

    def test_p1_folder_untouched_after_tombstone(self):
        """projects/P1/ must still exist after tombstoning P2.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        p1_folder = s.store_dir(self.P1)
        self.assertTrue(
            os.path.isdir(p1_folder),
            f"P1 folder must exist before tombstone: {p1_folder}",
        )
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)
        self.assertTrue(
            os.path.isdir(p1_folder),
            f"projects/P1/ must still exist after tombstoning P2: {p1_folder}",
        )


# ---------------------------------------------------------------------------
# T16 — AC4: tracker row remains; P1 data completely untouched
# ---------------------------------------------------------------------------

class TombstoneTrackerRowAndP1IntactTest(_PurgeFixture):
    """After tombstone_project(P2):
      - The project tracker row for P2 must remain (state='tombstoned').
      - P1's addresses, messages, and body counts must be exactly unchanged.

    RED: AttributeError — tombstone_project not yet implemented.
    """

    def _run_tombstone(self):
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

    def test_tracker_row_remains(self):
        """The project tracker row for P2 must still exist after tombstone.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self._project_row(self.P2)
        self.assertIsNotNone(
            row, "Tracker row for P2 must remain after tombstone"
        )

    def test_tracker_row_state_tombstoned(self):
        """Tracker row state must be 'tombstoned'.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        self.assertEqual(
            self._raw_project_state(self.P2), "tombstoned",
            "Tracker state must be 'tombstoned'",
        )

    def test_p1_address_count_unchanged(self):
        """P1's address count must be exactly the same after tombstoning P2.

        RED: callable pre-check → AttributeError.
        """
        count_before = self._address_count(self.P1)
        self._run_tombstone()
        count_after = self._address_count(self.P1)
        self.assertEqual(
            count_after, count_before,
            f"P1 address count must be unchanged; before={count_before}, after={count_after}",
        )

    def test_p1_ml_address_still_present(self):
        """'Mainline - P1' must still exist in the address table after tombstoning P2.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT address FROM address WHERE address=?", (self.ML_P1,)
        ).fetchone()
        self.assertIsNotNone(
            row, "'Mainline - P1' must still be in address table after tombstoning P2"
        )

    def test_p1_internal_message_untouched(self):
        """The P1-internal message must still exist after tombstoning P2.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_p1_internal,)
        ).fetchone()
        self.assertIsNotNone(
            row,
            f"P1-internal message {self.msg_p1_internal} must not be touched "
            f"when tombstoning P2",
        )

    def test_p1_folder_intact(self):
        """P1's folder must still exist and be a directory after tombstoning P2.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        self.assertTrue(
            os.path.isdir(s.store_dir(self.P1)),
            "P1's project folder must be intact after tombstoning P2",
        )


# ---------------------------------------------------------------------------
# T17 — multi-recipient edge: purely-internal vs mixed classification
# ---------------------------------------------------------------------------

class TombstoneMixedRecipientsClassificationTest(_PurgeFixture):
    """Specific multi-recipient edge cases:
      1. P2-internal message with BOTH P2 addresses as recipients → purged
      2. P2→[T1_P2, ML_P1] mixed message → classified CROSS → survives

    Both asserted together to confirm the classifier correctly distinguishes
    "has at least one non-P2 recipient" from "all recipients within P2".

    RED: AttributeError — tombstone_project not yet implemented.
    """

    def _run_tombstone(self):
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

    def test_both_p2_recipients_message_purged(self):
        """msg_int2 (recipients: both P2 addresses) must be classified internal → purged.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_int2,)
        ).fetchone()
        self.assertIsNone(
            row,
            f"msg_int2 (both-P2-addr recipients) must be purged as internal; "
            f"id={self.msg_int2}",
        )

    def test_mixed_message_survives_because_cross(self):
        """msg_mixed (P2→[T1_P2, ML_P1]) must survive because it has a P1 recipient.

        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        row = self.con.execute(
            "SELECT id FROM message WHERE id=?", (self.msg_mixed,)
        ).fetchone()
        self.assertIsNotNone(
            row,
            f"msg_mixed must survive (cross-project due to ML_P1 recipient); "
            f"id={self.msg_mixed}",
        )

    def test_both_p2_and_p1_recipient_counts_consistent(self):
        """After tombstone: msg_int2 recipient count == 0; msg_mixed recipient count > 0.

        This asserts the classifier correctly purges vs. preserves on a per-message basis.
        RED: callable pre-check → AttributeError.
        """
        self._run_tombstone()
        int2_count = self._recipient_count_for_message(self.msg_int2)
        mixed_count = self._recipient_count_for_message(self.msg_mixed)
        self.assertEqual(
            int2_count, 0,
            f"Internal msg_int2 must have 0 recipient rows; got {int2_count}",
        )
        self.assertGreater(
            mixed_count, 0,
            f"Cross msg_mixed must have >0 recipient rows; got {mixed_count}",
        )


# ---------------------------------------------------------------------------
# T18 — DRIFT-2 ordering: address rows must be DELETED AFTER message purge
#        (tests that purge order is correct by verifying outcomes separately)
# ---------------------------------------------------------------------------

class TombstonePurgeOrderingTest(_PurgeFixture):
    """Verify the DRIFT-2 ordering contract: internal message set computed
    while address rows still exist, messages+recipients deleted first,
    then address+notifier rows deleted.

    We can only observe the OUTCOMES (not the intermediate steps), so this
    test asserts:
      a) all P2-internal message rows are gone (computed correctly)
      b) all P2 address rows are gone (step 3 completed)
      c) cross-project rows survive (no over-purge from mis-ordering)

    RED: AttributeError — tombstone_project not yet implemented.
    """

    def test_ordering_outcomes_correct(self):
        """All DRIFT-2 ordering outcomes hold simultaneously after one tombstone call.

        RED: callable pre-check → AttributeError.
        """
        self._assert_tombstone_project_exists()
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

        # (a) P2-internal messages gone
        for mid in [self.msg_int1, self.msg_int2, self.msg_reply, self.msg_int_body]:
            row = self.con.execute(
                "SELECT id FROM message WHERE id=?", (mid,)
            ).fetchone()
            self.assertIsNone(
                row, f"P2-internal message {mid} must be purged (DRIFT-2 step 1)"
            )

        # (b) P2 address rows gone
        count = self._address_count(self.P2)
        self.assertEqual(
            count, 0,
            f"P2 address rows must be gone (DRIFT-2 step 3); got {count}",
        )

        # (c) Cross-project rows survive
        for mid in [self.msg_p1_to_p2, self.msg_p2_to_p1, self.msg_mixed]:
            row = self.con.execute(
                "SELECT id FROM message WHERE id=?", (mid,)
            ).fetchone()
            self.assertIsNotNone(
                row, f"Cross-project message {mid} must survive (DRIFT-2 step 2)"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
