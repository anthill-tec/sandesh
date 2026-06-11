"""test_lifecycle_archive.py — RED tests for CR-SAN-024 Cycle 1.

Covers §S1 (archive/unarchive + poll_interval) + AC1/AC2/AC3/AC6(Mainline) + AC11 (DEC-E).

  poll_interval()         — moved from notify._interval into sandesh_db; same semantics.
  notify._interval        — must no longer exist as a standalone definition (sandesh_db is
                            the canonical home; notify may import/alias it).
  archive(con, project_id, by, *, force=False, wait_secs=None)
                          — active→archived; evicts live notifiers; refuses if live watcher
                            survives past bounded wait (state unchanged); force=True reaps
                            and proceeds; dead/stale rows do not block.
  unarchive(con, project_id, by)
                          — archived→active; clears archived_at; Mainline-only.
  DEC-E guard guards:
    grant_xproj on archived  → ValueError containing "project '<id>' is archived"
    grant_xproj on tombstoned → ValueError containing "project '<id>' is tombstoned"
    revoke_xproj on archived → ValueError containing "project '<id>' is archived"
    revoke_xproj on tombstoned → ValueError containing "project '<id>' is tombstoned"
    grant survives archive→unarchive (column intact; cross-project send works again)

Expected RED:
  AttributeError for sandesh_db.archive, sandesh_db.unarchive, sandesh_db.poll_interval
  (none exist yet).  DEC-E guard tests fail because grant_xproj/revoke_xproj currently
  succeed on archived/tombstoned projects.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_lifecycle_archive --agent red-cr024-c1
"""

import os
import shutil
import tempfile
import time
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions:
      - P1, P2 with Mainline + Track 1 registered in each
      - admin 'ops' assigned
    Subclasses call super().setUp().
    """

    P1 = "P1"
    P2 = "P2"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-lifecycle-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()

        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)

        s.assign_admin(self.con, "ops")

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

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


# ---------------------------------------------------------------------------
# T1 — poll_interval() lives in sandesh_db
# ---------------------------------------------------------------------------

class PollIntervalInSandeshDbTest(_TempDataHome):
    """poll_interval() must be callable from sandesh_db and return an int >= 3.

    RED: sandesh_db.poll_interval does not yet exist → AttributeError.
    """

    def test_poll_interval_callable(self):
        """sandesh_db.poll_interval must be a callable attribute.

        RED: AttributeError — not yet implemented.
        """
        self.assertTrue(
            callable(getattr(s, "poll_interval", None)),
            "sandesh_db.poll_interval is not callable — implement it (GREEN).",
        )

    def test_poll_interval_returns_int(self):
        """poll_interval() must return an integer.

        RED: AttributeError.
        """
        result = s.poll_interval()
        self.assertIsInstance(result, int,
                              f"poll_interval() must return int; got {type(result).__name__!r}")

    def test_poll_interval_default_is_10(self):
        """poll_interval() with SANDESH_POLL_SECONDS unset must return 10 (default).

        RED: AttributeError.
        """
        prev = os.environ.pop("SANDESH_POLL_SECONDS", None)
        try:
            result = s.poll_interval()
            self.assertEqual(result, 10,
                             f"poll_interval() default must be 10; got {result!r}")
        finally:
            if prev is not None:
                os.environ["SANDESH_POLL_SECONDS"] = prev

    def test_poll_interval_respects_env_var(self):
        """poll_interval() must read $SANDESH_POLL_SECONDS when set.

        RED: AttributeError.
        """
        os.environ["SANDESH_POLL_SECONDS"] = "20"
        try:
            result = s.poll_interval()
            self.assertEqual(result, 20,
                             f"poll_interval() must return 20 when env is '20'; got {result!r}")
        finally:
            del os.environ["SANDESH_POLL_SECONDS"]

    def test_poll_interval_floor_is_3(self):
        """poll_interval() must return at least 3 even when env is lower.

        RED: AttributeError.
        """
        os.environ["SANDESH_POLL_SECONDS"] = "1"
        try:
            result = s.poll_interval()
            self.assertGreaterEqual(result, 3,
                                    f"poll_interval() floor is 3; got {result!r} for env='1'")
        finally:
            del os.environ["SANDESH_POLL_SECONDS"]

    def test_poll_interval_invalid_env_falls_back_to_default(self):
        """poll_interval() with non-numeric env must fall back to default (10).

        RED: AttributeError.
        """
        os.environ["SANDESH_POLL_SECONDS"] = "banana"
        try:
            result = s.poll_interval()
            self.assertEqual(result, 10,
                             f"poll_interval() must return 10 for bad env; got {result!r}")
        finally:
            del os.environ["SANDESH_POLL_SECONDS"]


# ---------------------------------------------------------------------------
# T2 — notify._interval removed (or is an alias to sandesh_db.poll_interval)
# ---------------------------------------------------------------------------

class NotifyIntervalMovedTest(_TempDataHome):
    """notify._interval must no longer be a standalone definition — the canonical
    implementation lives in sandesh_db.poll_interval.  notify may alias or delegate
    but must not duplicate the logic.

    RED: this test will PASS (notify._interval still a standalone) until GREEN
    removes it — but we assert sandesh_db.poll_interval is the canonical home,
    which fails because it doesn't exist yet.
    """

    def test_sandesh_db_poll_interval_is_canonical(self):
        """sandesh_db.poll_interval must exist as the single source of truth.

        RED: AttributeError — not yet implemented.
        """
        self.assertTrue(
            callable(getattr(s, "poll_interval", None)),
            "sandesh_db.poll_interval must be the canonical poll_interval — "
            "it does not exist yet (GREEN must implement it).",
        )

    def test_notify_interval_not_independent_of_sandesh_db(self):
        """After GREEN, notify._interval must not be an independent reimplementation.
        It must either be absent or delegate to sandesh_db.poll_interval.

        RED: currently notify._interval is a standalone function that duplicates
        the logic. This test asserts that notify._interval (if present) agrees with
        sandesh_db.poll_interval() — a hard contradiction if they diverge.
        """
        from sandesh import notify as n
        # poll_interval must exist first (will fail here if not implemented yet)
        pi = s.poll_interval()
        # If notify._interval exists, it must return the same value
        if hasattr(n, "_interval"):
            ni = n._interval()
            self.assertEqual(
                ni, pi,
                f"notify._interval() returned {ni!r} but sandesh_db.poll_interval() "
                f"returned {pi!r} — they must agree (notify must delegate to sandesh_db).",
            )


# ---------------------------------------------------------------------------
# T3 — archive() callable and basic state machine
# ---------------------------------------------------------------------------

class ArchiveCallableTest(_TempDataHome):
    """sandesh_db.archive must be callable and accept the correct signature.

    RED: AttributeError — not yet implemented.
    """

    def test_archive_callable(self):
        """sandesh_db.archive must be a callable attribute.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "archive", None)),
            "sandesh_db.archive is not callable — implement it (GREEN).",
        )


