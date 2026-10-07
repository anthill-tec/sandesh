"""test_axi_disclosure.py — RED tests for CR-SAN-047 cycle 207 (§S4 truncation +
`help[]` + "remaining verbs" envelopes, §S4b home view), against:
  docs/changes/CR-SAN-047-axi-toon-cli-envelope.md  §S4, §S4b, AC11 (remainder),
  AC13, AC14
  docs/research/PRD-axi-toon.md  §4.0 P3 (truncation), P5 (empty states),
  P8 (content-first home view), P9 (contextual disclosure / help[]),
  P10 (consistent help) and §4.4 last row (the generic "every other verb"
  envelope)

Scope for THIS cycle: everything NOT already covered by cycles 204-206
(`tests/test_axi_envelope.py`, `test_axi_cli_format.py`, `test_axi_verbs.py`):
  1. AC13 — fetch/thread body truncation, `--full`, and the truncation-only
     `help[]` hint.
  2. P9 `help[]` presence/absence rules across the eight named verbs plus
     search/projects.
  3. The "remaining verbs" generic envelope fields (search, thread defaults,
     projects, setup, grant, revoke) and the five tolerant-shape pass-through
     verbs (`projects --all`, `init --check`, `migrate --status`,
     `consolidate`, `reindex`) plus the `tombstone` no-`--yes` machine-mode
     usage error.
  4. AC14 — the home view (`sandesh` with no subcommand / `sandesh status`)
     and its usage-error siblings.

`notify`'s final envelope is cycle 208 — NOT exercised here.

Expected RED (confirmed empirically against the current tree before writing
these tests — see the probe transcripts in the cycle notes): `fetch`/`thread`
have no `--full` flag yet (argparse "unrecognized arguments" -> exit 2);
`cli.py`'s `AXI_FN` has no entries for `search`/`thread`/`projects`/`setup`/
`grant`/`revoke`, so `_run_machine`'s fallback path always builds an EMPTY
`fields` dict for them regardless of what the human handler printed; no
handler anywhere ever populates a `help` key; there is no `status` verb and
`add_subparsers` is still `required=True`, so bare `sandesh` (argv `[]`)
raises argparse's "the following arguments are required: cmd" (exit 2) in
every format, never the AC14 dashisboard.

Ambiguities resolved (documented, not escalated):
  * "empty query" (item 3, search): read as a QUERY THAT MATCHES ZERO
    MESSAGES (exercising the P5 empty-state sentence), not a literal empty
    string (which risks being an FTS5 syntax edge case unrelated to what
    this item is testing). A distinctive nonsense term is used instead.
  * The digit-placeholder check ("no entry contains a concrete message id
    ... like `#1` or ` 1 `", item 2): implemented against the ACTUAL fixture
    message ids (not a blanket single/double-digit regex), since a
    numeric CLI default appearing in a help template (e.g. a `--limit 50`
    example) is not itself a "concrete message id" and must not be flagged.
  * `bin` (item 4, AC14 home view): the PRD only fixes it as "a `~`-collapsed
    path"; since these tests run `cli.main` in-process (not a subprocess),
    the exact value a real installed binary would report cannot be
    reproduced here. Asserted structurally: a non-empty string starting with
    `~` or `/` (an absolute-ish path either way an implementation resolves
    `sys.argv[0]`/the console-script path).
  * `["status"]` "identical envelope except any timing field": there is no
    timing field named anywhere in the spec for this envelope, so the two
    tests (bare argv vs explicit `status`) assert the identical field set.

Conventions follow `tests/test_axi_verbs.py`: `run_cli()`/`_toon.decode`,
XDG-temp store per test, in-process `cli.main`. Fixture: project `Demo` with
`Mainline - Demo` + `Track 1 - Demo` + `Track 2 - Demo` registered by
`_BaseFixture.setUp` (subclasses add more addresses/messages as needed).

Run (from the repo root):
  python3 -m unittest -v tests.test_axi_disclosure
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_disclosure.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import os
import shutil
import signal
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import _toon  # noqa: E402
from sandesh import cli  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_verbs.py's run_cli — handles both a plain int
    return and SystemExit (argparse -> exit(2); the house
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


# Known verb prefixes a help[] template may start with (P10/P9 conformance
# check) — mirrors cli.py's registered subcommands + the new AC14 `status`.
_KNOWN_VERBS = (
    "setup", "projects", "register", "unregister", "addressbook", "send",
    "reply", "inbox", "fetch", "thread", "notify", "migrate", "consolidate",
    "search", "reindex", "grant", "revoke", "archive", "unarchive",
    "tombstone", "init", "status",
)


def _assert_help_conforms(test, help_list):
    """Shared P9/P10 shape checks: non-empty list of strings, each either
    mentioning 'sandesh' or opening with a known verb name."""
    test.assertIsInstance(help_list, list)
    test.assertGreater(len(help_list), 0, "help[] must be non-empty")
    for h in help_list:
        test.assertIsInstance(h, str)
        test.assertTrue(
            "sandesh" in h or any(h.startswith(v) for v in _KNOWN_VERBS),
            f"help entry must reference sandesh or open with a known verb; got {h!r}",
        )


# --------------------------------------------------------------------------- #
# Fixture: 'Demo' project, Mainline + Track 1 + Track 2 registered.
# --------------------------------------------------------------------------- #

class _BaseFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-disclosure-test-")
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


# =========================================================================== #
# Item 1 — AC13 truncation.
# =========================================================================== #

class TruncationTest(_BaseFixture):

    def setUp(self):
        super().setUp()
        self.body_2000 = ("lorem ipsum " * 200)[:2000]
        self.assertEqual(len(self.body_2000), 2000)  # fixture sanity, not the RED assertion
        self.mid_long = sdb.send(
            self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
            subject="long", body_text=self.body_2000, project="Demo")

    def test_truncated_body_is_exactly_500_chars_plus_suffix_and_help_names_full(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo",
             "--to", "Mainline - Demo", "--peek"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("bodies", axi)
        expected = self.body_2000[:500] + " (truncated, 2000 chars total)"
        self.assertEqual(
            axi["bodies"].get(str(self.mid_long)), expected,
            "truncated body must be exactly the first 500 chars + the size suffix")
        self.assertIn("help", axi, "a truncated fetch must carry help[] naming --full")
        self.assertTrue(
            any("fetch" in h and "--full" in h for h in axi.get("help", [])),
            f"help[] must name 'fetch ... --full'; got {axi.get('help')!r}")

    def test_full_flag_returns_complete_body_and_no_help_key(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo",
             "--to", "Mainline - Demo", "--full"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("bodies", axi)
        self.assertEqual(axi["bodies"].get(str(self.mid_long)), self.body_2000,
                         "--full must return the complete, untruncated body")
        self.assertNotIn("help", axi, "--full leaves nothing truncated -> no help[]")

    def test_short_body_is_never_truncated_and_carries_no_help(self):
        # peel off the long message from setUp so it doesn't interfere: this
        # fetch MARKS it read (no --peek: peek never marks; locked semantics),
        # so it must run BEFORE the short message is sent.
        self.run_cli(["--format", "toon", "fetch", "--project", "Demo",
                      "--to", "Mainline - Demo"])
        body_100 = "y" * 100
        sdb.send(self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
                 subject="short", body_text=body_100, project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("bodies", axi)
        by_short = [v for v in axi["bodies"].values() if v == body_100 or "y" * 50 in str(v)]
        self.assertTrue(by_short, f"the 100-char body must be present verbatim; bodies={axi['bodies']!r}")
        for v in axi["bodies"].values():
            self.assertNotIn("(truncated", v, f"a 100-char body must never be marked truncated: {v!r}")
        self.assertNotIn("help", axi, "no truncation occurred -> no help[]")

    def test_thread_default_truncates_body_and_suggests_full(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(self.mid_long)],
            env={"SANDESH_ADDRESS": "Track 1 - Demo"})
        self.assertEqual(rc, 0, f"thread must exit 0; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(
            axi["bodies"].get(str(self.mid_long)),
            self.body_2000[:500] + " (truncated, 2000 chars total)",
        )
        self.assertTrue(any("thread" in h and "--full" in h for h in axi.get("help", [])))

    def test_thread_full_flag_returns_complete_body_without_truncation_help(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo",
             "--id", str(self.mid_long), "--full"],
            env={"SANDESH_ADDRESS": "Track 1 - Demo"})
        self.assertEqual(rc, 0, f"'thread --full' must be accepted; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["bodies"].get(str(self.mid_long)), self.body_2000)
        self.assertNotIn("help", axi)

    def test_thread_redacts_body_without_caller_identity_but_keeps_metadata(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(self.mid_long)])
        self.assertEqual(rc, 0, f"thread metadata must remain readable; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertTrue(any(row["id"] == self.mid_long for row in axi["chain"]))
        self.assertNotIn(str(self.mid_long), axi["bodies"])

    def test_thread_redacts_body_for_unrelated_same_project_caller(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(self.mid_long)],
            env={"SANDESH_ADDRESS": "Track 2 - Demo"})
        self.assertEqual(rc, 0, f"thread metadata must remain readable; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertNotIn(str(self.mid_long), axi["bodies"])

    def test_thread_allows_recipient_and_redacts_on_project_mismatch(self):
        sdb.setup("Other")
        sdb.register(self.con, "Mainline - Other", kind="mainline", project="Other")
        sdb.assign_admin(self.con, "TestAdmin")
        sdb.grant_xproj(self.con, "Demo", by="TestAdmin")
        mid = sdb.send(self.con, self.store, "Track 1 - Demo",
                       to=["Mainline - Other"], subject="cross-project",
                       body_text="recipient body", project="Demo")

        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Other", "--id", str(mid)],
            env={"SANDESH_ADDRESS": "Mainline - Other"})
        self.assertEqual(rc, 0, f"recipient must be allowed to read; err={err!r}")
        self.assertEqual(_toon.decode(out)["axi"]["bodies"][str(mid)], "recipient body")

        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(mid)],
            env={"SANDESH_ADDRESS": "Mainline - Other"})
        self.assertEqual(rc, 0, f"thread metadata must remain readable; err={err!r}")
        self.assertNotIn(str(mid), _toon.decode(out)["axi"]["bodies"])


class RelativeLegacyBodyPathTest(_BaseFixture):

    def _send_with_project_collision(self, recipient):
        sdb.setup("Other")
        sdb.register(self.con, "Mainline - Other", kind="mainline", project="Other")
        sdb.assign_admin(self.con, "TestAdmin")
        sdb.grant_xproj(self.con, "Demo", by="TestAdmin")
        mid = sdb.send(self.con, self.store, "Track 1 - Demo", to=[recipient],
                       subject="relative legacy body", body_text="AlphaOwnerBodyToken",
                       project="Demo")
        relative_path = f"messages/msg-{mid}.md"
        self.con.execute("UPDATE message SET body_path=? WHERE id=?", (relative_path, mid))
        self.con.commit()
        collision_path = os.path.join(sdb.store_dir("Other"), relative_path)
        os.makedirs(os.path.dirname(collision_path), exist_ok=True)
        with open(collision_path, "w", encoding="utf-8") as fh:
            fh.write("BetaCollisionBodyToken")
        return mid

    def test_thread_reads_relative_body_from_sender_project_not_request_project(self):
        mid = self._send_with_project_collision("Mainline - Other")
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Other", "--id", str(mid), "--full"],
            env={"SANDESH_ADDRESS": "Mainline - Other"})
        self.assertEqual(rc, 0, f"authorized recipient should read the body; err={err!r}")
        bodies = _toon.decode(out)["axi"]["bodies"]
        self.assertEqual(bodies[str(mid)], "AlphaOwnerBodyToken")
        self.assertNotEqual(bodies[str(mid)], "BetaCollisionBodyToken")

    def test_thread_does_not_read_a_colliding_body_for_an_unauthorized_message(self):
        mid = self._send_with_project_collision("Mainline - Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Other", "--id", str(mid), "--full"],
            env={"SANDESH_ADDRESS": "Mainline - Other"})
        self.assertEqual(rc, 0, f"thread metadata should remain readable; err={err!r}")
        self.assertNotIn(str(mid), _toon.decode(out)["axi"]["bodies"])

    def test_fetch_reads_relative_body_from_sender_project_not_recipient_project(self):
        mid = self._send_with_project_collision("Mainline - Other")
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Other", "--to", "Mainline - Other",
             "--peek", "--full"])
        self.assertEqual(rc, 0, f"authorized recipient should fetch the body; err={err!r}")
        bodies = _toon.decode(out)["axi"]["bodies"]
        self.assertEqual(bodies[str(mid)], "AlphaOwnerBodyToken")
        self.assertNotEqual(bodies[str(mid)], "BetaCollisionBodyToken")

    def test_reindex_uses_the_sender_project_for_relative_legacy_bodies(self):
        mid = self._send_with_project_collision("Mainline - Other")
        sdb.reindex(self.con)
        alpha = sdb.search(self.con, "Mainline - Other", "AlphaOwnerBodyToken")
        beta = sdb.search(self.con, "Mainline - Other", "BetaCollisionBodyToken")
        self.assertEqual([hit["id"] for hit in alpha["hits"]], [mid])
        self.assertEqual(beta["hits"], [])


# =========================================================================== #
# Item 2 — P9 help[] rules (AC11 remainder).
# =========================================================================== #

class HelpDisclosureTest(_BaseFixture):

    def setUp(self):
        super().setUp()
        self.mid1 = sdb.send(self.con, self.store, "Track 1 - Demo",
                             to=["Mainline - Demo"], subject="one", project="Demo")
        self.mid2 = sdb.send(self.con, self.store, "Track 2 - Demo",
                             to=["Mainline - Demo"], subject="two", project="Demo")

    def test_inbox_with_unread_help_present_and_placeholders_not_concrete_ids(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi, "inbox with unread mail must carry help[]")
        help_ = axi["help"]
        _assert_help_conforms(self, help_)
        self.assertTrue(
            any("fetch" in h and ("<id>" in h or "--to" in h) for h in help_),
            f"help[] must offer a fetch next-step; got {help_!r}")
        for h in help_:
            self.assertNotIn(f"#{self.mid1}", h, f"help must not name concrete id #{self.mid1}: {h!r}")
            self.assertNotIn(f"#{self.mid2}", h, f"help must not name concrete id #{self.mid2}: {h!r}")
            self.assertNotIn(f" {self.mid1} ", h, f"help must not name concrete id {self.mid1}: {h!r}")
            self.assertNotIn(f" {self.mid2} ", h, f"help must not name concrete id {self.mid2}: {h!r}")

    def test_inbox_empty_help_present_and_mentions_send(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "inbox", "--project", "Demo", "--to", "Track 1 - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi, "an empty inbox must still carry help[] (P9: after empty -> produce)")
        self.assertTrue(any("send" in h for h in axi["help"]),
                        f"help[] must suggest 'send' after an empty inbox; got {axi['help']!r}")

    def test_addressbook_empty_project_help_mentions_register(self):
        sdb.setup("Empty")
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Empty"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi, "an empty addressbook must carry help[]")
        self.assertTrue(any("register" in h for h in axi["help"]),
                        f"help[] must suggest 'register' for an empty addressbook; got {axi['help']!r}")

    def test_addressbook_nonempty_help_mentions_send_or_notify(self):
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi)
        self.assertTrue(any("send" in h or "notify" in h for h in axi["help"]),
                        f"help[] must mention send/notify; got {axi['help']!r}")

    def test_send_help_present_names_thread_with_id_placeholder(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "send", "--project", "Demo", "--from", "Track 1 - Demo",
             "--to", "Mainline - Demo", "--subject", "hi"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi)
        self.assertTrue(any("thread" in h and "<id>" in h for h in axi["help"]),
                        f"help[] must offer 'thread --id <id>'; got {axi['help']!r}")

    def test_reply_help_present_names_thread_with_id_placeholder(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "reply", "--project", "Demo", "--from", "Mainline - Demo",
             "--to-msg", str(self.mid1)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi)
        self.assertTrue(any("thread" in h and "<id>" in h for h in axi["help"]),
                        f"help[] must offer 'thread --id <id>'; got {axi['help']!r}")

    def test_register_help_present_mentions_notify(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "register", "--project", "Demo",
             "--address", "Track 3 - Demo", "--kind", "track"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi)
        self.assertTrue(any("notify" in h for h in axi["help"]),
                        f"help[] must mention notify after register; got {axi['help']!r}")

    def test_unregister_carries_no_help_confirmation(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "unregister", "--project", "Demo",
             "--address", "Track 1 - Demo", "--as", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertNotIn("help", axi, "unregister is a confirmation -> no help[]")

    def test_fetch_carries_no_help_key(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "fetch", "--project", "Demo", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertNotIn("help", axi, "fetch is a detail view -> no help[]")

    def test_thread_carries_no_help_key(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(self.mid1)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertNotIn("help", axi, "thread is a detail view -> no help[]")

    def test_search_with_hits_help_present(self):
        sdb.send(self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
                 subject="helpsearchxyz", body_text="helpsearchxyz body", project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "helpsearchxyz", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi, "search with hits must carry help[]")
        _assert_help_conforms(self, axi["help"])

    def test_projects_help_present(self):
        rc, out, err = self.run_cli(["--format", "toon", "projects"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("help", axi, "projects must carry help[]")
        _assert_help_conforms(self, axi["help"])


# =========================================================================== #
# Item 3 — remaining verbs' generic envelopes (§S4 last sentences).
# =========================================================================== #

class RemainingVerbsEnvelopeTest(_BaseFixture):

    def test_search_default_columns_and_int_aggregates(self):
        sdb.send(self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
                 subject="searchable pingxyz", body_text="pingxyz body content",
                 project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "pingxyz", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("hits", axi)
        rows = axi["hits"]
        self.assertEqual(len(rows), 1)
        for row in rows:
            self.assertEqual(set(row.keys()), {"id", "from", "subject", "snippet"})
        self.assertIn("total", axi)
        self.assertIsInstance(axi["total"], int)
        self.assertEqual(axi["total"], 1)
        self.assertIn("limit", axi)
        self.assertEqual(axi["limit"], 20, "the CLI's --limit default is 20")
        self.assertIn("offset", axi)
        self.assertEqual(axi["offset"], 0, "the CLI's --offset default is 0")

    def test_search_zero_hit_query_reports_empty_state_sentence(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "zzzznomatchxyz", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("hits"), '0 for "zzzznomatchxyz"')

    def test_thread_default_columns_two_message_chain_incomplete_false(self):
        # `thread` walks root-ward from the queried id (CLAUDE.md #6), so the
        # two-node chain is reached by querying the REPLY (mid_b), not the root.
        mid_a = sdb.send(self.con, self.store, "Track 1 - Demo",
                         to=["Mainline - Demo"], subject="root", project="Demo")
        mid_b = sdb.reply(self.con, self.store, mid_a, "Track 2 - Demo", project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(mid_b)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("chain", axi)
        rows = axi["chain"]
        self.assertEqual(len(rows), 2)
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {mid_a, mid_b})
        for row in rows:
            self.assertEqual(set(row.keys()), {"id", "from", "subject"})
        self.assertIs(axi.get("incomplete"), False)
        self.assertEqual(axi.get("bodies"), {})

    def test_thread_full_includes_body_for_each_visible_message(self):
        mid_a = sdb.send(self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
                         subject="thread body root", body_text="root body", project="Demo")
        mid_b = sdb.reply(self.con, self.store, mid_a, "Track 2 - Demo", project="Demo")
        rc, out, err = self.run_cli([
            "--format", "toon", "thread", "--project", "Demo", "--id", str(mid_b), "--full",
        ], env={"SANDESH_ADDRESS": "Track 1 - Demo"})
        self.assertEqual(rc, 0, f"thread --full must exit 0; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["bodies"], {str(mid_a): "root body"})

    def test_thread_fields_flag_widens_to_five_columns(self):
        mid_a = sdb.send(self.con, self.store, "Track 1 - Demo",
                         to=["Mainline - Demo"], subject="root2", project="Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "thread", "--project", "Demo", "--id", str(mid_a),
             "--fields", "id,from,subject,created,re"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("chain", axi)
        for row in axi["chain"]:
            self.assertEqual(set(row.keys()), {"id", "from", "subject", "created", "re"})

    def test_projects_default_rows_have_project_state_cross_project_bool(self):
        rc, out, err = self.run_cli(["--format", "toon", "projects"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIn("projects", axi)
        rows = {r["project"]: r for r in axi["projects"]}
        self.assertIn("Demo", rows)
        row = rows["Demo"]
        self.assertEqual(set(row.keys()), {"project", "state", "cross_project"})
        self.assertEqual(row["state"], "active")
        self.assertIsInstance(row["cross_project"], bool)
        self.assertIs(row["cross_project"], False, "Demo has no cross-project grant")

    def test_setup_new_project_fields_and_ok_true(self):
        rc, out, err = self.run_cli(["--format", "toon", "setup", "--project", "NewProj"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi.get("project"), "NewProj")
        self.assertIsInstance(axi.get("store"), str)
        self.assertTrue(axi.get("store"), "store must be a non-empty path string")

    def test_grant_cross_project_sets_field_true(self):
        sdb.assign_admin(self.con, "TheAdmin")
        rc, out, err = self.run_cli(
            ["--format", "toon", "grant", "--cross-project", "--project", "Demo",
             "--by", "TheAdmin"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("project"), "Demo")
        self.assertIs(axi.get("cross_project"), True)

    def test_revoke_cross_project_sets_field_false(self):
        sdb.assign_admin(self.con, "TheAdmin")
        sdb.grant_xproj(self.con, "Demo", "TheAdmin")
        rc, out, err = self.run_cli(
            ["--format", "toon", "revoke", "--cross-project", "--project", "Demo",
             "--by", "TheAdmin"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("project"), "Demo")
        self.assertIs(axi.get("cross_project"), False)

    def test_projects_all_envelope_shape_matches_human_rc(self):
        rc_human, _, _ = self.run_cli(["projects", "--all"])
        rc, out, err = self.run_cli(["--format", "toon", "projects", "--all"])
        self.assertEqual(rc, rc_human, f"machine-mode exit must match human mode; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "projects")
        self.assertEqual(axi["ok"], (rc == 0))

    def test_init_check_envelope_shape_matches_human_rc_and_has_steps(self):
        rc_human, _, _ = self.run_cli(["init", "--check"])
        rc, out, err = self.run_cli(["--format", "toon", "init", "--check"])
        self.assertEqual(rc, rc_human, f"machine-mode exit must match human mode; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "init")
        self.assertEqual(axi["ok"], (rc == 0))
        self.assertIn("steps", axi)
        self.assertIsInstance(axi["steps"], list)
        for step in axi["steps"]:
            self.assertIsInstance(step, dict)
            self.assertIn("step", step)
            self.assertIn("result", step)

    def test_migrate_status_envelope_shape_matches_human_rc_and_has_steps(self):
        rc_human, _, _ = self.run_cli(["migrate", "--status"])
        rc, out, err = self.run_cli(["--format", "toon", "migrate", "--status"])
        self.assertEqual(rc, rc_human, f"machine-mode exit must match human mode; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "migrate")
        self.assertEqual(axi["ok"], (rc == 0))
        self.assertIn("steps", axi)
        self.assertIsInstance(axi["steps"], list, "steps may be empty for --status but must be a list")
        for step in axi["steps"]:
            self.assertIsInstance(step, dict)
            self.assertIn("step", step)
            self.assertIn("result", step)

    def test_consolidate_envelope_shape_matches_human_rc_and_has_steps(self):
        rc_human, _, _ = self.run_cli(["consolidate"])
        rc, out, err = self.run_cli(["--format", "toon", "consolidate"])
        self.assertEqual(rc, rc_human, f"machine-mode exit must match human mode; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "consolidate")
        self.assertEqual(axi["ok"], (rc == 0))
        self.assertIn("steps", axi)
        self.assertIsInstance(axi["steps"], list)
        for step in axi["steps"]:
            self.assertIsInstance(step, dict)
            self.assertIn("step", step)
            self.assertIn("result", step)

    def test_reindex_envelope_shape_matches_human_rc_and_has_steps(self):
        rc_human, _, _ = self.run_cli(["reindex"])
        rc, out, err = self.run_cli(["--format", "toon", "reindex"])
        self.assertEqual(rc, rc_human, f"machine-mode exit must match human mode; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "reindex")
        self.assertEqual(axi["ok"], (rc == 0))
        self.assertIn("steps", axi)
        self.assertIsInstance(axi["steps"], list)
        for step in axi["steps"]:
            self.assertIsInstance(step, dict)
            self.assertIn("step", step)
            self.assertIn("result", step)

    def test_tombstone_without_yes_machine_mode_exits_2_names_yes_flag(self):
        sdb.assign_admin(self.con, "TheAdmin")
        sdb.archive(self.con, "Demo", "Mainline - Demo")

        class _Alarm(Exception):
            pass

        def _handler(signum, frame):
            raise _Alarm("tombstone without --yes must not prompt in machine mode")

        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(5)
        try:
            with mock.patch.object(sys, "stdin", io.StringIO("")):
                rc, out, err = self.run_cli(
                    ["--format", "toon", "tombstone", "--project", "Demo", "--by", "TheAdmin"])
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        self.assertEqual(rc, 2, f"no --yes in machine mode must exit 2 (no prompt); out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("--yes", axi.get("error", ""),
                      f"error must name --yes; got {axi.get('error')!r}")

    def test_tombstone_yes_on_an_active_project_surfaces_the_refusal_reason_exit_1(self):
        sdb.assign_admin(self.con, "TheAdmin")
        rc, out, err = self.run_cli(
            ["--format", "toon", "tombstone", "--project", "Demo", "--by", "TheAdmin", "--yes"])
        self.assertEqual(rc, 1, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("archive it first", axi["error"])
        self.assertEqual(sdb.project_state(self.con, "Demo"), "active")

    def test_tombstone_yes_by_a_non_admin_surfaces_the_refusal_reason_exit_1(self):
        sdb.assign_admin(self.con, "TheAdmin")
        sdb.archive(self.con, "Demo", "Mainline - Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "tombstone", "--project", "Demo", "--by", "Nobody", "--yes"])
        self.assertEqual(rc, 1, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("only the Sandesh admin may tombstone a project", axi["error"])
        self.assertEqual(sdb.project_state(self.con, "Demo"), "archived")

    def test_tombstone_yes_succeeds_then_a_second_call_is_result_already_exit_0(self):
        sdb.assign_admin(self.con, "TheAdmin")
        sdb.archive(self.con, "Demo", "Mainline - Demo")
        argv = ["--format", "toon", "tombstone", "--project", "Demo", "--by", "TheAdmin", "--yes"]
        rc, out, err = self.run_cli(argv)
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["state"], "tombstoned")
        self.assertNotIn("result", axi)
        self.assertEqual(sdb.project_state(self.con, "Demo"), "tombstoned")
        rc, out, err = self.run_cli(argv)
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["result"], "already")
        self.assertEqual(axi["state"], "tombstoned")


# =========================================================================== #
# Item 4 — AC14 home view (P8/P10).
# =========================================================================== #

class HomeViewTest(_BaseFixture):

    def _seed_two_unread(self):
        sdb.send(self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
                 subject="a", project="Demo")
        sdb.send(self.con, self.store, "Track 2 - Demo", to=["Mainline - Demo"],
                 subject="b", project="Demo")

    def test_no_subcommand_toon_mode_prints_status_dashboard_and_exits_0(self):
        self._seed_two_unread()
        env = {"SANDESH_FORMAT": "toon", "SANDESH_PROJECT": "Demo",
               "SANDESH_ADDRESS": "Mainline - Demo"}
        rc, out, err = self.run_cli([], env=env)
        self.assertEqual(rc, 0, f"home view must exit 0; out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "status")
        bin_ = axi.get("bin")
        self.assertIsInstance(bin_, str)
        self.assertTrue(bin_.startswith(("~", "/")),
                        f"bin must be a '~'-collapsed or absolute path; got {bin_!r}")
        self.assertTrue(axi.get("description"), "description must be a non-empty string")
        self.assertEqual(axi.get("project"), "Demo")
        self.assertEqual(axi.get("address"), "Mainline - Demo")
        self.assertIsInstance(axi.get("listening"), bool)
        self.assertIs(axi.get("listening"), False, "no live notifier -> listening:false")
        self.assertEqual(axi.get("unread"), 2)
        help_ = axi.get("help")
        self.assertIsInstance(help_, list)
        self.assertEqual(len(help_), 2, f"home view must carry exactly 2 help[] entries; got {help_!r}")

    def test_home_view_counts_unread_cc_messages(self):
        sdb.send(self.con, self.store, "Track 1 - Demo", to=[], cc=["Mainline - Demo"],
                 subject="cc-only", project="Demo")
        rc, out, err = self.run_cli(["--format", "toon", "status"], env={
            "SANDESH_PROJECT": "Demo", "SANDESH_ADDRESS": "Mainline - Demo",
        })
        self.assertEqual(rc, 0, f"status must include unread Cc mail; err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["unread"], 1)

    def test_status_on_absent_store_reports_zeros_without_creating_it(self):
        empty = tempfile.mkdtemp(prefix="sandesh-axi-status-absent-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        os.environ["XDG_DATA_HOME"] = empty
        rc, out, err = self.run_cli(["--format", "toon", "status"], env={
            "SANDESH_PROJECT": "Demo", "SANDESH_ADDRESS": "Mainline - Demo",
        })
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["unread"], 0)
        self.assertIs(axi["listening"], False)
        self.assertEqual(os.listdir(empty), [], "status must not create the store")

    def test_status_on_schema_behind_store_does_not_migrate_it(self):
        self.con.execute("CREATE TABLE _yoyo_migration (migration_id TEXT)")
        self.con.commit()
        rc, out, err = self.run_cli(["--format", "toon", "status"], env={
            "SANDESH_PROJECT": "Demo", "SANDESH_ADDRESS": "Mainline - Demo",
        })
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        applied = self.con.execute("SELECT COUNT(*) FROM _yoyo_migration").fetchone()[0]
        self.assertEqual(applied, 0, "status must not auto-apply migrations")

    def test_status_rejects_an_address_from_a_different_project(self):
        sdb.setup("Beta")  # CR-SAN-054 S5: must be a KNOWN project so the
        # unknown-project guard doesn't preempt this test's real target, the
        # address/project MISMATCH error.
        env = {"SANDESH_ADDRESS": "Mainline - Demo"}
        rc_human, _, err_human = self.run_cli(
            ["--format", "human", "--project", "Beta", "status"], env=env,
        )
        self.assertEqual(rc_human, 1)
        self.assertIn("address project 'Demo' != project_id 'Beta'", err_human)
        rc, out, err = self.run_cli(
            ["--format", "toon", "--project", "Beta", "status"], env=env,
        )
        self.assertEqual(rc, 2, f"machine status must reject mismatched identity; out={out!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("address project 'Demo' != project_id 'Beta'", axi["error"])
        self.assertNotIn("unread", axi)

    def test_no_subcommand_live_notifier_flips_listening_true(self):
        sdb.notifier_acquire(self.con, "Mainline - Demo", os.getpid(), "tok-1", "host")
        env = {"SANDESH_FORMAT": "toon", "SANDESH_PROJECT": "Demo",
               "SANDESH_ADDRESS": "Mainline - Demo"}
        rc, out, err = self.run_cli([], env=env)
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi.get("listening"), True,
                      "a live notifier row for the address must flip listening:true")

    def test_explicit_status_verb_matches_no_subcommand_envelope(self):
        self._seed_two_unread()
        env = {"SANDESH_FORMAT": "toon", "SANDESH_PROJECT": "Demo",
               "SANDESH_ADDRESS": "Mainline - Demo"}
        rc, out, err = self.run_cli(["status"], env=env)
        self.assertEqual(rc, 0, f"'sandesh status' must exit 0; out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["verb"], "status")
        self.assertEqual(axi.get("project"), "Demo")
        self.assertEqual(axi.get("address"), "Mainline - Demo")
        self.assertIsInstance(axi.get("listening"), bool)
        self.assertEqual(axi.get("unread"), 2)
        self.assertEqual(len(axi.get("help", [])), 2)

    def test_no_subcommand_toon_mode_without_address_exits_2_names_both_vars(self):
        env = {"SANDESH_FORMAT": "toon", "SANDESH_PROJECT": "Demo"}  # no SANDESH_ADDRESS
        rc, out, err = self.run_cli([], env=env)
        self.assertEqual(rc, 2, f"missing $SANDESH_ADDRESS must exit 2; out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("SANDESH_ADDRESS", axi.get("error", ""),
                      f"error must name SANDESH_ADDRESS; got {axi.get('error')!r}")
        self.assertIn("SANDESH_PROJECT", axi.get("error", ""),
                      f"error must name SANDESH_PROJECT; got {axi.get('error')!r}")

    def test_no_subcommand_human_mode_unchanged_exit_2_usage_stderr_empty_stdout(self):
        # Regression pin per the CR: human mode's no-subcommand path is
        # explicitly unchanged — this specific assertion may already hold.
        rc, out, err = self.run_cli([])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "", f"human mode must write nothing to stdout; out={out!r}")
        self.assertIn("usage:", err.lower(), f"argparse usage must be on stderr; err={err!r}")

    def test_bogus_global_flag_toon_mode_exit_2_help_lists_global_flags(self):
        rc, out, err = self.run_cli(["--format", "toon", "--bogus"])
        self.assertEqual(rc, 2, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        help_ = axi.get("help", [])
        self.assertTrue(any("--project" in h for h in help_),
                        f"help[] must list the valid global flags incl. --project; got {help_!r}")
        self.assertTrue(any("--format" in h for h in help_),
                        f"help[] must list the valid global flags incl. --format; got {help_!r}")


class StepDiagnosticEnvelopeTest(_BaseFixture):
    def test_system_exit_diagnostics_survive_all_step_handlers(self):
        handlers = ("init", "migrate", "consolidate", "reindex")
        for verb in handlers:
            def fail(_args, command=verb):
                print(f"[sandesh] diagnostic from {command}", file=cli.sys.stderr)
                raise SystemExit(1)

            with mock.patch.object(cli, f"cmd_{verb}", fail):
                rc, out, err = self.run_cli(["--format", "toon", verb])
            self.assertEqual(rc, 1, f"{verb} should preserve the handler exit code")
            axi = _toon.decode(out)["axi"]
            self.assertEqual(axi["error"], f"diagnostic from {verb}")
            self.assertEqual(axi["steps"], [])
            self.assertTrue(axi.get("help"))


# =========================================================================== #
# AC3 (CR-SAN-050 §S3) — archive/unarchive/tombstone --dry-run envelope fields.
# =========================================================================== #

class DryRunEnvelopeTest(_BaseFixture):
    """AC3 — `archive`/`unarchive --dry-run --format toon` decode with the
    preview fields (`dry_run: true`, `state`, `evicted[N]`, `project`)
    instead of `{}`; `tombstone --dry-run` additionally carries the purge
    counts (`messages`, `bodies`, `cross_project`, all ints). Nothing is
    written by any of the three — the project's tracker state is unchanged
    after each dry-run call.

    RED today: `cli.py`'s `axi_archive`/`axi_unarchive` short-circuit
    `--dry-run` to `return cmd_archive(args) or 0, {}` / `return
    cmd_unarchive(args) or 0, {}` — an EMPTY fields dict regardless of what
    the human preview printed; `axi_tombstone` does the same
    (`{} if args.dry_run or rc else {...}`).
    """

    def test_archive_dry_run_envelope_carries_preview_fields_and_writes_nothing(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "archive", "--project", "Demo",
             "--by", "Mainline - Demo", "--dry-run"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi.get("dry_run"), True,
                      f"archive --dry-run envelope must carry dry_run:true; got {axi!r}")
        self.assertEqual(axi.get("state"), "archived",
                         f"archive --dry-run must report the would-be state; got {axi!r}")
        self.assertIsInstance(axi.get("evicted"), list,
                              f"archive --dry-run must carry an 'evicted' list; got {axi!r}")
        self.assertEqual(axi.get("project"), "Demo")
        self.assertEqual(
            sdb.project_state(self.con, "Demo"), "active",
            "archive --dry-run must write NOTHING — project state must still be 'active'")

    def test_unarchive_dry_run_envelope_carries_preview_fields_and_writes_nothing(self):
        sdb.archive(self.con, "Demo", "Mainline - Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "unarchive", "--project", "Demo",
             "--by", "Mainline - Demo", "--dry-run"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi.get("dry_run"), True,
                      f"unarchive --dry-run envelope must carry dry_run:true; got {axi!r}")
        self.assertEqual(axi.get("state"), "active",
                         f"unarchive --dry-run must report the would-be state; got {axi!r}")
        self.assertEqual(
            sdb.project_state(self.con, "Demo"), "archived",
            "unarchive --dry-run must write NOTHING — project state must still be 'archived'")

    def test_tombstone_dry_run_envelope_carries_dry_run_flag_and_int_purge_counts(self):
        sdb.assign_admin(self.con, "TheAdmin")
        sdb.archive(self.con, "Demo", "Mainline - Demo")
        rc, out, err = self.run_cli(
            ["--format", "toon", "tombstone", "--project", "Demo",
             "--by", "TheAdmin", "--dry-run"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi.get("dry_run"), True,
                      f"tombstone --dry-run envelope must carry dry_run:true; got {axi!r}")
        for key in ("messages", "bodies", "cross_project"):
            self.assertIn(key, axi, f"tombstone --dry-run must carry a {key!r} purge count; got {axi!r}")
            self.assertIsInstance(axi[key], int,
                                  f"tombstone --dry-run's {key!r} count must be an int; got {axi[key]!r}")
        self.assertEqual(
            sdb.project_state(self.con, "Demo"), "archived",
            "tombstone --dry-run must write NOTHING — project state must still be 'archived'")


if __name__ == "__main__":
    unittest.main()
