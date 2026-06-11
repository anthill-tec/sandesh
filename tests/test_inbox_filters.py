"""test_inbox_filters.py — RED tests for CR-SAN-026 Cycle 1.

Covers §S1/§S2 + AC1–AC5 + AC7 (lib layer only; CLI flags are C2):

  inbox(con, recipient, unread_only=True, *, sender=None, sender_project=None,
        kind=None, since=None, until=None, subject_like=None)
  fetch(con, store, recipient, mark=True, *, <same filter params>)
  unread_to: UNCHANGED signature/behaviour (AC7 pin).

Expected RED:
  AC1–AC5: TypeError — inbox()/fetch() do not yet accept filter keyword params.
  AC7: likely GREEN (pinning test — unread_to unchanged); noted in each method.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_inbox_filters --agent red-cr026-c1
"""

import inspect
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions three active projects + one tombstoned project + one
    archived project, with cross-project grants so a P1 recipient can hold
    mail from P1, P2, P3, and P_tomb.

    Projects:
      P1       — active; the primary reader/recipient
      P2       — active; cross-project sender (the headline filter target)
      P3       — active; second cross-project sender (composition tests)
      P_arch   — archived only; contrast: traffic filterable normally
      P_tomb   — tombstoned; contrast: traffic invisible

    Addresses registered:
      ML_P1, T1_P1  in P1
      ML_P2         in P2
      ML_P3         in P3
      ML_Parch      in P_arch
      ML_Ptomb      in P_tomb

    Admin 'ops' with grants on P1, P2, P3, P_arch, P_tomb enables the
    cross-project sends.

    Message matrix seeded in setUp (all unread unless noted):
      mid_p1_req   — P1→P1, kind='request',  subject='Gate review'
      mid_p2_fyi   — P2→P1, kind='fyi',      subject='P2 status update'
      mid_p2_req   — P2→P1, kind='request',  subject='P2 request task'
      mid_p3_fyi   — P3→P1, kind='fyi',      subject='P3 hello'
      mid_arch     — P_arch→P1 (archived project; sent before archiving)
      mid_tomb     — P_tomb→P1 (tombstoned project; invisible)

    Explicit created_at timestamps (raw UPDATE after each send):
      mid_p1_req   → '2026-06-01 08:00:00'
      mid_p2_fyi   → '2026-06-02 10:00:00'
      mid_p2_req   → '2026-06-03 12:00:00'
      mid_p3_fyi   → '2026-06-04 14:00:00'
      mid_arch     → '2026-06-05 16:00:00'
      mid_tomb     → '2026-06-06 18:00:00'   (will be invisible after tombstone)
    """

    P1      = "P1"
    P2      = "P2"
    P3      = "P3"
    P_arch  = "Parch"
    P_tomb  = "Ptomb"
    ADMIN   = "ops"

    ML_P1    = "Mainline - P1"
    T1_P1    = "Track 1 - P1"
    ML_P2    = "Mainline - P2"
    ML_P3    = "Mainline - P3"
    ML_Parch = "Mainline - Parch"
    ML_Ptomb = "Mainline - Ptomb"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-inbox-filters-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision all projects.
        s.setup(self.P1)
        s.setup(self.P2)
        s.setup(self.P3)
        s.setup(self.P_arch)
        s.setup(self.P_tomb)

        self.con = s.connect()

        # Register addresses.
        s.register(self.con, self.ML_P1,    kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1,    kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2,    kind="mainline", project=self.P2)
        s.register(self.con, self.ML_P3,    kind="mainline", project=self.P3)
        s.register(self.con, self.ML_Parch, kind="mainline", project=self.P_arch)
        s.register(self.con, self.ML_Ptomb, kind="mainline", project=self.P_tomb)

        # Admin + cross-project grants (enables all cross-project sends).
        s.assign_admin(self.con, self.ADMIN)
        s.grant_xproj(self.con, self.P1,     self.ADMIN)
        s.grant_xproj(self.con, self.P2,     self.ADMIN)
        s.grant_xproj(self.con, self.P3,     self.ADMIN)
        s.grant_xproj(self.con, self.P_arch, self.ADMIN)
        s.grant_xproj(self.con, self.P_tomb, self.ADMIN)

        # --- Seed messages ---

        # P1-internal: T1_P1 → ML_P1 (kind='request', subject 'Gate review').
        # Using T1_P1 as sender so ML_P1 is the recipient (sender is excluded from
        # its own recipient list, so ML_P1 cannot self-send to itself).
        self.mid_p1_req = s.send(
            self.con, s.store_dir(self.P1),
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject="Gate review",
            kind="request",
        )
        self._stamp(self.mid_p1_req, "2026-06-01 08:00:00")

        # P2 → P1: kind='fyi'
        self.mid_p2_fyi = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="P2 status update",
            kind="fyi",
        )
        self._stamp(self.mid_p2_fyi, "2026-06-02 10:00:00")

        # P2 → P1: kind='request'
        self.mid_p2_req = s.send(
            self.con, s.store_dir(self.P2),
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="P2 request task",
            kind="request",
        )
        self._stamp(self.mid_p2_req, "2026-06-03 12:00:00")

        # P3 → P1: kind='fyi'
        self.mid_p3_fyi = s.send(
            self.con, s.store_dir(self.P3),
            from_addr=self.ML_P3,
            to=[self.ML_P1],
            subject="P3 hello",
            kind="fyi",
        )
        self._stamp(self.mid_p3_fyi, "2026-06-04 14:00:00")

        # P_arch → P1 (sent before archiving).
        self.mid_arch = s.send(
            self.con, s.store_dir(self.P_arch),
            from_addr=self.ML_Parch,
            to=[self.ML_P1],
            subject="Archived project hello",
            kind="fyi",
        )
        self._stamp(self.mid_arch, "2026-06-05 16:00:00")

        # P_tomb → P1 (sent before tombstoning).
        self.mid_tomb = s.send(
            self.con, s.store_dir(self.P_tomb),
            from_addr=self.ML_Ptomb,
            to=[self.ML_P1],
            subject="Tombstoned project hello",
            kind="fyi",
        )
        self._stamp(self.mid_tomb, "2026-06-06 18:00:00")

        # --- Lifecycle transitions ---
        # Archive P_arch (contrast: traffic still filterable).
        s.archive(self.con, self.P_arch, self.ML_Parch, wait_secs=0.1)

        # Archive then tombstone P_tomb (traffic becomes invisible).
        s.archive(self.con, self.P_tomb, self.ML_Ptomb, wait_secs=0.1)
        s.tombstone_project(self.con, self.P_tomb, self.ADMIN, wait_secs=0.1)

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
        """Raw read_at value from message_recipient."""
        row = self.con.execute(
            "SELECT read_at FROM message_recipient WHERE message_id=? AND recipient=?",
            (message_id, recipient),
        ).fetchone()
        return row["read_at"] if row else None


# ---------------------------------------------------------------------------
# AC1 — sender_project filter
# ---------------------------------------------------------------------------

class SenderProjectFilterTest(_TempDataHome):
    """AC1: inbox(con, r, sender_project='P2') returns ONLY rows whose sender's
    project is P2; sender_project='P1' only P1 rows; a project with no matching
    mail returns [].

    Expected RED: TypeError — inbox() does not yet accept sender_project kwarg.
    """

    def test_sender_project_p2_returns_only_p2_rows(self):
        """inbox(sender_project='P2') must return exactly the P2-sender messages.

        With ML_P1's inbox holding mail from P1, P2, P3 (and P_arch), filtering
        by sender_project='P2' must return only mid_p2_fyi and mid_p2_req.

        RED: TypeError on the sender_project kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project=self.P2)
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_fyi, ids,
                      f"mid_p2_fyi must be in sender_project=P2 results; got {ids!r}")
        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req must be in sender_project=P2 results; got {ids!r}")
        # Exactly 2 rows — no P1, P3, P_arch rows.
        self.assertEqual(len(ids), 2,
                         f"sender_project='P2' must return exactly 2 rows; got {len(ids)}: {ids!r}")

    def test_sender_project_p1_returns_only_p1_rows(self):
        """inbox(sender_project='P1') must return only the P1-internal message.

        mid_p1_req is sent by T1_P1 (project P1) to ML_P1.
        RED: TypeError on the sender_project kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project=self.P1)
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p1_req, ids,
                      f"mid_p1_req must be in sender_project=P1 results; got {ids!r}")
        self.assertEqual(len(ids), 1,
                         f"sender_project='P1' must return exactly 1 row; got {len(ids)}: {ids!r}")

    def test_sender_project_uninvolved_returns_empty(self):
        """inbox(sender_project='Nope') for a project with no sent mail returns [].

        RED: TypeError on the sender_project kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project="Nope")
        self.assertEqual(rows, [],
                         f"sender_project='Nope' must return []; got {rows!r}")


