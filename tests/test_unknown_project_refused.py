"""test_unknown_project_refused.py — RED tests for CR-SAN-054 cycle C5 (§S5, AC9).

Covers `sandesh_db.require_known_project(con, project_id)` (new, pure guard) and its
wiring into the read verbs `addressbook`, `inbox`, `fetch`, `thread` and `status`
(CLI machine mode + human mode), plus the MCP `sandesh_addressbook` tool:

  - An unknown `--project`/`project_id` is refused with
    `unknown project '<id>' — known projects: <A, B, …>` (active + archived ids,
    sorted), with `— did you mean '<Id>'?` appended when exactly one known id
    matches case-insensitively.
  - Machine mode returns `ok:false` with `help[]` naming `projects`.
  - `status` checks the project BEFORE `validate_address`, so the unknown-project
    error wins over an address/project mismatch.
  - An archived project is still "known" (reads stay allowed).
  - Human mode keeps the house `[sandesh] ERROR: …` exit (non-zero, no raw
    traceback).
  - `sandesh_db.require_known_project` is unit-tested directly: raises ValueError
    for an unenrolled id, returns (no error) for an active OR archived one.

  python-crucible.py test --tests tests.test_unknown_project_refused --agent CR-SAN-054-C5-RED

Expected RED (confirmed against the current tree before writing these tests):
`sandesh_db` has no `require_known_project` — the unit test fails with
`AttributeError`. None of `addressbook`/`inbox`/`fetch`/`thread`/`status` validate
project enrollment at all today (`sdb.addressbook`/`sdb.inbox`/`sdb.fetch`/
`sdb.thread` scope by recipient/sender address or message id, never by the
`--project` value itself, and `_status_identity` only calls `validate_address`,
which checks address FORMAT, not project enrollment) — so every CLI case below
returns `ok:true` today (an empty addressbook / today's thread chain / today's
status fields) instead of `ok:false`, and the `status` case surfaces the
address-mismatch message instead of (today never) an unknown-project one. The
MCP case similarly returns the (empty) addressbook list instead of raising
`ToolError`. Human mode today exits 0 with `addressbook (demo): empty` on
stdout, not a non-zero `[sandesh] ERROR: …` on stderr.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import cli  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402
from sandesh import mcp_server  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

PROJ_DEMO = "Demo"
PROJ_ALPHA = "Alpha"
PROJ_ARCH = "Arch"
MAINLINE_DEMO = "Mainline - Demo"
TRACK1_DEMO = "Track 1 - Demo"
MAINLINE_ARCH = "Mainline - Arch"


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_disclosure.py's run_cli — handles both a plain int
    return and SystemExit (argparse -> exit(2); the house
    `sys.exit(f"[sandesh] {msg}")` single-arg idiom -> exit 1, matching what a
    real subprocess would report for that idiom).
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


# =========================================================================== #
# Fixture: 'Demo' (Mainline + Track 1), 'Alpha' (no addresses), 'Arch'
# (Mainline, archived). One message #mid in Demo (Track 1 -> Mainline).
# =========================================================================== #

class _BaseFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-unknown-project-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_format = os.environ.pop("SANDESH_FORMAT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ_DEMO)
        sdb.setup(PROJ_ALPHA)
        sdb.setup(PROJ_ARCH)

        self.con = sdb.connect()
        self.store_demo = sdb.store_dir(PROJ_DEMO)
        sdb.register(self.con, MAINLINE_DEMO, kind="mainline", project=PROJ_DEMO)
        sdb.register(self.con, TRACK1_DEMO, kind="track", project=PROJ_DEMO)
        sdb.register(self.con, MAINLINE_ARCH, kind="mainline", project=PROJ_ARCH)

        self.mid = sdb.send(self.con, self.store_demo, TRACK1_DEMO,
                            to=[MAINLINE_DEMO], subject="ping", project=PROJ_DEMO)

        sdb.archive(self.con, PROJ_ARCH, MAINLINE_ARCH)

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
# 1-2 — addressbook: known-projects listing + did-you-mean
# =========================================================================== #

class UnknownProjectAddressbookTest(_BaseFixture):

    def test_lowercase_project_id_refused_names_known_projects_and_did_you_mean(self):
        """AC9: `addressbook --project demo --format json` -> ok:false, error names
        the known projects (sorted, active+archived) and 'did you mean 'Demo'?'
        (the unique case-insensitive match); help[] names 'projects'."""
        rc, out, err = self.run_cli(
            ["--format", "json", "addressbook", "--project", "demo"])
        env = json.loads(out)["axi"]
        self.assertFalse(
            env["ok"],
            f"an unknown (wrong-case) project id must be refused; rc={rc} "
            f"out={out!r} err={err!r}")
        self.assertNotEqual(rc, 0, f"a refused verb must exit non-zero; out={out!r}")
        error = env.get("error", "")
        self.assertIn("unknown project 'demo'", error)
        self.assertIn(
            "known projects: Alpha, Arch, Demo", error,
            f"error must list the known ids sorted, active+archived; got {error!r}")
        self.assertIn(
            "did you mean 'Demo'?", error,
            f"exactly one known id ('Demo') matches 'demo' case-insensitively; "
            f"got {error!r}")
        help_ = env.get("help", [])
        self.assertTrue(
            any("projects" in h for h in help_),
            f"help[] must name the 'projects' verb; got {help_!r}")

    def test_project_id_with_no_case_insensitive_match_has_no_did_you_mean(self):
        """AC9: `--project Nope` -> unknown-project error + known projects, but NO
        'did you mean' (neither 'Alpha', 'Arch' nor 'Demo' matches 'Nope')."""
        rc, out, err = self.run_cli(
            ["--format", "json", "addressbook", "--project", "Nope"])
        env = json.loads(out)["axi"]
        self.assertFalse(env["ok"], f"out={out!r} err={err!r}")
        error = env.get("error", "")
        self.assertIn("unknown project 'Nope'", error)
        self.assertIn("known projects:", error)
        self.assertNotIn(
            "did you mean", error,
            f"no known id resembles 'Nope'; must not suggest one: {error!r}")


# =========================================================================== #
# 3 — inbox / fetch / thread refused for an unknown project
# =========================================================================== #

class UnknownProjectReadVerbsRefusedTest(_BaseFixture):

    def test_inbox_with_unknown_project_refused(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "inbox", "--project", "demo", "--to", MAINLINE_DEMO])
        env = json.loads(out)["axi"]
        self.assertFalse(
            env["ok"],
            f"inbox must refuse an unknown project before reading the recipient's "
            f"mail; out={out!r} err={err!r}")
        self.assertIn("unknown project 'demo'", env.get("error", ""))

    def test_fetch_with_unknown_project_refused(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "fetch", "--project", "demo", "--to", MAINLINE_DEMO])
        env = json.loads(out)["axi"]
        self.assertFalse(
            env["ok"],
            f"fetch must refuse an unknown project; out={out!r} err={err!r}")
        self.assertIn("unknown project 'demo'", env.get("error", ""))

    def test_thread_with_unknown_project_refused(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "thread", "--project", "demo", "--id", str(self.mid)])
        env = json.loads(out)["axi"]
        self.assertFalse(
            env["ok"],
            f"thread must refuse an unknown project even for an existing message "
            f"id; out={out!r} err={err!r}")
        self.assertIn("unknown project 'demo'", env.get("error", ""))


# =========================================================================== #
# 4 — status: unknown-project error wins over the address-mismatch error
# =========================================================================== #

class UnknownProjectStatusTest(_BaseFixture):

    def test_status_with_unknown_project_wins_over_address_mismatch(self):
        """AC9: `status --project demo` with $SANDESH_ADDRESS='Mainline - Demo' ->
        ok:false with the unknown-project error, NOT the address-project-mismatch
        error ('Mainline - Demo''s project 'Demo' != the passed 'demo' would
        otherwise also be a mismatch — the unknown-project check must run first)."""
        rc, out, err = self.run_cli(
            ["--format", "json", "status", "--project", "demo"],
            env={"SANDESH_ADDRESS": MAINLINE_DEMO})
        env = json.loads(out)["axi"]
        self.assertFalse(
            env["ok"],
            f"status must refuse an unknown project; out={out!r} err={err!r}")
        error = env.get("error", "")
        self.assertIn("unknown project 'demo'", error)
        self.assertNotIn(
            "!= project_id", error,
            f"the unknown-project error must win over validate_address's "
            f"project-mismatch wording: {error!r}")


# =========================================================================== #
# 5 — an archived project is still known (paired with the demo-refusal case so
# this is not a vacuous pre-feature pass: the 'demo' half fails today).
# =========================================================================== #

class ArchivedProjectStillKnownTest(_BaseFixture):

    def test_archived_project_still_answers_while_unknown_one_is_refused(self):
        """AC9: `addressbook --project Arch` (archived) -> ok:true, in the SAME
        test as the 'demo' (unenrolled-case) refusal, so the test is not vacuous:
        the 'demo' assertion fails today (nothing refuses it yet), proving the
        enforcement itself is the missing behaviour, while the archived control
        case pins that the fix must not also block archived reads."""
        rc_demo, out_demo, _ = self.run_cli(
            ["--format", "json", "addressbook", "--project", "demo"])
        env_demo = json.loads(out_demo)["axi"]
        self.assertFalse(
            env_demo["ok"],
            f"an unenrolled project must be refused; out={out_demo!r}")

        rc_arch, out_arch, err_arch = self.run_cli(
            ["--format", "json", "addressbook", "--project", PROJ_ARCH])
        env_arch = json.loads(out_arch)["axi"]
        self.assertEqual(rc_arch, 0, f"out={out_arch!r} err={err_arch!r}")
        self.assertTrue(
            env_arch["ok"],
            f"an archived project must still answer addressbook reads; "
            f"out={out_arch!r} err={err_arch!r}")
        self.assertEqual(env_arch["participants"], f"0 registered in {PROJ_ARCH}")


# =========================================================================== #
# 6 — MCP sandesh_addressbook raises ToolError with the same message
# =========================================================================== #

class McpAddressbookUnknownProjectTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-unknown-project-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ_DEMO)
        sdb.setup(PROJ_ALPHA)
        self.con = sdb.connect()
        sdb.register(self.con, MAINLINE_DEMO, kind="mainline", project=PROJ_DEMO)

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

    async def test_sandesh_addressbook_unknown_project_raises_toolerror(self):
        """AC9: MCP `sandesh_addressbook(project_id='demo')` -> ToolError with
        'unknown project 'demo'' and 'did you mean 'Demo'?' (same wording as the
        CLI, 'Alpha'/'Demo' being the only two enrolled projects).

        RED: today `sandesh_addressbook` never checks enrollment — the call
        returns the (empty) addressbook list for 'demo' instead of raising.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_addressbook", {"project_id": "demo"})
        err_msg = str(ctx.exception)
        self.assertIn(
            "unknown project 'demo'", err_msg,
            f"ToolError must carry the unknown-project wording; got {err_msg!r}")
        self.assertIn(
            "did you mean 'Demo'?", err_msg,
            f"ToolError must suggest the unique case-insensitive match; "
            f"got {err_msg!r}")


