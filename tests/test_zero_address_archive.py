"""test_zero_address_archive.py — RED tests for CR-SAN-045 Cycle 2 (§S2).

Covers §S2 — the super-admin escape hatch that lets a *zero-address* project
(one whose grammar-invalid id could never register a Mainline, or any project
with literally no address rows) be archived by the super-admin, so the
mandatory two-step (`active -> archived -> tombstoned`) becomes reachable.

  _has_any_address(con, project_id) -> bool   (not tested directly — internal
      helper; exercised indirectly through archive()'s authz branching below)

  _archive_guards(con, project_id, by)   — relaxed, additively:
    zero-address + by == stored admin name          -> allowed (the unwedge)
    zero-address + by == "Mainline - <valid-id>"     -> still allowed (additive)
    zero-address + any other by                      -> PermissionError (unchanged)
    >=1 address row (even soft-deleted)               -> straight to
        _require_project_mainline — UNCHANGED (admin may NOT archive a
        populated project)

  _tombstone_guards()/tombstone_project()/archive()/_unarchive_guards()/
  unarchive() are UNCHANGED by this cycle — the two-step interlock (archived
  before tombstoned) must still hold even for a zero-address project.

Acceptance criteria covered (CR-SAN-045 spec, S2):
  AC6  archive(con, "Empty", <admin>) on an active zero-address project
       succeeds: state -> "archived", archived_at non-NULL.
  AC7  AC6 also succeeds when store_dir("Empty") is ABSENT.
  AC8  Additive: archive(con, "Empty", "Mainline - Empty") on the active
       zero-address project ALSO succeeds.
  AC9  Authz still holds: archive(con, "Empty", "random") and
       archive(con, "Empty", "Track 1 - Empty") raise PermissionError, state
       stays "active".
  AC10 Tight carve-out: for an active project WITH >=1 address row ("Full"),
       archive(con, "Full", <admin>) raises PermissionError while
       archive(con, "Full", "Mainline - Full") succeeds.
  AC11 Soft-deleted != zero: a project whose only address was register'ed
       then unregister'ed (-> 1 soft-deleted row) is NOT zero-address —
       archive(con, X, <admin>) raises PermissionError.
  AC12 Two-step preserved: tombstone_project(con, "Empty", <admin>) while
       "Empty" is ACTIVE (even though zero-address) still raises ValueError
       containing "archive it first"; state unchanged.
  AC13 Full unwedge E2E: zero-address active "Zombie" with its store dir
       DELETED: archive() then tombstone_project() succeed in sequence;
       final project_state == "tombstoned", "Zombie" absent from
       list_projects(), no exception.
  AC14 archive_preview(con, "Empty", <admin>) (dry-run) on the active
       zero-address project returns [] and writes nothing — state still
       "active".

Expected RED/GREEN split against CURRENT (pre-S2) code:
  - AC6/AC7/AC8-admin-half/AC13-admin-half/AC14-admin-half FAIL on current
    code: `by=<admin>` is not a valid Mainline address, so
    `_require_project_mainline` raises PermissionError instead of the
    zero-address carve-out (which does not exist yet). These are the
    expected REDs.
  - AC8-Mainline-half, AC9, AC10-Mainline-half, AC11, AC12 assert CURRENT
    behaviour that must survive S2 unchanged — they are regression pins and
    may already PASS against current code. That is correct.

Run via the crucible (uses .venv interpreter):
  WORKFLOW_CYCLE_ID=2 python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_zero_address_archive --agent CR-SAN-045-C2-RED
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
# Fixture base class — per-test isolated XDG_DATA_HOME + admin assignment.
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME with the super-admin 'ops' assigned.

    Subclasses provision whichever projects/addresses they need in setUp
    (after calling super().setUp()).
    """

    ADMIN = "ops"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-zeroaddr-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        self.con = s.connect()
        s.assign_admin(self.con, self.ADMIN)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _raw_project_state(self, project_id):
        row = self.con.execute(
            "SELECT state FROM project WHERE project_id=?", (project_id,)
        ).fetchone()
        return row["state"] if row else None

    def _project_row(self, project_id):
        return self.con.execute(
            "SELECT * FROM project WHERE project_id=?", (project_id,)
        ).fetchone()

    def _address_count(self, project_id):
        return self.con.execute(
            "SELECT COUNT(*) FROM address WHERE project=?", (project_id,)
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# AC6 — admin may archive an active, zero-address project
# ---------------------------------------------------------------------------

class ZeroAddressArchiveAdminSucceedsTest(_TempDataHome):
    """archive(con, "Empty", <admin>) on an active zero-address project must
    succeed: state -> "archived", archived_at non-NULL.

    AC6. RED (current code): PermissionError from _require_project_mainline,
    because the admin name is not a valid 'Mainline - <id>' address.
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")  # zero-address, active — no register() call.

    def test_admin_archive_sets_state_archived(self):
        s.archive(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        state = self._raw_project_state("Empty")
        self.assertEqual(
            state, "archived",
            f"zero-address project must be archived by the admin; got {state!r}",
        )

    def test_admin_archive_sets_archived_at_nonnull(self):
        s.archive(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        row = self._project_row("Empty")
        self.assertIsNotNone(
            row["archived_at"],
            "archived_at must be non-NULL after admin archives a zero-address project",
        )

    def test_admin_archive_preconditions_zero_address(self):
        """Fixture-validity check: 'Empty' truly has zero address rows."""
        self.assertEqual(
            self._address_count("Empty"), 0,
            "fixture 'Empty' must have zero address rows before archive",
        )


# ---------------------------------------------------------------------------
# AC7 — AC6 also holds when the store dir is ABSENT
# ---------------------------------------------------------------------------

class ZeroAddressArchiveNoStoreDirTest(_TempDataHome):
    """archive(con, "Empty", <admin>) must succeed even when store_dir("Empty")
    does not exist on disk — archive touches no store dir. Models the
    out-of-band-deleted "Model B" zombie.

    AC7. RED: PermissionError (current code), same root cause as AC6.
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")
        # Delete the store dir out-of-band (simulates the reported zombie).
        shutil.rmtree(s.store_dir("Empty"), ignore_errors=True)

    def test_store_dir_absent_before_archive(self):
        """Fixture-validity check: the store dir is really gone."""
        self.assertFalse(
            os.path.isdir(s.store_dir("Empty")),
            "fixture must have deleted the 'Empty' store dir before archive",
        )

    def test_admin_archive_succeeds_without_store_dir(self):
        s.archive(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        state = self._raw_project_state("Empty")
        self.assertEqual(
            state, "archived",
            f"admin archive must succeed with the store dir absent; got {state!r}",
        )

    def test_admin_archive_does_not_recreate_store_dir(self):
        s.archive(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        self.assertFalse(
            os.path.isdir(s.store_dir("Empty")),
            "archive() must not recreate the store dir",
        )


# ---------------------------------------------------------------------------
# AC8 — additive: a grammar-valid Mainline still works for a zero-address
# project (no regression from the admin carve-out)
# ---------------------------------------------------------------------------

class ZeroAddressArchiveMainlineAdditiveTest(_TempDataHome):
    """archive(con, "Empty", "Mainline - Empty") on the active zero-address
    project must ALSO succeed (additive — the admin carve-out does not
    displace the existing grammar-valid-Mainline path).

    AC8. Expected to already PASS against current code (regression pin) —
    _require_project_mainline accepts a format-valid "Mainline - Empty" even
    though no such address is registered (honor-system, per the existing
    _require_project_mainline docstring/tests).
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")

    def test_mainline_archive_succeeds_on_zero_address_project(self):
        s.archive(self.con, "Empty", "Mainline - Empty", wait_secs=0.1)
        state = self._raw_project_state("Empty")
        self.assertEqual(
            state, "archived",
            f"grammar-valid Mainline must still archive a zero-address project; "
            f"got {state!r}",
        )

    def test_mainline_archive_sets_archived_at(self):
        s.archive(self.con, "Empty", "Mainline - Empty", wait_secs=0.1)
        row = self._project_row("Empty")
        self.assertIsNotNone(
            row["archived_at"],
            "archived_at must be set after Mainline archives a zero-address project",
        )


# ---------------------------------------------------------------------------
# AC9 — authz still holds on the escape hatch: non-admin, non-Mainline `by`
# is rejected
# ---------------------------------------------------------------------------

class ZeroAddressArchiveAuthzRejectedTest(_TempDataHome):
    """archive(con, "Empty", "random") and archive(con, "Empty",
    "Track 1 - Empty") must both raise PermissionError; state stays "active".

    AC9. Expected to already PASS against current code (regression pin) —
    neither "random" nor a Track address ever satisfied
    _require_project_mainline, admin carve-out or not.
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")

    def test_random_string_by_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            s.archive(self.con, "Empty", "random", wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Empty"), "active",
            "state must remain 'active' after a rejected archive (by='random')",
        )

    def test_track_address_by_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            s.archive(self.con, "Empty", "Track 1 - Empty", wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Empty"), "active",
            "state must remain 'active' after a rejected archive (by=Track)",
        )


# ---------------------------------------------------------------------------
# AC10 — tight carve-out: a populated project (>=1 address row) can NOT be
# archived by the admin; only its own Mainline can
# ---------------------------------------------------------------------------

class PopulatedProjectArchiveCarveOutTest(_TempDataHome):
    """For an active project WITH >=1 address row ("Full"):
      archive(con, "Full", <admin>) -> PermissionError (admin may NOT archive
        a populated project — the carve-out is zero-address-only).
      archive(con, "Full", "Mainline - Full") -> succeeds.

    AC10. The admin-rejected half is expected to already PASS against
    current code (regression pin — current code has no admin path at all).
    The Mainline-succeeds half is also an existing-behaviour regression pin.
    """

    def setUp(self):
        super().setUp()
        s.setup("Full")
        s.register(self.con, "Mainline - Full", kind="mainline", project="Full")
        s.register(self.con, "Track 1 - Full", kind="track", project="Full")

    def test_admin_archive_of_populated_project_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            s.archive(self.con, "Full", self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Full"), "active",
            "state must remain 'active' after admin is rejected on a populated project",
        )

    def test_mainline_archive_of_populated_project_succeeds(self):
        s.archive(self.con, "Full", "Mainline - Full", wait_secs=0.1)
        state = self._raw_project_state("Full")
        self.assertEqual(
            state, "archived",
            f"the project's own Mainline must still archive a populated project; "
            f"got {state!r}",
        )


# ---------------------------------------------------------------------------
# AC11 — soft-deleted address rows are NOT zero-address (DRIFT-2 pin)
# ---------------------------------------------------------------------------

class SoftDeletedNotZeroAddressTest(_TempDataHome):
    """A project whose only address was register'ed then unregister'ed
    (-> exactly 1 soft-deleted address row) is NOT a zero-address project:
    archive(con, "Solo", <admin>) must raise PermissionError (the admin path
    must not be taken; _has_any_address counts soft-deleted rows).

    AC11. Expected to already PASS against current code (regression pin) —
    current code has no admin path at all, so any non-Mainline `by` is
    already rejected regardless of address state.
    """

    def setUp(self):
        super().setUp()
        s.setup("Solo")
        s.register(self.con, "Mainline - Solo", kind="mainline", project="Solo")
        # Self-unregister -> soft-delete (active=0), row remains in `address`.
        result = s.unregister(self.con, "Mainline - Solo", "Mainline - Solo",
                              project="Solo")
        self.assertEqual(
            result[0], "unregistered",
            f"fixture precondition: unregister must soft-delete (no live "
            f"notifier); got {result!r}",
        )

    def test_solo_has_exactly_one_address_row(self):
        """Fixture-validity check: 1 row exists (soft-deleted), not 0."""
        self.assertEqual(
            self._address_count("Solo"), 1,
            "fixture 'Solo' must retain exactly 1 (soft-deleted) address row",
        )

    def test_admin_archive_of_soft_deleted_project_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            s.archive(self.con, "Solo", self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Solo"), "active",
            "state must remain 'active' — soft-deleted-only project is not "
            "zero-address, so the admin path must not apply",
        )


# ---------------------------------------------------------------------------
# AC12 — the mandatory two-step is preserved even for a zero-address project
# ---------------------------------------------------------------------------

class ZeroAddressTombstoneTwoStepPreservedTest(_TempDataHome):
    """tombstone_project(con, "Empty", <admin>) while "Empty" is ACTIVE (even
    though it is zero-address) must still raise ValueError containing
    "archive it first"; state must remain unchanged.

    AC12. Expected to already PASS against current code (regression pin) —
    _tombstone_guards is untouched by S2 and already enforces this.
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")

    def test_tombstone_active_zero_address_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            s.tombstone_project(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        self.assertIn(
            "archive it first", str(ctx.exception),
            f"tombstone of an active (even zero-address) project must still "
            f"require archive-first; got: {ctx.exception!r}",
        )

    def test_tombstone_active_zero_address_state_unchanged(self):
        try:
            s.tombstone_project(self.con, "Empty", self.ADMIN, wait_secs=0.1)
        except ValueError:
            pass
        self.assertEqual(
            self._raw_project_state("Empty"), "active",
            "state must remain 'active' after the two-step guard fires",
        )


# ---------------------------------------------------------------------------
# AC13 — full unwedge E2E: archive then tombstone in sequence
# ---------------------------------------------------------------------------

class ZeroAddressFullUnwedgeE2ETest(_TempDataHome):
    """For a zero-address active "Zombie" project with its store dir DELETED:
      archive(con, "Zombie", <admin>) then
      tombstone_project(con, "Zombie", <admin>)
    must both succeed in sequence; final project_state == "tombstoned",
    "Zombie" absent from list_projects(), no exception raised anywhere.

    AC13. RED (current code): the archive() call fails with PermissionError
    (same root cause as AC6/AC7), so the E2E sequence never reaches tombstone.
    """

    def setUp(self):
        super().setUp()
        s.setup("Zombie")
        shutil.rmtree(s.store_dir("Zombie"), ignore_errors=True)

    def test_zombie_precondition_zero_address_and_no_store_dir(self):
        """Fixture-validity check."""
        self.assertEqual(self._address_count("Zombie"), 0)
        self.assertFalse(os.path.isdir(s.store_dir("Zombie")))

    def test_archive_then_tombstone_succeed_in_sequence(self):
        s.archive(self.con, "Zombie", self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Zombie"), "archived",
            "Zombie must be archived after step 1 of the unwedge sequence",
        )
        s.tombstone_project(self.con, "Zombie", self.ADMIN, wait_secs=0.1)
        self.assertEqual(
            self._raw_project_state("Zombie"), "tombstoned",
            "Zombie must be tombstoned after step 2 of the unwedge sequence",
        )

    def test_zombie_absent_from_list_projects_after_unwedge(self):
        s.archive(self.con, "Zombie", self.ADMIN, wait_secs=0.1)
        s.tombstone_project(self.con, "Zombie", self.ADMIN, wait_secs=0.1)
        ids = s.list_projects()
        self.assertNotIn(
            "Zombie", ids,
            f"'Zombie' must not appear in list_projects() after tombstone; got {ids!r}",
        )


# ---------------------------------------------------------------------------
# AC14 — archive_preview dry-run on a zero-address project
# ---------------------------------------------------------------------------

class ZeroAddressArchivePreviewDryRunTest(_TempDataHome):
    """archive_preview(con, "Empty", <admin>) (dry-run) on the active
    zero-address project must return [] (no live watchers — there ARE no
    addresses to have watchers) and write nothing — state stays "active".

    AC14. RED (current code): PermissionError, same root cause as AC6
    (archive_preview shares _archive_guards with archive()).
    """

    def setUp(self):
        super().setUp()
        s.setup("Empty")

    def test_admin_preview_returns_empty_list(self):
        result = s.archive_preview(self.con, "Empty", self.ADMIN)
        self.assertEqual(
            result, [],
            f"archive_preview on a zero-address project must return []; got {result!r}",
        )

    def test_admin_preview_writes_nothing(self):
        s.archive_preview(self.con, "Empty", self.ADMIN)
        self.assertEqual(
            self._raw_project_state("Empty"), "active",
            "archive_preview must be a pure dry-run — state must remain 'active'",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