# ---------------------------------------------------------------------------
# AC2 — each remaining filter
# ---------------------------------------------------------------------------

class SenderExactMatchFilterTest(_TempDataHome):
    """AC2 — sender exact-match filter.

    inbox(sender='Mainline - P2') must return only rows with from_addr='Mainline - P2'.
    RED: TypeError on the sender kwarg.
    """

    def test_sender_exact_match_returns_only_that_sender(self):
        """inbox(sender='Mainline - P2') returns only ML_P2 rows (mid_p2_fyi + mid_p2_req).

        RED: TypeError on sender kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender=self.ML_P2)
        ids = [r["id"] for r in rows]
        from_addrs = [r["from_addr"] for r in rows]

        self.assertEqual(len(ids), 2,
                         f"sender='Mainline - P2' must return 2 rows; got {len(ids)}: {ids!r}")
        self.assertTrue(all(a == self.ML_P2 for a in from_addrs),
                        f"all from_addr must equal 'Mainline - P2'; got {from_addrs!r}")

    def test_sender_exact_match_excludes_other_senders(self):
        """inbox(sender='Mainline - P2') must NOT include rows from T1_P1 (P1) or ML_P3.

        mid_p1_req has from_addr=T1_P1; mid_p3_fyi has from_addr=ML_P3 — neither
        must appear when filtering by sender=ML_P2.
        RED: TypeError on sender kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender=self.ML_P2)
        ids = [r["id"] for r in rows]

        self.assertNotIn(self.mid_p1_req, ids,
                         f"mid_p1_req (T1_P1 sender, P1) must not appear with sender=ML_P2 filter")
        self.assertNotIn(self.mid_p3_fyi, ids,
                         f"mid_p3_fyi (P3 sender) must not appear with sender=ML_P2 filter")


