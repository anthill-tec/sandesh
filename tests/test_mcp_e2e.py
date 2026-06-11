"""test_mcp_e2e.py — E2E characterization tests for the Sandesh MCP server.

CR-SAN-004 §S1 (T2) + §S2 (T3) + §S3 (AC6/AC7 gating).
CR-SAN-005: sandesh_actioned removed; tool count updated 10 → 9.

These tests exercise the 9 MCP tools through the real MCP protocol (in-memory
client and real subprocess over stdio). The tool-count assertions are RED
drivers until GREEN removes sandesh_actioned from the server.

Result-shape reference (confirmed via /tmp probes, mcp 1.27.x):
  - session.list_tools()    → ListToolsResult with .tools (list of Tool; each has .name)
  - session.call_tool(...)  → CallToolResult with:
      .isError          bool  (False on success, True on tool error)
      .content          list[TextContent]  (always present)
      .structuredContent dict{"result": <value>} on success, None on error
  - error path: isError=True, structuredContent=None, message in content[0].text

  python-crucible.py test --tests tests.test_mcp_e2e --agent CR-SAN-005-C0-RED
"""

import os
import shutil
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Optional MCP import guard (AC6/AC7) — tests skip gracefully when absent.
# ---------------------------------------------------------------------------
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.memory import create_connected_server_and_client_session
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

# Repo / venv paths computed from this file's location so the tests are
# runnable from any CWD (CI-safe).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_VENV_PYTHON = os.path.join(_REPO, ".venv", "bin", "python")

from sandesh import sandesh_db as sdb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sc(result):
    """Return structuredContent["result"] from a successful CallToolResult."""
    assert not result.isError, (
        f"Expected success but got error: {result.content[0].text if result.content else '?'}"
    )
    return result.structuredContent["result"]


