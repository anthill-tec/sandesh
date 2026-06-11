"""test_tombstone_read_rules.py — RED tests for CR-SAN-024 Cycle 3.

Covers §S2 (read rules + DRIFT-3 mechanism) + AC5:

  inbox/fetch/unread_to: rows whose SENDER's project is tombstoned are filtered out
  entirely (no placeholder; previously-unread cross-project mail from a now-tombstoned
  project never surfaces, is NOT marked read, simply invisible). Sender project
  resolution: _address_project (suffix fallback — the address rows are purged) checked
  against the tombstoned set (SELECT project_id FROM project WHERE state='tombstoned').

  unread_to (the WAKE filter): same rule — a watcher must not wake for invisible mail.

  Archived projects' traffic: fully visible everywhere (explicit contrast tests).

  thread: chain nodes whose sender's project is tombstoned are REPLACED by a warning
  entry — the rendered chain contains the exact line
  `incomplete chain — message(s) removed (project tombstoned)`.
  A thread whose REQUESTED msg_id itself is from a tombstoned project: the CLI prints
  the warning line, exit 0 (LEAST-SURPRISING pick: the cross-project row survived the
  purge but the sender is invisible; rather than an opaque "no such message" the user
  gets the actionable warning).

Expected RED:
  All tests FAIL because inbox/fetch/unread_to currently return tombstoned-project
  traffic (no filter exists), and thread() currently returns the full sqlite row
  (no warning substitution exists). These are BEHAVIOUR failures, not collection errors.

Thread-root pick (pinned in test_thread_on_p1_reply_renders_warning_for_p2_parent and
test_cmd_thread_cli_output_contains_warning): the CLI cmd_thread prints the warning
line and exits 0 for a chain whose root msg is from a tombstoned project.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_tombstone_read_rules --agent red-cr024-c3
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

    setUp provisions:
      P1  — active throughout (the reader / observer)
      P2  — tombstoned (the source of hidden traffic)
      P3  — archived only (the contrast project: traffic fully visible)

    Pre-tombstone message matrix (built in setUp):
      mid_p2_to_p1_unread  — P2→P1 cross message; P1 has NOT read it at tombstone time
      mid_p2_to_p1_read    — P2→P1 cross message; P1 HAS already read it
      mid_p1_reply_to_p2   — P1 reply to mid_p2_to_p1_unread (parent = P2 cross msg)
      mid_p3_to_p1         — P3→P1 cross message (archived contrast; stays visible)

    Then: archive(P2) + tombstone_project(P2, ADMIN); archive(P3) only.
    """

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1"
    T1_P1 = "Track 1 - P1"
    ML_P2 = "Mainline - P2"
    T1_P2 = "Track 1 - P2"
    ML_P3 = "Mainline - P3"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-read-rules-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision all three projects.
        s.setup(self.P1)
        s.setup(self.P2)
        s.setup(self.P3)

        self.con = s.connect()

        # Register addresses.
        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)
        s.register(self.con, self.T1_P2, kind="track",    project=self.P2)
        s.register(self.con, self.ML_P3, kind="mainline", project=self.P3)

        s.assign_admin(self.con, self.ADMIN)

        # Grant P2 + P1 cross-project access so the sends below work.
        s.grant_xproj(self.con, self.P1, self.ADMIN)
        s.grant_xproj(self.con, self.P2, self.ADMIN)
        s.grant_xproj(self.con, self.P3, self.ADMIN)

        # --- Build pre-tombstone messages ---

        # P2→P1: the key unread cross-project message (ML_P1 is 'to').
        self.mid_p2_to_p1_unread = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="P2 unread cross-project ping",
        )

        # P2→P1: a cross-project message P1 has already read.
        self.mid_p2_to_p1_read = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="P2 already-read cross-project ping",
        )
        # Mark this one read immediately.
        s.mark_read(self.con, self.ML_P1, [self.mid_p2_to_p1_read])

        # P1 reply to the unread P2 message (creates a thread through P2's message).
        self.mid_p1_reply_to_p2 = s.reply(
            self.con, s.store_dir(self.P1),
            parent_id=self.mid_p2_to_p1_unread,
            from_addr=self.ML_P1,
            subject="Re: P2 unread cross-project ping",
        )

        # P3→P1: archived contrast (should remain fully visible after P3 archived).
        self.mid_p3_to_p1 = s.send(
            self.con, s.store_dir(self.P3),
            from_addr=self.ML_P3,
            to=[self.ML_P1],
            subject="P3 archived contrast ping",
        )

        # --- Lifecycle transitions ---
        # Archive then tombstone P2.
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)
        s.tombstone_project(self.con, self.P2, self.ADMIN, wait_secs=0.1)

        # Archive P3 only (stays archived — contrast case).
        s.archive(self.con, self.P3, self.ML_P3, wait_secs=0.1)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_at_for(self, message_id, recipient):
        """Raw read_at value from message_recipient for (message_id, recipient)."""
        row = self.con.execute(
            "SELECT read_at FROM message_recipient WHERE message_id=? AND recipient=?",
            (message_id, recipient),
        ).fetchone()
        return row["read_at"] if row else None

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
# Part 1 — inbox() filtering
# ---------------------------------------------------------------------------