class KindFilterTest(_TempDataHome):
    """AC2 — kind exact-match filter; NULL-kind rows excluded when kind is set.

    RED: TypeError on the kind kwarg.
    """

    def test_kind_request_returns_only_request_rows(self):
        """inbox(kind='request') returns only kind='request' messages.

        mid_p1_req and mid_p2_req have kind='request'; mid_p2_fyi/mid_p3_fyi
        have kind='fyi'. mid_arch and mid_tomb have kind='fyi'.
        Filter must return only the two request rows (mid_tomb invisible).

        RED: TypeError on kind kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False, kind="request")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p1_req, ids,
                      f"mid_p1_req must be in kind='request' results; got {ids!r}")
        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req must be in kind='request' results; got {ids!r}")
        self.assertEqual(len(ids), 2,
                         f"kind='request' must return exactly 2 rows; got {len(ids)}: {ids!r}")

    def test_kind_fyi_excludes_request_rows(self):
        """inbox(kind='fyi') must NOT include request-kind rows.

        RED: TypeError on kind kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False, kind="fyi")
        ids = [r["id"] for r in rows]

        self.assertNotIn(self.mid_p1_req, ids,
                         f"mid_p1_req (kind=request) must not appear with kind='fyi' filter")
        self.assertNotIn(self.mid_p2_req, ids,
                         f"mid_p2_req (kind=request) must not appear with kind='fyi' filter")

    def test_kind_set_excludes_null_kind_rows(self):
        """When kind is set, rows with NULL kind must be excluded.

        Send a subject-only message (no kind) from T1_P1 to ML_P1, then filter by
        kind='fyi' — the null-kind row must NOT appear.

        RED: TypeError on kind kwarg.
        """
        mid_no_kind = s.send(
            self.con, s.store_dir(self.P1),
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject="No kind message",
            kind=None,
        )
        rows = s.inbox(self.con, self.ML_P1, unread_only=False, kind="fyi")
        ids = [r["id"] for r in rows]

        self.assertNotIn(mid_no_kind, ids,
                         f"NULL-kind message must not appear when kind='fyi' filter is set; "
                         f"got ids={ids!r}")


