"""test_mcp_mutating_tools.py — MCP mutating tool parity tests (CR-SAN-003 §S1, AC1–AC11).

Tests drive the four mutating tools via FastMCP.call_tool and verify side-effects by
reading back from sandesh_db directly (fresh connections).

Return-shape reference (mcp 1.27.2):
  - int/str scalar tools → (list[TextContent], {"result": <value>})
  - list[dict] tools     → (list[TextContent], {"result": [...]})
  _scalar() extracts structured["result"] for int/str-returning tools.
  _data()   extracted from test_mcp_read_tools.py for list-returning tools.

CR-SAN-005 contract: sandesh_actioned has been removed (10 → 9 tools).
  - AC1/AC2 list_tools tests assert exactly 9 tools; sandesh_actioned must NOT be present.
  - sandesh_reply has no resolves/reply_all parameters (invariant lock, AC3).

  python-crucible.py test --tests tests.test_mcp_mutating_tools --agent CR-SAN-005-C0-RED
"""

import json
import os
import shutil
import tempfile
import unittest

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp.exceptions import ToolError

PROJ = "TestMut"
MAINLINE = "Mainline - TestMut"
TRACK1 = "Track 1 - TestMut"
TRACK2 = "Track 2 - TestMut"


def _scalar(result):
    """Unwrap FastMCP.call_tool's converted return for scalar (int/str) tools.

    Observed shape: (list[TextContent], {"result": <value>})
    Falls back to parsing TextContent.text if the structured tuple is absent.
    """
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        result = content
    if isinstance(result, list) and result:
        first = result[0]
        text = getattr(first, "text", first)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    return result