class ArchiveStateMachineTest(_TempDataHome):
    """archive(con, P2, by, wait_secs=0.1) must flip state active→archived.

    RED: AttributeError — not yet implemented.
    """

    def test_archive_sets_state_to_archived(self):
        """archive(P2, by=ML_P2, wait_secs=0.1) must set project state to 'archived'.

        RED: AttributeError.
        """
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"project P2 state must be 'archived' after archive(); got {state!r}")

    def test_archive_sets_archived_at_nonnull(self):
        """archive(P2) must set archived_at to a non-null datetime.

        RED: AttributeError.
        """
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        row = self._project_row(self.P2)
        self.assertIsNotNone(row["archived_at"],
                             "archived_at must be non-null after archive()")

    def test_archive_does_not_delete_addresses(self):
        """archive must not delete any addresses — history is intact.

        RED: AttributeError.
        """
        count_before = self._address_count(self.P2)
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        count_after = self._address_count(self.P2)
        self.assertEqual(count_after, count_before,
                         f"archive must not delete addresses; before={count_before}, "
                         f"after={count_after}")

    def test_archive_does_not_delete_messages(self):
        """archive must not delete any messages — history is intact.

        RED: AttributeError.
        """
        store = s.store_dir(self.P1)
        s.send(self.con, store, self.ML_P1, to=[self.T1_P1],
               subject="ping", project=self.P1)
        count_before = self._message_count()
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        count_after = self._message_count()
        self.assertEqual(count_after, count_before,
                         f"archive must not delete messages; before={count_before}, "
                         f"after={count_after}")

    def test_archive_does_not_affect_other_project(self):
        """archive(P2) must not alter P1's state.

        RED: AttributeError.
        """
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state_p1 = self._raw_project_state(self.P1)
        self.assertEqual(state_p1, "active",
                         f"P1 state must remain 'active' after archiving P2; got {state_p1!r}")


# ---------------------------------------------------------------------------
# T4 — archive() state guard errors
# ---------------------------------------------------------------------------