class SinceUntilFilterTest(_TempDataHome):
    """AC2 — since/until inclusive bounds on created_at.

    Timestamps (from setUp):
      mid_p1_req → 2026-06-01 08:00:00
      mid_p2_fyi → 2026-06-02 10:00:00
      mid_p2_req → 2026-06-03 12:00:00
      mid_p3_fyi → 2026-06-04 14:00:00
      mid_arch   → 2026-06-05 16:00:00
      mid_tomb   → 2026-06-06 18:00:00 (invisible)

    RED: TypeError on since/until kwargs.
    """

    def test_since_full_datetime_inclusive(self):
        """inbox(since='2026-06-03 12:00:00') must include mid_p2_req and later rows.

        Inclusive lower bound: the row stamped at exactly since must be returned.
        RED: TypeError on since kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       since="2026-06-03 12:00:00")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req (2026-06-03 12:00:00) must be >= since='2026-06-03 12:00:00'")
        self.assertIn(self.mid_p3_fyi, ids,
                      f"mid_p3_fyi (2026-06-04) must be >= since='2026-06-03 12:00:00'")
        self.assertIn(self.mid_arch, ids,
                      f"mid_arch (2026-06-05) must be >= since='2026-06-03 12:00:00'")
        # mid_p1_req and mid_p2_fyi are before the boundary.
        self.assertNotIn(self.mid_p1_req, ids,
                         "mid_p1_req (2026-06-01) must be < since='2026-06-03 12:00:00'")
        self.assertNotIn(self.mid_p2_fyi, ids,
                         "mid_p2_fyi (2026-06-02) must be < since='2026-06-03 12:00:00'")

    def test_since_date_only_inclusive(self):
        """inbox(since='2026-06-03') with date-only form must include rows on that date.

        RED: TypeError on since kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       since="2026-06-03")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req (2026-06-03 12:00:00) must be included by since='2026-06-03'")

    def test_until_full_datetime_inclusive(self):
        """inbox(until='2026-06-02 10:00:00') must include mid_p2_fyi (exactly at bound).

        Inclusive upper bound.
        RED: TypeError on until kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       until="2026-06-02 10:00:00")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p1_req, ids,
                      f"mid_p1_req (2026-06-01) must be <= until='2026-06-02 10:00:00'")
        self.assertIn(self.mid_p2_fyi, ids,
                      f"mid_p2_fyi (2026-06-02 10:00:00) must be included at until boundary")
        self.assertNotIn(self.mid_p2_req, ids,
                         "mid_p2_req (2026-06-03) must be > until='2026-06-02 10:00:00'")

    def test_until_date_only_normalizes_to_end_of_day(self):
        """inbox(until='2026-06-05') with date-only must include mid_arch at 16:00:00.

        The spec: date-only until normalizes to inclusive end-of-day — a lexicographic
        compare to '2026-06-05' would exclude the row at '2026-06-05 16:00:00', but
        the normalization must include it.

        RED: TypeError on until kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       until="2026-06-05")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_arch, ids,
                      f"mid_arch stamped '2026-06-05 16:00:00' must be INCLUDED by "
                      f"until='2026-06-05' (end-of-day normalization); got ids={ids!r}")

    def test_since_malformed_raises_value_error(self):
        """inbox(since='12/06/2026') (DD/MM/YYYY) must raise ValueError.

        RED: TypeError on since kwarg (before ValueError can be raised).
        """
        with self.assertRaises(ValueError):
            s.inbox(self.con, self.ML_P1, unread_only=False,
                    since="12/06/2026")

    def test_until_malformed_raises_value_error(self):
        """inbox(until='12/06/2026') (DD/MM/YYYY) must raise ValueError.

        RED: TypeError on until kwarg (before ValueError can be raised).
        """
        with self.assertRaises(ValueError):
            s.inbox(self.con, self.ML_P1, unread_only=False,
                    until="12/06/2026")

    def test_since_until_window_returns_exact_rows(self):
        """inbox(since='2026-06-02', until='2026-06-03 23:59:59') returns exactly
        mid_p2_fyi and mid_p2_req (both P2 rows in that window).

        RED: TypeError on since/until kwargs.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       since="2026-06-02", until="2026-06-03 23:59:59")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_fyi, ids)
        self.assertIn(self.mid_p2_req, ids)
        self.assertEqual(len(ids), 2,
                         f"since+until window must return exactly 2 rows; got {len(ids)}: {ids!r}")


class SubjectLikeFilterTest(_TempDataHome):
    """AC2 — subject_like case-insensitive substring; no regex/glob semantics.

    RED: TypeError on subject_like kwarg.
    """

    def test_subject_like_case_insensitive_match(self):
        """inbox(subject_like='gate') matches 'Gate review' (mid_p1_req).

        Case-insensitive substring: 'gate' matches the upper-case 'G' in 'Gate review'.
        RED: TypeError on subject_like kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       subject_like="gate")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p1_req, ids,
                      f"subject_like='gate' must match 'Gate review'; got ids={ids!r}")
        self.assertEqual(len(ids), 1,
                         f"subject_like='gate' must return exactly 1 row; got {len(ids)}: {ids!r}")

    def test_subject_like_uppercase_query_matches_lowercase_subject(self):
        """inbox(subject_like='STATUS') matches 'P2 status update' (case-insensitive).

        RED: TypeError on subject_like kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       subject_like="STATUS")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_fyi, ids,
                      f"subject_like='STATUS' must match 'P2 status update'; got ids={ids!r}")

    def test_subject_like_no_regex_semantics(self):
        """inbox(subject_like='g.te') must NOT match 'Gate review' — no regex.

        The '.' is treated literally, not as a wildcard.
        RED: TypeError on subject_like kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       subject_like="g.te")
        ids = [r["id"] for r in rows]

        self.assertNotIn(self.mid_p1_req, ids,
                         f"subject_like='g.te' must NOT match 'Gate review' (no regex); "
                         f"got ids={ids!r}")

    def test_subject_like_no_match_returns_empty(self):
        """inbox(subject_like='xyzzy') with no matching subjects returns [].

        RED: TypeError on subject_like kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       subject_like="xyzzy")
        self.assertEqual(rows, [],
                         f"subject_like='xyzzy' must return []; got {rows!r}")

    def test_subject_like_partial_substring_matches(self):
        """inbox(subject_like='hello') matches both 'P3 hello' and 'Tombstoned project hello'
        (but the tombstoned project's mail is invisible), so exactly 1 row returned.

        RED: TypeError on subject_like kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       subject_like="hello")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p3_fyi, ids,
                      f"mid_p3_fyi subject 'P3 hello' must match subject_like='hello'")
        # mid_tomb is from a tombstoned project — must be invisible even with subject match.
        self.assertNotIn(self.mid_tomb, ids,
                         f"mid_tomb (tombstoned project) must not appear even with matching subject")


