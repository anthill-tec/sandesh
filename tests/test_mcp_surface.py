"""test_mcp_surface.py — MCP per-tool surface enrichment tests (CR-SAN-006 C0).

Tests for:
  AC1 — tool count + sandesh_reply signature contract preserved (no resolves/reply_all)
  AC2 — per-param descriptions present and non-empty
  AC3 — docstring/description semantics for sandesh_send and sandesh_reply
  AC4 — tool annotations: readOnlyHint, destructiveHint, and negative fetch check

All tests FAIL now (no descriptions/annotations on any tool) and will pass once
GREEN enriches app/mcp_server.py with Annotated[..., Field(description=...)], docstrings,
and @mcp.tool(annotations=...) arguments.

  python-crucible.py test --tests tests.test_mcp_surface --agent CR-SAN-006-C0-RED
"""

import inspect
import os
import shutil
import sys
import tempfile
import unittest

# Mirror the exact bootstrap from test_mcp_mutating_tools.py.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import sandesh_db as sdb
import mcp_server

PROJ = "TestSurface"
MAINLINE = "Mainline - TestSurface"
TRACK1 = "Track 1 - TestSurface"


class McpSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """Per-tool MCP surface enrichment tests: descriptions, param descriptions,
    and tool annotations (CR-SAN-006 §AC1–AC4)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-surface-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

    def tearDown(self):
        for k, v in (("XDG_DATA_HOME", self._prev_xdg), ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -------------------------------------------------------------------------
    # AC1 — contract preserved: exactly 9 tools; sandesh_reply signature locked
    # -------------------------------------------------------------------------

    async def test_ac1_list_tools_returns_exactly_nine_tools(self):
        """AC1: list_tools() returns exactly 9 tools; sandesh_actioned must NOT be present."""
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        expected_names = {
            "sandesh_setup",
            "sandesh_addressbook",
            "sandesh_inbox",
            "sandesh_fetch",
            "sandesh_thread",
            "sandesh_register",
            "sandesh_unregister",
            "sandesh_send",
            "sandesh_reply",
        }
        self.assertEqual(
            len(tools), 9,
            f"Expected exactly 9 tools, got {len(tools)}: {sorted(names)}",
        )
        self.assertEqual(
            names, expected_names,
            f"Tool name mismatch. got={sorted(names)}, expected={sorted(expected_names)}",
        )
        self.assertNotIn(
            "sandesh_actioned", names,
            "sandesh_actioned must not be present (removed in CR-SAN-005)",
        )

    async def test_ac1_sandesh_reply_signature_has_exact_five_params(self):
        """AC1: sandesh_reply signature is exactly {parent_id, from_addr, project_id,
        subject, body_text} — no resolves, no reply_all (CR-SAN-005 lock invariant)."""
        sig = inspect.signature(mcp_server.sandesh_reply)
        actual_params = set(sig.parameters.keys())
        expected_params = {"parent_id", "from_addr", "project_id", "subject", "body_text"}
        self.assertEqual(
            actual_params, expected_params,
            f"sandesh_reply params mismatch. got={sorted(actual_params)}, "
            f"expected={sorted(expected_params)}",
        )
        self.assertNotIn(
            "resolves", actual_params,
            "sandesh_reply must not have a 'resolves' parameter",
        )
        self.assertNotIn(
            "reply_all", actual_params,
            "sandesh_reply must not have a 'reply_all' parameter",
        )

    # -------------------------------------------------------------------------
    # AC2 — per-param descriptions present and non-empty
    # -------------------------------------------------------------------------

    def _param_desc(self, tool, param_name):
        """Return the inputSchema description string for a named param, or None."""
        schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
        props = schema.get("properties", {})
        param = props.get(param_name, {})
        return param.get("description", None)

    def _tool_by_name(self, tools, name):
        """Return the Tool with the given name from the list, or fail."""
        for t in tools:
            if t.name == name:
                return t
        self.fail(f"Tool '{name}' not found in list_tools()")

    async def test_ac2_sandesh_send_param_to_has_description(self):
        """AC2: sandesh_send's 'to' parameter has a non-empty description."""
        tools = await mcp_server.mcp.list_tools()
        send_tool = self._tool_by_name(tools, "sandesh_send")
        desc = self._param_desc(send_tool, "to")
        self.assertIsNotNone(
            desc,
            "sandesh_send.inputSchema['properties']['to']['description'] is missing",
        )
        self.assertIsInstance(desc, str, "'to' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_send 'to' param description must not be empty",
        )

    async def test_ac2_sandesh_send_param_cc_has_description(self):
        """AC2: sandesh_send's 'cc' parameter has a non-empty description."""
        tools = await mcp_server.mcp.list_tools()
        send_tool = self._tool_by_name(tools, "sandesh_send")
        desc = self._param_desc(send_tool, "cc")
        self.assertIsNotNone(
            desc,
            "sandesh_send.inputSchema['properties']['cc']['description'] is missing",
        )
        self.assertIsInstance(desc, str, "'cc' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_send 'cc' param description must not be empty",
        )

    async def test_ac2_sandesh_reply_param_parent_id_has_description(self):
        """AC2: sandesh_reply's 'parent_id' parameter has a non-empty description."""
        tools = await mcp_server.mcp.list_tools()
        reply_tool = self._tool_by_name(tools, "sandesh_reply")
        desc = self._param_desc(reply_tool, "parent_id")
        self.assertIsNotNone(
            desc,
            "sandesh_reply.inputSchema['properties']['parent_id']['description'] is missing",
        )
        self.assertIsInstance(desc, str, "'parent_id' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_reply 'parent_id' param description must not be empty",
        )

    async def test_ac2_sandesh_register_param_addr_has_description(self):
        """AC2: sandesh_register's 'addr' parameter has a non-empty description."""
        tools = await mcp_server.mcp.list_tools()
        reg_tool = self._tool_by_name(tools, "sandesh_register")
        desc = self._param_desc(reg_tool, "addr")
        self.assertIsNotNone(
            desc,
            "sandesh_register.inputSchema['properties']['addr']['description'] is missing",
        )
        self.assertIsInstance(desc, str, "'addr' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_register 'addr' param description must not be empty",
        )

    async def test_ac2_project_id_param_has_description_on_sandesh_send(self):
        """AC2: sandesh_send's 'project_id' parameter has a non-empty description
        (representative check for the shared project_id param across tools)."""
        tools = await mcp_server.mcp.list_tools()
        send_tool = self._tool_by_name(tools, "sandesh_send")
        desc = self._param_desc(send_tool, "project_id")
        self.assertIsNotNone(
            desc,
            "sandesh_send.inputSchema['properties']['project_id']['description'] is missing",
        )
        self.assertIsInstance(desc, str, "'project_id' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_send 'project_id' param description must not be empty",
        )

    # -------------------------------------------------------------------------
    # AC3 — docstring / description semantics
    # -------------------------------------------------------------------------

    async def test_ac3_sandesh_send_description_mentions_to_wake_semantic(self):
        """AC3: sandesh_send.description mentions the To-wake semantic.
        At least one of ('to wakes', 'wakes', 'wake') must appear in the description
        (case-insensitive)."""
        tools = await mcp_server.mcp.list_tools()
        send_tool = self._tool_by_name(tools, "sandesh_send")
        desc = (send_tool.description or "").lower()
        wake_terms = ("to wakes", "wakes", "wake")
        self.assertTrue(
            any(term in desc for term in wake_terms),
            f"sandesh_send.description must mention the To-wake semantic "
            f"(one of {wake_terms}). Got: {send_tool.description!r}",
        )

    async def test_ac3_sandesh_send_description_mentions_cc_silent_semantic(self):
        """AC3: sandesh_send.description mentions 'cc' AND a cc-silent concept.
        'cc' must appear AND one of ('silent', 'does not wake', 'not wake', 'awareness')."""
        tools = await mcp_server.mcp.list_tools()
        send_tool = self._tool_by_name(tools, "sandesh_send")
        desc = (send_tool.description or "").lower()
        self.assertIn(
            "cc", desc,
            f"sandesh_send.description must mention 'cc'. Got: {send_tool.description!r}",
        )
        silent_terms = ("silent", "does not wake", "not wake", "awareness")
        self.assertTrue(
            any(term in desc for term in silent_terms),
            f"sandesh_send.description must mention cc-silent semantic "
            f"(one of {silent_terms}). Got: {send_tool.description!r}",
        )

    async def test_ac3_sandesh_reply_parent_id_conveys_original_message(self):
        """AC3: combined text of sandesh_reply.description and its 'parent_id' param
        description must convey that parent_id is the ORIGINAL message's id.
        Combined text (lowercased) must contain both 'original' and 'message'."""
        tools = await mcp_server.mcp.list_tools()
        reply_tool = self._tool_by_name(tools, "sandesh_reply")
        tool_desc = (reply_tool.description or "").lower()
        param_desc = (self._param_desc(reply_tool, "parent_id") or "").lower()
        combined = tool_desc + " " + param_desc
        self.assertIn(
            "original", combined,
            f"sandesh_reply combined description must mention 'original'. "
            f"tool.description={reply_tool.description!r}, "
            f"parent_id.description={self._param_desc(reply_tool, 'parent_id')!r}",
        )
        self.assertIn(
            "message", combined,
            f"sandesh_reply combined description must mention 'message'. "
            f"tool.description={reply_tool.description!r}, "
            f"parent_id.description={self._param_desc(reply_tool, 'parent_id')!r}",
        )

    # -------------------------------------------------------------------------
    # AC4 — tool annotations
    # -------------------------------------------------------------------------

    async def test_ac4_sandesh_addressbook_is_read_only(self):
        """AC4: sandesh_addressbook.annotations.readOnlyHint is True."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_addressbook")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_addressbook must have annotations (not None)",
        )
        self.assertTrue(
            getattr(tool.annotations, "readOnlyHint", None) is True,
            f"sandesh_addressbook.annotations.readOnlyHint must be True, "
            f"got: {getattr(tool.annotations, 'readOnlyHint', None)!r}",
        )

    async def test_ac4_sandesh_inbox_is_read_only(self):
        """AC4: sandesh_inbox.annotations.readOnlyHint is True."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_inbox")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_inbox must have annotations (not None)",
        )
        self.assertTrue(
            getattr(tool.annotations, "readOnlyHint", None) is True,
            f"sandesh_inbox.annotations.readOnlyHint must be True, "
            f"got: {getattr(tool.annotations, 'readOnlyHint', None)!r}",
        )

    async def test_ac4_sandesh_thread_is_read_only(self):
        """AC4: sandesh_thread.annotations.readOnlyHint is True."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_thread")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_thread must have annotations (not None)",
        )
        self.assertTrue(
            getattr(tool.annotations, "readOnlyHint", None) is True,
            f"sandesh_thread.annotations.readOnlyHint must be True, "
            f"got: {getattr(tool.annotations, 'readOnlyHint', None)!r}",
        )

    async def test_ac4_sandesh_unregister_is_destructive(self):
        """AC4: sandesh_unregister.annotations.destructiveHint is True."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_unregister")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_unregister must have annotations (not None)",
        )
        self.assertTrue(
            getattr(tool.annotations, "destructiveHint", None) is True,
            f"sandesh_unregister.annotations.destructiveHint must be True, "
            f"got: {getattr(tool.annotations, 'destructiveHint', None)!r}",
        )

    async def test_ac4_sandesh_fetch_is_not_read_only(self):
        """AC4: sandesh_fetch must NOT have readOnlyHint=True (it mutates read-state
        via mark=True). Either annotations is None, or readOnlyHint is not True."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_fetch")
        read_only_hint = getattr(tool.annotations, "readOnlyHint", None) if tool.annotations else None
        self.assertIsNot(
            read_only_hint, True,
            "sandesh_fetch.annotations.readOnlyHint must NOT be True "
            "(fetch mutates read state via mark=True)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
