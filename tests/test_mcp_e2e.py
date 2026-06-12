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


# T5 — stdio E2E: search/filter scenario (CR-SAN-028 AC6)
#
# Fixture (library, pre-spawn):
#   Two projects P1="SearchE2EP1", P2="SearchE2EP2".
#   Register Mainline - SearchE2EP1, Track 1 - SearchE2EP1, Mainline - SearchE2EP2.
#   assign_admin("ops"); grant_xproj on both projects so P2 can send cross-project.
#   Seed corpus:
#     3 messages from P1 (T1 → Mainline-P1) each body contains unique term "tubdeploy"
#       so pagination with limit=2 gives page-1 (2 hits) + page-2 (1 hit), total=3.
#     1 cross-project message from Mainline-P2 → Mainline-P1, body contains "p2xmigrate"
#       (used for sender_project filter assertion).
#   All mail is addressed to Mainline-P1 — T5 session exercises only Mainline-P1.
#
# Scenario over stdio (NO $SANDESH_PROJECT — derivation only):
#   1. sandesh_inbox(recipient=ML_P1, sender_project=P2) returns exactly the P2 row;
#      the unfiltered call returns more (superset).
#   2. sandesh_search for "tubdeploy" with limit=2 offset=0 → total=3, 2 hits, page
#      has expected ids; page 2 (offset=2) → total=3, 1 hit — consistent totals.
#   3. A malformed FTS5 query comes back as an MCP error result (isError=True), not a
#      transport crash; error text mentions the query or 'invalid'.
#
# Result-shape note: sandesh_search returns a plain dict (not int/list).
#   Over stdio the MCP SDK delivers it in content[0].text as a JSON string
#   (structuredContent stays None for dict-typed returns in this SDK version).
#   _sc_search() below parses that JSON string.  All other tools (inbox, send, …)
#   return int or list and are accessible via .structuredContent["result"] as usual.


def _sc_search(result):
    """Extract the search-result dict from a successful sandesh_search CallToolResult.

    sandesh_search returns a plain dict.  Over the stdio transport the MCP SDK
    (mcp 1.27.x) delivers dict-typed tool returns in content[0].text as a JSON
    string; structuredContent is None.  This helper parses that JSON.

    Fails the calling test with a descriptive message if the result is an error
    or the payload cannot be parsed.
    """
    import json as _json
    if result.isError:
        raise AssertionError(
            f"Expected search success but got isError=True; "
            f"error: {result.content[0].text if result.content else '?'}"
        )
    # structuredContent carries the dict on some SDK versions; content[0].text on others.
    if result.structuredContent is not None:
        sc = result.structuredContent
        return sc["result"] if isinstance(sc, dict) and "result" in sc else sc
    if result.content:
        text = getattr(result.content[0], "text", None)
        if text:
            return _json.loads(text)
    raise AssertionError(
        f"Cannot extract search dict from result: "
        f"structuredContent={result.structuredContent!r}, content={result.content!r}"
    )