# ---------------------------------------------------------------------------
# AC3 — composition + all-None reproduces unfiltered result
# ---------------------------------------------------------------------------

class FilterCompositionTest(_TempDataHome):
    """AC3: filters compose as intersection; all-None reproduces unfiltered result.

    RED: TypeError on filter kwargs.
    """

    def test_sender_project_and_kind_and_unread_only_intersect(self):
        """inbox(sender_project='P2', kind='request', unread_only=True) returns
        exactly the P2 request rows that are also unread.

        Only mid_p2_req qualifies (P2 sender, kind='request', unread).
        mid_p2_fyi is P2 but kind='fyi'; mid_p1_req is 'request' but P1.

        RED: TypeError on sender_project/kind kwargs.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=True,
                       sender_project=self.P2, kind="request")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req must satisfy sender_project=P2 + kind=request + unread")
        self.assertNotIn(self.mid_p2_fyi, ids,
                         f"mid_p2_fyi (kind=fyi) must not appear with kind='request' filter")
        self.assertNotIn(self.mid_p1_req, ids,
                         f"mid_p1_req (P1 sender) must not appear with sender_project='P2' filter")
        self.assertEqual(len(ids), 1,
                         f"intersection must return exactly 1 row; got {len(ids)}: {ids!r}")

    def test_all_none_filters_reproduces_unfiltered_result(self):
        """inbox(all filters=None) must return exactly the same rows as
        inbox(unread_only=False) with no filter kwargs.

        Row-for-row comparison (ids and order must match).
        RED: TypeError on filter kwargs.
        """
        # Baseline: plain inbox with no filters.
        baseline = s.inbox(self.con, self.ML_P1, unread_only=False)
        baseline_ids = [r["id"] for r in baseline]

        # Filtered call with all-None (keyword-only params explicitly None).
        filtered = s.inbox(self.con, self.ML_P1, unread_only=False,
                           sender=None, sender_project=None, kind=None,
                           since=None, until=None, subject_like=None)
        filtered_ids = [r["id"] for r in filtered]

        self.assertEqual(
            baseline_ids, filtered_ids,
            f"all-None filters must reproduce the unfiltered result row-for-row; "
            f"baseline={baseline_ids!r}, filtered={filtered_ids!r}",
        )

    def test_sender_project_and_since_compose(self):
        """inbox(sender_project='P2', since='2026-06-03') returns only the P2 rows
        on or after 2026-06-03 — i.e. mid_p2_req only (mid_p2_fyi is on 2026-06-02).

        RED: TypeError on filter kwargs.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project=self.P2, since="2026-06-03")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_p2_req, ids,
                      f"mid_p2_req must satisfy sender_project=P2 + since=2026-06-03")
        self.assertNotIn(self.mid_p2_fyi, ids,
                         f"mid_p2_fyi (2026-06-02) must be excluded by since=2026-06-03")
        self.assertEqual(len(ids), 1,
                         f"P2 rows since 2026-06-03 must be exactly 1; got {len(ids)}: {ids!r}")


