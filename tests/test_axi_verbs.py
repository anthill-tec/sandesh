"""test_axi_verbs.py — RED tests for CR-SAN-047 cycle 206 (§S1b library seams +
§S4 per-verb envelope fields, EXCLUDING §S4b), against:
  docs/changes/CR-SAN-047-axi-toon-cli-envelope.md  AC5, AC6, AC11, AC12
  docs/research/PRD-axi-toon.md  §4.0 (P2 minimal schemas, P4 aggregates,
  P5 empty states, P6 idempotent no-ops), §4.4 (default vs FULL fields)

Scope for THIS cycle: the eight named verbs' per-verb envelope FIELDS
(addressbook, inbox, fetch, send, reply, register, unregister, archive/
unarchive) built on TOP of the §S3 plumbing shipped in cycle 205
(`tests/test_axi_cli_format.py`) and shipped this cycle's new library seams
(`sandesh.sandesh_db.message_recipients`, `AlreadyRegistered`,
`AlreadyInState`). Truncation/`--full`, `help[]` presence rules, and generic
envelopes for verbs other than the eight (search/thread/projects/…) and the
home view are cycle 207 — NOT asserted here.

Expected RED: nearly everything fails today because
  * `sandesh.sandesh_db` has no `message_recipients`, `AlreadyRegistered`,
    `AlreadyInState` symbols yet (AttributeError);
  * `cli.py`'s `_run_machine` always builds `Envelope(verb, True, {}, context)`
    — an EMPTY fields dict — regardless of what the verb handler printed, so
    every field assertion below (`participants`, `messages`, `id`, `to`,
    `cc`, `result`, `state`, aggregates, …) currently fails: either the key is
    simply absent (AssertionError from `assertIn`) or an unrecognized
    `--fields`/`--limit` CLI flag makes argparse itself fail with exit 2 for
    the wrong reason (checked via the resulting `rc`/`error` assertions, not
    a crash).
  * One exception, called out explicitly below: `register` of an active
    duplicate address in **human** mode currently already exits 1 (unchanged
    0.3.6 behaviour) — that single assertion is a regression PIN, not new
    RED; kept because the CR explicitly asks for it ("golden-style assert on
    exit code only").

Ambiguity resolved (documented, not escalated — see the dispatch prompt's
own wording): the CR text for AC6 says "reply-to #A from Mainline is NOT
needed; instead make #B a reply to #A from Track 2 - Demo so `re` is
exercised" while also requiring `fetch --to "Mainline - Demo"` to report
`marked_read == 2` (i.e. BOTH #A and #B must be in Mainline's mailbox).
`sdb.reply()` always sets `to=[parent's sender]` (Track 3 - Demo here) — it
has no override — so the only way for Mainline (an original recipient of
#A) to also receive #B is via `reply_all=True`, which Cc's every OTHER
recipient of #A (Mainline, Track 1) besides the replier (Track 2) and the
parent's sender. I used `reply_all=True` to satisfy the `marked_read == 2`
requirement; this is presentation-agnostic (message construction only) and
does not itself assert anything about reply-all semantics.

A second finding (not an escalation, a note for the GREEN/VERIFY reader):
AC11 requires `context.address` on `inbox` (the recipient given via `--to`),
but `cli.py`'s current `_axi_context()` walks
`("from_", "as_", "address", "to")` and `break`s on the FIRST attribute that
EXISTS on `args` — `inbox`'s parser already has a `--from` FILTER flag (dest
`from_`, unrelated to "my own address"), so it wins the race and, being
`None` when unset, leaves `context` with no `address` key at all. This is a
genuine pre-existing gap AC11 now exercises, not a defect in this test —
`test_inbox_default_columns_le4_aggregate_unread_context_project_and_address`
below documents it via `assertIn` before `assertEqual` so the failure names
the missing key precisely.

Conventions follow `tests/test_axi_cli_format.py`: `run_cli()`/`_toon.decode`,
XDG-temp store, in-process `cli.main`. Fixture: project `Demo` with
`Mainline - Demo` + `Track 1 - Demo` + `Track 2 - Demo` registered by
`_BaseFixture.setUp` (subclasses add more addresses/messages as needed).

Run (from the repo root):
  python3 -m unittest -v tests.test_axi_verbs
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_verbs.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import sqlite3
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as sdb
from sandesh import cli
from sandesh import _toon


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_cli_format.py's run_cli — handles both a plain
    int return and SystemExit (argparse -> exit(2); the house
    `sys.exit(f"[sandesh] {msg}")` single-arg idiom -> exit 1, matching what
    a real subprocess would report for that idiom).
    """
    prev = {}
    if env:
        for k, v in env.items():
            prev[k] = os.environ.get(k)
            os.environ[k] = v
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
                if rc is None:
                    rc = 0
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    rc = 0
                elif isinstance(code, int):
                    rc = code
                else:
                    text = str(code)
                    err_buf.write(text if text.endswith("\n") else text + "\n")
                    rc = 1
    finally:
        if env:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return rc, out_buf.getvalue(), err_buf.getvalue()