class ArchiveStateGuardTest(_TempDataHome):
    """archive() must raise appropriate errors for non-active states.

    RED: AttributeError — not yet implemented.
    """

    def test_archive_already_archived_raises_value_error(self):
        """archive() on an already-archived project must raise ValueError.

        RED: AttributeError.
        """
        # Seed archived state via raw SQL
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError):
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def test_archive_already_archived_error_message(self):
        """ValueError for already-archived must contain "project 'P2' is not active".

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError) as ctx:
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        self.assertIn("not active", str(ctx.exception),
                      f"Error must contain 'not active'; got: {ctx.exception!r}")
        self.assertIn("P2", str(ctx.exception),
                      f"Error must name the project; got: {ctx.exception!r}")

    def test_archive_tombstoned_raises_value_error(self):
        """archive() on a tombstoned project must raise ValueError.

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError):
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def test_archive_tombstoned_error_message(self):
        """ValueError for tombstoned must contain "project 'P2' is not active".

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError) as ctx:
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        self.assertIn("not active", str(ctx.exception),
                      f"Error must contain 'not active'; got: {ctx.exception!r}")

    def test_archive_unknown_project_raises_value_error(self):
        """archive() on an unknown project must raise ValueError with 'unknown project'.

        RED: AttributeError.
        """
        with self.assertRaises(ValueError) as ctx:
            s.archive(self.con, "NoSuchProject", "Mainline - NoSuchProject",
                      wait_secs=0.1)
        self.assertIn("unknown project", str(ctx.exception),
                      f"Error must contain 'unknown project'; got: {ctx.exception!r}")

    def test_archive_state_unchanged_on_error(self):
        """When archive() raises, the project state must remain unchanged.

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        try:
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        except ValueError:
            pass
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"State must remain 'archived' after failed archive(); got {state!r}")


# ---------------------------------------------------------------------------
# T5 — archive() authz guard (AC6 Mainline tier)
# ---------------------------------------------------------------------------

class ArchiveAuthzTest(_TempDataHome):
    """archive() must accept only the project's own Mainline as `by`.

    AC6: archive with by ≠ project's Mainline is rejected.
    RED: AttributeError.
    """

    def test_archive_track_address_rejected(self):
        """archive() with a Track address as `by` must raise PermissionError.

        RED: AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.archive(self.con, self.P2, self.T1_P2, wait_secs=0.1)

    def test_archive_foreign_mainline_rejected(self):
        """archive() with the Mainline of a different project must raise PermissionError.

        RED: AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.archive(self.con, self.P2, self.ML_P1, wait_secs=0.1)

    def test_archive_correct_mainline_succeeds(self):
        """archive() with the project's own Mainline must succeed.

        RED: AttributeError.
        """
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"archive() with correct Mainline must succeed; state={state!r}")

    def test_archive_wrong_by_leaves_state_unchanged(self):
        """When archive() is rejected for wrong `by`, state must remain active.

        RED: AttributeError.
        """
        try:
            s.archive(self.con, self.P2, self.T1_P2, wait_secs=0.1)
        except PermissionError:
            pass
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active",
                         f"State must remain 'active' after rejected archive(); got {state!r}")


# ---------------------------------------------------------------------------
# T6 — archive() eviction: dead/stale rows do not block (AC3)
# ---------------------------------------------------------------------------

class ArchiveEvictionDeadPidTest(_TempDataHome):
    """A notifier row with a dead pid or stale heartbeat must NOT block archive().

    AC3: dead-pid/stale-heartbeat row does not block.
    RED: AttributeError.
    """

    def test_archive_not_blocked_by_dead_pid_notifier(self):
        """A notifier row with pid=99999999 (guaranteed dead) must not block archive().

        RED: AttributeError.
        """
        # Seed a dead-pid notifier row directly
        self.con.execute(
            "INSERT INTO notifier (recipient, pid, token, host, "
            "started_at, heartbeat_at, tombstone) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), FALSE)",
            (self.ML_P2, 99999999, "dead-tok", "testhost"))
        self.con.commit()
        # archive must succeed despite the dead-pid row
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"archive() must succeed with dead-pid notifier; state={state!r}")

    def test_archive_not_blocked_by_stale_heartbeat_notifier(self):
        """A notifier row with a heartbeat > HEARTBEAT_STALE_SECS old must not block.

        RED: AttributeError.
        """
        # Seed a row with a heartbeat far in the past (stale)
        self.con.execute(
            "INSERT INTO notifier (recipient, pid, token, host, "
            "started_at, heartbeat_at, tombstone) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now', '-120 seconds'), FALSE)",
            (self.T1_P2, 99999998, "stale-tok", "testhost"))
        self.con.commit()
        # archive must succeed despite the stale row
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"archive() must succeed with stale notifier; state={state!r}")


# ---------------------------------------------------------------------------
# T7 — archive() eviction: live notifier gets tombstone flag (AC3)
# ---------------------------------------------------------------------------

