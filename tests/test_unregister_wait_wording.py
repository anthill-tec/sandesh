"""test_unregister_wait_wording.py — CR-SAN-055 §S1/§S4, AC7 (RED).

The bounded wait for `unregister` against a live watcher (library + CLI + MCP)
was implemented in an earlier cycle; this cycle is the WORDING: every text
that tells a caller what `tombstoned` means must describe the wait as
ALREADY DONE (a live watcher is waited for up to two poll intervals before
`tombstoned` is returned) and must stop telling the caller to re-run the
command once the watcher goes offline.

Covers:
  - the CLI human-mode `tombstoned` message (`sandesh/cli.py::_print_unregister`)
  - the MCP `sandesh_unregister` tool docstring
  - the MCP `unregister` prompt's rendered text
  - `CLAUDE.md` locked semantic #8
  - `docs/USER_GUIDE.md`
  - `sandesh/data/usage-scenarios.md`

All FAIL now: the CLI message still says "It stops within one poll; re-run
once `addressbook` shows it offline"; the MCP docstring and prompt still say
"re-run ... once it is offline"/"re-run once the watcher is offline"; CLAUDE.md
still says "the caller retries once it's offline"; usage-scenarios.md still
says "re-run once offline"; USER_GUIDE.md never mentions the wait at all.

Run:
  PYTHONPATH=. .venv/bin/python tests/test_unregister_wait_wording.py
  python3 ~/.crucible/clients/python-crucible.py test \\
      --tests tests.test_unregister_wait_wording \\
      --agent CR-SAN-055-C4-RED
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from sandesh import sandesh_db as sdb
from sandesh import cli
from sandesh import mcp_server

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLAUDE_MD = os.path.join(_REPO_ROOT, "CLAUDE.md")
_USER_GUIDE = os.path.join(_REPO_ROOT, "docs", "USER_GUIDE.md")
_USAGE_SCENARIOS = os.path.join(_REPO_ROOT, "sandesh", "data", "usage-scenarios.md")

PROJ = "Demo"
MAINLINE = "Mainline - Demo"
TRACK1 = "Track 1 - Demo"

# The forbidden retry-instruction language (none of the pinned texts may
# contain it any more — the wait already happened by the time the caller
# sees "tombstoned").
_RERUN_TOKEN = "re-run"
# The technical term the rest of the codebase already uses for this exact
# wait (sandesh_db.unregister's own docstring, the archive docstring in
# mcp_server.py, CLAUDE.md's CR-SAN-024 lineage) — the wording must describe
# the wait using it.
_WAIT_TOKEN = "poll interval"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_verbs.py's run_cli.
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
# CLI human-mode `tombstoned` message
# --------------------------------------------------------------------------- #


class CliUnregisterTombstonedWordingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-unreg-wording-cli-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        os.environ["XDG_DATA_HOME"] = self.tmp

        sdb.setup(PROJ)
        self.con = sdb.connect()
        sdb.register(self.con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(self.con, TRACK1, kind="track", project=PROJ)

    def tearDown(self):
        self.con.close()
        for var, prev in (
            ("XDG_DATA_HOME", self._prev_xdg),
            ("SANDESH_PROJECT", self._prev_project),
            ("SANDESH_ADDRESS", self._prev_address),
        ):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tombstoned_message_describes_the_wait_as_already_done_not_a_rerun_instruction(self):
        # Hold the notifier with this test's own pid so it never goes
        # offline on its own — SANDESH_POLL_SECONDS=3 bounds the new wait to
        # 2*3=6s instead of the 20s default (CR-SAN-055 §S1 behaviour, already
        # implemented; this test is about the message TEXT once that wait
        # has elapsed and the result is still 'tombstoned').
        sdb.notifier_acquire(self.con, TRACK1, os.getpid(), "tok-wording", "host")

        rc, out, err = run_cli(
            ["unregister", "--project", PROJ, "--address", TRACK1, "--as", MAINLINE],
            env={"SANDESH_POLL_SECONDS": "3"},
        )
        self.assertEqual(rc, 3, f"tombstoned exit code stays 3; out={out!r} err={err!r}")
        lowered = out.lower()
        self.assertNotIn(
            _RERUN_TOKEN,
            lowered,
            "the human-mode tombstoned message must no longer instruct the "
            f"caller to re-run the command (the wait already happened). Got: {out!r}",
        )
        self.assertNotIn(
            "within one poll",
            lowered,
            f"the old 'it stops within one poll' wording must be gone. Got: {out!r}",
        )
        self.assertIn(
            _WAIT_TOKEN,
            lowered,
            "the tombstoned message must describe the bounded wait it already "
            f"ran (the term 'poll interval(s)') before returning tombstoned. Got: {out!r}",
        )


# --------------------------------------------------------------------------- #
# MCP `sandesh_unregister` tool docstring + `unregister` prompt text
# --------------------------------------------------------------------------- #


class McpUnregisterWordingSetupMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-unreg-wording-mcp-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        for var, prev in (
            ("XDG_DATA_HOME", self._prev_xdg),
            ("SANDESH_PROJECT", self._prev_project),
        ):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)


class McpUnregisterToolDocstringWordingTest(McpUnregisterWordingSetupMixin, unittest.IsolatedAsyncioTestCase):
    async def test_tool_description_describes_the_wait_not_a_rerun_instruction(self):
        tools = await mcp_server.mcp.list_tools()
        tool = next((t for t in tools if t.name == "sandesh_unregister"), None)
        assert tool is not None, (
            f"sandesh_unregister tool not found; available: {[t.name for t in tools]}"
        )
        desc = (tool.description or "").lower()
        self.assertNotIn(
            _RERUN_TOKEN,
            desc,
            "the sandesh_unregister tool docstring must no longer instruct "
            f"the caller to re-run the tool once offline. Got: {tool.description!r}",
        )
        self.assertIn(
            _WAIT_TOKEN,
            desc,
            "the sandesh_unregister tool docstring must describe the bounded "
            f"wait (the term 'poll interval(s)'). Got: {tool.description!r}",
        )


class McpUnregisterPromptWordingTest(McpUnregisterWordingSetupMixin, unittest.IsolatedAsyncioTestCase):
    def _collect_text(self, result):
        parts = []
        for msg in result.messages:
            content = msg.content
            if hasattr(content, "text"):
                parts.append(content.text)
            elif isinstance(content, str):
                parts.append(content)
        return " ".join(parts)

    async def test_prompt_rendered_text_describes_the_wait_not_a_rerun_instruction(self):
        result = await mcp_server.mcp.get_prompt(
            "unregister", {"recipient": TRACK1, "requester": MAINLINE},
        )
        full_text = self._collect_text(result)
        lowered = full_text.lower()
        self.assertNotIn(
            _RERUN_TOKEN,
            lowered,
            "the rendered unregister prompt must no longer instruct the "
            f"caller to re-run sandesh_unregister once offline. Got: {full_text!r}",
        )
        self.assertIn(
            _WAIT_TOKEN,
            lowered,
            "the rendered unregister prompt must describe the bounded wait "
            f"(the term 'poll interval(s)'). Got: {full_text!r}",
        )


# --------------------------------------------------------------------------- #
# CLAUDE.md locked semantic #8
# --------------------------------------------------------------------------- #


def _extract_semantic_8(text):
    """Slice CLAUDE.md's numbered locked-semantics list item #8 (Cooperative
    tombstone eviction) up to the start of item #9 (Removal authorization)."""
    m = re.search(r"8\. \*\*Cooperative tombstone eviction\.\*\*", text)
    if m is None:
        raise AssertionError("CLAUDE.md is missing locked semantic #8 (Cooperative tombstone eviction)")
    rest = text[m.start():]
    next_m = re.search(r"\n9\. \*\*Removal authorization\.\*\*", rest)
    return rest if next_m is None else rest[: next_m.start()]


class ClaudeMdUnregisterWaitWordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(_CLAUDE_MD)
        cls.section = _extract_semantic_8(cls.text)

    def test_locked_semantic_8_no_longer_says_the_caller_retries_once_offline(self):
        self.assertNotIn(
            "the caller retries once it's offline",
            self.section,
            "CLAUDE.md locked semantic #8 must no longer say the caller retries "
            f"once offline. Got (section): {self.section!r}",
        )

    def test_locked_semantic_8_states_the_bounded_wait(self):
        self.assertIn(
            _WAIT_TOKEN,
            self.section.lower(),
            "CLAUDE.md locked semantic #8 must state the bounded wait "
            f"(the term 'poll interval(s)'). Got (section): {self.section!r}",
        )


# --------------------------------------------------------------------------- #
# docs/USER_GUIDE.md — mentions the bounded wait
# --------------------------------------------------------------------------- #


class UserGuideUnregisterWaitWordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(_USER_GUIDE)

    def test_mentions_the_bounded_wait_for_unregister(self):
        self.assertIn(
            _WAIT_TOKEN,
            self.text.lower(),
            "docs/USER_GUIDE.md must mention the bounded wait "
            "(the term 'poll interval(s)') unregister runs against a live "
            "watcher before returning tombstoned — currently absent entirely.",
        )


# --------------------------------------------------------------------------- #
# sandesh/data/usage-scenarios.md — sandesh_unregister row
# --------------------------------------------------------------------------- #


class UsageScenariosUnregisterWaitWordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(_USAGE_SCENARIOS)

    def test_no_longer_says_rerun_once_offline(self):
        self.assertNotIn(
            "re-run once offline",
            self.text,
            f"usage-scenarios.md must no longer say 're-run once offline'. Got: {self.text!r}"[:2000],
        )

    def test_mentions_the_bounded_wait(self):
        self.assertIn(
            _WAIT_TOKEN,
            self.text.lower(),
            "usage-scenarios.md must mention the bounded wait "
            "(the term 'poll interval(s)') the sandesh_unregister tool runs.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