# ---------------------------------------------------------------------------
# T2 — in-memory client ↔ server (AC1–AC3)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MCP, "mcp package not available")
class T2InMemoryClientServerTest(unittest.IsolatedAsyncioTestCase):
    """Drive the 9 tools through a real ClientSession backed by in-memory streams.

    CR-SAN-005: sandesh_actioned removed; tool count is now 9.
    """

    PROJ = "E2EMemory"
    MAINLINE = "Mainline - E2EMemory"
    TRACK1 = "Track 1 - E2EMemory"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-e2e-mem-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

    def tearDown(self):
        for k, v in (
            ("XDG_DATA_HOME", self._prev_xdg),
            ("SANDESH_PROJECT", self._prev_proj),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- AC1: list_tools contains the original 9 tools (CR-SAN-005: sandesh_actioned removed) ----

    async def test_ac1_list_tools_contains_the_original_nine_tools(self):
        """AC1 (CR-SAN-005) — session.list_tools() includes the original 9 tool names."""
        # Import here so collection still works when HAS_MCP is False at module level
        from sandesh import mcp_server  # noqa: F401

        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            list_result = await session.list_tools()
            names = {t.name for t in list_result.tools}
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
            # Exact-count contract lives in test_mcp_lifecycle_tools (CR-SAN-025 AC1).
            self.assertTrue(
                expected_names <= names,
                f"Missing original tools: {sorted(expected_names - names)}; got={sorted(names)}",
            )

    # -- AC2a: read tool (sandesh_addressbook) matches seeded library state --

    async def test_ac2_addressbook_matches_seeded_db_state(self):
        """AC2 — sandesh_addressbook via ClientSession returns the seeded addressbook."""
        from sandesh import mcp_server  # noqa: F401

        # Seed via the library directly (same XDG_DATA_HOME)
        store = sdb.setup(self.PROJ)
        con = sdb.connect()
        try:
            sdb.register(con, self.MAINLINE, kind="mainline", project=self.PROJ)
            sdb.register(con, self.TRACK1, kind="track", project=self.PROJ)
        finally:
            con.close()

        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            result = await session.call_tool(
                "sandesh_addressbook", {"project_id": self.PROJ}
            )
            self.assertFalse(result.isError, "sandesh_addressbook returned an error")
            self.assertIsNotNone(
                result.structuredContent, "structuredContent should not be None on success"
            )
            entries = _sc(result)
            self.assertIsInstance(entries, list)
            addresses = [e["address"] for e in entries]
            self.assertEqual(len(entries), 2, f"Expected 2 entries, got {len(entries)}: {addresses}")
            self.assertIn(self.MAINLINE, addresses)
            self.assertIn(self.TRACK1, addresses)
            # Verify the kind field round-trips
            mainline_entry = next(e for e in entries if e["address"] == self.MAINLINE)
            self.assertEqual(mainline_entry["kind"], "mainline")

    # -- AC2b: mutating tool (sandesh_send) creates a fetchable message ------

    async def test_ac2_send_then_fetch_round_trip(self):
        """AC2 — sandesh_send via ClientSession; the message is fetchable by the recipient."""
        from sandesh import mcp_server  # noqa: F401

        # Seed addressbook via library
        store = sdb.setup(self.PROJ)
        con = sdb.connect()
        try:
            sdb.register(con, self.MAINLINE, kind="mainline", project=self.PROJ)
            sdb.register(con, self.TRACK1, kind="track", project=self.PROJ)
        finally:
            con.close()

        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            # Send via ClientSession (mutating tool)
            send_result = await session.call_tool("sandesh_send", {
                "project_id": self.PROJ,
                "from_addr": self.TRACK1,
                "to": [self.MAINLINE],
                "subject": "E2E in-memory subject",
                "body_text": "E2E in-memory body",
            })
            self.assertFalse(send_result.isError, f"send returned error: {send_result.content}")
            self.assertIsNotNone(send_result.structuredContent)
            msg_id = _sc(send_result)
            self.assertIsInstance(msg_id, int)
            self.assertGreater(msg_id, 0, "message id must be positive")

            # Fetch via ClientSession (read tool)
            fetch_result = await session.call_tool("sandesh_fetch", {
                "project_id": self.PROJ,
                "recipient": self.MAINLINE,
            })
            self.assertFalse(fetch_result.isError, f"fetch returned error: {fetch_result.content}")
            messages = _sc(fetch_result)
            self.assertIsInstance(messages, list)
            self.assertEqual(len(messages), 1, f"Expected exactly 1 message, got {len(messages)}")
            msg = messages[0]
            self.assertEqual(msg["id"], msg_id)
            self.assertEqual(msg["subject"], "E2E in-memory subject")
            self.assertEqual(msg["body"], "E2E in-memory body")
            self.assertEqual(msg["from"], self.TRACK1)
            self.assertEqual(msg["role"], "to")

    # -- AC3: error path — malformed address returns isError result ----------

    async def test_ac3_register_malformed_address_returns_error_result(self):
        """AC3 — sandesh_register with a malformed address: client receives isError=True."""
        from sandesh import mcp_server  # noqa: F401

        # Ensure the project store exists so the error is about the address, not setup
        sdb.setup(self.PROJ)

        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            result = await session.call_tool("sandesh_register", {
                "project_id": self.PROJ,
                "addr": "bad-address-format",
            })
            # MUST be an error result (not a raised exception over the protocol)
            self.assertTrue(
                result.isError,
                "Expected isError=True for a malformed address but got success",
            )
            # structuredContent is None on errors
            self.assertIsNone(
                result.structuredContent,
                "structuredContent should be None when isError=True",
            )
            # The error message must reference the expected format
            self.assertTrue(result.content, "Error result must have content")
            error_text = result.content[0].text
            self.assertIn(
                "expected '<Orchestrator> - <Project>'",
                error_text,
                f"Error message does not reference expected format: {error_text!r}",
            )

    # -- AC3 (bound): cc recipient stays unread while to recipient reads -----

    async def test_ac2_cc_recipient_stays_unread_after_to_reads(self):
        """AC2 bound — after the 'to' recipient fetches, cc recipient is still unread."""
        from sandesh import mcp_server  # noqa: F401

        # Three-address scenario: sender -> to + cc
        SENDER = "Track 1 - E2EMemory"
        TO_ADDR = "Mainline - E2EMemory"
        CC_ADDR = "Track 2 - E2EMemory"

        store = sdb.setup(self.PROJ)
        con = sdb.connect()
        try:
            sdb.register(con, TO_ADDR, kind="mainline", project=self.PROJ)
            sdb.register(con, SENDER, kind="track", project=self.PROJ)
            sdb.register(con, CC_ADDR, kind="track", project=self.PROJ)
        finally:
            con.close()

        async with create_connected_server_and_client_session(mcp_server.mcp) as session:
            # Send with to + cc
            send_result = await session.call_tool("sandesh_send", {
                "project_id": self.PROJ,
                "from_addr": SENDER,
                "to": [TO_ADDR],
                "cc": [CC_ADDR],
                "subject": "cc-unread test",
            })
            self.assertFalse(send_result.isError)
            msg_id = _sc(send_result)

            # TO recipient fetches (marks as read)
            fetch_to = await session.call_tool("sandesh_fetch", {
                "project_id": self.PROJ,
                "recipient": TO_ADDR,
            })
            self.assertFalse(fetch_to.isError)
            to_msgs = _sc(fetch_to)
            self.assertEqual(len(to_msgs), 1)
            self.assertEqual(to_msgs[0]["id"], msg_id)

            # CC recipient inbox should still show the message unread
            inbox_cc = await session.call_tool("sandesh_inbox", {
                "project_id": self.PROJ,
                "recipient": CC_ADDR,
                "unread_only": True,
            })
            self.assertFalse(inbox_cc.isError)
            cc_unread = _sc(inbox_cc)
            self.assertEqual(
                len(cc_unread), 1,
                f"CC recipient should still have 1 unread message but got {len(cc_unread)}",
            )
            self.assertEqual(cc_unread[0]["id"], msg_id)


# ---------------------------------------------------------------------------
# T3 — real subprocess over stdio (AC4–AC5)
# ---------------------------------------------------------------------------

_STDIO_SKIP_REASON = (
    "mcp package or repo venv python not available"
    if not HAS_MCP
    else (
        f"venv python not found at {_VENV_PYTHON}"
        if not os.path.exists(_VENV_PYTHON)
        else None
    )
)


@unittest.skipUnless(
    HAS_MCP and os.path.exists(_VENV_PYTHON),
    _STDIO_SKIP_REASON or "mcp/venv not available",
)
class T3SubprocessStdioTest(unittest.IsolatedAsyncioTestCase):
    """Drive a full round trip over real subprocess stdio transport (AC4–AC5)."""

    PROJ = "StdioE2E"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-e2e-stdio-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- AC4: full round-trip over real stdio --------------------------------

    async def test_ac4_full_round_trip_over_stdio(self):
        """AC4 — setup→register→send→fetch over a real subprocess; body round-trips."""
        params = StdioServerParameters(
            command=_VENV_PYTHON,
            args=["-m", "sandesh.mcp_server"],
            env={**os.environ, "XDG_DATA_HOME": self.tmp},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # sandesh_setup provisions the project store
                setup_result = await session.call_tool(
                    "sandesh_setup", {"project_id": self.PROJ}
                )
                self.assertFalse(
                    setup_result.isError,
                    f"sandesh_setup error: {setup_result.content}",
                )
                store_path = _sc(setup_result)
                self.assertIsInstance(store_path, str)
                self.assertIn(self.PROJ, store_path)

                # Register sender and recipient
                sender = f"Track 1 - {self.PROJ}"
                recipient = f"Mainline - {self.PROJ}"

                reg_sender = await session.call_tool("sandesh_register", {
                    "project_id": self.PROJ,
                    "addr": sender,
                    "kind": "track",
                })
                self.assertFalse(reg_sender.isError, f"register sender error: {reg_sender.content}")
                self.assertEqual(_sc(reg_sender), sender)

                reg_recipient = await session.call_tool("sandesh_register", {
                    "project_id": self.PROJ,
                    "addr": recipient,
                    "kind": "mainline",
                })
                self.assertFalse(reg_recipient.isError, f"register recipient error: {reg_recipient.content}")
                self.assertEqual(_sc(reg_recipient), recipient)

                # Send a message with body_text
                SUBJECT = "stdio E2E subject"
                BODY = "stdio E2E body text"

                send_result = await session.call_tool("sandesh_send", {
                    "project_id": self.PROJ,
                    "from_addr": sender,
                    "to": [recipient],
                    "subject": SUBJECT,
                    "body_text": BODY,
                })
                self.assertFalse(send_result.isError, f"send error: {send_result.content}")
                msg_id = _sc(send_result)
                self.assertIsInstance(msg_id, int)
                self.assertGreater(msg_id, 0)

                # Fetch and verify subject + body round-trip
                fetch_result = await session.call_tool("sandesh_fetch", {
                    "project_id": self.PROJ,
                    "recipient": recipient,
                })
                self.assertFalse(fetch_result.isError, f"fetch error: {fetch_result.content}")
                messages = _sc(fetch_result)
                self.assertIsInstance(messages, list)
                self.assertEqual(
                    len(messages), 1,
                    f"Expected exactly 1 fetched message, got {len(messages)}",
                )
                msg = messages[0]
                self.assertEqual(msg["id"], msg_id)
                self.assertEqual(
                    msg["subject"], SUBJECT,
                    f"Subject mismatch: got {msg['subject']!r}",
                )
                self.assertEqual(
                    msg["body"], BODY,
                    f"Body mismatch: got {msg['body']!r}",
                )
                self.assertEqual(msg["from"], sender)

    # -- AC5: list_tools over stdio contains the original 9 tools (CR-SAN-005) ------------

    async def test_ac5_list_tools_over_stdio_contains_the_original_nine_tools(self):
        """AC5 (CR-SAN-005) — list_tools over subprocess stdio includes the original
        9 tool names."""
        params = StdioServerParameters(
            command=_VENV_PYTHON,
            args=["-m", "sandesh.mcp_server"],
            env={**os.environ, "XDG_DATA_HOME": self.tmp},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                list_result = await session.list_tools()
                names = {t.name for t in list_result.tools}
                expected = {
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
                # Exact-count contract lives in test_mcp_lifecycle_tools (CR-SAN-025 AC1).
                self.assertTrue(
                    expected <= names,
                    f"Missing original tools: {sorted(expected - names)}; got={sorted(names)}",
                )

    # -- AC5 extended: error path over stdio ---------------------------------

    async def test_ac5_error_path_over_stdio(self):
        """AC5 — a tool error (malformed address) is returned as isError=True over stdio."""
        params = StdioServerParameters(
            command=_VENV_PYTHON,
            args=["-m", "sandesh.mcp_server"],
            env={**os.environ, "XDG_DATA_HOME": self.tmp},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Setup the project first (needed for register to reach address validation)
                await session.call_tool("sandesh_setup", {"project_id": self.PROJ})

                # Attempt to register a malformed address
                err_result = await session.call_tool("sandesh_register", {
                    "project_id": self.PROJ,
                    "addr": "not-a-valid-address",
                })
                self.assertTrue(
                    err_result.isError,
                    "Expected isError=True for malformed address over stdio",
                )
                self.assertIsNone(err_result.structuredContent)
                self.assertTrue(err_result.content)
                error_text = err_result.content[0].text
                self.assertIn(
                    "expected '<Orchestrator> - <Project>'",
                    error_text,
                    f"Error message missing expected format hint: {error_text!r}",
                )


# ---------------------------------------------------------------------------
# T4 — stdio E2E: cross-project send + archive/unarchive scenario (AC7)
# ---------------------------------------------------------------------------
#
# Fixture (library, pre-spawn): setup P1+P2; register Mainline - P1 and
# Mainline - P2; assign_admin('ops'); grant_xproj P2 and P1 (so the reply
# path works too).
#
# Scenario over stdio (NO $SANDESH_PROJECT in server env — derivation only):
#   1. sandesh_send from Mainline - P2 to [Mainline - P1] WITHOUT project_id
#      → success (project derived from from_addr).
#   2. sandesh_fetch recipient Mainline - P1 WITHOUT project_id
#      → the message arrives with correct subject and body.
#   3. sandesh_archive(project_id='P2', by='Mainline - P2') → success.
#   4. sandesh_send from Mainline - P2 again → error result with 'archived'.
#   5. sandesh_unarchive(project_id='P2', by='Mainline - P2') → success.
#   6. sandesh_send from Mainline - P2 → success again (round-trip closed).
#   7. tools/list contains sandesh_archive + sandesh_unarchive; NONE of
#      tombstone/grant/revoke/admin (AC1 stdio leg).


@unittest.skipUnless(
    HAS_MCP and os.path.exists(_VENV_PYTHON),
    _STDIO_SKIP_REASON or "mcp/venv not available",
)
class T4SubprocessStdioArchiveTest(unittest.IsolatedAsyncioTestCase):
    """CR-SAN-025 AC7 — stdio E2E: cross-project send + archive/unarchive scenario.

    Fixture is set up in-process (library calls against the temp XDG store)
    before the subprocess is spawned. The subprocess env has XDG_DATA_HOME
    set to the same temp dir and NO $SANDESH_PROJECT, so all project_id
    derivation is exercised over the wire.
    """

    P1 = "XP1"
    P2 = "XP2"
    MAINLINE_P1 = "Mainline - XP1"
    MAINLINE_P2 = "Mainline - XP2"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-e2e-archive-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        # Provision both projects via the library (same XDG_DATA_HOME as the subprocess).
        sdb.setup(self.P1)
        sdb.setup(self.P2)

        con = sdb.connect()
        try:
            sdb.register(con, self.MAINLINE_P1, kind="mainline", project=self.P1)
            sdb.register(con, self.MAINLINE_P2, kind="mainline", project=self.P2)
            # Assign admin and grant cross-project access on both sides.
            sdb.assign_admin(con, "ops")
            sdb.grant_xproj(con, self.P2, "ops")
            sdb.grant_xproj(con, self.P1, "ops")
        finally:
            con.close()

    def tearDown(self):
        for k, v in (
            ("XDG_DATA_HOME", self._prev_xdg),
            ("SANDESH_PROJECT", self._prev_proj),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spawn_params(self):
        """StdioServerParameters for the sandesh-mcp subprocess.
        XDG_DATA_HOME is set; SANDESH_PROJECT is intentionally absent."""
        env = {k: v for k, v in os.environ.items() if k != "SANDESH_PROJECT"}
        env["XDG_DATA_HOME"] = self.tmp
        return StdioServerParameters(
            command=_VENV_PYTHON,
            args=["-m", "sandesh.mcp_server"],
            env=env,
        )

    # -- AC7 step 1–2: cross-project send → fetch without project_id -----------

    async def test_ac7_cross_project_send_without_project_id_succeeds(self):
        """AC7 step 1: sandesh_send from Mainline - P2 to [Mainline - P1] without
        project_id succeeds (project derived from from_addr)."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool("sandesh_send", {
                    "from_addr": self.MAINLINE_P2,
                    "to": [self.MAINLINE_P1],
                    "subject": "cross-project AC7 ping",
                    "body_text": "body from P2 to P1",
                })
                self.assertFalse(
                    result.isError,
                    f"sandesh_send without project_id must succeed (derivation from "
                    f"from_addr); got error: {result.content[0].text if result.content else '?'}",
                )
                msg_id = result.structuredContent["result"]
                self.assertIsInstance(msg_id, int)
                self.assertGreater(msg_id, 0, "message id must be positive")

    async def test_ac7_cross_project_fetch_without_project_id_delivers_message(self):
        """AC7 step 2: sandesh_fetch for Mainline - P1 without project_id returns
        the cross-project message with correct subject and body."""
        # First send via the library (fast, avoids a second subprocess spawn).
        con = sdb.connect()
        try:
            store_p2 = sdb.store_dir(self.P2)
            msg_id = sdb.send(
                con, store_p2, self.MAINLINE_P2,
                to=[self.MAINLINE_P1],
                subject="cross-project AC7 fetch-test",
                body_text="body for fetch verification",
            )
        finally:
            con.close()

        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool("sandesh_fetch", {
                    "recipient": self.MAINLINE_P1,
                    # no project_id — derived from recipient address
                })
                self.assertFalse(
                    result.isError,
                    f"sandesh_fetch without project_id must succeed; error: "
                    f"{result.content[0].text if result.content else '?'}",
                )
                messages = result.structuredContent["result"]
                self.assertIsInstance(messages, list)
                self.assertEqual(
                    len(messages), 1,
                    f"Expected exactly 1 message for Mainline - P1, got {len(messages)}",
                )
                msg = messages[0]
                self.assertEqual(msg["subject"], "cross-project AC7 fetch-test")
                self.assertEqual(msg["body"], "body for fetch verification")
                self.assertEqual(msg["from"], self.MAINLINE_P2)

    # -- AC7 step 3–6: archive / send-guard / unarchive round-trip over stdio --

    async def test_ac7_archive_unarchive_round_trip_over_stdio(self):
        """AC7 steps 3–6 combined: archive P2 via MCP → further send fails with
        'archived' → unarchive restores sends.  Full scenario in one session to
        keep the subprocess alive across steps."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Step 3: archive P2
                arch_result = await session.call_tool("sandesh_archive", {
                    "project_id": self.P2,
                    "by": self.MAINLINE_P2,
                })
                self.assertFalse(
                    arch_result.isError,
                    f"sandesh_archive must succeed; got: "
                    f"{arch_result.content[0].text if arch_result.content else '?'}",
                )
                arch_text = arch_result.structuredContent["result"]
                self.assertIn(
                    "archived", arch_text,
                    f"archive confirmation must mention 'archived'; got {arch_text!r}",
                )

                # Step 4: send from P2 must now fail with 'archived' in the error
                send_blocked = await session.call_tool("sandesh_send", {
                    "from_addr": self.MAINLINE_P2,
                    "to": [self.MAINLINE_P1],
                    "subject": "should be blocked",
                })
                self.assertTrue(
                    send_blocked.isError,
                    "sandesh_send from archived P2 must return an error result "
                    "(isError=True), not succeed",
                )
                self.assertIsNone(
                    send_blocked.structuredContent,
                    "structuredContent must be None on error",
                )
                blocked_text = send_blocked.content[0].text
                self.assertIn(
                    "archived", blocked_text,
                    f"Error from send to archived project must mention 'archived'; "
                    f"got: {blocked_text!r}",
                )

                # Step 5: unarchive P2
                unarch_result = await session.call_tool("sandesh_unarchive", {
                    "project_id": self.P2,
                    "by": self.MAINLINE_P2,
                })
                self.assertFalse(
                    unarch_result.isError,
                    f"sandesh_unarchive must succeed; got: "
                    f"{unarch_result.content[0].text if unarch_result.content else '?'}",
                )
                unarch_text = unarch_result.structuredContent["result"]
                self.assertIn(
                    "active", unarch_text,
                    f"unarchive confirmation must mention 'active'; got {unarch_text!r}",
                )

                # Step 6: send from P2 succeeds again
                send_again = await session.call_tool("sandesh_send", {
                    "from_addr": self.MAINLINE_P2,
                    "to": [self.MAINLINE_P1],
                    "subject": "send after unarchive",
                })
                self.assertFalse(
                    send_again.isError,
                    f"sandesh_send must succeed after unarchive; got error: "
                    f"{send_again.content[0].text if send_again.content else '?'}",
                )
                restored_id = send_again.structuredContent["result"]
                self.assertIsInstance(restored_id, int)
                self.assertGreater(restored_id, 0)

    # -- AC7 / AC1 stdio leg: tools/list contains archive+unarchive; no forbidden --

    async def test_ac7_ac1_tools_list_contains_archive_and_unarchive(self):
        """AC7 / AC1 (stdio leg): tools/list contains sandesh_archive and
        sandesh_unarchive; no name contains tombstone, grant, revoke, or admin."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                list_result = await session.list_tools()
                names = {t.name for t in list_result.tools}

                # Must contain the two new lifecycle tools
                self.assertIn(
                    "sandesh_archive", names,
                    f"sandesh_archive missing from stdio tools/list; got {sorted(names)}",
                )
                self.assertIn(
                    "sandesh_unarchive", names,
                    f"sandesh_unarchive missing from stdio tools/list; got {sorted(names)}",
                )

                # Must NOT contain forbidden admin/destructive tools
                forbidden_keywords = ("tombstone", "grant", "revoke", "admin")
                for keyword in forbidden_keywords:
                    matching = [n for n in names if keyword in n]
                    self.assertEqual(
                        matching, [],
                        f"No tool name may contain '{keyword}' (CLI-only ops — D9/D11); "
                        f"found: {matching}",
                    )


if __name__ == "__main__":
    unittest.main()