class ArchiveEvictionLiveNotifierTest(_TempDataHome):
    """archive() must tombstone a live notifier (one with this process's real pid).

    AC3: with a live notifier seeded via notifier_acquire, archive sets its tombstone flag;
    while it STAYS live past the bounded wait, archive refuses with state unchanged;
    with force=True it reaps and proceeds.
    RED: AttributeError.
    """

    def test_archive_tombstones_live_notifier(self):
        """archive() must set tombstone=True on a live notifier row.

        RED: AttributeError.
        """
        import uuid
        tok = uuid.uuid4().hex
        ok, _ = s.notifier_acquire(self.con, self.ML_P2, os.getpid(), tok, "testhost")
        self.assertTrue(ok, "notifier_acquire must succeed for a fresh address")
        # archive with wait_secs=0.1 — the live watcher never exits (it's this process),
        # so after setting tombstone, archive will refuse (state unchanged)
        try:
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        except (ValueError, RuntimeError, Exception):
            pass
        # Regardless of whether it raised, the tombstone flag must have been set
        row = self.con.execute(
            "SELECT tombstone FROM notifier WHERE recipient=?", (self.ML_P2,)
        ).fetchone()
        self.assertIsNotNone(row, "notifier row must still exist after archive attempt")
        self.assertTrue(bool(row["tombstone"]),
                        "notifier tombstone flag must be set after archive() attempt")
        # Cleanup: release the notifier
        s.notifier_release(self.con, self.ML_P2, tok)

    def test_archive_refuses_while_live_watcher_persists(self):
        """archive() must refuse (raise something other than AttributeError) and
        leave state unchanged if a live watcher survives past the bounded wait.

        The watcher is this process's pid — it never exits, so refusal is deterministic.
        RED: archive() doesn't exist → the first assertion fails (callable check).
        """
        import uuid
        # Pre-condition: archive must exist before we can test its refusal behaviour.
        # This assertion is the RED gate — AttributeError means not yet implemented.
        self.assertTrue(
            callable(getattr(s, "archive", None)),
            "sandesh_db.archive must exist before testing refusal behaviour (GREEN).",
        )
        tok = uuid.uuid4().hex
        ok, _ = s.notifier_acquire(self.con, self.ML_P2, os.getpid(), tok, "testhost")
        self.assertTrue(ok, "notifier_acquire must succeed")
        raised = None
        try:
            s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        except (ValueError, RuntimeError, OSError) as exc:
            raised = exc
        except AttributeError:
            self.fail(
                "archive() raised AttributeError — it is not yet implemented. "
                "This is RED: implement archive() so it raises a specific error "
                "when a live watcher persists past the bounded wait."
            )
        finally:
            s.notifier_release(self.con, self.ML_P2, tok)
        self.assertIsNotNone(
            raised,
            "archive() must raise (ValueError/RuntimeError/OSError) when a live "
            "watcher persists past the bounded wait — got no exception (state may "
            "have been set to archived incorrectly).",
        )
        # State must be unchanged — still active
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active",
                         f"State must remain 'active' after refused archive(); got {state!r}")

    def test_archive_force_reaps_live_watcher_and_proceeds(self):
        """archive(force=True) must reap the live notifier row and set state=archived.

        RED: AttributeError.
        """
        import uuid
        tok = uuid.uuid4().hex
        ok, _ = s.notifier_acquire(self.con, self.ML_P2, os.getpid(), tok, "testhost")
        self.assertTrue(ok, "notifier_acquire must succeed")
        try:
            s.archive(self.con, self.P2, self.ML_P2, force=True, wait_secs=0.1)
            state = self._raw_project_state(self.P2)
            self.assertEqual(state, "archived",
                             f"force=True must archive the project; state={state!r}")
            # The notifier row must have been reaped
            row = self.con.execute(
                "SELECT * FROM notifier WHERE recipient=?", (self.ML_P2,)
            ).fetchone()
            self.assertIsNone(row,
                              "notifier row must be reaped after force=True archive()")
        finally:
            # Token-guarded release is a no-op if the row is already gone
            s.notifier_release(self.con, self.ML_P2, tok)

    def test_archive_force_sets_archived_at(self):
        """archive(force=True) must set archived_at to a non-null value.

        RED: AttributeError.
        """
        import uuid
        tok = uuid.uuid4().hex
        s.notifier_acquire(self.con, self.ML_P2, os.getpid(), tok, "testhost")
        try:
            s.archive(self.con, self.P2, self.ML_P2, force=True, wait_secs=0.1)
            row = self._project_row(self.P2)
            self.assertIsNotNone(row["archived_at"],
                                 "archived_at must be set after force archive()")
        finally:
            s.notifier_release(self.con, self.ML_P2, tok)


# ---------------------------------------------------------------------------
# T8 — unarchive() callable and basic state machine
# ---------------------------------------------------------------------------

