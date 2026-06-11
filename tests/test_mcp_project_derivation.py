"""test_mcp_project_derivation.py — CR-SAN-025 §S2/AC5 + §S3/AC6 RED tests.

Tests the project_id derivation chain when BOTH `project_id` arg AND
$SANDESH_PROJECT env are absent:
  - sandesh_send / sandesh_reply: derive from from_addr's <Project> part
  - sandesh_fetch: derives from recipient's <Project> part
  - sandesh_inbox / sandesh_thread: succeed with no project (recipient/id-keyed)
  - sandesh_setup / sandesh_addressbook / sandesh_register: UNCHANGED — still raise
    "project_id is required" ToolError (locked pins)

AC6 pins (docs markers):
  - SANDESH_INSTRUCTIONS contains: 'cross-project', 'grant', 'archive'
  - _read_usage_doc() output contains: 'cross-project', 'sandesh grant', and archive
    lifecycle mention
  - sandesh_archive docstring is verifiable via tool metadata (listed in tools)

Expected RED for items 1–4: these currently raise the "project_id is required"
ToolError because _resolve_project is called unconditionally.
Items 5–6 (mismatch tests) may already pass — recorded as locked pins.
Items 7 (setup/addressbook) may already pass — recorded as locked pins.
Item 8 (AC6 docs) will FAIL on missing markers.

  python-crucible.py test --tests tests.test_mcp_project_derivation --agent red-cr025-c2
"""

import json
import os
import shutil
import tempfile
import unittest

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from sandesh.mcp_server import SANDESH_INSTRUCTIONS, _read_usage_doc
from mcp.server.fastmcp.exceptions import ToolError

# Two projects for cross-project / derivation tests
PROJ1 = "P1"
PROJ2 = "P2"
MAINLINE_P1 = "Mainline - P1"
TRACK1_P1 = "Track 1 - P1"
MAINLINE_P2 = "Mainline - P2"
TRACK1_P2 = "Track 1 - P2"


def _scalar(result):
    """Unwrap FastMCP.call_tool's converted return for scalar (int/str) tools."""
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
    """Unwrap FastMCP.call_tool's converted return for list[dict]-returning tools."""
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