class InboxFiltersTombstonedTrafficTest(_TempDataHome):
    """inbox() must return zero rows whose sender's project is tombstoned.

    AC5: P1's inbox shows NO messages to/from P2 addresses (including
    previously-unread ones) after P2 is tombstoned.

    RED: inbox() currently has no filter — it returns all rows regardless of
    the sender project's lifecycle state.
    """

    def test_inbox_unread_only_shows_zero_p2_rows(self):
        """inbox(ML_P1, unread_only=True) must return zero rows from P2 senders.

        The key case: mid_p2_to_p1_unread was unread at tombstone time and must
        now be invisible (not surfaced, not marked read — simply absent).

        RED: today inbox returns mid_p2_to_p1_unread in the results.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=True)
        from_addresses = [r["from_addr"] for r in rows]

        # No P2 addresses must appear as senders.
        p2_rows = [a for a in from_addresses if a in (self.ML_P2, self.T1_P2)]
        self.assertEqual(
            len(p2_rows), 0,
            f"inbox(ML_P1, unread_only=True) must contain ZERO rows from P2 senders "
            f"after P2 is tombstoned; got P2 senders: {p2_rows!r} "
            f"(all from_addrs: {from_addresses!r})",
        )

    def test_inbox_all_messages_shows_zero_p2_rows(self):
        """inbox(ML_P1, unread_only=False) must also return zero rows from P2 senders.

        Even already-read P2→P1 traffic (mid_p2_to_p1_read) must be hidden after
        tombstone. The filter applies regardless of the unread_only flag.

        RED: today inbox(unread_only=False) returns both P2 messages.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False)
        from_addresses = [r["from_addr"] for r in rows]

        p2_rows = [a for a in from_addresses if a in (self.ML_P2, self.T1_P2)]
        self.assertEqual(
            len(p2_rows), 0,
            f"inbox(ML_P1, unread_only=False) must contain ZERO rows from P2 senders "
            f"after P2 is tombstoned; got P2 senders: {p2_rows!r}",
        )

    def test_inbox_p3_archived_row_present(self):
        """inbox(ML_P1) must still show the P3 (archived) message.

        Archived projects' traffic is fully visible (AC5 contrast: 'messages
        involving an *archived* P3 still display').

        RED: this test may pass today (the row is visible), but it must stay
        green after the P2 filter is added. It is a correctness anchor.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=True)
        ids = [r["id"] for r in rows]

        self.assertIn(
            self.mid_p3_to_p1, ids,
            f"inbox(ML_P1) must contain the P3 (archived) message "
            f"(mid_p3_to_p1={self.mid_p3_to_p1}); "
            f"got ids: {ids!r}",
        )

    def test_inbox_exactly_the_p3_row_in_unread(self):
        """After tombstoning P2 and archiving P3, P1's unread inbox has exactly
        the one P3 message (mid_p3_to_p1).

        Positive + negative combined: the P2 unread message is absent, the P3
        archived message is present, no spurious rows.

        RED: today the P2 unread message also appears.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=True)
        ids = [r["id"] for r in rows]

        # Exactly the P3 message must be in the unread inbox.
        self.assertIn(
            self.mid_p3_to_p1, ids,
            f"mid_p3_to_p1={self.mid_p3_to_p1} must be in unread inbox; "
            f"got: {ids!r}",
        )
        # The P2 unread message must not be present.
        self.assertNotIn(
            self.mid_p2_to_p1_unread, ids,
            f"mid_p2_to_p1_unread={self.mid_p2_to_p1_unread} must NOT be in "
            f"unread inbox after P2 tombstoned; got: {ids!r}",
        )
        # Exactly 1 unread row (the P3 one).
        self.assertEqual(
            len(ids), 1,
            f"P1's unread inbox must have exactly 1 row (the P3 message); "
            f"got {len(ids)}: {ids!r}",
        )


