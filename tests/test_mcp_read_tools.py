"""test_mcp_read_tools.py — MCP read-tool parity tests (CR-SAN-002 §S1, AC1–AC6).

Parity tests: each tool's unwrapped result must equal the dict-normalised result
of the corresponding sandesh_db.* function.

  python-crucible.py test --tests tests.test_mcp_read_tools --agent CR-SAN-002-C0-RED

Return-shape reference (mcp 1.27.2, confirmed via /tmp probe):
  - list[dict] tools → (list[TextContent], {"result": [...]})
  - empty list       → ([], {"result": []})
  _data() extracts structured["result"] when the tuple pattern is present.
"""

import json
import os
import shutil
import tempfile
import unittest

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp.exceptions import ToolError

PROJ = "TestRead"
MAINLINE = "Mainline - TestRead"
TRACK1 = "Track 1 - TestRead"
TRACK2 = "Track 2 - TestRead"  # third address used as sender to avoid sender-drop on cc


def _data(result):
    """Unwrap FastMCP.call_tool's converted return for list/dict-returning tools.

    Observed shapes (mcp 1.27.2, confirmed via /tmp/probe_mcp_return.py):
      - list[dict] → (list[TextContent], {"result": [...]})
      - empty list → ([], {"result": []})

    Strategy: if it's a (content, structured) tuple where structured has "result",
    return that directly.  Fall back to JSON-parsing each TextContent.text.
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
        # a single JSON-encoded list would be unwrapped here; not expected for our tools
        if len(out) == 1 and isinstance(out[0], list):
            return out[0]
        return out
    return result


class McpReadToolsTest(unittest.IsolatedAsyncioTestCase):
    """Parity tests for the four read tools: addressbook, inbox, fetch, thread."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-readtools-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)  # clean baseline; tests opt-in explicitly

        # Provision the project store and seed data via the library directly.
        sdb.setup(PROJ)
        self.store = sdb.store_dir(PROJ)
        self.con = sdb.connect()

        # Register three addresses.
        sdb.register(self.con, MAINLINE, kind="mainline")
        sdb.register(self.con, TRACK1, kind="track")
        sdb.register(self.con, TRACK2, kind="track")

        # Send a subject-only message and a message with a body.
        self.mid_subj = sdb.send(
            self.con, self.store, TRACK1,
            to=[MAINLINE], subject="ping", project=PROJ,
        )
        # mid_body is sent FROM TRACK2 so that TRACK1 can appear as cc without being
        # dropped by _expand_recipients (which removes the sender from all recipient lists).
        # This preserves AC3 / locked semantic: TRACK1's cc row is independent of MAINLINE's
        # to row, so TRACK1 stays unread after MAINLINE fetches.
        self.mid_body = sdb.send(
            self.con, self.store, TRACK2,
            to=[MAINLINE], cc=[TRACK1], subject="detailed report",
            body_text="This is the message body.\n", project=PROJ,
        )

        # Thread: reply to mid_body (for thread tests).
        self.mid_reply = sdb.reply(
            self.con, self.store, self.mid_body,
            MAINLINE, subject="Re: detailed report", project=PROJ,
        )

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass
        for k, v in (("XDG_DATA_HOME", self._prev_xdg), ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # AC1 — list_tools includes all four read tools

    async def test_list_tools_includes_all_four_read_tools(self):
        """AC1: list_tools() must include sandesh_addressbook, sandesh_inbox,
        sandesh_fetch, sandesh_thread."""
        names = [t.name for t in await mcp_server.mcp.list_tools()]
        self.assertIn("sandesh_addressbook", names)
        self.assertIn("sandesh_inbox", names)
        self.assertIn("sandesh_fetch", names)
        self.assertIn("sandesh_thread", names)

    # ------------------------------------------------------------------
    # AC2 — sandesh_addressbook parity

    async def test_addressbook_returns_same_as_direct_call(self):
        """AC2: sandesh_addressbook(project_id) == addressbook(con)."""
        expected = sdb.addressbook(self.con, PROJ)
        result = await mcp_server.mcp.call_tool("sandesh_addressbook", {"project_id": PROJ})
        actual = _data(result)
        self.assertIsInstance(actual, list)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(actual, expected)

    async def test_addressbook_returns_all_registered_addresses(self):
        """AC2 behavioural: all three registered addresses appear in the result."""
        result = await mcp_server.mcp.call_tool("sandesh_addressbook", {"project_id": PROJ})
        actual = _data(result)
        addresses = [row["address"] for row in actual]
        self.assertIn(MAINLINE, addresses)
        self.assertIn(TRACK1, addresses)
        self.assertIn(TRACK2, addresses)
        # Exactly 3 addresses (MAINLINE + TRACK1 + TRACK2)
        self.assertEqual(len(addresses), 3)

    async def test_addressbook_shows_active_flag(self):
        """AC2 behavioural: active addresses have active=True."""
        result = await mcp_server.mcp.call_tool("sandesh_addressbook", {"project_id": PROJ})
        actual = _data(result)
        for row in actual:
            self.assertTrue(row["active"])

    async def test_addressbook_no_project_raises_toolerror(self):
        """AC2 error path: calling without project_id and no $SANDESH_PROJECT raises ToolError."""
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool("sandesh_addressbook", {})

    # ------------------------------------------------------------------
    # AC3 — sandesh_inbox parity (list[sqlite3.Row] → list[dict])

    async def test_inbox_returns_same_as_direct_call_normalized(self):
        """AC3: sandesh_inbox(project_id, recipient, unread_only) == [dict(r) for r in inbox(con, ...)]."""
        expected = [dict(r) for r in sdb.inbox(self.con, MAINLINE, unread_only=True)]
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"project_id": PROJ, "recipient": MAINLINE, "unread_only": True},
        )
        actual = _data(result)
        self.assertIsInstance(actual, list)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(actual, expected)

    async def test_inbox_unread_only_default_is_true(self):
        """AC3: unread_only defaults to True — after marking all read, inbox returns empty."""
        # Mark all unread messages read by calling fetch (mark=True).
        sdb.fetch(self.con, self.store, MAINLINE, mark=True)
        # Now inbox with default unread_only=True should be empty.
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"project_id": PROJ, "recipient": MAINLINE},  # no unread_only → default True
        )
        actual = _data(result)
        self.assertIsInstance(actual, list)
        self.assertEqual(actual, [])

    async def test_inbox_unread_only_false_returns_all(self):
        """AC3 negative: unread_only=False returns all messages regardless of read status."""
        # Mark all read first.
        sdb.fetch(self.con, self.store, MAINLINE, mark=True)
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"project_id": PROJ, "recipient": MAINLINE, "unread_only": False},
        )
        actual = _data(result)
        # mid_subj and mid_body were sent to MAINLINE; mid_reply was sent from MAINLINE
        # so MAINLINE is sender not recipient there — only 2 rows.
        self.assertGreaterEqual(len(actual), 2)

    async def test_inbox_no_project_raises_toolerror(self):
        """AC3 error path: no project_id and no $SANDESH_PROJECT raises ToolError."""
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool("sandesh_inbox", {"recipient": MAINLINE})

    async def test_inbox_cc_recipient_stays_unread_after_to_recipient_reads(self):
        """AC3 / locked semantic: TRACK1 was cc'd on mid_body. Its unread state is
        independent of MAINLINE reading it."""
        # Fetch (mark=True) for MAINLINE — marks MAINLINE's rows read.
        sdb.fetch(self.con, self.store, MAINLINE, mark=True)
        # TRACK1 was cc'd on mid_body; it should still see it as unread.
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"project_id": PROJ, "recipient": TRACK1, "unread_only": True},
        )
        actual = _data(result)
        msg_ids = [row["id"] for row in actual]
        self.assertIn(self.mid_body, msg_ids)

    # ------------------------------------------------------------------
    # AC4 — sandesh_fetch parity (returns list[dict] already)

    async def test_fetch_returns_same_as_direct_call(self):
        """AC4: sandesh_fetch(project_id, recipient, mark) == fetch(con, store, recipient, mark)."""
        # Re-seed: fetch consumes unread state; use mark=False on both so the direct call
        # sees the same state the tool sees.
        # Open fresh connections so neither call has seen the rows already.
        con2 = sdb.connect()
        expected = sdb.fetch(con2, self.store, MAINLINE, mark=False)
        con2.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {"project_id": PROJ, "recipient": MAINLINE, "mark": False},
        )
        actual = _data(result)
        self.assertIsInstance(actual, list)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(actual, expected)

    async def test_fetch_mark_default_is_true_marks_messages_read(self):
        """AC4: mark defaults to True — after calling sandesh_fetch, inbox shows 0 unread."""
        # Call fetch without explicit mark (default True).
        await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {"project_id": PROJ, "recipient": MAINLINE},
        )
        # Now inbox with unread_only=True should be empty.
        unread = sdb.inbox(self.con, MAINLINE, unread_only=True)
        self.assertEqual(len(unread), 0)

    async def test_fetch_reads_body_from_file(self):
        """AC4 behavioural: fetch returns the body text for a message with body_path."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {"project_id": PROJ, "recipient": MAINLINE, "mark": False},
        )
        actual = _data(result)
        body_messages = [m for m in actual if m["id"] == self.mid_body]
        self.assertEqual(len(body_messages), 1)
        self.assertEqual(body_messages[0]["body"], "This is the message body.\n")

    async def test_fetch_subject_only_has_no_body(self):
        """AC4 behavioural: fetch returns body=None for subject-only messages."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {"project_id": PROJ, "recipient": MAINLINE, "mark": False},
        )
        actual = _data(result)
        subj_messages = [m for m in actual if m["id"] == self.mid_subj]
        self.assertEqual(len(subj_messages), 1)
        self.assertIsNone(subj_messages[0]["body"])

    async def test_fetch_no_project_raises_toolerror(self):
        """AC4 error path: no project_id and no $SANDESH_PROJECT raises ToolError."""
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool("sandesh_fetch", {"recipient": MAINLINE})

    # ------------------------------------------------------------------
    # AC5 — sandesh_thread parity (list[sqlite3.Row] → list[dict])

    async def test_thread_returns_same_as_direct_call_normalized(self):
        """AC5: sandesh_thread(project_id, msg_id) == [dict(r) for r in thread(con, msg_id)]."""
        expected = [dict(r) for r in sdb.thread(self.con, self.mid_reply)]
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"project_id": PROJ, "msg_id": self.mid_reply},
        )
        actual = _data(result)
        self.assertIsInstance(actual, list)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(actual, expected)

    async def test_thread_returns_chain_root_to_leaf(self):
        """AC5 behavioural: thread walks from root to the reply (ascending by id)."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"project_id": PROJ, "msg_id": self.mid_reply},
        )
        actual = _data(result)
        # Chain should be: mid_body → mid_reply (2 messages)
        self.assertEqual(len(actual), 2)
        self.assertEqual(actual[0]["id"], self.mid_body)
        self.assertEqual(actual[1]["id"], self.mid_reply)
        # Root has no in_reply_to; leaf links back
        self.assertIsNone(actual[0]["in_reply_to"])
        self.assertEqual(actual[1]["in_reply_to"], self.mid_body)

    async def test_thread_single_message_returns_single_element_list(self):
        """AC5 behavioural: a standalone (non-reply) message returns a 1-element thread."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"project_id": PROJ, "msg_id": self.mid_subj},
        )
        actual = _data(result)
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["id"], self.mid_subj)

    async def test_thread_no_project_raises_toolerror(self):
        """AC5 error path: no project_id and no $SANDESH_PROJECT raises ToolError."""
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool("sandesh_thread", {"msg_id": self.mid_subj})


if __name__ == "__main__":
    unittest.main()
