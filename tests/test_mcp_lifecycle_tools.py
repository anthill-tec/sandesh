"""test_mcp_lifecycle_tools.py — MCP lifecycle tool tests (CR-SAN-025 §S1, AC1–AC4).

Tests drive `sandesh_archive` and `sandesh_unarchive` via the in-process FastMCP
harness (same pattern as test_mcp_mutating_tools.py) and verify side-effects by
reading back from sandesh_db directly (fresh connections).

AC1 — tool inventory: exactly 11 tools, no tombstone/grant/revoke/admin names,
       archive annotations are destructiveHint=False + idempotentHint=False.
AC2 — archive/unarchive round-trip via MCP: state transitions + send guard.
AC3 — required project_id: missing project_id is a ToolError (pydantic validation)
       even when $SANDESH_PROJECT is set — the env fallback must NOT apply.
AC4 — authz mapped: non-Mainline archive → ToolError; already-archived → ToolError.
+ RuntimeError mapping: live notifier + force=False → ToolError; force=True → succeeds.

  python-crucible.py test --tests tests.test_mcp_lifecycle_tools --agent red-cr025-c1
"""

import os
import shutil
import tempfile
import unittest
import uuid

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp.exceptions import ToolError

# Two projects: P1 is granted cross-project access; P2 is the archive subject.
PROJ1 = "P1"
PROJ2 = "P2"
MAINLINE_P1 = "Mainline - P1"
TRACK1_P1 = "Track 1 - P1"
MAINLINE_P2 = "Mainline - P2"
TRACK1_P2 = "Track 1 - P2"


def _scalar(result):
    """Unwrap FastMCP.call_tool's converted return for scalar (int/str/None) tools.
    Mirrors the helper in test_mcp_mutating_tools.py."""
    import json
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