# =========================================================================== #
# 7 — unit: sandesh_db.require_known_project
# =========================================================================== #

class RequireKnownProjectUnitTest(_BaseFixture):

    def test_raises_valueerror_for_an_unenrolled_project_id(self):
        with self.assertRaises(ValueError) as ctx:
            sdb.require_known_project(self.con, "demo")
        self.assertIn(
            "unknown project 'demo'", str(ctx.exception),
            f"must reuse the existing 'unknown project' wording; got "
            f"{ctx.exception!r}")

    def test_returns_without_error_for_an_active_project(self):
        self.assertIsNone(sdb.require_known_project(self.con, PROJ_DEMO))

    def test_returns_without_error_for_an_archived_project(self):
        """Unlike `_require_active_project`, an archived project is accepted."""
        self.assertIsNone(sdb.require_known_project(self.con, PROJ_ARCH))


# =========================================================================== #
# 8 — human mode: the house '[sandesh] ERROR: …' exit, no raw traceback
# =========================================================================== #

class HumanModeUnknownProjectTest(_BaseFixture):

    def test_human_mode_addressbook_unknown_project_exits_nonzero_names_it(self):
        """AC9: human mode (no --format) keeps the house `[sandesh] ERROR: …`
        exit — non-zero, the message on stderr naming the unknown project — not
        a raw Python traceback and not the today's-exit-0 empty addressbook.

        Run via a real subprocess (not in-process `cli.main`) so an unhandled
        exception doesn't crash the test runner itself; it must show up as a
        clean non-zero exit with a clean stderr message instead.
        """
        env = {**os.environ, "PYTHONPATH": _REPO_ROOT, "XDG_DATA_HOME": self.tmp}
        for k in ("SANDESH_FORMAT", "SANDESH_ADDRESS", "SANDESH_PROJECT"):
            env.pop(k, None)
        result = subprocess.run(
            [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "addressbook", "--project", "demo"],
            capture_output=True, text=True, env=env, cwd=_REPO_ROOT, timeout=30)
        self.assertNotEqual(
            result.returncode, 0,
            f"human mode must exit non-zero for an unknown project; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertIn(
            "[sandesh] ERROR:", result.stderr,
            f"must use the house error prefix, not a raw traceback; "
            f"stderr={result.stderr!r}")
        self.assertIn(
            "unknown project 'demo'", result.stderr,
            f"stderr must name the unknown project; stderr={result.stderr!r}")
        self.assertNotIn(
            "Traceback (most recent call last)", result.stderr,
            f"must not leak a raw Python traceback; stderr={result.stderr!r}")


# =========================================================================== #
# 9 — regression guard: the correct-case project id is unaffected
# =========================================================================== #

class CorrectCaseProjectStillWorksTest(_BaseFixture):

    def test_addressbook_with_correct_case_project_id_still_ok_true(self):
        """Regression guard: `addressbook --project Demo` (exact enrolled case)
        must remain ok:true — a buggy enforcement (e.g. a case-sensitivity slip,
        or rejecting every project id outright) would make this fail alongside
        the unknown-case tests above; a correct one leaves it green."""
        rc, out, err = self.run_cli(
            ["--format", "json", "addressbook", "--project", PROJ_DEMO])
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        env = json.loads(out)["axi"]
        self.assertTrue(env["ok"], f"out={out!r} err={err!r}")
        participants = env["participants"]
        addresses = [p["address"] for p in participants]
        self.assertEqual(
            sorted(addresses), sorted([MAINLINE_DEMO, TRACK1_DEMO]),
            f"must list the two registered Demo addresses; got {participants!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
