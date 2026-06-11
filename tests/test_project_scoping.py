"""test_project_scoping.py — RED tests for CR-SAN-022 Cycle 4.

Covers AC7 — Explicit project scoping (DRIFT-1):
  With P1+P2 both enrolled and populated in the global DB:
  1. Cross-project send refused with exact error + no rows written (atomicity).
  2. Mixed-recipient list refused — atomicity: even the valid P1 recipient not delivered.
  3. In-project send still works (locked behaviour — should PASS already).
  4. all-tracks expansion scoped to sender's project only (no P2 rows).
  5. addressbook(con, project) filters by project (TypeError on cur no-arg API).
     CLI addressbook shows only same-project addresses.
  6. unregister cross-project raises PermissionError; same-project still works.

Expected RED failures (against cur code, pre-GREEN C4):
  1: send delivers cross-project (is_active finds the foreign row) → assertion failure
     on the raised-error expectation.
  2: mixed list — same as (1).
  3: in-project send works already → PASS (locked behaviour).
  4: all-tracks expands to ALL active addresses → P2 rows exist → assertion failure.
  5: addressbook(con, 'P1') → TypeError (extra arg not accepted).
  6: unregister cross-project currently succeeds (kind check only) → assertion failure.

Run via the Crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_project_scoping --agent red-cr022-c4
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Mixin: per-test isolated XDG_DATA_HOME + two projects P1 and P2 with
    four registered addresses:
        Mainline - P1, Track 1 - P1  (project P1)
        Mainline - P2, Track 1 - P2  (project P2)

    The connection (self.con) is open for the duration of the test; the
    per-project body store used in send() calls is self.store_p1 / self.store_p2.
    """

    P1 = "P1"
    P2 = "P2"
    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-scope-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision both projects
        self.store_p1 = s.setup(self.P1)
        self.store_p2 = s.setup(self.P2)

        # Open a single shared connection (global DB)
        self.con = s.connect()

        # Register four addresses: two per project
        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --

    def _msg_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def _recip_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message_recipient").fetchone()[0]

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process and capture stdout. Returns (rc, output_str)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# 1 & 2: Cross-project send refused + atomicity
# ---------------------------------------------------------------------------

class CrossProjectSendRefusedTest(_TempDataHome):
    """send() with a cross-project recipient must raise ValueError with the exact
    error text 'cross-project sending is not enabled (CR-SAN-023)' and must NOT
    write any message or message_recipient row.

    RED: current send() validates only is_active(); it delivers the message to
    the foreign address because the address IS active — the is_active check passes.
    """

    EXPECTED_ERROR = "cross-project sending is not enabled (CR-SAN-023)"

    def test_cross_project_to_raises_valueerror(self):
        """send from Mainline-P1 to Mainline-P2 must raise ValueError.

        RED: is_active(con, 'Mainline - P2') returns True → send succeeds
        instead of raising.
        """
        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.ML_P2],
                subject="x",
                project=self.P1,
            )
        self.assertIn(
            self.EXPECTED_ERROR,
            str(ctx.exception),
            f"ValueError must contain exact text {self.EXPECTED_ERROR!r}; "
            f"got: {ctx.exception!r}",
        )

    def test_cross_project_to_writes_no_message_row(self):
        """After a cross-project send attempt, the message table must be unchanged.

        RED: current code inserts the message row before doing any scoping check
        (if the check existed at all), so a row is written.
        """
        msg_before = self._msg_count()
        try:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.ML_P2],
                subject="x",
                project=self.P1,
            )
        except ValueError:
            pass
        self.assertEqual(
            self._msg_count(),
            msg_before,
            "A failed cross-project send must write ZERO message rows (atomicity).",
        )

    def test_cross_project_to_writes_no_recipient_row(self):
        """After a cross-project send attempt, message_recipient must be unchanged.

        RED: even if the message row is not written, we check recipients too.
        """
        recip_before = self._recip_count()
        try:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.ML_P2],
                subject="x",
                project=self.P1,
            )
        except ValueError:
            pass
        self.assertEqual(
            self._recip_count(),
            recip_before,
            "A failed cross-project send must write ZERO message_recipient rows (atomicity).",
        )

    def test_cross_project_in_cc_raises_valueerror(self):
        """A cross-project address in cc must also raise ValueError.

        The error message must contain the exact spec text.
        RED: same as the to case — no project check in current code.
        """
        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.T1_P1],
                cc=[self.ML_P2],
                subject="cc-cross",
                project=self.P1,
            )
        self.assertIn(
            self.EXPECTED_ERROR,
            str(ctx.exception),
            f"Cross-project cc must raise ValueError with exact text; got: {ctx.exception!r}",
        )

    def test_cross_project_in_cc_writes_no_rows(self):
        """Cross-project cc attempt must leave both tables unchanged (atomicity).

        RED: current code would deliver to T1_P1 (same-project recipient) even
        though the cc is cross-project — because there is no scoping check.
        """
        msg_before = self._msg_count()
        recip_before = self._recip_count()
        try:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.T1_P1],
                cc=[self.ML_P2],
                subject="cc-cross",
                project=self.P1,
            )
        except ValueError:
            pass
        self.assertEqual(
            self._msg_count(),
            msg_before,
            "Cross-project cc: ZERO message rows must be written (atomicity).",
        )
        self.assertEqual(
            self._recip_count(),
            recip_before,
            "Cross-project cc: ZERO message_recipient rows must be written — "
            "the valid P1 recipient must NOT have been partially delivered.",
        )