def _data(result):
    """Unwrap FastMCP.call_tool's converted return for list[dict]-returning tools.

    Copied from test_mcp_read_tools.py (same SDK version, same return shape).
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


class McpMutatingToolsTest(unittest.IsolatedAsyncioTestCase):
    """Parity + behaviour tests for the four mutating tools:
    sandesh_register, sandesh_unregister, sandesh_send, sandesh_reply.

    CR-SAN-005: sandesh_actioned removed; tool count is now 9.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-mutating-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)  # tests opt-in explicitly

        # Provision the project store.
        sdb.setup(PROJ)
        self.store = sdb.store_dir(PROJ)

    def tearDown(self):
        for k, v in (("XDG_DATA_HOME", self._prev_xdg), ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fresh_con(self):
        """Open a fresh DB connection to read back side-effects."""
        return sdb.connect()

    # ------------------------------------------------------------------
    # AC1 — list_tools includes all four mutating tools; sandesh_actioned is gone

    async def test_list_tools_includes_all_four_mutating_tools(self):
        """AC1 (CR-SAN-005): list_tools() must include sandesh_register, sandesh_unregister,
        sandesh_send, sandesh_reply — and must NOT include sandesh_actioned."""
        names = [t.name for t in await mcp_server.mcp.list_tools()]
        self.assertIn("sandesh_register", names)
        self.assertIn("sandesh_unregister", names)
        self.assertIn("sandesh_send", names)
        self.assertIn("sandesh_reply", names)
        # CR-SAN-005 RED driver: sandesh_actioned IS still present → this will FAIL
        self.assertNotIn("sandesh_actioned", names)

    # ------------------------------------------------------------------
    # AC2 — exactly 9 tools total (CR-SAN-005: sandesh_actioned removed)

    async def test_list_tools_returns_exactly_nine_tools(self):
        """AC2 (CR-SAN-005): list_tools() returns exactly 9 tools total
        (sandesh_setup + 4 read tools + 4 mutating tools = 9).
        RED driver: currently 10 — assertEqual(9) will FAIL."""
        tools = await mcp_server.mcp.list_tools()
        names = [t.name for t in tools]
        self.assertEqual(
            len(names), 9,
            f"expected exactly 9 tools but got {len(names)}: {sorted(names)}",
        )

    # ------------------------------------------------------------------
    # AC3 — sandesh_register registers the address (visible in addressbook)

    async def test_register_address_appears_in_addressbook(self):
        """AC3: sandesh_register(project_id, addr) registers the address; it is visible
        in addressbook(con)."""
        await mcp_server.mcp.call_tool(
            "sandesh_register",
            {"project_id": PROJ, "addr": MAINLINE},
        )
        con = self._fresh_con()
        try:
            book = sdb.addressbook(con, PROJ)
            addresses = [row["address"] for row in book]
            self.assertIn(MAINLINE, addresses)
            # Exactly one entry (we registered one)
            self.assertEqual(len(addresses), 1)
        finally:
            con.close()

    async def test_register_address_is_active(self):
        """AC3 behavioural: a registered address has active=True."""
        await mcp_server.mcp.call_tool(
            "sandesh_register",
            {"project_id": PROJ, "addr": MAINLINE, "kind": "mainline"},
        )
        con = self._fresh_con()
        try:
            self.assertTrue(sdb.is_active(con, MAINLINE))
        finally:
            con.close()

    async def test_register_stores_kind_and_display_name(self):
        """AC3 behavioural: kind and display_name are persisted in the addressbook."""
        await mcp_server.mcp.call_tool(
            "sandesh_register",
            {"project_id": PROJ, "addr": MAINLINE, "kind": "mainline",
             "display_name": "Main Line", "by": MAINLINE},
        )
        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT kind, display_name FROM address WHERE address=?", (MAINLINE,)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["kind"], "mainline")
            self.assertEqual(row["display_name"], "Main Line")
        finally:
            con.close()

    async def test_register_passes_project_id_to_library(self):
        """AC3 / project-routing: registering an address whose project part does not
        match project_id must raise ToolError (validates project=project_id routing)."""
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool(
                "sandesh_register",
                {"project_id": PROJ, "addr": "Mainline - WrongProject"},
            )

    # ------------------------------------------------------------------
    # AC4 — sandesh_send creates a message (recipient sees it via inbox/fetch)

    async def test_send_message_recipient_sees_it_in_inbox(self):
        """AC4: sandesh_send(project_id, from_addr, to, subject) creates a message;
        the recipient sees it in inbox."""
        # Register sender and recipient via the library directly.
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "project_id": PROJ,
                "from_addr": TRACK1,
                "to": [MAINLINE],
                "subject": "ping",
            },
        )

        con = self._fresh_con()
        try:
            unread = sdb.inbox(con, MAINLINE, unread_only=True)
            subjects = [r["subject"] for r in unread]
            self.assertIn("ping", subjects)
            self.assertEqual(len(subjects), 1)
        finally:
            con.close()

    async def test_send_returns_new_message_id(self):
        """AC4 behavioural: sandesh_send returns the new message id (int > 0)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "project_id": PROJ,
                "from_addr": TRACK1,
                "to": [MAINLINE],
                "subject": "ping",
            },
        )
        mid = _scalar(result)
        self.assertIsInstance(mid, int)
        self.assertGreater(mid, 0)

    async def test_send_with_body_text_body_is_retrievable(self):
        """AC4 behavioural: sandesh_send with body_text stores the body; fetch reads it back."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "project_id": PROJ,
                "from_addr": TRACK1,
                "to": [MAINLINE],
                "subject": "report",
                "body_text": "This is the report body.\n",
            },
        )
        mid = _scalar(result)

        con = self._fresh_con()
        try:
            fetched = sdb.fetch(con, self.store, MAINLINE, mark=False)
            matching = [m for m in fetched if m["id"] == mid]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["body"], "This is the report body.\n")
        finally:
            con.close()

    async def test_send_cc_recipient_stays_unread_after_to_recipient_reads(self):
        """AC4 / locked semantic: cc recipient's read_at is independent of the to
        recipient reading the message (per-recipient read state)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        sdb.register(con, TRACK2, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "project_id": PROJ,
                "from_addr": TRACK2,
                "to": [MAINLINE],
                "cc": [TRACK1],
                "subject": "broadcast-style",
            },
        )
        mid = _scalar(result)

        # Mark MAINLINE's copy read.
        con = self._fresh_con()
        sdb.mark_read(con, MAINLINE, [mid])
        con.close()

        # TRACK1 (cc'd) should still see it as unread.
        con = self._fresh_con()
        try:
            unread = sdb.inbox(con, TRACK1, unread_only=True)
            msg_ids = [r["id"] for r in unread]
            self.assertIn(mid, msg_ids)
        finally:
            con.close()

    async def test_send_passes_store_and_project_id(self):
        """AC4 / project-routing: message is stored in the correct project's store
        (a body file is written under this project's store dir, not another)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "project_id": PROJ,
                "from_addr": TRACK1,
                "to": [MAINLINE],
                "subject": "store check",
                "body_text": "body content\n",
            },
        )
        mid = _scalar(result)

        # The body file must exist under this project's store dir.
        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT body_path FROM message WHERE id=?", (mid,)
            ).fetchone()
            self.assertIsNotNone(row["body_path"])
            self.assertTrue(row["body_path"].startswith(self.store))
            self.assertTrue(os.path.isfile(row["body_path"]))
        finally:
            con.close()

    # ------------------------------------------------------------------
    # AC5 — sandesh_reply creates a reply linked via in_reply_to

    async def test_reply_creates_message_linked_to_parent(self):
        """AC5: sandesh_reply(project_id, parent_id, from_addr) creates a reply whose
        in_reply_to links the parent message id."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        parent_id = sdb.send(
            con, self.store, TRACK1, to=[MAINLINE], subject="request", project=PROJ
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "project_id": PROJ,
                "parent_id": parent_id,
                "from_addr": MAINLINE,
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT in_reply_to, subject FROM message WHERE id=?", (reply_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["in_reply_to"], parent_id)
            # Subject defaults to Re: <parent subject>
            self.assertEqual(row["subject"], "Re: request")
        finally:
            con.close()

    async def test_reply_returns_new_message_id(self):
        """AC5 behavioural: sandesh_reply returns the new reply message id (int > 0)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        parent_id = sdb.send(
            con, self.store, TRACK1, to=[MAINLINE], subject="request", project=PROJ
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "project_id": PROJ,
                "parent_id": parent_id,
                "from_addr": MAINLINE,
            },
        )
        reply_id = _scalar(result)
        self.assertIsInstance(reply_id, int)
        self.assertGreater(reply_id, parent_id)

    async def test_reply_with_body_text_stores_body(self):
        """AC5 behavioural: sandesh_reply with body_text stores a body file in the store."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        parent_id = sdb.send(
            con, self.store, TRACK1, to=[MAINLINE], subject="request", project=PROJ
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "project_id": PROJ,
                "parent_id": parent_id,
                "from_addr": MAINLINE,
                "body_text": "ACK — done.\n",
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            fetched = sdb.fetch(con, self.store, TRACK1, mark=False)
            reply_msgs = [m for m in fetched if m["id"] == reply_id]
            self.assertEqual(len(reply_msgs), 1)
            self.assertEqual(reply_msgs[0]["body"], "ACK — done.\n")
        finally:
            con.close()

    async def test_reply_with_custom_subject(self):
        """AC5 behavioural: sandesh_reply with an explicit subject uses it."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        parent_id = sdb.send(
            con, self.store, TRACK1, to=[MAINLINE], subject="request", project=PROJ
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "project_id": PROJ,
                "parent_id": parent_id,
                "from_addr": MAINLINE,
                "subject": "Custom Reply Subject",
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT subject FROM message WHERE id=?", (reply_id,)
            ).fetchone()
            self.assertEqual(row["subject"], "Custom Reply Subject")
        finally:
            con.close()

    async def test_reply_passes_store_and_project_id(self):
        """AC5 / project-routing: reply is stored in the correct project's store."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        parent_id = sdb.send(
            con, self.store, TRACK1, to=[MAINLINE], subject="p", project=PROJ
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "project_id": PROJ,
                "parent_id": parent_id,
                "from_addr": MAINLINE,
                "body_text": "body\n",
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT body_path FROM message WHERE id=?", (reply_id,)
            ).fetchone()
            self.assertIsNotNone(row["body_path"])
            self.assertTrue(row["body_path"].startswith(self.store))
        finally:
            con.close()

    # ------------------------------------------------------------------
    # AC6 — sandesh_unregister returns the library's result tuple

    async def test_unregister_no_live_notifier_returns_unregistered(self):
        """AC6: sandesh_unregister with no live notifier returns ('unregistered', None)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_unregister",
            {
                "project_id": PROJ,
                "recipient": TRACK1,
                "requester": MAINLINE,
            },
        )
        value = _scalar(result)
        # The library returns ('unregistered', None) → expect a list/tuple of 2 elements
        self.assertEqual(len(value), 2)
        self.assertEqual(value[0], "unregistered")
        self.assertIsNone(value[1])

    async def test_unregister_soft_deletes_the_address(self):
        """AC6 behavioural: after sandesh_unregister (no live notifier), the address
        is soft-deleted (is_active returns False)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        await mcp_server.mcp.call_tool(
            "sandesh_unregister",
            {
                "project_id": PROJ,
                "recipient": TRACK1,
                "requester": MAINLINE,
            },
        )

        con = self._fresh_con()
        try:
            self.assertFalse(sdb.is_active(con, TRACK1))
        finally:
            con.close()

    async def test_unregister_self_removal_allowed(self):
        """AC6 behavioural: a track may unregister itself (requester == recipient)."""
        con = self._fresh_con()
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_unregister",
            {
                "project_id": PROJ,
                "recipient": TRACK1,
                "requester": TRACK1,
            },
        )
        value = _scalar(result)
        self.assertEqual(value[0], "unregistered")

        con = self._fresh_con()
        try:
            self.assertFalse(sdb.is_active(con, TRACK1))
        finally:
            con.close()

    async def test_unregister_with_live_notifier_returns_tombstoned(self):
        """AC6 behavioural: when a live notifier exists, sandesh_unregister returns
        ('tombstoned', pid) and the address stays active (not yet soft-deleted)."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        # Acquire a live notifier for TRACK1 using the current process PID.
        sdb.notifier_acquire(con, TRACK1, os.getpid(), "tok-live", "localhost")
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_unregister",
            {
                "project_id": PROJ,
                "recipient": TRACK1,
                "requester": MAINLINE,
            },
        )
        value = _scalar(result)
        self.assertEqual(len(value), 2)
        self.assertEqual(value[0], "tombstoned")
        # pid must be an int matching the current process
        self.assertEqual(value[1], os.getpid())

        # Address is still active (notifier hasn't exited yet)
        con = self._fresh_con()
        try:
            self.assertTrue(sdb.is_active(con, TRACK1))
        finally:
            con.close()

    # ------------------------------------------------------------------
    # AC3 (CR-SAN-005) — sandesh_reply has no resolves or reply_all parameters

    async def test_reply_has_no_resolves_parameter(self):
        """AC3 (CR-SAN-005): sandesh_reply exposes no 'resolves' parameter.
        The status-disposition model was removed; reply = done, no separate flag needed.
        This is a lock invariant — must hold now and after GREEN."""
        import inspect
        sig = inspect.signature(mcp_server.sandesh_reply)
        self.assertNotIn(
            "resolves",
            sig.parameters,
            "sandesh_reply must not expose a 'resolves' parameter (CR-SAN-005 AC3)",
        )

    async def test_reply_has_no_reply_all_parameter(self):
        """AC3 (CR-SAN-005): sandesh_reply exposes no 'reply_all' parameter.
        Lock invariant — must hold now and after GREEN."""
        import inspect
        sig = inspect.signature(mcp_server.sandesh_reply)
        self.assertNotIn(
            "reply_all",
            sig.parameters,
            "sandesh_reply must not expose a 'reply_all' parameter (CR-SAN-005 AC3)",
        )

    # ------------------------------------------------------------------
    # AC8 — malformed address via sandesh_register raises ToolError
    # NOTE: at RED this test passes VACUOUSLY — "sandesh_register" is an Unknown tool,
    # so call_tool raises ToolError regardless. At GREEN it becomes meaningful:
    # the ToolError must carry the library's validation message, not "Unknown tool".

    async def test_register_malformed_address_raises_toolerror(self):
        """AC8: sandesh_register with a malformed address raises ToolError carrying
        the library's validation message (expected '<Orchestrator> - <Project>')."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_register",
                {"project_id": PROJ, "addr": "not a valid address"},
            )
        # At GREEN: the error message must come from the library's validate_address.
        # We can't assert the exact message at RED (ToolError just says Unknown tool),
        # but we record the shape for GREEN verification.
        self.assertIsNotNone(ctx.exception)

    async def test_register_malformed_address_error_mentions_expected_format(self):
        """AC8 behavioural: the ToolError from a malformed address must mention the
        expected format string from the library's validate_address message.
        NOTE: this test is VACUOUS at RED (Unknown tool error, not validation error)
        and becomes a real assertion at GREEN."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_register",
                {"project_id": PROJ, "addr": "bad@address"},
            )
        err_msg = str(ctx.exception)
        # The library raises:
        # "bad address 'bad@address': expected '<Orchestrator> - <Project>', ..."
        # At RED: ToolError says "Unknown tool: sandesh_register" — this assertion FAILS.
        # At GREEN: the tool exists and maps the library's ValueError to ToolError.
        self.assertIn("expected '<Orchestrator> - <Project>'", err_msg)

    # ------------------------------------------------------------------
    # AC9 — unauthorized unregister raises ToolError
    # NOTE: same vacuous-pass trap at RED — Unknown tool raises ToolError.
    # At GREEN: must be the library's PermissionError mapped to ToolError.

    async def test_unregister_unauthorized_requester_raises_toolerror(self):
        """AC9: sandesh_unregister where requester is neither Mainline nor self raises
        ToolError with the library's authorization message."""
        # Pre-register the addresses via the library so the validation reaches the
        # permission check (at GREEN; at RED this never executes — Unknown tool fires first).
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        sdb.register(con, TRACK2, kind="track", project=PROJ)
        con.close()

        # Track 2 tries to unregister Track 1 — neither Mainline nor self.
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_unregister",
                {
                    "project_id": PROJ,
                    "recipient": TRACK1,
                    "requester": TRACK2,
                },
            )
        self.assertIsNotNone(ctx.exception)

    async def test_unregister_unauthorized_error_mentions_mainline(self):
        """AC9 behavioural: the ToolError from unauthorized unregister must mention
        'Mainline' (the library's authorization message: 'only Mainline may remove
        another participant').
        NOTE: VACUOUS at RED (Unknown tool error); meaningful at GREEN."""
        con = self._fresh_con()
        sdb.register(con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(con, TRACK1, kind="track", project=PROJ)
        sdb.register(con, TRACK2, kind="track", project=PROJ)
        con.close()

        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_unregister",
                {
                    "project_id": PROJ,
                    "recipient": TRACK1,
                    "requester": TRACK2,
                },
            )
        err_msg = str(ctx.exception)
        # The library raises:
        # PermissionError("only Mainline may remove another participant")
        # At RED: ToolError says "Unknown tool: sandesh_unregister" — this assertion FAILS.
        # At GREEN: the tool exists and maps the library's PermissionError to ToolError.
        self.assertIn("only Mainline may remove another participant", err_msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
