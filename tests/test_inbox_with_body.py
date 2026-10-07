"""test_inbox_with_body.py — RED tests for CR-SAN-054 cycle 4 (\u00a7S4, AC7, and the
MCP half of AC8), against:
  docs/changes/CR-SAN-054-read-mail-readable.md  \u00a7S4, AC7, AC8 (MCP half only --
  the Pi half of AC8 is cycle C6, NOT this file).

Scope:
  CLI (--format json): `inbox` gains `--with-body` (re-reads bodies for the listed
  rows into `bodies: {"<id>": text}`, without marking anything read) and `--full`
  (complete bodies; otherwise cut to BODY_LIMIT=500 chars with a size suffix, and a
  cut adds an `inbox ... --with-body --full` template to help[]). Works with or
  without `--all`.
  MCP: `sandesh_inbox` gains `with_body: bool = False`; when true, each returned row
  with a body gains `body` (full text, untruncated -- MCP has no --full/--limit
  concept). Nothing is marked read either way.

Expected RED (confirmed empirically against the current tree before writing these
tests): the CLI `inbox` subparser has no `--with-body`/`--full` flags yet, so
every CLI case below fails argparse's "unrecognized arguments" (exit 2), not the
spec's field-level outcome. The MCP `sandesh_inbox` tool has no `with_body`
parameter; FastMCP's generated input schema is `additionalProperties: false`-style
(sibling tests, e.g. test_mcp_read_tools.py, rely on unknown-kwarg rejection), so
passing `with_body=True` today raises a validation ToolError instead of returning
rows with a `body` key -- the test's positive assertion is what fails.

Fixture (both CLI and MCP suites, independently built per test via setUp): project
`Demo`, `Mainline - Demo` + `Track 1 - Demo` registered.
  #m    Track 1 -> Mainline, body contains "gateway timeout", already fetched (read).
  #u    Track 1 -> Mainline, has a body, left UNREAD.
  #s    Track 1 -> Mainline, subject-only (no body), left UNREAD.
  #long Track 1 -> Mainline, body > 500 chars, left UNREAD.

Run (from the repo root):
  python3 -m unittest -v tests.test_inbox_with_body
  or   PYTHONPATH=. .venv/bin/python tests/test_inbox_with_body.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import cli  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402
from sandesh import mcp_server  # noqa: E402

PROJ = "Demo"
MAINLINE = "Mainline - Demo"
TRACK1 = "Track 1 - Demo"
BODY_TERM = "gateway timeout"
BODY_LIMIT = 500


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_disclosure.py's run_cli.
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


def _json_axi(out):
    return json.loads(out)["axi"]


# =========================================================================== #
# CLI fixture (`--format json`).
# =========================================================================== #

class _CliInboxWithBodyFixture(unittest.TestCase):
    """Project Demo, Mainline - Demo + Track 1 - Demo. Messages #m (read),
    #u (unread, body), #s (unread, subject-only), #long (unread, >500-char body),
    sent in that order so ORDER BY created_at, id lists them m, u, s, long."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-inbox-with-body-cli-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_format = os.environ.pop("SANDESH_FORMAT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ)
        self.con = sdb.connect()
        self.store = sdb.store_dir(PROJ)
        sdb.register(self.con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(self.con, TRACK1, kind="track", project=PROJ)

        self.mid_m = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="gateway timeout error",
                              body_text=f"encountered {BODY_TERM} during load test",
                              project=PROJ)
        sdb.fetch(self.con, self.store, MAINLINE)  # mark #m read before the rest exist

        self.mid_u = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="unread with body",
                              body_text="escalation needed, please advise",
                              project=PROJ)
        self.mid_s = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="subject only ping", project=PROJ)
        self.body_long = ("lorem ipsum " * 200)[:2000]
        self.assertEqual(len(self.body_long), 2000)  # fixture sanity, not the RED assertion
        self.mid_long = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                                 subject="long body", body_text=self.body_long,
                                 project=PROJ)

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

    def _unread_ids(self):
        """The recipient's current unread ids (direct sdb check, not the CLI)."""
        rows = sdb.inbox(self.con, MAINLINE, unread_only=True)
        return [r["id"] for r in rows]