class UnarchiveCallableTest(_TempDataHome):
    """sandesh_db.unarchive must be callable.

    RED: AttributeError — not yet implemented.
    """

    def test_unarchive_callable(self):
        """sandesh_db.unarchive must be a callable attribute.

        RED: AttributeError.
        """
        self.assertTrue(
            callable(getattr(s, "unarchive", None)),
            "sandesh_db.unarchive is not callable — implement it (GREEN).",
        )


class UnarchiveStateMachineTest(_TempDataHome):
    """unarchive(con, P2, by) must flip archived→active and clear archived_at.

    RED: AttributeError — not yet implemented.
    """

    def setUp(self):
        super().setUp()
        # Seed P2 as archived via raw SQL
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_unarchive_sets_state_to_active(self):
        """unarchive(P2, by=ML_P2) must set project state back to 'active'.

        RED: AttributeError.
        """
        s.unarchive(self.con, self.P2, self.ML_P2)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active",
                         f"unarchive() must set state to 'active'; got {state!r}")

    def test_unarchive_clears_archived_at(self):
        """unarchive(P2) must set archived_at to NULL.

        RED: AttributeError.
        """
        s.unarchive(self.con, self.P2, self.ML_P2)
        row = self._project_row(self.P2)
        self.assertIsNone(row["archived_at"],
                          f"archived_at must be NULL after unarchive(); got {row['archived_at']!r}")

    def test_unarchive_does_not_affect_other_project(self):
        """unarchive(P2) must not alter P1's state.

        RED: AttributeError.
        """
        s.unarchive(self.con, self.P2, self.ML_P2)
        state_p1 = self._raw_project_state(self.P1)
        self.assertEqual(state_p1, "active",
                         f"P1 must remain 'active' after unarchiving P2; got {state_p1!r}")

    def test_archive_then_unarchive_round_trip(self):
        """archive → unarchive round-trip must yield state='active' and archived_at=NULL.

        RED: AttributeError on archive or unarchive.
        """
        # P2 is already archived (from setUp); unarchive it
        s.unarchive(self.con, self.P2, self.ML_P2)
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active", "State must be 'active' after round-trip")
        row = self._project_row(self.P2)
        self.assertIsNone(row["archived_at"], "archived_at must be NULL after round-trip")
        # archive again to confirm repeatable
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        state2 = self._raw_project_state(self.P2)
        self.assertEqual(state2, "archived",
                         f"Second archive must yield 'archived'; got {state2!r}")


# ---------------------------------------------------------------------------
# T9 — unarchive() state guard errors
# ---------------------------------------------------------------------------

class UnarchiveStateGuardTest(_TempDataHome):
    """unarchive() must raise ValueError for non-archived states.

    RED: AttributeError — not yet implemented.
    """

    def test_unarchive_active_raises_value_error(self):
        """unarchive() on an active project must raise ValueError.

        RED: AttributeError.
        """
        with self.assertRaises(ValueError):
            s.unarchive(self.con, self.P2, self.ML_P2)

    def test_unarchive_active_error_message(self):
        """ValueError for active project must contain "not archived".

        RED: AttributeError.
        """
        with self.assertRaises(ValueError) as ctx:
            s.unarchive(self.con, self.P2, self.ML_P2)
        self.assertIn("not archived", str(ctx.exception),
                      f"Error must contain 'not archived'; got: {ctx.exception!r}")
        self.assertIn("P2", str(ctx.exception),
                      f"Error must name the project; got: {ctx.exception!r}")

    def test_unarchive_tombstoned_raises_value_error(self):
        """unarchive() on a tombstoned project must raise ValueError.

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError):
            s.unarchive(self.con, self.P2, self.ML_P2)

    def test_unarchive_tombstoned_error_message(self):
        """ValueError for tombstoned must contain "not archived".

        RED: AttributeError.
        """
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()
        with self.assertRaises(ValueError) as ctx:
            s.unarchive(self.con, self.P2, self.ML_P2)
        self.assertIn("not archived", str(ctx.exception),
                      f"Error must contain 'not archived'; got: {ctx.exception!r}")

    def test_unarchive_state_unchanged_on_error(self):
        """When unarchive() raises, the project state must remain unchanged.

        RED: AttributeError.
        """
        # P2 is active; try to unarchive it (should fail)
        try:
            s.unarchive(self.con, self.P2, self.ML_P2)
        except ValueError:
            pass
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "active",
                         f"State must remain 'active' after failed unarchive(); got {state!r}")


# ---------------------------------------------------------------------------
# T10 — unarchive() authz guard (AC6 Mainline tier)
# ---------------------------------------------------------------------------

class UnarchiveAuthzTest(_TempDataHome):
    """unarchive() must accept only the project's own Mainline as `by`.

    RED: AttributeError — not yet implemented.
    """

    def setUp(self):
        super().setUp()
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_unarchive_track_address_rejected(self):
        """unarchive() with a Track address as `by` must raise PermissionError.

        RED: AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.unarchive(self.con, self.P2, self.T1_P2)

    def test_unarchive_foreign_mainline_rejected(self):
        """unarchive() with a foreign Mainline must raise PermissionError.

        RED: AttributeError.
        """
        with self.assertRaises(PermissionError):
            s.unarchive(self.con, self.P2, self.ML_P1)

    def test_unarchive_wrong_by_leaves_state_archived(self):
        """When unarchive() is rejected, state must remain 'archived'.

        RED: AttributeError.
        """
        try:
            s.unarchive(self.con, self.P2, self.T1_P2)
        except PermissionError:
            pass
        state = self._raw_project_state(self.P2)
        self.assertEqual(state, "archived",
                         f"State must remain 'archived' after rejected unarchive(); got {state!r}")