# --------------------------------------------------------------------------- #
# Fixture: 'Demo' project, Mainline + Track 1 + Track 2 registered.
# --------------------------------------------------------------------------- #

class _BaseFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-verbs-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_format = os.environ.pop("SANDESH_FORMAT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup("Demo")
        self.con = sdb.connect()
        self.store = sdb.store_dir("Demo")
        sdb.register(self.con, "Mainline - Demo", kind="mainline", project="Demo")
        sdb.register(self.con, "Track 1 - Demo", kind="track", project="Demo")
        sdb.register(self.con, "Track 2 - Demo", kind="track", project="Demo")

    def tearDown(self):
        self.con.close()
        for var, prev in (("XDG_DATA_HOME", self._prev_xdg),
                          ("SANDESH_FORMAT", self._prev_format),
                          ("SANDESH_ADDRESS", self._prev_address),
                          ("SANDESH_PROJECT", self._prev_project)):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, argv, env=None):
        return run_cli(argv, env=env)


# --------------------------------------------------------------------------- #
# §S1b — additive library seams: message_recipients, AlreadyRegistered,
# AlreadyInState.
# --------------------------------------------------------------------------- #

class LibrarySeamsTest(_BaseFixture):

    def test_message_recipients_groups_to_and_cc_ordered_by_recipient_address(self):
        sdb.register(self.con, "Track 3 - Demo", kind="track", project="Demo")
        mid = sdb.send(self.con, self.store, "Track 3 - Demo",
                       to=["Track 2 - Demo", "Mainline - Demo"],
                       cc=["Track 1 - Demo"], subject="multi", project="Demo")
        result = sdb.message_recipients(self.con, [mid])
        self.assertEqual(
            result,
            {mid: {"to": ["Mainline - Demo", "Track 2 - Demo"],
                   "cc": ["Track 1 - Demo"]}},
            "to/cc must each be present and sorted by recipient address, "
            "not preserved in call/insertion order")

    def test_message_recipients_id_with_no_recipients_returns_empty_lists(self):
        result = sdb.message_recipients(self.con, [999999])
        self.assertEqual(result, {999999: {"to": [], "cc": []}})

    def test_message_recipients_empty_ids_list_returns_empty_dict(self):
        self.assertEqual(sdb.message_recipients(self.con, []), {})

    def test_AlreadyRegistered_is_ValueError_subclass_raised_on_active_duplicate(self):
        with self.assertRaises(ValueError) as baseline:
            sdb.register(self.con, "Mainline - Demo", kind="mainline", project="Demo")
        baseline_message = str(baseline.exception)
        self.assertEqual(baseline_message, "address already registered: Mainline - Demo")

        self.assertTrue(issubclass(sdb.AlreadyRegistered, ValueError),
                         "AlreadyRegistered must subclass ValueError")
        with self.assertRaises(sdb.AlreadyRegistered) as second:
            sdb.register(self.con, "Mainline - Demo", kind="mainline", project="Demo")
        self.assertEqual(str(second.exception), baseline_message,
                         "the refusal message must be unchanged")

    def test_AlreadyInState_is_ValueError_subclass_raised_by_archive_when_already_archived(self):
        sdb.archive(self.con, "Demo", "Mainline - Demo")  # -> archived
        with self.assertRaises(ValueError) as baseline:
            sdb.archive(self.con, "Demo", "Mainline - Demo")  # throwaway: current message
        baseline_message = str(baseline.exception)

        self.assertTrue(issubclass(sdb.AlreadyInState, ValueError),
                         "AlreadyInState must subclass ValueError")
        with self.assertRaises(sdb.AlreadyInState) as second:
            sdb.archive(self.con, "Demo", "Mainline - Demo")
        self.assertEqual(str(second.exception), baseline_message,
                         "the refusal message must be unchanged")

    def test_AlreadyInState_is_ValueError_subclass_raised_by_unarchive_when_already_active(self):
        sdb.archive(self.con, "Demo", "Mainline - Demo")
        sdb.unarchive(self.con, "Demo", "Mainline - Demo")  # -> active again
        with self.assertRaises(ValueError) as baseline:
            sdb.unarchive(self.con, "Demo", "Mainline - Demo")  # throwaway: current message
        baseline_message = str(baseline.exception)

        self.assertTrue(issubclass(sdb.AlreadyInState, ValueError),
                         "AlreadyInState must subclass ValueError")
        with self.assertRaises(sdb.AlreadyInState) as second:
            sdb.unarchive(self.con, "Demo", "Mainline - Demo")
        self.assertEqual(str(second.exception), baseline_message,
                         "the refusal message must be unchanged")


