"""test_sandesh.py — unit tests for the standalone Sandesh store.

  python3 -m unittest -v   (from the repo root)   or   python3 tests/test_sandesh.py
"""

import os
import tempfile
import unittest

from sandesh import sandesh_db as s

PROJ = "Nai"
MAINLINE = "Mainline - Nai"
T1, T2, T3 = "Track 1 - Nai", "Track 2 - Nai", "Track 3 - Nai"


class SandeshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.con = s.connect()

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roster(self):
        for a, k in [(MAINLINE, "mainline"), (T1, "track"), (T2, "track"), (T3, "track")]:
            s.register(self.con, a, kind=k, project=PROJ)

    # ----- setup / project provisioning ----------------------------------- #

    def test_setup_creates_store_and_db(self):
        os.environ["XDG_DATA_HOME"] = self.tmp
        try:
            store = s.setup("DemoProj")
            self.assertTrue(os.path.isfile(s.db_path()))   # CR-SAN-022: global DB, not <store>/sandesh.db
            self.assertTrue(os.path.isdir(os.path.join(store, s.MESSAGES_DIR)))
            self.assertIn("DemoProj", s.list_projects())
            self.assertIn(os.path.join("sandesh", "projects", "DemoProj"), store)
        finally:
            del os.environ["XDG_DATA_HOME"]

    def test_store_dir_requires_project(self):
        with self.assertRaises(ValueError):
            s.store_dir("")

    # ----- address format + addressbook ----------------------------------- #

    def test_address_format_valid(self):
        self.assertEqual(s.validate_address(T2, PROJ), ("Track 2", "Nai"))

    def test_address_format_rejects_malformed(self):
        for bad in ["Track2 - Nai", "track 2 - Nai", "Worker 2 - Nai", "Nai", ""]:
            with self.assertRaises(ValueError):
                s.validate_address(bad, PROJ)

    def test_address_rejects_wrong_project(self):
        with self.assertRaises(ValueError):
            s.validate_address("Track 1 - Other", PROJ)

    def test_register_and_duplicate_rejected(self):
        s.register(self.con, T1, kind="track", project=PROJ)
        self.assertTrue(s.is_active(self.con, T1))
        with self.assertRaises(ValueError):
            s.register(self.con, T1, project=PROJ)

    def test_unregister_then_reregister_reactivates(self):
        s.register(self.con, T1, project=PROJ)
        s.deactivate(self.con, T1)
        self.assertFalse(s.is_active(self.con, T1))
        s.register(self.con, T1, project=PROJ)
        self.assertTrue(s.is_active(self.con, T1))

    def test_addressbook_lists_with_liveness(self):
        self._roster()
        book = s.addressbook(self.con, PROJ)
        self.assertEqual({b["address"] for b in book}, {MAINLINE, T1, T2, T3})
        self.assertTrue(all(b["listening"] is False for b in book))

    # ----- send: roles, sender-exclusion, broadcast, dedup ---------------- #

    def test_send_requires_subject(self):
        self._roster()
        with self.assertRaises(ValueError):
            s.send(self.con, self.tmp, T2, to=[MAINLINE], subject="", project=PROJ)

    def test_send_rejects_unknown_recipient(self):
        self._roster()
        with self.assertRaises(ValueError):
            s.send(self.con, self.tmp, T2, to=["Track 9 - Nai"], subject="x", project=PROJ)

    def test_to_and_cc_roles(self):
        self._roster()
        mid = s.send(self.con, self.tmp, T2, to=[MAINLINE], cc=[T1, T3], subject="hi", project=PROJ)
        rows = {r["recipient"]: r["role"] for r in
                self.con.execute("SELECT recipient, role FROM message_recipient WHERE message_id=?",
                                 (mid,)).fetchall()}
        self.assertEqual(rows, {MAINLINE: "to", T1: "cc", T3: "cc"})
        self.assertNotIn(T2, rows)

    def test_dedup_to_wins_over_cc(self):
        self._roster()
        mid = s.send(self.con, self.tmp, T2, to=[T1], cc=[T1, T3], subject="dup", project=PROJ)
        rows = {r["recipient"]: r["role"] for r in
                self.con.execute("SELECT recipient, role FROM message_recipient WHERE message_id=?",
                                 (mid,)).fetchall()}
        self.assertEqual(rows[T1], "to")
        self.assertEqual(rows[T3], "cc")

    def test_broadcast_all_tracks_minus_sender(self):
        self._roster()
        mid = s.send(self.con, self.tmp, T2, to=[s.BROADCAST], subject="all", project=PROJ)
        recips = {r["recipient"] for r in
                  self.con.execute("SELECT recipient FROM message_recipient WHERE message_id=?",
                                   (mid,)).fetchall()}
        self.assertEqual(recips, {MAINLINE, T1, T3})

    # ----- subject-only vs body ------------------------------------------- #

    def test_subject_only_has_no_body(self):
        self._roster()
        s.send(self.con, self.tmp, MAINLINE, to=[T2], subject="ACK", project=PROJ)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, s.MESSAGES_DIR)))
        self.assertIsNone(s.fetch(self.con, self.tmp, T2)[0]["body"])

    def test_body_written_full_path_and_fetched(self):
        self._roster()
        mid = s.send(self.con, self.tmp, MAINLINE, to=[T2], subject="d",
                     body_text="line1\nline2\n", project=PROJ)
        bp = self.con.execute("SELECT body_path FROM message WHERE id=?", (mid,)).fetchone()["body_path"]
        self.assertTrue(os.path.isabs(bp))                 # stored as a FULL path
        self.assertTrue(os.path.isfile(bp))
        self.assertEqual(s.fetch(self.con, self.tmp, T2)[0]["body"], "line1\nline2\n")

    # ----- read state: to wakes, cc silent -------------------------------- #

    def test_to_wakes_cc_silent(self):
        self._roster()
        s.send(self.con, self.tmp, MAINLINE, to=[T1], cc=[T3], subject="ping", project=PROJ)
        self.assertEqual(len(s.unread_to(self.con, T1)), 1)
        self.assertEqual(len(s.unread_to(self.con, T3)), 0)
        self.assertEqual(len(s.inbox(self.con, T3, unread_only=True)), 1)

    def test_fetch_marks_read_per_recipient(self):
        self._roster()
        s.send(self.con, self.tmp, MAINLINE, to=[T1, T2], subject="multi", project=PROJ)
        s.fetch(self.con, self.tmp, T1)
        self.assertEqual(len(s.inbox(self.con, T1, unread_only=True)), 0)
        self.assertEqual(len(s.inbox(self.con, T2, unread_only=True)), 1)

    # ----- reply + thread ------------------------------------------------- #

    def test_reply_defaults(self):
        self._roster()
        req = s.send(self.con, self.tmp, T2, to=[MAINLINE], subject="cull", kind="request", project=PROJ)
        rep = s.reply(self.con, self.tmp, req, MAINLINE, body_text="done", project=PROJ)
        m = self.con.execute("SELECT * FROM message WHERE id=?", (rep,)).fetchone()
        self.assertEqual(m["in_reply_to"], req)
        self.assertEqual(m["subject"], "Re: cull")
        recips = [r["recipient"] for r in
                  self.con.execute("SELECT recipient FROM message_recipient WHERE message_id=?",
                                   (rep,)).fetchall()]
        self.assertEqual(recips, [T2])

    def test_thread_chain(self):
        self._roster()
        m1 = s.send(self.con, self.tmp, T1, to=[MAINLINE], subject="root", project=PROJ)
        m2 = s.reply(self.con, self.tmp, m1, MAINLINE, project=PROJ)
        m3 = s.reply(self.con, self.tmp, m2, T1, project=PROJ)
        self.assertEqual([m["id"] for m in s.thread(self.con, m3)], [m1, m2, m3])

    # ----- notifier liveness, dedup, tombstone ---------------------------- #

    def test_notifier_acquire_and_dedup(self):
        self._roster()
        self.assertTrue(s.notifier_acquire(self.con, T1, os.getpid(), "tok-a", "h")[0])
        ok2, reason = s.notifier_acquire(self.con, T1, os.getpid(), "tok-b", "h")
        self.assertFalse(ok2)
        self.assertIn("already live", reason)

    def test_notifier_tombstone_and_evict(self):
        self._roster()
        s.notifier_acquire(self.con, T1, os.getpid(), "tok-a", "h")
        self.assertEqual(s.notifier_check(self.con, T1, "tok-a"), "ok")
        s.notifier_tombstone(self.con, T1)
        self.assertEqual(s.notifier_check(self.con, T1, "tok-a"), "tombstoned")
        self.assertEqual(s.notifier_check(self.con, T1, "other"), "evicted")

    def test_notifier_stale_dead_pid_reaped(self):
        self._roster()
        self.con.execute("INSERT INTO notifier (recipient,pid,token,host) VALUES (?,?,?,?)",
                         (T1, 2_000_000_000, "dead", "h"))
        self.con.commit()
        self.assertIsNone(s.notifier_live(self.con, T1))
        self.assertTrue(s.notifier_acquire(self.con, T1, os.getpid(), "fresh", "h")[0])

    # ----- unregister: auth + cooperative tombstone ----------------------- #

    def test_unregister_auth(self):
        self._roster()
        with self.assertRaises(PermissionError):
            s.unregister(self.con, T1, requester=T2, project=PROJ)
        self.assertEqual(s.unregister(self.con, T1, requester=T1, project=PROJ)[0], "unregistered")
        self.assertEqual(s.unregister(self.con, T3, requester=MAINLINE, project=PROJ)[0], "unregistered")

    def test_unregister_tombstones_live_notifier_first(self):
        self._roster()
        s.notifier_acquire(self.con, T1, os.getpid(), "tok-a", "h")
        self.assertEqual(s.unregister(self.con, T1, requester=MAINLINE, project=PROJ)[0], "tombstoned")
        self.assertTrue(s.is_active(self.con, T1))
        s.notifier_release(self.con, T1, "tok-a")
        self.assertEqual(s.unregister(self.con, T1, requester=MAINLINE, project=PROJ)[0], "unregistered")
        self.assertFalse(s.is_active(self.con, T1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