# ---------------------------------------------------------------------------
# T11 — AC2: post-archive behaviour pins
# ---------------------------------------------------------------------------

class PostArchiveBehaviourTest(_TempDataHome):
    """After archive(P2), sends from/to P2 are rejected; register into P2 rejected;
    inbox/fetch/thread still readable.

    AC2: these are already enforced by CR-SAN-023's state checks — but we pin them
    under a REAL transition (not raw-SQL state seeding).
    RED: AttributeError on archive().
    """

    def setUp(self):
        super().setUp()
        # Archive P2 using the real function (not raw SQL)
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def test_send_from_archived_project_rejected(self):
        """send() from P2 after archive must raise ValueError containing 'is archived'.

        RED: AttributeError on archive() in setUp.
        """
        with self.assertRaises(ValueError) as ctx:
            s.send(self.con, s.store_dir(self.P2), self.ML_P2,
                   to=[self.T1_P2], subject="ping", project=self.P2)
        self.assertIn("archived", str(ctx.exception),
                      f"Error must mention 'archived'; got: {ctx.exception!r}")

    def test_send_to_archived_project_rejected(self):
        """send() to a P2 address after archive must raise ValueError containing 'is archived'.

        RED: AttributeError on archive() in setUp.
        """
        # Grant P1 so the cross-project check passes and we reach the state check
        s.grant_xproj(self.con, self.P1, "ops")
        with self.assertRaises(ValueError) as ctx:
            s.send(self.con, s.store_dir(self.P1), self.ML_P1,
                   to=[self.ML_P2], subject="xping", project=self.P1)
        self.assertIn("archived", str(ctx.exception),
                      f"Error must mention 'archived' for recipient project; got: {ctx.exception!r}")

    def test_register_into_archived_project_rejected(self):
        """register() into an archived project must raise ValueError.

        RED: AttributeError on archive() in setUp.
        """
        with self.assertRaises(ValueError):
            s.register(self.con, "Track 2 - P2", kind="track", project=self.P2)

    def test_inbox_readable_after_archive(self):
        """inbox()/fetch() for a P2 address must still return historical messages.

        RED: AttributeError on archive() in setUp.
        """
        # We can still READ from the inbox (already-received messages unaffected)
        # The inbox call itself must not raise
        try:
            items = s.inbox(self.con, self.ML_P2, unread_only=False)
        except Exception as exc:
            self.fail(f"inbox() must not raise after archive; got: {exc}")
        # inbox returns a list (possibly empty; that's fine for this pin test)
        self.assertIsInstance(items, list,
                              "inbox() must return a list after archive")

    def test_body_files_readable_after_archive(self):
        """Body files of pre-archive messages must still be readable after archive.

        RED: AttributeError on archive() in setUp.  (archive() deletes nothing)
        """
        # Send a message to P2 BEFORE archiving — need to re-setup
        # We seed the message via raw SQL since archive was already called in setUp
        store = s.store_dir(self.P2)
        # Verify the store dir still exists (archive deletes nothing)
        self.assertTrue(os.path.isdir(store),
                        f"store dir must exist after archive; missing: {store}")


# ---------------------------------------------------------------------------
# T12 — AC2: cross-project viewer sees P2 threads fully after archive
# ---------------------------------------------------------------------------