# =========================================================================== #
# Case 1 -- `--all --with-body`: bodies for m (contains gateway timeout) and u;
# no key for the subject-only #s.
# =========================================================================== #

class InboxAllWithBodyTest(_CliInboxWithBodyFixture):

    def test_all_with_body_populates_bodies_for_m_and_u_but_not_subject_only_s(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        self.assertIn("bodies", axi, f"--with-body must add a bodies key; axi={axi!r}")
        bodies = axi["bodies"]
        self.assertIn(
            BODY_TERM, bodies.get(str(self.mid_m), ""),
            f"bodies[{self.mid_m!r}] must contain {BODY_TERM!r}; got {bodies.get(str(self.mid_m))!r}")
        self.assertIn(
            str(self.mid_u), bodies,
            f"bodies must have a key for the unread #u; bodies={bodies!r}")
        self.assertEqual(
            bodies[str(self.mid_u)], "escalation needed, please advise",
            f"bodies[{self.mid_u!r}] must be #u's exact body; got {bodies[str(self.mid_u)]!r}")
        self.assertNotIn(
            str(self.mid_s), bodies,
            f"the subject-only #s must never gain a bodies entry; bodies={bodies!r}")


# =========================================================================== #
# Case 2 -- afterwards, unread-only inbox still lists #u: nothing was marked
# read by --with-body.
# =========================================================================== #

class InboxWithBodyDoesNotMarkReadTest(_CliInboxWithBodyFixture):

    def test_with_body_call_leaves_u_unread_for_a_subsequent_unread_only_inbox(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        self.assertIn(
            BODY_TERM, axi.get("bodies", {}).get(str(self.mid_m), ""),
            "control case: the --with-body call above must disclose #m's body "
            f"before the no-marking claim is checked; axi={axi!r}")

        rc2, out2, err2 = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ, "--to", MAINLINE])
        self.assertEqual(rc2, 0, f"err={err2!r}")
        axi2 = _json_axi(out2)
        listed_ids = [m["id"] for m in axi2["messages"]] if isinstance(axi2["messages"], list) else []
        self.assertIn(
            self.mid_u, listed_ids,
            f"#u must still be unread after the earlier --with-body call; "
            f"unread-only inbox listed {listed_ids!r}")


# =========================================================================== #
# Case 3 -- without --all, --with-body covers only the listed UNREAD rows.
# =========================================================================== #