@unittest.skipUnless(
    HAS_MCP and os.path.exists(_VENV_PYTHON),
    _STDIO_SKIP_REASON or "mcp/venv not available",
)
class T5SubprocessStdioSearchFilterTest(unittest.IsolatedAsyncioTestCase):
    """CR-SAN-028 AC6 — stdio E2E: sandesh_inbox filter by sender_project +
    sandesh_search with pagination + malformed query → isError=True.

    Fixture is seeded in-process before the subprocess spawns so the same
    XDG_DATA_HOME is used by both. The subprocess env has no $SANDESH_PROJECT.
    """

    P1 = "SearchE2EP1"
    P2 = "SearchE2EP2"
    ML_P1 = "Mainline - SearchE2EP1"
    T1_P1 = "Track 1 - SearchE2EP1"
    ML_P2 = "Mainline - SearchE2EP2"

    # Unique corpus terms — no collisions with any other test in the file.
    TERM_P1 = "tubdeploy"       # appears in all 3 P1 messages → total=3 for pagination
    TERM_P2 = "p2xmigrate"      # appears in the P2 cross-project message only

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-e2e-search-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        # Provision both projects.
        sdb.setup(self.P1)
        sdb.setup(self.P2)

        self.store1 = sdb.store_dir(self.P1)
        self.store2 = sdb.store_dir(self.P2)

        con = sdb.connect()
        try:
            sdb.register(con, self.ML_P1, kind="mainline", project=self.P1)
            sdb.register(con, self.T1_P1, kind="track",    project=self.P1)
            sdb.register(con, self.ML_P2, kind="mainline", project=self.P2)

            # Admin + cross-project grant so P2 can send to P1.
            sdb.assign_admin(con, "ops")
            sdb.grant_xproj(con, self.P1, "ops")
            sdb.grant_xproj(con, self.P2, "ops")

            # Seed 3 P1-internal messages sharing TERM_P1 (for pagination: limit=2 → 2+1).
            self.p1_ids = []
            for i in range(3):
                mid = sdb.send(
                    con, self.store1,
                    from_addr=self.T1_P1,
                    to=[self.ML_P1],
                    subject=f"p1 deploy item {i}",
                    body_text=f"body {self.TERM_P1} item {i}",
                )
                self.p1_ids.append(mid)

            # Seed 1 cross-project message from P2 to ML_P1 with TERM_P2.
            self.p2_id = sdb.send(
                con, self.store2,
                from_addr=self.ML_P2,
                to=[self.ML_P1],
                subject="p2 migration handoff",
                body_text=f"cross-project {self.TERM_P2} complete",
            )
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
        """StdioServerParameters — XDG_DATA_HOME set, no SANDESH_PROJECT."""
        env = {k: v for k, v in os.environ.items() if k != "SANDESH_PROJECT"}
        env["XDG_DATA_HOME"] = self.tmp
        return StdioServerParameters(
            command=_VENV_PYTHON,
            args=["-m", "sandesh.mcp_server"],
            env=env,
        )

    # ── scenario step 1: sandesh_inbox filtered by sender_project ────────────

    async def test_ac6_inbox_filtered_by_sender_project_returns_only_p2_rows(self):
        """AC6 step 1a: sandesh_inbox(recipient=ML_P1, sender_project=P2) over stdio
        returns exactly the one P2-sender row (p2_id) and excludes all P1 rows."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool("sandesh_inbox", {
                    "recipient": self.ML_P1,
                    "unread_only": False,
                    "sender_project": self.P2,
                })
                self.assertFalse(
                    result.isError,
                    f"sandesh_inbox(sender_project={self.P2!r}) must not error; "
                    f"got: {result.content[0].text if result.content else '?'}",
                )
                rows = result.structuredContent["result"]
                self.assertIsInstance(rows, list)
                ids = [r["id"] for r in rows]

                # Must contain the P2 cross-project message.
                self.assertIn(
                    self.p2_id, ids,
                    f"p2_id={self.p2_id} must appear with sender_project={self.P2!r}; "
                    f"got ids={ids!r}",
                )
                # Must NOT contain any of the P1 messages.
                for p1_mid in self.p1_ids:
                    self.assertNotIn(
                        p1_mid, ids,
                        f"P1 message {p1_mid} must NOT appear with sender_project={self.P2!r}; "
                        f"got ids={ids!r}",
                    )
                # Exactly 1 row — only p2_id is a P2-sender message to ML_P1.
                self.assertEqual(
                    len(ids), 1,
                    f"Exactly 1 row expected for sender_project={self.P2!r}; "
                    f"got {len(ids)}: {ids!r}",
                )

    async def test_ac6_inbox_unfiltered_is_superset_of_filtered(self):
        """AC6 step 1b: unfiltered sandesh_inbox(recipient=ML_P1) returns a STRICT
        superset of the sender_project-filtered call — it includes both P1 and P2 rows."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                unfiltered_result = await session.call_tool("sandesh_inbox", {
                    "recipient": self.ML_P1,
                    "unread_only": False,
                })
                self.assertFalse(unfiltered_result.isError)
                filtered_result = await session.call_tool("sandesh_inbox", {
                    "recipient": self.ML_P1,
                    "unread_only": False,
                    "sender_project": self.P2,
                })
                self.assertFalse(filtered_result.isError)

                unfiltered_ids = {r["id"] for r in unfiltered_result.structuredContent["result"]}
                filtered_ids   = {r["id"] for r in filtered_result.structuredContent["result"]}

                # Filtered must be a strict subset — not equal.
                self.assertLess(
                    filtered_ids, unfiltered_ids,
                    f"Filtered set must be a STRICT subset of unfiltered set; "
                    f"filtered={sorted(filtered_ids)!r}, "
                    f"unfiltered={sorted(unfiltered_ids)!r}",
                )
                # p2_id in both; all p1_ids only in unfiltered.
                self.assertIn(
                    self.p2_id, unfiltered_ids,
                    f"p2_id must be in unfiltered inbox",
                )
                for p1_mid in self.p1_ids:
                    self.assertIn(
                        p1_mid, unfiltered_ids,
                        f"P1 message {p1_mid} must be in unfiltered inbox",
                    )
                    self.assertNotIn(
                        p1_mid, filtered_ids,
                        f"P1 message {p1_mid} must NOT be in sender_project={self.P2!r} filtered inbox",
                    )

    # ── scenario step 2: sandesh_search + pagination ─────────────────────────

    async def test_ac6_search_returns_hit_with_envelope_and_snippet(self):
        """AC6 step 2a: sandesh_search for TERM_P2 returns p2_id with envelope keys
        and a snippet containing the search term."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool("sandesh_search", {
                    "recipient": self.ML_P1,
                    "query": self.TERM_P2,
                })
                # _sc_search() parses content[0].text JSON (dict return over stdio).
                data = _sc_search(result)
                self.assertIsInstance(data, dict)

                # Required envelope keys on every hit.
                self.assertIn("hits",   data)
                self.assertIn("total",  data)
                self.assertIn("limit",  data)
                self.assertIn("offset", data)

                # Exactly 1 hit — TERM_P2 is unique to p2_id.
                self.assertEqual(
                    data["total"], 1,
                    f"TERM_P2={self.TERM_P2!r} is unique to p2_id; "
                    f"expected total=1, got {data['total']!r}",
                )
                self.assertEqual(
                    len(data["hits"]), 1,
                    f"Exactly 1 hit expected; got {len(data['hits'])!r}",
                )
                hit = data["hits"][0]
                self.assertEqual(
                    hit["id"], self.p2_id,
                    f"hit id must be p2_id={self.p2_id}; got {hit['id']!r}",
                )
                # Required envelope fields.
                for key in ("id", "from", "subject", "kind", "created_at", "role", "snippet"):
                    self.assertIn(
                        key, hit,
                        f"hit must contain envelope key {key!r}; got {sorted(hit.keys())!r}",
                    )
                # Snippet must be non-empty and contain the search term.
                snippet = hit["snippet"]
                self.assertIsInstance(snippet, str)
                self.assertGreater(len(snippet.strip()), 0, "snippet must not be empty")
                self.assertIn(
                    self.TERM_P2.lower(), snippet.lower(),
                    f"snippet must contain the search term {self.TERM_P2!r}; got {snippet!r}",
                )

    async def test_ac6_search_pagination_page1_and_page2(self):
        """AC6 step 2b+2c: sandesh_search for TERM_P1 (3 total hits) with limit=2:
        page 1 (offset=0) → 2 hits, total=3;
        page 2 (offset=2) → 1 hit, total=3;
        pages don't overlap and together cover all 3 p1 corpus ids."""
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Page 1 — _sc_search() parses content[0].text JSON (dict over stdio).
                r1 = await session.call_tool("sandesh_search", {
                    "recipient": self.ML_P1,
                    "query": self.TERM_P1,
                    "limit": 2,
                    "offset": 0,
                })
                d1 = _sc_search(r1)

                self.assertEqual(
                    d1["total"], 3,
                    f"TERM_P1 has 3 corpus messages; total must be 3 on page 1; "
                    f"got {d1['total']!r}",
                )
                self.assertEqual(
                    len(d1["hits"]), 2,
                    f"limit=2 → page 1 must have 2 hits; got {len(d1['hits'])!r}",
                )
                self.assertEqual(d1["limit"], 2)
                self.assertEqual(d1["offset"], 0)

                # Page 2.
                r2 = await session.call_tool("sandesh_search", {
                    "recipient": self.ML_P1,
                    "query": self.TERM_P1,
                    "limit": 2,
                    "offset": 2,
                })
                d2 = _sc_search(r2)

                self.assertEqual(
                    d2["total"], 3,
                    f"total must still be 3 on page 2; got {d2['total']!r}",
                )
                self.assertEqual(
                    len(d2["hits"]), 1,
                    f"limit=2, offset=2 → page 2 must have 1 hit; got {len(d2['hits'])!r}",
                )
                self.assertEqual(d2["offset"], 2)

                # Pages are disjoint and together cover all 3 p1 corpus ids.
                ids1 = {h["id"] for h in d1["hits"]}
                ids2 = {h["id"] for h in d2["hits"]}
                self.assertEqual(
                    ids1 & ids2, set(),
                    f"pages must not overlap; overlap={ids1 & ids2!r}",
                )
                self.assertEqual(
                    ids1 | ids2, set(self.p1_ids),
                    f"page1+page2 must cover all 3 p1 ids={set(self.p1_ids)!r}; "
                    f"combined={ids1 | ids2!r}",
                )

    # ── scenario step 3: malformed FTS5 query → MCP error result ─────────────

    async def test_ac6_malformed_fts5_query_returns_error_result_not_crash(self):
        """AC6 step 3: a malformed FTS5 query (unbalanced quote) comes back as an MCP
        error result (isError=True) over the real stdio subprocess — not a transport
        crash, not an unhandled exception.

        Spec: 'a malformed query surfaces as an MCP error result (isError=True on the
        CallToolResult, message mentioning the invalid query)' — §S4 / AC6.
        """
        async with stdio_client(self._spawn_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool("sandesh_search", {
                    "recipient": self.ML_P1,
                    "query": '"unterminated',   # unbalanced quote → malformed FTS5
                })
                # Must be an error result — NOT a raised exception (transport crash).
                self.assertTrue(
                    result.isError,
                    "A malformed FTS5 query must produce isError=True on the "
                    "CallToolResult over stdio (not a transport crash); "
                    "got isError=False (success)",
                )
                # structuredContent must be None on an error result.
                self.assertIsNone(
                    result.structuredContent,
                    "structuredContent must be None when isError=True; "
                    f"got {result.structuredContent!r}",
                )
                # Error text must be present and mention the query problem.
                self.assertTrue(
                    result.content,
                    "Error result must have at least one content item",
                )
                err_text = result.content[0].text
                self.assertIsInstance(err_text, str)
                err_lower = err_text.lower()
                problem_terms = ("invalid", "query", "fts", "unterminated")
                self.assertTrue(
                    any(term in err_lower for term in problem_terms),
                    f"Error message must mention the query problem "
                    f"(one of {problem_terms}); got: {err_text!r}",
                )


if __name__ == "__main__":
    unittest.main()