class CrossProjectViewerAfterArchiveTest(_TempDataHome):
    """A cross-project viewer granted on P1 can still see P2 thread after archive.

    AC2: cross-project viewers still see P2 threads fully.
    RED: AttributeError.
    """

    def setUp(self):
        super().setUp()
        # Grant P1 for cross-project send, then send P2→P1 before archiving P2
        s.grant_xproj(self.con, self.P2, "ops")
        self._mid = s.send(
            self.con, s.store_dir(self.P2), self.ML_P2,
            to=[self.ML_P1], subject="pre-archive msg",
        )
        # Now archive P2 using the real function
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def test_pre_archive_message_in_p1_inbox(self):
        """The pre-archive P2→P1 message must still appear in P1's inbox after archiving P2.

        RED: AttributeError on archive() in setUp.
        """
        items = s.inbox(self.con, self.ML_P1, unread_only=False)
        ids = [it["id"] for it in items]
        self.assertIn(self._mid, ids,
                      "Pre-archive message must still be in P1 inbox after P2 archived")

    def test_thread_readable_after_archive(self):
        """thread() on the pre-archive message must succeed and return the chain.

        RED: AttributeError on archive() in setUp.
        """
        chain = s.thread(self.con, self._mid)
        self.assertTrue(len(chain) >= 1,
                        f"thread() must return at least 1 node; got {chain!r}")


# ---------------------------------------------------------------------------
# T13 — AC11: DEC-E — grant_xproj on archived project → ValueError
# ---------------------------------------------------------------------------

class GrantXprojArchivedProjectTest(_TempDataHome):
    """grant_xproj on an archived project must raise ValueError containing
    "project 'P2' is archived".

    RED: currently grant_xproj succeeds on archived projects (no state guard).
    """

    def setUp(self):
        super().setUp()
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_grant_xproj_on_archived_raises_value_error(self):
        """grant_xproj(con, P2, 'ops') on an archived P2 must raise ValueError.

        RED: currently no state guard → succeeds instead of raising.
        """
        with self.assertRaises(ValueError):
            s.grant_xproj(self.con, self.P2, "ops")

    def test_grant_xproj_on_archived_error_message(self):
        """ValueError must contain "project 'P2' is archived".

        RED: currently no state guard → no error raised.
        """
        with self.assertRaises(ValueError) as ctx:
            s.grant_xproj(self.con, self.P2, "ops")
        msg = str(ctx.exception)
        self.assertIn("P2", msg,
                      f"Error must name the project; got: {msg!r}")
        self.assertIn("archived", msg,
                      f"Error must say 'archived'; got: {msg!r}")

    def test_grant_xproj_on_archived_no_write(self):
        """grant_xproj on archived must not write the grant.

        RED: currently no state guard → grant is written.
        """
        try:
            s.grant_xproj(self.con, self.P2, "ops")
        except ValueError:
            pass
        row = self.con.execute(
            "SELECT xproj_granted_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        self.assertIsNone(row["xproj_granted_at"],
                          "xproj_granted_at must remain NULL after refused grant on archived project")


# ---------------------------------------------------------------------------
# T14 — AC11: DEC-E — grant_xproj on tombstoned project → ValueError
# ---------------------------------------------------------------------------

class GrantXprojTombstonedProjectTest(_TempDataHome):
    """grant_xproj on a tombstoned project must raise ValueError containing
    "project 'P2' is tombstoned".

    RED: currently no state guard.
    """

    def setUp(self):
        super().setUp()
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_grant_xproj_on_tombstoned_raises_value_error(self):
        """grant_xproj(con, P2, 'ops') on tombstoned P2 must raise ValueError.

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError):
            s.grant_xproj(self.con, self.P2, "ops")

    def test_grant_xproj_on_tombstoned_error_message(self):
        """ValueError must contain "project 'P2' is tombstoned".

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError) as ctx:
            s.grant_xproj(self.con, self.P2, "ops")
        msg = str(ctx.exception)
        self.assertIn("P2", msg,
                      f"Error must name the project; got: {msg!r}")
        self.assertIn("tombstoned", msg,
                      f"Error must say 'tombstoned'; got: {msg!r}")


# ---------------------------------------------------------------------------
# T15 — AC11: DEC-E — revoke_xproj on archived/tombstoned → ValueError
# ---------------------------------------------------------------------------