# ---------------------------------------------------------------------------
# 2: Mixed recipient list (valid P1 + invalid P2) — atomicity
# ---------------------------------------------------------------------------

class MixedRecipientAtomicityTest(_TempDataHome):
    """When the to list contains both a same-project and a cross-project address,
    the entire send must be refused and NOTHING written — not even the valid
    same-project recipient gets delivered.

    RED: current code delivers to the valid P1 address and silently delivers to
    the P2 address too (no project check at all).
    """

    EXPECTED_ERROR = "cross-project sending is not enabled (CR-SAN-023)"

    def test_mixed_to_list_raises_valueerror(self):
        """to=['Track 1 - P1', 'Mainline - P2'] must raise ValueError.

        RED: no cross-project guard → send succeeds, delivering to both.
        """
        with self.assertRaises(ValueError) as ctx:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.T1_P1, self.ML_P2],
                subject="mixed",
                project=self.P1,
            )
        self.assertIn(
            self.EXPECTED_ERROR,
            str(ctx.exception),
            f"Mixed to-list must raise ValueError with exact text; got: {ctx.exception!r}",
        )

    def test_mixed_to_list_p1_recipient_not_delivered(self):
        """After the refused mixed send, Track 1 - P1 (valid) must have received nothing.

        This is the atomicity assertion: the scoping check must abort before ANY
        message_recipient row is inserted.

        RED: current code inserts the valid P1 recipient row, then the P2 row — or
        in order, P1 is delivered before P2 is encountered (depending on expansion
        order). Either way the P1 row is written.
        """
        recip_before = self._recip_count()
        try:
            s.send(
                self.con, self.store_p1,
                self.ML_P1,
                to=[self.T1_P1, self.ML_P2],
                subject="mixed",
                project=self.P1,
            )
        except ValueError:
            pass
        # Both tables must be unchanged
        self.assertEqual(
            self._msg_count(),
            0,
            "ZERO message rows — both tables must be unchanged after refused mixed send.",
        )
        self.assertEqual(
            self._recip_count(),
            recip_before,
            "ZERO message_recipient rows — T1_P1 must NOT have been partially delivered "
            "when the send is refused due to a cross-project address in the same to-list.",
        )


# ---------------------------------------------------------------------------
# 2b: Reply cross-project refused
# ---------------------------------------------------------------------------

class CrossProjectReplyRefusedTest(_TempDataHome):
    """reply() from a P1 address to a P2-internal message must raise ValueError with
    the same error text.

    Approach: send a P2→P2 message first (T1_P2 → ML_P2), then attempt to reply
    from ML_P1 (a P1 address). The reply defaults to=ML_P2 (the P2 sender), which
    is a cross-project recipient for a P1 sender.

    RED: reply() calls send() internally; since send() has no project scoping check
    today, the reply succeeds and delivers to ML_P2.
    """

    EXPECTED_ERROR = "cross-project sending is not enabled (CR-SAN-023)"

    def _p2_internal_msg(self):
        """Insert a P2→P2 message and return its id."""
        return s.send(
            self.con, self.store_p2,
            self.T1_P2,
            to=[self.ML_P2],
            subject="P2 internal",
            project=self.P2,
        )

    def test_reply_cross_project_raises_valueerror(self):
        """reply from ML_P1 to a P2-internal message must raise ValueError.

        The reply would default to=[T1_P2] (the parent's sender), which is a P2
        address — cross-project for a P1 sender.

        RED: no project scoping in send/reply → reply delivers to T1_P2.
        """
        parent_id = self._p2_internal_msg()
        with self.assertRaises(ValueError) as ctx:
            s.reply(
                self.con, self.store_p1,
                parent_id,
                self.ML_P1,
                project=self.P1,
            )
        self.assertIn(
            self.EXPECTED_ERROR,
            str(ctx.exception),
            f"Cross-project reply must raise ValueError with exact text; "
            f"got: {ctx.exception!r}",
        )

    def test_reply_cross_project_writes_no_rows(self):
        """A refused cross-project reply must write no message or recipient rows.

        RED: current code writes the rows.
        """
        parent_id = self._p2_internal_msg()
        msg_before = self._msg_count()
        recip_before = self._recip_count()
        try:
            s.reply(
                self.con, self.store_p1,
                parent_id,
                self.ML_P1,
                project=self.P1,
            )
        except ValueError:
            pass
        self.assertEqual(
            self._msg_count(),
            msg_before,
            "A refused cross-project reply must write ZERO message rows.",
        )
        self.assertEqual(
            self._recip_count(),
            recip_before,
            "A refused cross-project reply must write ZERO message_recipient rows.",
        )