class McpLifecycleToolsTest(unittest.IsolatedAsyncioTestCase):
    """MCP sandesh_archive / sandesh_unarchive tool tests (CR-SAN-025 §S1, AC1–AC4)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-lifecycle-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)  # tests opt-in explicitly

        # Provision both project stores.
        sdb.setup(PROJ1)
        sdb.setup(PROJ2)

        # Register participants in both projects (library calls — no MCP surface).
        con = sdb.connect()
        try:
            sdb.register(con, MAINLINE_P1, kind="mainline", project=PROJ1)
            sdb.register(con, TRACK1_P1, kind="track", project=PROJ1)
            sdb.register(con, MAINLINE_P2, kind="mainline", project=PROJ2)
            sdb.register(con, TRACK1_P2, kind="track", project=PROJ2)
            # Assign admin and grant P2 (no MCP/CLI exposure — library-only).
            sdb.assign_admin(con, "ops")
            sdb.grant_xproj(con, PROJ2, "ops")
        finally:
            con.close()

        self.store1 = sdb.store_dir(PROJ1)
        self.store2 = sdb.store_dir(PROJ2)

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

    def _tool_by_name(self, tools, name):
        for t in tools:
            if t.name == name:
                return t
        self.fail(f"Tool '{name}' not found in list_tools()")

    # -------------------------------------------------------------------------
    # AC1 — tool inventory: exactly 11 tools; no forbidden names; annotations
    # -------------------------------------------------------------------------

    async def test_ac1_list_tools_returns_exactly_eleven_tools(self):
        """AC1: list_tools() returns exactly 11 tools (existing 9 + sandesh_archive
        + sandesh_unarchive)."""
        tools = await mcp_server.mcp.list_tools()
        names = sorted(t.name for t in tools)
        self.assertEqual(
            len(tools), 11,
            f"Expected exactly 11 tools, got {len(tools)}: {names}",
        )

    async def test_ac1_sandesh_archive_present_in_tool_list(self):
        """AC1: sandesh_archive must appear in list_tools()."""
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        self.assertIn(
            "sandesh_archive", names,
            f"sandesh_archive missing from list_tools(): {sorted(names)}",
        )

    async def test_ac1_sandesh_unarchive_present_in_tool_list(self):
        """AC1: sandesh_unarchive must appear in list_tools()."""
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        self.assertIn(
            "sandesh_unarchive", names,
            f"sandesh_unarchive missing from list_tools(): {sorted(names)}",
        )

    async def test_ac1_no_tool_name_contains_tombstone(self):
        """AC1: no tool name contains 'tombstone' (admin-only, CLI-only — D9/D11)."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "tombstone" in t.name]
        self.assertEqual(
            forbidden, [],
            f"Tool names must not contain 'tombstone' (CLI-only ops): {forbidden}",
        )

    async def test_ac1_no_tool_name_contains_grant(self):
        """AC1: no tool name contains 'grant' (admin-only, CLI-only — D11)."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "grant" in t.name]
        self.assertEqual(
            forbidden, [],
            f"Tool names must not contain 'grant' (CLI-only ops): {forbidden}",
        )

    async def test_ac1_no_tool_name_contains_revoke(self):
        """AC1: no tool name contains 'revoke' (admin-only, CLI-only — D11)."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "revoke" in t.name]
        self.assertEqual(
            forbidden, [],
            f"Tool names must not contain 'revoke' (CLI-only ops): {forbidden}",
        )

    async def test_ac1_no_tool_name_contains_admin(self):
        """AC1: no tool name contains 'admin' (admin assignment has no MCP surface — D9/D11)."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "admin" in t.name]
        self.assertEqual(
            forbidden, [],
            f"Tool names must not contain 'admin': {forbidden}",
        )

    async def test_ac1_sandesh_archive_annotation_destructive_hint_false(self):
        """AC1: sandesh_archive.annotations.destructiveHint is False
        (archive is reversible — unarchive restores the project)."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_archive")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_archive must have annotations (not None)",
        )
        self.assertIs(
            getattr(tool.annotations, "destructiveHint", None), False,
            f"sandesh_archive.annotations.destructiveHint must be False "
            f"(reversible op); got: {getattr(tool.annotations, 'destructiveHint', None)!r}",
        )

    async def test_ac1_sandesh_archive_annotation_idempotent_hint_false(self):
        """AC1: sandesh_archive.annotations.idempotentHint is False
        (second call on already-archived project raises an error)."""
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_archive")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_archive must have annotations (not None)",
        )
        self.assertIs(
            getattr(tool.annotations, "idempotentHint", None), False,
            f"sandesh_archive.annotations.idempotentHint must be False; "
            f"got: {getattr(tool.annotations, 'idempotentHint', None)!r}",
        )

    # -------------------------------------------------------------------------
    # AC2 — archive/unarchive round-trip
    # -------------------------------------------------------------------------

    async def test_ac2_archive_sets_tracker_state_to_archived(self):
        """AC2: sandesh_archive(project_id='P2', by='Mainline - P2') flips the
        tracker row state to 'archived' (verified in the DB)."""
        await mcp_server.mcp.call_tool(
            "sandesh_archive",
            {"project_id": PROJ2, "by": MAINLINE_P2},
        )
        con = self._fresh_con()
        try:
            state = sdb.project_state(con, PROJ2)
        finally:
            con.close()
        self.assertEqual(
            state, "archived",
            f"project '{PROJ2}' must be 'archived' after sandesh_archive, got {state!r}",
        )

    async def test_ac2_archive_returns_without_error_on_success(self):
        """AC2: sandesh_archive completes without raising — happy path."""
        # Should not raise; result value is unspecified (archive() returns None)
        try:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2, "by": MAINLINE_P2},
            )
        except ToolError as e:
            self.fail(f"sandesh_archive raised unexpected ToolError: {e}")

    async def test_ac2_send_to_archived_project_raises_toolerror_with_archived(self):
        """AC2: after archiving P2, sandesh_send to a P2 address raises a ToolError
        whose message contains 'archived'."""
        # Archive P2 first via the library (faster; AC2 behaviour under test is the send)
        con = self._fresh_con()
        sdb.archive(con, PROJ2, MAINLINE_P2)
        # Grant P1 cross-project access so the send can reach the archive check
        sdb.grant_xproj(con, PROJ1, "ops")
        con.close()

        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_send",
                {
                    "project_id": PROJ1,
                    "from_addr": MAINLINE_P1,
                    "to": [TRACK1_P2],
                    "subject": "ping after archive",
                },
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "archived", err_msg,
            f"ToolError from send to archived project must mention 'archived'; "
            f"got: {err_msg!r}",
        )

    async def test_ac2_unarchive_restores_tracker_state_to_active(self):
        """AC2: sandesh_unarchive(project_id='P2', by='Mainline - P2') restores state
        to 'active'."""
        # Archive first (library call)
        con = self._fresh_con()
        sdb.archive(con, PROJ2, MAINLINE_P2)
        con.close()

        await mcp_server.mcp.call_tool(
            "sandesh_unarchive",
            {"project_id": PROJ2, "by": MAINLINE_P2},
        )
        con = self._fresh_con()
        try:
            state = sdb.project_state(con, PROJ2)
        finally:
            con.close()
        self.assertEqual(
            state, "active",
            f"project '{PROJ2}' must be 'active' after sandesh_unarchive, got {state!r}",
        )

    async def test_ac2_send_succeeds_after_unarchive(self):
        """AC2: after archive → unarchive round-trip, sandesh_send to a P2 address
        succeeds (no ToolError)."""
        # Full round-trip via library calls (faster)
        con = self._fresh_con()
        sdb.archive(con, PROJ2, MAINLINE_P2)
        sdb.unarchive(con, PROJ2, MAINLINE_P2)
        sdb.grant_xproj(con, PROJ1, "ops")
        con.close()

        try:
            result = await mcp_server.mcp.call_tool(
                "sandesh_send",
                {
                    "project_id": PROJ1,
                    "from_addr": MAINLINE_P1,
                    "to": [TRACK1_P2],
                    "subject": "ping after unarchive",
                },
            )
        except ToolError as e:
            self.fail(
                f"sandesh_send after unarchive should succeed but raised ToolError: {e}"
            )
        # Verify the send actually created a message (positive assertion)
        con = self._fresh_con()
        try:
            unread = sdb.inbox(con, TRACK1_P2, unread_only=True)
            subjects = [r["subject"] for r in unread]
        finally:
            con.close()
        self.assertIn(
            "ping after unarchive", subjects,
            f"Message must arrive at P2 after unarchive; inbox subjects: {subjects}",
        )
        self.assertEqual(
            len(subjects), 1,
            f"Exactly 1 unread message expected; got {len(subjects)}: {subjects}",
        )

    # -------------------------------------------------------------------------
    # AC3 — required project_id (env fallback MUST NOT apply)
    # -------------------------------------------------------------------------

    async def test_ac3_archive_without_project_id_raises_toolerror(self):
        """AC3: calling sandesh_archive WITHOUT project_id raises a ToolError
        (pydantic validation — 'Field required') even when $SANDESH_PROJECT is set.
        The env fallback (_resolve_project) must NOT be used for lifecycle tools."""
        os.environ["SANDESH_PROJECT"] = PROJ2  # env is set — must be ignored
        try:
            with self.assertRaises(ToolError) as ctx:
                await mcp_server.mcp.call_tool(
                    "sandesh_archive",
                    {"by": MAINLINE_P2},  # project_id intentionally absent
                )
            err_msg = str(ctx.exception)
            # FastMCP/pydantic surfaces missing required params as a validation ToolError.
            # The message contains 'Field required' and 'type=missing'.
            # Note: at RED this will be an "Unknown tool" ToolError — the assertion
            # below distinguishes the real validation error from an Unknown-tool error.
            self.assertIn(
                "Field required", err_msg,
                f"Missing required project_id must raise pydantic 'Field required' "
                f"ToolError; got: {err_msg!r}",
            )
        finally:
            os.environ.pop("SANDESH_PROJECT", None)

    async def test_ac3_archive_without_by_raises_toolerror(self):
        """AC3: calling sandesh_archive WITHOUT `by` raises a ToolError
        (pydantic validation — 'Field required'). `by` is also required with no default."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2},  # `by` intentionally absent
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "Field required", err_msg,
            f"Missing required `by` must raise pydantic 'Field required' ToolError; "
            f"got: {err_msg!r}",
        )

    async def test_ac3_archive_with_env_project_but_no_arg_still_fails(self):
        """AC3: $SANDESH_PROJECT set to a valid project; sandesh_archive called with
        only `by` (no project_id arg) — must still raise ToolError, not succeed.
        This confirms _resolve_project is NOT used (no env fallback on lifecycle ops)."""
        os.environ["SANDESH_PROJECT"] = PROJ2
        try:
            with self.assertRaises(ToolError):
                await mcp_server.mcp.call_tool(
                    "sandesh_archive",
                    {"by": MAINLINE_P2},
                )
            # If we reach here, a ToolError was raised — correct.
            # The project state must NOT have changed to 'archived'.
            con = self._fresh_con()
            try:
                state = sdb.project_state(con, PROJ2)
            finally:
                con.close()
            self.assertEqual(
                state, "active",
                f"P2 must remain 'active' when the call is rejected; got {state!r}",
            )
        finally:
            os.environ.pop("SANDESH_PROJECT", None)

    # -------------------------------------------------------------------------
    # AC4 — authz mapped to ToolError (not a crash)
    # -------------------------------------------------------------------------

    async def test_ac4_archive_by_track_raises_toolerror(self):
        """AC4: sandesh_archive(by='Track 1 - P2') is rejected with a ToolError —
        not a crash — because only Mainline may archive a project."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2, "by": TRACK1_P2},
            )
        self.assertIsNotNone(ctx.exception)

    async def test_ac4_archive_by_track_error_contains_authz_message(self):
        """AC4 behavioural: ToolError from Track-issued archive mentions the Mainline
        authz requirement (PermissionError mapped to ToolError)."""
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2, "by": TRACK1_P2},
            )
        err_msg = str(ctx.exception)
        # The library raises:
        # PermissionError("only the project's own Mainline ('Mainline - P2') may …")
        # At RED: ToolError says "Unknown tool: sandesh_archive" — assertion FAILS.
        # At GREEN: mapped PermissionError surfaces with Mainline context.
        self.assertIn(
            "Mainline", err_msg,
            f"ToolError from unauthorised archive must mention 'Mainline'; "
            f"got: {err_msg!r}",
        )

    async def test_ac4_archive_already_archived_raises_toolerror(self):
        """AC4: archiving an already-archived project raises a ToolError (not a crash)
        whose message contains 'is not active'."""
        # Archive P2 via library so the MCP call hits the 'already archived' guard.
        con = self._fresh_con()
        sdb.archive(con, PROJ2, MAINLINE_P2)
        con.close()

        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2, "by": MAINLINE_P2},
            )
        err_msg = str(ctx.exception)
        # The library raises ValueError("project 'P2' is not active")
        # At RED: ToolError is "Unknown tool: sandesh_archive" — assertion FAILS.
        # At GREEN: ValueError mapped to ToolError, message preserved.
        self.assertIn(
            "is not active", err_msg,
            f"ToolError from already-archived project must contain 'is not active'; "
            f"got: {err_msg!r}",
        )

    async def test_ac4_archive_state_unchanged_on_authz_rejection(self):
        """AC4 behavioural: P2's state must remain 'active' when the archive call is
        rejected due to authz (PermissionError)."""
        try:
            await mcp_server.mcp.call_tool(
                "sandesh_archive",
                {"project_id": PROJ2, "by": TRACK1_P2},
            )
        except ToolError:
            pass  # expected

        con = self._fresh_con()
        try:
            state = sdb.project_state(con, PROJ2)
        finally:
            con.close()
        self.assertEqual(
            state, "active",
            f"P2 must remain 'active' after rejected archive; got {state!r}",
        )

    # -------------------------------------------------------------------------
    # RuntimeError mapping: live notifier + force=False / force=True
    # -------------------------------------------------------------------------

    async def test_runtime_error_force_false_with_live_notifier_raises_toolerror(self):
        """RuntimeError mapping: seeding a live notifier for P2 then calling
        sandesh_archive(force=False) with a tiny wait must raise a ToolError
        (RuntimeError mapped — not a raw traceback)."""
        import unittest.mock

        tok = str(uuid.uuid4())
        con = self._fresh_con()
        # Acquire a live notifier using the current process PID (guaranteed alive).
        ok, _ = sdb.notifier_acquire(con, MAINLINE_P2, os.getpid(), tok, "localhost")
        con.close()
        self.assertTrue(ok, "notifier_acquire must succeed for the setup to be valid")

        # Monkeypatch poll_interval to return ~0.05 s so the eviction wait is tiny.
        with unittest.mock.patch.object(sdb, "poll_interval", return_value=0.05):
            with self.assertRaises(ToolError) as ctx:
                await mcp_server.mcp.call_tool(
                    "sandesh_archive",
                    {"project_id": PROJ2, "by": MAINLINE_P2, "force": False},
                )
        err_msg = str(ctx.exception)
        # RuntimeError from _evict_project_notifiers must be mapped to ToolError.
        # At RED: "Unknown tool: sandesh_archive" — no RuntimeError content.
        self.assertIsNotNone(
            ctx.exception,
            "ToolError must be raised (not a raw RuntimeError traceback)",
        )

    async def test_runtime_error_force_false_state_unchanged(self):
        """RuntimeError mapping: when force=False fails due to live notifier,
        the project state must remain 'active' (refusal leaves nothing changed)."""
        import unittest.mock

        tok = str(uuid.uuid4())
        con = self._fresh_con()
        sdb.notifier_acquire(con, MAINLINE_P2, os.getpid(), tok, "localhost")
        con.close()

        with unittest.mock.patch.object(sdb, "poll_interval", return_value=0.05):
            try:
                await mcp_server.mcp.call_tool(
                    "sandesh_archive",
                    {"project_id": PROJ2, "by": MAINLINE_P2, "force": False},
                )
            except ToolError:
                pass  # expected

        con = self._fresh_con()
        try:
            state = sdb.project_state(con, PROJ2)
        finally:
            con.close()
        self.assertEqual(
            state, "active",
            f"P2 must remain 'active' after force=False refusal; got {state!r}",
        )

    async def test_runtime_error_force_true_with_live_notifier_succeeds(self):
        """RuntimeError mapping: sandesh_archive(force=True) reaps the surviving
        notifier and transitions the project to 'archived'."""
        import unittest.mock

        tok = str(uuid.uuid4())
        con = self._fresh_con()
        sdb.notifier_acquire(con, MAINLINE_P2, os.getpid(), tok, "localhost")
        con.close()

        with unittest.mock.patch.object(sdb, "poll_interval", return_value=0.05):
            try:
                await mcp_server.mcp.call_tool(
                    "sandesh_archive",
                    {"project_id": PROJ2, "by": MAINLINE_P2, "force": True},
                )
            except ToolError as e:
                self.fail(
                    f"sandesh_archive(force=True) must succeed with live notifier "
                    f"but raised ToolError: {e}"
                )

        con = self._fresh_con()
        try:
            state = sdb.project_state(con, PROJ2)
        finally:
            con.close()
        self.assertEqual(
            state, "archived",
            f"P2 must be 'archived' after force=True archive; got {state!r}",
        )


    # -------------------------------------------------------------------------
    # sandesh_unarchive error mapping: active project → ToolError "is not archived"
    # -------------------------------------------------------------------------

    async def test_unarchive_on_active_project_raises_toolerror_with_is_not_archived(self):
        """sandesh_unarchive on an ACTIVE project (i.e. never archived) must raise a
        ToolError whose message contains 'is not archived'.

        The underlying library raises ValueError("project 'P2' is not archived");
        mcp_server.py maps (ValueError, PermissionError, RuntimeError) → ToolError.
        This pins that error-mapping path for unarchive (dispatch-approved follow-up
        from C1 review)."""
        # P2 is provisioned by setUp and is in state 'active' — never archived.
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_unarchive",
                {"project_id": PROJ2, "by": MAINLINE_P2},
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "is not archived", err_msg,
            f"ToolError from unarchive on active project must contain 'is not archived'; "
            f"got: {err_msg!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