# ---------------------------------------------------------------------------
# Part 2 — fetch() filtering + read_at NOT modified
# ---------------------------------------------------------------------------

class FetchFiltersTombstonedTrafficTest(_TempDataHome):
    """fetch() must omit rows from tombstoned-project senders, AND must NOT
    mark those hidden rows read (read_at stays NULL).

    AC5: P1's fetch shows NO messages to/from P2 addresses; messages involving
    archived P3 still display.

    RED: fetch() calls inbox() which has no filter — P2 messages surface and
    get marked read.
    """

    def test_fetch_returns_zero_p2_items(self):
        """fetch(ML_P1) must return zero items from P2 senders.

        RED: today fetch returns mid_p2_to_p1_unread (the only unread P2→P1
        message) in the item list.
        """
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True)
        p2_items = [it for it in items if it["from"] in (self.ML_P2, self.T1_P2)]

        self.assertEqual(
            len(p2_items), 0,
            f"fetch(ML_P1) must return ZERO items from P2 senders after P2 tombstoned; "
            f"got {len(p2_items)}: {[it['from'] for it in p2_items]!r}",
        )

    def test_fetch_does_not_mark_p2_message_read(self):
        """fetch(ML_P1, mark=True) must NOT set read_at on the hidden P2 message.

        The spec: 'previously-unread cross mail from a now-tombstoned project
        never surfaces, is NOT marked read, simply invisible.'

        RED: today fetch returns and marks mid_p2_to_p1_unread — read_at becomes
        non-NULL after the call.
        """
        # Call fetch with mark=True — the hidden P2 message must not be touched.
        s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True)

        raw_read_at = self._read_at_for(self.mid_p2_to_p1_unread, self.ML_P1)
        self.assertIsNone(
            raw_read_at,
            f"read_at for (mid_p2_to_p1_unread={self.mid_p2_to_p1_unread}, ML_P1) "
            f"must remain NULL after fetch — the tombstoned-sender message must not "
            f"be marked read; got read_at={raw_read_at!r}",
        )

    def test_fetch_returns_p3_archived_item(self):
        """fetch(ML_P1) must return the P3 (archived) message normally.

        Archived projects' traffic is fully visible (AC5 contrast).

        RED: this test may pass today, but it must remain correct after the
        P2 filter is wired in. Correctness anchor.
        """
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=False)
        ids = [it["id"] for it in items]

        self.assertIn(
            self.mid_p3_to_p1, ids,
            f"fetch(ML_P1) must return the P3 (archived) message "
            f"(mid_p3_to_p1={self.mid_p3_to_p1}); got ids: {ids!r}",
        )

    def test_fetch_exactly_p3_item_in_results(self):
        """fetch(ML_P1) must return exactly the one P3 item (zero P2, zero spurious).

        Positive + bound: after tombstoning P2 and archiving P3, only the P3
        unread message survives.

        RED: today mid_p2_to_p1_unread also appears.
        """
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=False)
        ids = [it["id"] for it in items]

        self.assertIn(
            self.mid_p3_to_p1, ids,
            f"mid_p3_to_p1={self.mid_p3_to_p1} must be in fetch results; "
            f"got: {ids!r}",
        )
        self.assertNotIn(
            self.mid_p2_to_p1_unread, ids,
            f"mid_p2_to_p1_unread={self.mid_p2_to_p1_unread} must NOT appear "
            f"in fetch results after P2 tombstoned; got: {ids!r}",
        )
        self.assertEqual(
            len(ids), 1,
            f"fetch(ML_P1) must return exactly 1 item (P3 message); "
            f"got {len(ids)}: {ids!r}",
        )