# ---------------------------------------------------------------------------
# 3: In-project send still works (locked behaviour — should PASS)
# ---------------------------------------------------------------------------

class InProjectSendStillWorksTest(_TempDataHome):
    """Same-project send must continue to work end-to-end (locked behaviour).

    This is expected to PASS already; it guards against over-scoping regressions
    where the new project check accidentally blocks same-project sends.
    """

    def test_in_project_send_delivers(self):
        """P1→P1 send must succeed and be visible in T1_P1's inbox.

        This is locked behaviour. If this fails, GREEN has over-restricted scoping.
        """
        mid = s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[self.T1_P1],
            subject="hello same project",
            project=self.P1,
        )
        self.assertIsNotNone(mid, "send must return a message id")
        self.assertIsInstance(mid, int, "send must return an integer message id")

        # The message must be in T1_P1's unread inbox
        unread = s.inbox(self.con, self.T1_P1, unread_only=True)
        self.assertEqual(
            len(unread),
            1,
            f"T1_P1 must have exactly 1 unread message after P1→P1 send; "
            f"got {len(unread)}",
        )
        self.assertEqual(
            unread[0]["subject"],
            "hello same project",
            f"Message subject must match; got {unread[0]['subject']!r}",
        )

    def test_in_project_send_round_trip_via_fetch(self):
        """P1→P1 send + fetch round-trip must succeed.

        Verifies that the body arrives correctly and the message is marked read.
        """
        s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[self.T1_P1],
            subject="round-trip",
            project=self.P1,
        )
        items = s.fetch(self.con, self.store_p1, self.T1_P1)
        self.assertEqual(len(items), 1, "fetch must return exactly 1 item")
        self.assertEqual(items[0]["subject"], "round-trip")

        # After fetch, inbox must be empty
        after_fetch = s.inbox(self.con, self.T1_P1, unread_only=True)
        self.assertEqual(
            len(after_fetch),
            0,
            "After fetch, T1_P1's unread inbox must be empty.",
        )


# ---------------------------------------------------------------------------
# 4: all-tracks scoped to sender's project
# ---------------------------------------------------------------------------