class InboxWithBodyWithoutAllCoversOnlyUnreadRowsTest(_CliInboxWithBodyFixture):

    def test_without_all_with_body_covers_unread_rows_only_not_the_already_read_m(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--with-body"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        bodies = axi.get("bodies", {})
        self.assertEqual(
            bodies.get(str(self.mid_u)), "escalation needed, please advise",
            f"unread #u must be covered by --with-body; bodies={bodies!r}")
        self.assertIn(
            self.body_long[:BODY_LIMIT], bodies.get(str(self.mid_long), ""),
            f"unread #long must be covered by --with-body; bodies={bodies!r}")
        self.assertNotIn(
            str(self.mid_m), bodies,
            f"the already-read #m is not among the unread-only listed rows, so "
            f"it must not appear in bodies even though it HAS a body; bodies={bodies!r}")
        self.assertNotIn(
            str(self.mid_s), bodies,
            f"the subject-only #s has no body to disclose; bodies={bodies!r}")


# =========================================================================== #
# Case 4 -- #long is cut to BODY_LIMIT with the size suffix + help[] template;
# --full returns it whole.
# =========================================================================== #

class InboxWithBodyTruncationTest(_CliInboxWithBodyFixture):

    def test_long_body_is_cut_to_500_chars_with_suffix_and_help_names_with_body_full(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        bodies = axi.get("bodies", {})
        expected = self.body_long[:BODY_LIMIT] + f" (truncated, {len(self.body_long)} chars total)"
        self.assertEqual(
            bodies.get(str(self.mid_long)), expected,
            f"the 2000-char body must be cut to exactly the first {BODY_LIMIT} chars "
            f"plus the size suffix; got {bodies.get(str(self.mid_long))!r}")
        help_ = axi.get("help", [])
        self.assertTrue(
            any("inbox" in h and "--with-body" in h and "--full" in h for h in help_),
            f"help[] must carry an 'inbox ... --with-body --full' template when a "
            f"body was cut; got {help_!r}")

    def test_full_flag_returns_the_complete_untruncated_long_body(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body", "--full"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        bodies = axi.get("bodies", {})
        self.assertEqual(
            bodies.get(str(self.mid_long)), self.body_long,
            f"--full must return the complete, untruncated 2000-char body; "
            f"got a body of length {len(bodies.get(str(self.mid_long), ''))}")


# =========================================================================== #
# Case 5 -- `--limit 1 --all --with-body`: bodies only for the one listed row.
# =========================================================================== #

class InboxWithBodyRespectsLimitTest(_CliInboxWithBodyFixture):

    def test_limit_1_restricts_bodies_to_the_single_listed_row(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body", "--limit", "1"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        messages = axi.get("messages", [])
        self.assertIsInstance(messages, list)
        self.assertEqual(len(messages), 1, f"--limit 1 must list exactly one row; got {messages!r}")
        listed_id = messages[0]["id"]
        self.assertEqual(listed_id, self.mid_m, f"the oldest row (#m) must be the one listed; got {listed_id!r}")
        bodies = axi.get("bodies", {})
        self.assertIn(
            BODY_TERM, bodies.get(str(self.mid_m), ""),
            f"the listed row's body must be disclosed; bodies={bodies!r}")
        self.assertEqual(
            set(bodies.keys()), {str(self.mid_m)},
            f"--limit 1 must restrict bodies to ONLY the listed row, not the whole "
            f"(filtered) mailbox; bodies={bodies!r}")


# =========================================================================== #
# Case 6 -- without --with-body, the payload has no bodies key at all.
# =========================================================================== #

class InboxWithoutWithBodyHasNoBodiesKeyTest(_CliInboxWithBodyFixture):

    def test_without_with_body_flag_no_bodies_key_while_with_body_control_adds_one(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ,
             "--to", MAINLINE, "--all", "--with-body"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _json_axi(out)
        self.assertIn(
            "bodies", axi,
            f"control case: --with-body must add a bodies key; axi={axi!r}")

        rc2, out2, err2 = self.run_cli(
            ["--format", "json", "inbox", "--project", PROJ, "--to", MAINLINE, "--all"])
        self.assertEqual(rc2, 0, f"err={err2!r}")
        axi2 = _json_axi(out2)
        self.assertNotIn(
            "bodies", axi2,
            f"without --with-body the payload shape must be unchanged (no bodies "
            f"key); axi={axi2!r}")


# =========================================================================== #
# MCP fixture (in-process call_tool).
# =========================================================================== #

def _data(result):
    """Unwrap FastMCP.call_tool's converted return for list/dict-returning tools.

    Same pattern as tests/test_mcp_thread_requester.py.
    """
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        result = content
    if isinstance(result, list):
        out = []
        for item in result:
            text = getattr(item, "text", item)
            try:
                out.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                out.append(text)
        if len(out) == 1 and isinstance(out[0], list):
            return out[0]
        return out
    return result


class _McpInboxWithBodyFixture(unittest.IsolatedAsyncioTestCase):
    """Same message set as the CLI fixture, built directly via sandesh_db."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-inbox-with-body-mcp-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ)
        self.store = sdb.store_dir(PROJ)
        self.con = sdb.connect()
        sdb.register(self.con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(self.con, TRACK1, kind="track", project=PROJ)

        self.mid_m = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="gateway timeout error",
                              body_text=f"encountered {BODY_TERM} during load test",
                              project=PROJ)
        sdb.fetch(self.con, self.store, MAINLINE)  # mark #m read

        self.mid_u = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="unread with body",
                              body_text="escalation needed, please advise",
                              project=PROJ)
        self.mid_s = sdb.send(self.con, self.store, TRACK1, to=[MAINLINE],
                              subject="subject only ping", project=PROJ)

    def tearDown(self):
        import contextlib
        with contextlib.suppress(Exception):
            self.con.close()
        for k, v in (("XDG_DATA_HOME", self._prev_xdg),
                     ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row_by_id(self, rows, msg_id):
        for r in rows:
            if r["id"] == msg_id:
                return r
        self.fail(f"message #{msg_id} not found in returned rows: {rows!r}")

    def _is_u_still_unread(self):
        rows = sdb.inbox(self.con, MAINLINE, unread_only=True)
        return any(r["id"] == self.mid_u and r["read_at"] is None for r in rows)


# =========================================================================== #
# Case 7 -- MCP with_body=True: body present for #m, absent for subject-only
# #s; nothing marked read.
# =========================================================================== #

class McpInboxWithBodyTest(_McpInboxWithBodyFixture):

    async def test_with_body_true_discloses_m_body_and_omits_subject_only_s_body(self):
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"recipient": MAINLINE, "unread_only": False, "with_body": True},
        )
        rows = _data(result)
        row_m = self._row_by_id(rows, self.mid_m)
        self.assertIn(
            "body", row_m,
            f"row for #{self.mid_m} must gain a 'body' key with with_body=True: {row_m!r}")
        self.assertIn(
            BODY_TERM, row_m["body"],
            f"body for #{self.mid_m} must contain {BODY_TERM!r}; got {row_m['body']!r}")
        row_s = self._row_by_id(rows, self.mid_s)
        self.assertNotIn(
            "body", row_s,
            f"the subject-only row for #{self.mid_s} must never gain a 'body' key: {row_s!r}")

    async def test_with_body_true_does_not_mark_u_read(self):
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"recipient": MAINLINE, "unread_only": False, "with_body": True},
        )
        rows = _data(result)
        row_u = self._row_by_id(rows, self.mid_u)
        self.assertIn(
            "body", row_u,
            f"control case: with_body=True must disclose #{self.mid_u}'s body "
            f"before the no-marking claim is checked: {row_u!r}")
        self.assertTrue(
            self._is_u_still_unread(),
            f"#{self.mid_u} must still be unread after an inbox call with "
            f"with_body=True")


# =========================================================================== #
# Case 8 -- without with_body, no row has a body key (paired with a with_body
# control so this is not a vacuous pre-feature pass).
# =========================================================================== #

class McpInboxWithoutWithBodyHasNoBodyKeyTest(_McpInboxWithBodyFixture):

    async def test_without_with_body_no_row_has_body_while_with_body_control_does(self):
        with_body_result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"recipient": MAINLINE, "unread_only": False, "with_body": True},
        )
        with_body_rows = _data(with_body_result)
        row_m = self._row_by_id(with_body_rows, self.mid_m)
        self.assertIn(
            "body", row_m,
            f"control case: with_body=True must disclose a 'body' for #{self.mid_m}: {row_m!r}")

        plain_result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"recipient": MAINLINE, "unread_only": False},
        )
        plain_rows = _data(plain_result)
        with_body_keys = [r["id"] for r in plain_rows if "body" in r]
        self.assertEqual(
            with_body_keys, [],
            f"without with_body no row may carry a 'body' key; rows with body: "
            f"{with_body_keys!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
