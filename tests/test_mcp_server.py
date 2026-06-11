"""test_mcp_server.py — MCP server foundation + sandesh_setup.

Tests are guided by the CR acceptance criteria (AC4–AC11). Async tools are driven
in-process via FastMCP.call_tool / list_tools (unittest.IsolatedAsyncioTestCase).

  python-crucible.py test --tests tests.test_mcp_server --agent CR-SAN-001-FIX
"""

import os
import tempfile
import shutil
import unittest
from unittest import mock

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

PROJ = "Demo"


def _text(result):
    """Unwrap FastMCP.call_tool's converted return → the scalar text (CR-SAN-001 §S4).

    A str-returning tool comes back as content blocks (TextContent with .text); some SDK
    versions return a (content, structured) tuple. Handle both."""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, (list, tuple)):
        first = result[0]
        return getattr(first, "text", first)
    return getattr(result, "text", result)


class McpServerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)  # clean baseline; tests opt in

    def tearDown(self):
        for k, v in (("XDG_DATA_HOME", self._prev_xdg), ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # AC4 — FastMCP("sandesh")
    def test_server_is_fastmcp_named_sandesh(self):
        self.assertIsInstance(mcp_server.mcp, FastMCP)
        self.assertEqual(mcp_server.mcp.name, "sandesh")

    # AC5 — _ctx resolves store via store_dir + connects via connect (project or $SANDESH_PROJECT)
    def test_ctx_resolves_store_and_connection(self):
        project, store, con = mcp_server._ctx(PROJ)
        self.assertEqual(project, PROJ)
        self.assertEqual(store, sdb.store_dir(PROJ))
        self.assertEqual(con.execute("SELECT 1").fetchone()[0], 1)
        con.close()

    # AC5 — error raised when neither project_id nor $SANDESH_PROJECT is set
    def test_ctx_errors_when_no_project(self):
        with self.assertRaises(ValueError):
            mcp_server._ctx(None)

    # AC5 — _ctx falls back to $SANDESH_PROJECT
    def test_ctx_falls_back_to_env(self):
        os.environ["SANDESH_PROJECT"] = PROJ
        project, store, _con = mcp_server._ctx(None)
        self.assertEqual(project, PROJ)
        self.assertEqual(store, sdb.store_dir(PROJ))

    # AC6 — a library ValueError surfaces to the client as ToolError (not unhandled)
    async def test_setup_missing_project_raises_toolerror(self):
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool("sandesh_setup", {})

    # AC7 — main() runs the stdio transport
    def test_main_runs_stdio(self):
        with mock.patch.object(mcp_server.mcp, "run") as run:
            mcp_server.main()
        run.assert_called_once_with(transport="stdio")

    # AC8 — list_tools() includes sandesh_setup
    async def test_list_tools_includes_setup(self):
        names = [t.name for t in await mcp_server.mcp.list_tools()]
        self.assertIn("sandesh_setup", names)

    # AC9 + AC11 — sandesh_setup(project_id) provisions the store and returns its path
    async def test_setup_provisions_and_returns_path(self):
        result = await mcp_server.mcp.call_tool("sandesh_setup", {"project_id": PROJ})
        self.assertEqual(_text(result), sdb.store_dir(PROJ))
        self.assertTrue(os.path.isdir(sdb.store_dir(PROJ)))

    # AC10 — sandesh_setup with no project_id but $SANDESH_PROJECT set provisions that store
    async def test_setup_uses_env_fallback(self):
        os.environ["SANDESH_PROJECT"] = PROJ
        await mcp_server.mcp.call_tool("sandesh_setup", {})
        self.assertTrue(os.path.isdir(sdb.store_dir(PROJ)))


if __name__ == "__main__":
    unittest.main()