class AllTracksProjectScopedTest(_TempDataHome):
    """send with to=['all-tracks'] from a P1 sender must expand only to P1 active
    addresses minus the sender — ZERO P2 addresses.

    RED: current active_addresses(con) returns ALL active addresses regardless of
    project, so P2 addresses appear in the expanded recipient list.
    """

    def test_all_tracks_excludes_p2_addresses(self):
        """all-tracks from ML_P1 must NOT deliver to any P2 address.

        RED: current _expand_recipients / active_addresses has no project filter.
        With 4 active addresses, all-tracks returns Mainline-P2 and Track-1-P2 too.
        """
        mid = s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[s.BROADCAST],
            subject="broadcast",
            project=self.P1,
        )
        recips = {
            r["recipient"]
            for r in self.con.execute(
                "SELECT recipient FROM message_recipient WHERE message_id=?", (mid,)
            ).fetchall()
        }
        # Must NOT include any P2 address
        self.assertNotIn(
            self.ML_P2,
            recips,
            f"all-tracks must not deliver to {self.ML_P2!r}; got recips={recips!r}",
        )
        self.assertNotIn(
            self.T1_P2,
            recips,
            f"all-tracks must not deliver to {self.T1_P2!r}; got recips={recips!r}",
        )

    def test_all_tracks_delivers_exactly_t1_p1(self):
        """all-tracks from ML_P1 must deliver to exactly Track 1 - P1 (and not the sender).

        With only Mainline-P1 and Track 1-P1 in P1, and ML_P1 is the sender,
        the only expected recipient is T1_P1.

        RED: current code returns all active addresses → 3 recipients (T1_P1, ML_P2, T1_P2).
        """
        mid = s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[s.BROADCAST],
            subject="b",
            project=self.P1,
        )
        recips = {
            r["recipient"]
            for r in self.con.execute(
                "SELECT recipient FROM message_recipient WHERE message_id=?", (mid,)
            ).fetchall()
        }
        self.assertEqual(
            recips,
            {self.T1_P1},
            f"all-tracks from ML_P1 must deliver to exactly {{T1_P1}}; "
            f"got {recips!r}",
        )

    def test_all_tracks_sender_not_in_recipients(self):
        """The sender must always be excluded from all-tracks expansion, even when scoped.

        This is a pre-existing invariant; kept as a guard.
        """
        mid = s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[s.BROADCAST],
            subject="sender-excluded",
            project=self.P1,
        )
        recips = {
            r["recipient"]
            for r in self.con.execute(
                "SELECT recipient FROM message_recipient WHERE message_id=?", (mid,)
            ).fetchall()
        }
        self.assertNotIn(
            self.ML_P1,
            recips,
            f"The sender ML_P1 must not appear in its own all-tracks recipients; "
            f"got {recips!r}",
        )

    def test_all_tracks_p2_count_is_zero(self):
        """After an all-tracks from ML_P1, the count of P2 addresses in the recipient
        list is exactly 0 (not >=0 — a definite upper bound).

        RED: current code: count = 2 (ML_P2, T1_P2).
        """
        mid = s.send(
            self.con, self.store_p1,
            self.ML_P1,
            to=[s.BROADCAST],
            subject="count-check",
            project=self.P1,
        )
        p2_rows = self.con.execute(
            "SELECT COUNT(*) FROM message_recipient "
            "WHERE message_id=? AND (recipient=? OR recipient=?)",
            (mid, self.ML_P2, self.T1_P2),
        ).fetchone()[0]
        self.assertEqual(
            p2_rows,
            0,
            f"Exactly 0 P2-address recipient rows expected; got {p2_rows}",
        )


# ---------------------------------------------------------------------------
# 5: addressbook filtered by project
# ---------------------------------------------------------------------------

class AddressbookFilteredTest(_TempDataHome):
    """addressbook(con, project) must return only addresses for that project.

    The new contract (post-C4) is addressbook(con, project_id) with a project
    parameter. The current API is addressbook(con) — no project parameter.

    RED: addressbook(con, 'P1') raises TypeError because the current fn takes
    only one argument (con).
    """

    def test_addressbook_with_project_raises_typeerror_today(self):
        """addressbook(con, 'P1') raises TypeError with the current no-project API.

        This documents the WANT-OF-IMPLEMENTATION. GREEN must add the project param.
        """
        with self.assertRaises(TypeError):
            s.addressbook(self.con, self.P1)

    def test_addressbook_p1_returns_only_p1_addresses(self):
        """addressbook(con, 'P1') must return exactly the two P1 addresses.

        RED: TypeError (param doesn't exist yet) — the test will fail at the
        addressbook(con, 'P1') call.
        """
        book = s.addressbook(self.con, self.P1)
        addrs = {b["address"] for b in book}
        self.assertEqual(
            addrs,
            {self.ML_P1, self.T1_P1},
            f"addressbook(con, 'P1') must return exactly P1 addresses; got {addrs!r}",
        )

    def test_addressbook_p1_excludes_p2_addresses(self):
        """addressbook(con, 'P1') must not contain any P2 address.

        RED: TypeError.
        """
        book = s.addressbook(self.con, self.P1)
        addrs = {b["address"] for b in book}
        self.assertNotIn(
            self.ML_P2,
            addrs,
            f"P2 Mainline must not appear in addressbook(con, 'P1'); got {addrs!r}",
        )
        self.assertNotIn(
            self.T1_P2,
            addrs,
            f"P2 Track 1 must not appear in addressbook(con, 'P1'); got {addrs!r}",
        )

    def test_addressbook_p2_returns_only_p2_addresses(self):
        """addressbook(con, 'P2') must return exactly the two P2 addresses.

        RED: TypeError.
        """
        book = s.addressbook(self.con, self.P2)
        addrs = {b["address"] for b in book}
        self.assertEqual(
            addrs,
            {self.ML_P2, self.T1_P2},
            f"addressbook(con, 'P2') must return exactly P2 addresses; got {addrs!r}",
        )

    def test_addressbook_count_bounded(self):
        """addressbook(con, 'P1') returns exactly 2, not more.

        Ensures the filter is tight — not returning some or all addresses.

        RED: TypeError.
        """
        book = s.addressbook(self.con, self.P1)
        self.assertEqual(
            len(book),
            2,
            f"addressbook(con, 'P1') must return exactly 2 entries; got {len(book)}",
        )

    def test_cli_addressbook_p1_excludes_p2(self):
        """CLI 'sandesh --project P1 addressbook' output must not contain any P2 address.

        This drives the CLI in-process and captures stdout. The CLI calls
        sdb.addressbook(con) today — after C4 GREEN it must call addressbook(con, project).

        RED: CLI currently calls addressbook(con) with no project arg → shows all 4
        addresses, including P2 ones.
        """
        rc, output = self._run_cli(["--project", self.P1, "addressbook"])
        self.assertEqual(rc, 0, f"CLI addressbook must succeed; rc={rc}, output={output!r}")
        self.assertNotIn(
            self.P2,
            output,
            f"CLI addressbook --project P1 must not mention P2 in its output; "
            f"got: {output!r}",
        )
        # Positive: P1 addresses must appear
        self.assertIn(
            self.ML_P1,
            output,
            f"CLI addressbook --project P1 must include {self.ML_P1!r}; "
            f"got: {output!r}",
        )


