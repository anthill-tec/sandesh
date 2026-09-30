"""test_axi_cli_format.py — RED tests for CR-SAN-047 cycle 205 (§S3 `--format`
plumbing + the usage-error part of §S4b), against:
  docs/changes/CR-SAN-047-axi-toon-cli-envelope.md  AC3, AC4, AC7
  docs/research/PRD-axi-toon.md  §4.3

Scope for THIS cycle is ONLY the plumbing (`--format {human,toon,json}` on the
shared `common` parent, resolved flag > $SANDESH_FORMAT > human; human-mode
printers redirected to stderr in machine modes; the dispatcher emits one
envelope to stdout; ValueError/PermissionError -> error_envelope + the
existing exit code; a pre-argparse usage-error envelope). Per-verb field
contracts (§S4, AC5/AC6/AC11/etc.) are cycle 206 — assertions below only ever
check the plumbing-level shape: `axi.verb`, `axi.ok`, `axi.context.project`,
`axi.error`, `axi.help` (presence/non-emptiness), never verb-specific fields.

`sandesh/axi.py` (Envelope/render/emit/error_envelope) and `sandesh/_toon.py`
(decode) already shipped in cycle 204 (`tests/test_axi_envelope.py`,
`tests/test_toon_vendor.py`) — this file exercises them through the CLI.

  python3 -m unittest -v tests.test_axi_cli_format   (from the repo root)
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_cli_format.py

------------------------------------------------------------------------------
AC3 golden capture procedure (goldens committed under tests/golden/, captured
from the CURRENT tree — verified byte-identical to v0.3.6 for the human-mode
surface via `git diff --stat v0.3.6 -- sandesh/cli.py sandesh/notify.py` ==
empty on this branch at the RED commit):

For EACH of the eight verbs (addressbook, inbox, fetch, send, reply, register,
unregister, thread), in a FRESH, isolated `XDG_DATA_HOME` temp dir:

  1. `sdb.setup("Demo")`
  2. `con = sdb.connect(); store = sdb.store_dir("Demo")`
  3. `sdb.register(con, "Mainline - Demo", kind="mainline", project="Demo")`
  4. `sdb.register(con, "Track 1 - Demo", kind="track", project="Demo")`
  5. `sdb.send(con, store, "Track 1 - Demo", to=["Mainline - Demo"],
     subject="ping", project="Demo")`   # message #1, subject-only
  6. (unregister only) additionally, via the library (not the CLI, so it
     doesn't appear in the captured stdout):
     `sdb.register(con, "Track 2 - Demo", kind="track", project="Demo")`
  7. Run `cli.main(argv)` for the verb (argv listed in ARGV_BY_VERB below),
     with stdout captured via `contextlib.redirect_stdout`.
  8. Normalize: replace every ISO-ish timestamp matching
     `r"\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}"` with the literal `<TS>`
     (both `registered_at` in `addressbook` and `created_at` in `fetch`/
     `thread`; `inbox`/`send`/`reply`/`register`/`unregister` have none).
  9. Write the normalized text to `tests/golden/<verb>.human.txt`.

Capture script used: /tmp/capture_axi_goldens.py (throwaway, not committed —
reproduced verbatim by the steps above + ARGV_BY_VERB). Re-running the script
twice produced byte-identical goldens (`diff -r` clean) — confirming the
fixture is fully deterministic (fresh autoincrement ids, no wall-clock content
left unmasked).
------------------------------------------------------------------------------
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import re
import shutil
import sqlite3
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

_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _normalize_ts(text):
    return _TS_RE.sub("<TS>", text)


def _load_golden(verb):
    with open(os.path.join(_GOLDEN_DIR, f"{verb}.human.txt"), encoding="utf-8") as fh:
        return fh.read()


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Handles both ways `main()` signals its outcome:
      - a plain return value (int, e.g. 0 from cmd_addressbook), and
      - `SystemExit` (argparse errors -> exit(2); `sys.exit(<int>)`; and the
        house `sys.exit(f"[sandesh] {msg}")` single-arg idiom used by
        cmd_register/cmd_unregister/cmd_send/cmd_reply/cmd_thread/cmd_notify
        on their ValueError/PermissionError/FileNotFoundError paths).

    For the single-arg-string form, CPython's own top-level SystemExit
    handler (Modules/main.c `handle_system_exit`) prints the string to
    stderr and exits status 1 -- but that conversion only happens when the
    exception is left to propagate out of the real process. Since this
    harness catches SystemExit itself (matching every other CLI test in this
    repo, e.g. tests/test_lifecycle_cli.py), it reproduces that exact
    conversion here so "exit code" in these tests means what a real
    subprocess would report (this is what AC7 means by "the same code as
    human mode").
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
# Fixture: 'Demo' project, Mainline + Track 1 registered, message #1 'ping'
# (subject-only, Track 1 -> Mainline).
# --------------------------------------------------------------------------- #

class _DemoFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-cli-format-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_sandesh_format = os.environ.pop("SANDESH_FORMAT", None)

        sdb.setup("Demo")
        self.con = sdb.connect()
        self.store = sdb.store_dir("Demo")
        sdb.register(self.con, "Mainline - Demo", kind="mainline", project="Demo")
        sdb.register(self.con, "Track 1 - Demo", kind="track", project="Demo")
        self.mid_ping = sdb.send(self.con, self.store, "Track 1 - Demo",
                                  to=["Mainline - Demo"], subject="ping",
                                  project="Demo")

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        if self._prev_sandesh_format is None:
            os.environ.pop("SANDESH_FORMAT", None)
        else:
            os.environ["SANDESH_FORMAT"] = self._prev_sandesh_format
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, argv, env=None):
        return run_cli(argv, env=env)


# --------------------------------------------------------------------------- #
# AC4 — placement (before/after the subcommand) + $SANDESH_FORMAT + the
# toon/json round trip + the invalid-value error.
# --------------------------------------------------------------------------- #

class PlacementAndEnvTest(_DemoFixture):

    def test_format_before_after_subcommand_and_env_var_all_produce_identical_stdout(self):
        rc_before, out_before, _ = self.run_cli(
            ["--format", "toon", "addressbook", "--project", "Demo"])
        rc_after, out_after, _ = self.run_cli(
            ["addressbook", "--project", "Demo", "--format", "toon"])
        rc_env, out_env, _ = self.run_cli(
            ["addressbook", "--project", "Demo"], env={"SANDESH_FORMAT": "toon"})

        self.assertEqual(rc_before, 0, "--format before subcommand must exit 0")
        self.assertEqual(rc_after, 0, "--format after subcommand must exit 0")
        self.assertEqual(rc_env, 0, "$SANDESH_FORMAT=toon with no flag must exit 0")
        self.assertEqual(out_before, out_after,
                          "--format before vs after the subcommand must be byte-identical")
        self.assertEqual(out_before, out_env,
                          "$SANDESH_FORMAT=toon (no flag) must match an explicit --format toon")

    def test_toon_addressbook_decodes_to_envelope_with_verb_ok_context_project_and_warnings(self):
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"expected exit 0; err={err!r}")
        decoded = _toon.decode(out)
        axi = decoded["axi"]
        self.assertEqual(axi["verb"], "addressbook")
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["context"]["project"], "Demo")
        self.assertIn("warnings", axi)

    def test_format_json_decodes_to_the_same_dict_as_the_toon_output(self):
        rc_toon, out_toon, _ = self.run_cli(
            ["addressbook", "--project", "Demo", "--format", "toon"])
        rc_json, out_json, _ = self.run_cli(
            ["addressbook", "--project", "Demo", "--format", "json"])
        self.assertEqual(rc_toon, 0)
        self.assertEqual(rc_json, 0)
        self.assertEqual(json.loads(out_json), _toon.decode(out_toon))

    def test_env_var_xml_with_no_flag_exits_2_and_names_the_three_formats(self):
        rc, out, err = self.run_cli(
            ["addressbook", "--project", "Demo"], env={"SANDESH_FORMAT": "xml"})
        self.assertEqual(rc, 2, f"invalid $SANDESH_FORMAT must exit 2; out={out!r} err={err!r}")
        combined = out + err
        for fmt in ("human", "toon", "json"):
            self.assertIn(fmt, combined,
                           f"error message must name {fmt!r}; got out={out!r} err={err!r}")

    def test_explicit_format_xml_flag_is_an_argparse_choices_error_exit_2(self):
        rc, out, err = self.run_cli(["addressbook", "--project", "Demo", "--format", "xml"])
        self.assertEqual(rc, 2)
        combined = out + err
        for fmt in ("human", "toon", "json"):
            self.assertIn(fmt, combined,
                           f"argparse choices error must name {fmt!r}; got out={out!r} err={err!r}")


# --------------------------------------------------------------------------- #
# stdout/stderr separation — in machine mode the ONLY thing on stdout is the
# envelope; the pre-existing human printers move to stderr.
# --------------------------------------------------------------------------- #

class StdoutStderrSeparationTest(_DemoFixture):

    def test_addressbook_toon_mode_stdout_is_exactly_one_envelope_human_lines_on_stderr(self):
        rc, out, err = self.run_cli(["--format", "toon", "addressbook", "--project", "Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        self.assertTrue(out.strip().startswith("axi:"),
                         f"stdout must be the envelope; got {out!r}")
        self.assertEqual(out.count("axi:"), 1, f"exactly one envelope; got out={out!r}")
        _toon.decode(out)  # must parse cleanly as the sole content
        self.assertNotIn("[sandesh]", out)
        # 0.3.6's addressbook table header + the LISTENING marker move to stderr.
        self.assertIn("LISTENING", err, f"human table header must be on stderr; err={err!r}")
        self.assertIn("Mainline - Demo", err)
        self.assertNotIn("LISTENING", out)

    def test_register_toon_mode_stdout_is_exactly_one_envelope_registered_line_on_stderr(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "register", "--project", "Demo",
             "--address", "Track 2 - Demo", "--kind", "track"])
        self.assertEqual(rc, 0, f"err={err!r}")
        self.assertTrue(out.strip().startswith("axi:"), f"stdout must be the envelope; got {out!r}")
        self.assertEqual(out.count("axi:"), 1)
        decoded = _toon.decode(out)
        self.assertEqual(decoded["axi"]["verb"], "register")
        self.assertIs(decoded["axi"]["ok"], True)
        self.assertNotIn("registered:", out)
        self.assertIn("registered: Track 2 - Demo", err,
                       f"0.3.6's 'registered: ...' line must move to stderr; err={err!r}")


# --------------------------------------------------------------------------- #
# AC7 — failure envelope: ok:false + the human-mode message (sans its
# '[sandesh] ' prefix), same exit code as human mode, nothing else on stdout.
# --------------------------------------------------------------------------- #

class FailureEnvelopeTest(_DemoFixture):

    def _assert_matches_human(self, human_argv, verb):
        rc_human, out_human, err_human = self.run_cli(human_argv)
        self.assertNotEqual(rc_human, 0, "sanity: the scenario must fail in human mode too")
        self.assertTrue(err_human.strip().startswith("[sandesh]"),
                         f"human-mode error must carry the '[sandesh]' prefix; err={err_human!r}")
        expected_message = err_human.strip()[len("[sandesh]"):].strip()

        toon_argv = ["--format", "toon"] + human_argv
        rc_toon, out_toon, err_toon = self.run_cli(toon_argv)

        self.assertEqual(rc_toon, rc_human,
                          f"toon-mode exit code must equal human mode's; "
                          f"human={rc_human!r} toon={rc_toon!r}")
        self.assertNotIn("[sandesh]", out_toon, f"stdout must carry only the envelope; out={out_toon!r}")
        self.assertEqual(out_toon.count("axi:"), 1, f"exactly one envelope; out={out_toon!r}")
        decoded = _toon.decode(out_toon)
        axi = decoded["axi"]
        self.assertEqual(axi["verb"], verb)
        self.assertIs(axi["ok"], False)
        self.assertEqual(axi["error"], expected_message)
        return expected_message

    def test_send_to_unregistered_recipient_ac7_error_envelope(self):
        human_argv = ["send", "--project", "Demo", "--from", "Track 1 - Demo",
                      "--to", "Nobody - Demo", "--subject", "x"]
        expected_message = self._assert_matches_human(human_argv, "send")
        self.assertEqual(expected_message, "unknown or inactive recipient: 'Nobody - Demo'")

    def test_register_wrong_project_grammar_ac7_error_envelope(self):
        human_argv = ["register", "--project", "Demo", "--address", "Track 1 - Wrong",
                      "--kind", "track"]
        expected_message = self._assert_matches_human(human_argv, "register")
        self.assertEqual(expected_message, "address project 'Wrong' != project_id 'Demo'")

    def test_unregister_by_non_mainline_of_someone_else_ac7_error_envelope(self):
        human_argv = ["unregister", "--project", "Demo", "--address", "Mainline - Demo",
                      "--as", "Track 1 - Demo"]
        expected_message = self._assert_matches_human(human_argv, "unregister")
        self.assertEqual(expected_message, "only Mainline may remove another participant")


# --------------------------------------------------------------------------- #
# §S4b — usage errors in machine mode: exit 2, ok:false + error naming the
# offending flag + a non-empty help[]; human mode stays argparse's usage on
# stderr with an empty stdout (unchanged).
# --------------------------------------------------------------------------- #

class UsageErrorMachineModeTest(_DemoFixture):

    def test_bogus_flag_toon_mode_exit_2_ok_false_error_names_flag_nonempty_help(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "addressbook", "--project", "Demo", "--bogus"])
        self.assertEqual(rc, 2, f"usage error must exit 2; out={out!r} err={err!r}")
        decoded = _toon.decode(out)
        axi = decoded["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("--bogus", axi["error"],
                       f"error must name the offending flag; got {axi['error']!r}")
        self.assertIn("help", axi)
        self.assertIsInstance(axi["help"], list)
        self.assertGreater(len(axi["help"]), 0, "help[] must be non-empty on a usage error")

    def test_bogus_flag_human_mode_unchanged_exit_2_usage_on_stderr_empty_stdout(self):
        rc, out, err = self.run_cli(["addressbook", "--project", "Demo", "--bogus"])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "", f"human mode must write nothing to stdout; out={out!r}")
        self.assertIn("usage:", err.lower(), f"argparse usage must be on stderr; err={err!r}")


# --------------------------------------------------------------------------- #
# AC3 — byte-identical human mode: no-flag == --format human == the golden
# captured from the pre-CR tree (see module docstring for the procedure).
# --------------------------------------------------------------------------- #

ARGV_BY_VERB = {
    "addressbook": lambda: ["addressbook", "--project", "Demo"],
    "inbox": lambda: ["inbox", "--project", "Demo", "--to", "Mainline - Demo"],
    "fetch": lambda: ["fetch", "--project", "Demo", "--to", "Mainline - Demo"],
    "send": lambda: ["send", "--project", "Demo", "--from", "Track 1 - Demo",
                      "--to", "Mainline - Demo", "--subject", "status update"],
    "reply": lambda: ["reply", "--project", "Demo", "--to-msg", "1",
                       "--from", "Mainline - Demo"],
    "register": lambda: ["register", "--project", "Demo", "--address", "Track 2 - Demo",
                          "--kind", "track"],
    "unregister": lambda: ["unregister", "--project", "Demo", "--address", "Track 2 - Demo",
                            "--as", "Mainline - Demo"],
    "thread": lambda: ["thread", "--project", "Demo", "--id", "1"],
}


class _FreshDemoFixture(unittest.TestCase):
    """A fresh isolated store per build() call — used because send/reply/
    register/unregister MUTATE state, so the no-flag run and the
    --format human run must each get their own pristine fixture to remain
    comparable (both must independently reproduce message id #2 etc.)."""

    def build(self, verb):
        tmp = tempfile.mkdtemp(prefix="sandesh-axi-golden-fixture-")
        prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = tmp
        try:
            sdb.setup("Demo")
            con = sdb.connect()
            store = sdb.store_dir("Demo")
            sdb.register(con, "Mainline - Demo", kind="mainline", project="Demo")
            sdb.register(con, "Track 1 - Demo", kind="track", project="Demo")
            sdb.send(con, store, "Track 1 - Demo", to=["Mainline - Demo"],
                      subject="ping", project="Demo")
            if verb == "unregister":
                sdb.register(con, "Track 2 - Demo", kind="track", project="Demo")
            con.close()
        finally:
            pass
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(self._restore_xdg, prev_xdg)
        return tmp

    @staticmethod
    def _restore_xdg(prev_xdg):
        if prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = prev_xdg


def _make_golden_test(verb):
    def test(self):
        golden = _load_golden(verb)

        self.build(verb)
        rc_noflag, out_noflag, err_noflag = run_cli(ARGV_BY_VERB[verb]())
        self.assertEqual(rc_noflag, 0, f"[{verb}] no-flag must exit 0; err={err_noflag!r}")

        self.build(verb)
        rc_human, out_human, err_human = run_cli(ARGV_BY_VERB[verb]() + ["--format", "human"])
        self.assertEqual(rc_human, 0, f"[{verb}] --format human must exit 0; err={err_human!r}")

        self.assertEqual(_normalize_ts(out_noflag), golden,
                          f"[{verb}] no-flag stdout must equal the golden")
        self.assertEqual(_normalize_ts(out_human), golden,
                          f"[{verb}] --format human stdout must equal the golden")

    test.__name__ = f"test_{verb}_no_flag_equals_format_human_equals_golden"
    return test


class HumanModeGoldenTest(_FreshDemoFixture):
    pass


def _register_golden_tests():
    for verb in ARGV_BY_VERB:
        setattr(HumanModeGoldenTest, f"test_{verb}_no_flag_equals_format_human_equals_golden",
                _make_golden_test(verb))


_register_golden_tests()


# --------------------------------------------------------------------------- #
# Sanity — `--format human` behaves exactly as no flag, for a success AND a
# failure case (exit codes identical).
# --------------------------------------------------------------------------- #

class HumanIsDefaultSanityTest(_DemoFixture):

    def test_success_case_format_human_matches_no_flag_exit_code(self):
        rc_noflag, _, _ = self.run_cli(["addressbook", "--project", "Demo"])
        rc_human, _, _ = self.run_cli(["addressbook", "--project", "Demo", "--format", "human"])
        self.assertEqual(rc_noflag, 0)
        self.assertEqual(rc_human, rc_noflag)

    def test_failure_case_format_human_matches_no_flag_exit_code(self):
        bad_argv = ["send", "--project", "Demo", "--from", "Track 1 - Demo",
                    "--to", "Nobody - Demo", "--subject", "x"]
        rc_noflag, _, _ = self.run_cli(bad_argv)
        rc_human, _, _ = self.run_cli(bad_argv + ["--format", "human"])
        self.assertNotEqual(rc_noflag, 0, "sanity: the failure case must actually fail")
        self.assertEqual(rc_human, rc_noflag)


# --------------------------------------------------------------------------- #
# AC2 (CR-SAN-050 §S2) — search/projects/grant reuse the main()-closed
# connection seam: exactly one connection per run, closed once the verb is done.
# --------------------------------------------------------------------------- #

class ConnectionSeamTest(_DemoFixture):

    def _run_spied(self, argv):
        opened = []
        real_connect = sdb.connect

        def spy(*a, **kw):
            con = real_connect(*a, **kw)
            opened.append(con)
            return con

        with mock.patch.object(sdb, "connect", spy):
            rc, out, err = self.run_cli(argv)
        return rc, out, err, opened

    def test_search_projects_grant_open_one_connection_each_and_main_closes_it(self):
        sdb.assign_admin(self.con, "TheAdmin")
        cases = (
            ["--format", "toon", "search", "ping", "--to", "Mainline - Demo"],
            ["--format", "toon", "projects"],
            ["--format", "toon", "grant", "--cross-project", "--project", "Demo",
             "--by", "TheAdmin"],
        )
        for argv in cases:
            with self.subTest(verb=argv[2]):
                rc, out, err, opened = self._run_spied(argv)
                self.assertEqual(rc, 0, f"err={err!r}")
                self.assertEqual(_toon.decode(out)["axi"]["verb"], argv[2])
                self.assertEqual(len(opened), 1,
                                 f"{argv[2]} must open exactly one connection; opened {len(opened)}")
                with self.assertRaises(sqlite3.ProgrammingError,
                                       msg=f"{argv[2]}: main() must close the connection"):
                    opened[0].execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