class McpProjectDerivationTest(unittest.IsolatedAsyncioTestCase):
    """Tests for the project_id derivation chain (CR-SAN-025 §S2, AC5).

    Fixture: temp XDG_DATA_HOME, $SANDESH_PROJECT popped, P1+P2 provisioned,
    Mainline registered in each. Tests call MCP tools with NO project_id arg
    and NO $SANDESH_PROJECT in env — the derivation chain or global lookup must kick in.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-derivation-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)   # NO env project — derivation must kick in

        # Provision both project stores.
        sdb.setup(PROJ1)
        sdb.setup(PROJ2)

        self.store1 = sdb.store_dir(PROJ1)
        self.store2 = sdb.store_dir(PROJ2)

        # Register participants in both projects via the library.
        con = sdb.connect()
        try:
            sdb.register(con, MAINLINE_P1, kind="mainline", project=PROJ1)
            sdb.register(con, TRACK1_P1, kind="track", project=PROJ1)
            sdb.register(con, MAINLINE_P2, kind="mainline", project=PROJ2)
            sdb.register(con, TRACK1_P2, kind="track", project=PROJ2)
            # Admin + grant so cross-project sends work in derivation tests.
            sdb.assign_admin(con, "ops")
            sdb.grant_xproj(con, PROJ1, "ops")
            sdb.grant_xproj(con, PROJ2, "ops")
        finally:
            con.close()

    def tearDown(self):
        for k, v in (("XDG_DATA_HOME", self._prev_xdg),
                     ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fresh_con(self):
        """Open a fresh DB connection to read back side-effects."""
        return sdb.connect()

    # ------------------------------------------------------------------ #
    # 1. sandesh_send — derive project from from_addr's <Project> part    #
    # ------------------------------------------------------------------ #

    async def test_send_no_project_derives_from_from_addr_succeeds(self):
        """AC5/S2: sandesh_send with no project_id and no $SANDESH_PROJECT succeeds —
        context is derived from from_addr ('Mainline - P1' → project='P1').
        RED: currently raises 'project_id is required' ToolError."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "from_addr": MAINLINE_P1,
                "to": [TRACK1_P1],
                "subject": "hello via derivation",
            },
        )
        mid = _scalar(result)
        self.assertIsInstance(mid, int, f"Expected message id (int), got: {mid!r}")
        self.assertGreater(mid, 0, "Message id must be > 0")

    async def test_send_no_project_message_row_in_correct_project(self):
        """AC5/S2: the message created via derivation is visible to the recipient
        in P1 (verifies the correct project store was used).
        RED: currently raises 'project_id is required' ToolError."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "from_addr": MAINLINE_P1,
                "to": [TRACK1_P1],
                "subject": "store-check via derivation",
            },
        )
        mid = _scalar(result)

        con = self._fresh_con()
        try:
            unread = sdb.inbox(con, TRACK1_P1, unread_only=True)
            msg_ids = [r["id"] for r in unread]
        finally:
            con.close()
        self.assertIn(mid, msg_ids,
                      f"Message {mid} must appear in TRACK1_P1's inbox; "
                      f"got ids: {msg_ids}")
        self.assertEqual(len(msg_ids), 1,
                         f"Exactly 1 unread message expected; got {len(msg_ids)}")

    async def test_send_no_project_body_file_under_sender_project_folder(self):
        """AC5/S2: when body_text is given, the body file lands under
        projects/<sender-project>/messages/ — i.e. sender's own store folder.
        RED: currently raises 'project_id is required' ToolError."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_send",
            {
                "from_addr": MAINLINE_P1,
                "to": [TRACK1_P1],
                "subject": "body via derivation",
                "body_text": "body content for derivation test\n",
            },
        )
        mid = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT body_path FROM message WHERE id=?", (mid,)
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(row, f"Message row for id={mid} must exist")
        body_path = row["body_path"]
        self.assertIsNotNone(body_path, "body_path must not be NULL when body_text given")
        # Body file must be under the SENDER's project store folder (P1)
        self.assertTrue(
            body_path.startswith(self.store1),
            f"body_path must start with store1={self.store1!r}; got {body_path!r}",
        )
        self.assertTrue(
            os.path.isfile(body_path),
            f"body file must exist on disk at {body_path!r}",
        )

    # ------------------------------------------------------------------ #
    # 2. sandesh_reply — derive project from from_addr's <Project> part   #
    # ------------------------------------------------------------------ #

    async def test_reply_no_project_derives_from_from_addr_succeeds(self):
        """AC5/S2: sandesh_reply with no project_id and no $SANDESH_PROJECT succeeds —
        context derived from from_addr ('Mainline - P1' → project='P1').
        RED: currently raises 'project_id is required' ToolError."""
        # Seed a parent message using the library (explicit project)
        con = self._fresh_con()
        parent_id = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="request for reply test", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "parent_id": parent_id,
                "from_addr": MAINLINE_P1,
                # NO project_id — must derive from from_addr
            },
        )
        reply_id = _scalar(result)
        self.assertIsInstance(reply_id, int,
                              f"Expected reply id (int), got: {reply_id!r}")
        self.assertGreater(reply_id, parent_id,
                           f"Reply id must exceed parent id {parent_id}")

    async def test_reply_no_project_links_via_in_reply_to(self):
        """AC5/S2: reply created without project_id is correctly threaded
        (in_reply_to links back to parent).
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        parent_id = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="parent for threading test", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "parent_id": parent_id,
                "from_addr": MAINLINE_P1,
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT in_reply_to, subject FROM message WHERE id=?", (reply_id,)
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(row, f"Reply message row for id={reply_id} must exist")
        self.assertEqual(
            row["in_reply_to"], parent_id,
            f"in_reply_to must be {parent_id}; got {row['in_reply_to']!r}",
        )
        # Default subject is "Re: <parent subject>"
        self.assertEqual(
            row["subject"], "Re: parent for threading test",
            f"Reply subject must be 'Re: parent for threading test'; got {row['subject']!r}",
        )

    async def test_reply_no_project_body_file_under_sender_project_folder(self):
        """AC5/S2: reply with body_text stores the body under the sender's
        project folder (sender is 'Mainline - P1' → P1 store).
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        parent_id = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="parent for body test", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_reply",
            {
                "parent_id": parent_id,
                "from_addr": MAINLINE_P1,
                "body_text": "reply body via derivation\n",
            },
        )
        reply_id = _scalar(result)

        con = self._fresh_con()
        try:
            row = con.execute(
                "SELECT body_path FROM message WHERE id=?", (reply_id,)
            ).fetchone()
        finally:
            con.close()

        self.assertIsNotNone(row, f"Reply message row for id={reply_id} must exist")
        body_path = row["body_path"]
        self.assertIsNotNone(body_path, "body_path must not be NULL when body_text given")
        self.assertTrue(
            body_path.startswith(self.store1),
            f"body_path must be under P1 store ({self.store1!r}); got {body_path!r}",
        )
        self.assertTrue(os.path.isfile(body_path),
                        f"body file must exist on disk at {body_path!r}")

    # ------------------------------------------------------------------ #
    # 3. sandesh_fetch — derive project from recipient's <Project> part   #
    # ------------------------------------------------------------------ #

    async def test_fetch_no_project_derives_from_recipient_succeeds(self):
        """AC5/S2: sandesh_fetch with no project_id and no $SANDESH_PROJECT succeeds —
        context derived from recipient ('Mainline - P1' → project='P1').
        RED: currently raises 'project_id is required' ToolError."""
        # Seed an unread message to MAINLINE_P1
        con = self._fresh_con()
        sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="fetch me via derivation", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {
                "recipient": MAINLINE_P1,
                # NO project_id — must derive from recipient
                "mark": False,
            },
        )
        messages = _data(result)
        self.assertIsInstance(messages, list,
                              f"Expected list of messages; got: {type(messages)}")
        self.assertGreater(len(messages), 0,
                           "Must return at least one message for MAINLINE_P1")

    async def test_fetch_no_project_returns_correct_message_subject(self):
        """AC5/S2: fetch without project_id returns the correct message — body readable.
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="subject-only fetch derivation", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {
                "recipient": MAINLINE_P1,
                "mark": False,
            },
        )
        messages = _data(result)
        ids = [m["id"] for m in messages]
        self.assertIn(mid, ids,
                      f"Message {mid} must appear in fetched results; got ids: {ids}")
        matching = [m for m in messages if m["id"] == mid]
        self.assertEqual(matching[0]["subject"], "subject-only fetch derivation",
                         f"Subject mismatch: {matching[0]['subject']!r}")

    async def test_fetch_no_project_body_readable_from_correct_store(self):
        """AC5/S2: fetch without project_id can read a body file from the
        derived project's store (body is returned in the message dict, not None).
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1],
            subject="body fetch derivation",
            body_text="body text for fetch derivation test\n",
            project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {
                "recipient": MAINLINE_P1,
                "mark": False,
            },
        )
        messages = _data(result)
        matching = [m for m in messages if m["id"] == mid]
        self.assertEqual(len(matching), 1,
                         f"Expected exactly 1 matching message for id={mid}; "
                         f"got {len(matching)}")
        self.assertEqual(
            matching[0]["body"], "body text for fetch derivation test\n",
            f"Body text mismatch: {matching[0]['body']!r}",
        )

    # ------------------------------------------------------------------ #
    # 4. sandesh_inbox — works with no project (global/recipient-keyed)   #
    # ------------------------------------------------------------------ #

    async def test_inbox_no_project_succeeds(self):
        """AC5/S2: sandesh_inbox with no project_id and no $SANDESH_PROJECT succeeds —
        inbox is recipient-keyed and needs no project routing.
        RED: currently raises 'project_id is required' ToolError."""
        # Seed a message to MAINLINE_P1
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="inbox no-project test", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "recipient": MAINLINE_P1,
                "unread_only": True,
                # NO project_id — must work without it
            },
        )
        messages = _data(result)
        self.assertIsInstance(messages, list,
                              f"Expected list; got {type(messages)}")
        ids = [m["id"] for m in messages]
        self.assertIn(mid, ids,
                      f"Message {mid} must appear in inbox; got ids: {ids}")

    async def test_inbox_no_project_returns_correct_subject(self):
        """AC5/S2: inbox without project_id returns the expected message subject.
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="distinct-inbox-subject-42", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "recipient": MAINLINE_P1,
            },
        )
        messages = _data(result)
        subjects = [m["subject"] for m in messages]
        self.assertIn(
            "distinct-inbox-subject-42", subjects,
            f"Expected subject in inbox; got: {subjects}",
        )
        self.assertEqual(len(subjects), 1,
                         f"Expected exactly 1 unread message; got {len(subjects)}")

    async def test_inbox_no_project_unread_only_false_returns_all(self):
        """AC5/S2: inbox without project_id, unread_only=False returns read + unread.
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="read-already", project=PROJ1,
        )
        sdb.mark_read(con, MAINLINE_P1, [mid])
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "recipient": MAINLINE_P1,
                "unread_only": False,
            },
        )
        messages = _data(result)
        ids = [m["id"] for m in messages]
        self.assertIn(mid, ids,
                      f"Already-read message {mid} must appear with unread_only=False; "
                      f"got: {ids}")

    # ------------------------------------------------------------------ #
    # 4b. sandesh_thread — works with no project (id-keyed global query)  #
    # ------------------------------------------------------------------ #

    async def test_thread_no_project_succeeds(self):
        """AC5/S2: sandesh_thread with no project_id and no $SANDESH_PROJECT succeeds —
        thread is msg_id-keyed and needs no project routing.
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        mid = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="thread no-project root", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {
                "msg_id": mid,
                # NO project_id — must work without it
            },
        )
        messages = _data(result)
        self.assertIsInstance(messages, list,
                              f"Expected list; got {type(messages)}")
        self.assertEqual(len(messages), 1,
                         f"Standalone message yields 1-element thread; got {len(messages)}")
        self.assertEqual(messages[0]["id"], mid,
                         f"Thread root id must be {mid}; got {messages[0]['id']!r}")

    async def test_thread_no_project_returns_full_chain(self):
        """AC5/S2: thread without project_id returns the full reply chain root-to-leaf.
        RED: currently raises 'project_id is required' ToolError."""
        con = self._fresh_con()
        parent_id = sdb.send(
            con, self.store1, TRACK1_P1,
            to=[MAINLINE_P1], subject="chain root", project=PROJ1,
        )
        reply_id = sdb.reply(
            con, self.store1, parent_id, MAINLINE_P1,
            subject="Re: chain root", project=PROJ1,
        )
        con.close()

        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {
                "msg_id": reply_id,
            },
        )
        messages = _data(result)
        self.assertEqual(len(messages), 2,
                         f"Thread must have 2 entries (root + reply); got {len(messages)}")
        self.assertEqual(messages[0]["id"], parent_id,
                         f"Thread[0] must be root id={parent_id}; got {messages[0]['id']!r}")
        self.assertEqual(messages[1]["id"], reply_id,
                         f"Thread[1] must be reply id={reply_id}; got {messages[1]['id']!r}")

    # ------------------------------------------------------------------ #
    # 5. Explicit project still wins — mismatch raises ToolError          #
    # (may already pass — locked pin)                                     #
    # ------------------------------------------------------------------ #

    async def test_send_explicit_project_mismatch_from_addr_raises_toolerror(self):
        """Locked pin: sandesh_send(project_id='P1', from_addr='Mainline - P2', ...)
        raises ToolError because the address project (P2) != project_id (P1).
        This validates the sender-must-match-context rule is unchanged.
        This test may already pass (locked pin — must not regress)."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_send",
                {
                    "project_id": PROJ1,          # explicit: P1
                    "from_addr": MAINLINE_P2,     # address project: P2 — mismatch
                    "to": [TRACK1_P1],
                    "subject": "mismatch test",
                },
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "address project", err_msg.lower(),
            f"ToolError must mention 'address project'; got: {err_msg!r}",
        )

    # ------------------------------------------------------------------ #
    # 6. Env still wins — $SANDESH_PROJECT mismatch raises ToolError      #
    # (may already pass — locked pin)                                     #
    # ------------------------------------------------------------------ #

    async def test_send_env_project_mismatch_from_addr_raises_toolerror(self):
        """Locked pin: with $SANDESH_PROJECT=P1, sandesh_send(from_addr='Mainline - P2',
        no project_id) → env acts as context (project='P1'), from_addr's project='P2'
        → mismatch ToolError.
        This test may already pass (locked pin — must not regress)."""
        os.environ["SANDESH_PROJECT"] = PROJ1  # env is P1 — acts as context
        try:
            with self.assertRaises(ToolError) as ctx:
                await mcp_server.mcp.call_tool(
                    "sandesh_send",
                    {
                        # NO project_id — env provides P1
                        "from_addr": MAINLINE_P2,   # address project: P2 — mismatch with env
                        "to": [TRACK1_P1],
                        "subject": "env mismatch test",
                    },
                )
            err_msg = str(ctx.exception)
            self.assertIn(
                "address project", err_msg.lower(),
                f"ToolError must mention 'address project'; got: {err_msg!r}",
            )
        finally:
            os.environ.pop("SANDESH_PROJECT", None)

    # ------------------------------------------------------------------ #
    # 7. sandesh_setup + sandesh_addressbook — UNCHANGED (locked pins)    #
    # ------------------------------------------------------------------ #

    async def test_setup_no_project_raises_toolerror_project_id_required(self):
        """Locked pin: sandesh_setup() with neither project_id nor $SANDESH_PROJECT
        still raises ToolError containing 'project_id is required'.
        Behaviour is UNCHANGED for setup/addressbook/register — these keep today's
        explicit-or-env requirement."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_setup",
                {},   # no project_id, no env
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "project_id is required", err_msg,
            f"ToolError from setup with no project must contain "
            f"'project_id is required'; got: {err_msg!r}",
        )

    async def test_addressbook_no_project_raises_toolerror_project_id_required(self):
        """Locked pin: sandesh_addressbook() with neither project_id nor $SANDESH_PROJECT
        still raises ToolError containing 'project_id is required'.
        Behaviour is UNCHANGED — no derivation for addressbook."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_addressbook",
                {},   # no project_id, no env
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "project_id is required", err_msg,
            f"ToolError from addressbook with no project must contain "
            f"'project_id is required'; got: {err_msg!r}",
        )

    # ------------------------------------------------------------------ #
    # 8. AC6 — docs markers: SANDESH_INSTRUCTIONS + usage doc             #
    # ------------------------------------------------------------------ #

    def test_ac6_instructions_contains_cross_project_marker(self):
        """AC6: SANDESH_INSTRUCTIONS must contain 'cross-project' (cross-project
        sending is documented in the server instructions).
        RED: currently missing — the instructions mention only same-project messaging."""
        self.assertIn(
            "cross-project", SANDESH_INSTRUCTIONS,
            f"SANDESH_INSTRUCTIONS must contain 'cross-project'; "
            f"current instructions:\n{SANDESH_INSTRUCTIONS!r}",
        )

    def test_ac6_instructions_contains_grant_marker(self):
        """AC6: SANDESH_INSTRUCTIONS must contain 'grant' — agents need to know
        about the admin grant step when cross-project sending is rejected.
        RED: currently missing."""
        self.assertIn(
            "grant", SANDESH_INSTRUCTIONS,
            f"SANDESH_INSTRUCTIONS must contain 'grant'; "
            f"current instructions:\n{SANDESH_INSTRUCTIONS!r}",
        )

    def test_ac6_instructions_contains_archive_marker(self):
        """AC6: SANDESH_INSTRUCTIONS must contain 'archive' — agents need to
        understand the archive lifecycle.
        RED: currently missing."""
        self.assertIn(
            "archive", SANDESH_INSTRUCTIONS,
            f"SANDESH_INSTRUCTIONS must contain 'archive'; "
            f"current instructions:\n{SANDESH_INSTRUCTIONS!r}",
        )

    def test_ac6_usage_doc_contains_cross_project_marker(self):
        """AC6: _read_usage_doc() must contain 'cross-project' — the usage resource
        documents cross-project sending for agents that read it.
        RED: currently missing from usage-scenarios.md."""
        usage = _read_usage_doc()
        self.assertIn(
            "cross-project", usage,
            f"usage doc must contain 'cross-project'; "
            f"first 200 chars:\n{usage[:200]!r}",
        )

    def test_ac6_usage_doc_contains_sandesh_grant_marker(self):
        """AC6: _read_usage_doc() must contain 'sandesh grant' — the usage resource
        tells agents the CLI command for the admin grant step.
        RED: currently missing."""
        usage = _read_usage_doc()
        self.assertIn(
            "sandesh grant", usage,
            f"usage doc must contain 'sandesh grant' (the admin CLI command); "
            f"first 200 chars:\n{usage[:200]!r}",
        )

    def test_ac6_usage_doc_mentions_archive_lifecycle(self):
        """AC6: _read_usage_doc() must contain 'archive' — the usage resource
        documents the archive lifecycle so agents understand the error they may encounter.
        RED: currently missing from usage-scenarios.md."""
        usage = _read_usage_doc()
        self.assertIn(
            "archive", usage,
            f"usage doc must contain 'archive'; "
            f"first 200 chars:\n{usage[:200]!r}",
        )

    async def test_ac6_sandesh_archive_tool_listed_in_tools(self):
        """AC6 (tool-metadata): sandesh_archive appears in list_tools() so its
        docstring is reachable via the MCP surface.
        This should already pass (C1 shipped) — included as a locked pin."""
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        self.assertIn(
            "sandesh_archive", names,
            f"sandesh_archive must be listed in tools; got: {sorted(names)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