# ---------------------------------------------------------------------------
# 6: unregister cross-project raises PermissionError
# ---------------------------------------------------------------------------

class UnregisterProjectScopedTest(_TempDataHome):
    """unregister of a P2 address by a P1 Mainline must raise PermissionError,
    even though the requester IS a Mainline.

    Current authz check is: only Mainline may remove another participant —
    it does NOT check that the Mainline and target are in the same project.

    RED: current code allows ML_P1 to unregister T1_P2 because ML_P1 satisfies
    'orch_req == "Mainline"'.
    """

    def test_cross_project_unregister_raises_permission_error(self):
        """unregister(con, T1_P2, requester=ML_P1, project='P1') must raise PermissionError.

        RED: current code checks orchestrator kind (Mainline OK) but NOT project
        boundary — T1_P2 is unregistered successfully.
        """
        with self.assertRaises(PermissionError):
            s.unregister(
                self.con,
                self.T1_P2,
                requester=self.ML_P1,
                project=self.P1,
            )

    def test_cross_project_unregister_leaves_p2_row_active(self):
        """After a refused cross-project unregister, T1_P2 must still be active.

        RED: current code unregisters T1_P2 → is_active returns False.
        """
        try:
            s.unregister(
                self.con,
                self.T1_P2,
                requester=self.ML_P1,
                project=self.P1,
            )
        except PermissionError:
            pass  # expected
        self.assertTrue(
            s.is_active(self.con, self.T1_P2),
            f"{self.T1_P2!r} must remain active after a refused cross-project unregister.",
        )

    def test_self_removal_same_project_still_works(self):
        """T1_P1 may unregister itself — same-project self-removal must succeed.

        This is locked behaviour; kept as a guard against over-restriction.
        """
        result, pid = s.unregister(
            self.con,
            self.T1_P1,
            requester=self.T1_P1,
            project=self.P1,
        )
        self.assertEqual(
            result,
            "unregistered",
            f"Self-removal must return ('unregistered', None); got ({result!r}, {pid!r})",
        )
        self.assertIsNone(pid, f"pid must be None for a clean unregister; got {pid!r}")
        self.assertFalse(
            s.is_active(self.con, self.T1_P1),
            f"{self.T1_P1!r} must be inactive after self-unregister.",
        )

    def test_mainline_p1_unregisters_p1_track_succeeds(self):
        """Mainline - P1 may unregister Track 1 - P1 (same project).

        This is the same-project Mainline-remove-track path; must still work.
        Locked behaviour; kept as a guard.
        """
        result, pid = s.unregister(
            self.con,
            self.T1_P1,
            requester=self.ML_P1,
            project=self.P1,
        )
        self.assertEqual(
            result,
            "unregistered",
            f"Mainline-P1 removing Track-P1 must return ('unregistered', None); "
            f"got ({result!r}, {pid!r})",
        )
        self.assertFalse(
            s.is_active(self.con, self.T1_P1),
            f"{self.T1_P1!r} must be inactive after Mainline-P1 unregisters it.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