# ---------------------------------------------------------------------------
# Part 3 — unread_to() filtering (WAKE suppression)
# ---------------------------------------------------------------------------

class UnreadToFiltersTombstonedTrafficTest(_TempDataHome):
    """unread_to() must exclude message ids whose sender's project is tombstoned.

    The spec: 'unread_to(con, recipient) (the WAKE filter) must apply the same
    rule — a watcher must not wake for invisible mail.'

    RED: unread_to() currently has no filter — it returns mid_p2_to_p1_unread.
    """

    def test_unread_to_excludes_p2_message_id(self):
        """unread_to(ML_P1) must NOT include mid_p2_to_p1_unread.

        The P2 message is a 'to' role and was unread at tombstone time, so it
        would normally appear in unread_to(). After tombstone it must be excluded
        — a watcher for ML_P1 must not be woken by invisible mail.

        RED: today unread_to returns mid_p2_to_p1_unread.
        """
        ids = s.unread_to(self.con, self.ML_P1)

        self.assertNotIn(
            self.mid_p2_to_p1_unread, ids,
            f"unread_to(ML_P1) must NOT contain mid_p2_to_p1_unread="
            f"{self.mid_p2_to_p1_unread} after P2 tombstoned; "
            f"got ids: {ids!r}",
        )

    def test_unread_to_includes_p3_message_id(self):
        """unread_to(ML_P1) must include mid_p3_to_p1 (P3 is only archived).

        Archived projects' traffic is fully visible including the wake path.

        RED: this test may pass today (P3's message already in unread_to), but
        must remain correct after the P2 filter is added.
        """
        ids = s.unread_to(self.con, self.ML_P1)

        self.assertIn(
            self.mid_p3_to_p1, ids,
            f"unread_to(ML_P1) must include mid_p3_to_p1={self.mid_p3_to_p1} "
            f"(P3 is archived, not tombstoned); got ids: {ids!r}",
        )

    def test_unread_to_exactly_p3_id_only(self):
        """unread_to(ML_P1) must contain exactly 1 id: mid_p3_to_p1.

        After tombstoning P2 (removes its unread 'to' message from the wake filter)
        and archiving P3 (traffic visible), only P3's unread-to message survives.

        RED: today both mid_p2_to_p1_unread and mid_p3_to_p1 appear.
        """
        ids = s.unread_to(self.con, self.ML_P1)

        self.assertEqual(
            len(ids), 1,
            f"unread_to(ML_P1) must contain exactly 1 id (mid_p3_to_p1); "
            f"got {len(ids)}: {ids!r}",
        )
        self.assertEqual(
            ids[0], self.mid_p3_to_p1,
            f"unread_to(ML_P1)[0] must be mid_p3_to_p1={self.mid_p3_to_p1}; "
            f"got {ids[0]!r}",
        )


# ---------------------------------------------------------------------------
# Part 4 — thread() warning for tombstoned chain nodes
# ---------------------------------------------------------------------------