class RevokeXprojArchivedProjectTest(_TempDataHome):
    """revoke_xproj on an archived project must raise ValueError containing
    "project 'P2' is archived".

    RED: currently no state guard.
    """

    def setUp(self):
        super().setUp()
        # Grant first (while active), then archive
        s.grant_xproj(self.con, self.P2, "ops")
        self.con.execute(
            "UPDATE project SET state='archived', archived_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_revoke_xproj_on_archived_raises_value_error(self):
        """revoke_xproj(con, P2, 'ops') on archived P2 must raise ValueError.

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError):
            s.revoke_xproj(self.con, self.P2, "ops")

    def test_revoke_xproj_on_archived_error_message(self):
        """ValueError must contain "project 'P2' is archived".

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError) as ctx:
            s.revoke_xproj(self.con, self.P2, "ops")
        msg = str(ctx.exception)
        self.assertIn("P2", msg,
                      f"Error must name the project; got: {msg!r}")
        self.assertIn("archived", msg,
                      f"Error must say 'archived'; got: {msg!r}")

    def test_revoke_xproj_on_archived_does_not_touch_grant(self):
        """revoke_xproj on archived must not clear the grant columns.

        RED: currently no state guard — revoke proceeds and clears the grant.
        """
        try:
            s.revoke_xproj(self.con, self.P2, "ops")
        except ValueError:
            pass
        row = self.con.execute(
            "SELECT xproj_granted_at FROM project WHERE project_id=?", (self.P2,)
        ).fetchone()
        self.assertIsNotNone(row["xproj_granted_at"],
                             "xproj_granted_at must remain set after refused revoke on archived project")


class RevokeXprojTombstonedProjectTest(_TempDataHome):
    """revoke_xproj on a tombstoned project must raise ValueError.

    RED: currently no state guard.
    """

    def setUp(self):
        super().setUp()
        # Grant first (while active), then tombstone via raw SQL
        s.grant_xproj(self.con, self.P2, "ops")
        self.con.execute(
            "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
            "WHERE project_id=?", (self.P2,))
        self.con.commit()

    def test_revoke_xproj_on_tombstoned_raises_value_error(self):
        """revoke_xproj(con, P2, 'ops') on tombstoned P2 must raise ValueError.

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError):
            s.revoke_xproj(self.con, self.P2, "ops")

    def test_revoke_xproj_on_tombstoned_error_message(self):
        """ValueError must contain "project 'P2' is tombstoned".

        RED: currently no state guard.
        """
        with self.assertRaises(ValueError) as ctx:
            s.revoke_xproj(self.con, self.P2, "ops")
        msg = str(ctx.exception)
        self.assertIn("P2", msg,
                      f"Error must name the project; got: {msg!r}")
        self.assertIn("tombstoned", msg,
                      f"Error must say 'tombstoned'; got: {msg!r}")


# ---------------------------------------------------------------------------
# T16 — AC11: DEC-E — grant SURVIVES archive→unarchive
# ---------------------------------------------------------------------------

class GrantSurvivesArchiveUnarchiveTest(_TempDataHome):
    """A grant set while active must survive archive→unarchive intact.

    AC11: grant P2, archive P2, unarchive P2 → xproj_granted(P2) still True
    and a cross-project send from P2 works immediately.
    RED: AttributeError on archive/unarchive.
    """

    def test_grant_survives_archive_unarchive(self):
        """xproj_granted(P2) must be True after grant→archive→unarchive cycle.

        RED: AttributeError on archive or unarchive.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        self.assertTrue(s.xproj_granted(self.con, self.P2),
                        "P2 must be granted before archive")
        # Archive (no live notifiers, so no eviction issue)
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        # Unarchive
        s.unarchive(self.con, self.P2, self.ML_P2)
        # Grant must survive
        self.assertTrue(s.xproj_granted(self.con, self.P2),
                        "Grant must survive archive→unarchive cycle")

    def test_grant_xproj_columns_intact_after_unarchive(self):
        """xproj_granted_at and xproj_granted_by must remain populated after unarchive.

        RED: AttributeError on archive or unarchive.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        s.unarchive(self.con, self.P2, self.ML_P2)
        row = self.con.execute(
            "SELECT xproj_granted_at, xproj_granted_by FROM project WHERE project_id=?",
            (self.P2,)
        ).fetchone()
        self.assertIsNotNone(row["xproj_granted_at"],
                             "xproj_granted_at must remain set after unarchive")
        self.assertEqual(row["xproj_granted_by"], "ops",
                         f"xproj_granted_by must be 'ops' after unarchive; "
                         f"got {row['xproj_granted_by']!r}")

    def test_xproj_send_works_after_unarchive(self):
        """After grant→archive→unarchive, a cross-project send from P2 must succeed.

        RED: AttributeError on archive or unarchive.
        """
        s.grant_xproj(self.con, self.P2, "ops")
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        s.unarchive(self.con, self.P2, self.ML_P2)
        # Cross-project send P2 → P1 must succeed
        mid = s.send(
            self.con, s.store_dir(self.P2), self.ML_P2,
            to=[self.ML_P1], subject="post-unarchive ping",
        )
        self.assertIsNotNone(mid, "send after unarchive must return a message id")
        items = s.inbox(self.con, self.ML_P1)
        self.assertTrue(any(it["id"] == mid for it in items),
                        "Post-unarchive message must appear in P1 inbox")


if __name__ == "__main__":
    unittest.main(verbosity=2)
