"""test_project_id_validation.py — RED tests for CR-SAN-045 Cycle 1 (§S1).

setup() must validate project_id against the address <Project> grammar
[A-Za-z][A-Za-z0-9_]* — the SAME grammar ADDRESS_RE uses for its <proj> group.
An id the grammar cannot express (e.g. "Model B") must be rejected at CREATION,
with no store dir and no tracker row created — preventing un-addressable
'zombie' projects that can never register a valid Mainline (and so can never be
archived → never tombstoned).

Covers AC1–AC5:
  AC1 setup("Model B") -> ValueError (names id + grammar); no row, no store dir
  AC2 setup("ModelB")  -> succeeds unchanged ('active' row + store dir)
  AC3 reject/accept tables
  AC4 single-grammar-source pin: validate_project_id(pid) ok IFF
      ADDRESS_RE.match(f"Mainline - {pid}") is not None
  AC5 MCP sandesh_setup("Model B") -> ToolError; ("ModelB") -> store path

Expected RED:
  - validate_project_id not yet implemented (pin test callable pre-check)
  - setup() does NOT yet reject invalid ids -> assertRaises(ValueError) fails
    because setup currently SUCCEEDS for "Model B" (creates row + store dir)
  - MCP sandesh_setup("Model B") currently succeeds -> no ToolError

  ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_project_id_validation --agent CR-SAN-045-C1-RED
"""
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh.sandesh_db import ADDRESS_RE

REJECTED = ["Model B", "model-b", "2fast", "a.b", "Crucible v2", ""]
ACCEPTED = ["ModelB", "Nai", "P2", "a_b", "x1"]


class _TempHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-projid-test-")
        self._prev = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# AC1 / AC3 — setup rejects an un-addressable id, writing nothing

class SetupRejectsInvalidTest(_TempHome):
    def test_space_named_raises_valueerror(self):
        with self.assertRaises(ValueError):
            s.setup("Model B")

    def test_reject_message_names_id_and_grammar(self):
        with self.assertRaises(ValueError) as ctx:
            s.setup("Model B")
        msg = str(ctx.exception)
        self.assertIn("Model B", msg, f"error must name the id; got {msg!r}")
        self.assertIn("[A-Za-z][A-Za-z0-9_]*", msg,
                      f"error must cite the grammar; got {msg!r}")

    def test_no_tracker_row_on_reject(self):
        try:
            s.setup("Model B")
        except ValueError:
            pass
        con = s.connect()
        try:
            self.assertIsNone(
                s.project_state(con, "Model B"),
                "no tracker row may be created for a rejected id")
        finally:
            con.close()

    def test_no_store_dir_on_reject(self):
        try:
            s.setup("Model B")
        except ValueError:
            pass
        self.assertFalse(
            os.path.isdir(s.store_dir("Model B")),
            "no store dir may be created for a rejected id")

    def test_all_rejected_ids_raise(self):
        for pid in REJECTED:
            with self.assertRaises(ValueError, msg=f"{pid!r} must be rejected"):
                s.setup(pid)


# --------------------------------------------------------------------------- #
# AC2 / AC3 — setup still accepts a grammar-valid id (regression)

class SetupAcceptsValidTest(_TempHome):
    def test_modelb_succeeds(self):
        store = s.setup("ModelB")
        self.assertTrue(os.path.isdir(store))
        con = s.connect()
        try:
            self.assertEqual(s.project_state(con, "ModelB"), "active")
        finally:
            con.close()

    def test_all_accepted_ids_succeed(self):
        for pid in ACCEPTED:
            store = s.setup(pid)
            self.assertTrue(os.path.isdir(store), f"{pid!r} must succeed")


# --------------------------------------------------------------------------- #
# AC4 — validate_project_id uses EXACTLY the address <Project> grammar

class ValidateProjectIdSingleSourceTest(_TempHome):
    def test_validate_project_id_matches_address_grammar(self):
        self.assertTrue(
            callable(getattr(s, "validate_project_id", None)),
            "sandesh_db.validate_project_id must exist (GREEN)")
        for pid in ACCEPTED + REJECTED:
            addr_ok = ADDRESS_RE.match(f"Mainline - {pid}") is not None
            try:
                s.validate_project_id(pid)
                vpid_ok = True
            except ValueError:
                vpid_ok = False
            self.assertEqual(
                vpid_ok, addr_ok,
                f"validate_project_id({pid!r})={vpid_ok} but "
                f"address-grammar accepts={addr_ok} — must be identical")


# --------------------------------------------------------------------------- #
# AC5 — the MCP creation surface rejects too (ValueError -> ToolError)

class McpSetupValidationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-projid-mcp-test-")
        self._prev = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

    def tearDown(self):
        for k, v in (("XDG_DATA_HOME", self._prev),
                     ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_mcp_setup_rejects_space_named(self):
        from sandesh import mcp_server
        from mcp.server.fastmcp.exceptions import ToolError
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool(
                "sandesh_setup", {"project_id": "Model B"})

    async def test_mcp_setup_accepts_valid(self):
        from sandesh import mcp_server
        # Must not raise (returns the store path).
        await mcp_server.mcp.call_tool(
            "sandesh_setup", {"project_id": "ModelB"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