# --------------------------------------------------------------------------- #
# AC5 — addressbook envelope.
# --------------------------------------------------------------------------- #

class AddressbookEnvelopeTest(_BaseFixture):

    def test_default_envelope_rows_have_only_address_and_listening(self):
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("participants", axi, f"envelope missing 'participants': {axi!r}")
        rows = axi["participants"]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(set(row.keys()), {"address", "listening"})
            self.assertIsInstance(row["listening"], bool)
        self.assertIn("listening", axi, "aggregate 'listening: n/m' field must be present")
        self.assertEqual(axi["listening"], "0/3")
        self.assertIn("participants[3]{address,listening}:", out,
                       f"raw toon must carry the tabular header; out={out!r}")

        # Cheap JSON-parity check folded in here (same call re-run in json
        # mode) rather than a separate test that would pass vacuously today.
        rc_json, out_json, err_json = self.run_cli(
            ["--format", "json", "addressbook", "--project", "Demo"])
        self.assertEqual(rc_json, 0, f"err={err_json!r}")
        self.assertEqual(json.loads(out_json), {"axi": axi})

    def test_live_notifier_flips_listening_true_and_aggregate_counts_it(self):
        ok, reason = sdb.notifier_acquire(self.con, "Mainline - Demo", os.getpid(), "tok-1", "host")
        self.assertEqual(reason, "acquired")
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("participants", axi)
        rows = {r["address"]: r["listening"] for r in axi["participants"]}
        self.assertIn("Mainline - Demo", rows)
        self.assertIs(rows["Mainline - Demo"], True,
                       "the live-notifier address must report listening:true")
        self.assertEqual(axi.get("listening"), "1/3")

    def test_fields_flag_widens_to_the_full_column_set(self):
        sdb.deactivate(self.con, "Track 2 - Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "addressbook", "--project", "Demo",
             "--fields", "address,kind,status,listening,registered"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("participants", axi)
        rows = axi["participants"]
        statuses = set()
        for row in rows:
            self.assertEqual(set(row.keys()),
                              {"address", "kind", "status", "listening", "registered"})
            self.assertIn(row["status"], ("active", "inactive"))
            statuses.add(row["status"])
            self.assertTrue(row["registered"], "registered must be a non-empty timestamp string")
        self.assertIn("inactive", statuses,
                       "the deactivated Track 2 - Demo must report status:inactive")

    def test_bogus_fields_value_exits_2_ok_false_names_field_and_valid_set(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "addressbook", "--project", "Demo", "--fields", "bogus"])
        self.assertEqual(rc, 2, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("bogus", axi["error"],
                       f"error must name the offending field; got {axi['error']!r}")
        for name in ("address", "kind", "status", "listening", "registered"):
            self.assertIn(name, axi["error"],
                          f"error must list the valid field set; got {axi['error']!r}")


# --------------------------------------------------------------------------- #
# AC6 — fetch envelope.
# --------------------------------------------------------------------------- #

class FetchEnvelopeTest(_BaseFixture):

    def setUp(self):
        super().setUp()
        sdb.register(self.con, "Track 3 - Demo", kind="track", project="Demo")
        self.body_a = "y" * 100
        self.mid_a = sdb.send(
            self.con, self.store, "Track 3 - Demo",
            to=["Track 2 - Demo", "Mainline - Demo"], cc=["Track 1 - Demo"],
            subject="A", body_text=self.body_a, project="Demo")
        # reply_all so Mainline (an original 'to' of #A, not the replier) is
        # cc'd on #B too — see the module docstring's ambiguity note.
        self.mid_b = sdb.reply(self.con, self.store, self.mid_a, "Track 2 - Demo",
                               reply_all=True, project="Demo")

    def test_default_envelope_rows_have_exact_keys_to_cc_re_and_bodies(self):
        rc, out, err = self.run_cli(["--format", "toon", "fetch", "--project", "Demo",
                                     "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        rows = axi["messages"]
        self.assertEqual(len(rows), 2, f"Mainline must see both #A and #B; rows={rows!r}")
        for row in rows:
            self.assertEqual(set(row.keys()),
                              {"id", "from", "to", "cc", "kind", "subject", "created", "re"})
        by_id = {r["id"]: r for r in rows}
        self.assertIn(self.mid_a, by_id)
        self.assertIn(self.mid_b, by_id)
        row_a = by_id[self.mid_a]
        self.assertEqual(row_a["to"], "Mainline - Demo;Track 2 - Demo",
                         "to must be ';'-joined and decode back verbatim")
        self.assertEqual(row_a["cc"], "Track 1 - Demo")
        self.assertIsNone(row_a["re"], "#A is not a reply")
        row_b = by_id[self.mid_b]
        self.assertEqual(row_b["re"], self.mid_a, "#B's re must equal #A's id")

        self.assertIn("bodies", axi)
        self.assertEqual(set(axi["bodies"].keys()), {str(self.mid_a)},
                         "only #A (which has a body) may have a bodies[] key")
        self.assertEqual(axi["bodies"][str(self.mid_a)], self.body_a,
                         "a 100-char body must be complete, never truncated")
        self.assertIn("marked_read", axi)
        self.assertEqual(axi["marked_read"], 2)

        # Cheap JSON-parity check folded in here rather than a separate test
        # that would pass vacuously today (a near-empty envelope trivially
        # round-trips).
        rc_json, out_json, err_json = self.run_cli(
            ["--format", "json", "fetch", "--project", "Demo", "--to", "Mainline - Demo", "--peek"])
        self.assertEqual(rc_json, 0, f"err={err_json!r}")
        rc_toon2, out_toon2, err_toon2 = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo", "--peek"])
        self.assertEqual(rc_toon2, 0, f"err={err_toon2!r}")
        self.assertEqual(json.loads(out_json), _toon.decode(out_toon2))

    def test_second_fetch_reports_empty_state_sentence_and_marked_read_zero(self):
        self.run_cli(["--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo"])
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("messages"), "0 unread for Mainline - Demo")
        self.assertEqual(axi.get("marked_read"), 0)

    def test_peek_leaves_marked_read_zero_and_rows_still_present(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo",
             "--to", "Mainline - Demo", "--peek"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("marked_read"), 0)
        self.assertIn("messages", axi)
        self.assertEqual(len(axi["messages"]), 2,
                         "--peek must not remove/hide the unread rows")

    def test_machine_fetch_preserves_unread_state_if_recipient_lookup_fails(self):
        with mock.patch.object(sdb, "message_recipients", side_effect=ValueError("recipient lookup failed")):
            rc, out, err = self.run_cli([
                "--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo",
            ])
        self.assertEqual(rc, 1)
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["error"], "recipient lookup failed")
        self.assertTrue(axi.get("help"))
        self.assertEqual(len(sdb.inbox(self.con, "Mainline - Demo", unread_only=True)), 2)

    def test_message_recipients_chunks_below_connection_variable_limit(self):
        if not hasattr(self.con, "setlimit") or not hasattr(sqlite3, "SQLITE_LIMIT_VARIABLE_NUMBER"):
            self.skipTest("SQLite variable limit controls require Python 3.11+")
        limit = sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER
        previous = self.con.setlimit(limit, 999)
        try:
            recipients = sdb.message_recipients(self.con, range(1500))
        finally:
            self.con.setlimit(limit, previous)
        self.assertEqual(len(recipients), 1500)
        self.assertEqual(recipients[0], {"to": [], "cc": []})
        self.assertEqual(recipients[1499], {"to": [], "cc": []})


# --------------------------------------------------------------------------- #
# inbox envelope.
# --------------------------------------------------------------------------- #

class InboxEnvelopeTest(_BaseFixture):

    def setUp(self):
        super().setUp()
        self.mid1 = sdb.send(self.con, self.store, "Track 1 - Demo",
                             to=["Mainline - Demo"], subject="one", project="Demo")
        self.mid2 = sdb.send(self.con, self.store, "Track 2 - Demo",
                             to=["Mainline - Demo"], subject="two", project="Demo")
        self.mid3 = sdb.send(self.con, self.store, "Track 1 - Demo",
                             to=["Mainline - Demo"], subject="three", project="Demo")
        sdb.mark_read(self.con, "Mainline - Demo", [self.mid1])

    def test_default_envelope_rows_and_aggregate(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        rows = axi["messages"]
        self.assertEqual(len(rows), 2, "default (unread-only) must show mid2+mid3")
        for row in rows:
            self.assertEqual(set(row.keys()), {"id", "from", "subject", "unread"})
            self.assertIs(row["unread"], True)
        self.assertIn("unread", axi)
        self.assertEqual(axi["unread"], "2 of 3")

    def test_all_flag_includes_read_rows_with_per_row_unread_bool(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo",
             "--to", "Mainline - Demo", "--all"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        by_id = {r["id"]: r["unread"] for r in axi["messages"]}
        self.assertEqual(len(by_id), 3)
        self.assertIs(by_id[self.mid1], False, "mid1 was marked read")
        self.assertIs(by_id[self.mid2], True)
        self.assertIs(by_id[self.mid3], True)
        self.assertEqual(axi.get("unread"), "2 of 3",
                         "the aggregate is unaffected by --all")

    def test_limit_one_caps_rows_but_aggregate_reports_full_total(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo",
             "--to", "Mainline - Demo", "--limit", "1"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        self.assertEqual(len(axi["messages"]), 1)
        self.assertEqual(axi.get("unread"), "2 of 3",
                         "--limit slices the rows, not the aggregate")

    def test_fields_flag_widens_to_full_nine_column_set(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo",
             "--to", "Mainline - Demo", "--all",
             "--fields", "id,from,to,cc,kind,subject,created,re,unread"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        for row in axi["messages"]:
            self.assertEqual(
                set(row.keys()),
                {"id", "from", "to", "cc", "kind", "subject", "created", "re", "unread"})

    def test_empty_inbox_reports_empty_state_sentence(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo", "--to", "Track 2 - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("messages"), "0 unread for Track 2 - Demo")


# --------------------------------------------------------------------------- #
# send / reply envelope.
# --------------------------------------------------------------------------- #

class SendReplyEnvelopeTest(_BaseFixture):

    def test_send_envelope_fields(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "send", "--project", "Demo", "--from", "Track 1 - Demo",
             "--to", "Mainline - Demo", "--cc", "Track 2 - Demo", "--subject", "hello",
             "--kind", "fyi"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("id", axi)
        self.assertIsInstance(axi["id"], int)
        self.assertEqual(axi.get("to"), "Mainline - Demo")
        self.assertEqual(axi.get("cc"), "Track 2 - Demo")
        self.assertEqual(axi.get("kind"), "fyi")
        self.assertEqual(axi.get("subject"), "hello")
        self.assertEqual(axi.get("delivered"), 2)

    def test_send_without_kind_has_null_kind_and_correct_delivered_count(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "send", "--project", "Demo", "--from", "Track 1 - Demo",
             "--to", "Mainline - Demo", "--subject", "no kind"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("kind", axi)
        self.assertIsNone(axi["kind"])
        self.assertEqual(axi.get("delivered"), 1)

    def test_reply_envelope_adds_re_field(self):
        mid = sdb.send(self.con, self.store, "Track 1 - Demo",
                       to=["Mainline - Demo"], subject="orig", project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "reply", "--project", "Demo", "--from", "Mainline - Demo",
             "--to-msg", str(mid)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        for key in ("id", "to", "cc", "kind", "subject", "delivered", "re"):
            self.assertIn(key, axi, f"reply envelope missing {key!r}: {axi!r}")
        self.assertEqual(axi["re"], mid)
        self.assertEqual(axi["to"], "Track 1 - Demo")
        self.assertEqual(axi["delivered"], 1)


# --------------------------------------------------------------------------- #
# AC12 — register / unregister idempotence.
# --------------------------------------------------------------------------- #

class RegisterUnregisterIdempotenceTest(_BaseFixture):

    def test_fresh_register_envelope_result_registered(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "register", "--project", "Demo",
             "--address", "Track 3 - Demo", "--kind", "track"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        for key in ("address", "project", "kind", "result"):
            self.assertIn(key, axi, f"register envelope missing {key!r}: {axi!r}")
        self.assertEqual(axi["result"], "registered")
        self.assertEqual(axi["address"], "Track 3 - Demo")
        self.assertEqual(axi["project"], "Demo")
        self.assertEqual(axi["kind"], "track")
        self.assertEqual(axi["context"].get("project"), "Demo")
        self.assertEqual(axi["context"].get("address"), "Track 3 - Demo",
                         "AC11: context.address on register")

    def test_register_duplicate_toon_mode_ok_true_result_already_exit_0(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "register", "--project", "Demo",
             "--address", "Mainline - Demo", "--kind", "mainline"])
        self.assertEqual(rc, 0, f"idempotent machine-mode register must exit 0; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertIn("result", axi)
        self.assertEqual(axi["result"], "already")

    def test_register_duplicate_human_mode_still_exits_1_unchanged(self):
        # Regression PIN (per the CR: "unchanged, golden-style assert on exit
        # code only") — this specific assertion may already hold today since
        # human-mode behaviour is explicitly untouched by this CR.
        rc, out, err = self.run_cli(
            ["register", "--project", "Demo", "--address", "Mainline - Demo",
             "--kind", "mainline"])
        self.assertEqual(rc, 1)

    def test_unregister_active_address_result_unregistered(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 1 - Demo", "--as", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("result", axi)
        self.assertEqual(axi["result"], "unregistered")
        self.assertEqual(axi["context"].get("project"), "Demo")
        self.assertEqual(axi["context"].get("address"), "Mainline - Demo",
                         "AC11: context.address on unregister (the requester)")

    def test_unregister_absent_address_ok_true_result_absent_exit_0(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 9 - Demo", "--as", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"idempotent no-op unregister must exit 0; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertIn("result", axi)
        self.assertEqual(axi["result"], "absent")

    def test_unregister_malformed_absent_address_exits_1_with_the_human_error_not_absent(self):
        argv = ["unregister", "--project", "Demo", "--address", "Trck 1 - Demo",
                "--as", "Mainline - Demo"]
        rc_human, _, err_human = self.run_cli(argv)
        self.assertEqual(rc_human, 1, f"human mode: err={err_human!r}")
        rc, out, err = self.run_cli(["--format", "toon"] + argv)
        self.assertEqual(rc, 1, f"machine mode must mirror human mode's exit 1; out={out!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("bad address", axi["error"])
        self.assertNotIn("result", axi)
        self.assertTrue(sdb.is_active(self.con, "Track 1 - Demo"),
                        "the real address must stay registered")

    def test_unregister_absent_address_by_a_non_mainline_other_is_a_permission_error_exit_1(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 9 - Demo", "--as", "Track 1 - Demo"])
        self.assertEqual(rc, 1, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("only Mainline may remove another participant", axi["error"])
        self.assertNotIn("result", axi)

    def test_unregister_absent_foreign_project_address_is_a_permission_error_exit_1(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 1 - Other", "--as", "Mainline - Demo"])
        self.assertEqual(rc, 1, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("not in project 'Demo'", axi["error"])
        self.assertNotIn("result", axi)

    def test_unregister_live_notifier_ok_true_result_tombstoned_exit_3_unchanged(self):
        sdb.notifier_acquire(self.con, "Track 2 - Demo", os.getpid(), "tok", "host")
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 2 - Demo", "--as", "Mainline - Demo"])
        self.assertEqual(rc, 3, f"tombstoned exit code must stay 3 (unchanged); err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True, "P6: this is a successful cooperative eviction, not a failure")
        self.assertIn("result", axi)
        self.assertEqual(axi["result"], "tombstoned")


# --------------------------------------------------------------------------- #
# AC12 — archive / unarchive idempotence.
# --------------------------------------------------------------------------- #

class ArchiveUnarchiveIdempotenceTest(_BaseFixture):

    def test_archive_envelope_then_idempotent_second_call(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "archive", "--project", "Demo", "--by", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertIn("project", axi)
        self.assertIn("state", axi)
        self.assertEqual(axi["project"], "Demo")
        self.assertEqual(axi["state"], "archived")
        self.assertEqual(axi["context"].get("project"), "Demo",
                         "AC11: context.project must be present on every verb")

        rc2, out2, err2 = self.run_cli(
            ["--format", "toon", "archive", "--project", "Demo", "--by", "Mainline - Demo"])
        self.assertEqual(rc2, 0, f"idempotent archive must exit 0; err={err2!r}")
        axi2 = _toon.decode(out2)["axi"]
        self.assertIs(axi2["ok"], True)
        self.assertIn("result", axi2)
        self.assertEqual(axi2["result"], "already")

    def test_unarchive_envelope_then_idempotent_second_call(self):
        self.run_cli(["--format", "toon", "archive", "--project", "Demo", "--by", "Mainline - Demo"])
        rc, out, err = self.run_cli(
            ["--format", "toon", "unarchive", "--project", "Demo", "--by", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertIn("state", axi)
        self.assertEqual(axi["state"], "active")

        rc2, out2, err2 = self.run_cli(
            ["--format", "toon", "unarchive", "--project", "Demo", "--by", "Mainline - Demo"])
        self.assertEqual(rc2, 0, f"idempotent unarchive must exit 0; err={err2!r}")
        axi2 = _toon.decode(out2)["axi"]
        self.assertIs(axi2["ok"], True)
        self.assertIn("result", axi2)
        self.assertEqual(axi2["result"], "already")


# --------------------------------------------------------------------------- #
# AC11 — AXI conformance subset for THIS cycle's eight named verbs (minus
# fetch, already covered above): addressbook, inbox, send, reply, register,
# unregister, archive.
# --------------------------------------------------------------------------- #

class AC11ConformanceSubsetTest(_BaseFixture):

    def setUp(self):
        super().setUp()
        self.seed_mid = sdb.send(self.con, self.store, "Track 1 - Demo",
                                 to=["Mainline - Demo"], subject="inbox seed",
                                 project="Demo")

    def test_addressbook_default_columns_le4_aggregate_listening_context_project(self):
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("participants", axi)
        self.assertLessEqual(len(axi["participants"][0].keys()), 4,
                              "default list column count must be <=4")
        self.assertIn("listening", axi, "named aggregate field must be present")
        self.assertEqual(axi["context"].get("project"), "Demo")

    def test_inbox_default_columns_le4_aggregate_unread_context_project_and_address(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("messages", axi)
        self.assertLessEqual(len(axi["messages"][0].keys()), 4,
                              "default list column count must be <=4")
        self.assertIn("unread", axi, "named aggregate field must be present")
        self.assertEqual(axi["context"].get("project"), "Demo")
        self.assertIn("address", axi["context"],
                       "inbox envelope must carry context.address (the --to recipient) "
                       "— see the module docstring's finding on _axi_context's flag order")
        self.assertEqual(axi["context"]["address"], "Mainline - Demo")

    def test_send_aggregate_delivered_context_project_and_address(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "send", "--project", "Demo", "--from", "Track 1 - Demo",
             "--to", "Mainline - Demo", "--subject", "ac11"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("delivered", axi, "named aggregate field must be present")
        self.assertEqual(axi["context"].get("project"), "Demo")
        self.assertEqual(axi["context"].get("address"), "Track 1 - Demo")

    def test_reply_aggregate_delivered_context_project_and_address(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "reply", "--project", "Demo", "--from", "Mainline - Demo",
             "--to-msg", str(self.seed_mid)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("delivered", axi, "named aggregate field must be present")
        self.assertEqual(axi["context"].get("project"), "Demo")
        self.assertEqual(axi["context"].get("address"), "Mainline - Demo")


# NOTE: register/unregister/archive context.project+address are asserted
# inside RegisterUnregisterIdempotenceTest / ArchiveUnarchiveIdempotenceTest
# instead of as standalone tests here — those calls already fail on other
# assertions (result/state/etc.) this cycle, so folding the context checks
# in avoids a redundant test that would currently PASS vacuously (their
# context resolution already worked pre-cycle-206).


if __name__ == "__main__":
    unittest.main()