# ---------------------------------------------------------------------------
# AC4 — tombstone/archive interplay
# ---------------------------------------------------------------------------

class TombstoneArchiveInterplayTest(_TempDataHome):
    """AC4: tombstoned-project mail invisible regardless of filters;
    archived-project mail filterable normally.

    RED: TypeError on filter kwargs (for the filter calls); tombstone-only checks
    may already pass (existing tombstone read rules).
    """

    def test_tombstoned_project_mail_invisible_with_sender_project_filter(self):
        """inbox(sender_project='Ptomb') must return [] — tombstoned project mail invisible.

        Even when explicitly filtering for the tombstoned project, its mail must not appear.
        RED: TypeError on sender_project kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project=self.P_tomb)
        self.assertEqual(rows, [],
                         f"inbox(sender_project='Ptomb') must return [] for tombstoned project; "
                         f"got {rows!r}")

    def test_tombstoned_project_mail_invisible_with_no_filter(self):
        """inbox(unread_only=False) must NOT include mid_tomb (tombstoned project).

        This is the existing tombstone read rule; confirmed here as a correctness anchor.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False)
        ids = [r["id"] for r in rows]

        self.assertNotIn(self.mid_tomb, ids,
                         f"mid_tomb from tombstoned project must not appear in inbox; "
                         f"got ids={ids!r}")

    def test_archived_project_mail_visible_with_sender_project_filter(self):
        """inbox(sender_project='Parch') returns mid_arch (archived project traffic visible).

        Archived projects' traffic is filterable normally (AC4 contrast).
        RED: TypeError on sender_project kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       sender_project=self.P_arch)
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_arch, ids,
                      f"mid_arch from archived project must appear with sender_project='Parch'; "
                      f"got ids={ids!r}")
        self.assertEqual(len(ids), 1,
                         f"sender_project='Parch' must return exactly 1 row; got {len(ids)}: {ids!r}")

    def test_archived_project_mail_visible_with_since_filter(self):
        """inbox(since='2026-06-05') includes mid_arch from the archived project.

        RED: TypeError on since kwarg.
        """
        rows = s.inbox(self.con, self.ML_P1, unread_only=False,
                       since="2026-06-05")
        ids = [r["id"] for r in rows]

        self.assertIn(self.mid_arch, ids,
                      f"mid_arch (archived project, 2026-06-05) must appear with since='2026-06-05'; "
                      f"got ids={ids!r}")
        # mid_tomb is not in the since window (it's from a tombstoned project anyway).
        self.assertNotIn(self.mid_tomb, ids,
                         f"mid_tomb (tombstoned project) must not appear regardless of since filter")


# ---------------------------------------------------------------------------
# AC5 — filtered fetch marks only the matching subset
# ---------------------------------------------------------------------------

class FilteredFetchMarksSubsetTest(_TempDataHome):
    """AC5: fetch(..., sender_project='P2') renders/marks ONLY P2 mail;
    the recipient's P1/P3 unread mail stays unread.

    RED: TypeError on sender_project kwarg in fetch().
    """

    def test_filtered_fetch_marks_only_p2_mail_read(self):
        """fetch(sender_project='P2') must set read_at ONLY for P2 messages.

        After the filtered fetch, mid_p2_fyi and mid_p2_req must have read_at
        set for ML_P1. mid_p1_req, mid_p3_fyi, mid_arch must still have read_at=NULL.

        RED: TypeError on sender_project kwarg.
        """
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True,
                        sender_project=self.P2)
        item_ids = [it["id"] for it in items]

        # The returned items must be only P2 messages.
        self.assertIn(self.mid_p2_fyi, item_ids,
                      f"fetch(sender_project=P2) must return mid_p2_fyi; got {item_ids!r}")
        self.assertIn(self.mid_p2_req, item_ids,
                      f"fetch(sender_project=P2) must return mid_p2_req; got {item_ids!r}")
        self.assertEqual(len(item_ids), 2,
                         f"fetch(sender_project=P2) must return exactly 2 items; "
                         f"got {len(item_ids)}: {item_ids!r}")

        # P2 messages must now be marked read for ML_P1.
        self.assertIsNotNone(
            self._read_at_for(self.mid_p2_fyi, self.ML_P1),
            "read_at for mid_p2_fyi must be set after filtered fetch(sender_project=P2)",
        )
        self.assertIsNotNone(
            self._read_at_for(self.mid_p2_req, self.ML_P1),
            "read_at for mid_p2_req must be set after filtered fetch(sender_project=P2)",
        )

        # Non-P2 messages must still be unread (read_at=NULL).
        self.assertIsNone(
            self._read_at_for(self.mid_p1_req, self.ML_P1),
            "read_at for mid_p1_req (P1) must remain NULL after fetch(sender_project=P2)",
        )
        self.assertIsNone(
            self._read_at_for(self.mid_p3_fyi, self.ML_P1),
            "read_at for mid_p3_fyi (P3) must remain NULL after fetch(sender_project=P2)",
        )
        self.assertIsNone(
            self._read_at_for(self.mid_arch, self.ML_P1),
            "read_at for mid_arch (P_arch) must remain NULL after fetch(sender_project=P2)",
        )

    def test_subsequent_unfiltered_fetch_returns_remaining_unread(self):
        """After fetch(sender_project='P2'), an unfiltered fetch returns the remaining
        unread mail (P1, P3, P_arch) and NOT the already-read P2 messages.

        RED: TypeError on sender_project kwarg (in the first fetch call).
        """
        # First: filtered fetch marks P2 mail read.
        s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True,
                sender_project=self.P2)

        # Second: unfiltered fetch returns remaining unread.
        remaining = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1,
                            mark=False)
        remaining_ids = [it["id"] for it in remaining]

        self.assertIn(self.mid_p1_req, remaining_ids,
                      f"mid_p1_req must still be unread after P2-filtered fetch")
        self.assertIn(self.mid_p3_fyi, remaining_ids,
                      f"mid_p3_fyi must still be unread after P2-filtered fetch")
        self.assertIn(self.mid_arch, remaining_ids,
                      f"mid_arch must still be unread after P2-filtered fetch")
        # P2 mail should now be read — not in unread_only=True results.
        self.assertNotIn(self.mid_p2_fyi, remaining_ids,
                         f"mid_p2_fyi must be read (not in unfiltered fetch) after P2-filtered fetch")
        self.assertNotIn(self.mid_p2_req, remaining_ids,
                         f"mid_p2_req must be read (not in unfiltered fetch) after P2-filtered fetch")

    def test_filtered_fetch_does_not_touch_tombstoned_mail(self):
        """fetch(sender_project='Ptomb') must return [] and not mark mid_tomb read.

        Tombstoned mail is invisible: filtered fetch returns nothing, read_at stays NULL.
        RED: TypeError on sender_project kwarg.
        """
        items = s.fetch(self.con, s.store_dir(self.P1), self.ML_P1, mark=True,
                        sender_project=self.P_tomb)
        self.assertEqual(items, [],
                         f"fetch(sender_project='Ptomb') must return [] for tombstoned project; "
                         f"got {items!r}")

        # mid_tomb must remain unread (tombstone rule: not marked read).
        self.assertIsNone(
            self._read_at_for(self.mid_tomb, self.ML_P1),
            "read_at for mid_tomb must remain NULL — tombstoned mail must not be marked read",
        )


# ---------------------------------------------------------------------------
# AC7 — unread_to signature unchanged and behaviour pinned
# ---------------------------------------------------------------------------

class UnreadToSignaturePinTest(unittest.TestCase):
    """AC7: unread_to accepts NO filter params; existing behaviour unchanged.

    These are PINNING tests — they should be GREEN already (unread_to is not
    being modified). If they fail, GREEN has broken the wake path.
    """

    def test_unread_to_signature_has_only_con_and_recipient(self):
        """unread_to(con, recipient) — exactly 2 positional params, no keyword filters.

        Inspect the signature: must not have sender, sender_project, kind, since,
        until, or subject_like parameters.

        EXPECTED: GREEN (unread_to unchanged). Pinned here as a spec contract.
        """
        sig = inspect.signature(s.unread_to)
        params = list(sig.parameters.keys())

        # Must have exactly 'con' and 'recipient'.
        self.assertEqual(
            params, ["con", "recipient"],
            f"unread_to signature must be (con, recipient) only; got params={params!r}",
        )

        # Must NOT have any filter params.
        for forbidden in ("sender", "sender_project", "kind", "since", "until",
                          "subject_like"):
            self.assertNotIn(
                forbidden, params,
                f"unread_to must NOT have a '{forbidden}' param (wake path is unchanged)",
            )

    def test_unread_to_returns_only_to_role_unread_ids(self):
        """unread_to returns message ids for 'to'-role unread messages only.

        Sets up a minimal fixture: one 'to' and one 'cc' message; verifies only
        the 'to' appears in unread_to.

        EXPECTED: GREEN (existing behaviour). Pinned as a regression anchor.
        """
        tmp = tempfile.mkdtemp(prefix="sandesh-unread-to-pin-")
        prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = tmp
        try:
            s.setup("Pin")
            con = s.connect()
            ml = "Mainline - Pin"
            t1 = "Track 1 - Pin"
            s.register(con, ml, kind="mainline", project="Pin")
            s.register(con, t1, kind="track", project="Pin")

            # to=t1 (wakes), cc=t1 via separate send (cc-role)
            mid_to = s.send(con, s.store_dir("Pin"), from_addr=ml,
                            to=[t1], subject="wake me", project="Pin")
            mid_cc = s.send(con, s.store_dir("Pin"), from_addr=ml,
                            cc=[t1], to=[ml], subject="silent", project="Pin")

            ids = s.unread_to(con, t1)

            self.assertIn(mid_to, ids,
                          f"unread_to must include the 'to'-role message; got {ids!r}")
            self.assertNotIn(mid_cc, ids,
                             f"unread_to must NOT include the 'cc'-role message; got {ids!r}")
            self.assertEqual(len(ids), 1,
                             f"unread_to must return exactly 1 id; got {len(ids)}: {ids!r}")
            con.close()
        finally:
            if prev_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = prev_xdg
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unread_to_marks_nothing(self):
        """unread_to does NOT mark messages read — it is purely a query.

        EXPECTED: GREEN (existing behaviour). Pinned as a regression anchor.
        """
        tmp = tempfile.mkdtemp(prefix="sandesh-unread-to-nomark-")
        prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = tmp
        try:
            s.setup("Nomark")
            con = s.connect()
            ml = "Mainline - Nomark"
            t1 = "Track 1 - Nomark"
            s.register(con, ml, kind="mainline", project="Nomark")
            s.register(con, t1, kind="track", project="Nomark")

            mid = s.send(con, s.store_dir("Nomark"), from_addr=ml,
                         to=[t1], subject="check", project="Nomark")

            # Call unread_to — must NOT mark anything read.
            s.unread_to(con, t1)

            # The message must still be unread.
            row = con.execute(
                "SELECT read_at FROM message_recipient WHERE message_id=? AND recipient=?",
                (mid, t1),
            ).fetchone()
            self.assertIsNone(
                row["read_at"],
                f"unread_to must not set read_at; got read_at={row['read_at']!r}",
            )
            con.close()
        finally:
            if prev_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = prev_xdg
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