class ThreadWarningForTombstonedNodesTest(_TempDataHome):
    """thread() must replace chain nodes whose sender's project is tombstoned
    with a warning entry containing the exact text:
      incomplete chain — message(s) removed (project tombstoned)

    AC5: 'thread on a P1 chain that passed through P2 renders
    incomplete chain — message(s) removed (project tombstoned).'

    The chain is:
      mid_p2_to_p1_unread  (P2 sender — tombstoned → WARNING node)
        └─ mid_p1_reply_to_p2  (P1 sender — visible)

    thread(con, mid_p1_reply_to_p2) must walk to the root (mid_p2_to_p1_unread)
    and replace it with a warning entry.

    Thread-root pick (pinned): thread(con, mid_p2_to_p1_unread) itself — the
    cross-project row survived the tombstone purge (it was a cross-project message
    so it was NOT deleted by tombstone_project), but its sender's project is
    tombstoned. The function should return a list containing only the warning
    entry (not the raw message row), so cli cmd_thread prints the warning and
    exits 0.

    RED: today thread() returns the raw sqlite rows — no warning substitution.
    """

    WARNING_TEXT = "incomplete chain — message(s) removed (project tombstoned)"

    def test_thread_on_p1_reply_renders_warning_for_p2_parent(self):
        """thread(mid_p1_reply_to_p2) must contain the warning text for the P2 node.

        The chain has two nodes: the P2 root (tombstoned — warning) and the P1
        reply (visible). The returned list must contain at least one entry that
        signals the warning. The warning entry must include the exact text
        'incomplete chain — message(s) removed (project tombstoned)'.

        RED: today thread() returns both raw rows, no warning in sight.
        """
        chain = s.thread(self.con, self.mid_p1_reply_to_p2)

        # The chain must not be empty.
        self.assertGreater(
            len(chain), 0,
            "thread(mid_p1_reply_to_p2) must return a non-empty chain",
        )

        # Collect all string values across the chain entries to search for warning.
        all_text = " ".join(
            str(v) for entry in chain
            for v in (entry.values() if hasattr(entry, "values") else entry)
            if v is not None
        )
        self.assertIn(
            self.WARNING_TEXT, all_text,
            f"thread(mid_p1_reply_to_p2) must contain the warning text "
            f"{self.WARNING_TEXT!r} for the tombstoned P2 root node; "
            f"got chain with values: {all_text!r}",
        )

    def test_thread_p1_reply_node_still_visible(self):
        """thread(mid_p1_reply_to_p2) must still contain the P1 reply subject.

        The P1 reply node (mid_p1_reply_to_p2) is from a live project and must
        render normally. The warning replaces only the P2 root node.

        RED: today both raw nodes appear without any warning — the P1 node is
        present, but the test is still RED because the warning assertion fails
        (test_thread_on_p1_reply_renders_warning_for_p2_parent drives the RED).
        This test pins that the visible node survives the filter.
        """
        chain = s.thread(self.con, self.mid_p1_reply_to_p2)
        all_text = " ".join(
            str(v) for entry in chain
            for v in (entry.values() if hasattr(entry, "values") else entry)
            if v is not None
        )
        self.assertIn(
            "Re: P2 unread cross-project ping", all_text,
            f"thread(mid_p1_reply_to_p2) must still contain the P1 reply subject; "
            f"got chain text: {all_text!r}",
        )

    def test_thread_root_is_tombstoned_sender_returns_warning(self):
        """thread(mid_p2_to_p1_unread) — the requested id is the cross-project
        message whose sender's project is tombstoned.

        The cross-project row survived the purge. thread() must return a list
        whose entries signal the warning rather than the raw P2 row.

        Thread-root pick (pinned): return a list with only the warning entry —
        the CLI then prints the warning line and exits 0, which is less surprising
        than an opaque 'no such message' error for a row that still exists in the DB.

        RED: today thread() returns the raw sqlite row for this message.
        """
        chain = s.thread(self.con, self.mid_p2_to_p1_unread)

        self.assertGreater(
            len(chain), 0,
            "thread(mid_p2_to_p1_unread) must return a non-empty list (warning entry), "
            "not an empty list",
        )
        all_text = " ".join(
            str(v) for entry in chain
            for v in (entry.values() if hasattr(entry, "values") else entry)
            if v is not None
        )
        self.assertIn(
            self.WARNING_TEXT, all_text,
            f"thread(mid_p2_to_p1_unread) must contain the warning text for the "
            f"tombstoned sender; got chain text: {all_text!r}",
        )


# ---------------------------------------------------------------------------
# Part 5 — CLI cmd_thread output contains warning line
# ---------------------------------------------------------------------------

class CmdThreadCliOutputTest(_TempDataHome):
    """cli cmd_thread output must contain the exact warning line for tombstoned nodes.

    AC5: 'render incomplete chain — message(s) removed (project tombstoned).'

    RED: today cmd_thread calls thread() and prints raw sqlite rows — no warning.
    """

    WARNING_TEXT = "incomplete chain — message(s) removed (project tombstoned)"

    def test_cmd_thread_cli_output_contains_warning(self):
        """sandesh thread --id <p1_reply> must print the warning line in output.

        The P1 reply's thread traverses the P2 root (tombstoned). The CLI must
        print the warning line for the hole. Exit code 0 (partial chain rendered,
        not an error).

        RED: today the CLI renders both raw rows without any warning text.
        """
        rc, out, err = self._run_cli([
            "--project", self.P1,
            "thread", "--id", str(self.mid_p1_reply_to_p2),
        ])

        self.assertEqual(
            rc, 0,
            f"cmd_thread must exit 0 for a chain with a tombstoned hole; "
            f"got rc={rc!r} err={err!r}",
        )
        self.assertIn(
            self.WARNING_TEXT, out,
            f"cmd_thread output must contain the warning line "
            f"{self.WARNING_TEXT!r}; "
            f"got stdout:\n{out!r}",
        )

    def test_cmd_thread_root_tombstoned_prints_warning_exits_0(self):
        """sandesh thread --id <p2_cross_msg> — the root msg is from a tombstoned project.

        Thread-root pick (pinned): the CLI prints the warning line and exits 0.
        The cross-project row survived the purge so msg_id exists in the DB, but
        its sender's project is tombstoned. The least-surprising behaviour is a
        warning, not an opaque error.

        RED: today cmd_thread renders the raw P2 row without any warning.
        """
        rc, out, err = self._run_cli([
            "--project", self.P1,
            "thread", "--id", str(self.mid_p2_to_p1_unread),
        ])

        self.assertEqual(
            rc, 0,
            f"cmd_thread must exit 0 when the requested msg is from a tombstoned "
            f"project; got rc={rc!r} err={err!r}",
        )
        self.assertIn(
            self.WARNING_TEXT, out,
            f"cmd_thread output must contain warning {self.WARNING_TEXT!r} when "
            f"the root is a tombstoned-project message; "
            f"got stdout:\n{out!r}",
        )


# ---------------------------------------------------------------------------
# Part 6 — Archived-everything contrast (before tombstoning P2)
# ---------------------------------------------------------------------------

class ArchivedOnlyContrastTest(unittest.TestCase):
    """Contrast fixture: P2 is ARCHIVED only (not tombstoned). All P2→P1 traffic
    must be fully visible in inbox/fetch/unread_to.

    This is the AC5 explicit contrast: 'messages involving an *archived* P3 still
    display'. Mirrored here with P2 in the archived state to verify the filter
    does NOT fire for archived projects — only tombstoned ones.
    """

    P1 = "P1contrast"
    P2 = "P2contrast"
    ADMIN = "ops"

    ML_P1 = "Mainline - P1contrast"
    ML_P2 = "Mainline - P2contrast"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-archived-contrast-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()

        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)

        s.assign_admin(self.con, self.ADMIN)
        s.grant_xproj(self.con, self.P1, self.ADMIN)
        s.grant_xproj(self.con, self.P2, self.ADMIN)

        # P2→P1 cross message: UNREAD by P1.
        self.mid_p2_to_p1 = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="archived P2 cross ping",
        )

        # Archive P2 only — do NOT tombstone.
        s.archive(self.con, self.P2, self.ML_P2, wait_secs=0.1)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inbox_shows_archived_p2_message(self):
        """inbox(ML_P1) must show the P2→P1 message when P2 is only archived.

        Archived traffic is fully visible — the filter must not fire.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=True)
        ids = [r["id"] for r in rows]

        self.assertIn(
            self.mid_p2_to_p1, ids,
            f"inbox(ML_P1) must contain the archived-P2 message (id={self.mid_p2_to_p1}) "
            f"when P2 is only archived; got ids: {ids!r}",
        )

    def test_fetch_shows_archived_p2_message(self):
        """fetch(ML_P1) must return the P2→P1 message when P2 is only archived."""
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=False)
        ids = [it["id"] for it in items]

        self.assertIn(
            self.mid_p2_to_p1, ids,
            f"fetch(ML_P1) must return the archived-P2 message (id={self.mid_p2_to_p1}) "
            f"when P2 is only archived; got ids: {ids!r}",
        )

    def test_unread_to_includes_archived_p2_message(self):
        """unread_to(ML_P1) must include the P2→P1 message id when P2 is only archived."""
        ids = s.unread_to(self.con, self.ML_P1)

        self.assertIn(
            self.mid_p2_to_p1, ids,
            f"unread_to(ML_P1) must include archived-P2 message id={self.mid_p2_to_p1} "
            f"when P2 is only archived; got ids: {ids!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
