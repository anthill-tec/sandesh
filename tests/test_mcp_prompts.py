"""test_mcp_prompts.py — MCP prompt surface tests for CR-SAN-041 (C1 RED).

Tests for:
  AC1 — SANDESH_INSTRUCTIONS contains an imperative enable-listening directive
         (sandesh notify + run_in_background + on-exit sandesh_fetch + relaunch)
  AC2 — register and setup tool descriptions mention enabling listening / sandesh notify
  AC3 — exactly 5 prompts: setup, register, unregister, archive, unarchive;
         each has the expected argument names
  AC4 — register prompt rendered text instructs the agent to enable listening
  AC5 — boundary: no prompt for agent-only/admin verbs; still exactly 12 tools
  AC6 — get_prompt for each of the 5 renders without error and references its
         matching tool/verb

All tests FAIL now (0 prompts exist; SANDESH_INSTRUCTIONS lacks the imperative block;
register/setup tool descs have no listening pointer). GREEN adds the prompts + directives.

Run:
  python-crucible.py test --tests tests.test_mcp_prompts --agent CR-SAN-041-C1-RED
"""

import asyncio
import os
import shutil
import tempfile
import unittest

from sandesh import mcp_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJ = "TestPrompts"
MAINLINE = "Mainline - TestPrompts"
TRACK1 = "Track 1 - TestPrompts"

# Prompts that MUST exist after GREEN.
EXPECTED_PROMPT_NAMES = {"setup", "register", "unregister", "archive", "unarchive"}

# Prompts that MUST NOT exist (agent-only / admin-only verbs).
FORBIDDEN_PROMPT_NAMES = {
    "send", "reply", "fetch", "inbox", "thread", "search", "addressbook",
    "tombstone", "grant", "revoke",
}

# Exact tool count after GREEN (unchanged by this CR).
EXPECTED_TOOL_COUNT = 12


class McpPromptsSetupMixin:
    """Common setUp/tearDown for MCP prompt tests (mirrors test_mcp_surface.py)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-prompts-test-")
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


# ---------------------------------------------------------------------------
# AC1 — SANDESH_INSTRUCTIONS imperative enable-listening directive
# ---------------------------------------------------------------------------


class AC1InstructionsDirectiveTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC1: SANDESH_INSTRUCTIONS must contain a prominent, imperative enable-listening block."""

    def _instructions(self):
        return getattr(mcp_server, "SANDESH_INSTRUCTIONS", None)

    def test_ac1_instructions_constant_exists(self):
        """AC1: mcp_server.SANDESH_INSTRUCTIONS is a non-empty str."""
        instr = self._instructions()
        self.assertIsInstance(
            instr,
            str,
            f"SANDESH_INSTRUCTIONS must be a str, got {type(instr).__name__!r}",
        )
        self.assertGreater(len(instr.strip()), 0, "SANDESH_INSTRUCTIONS must not be empty")

    def test_ac1_instructions_names_sandesh_notify(self):
        """AC1: SANDESH_INSTRUCTIONS contains 'sandesh notify' (the wake command)."""
        instr = self._instructions() or ""
        self.assertIn(
            "sandesh notify",
            instr,
            "SANDESH_INSTRUCTIONS must contain 'sandesh notify' as the enable-listening "
            f"directive. Got: {instr[:300]!r}",
        )

    def test_ac1_instructions_mentions_background_task_launch(self):
        """AC1: SANDESH_INSTRUCTIONS mentions the background-task launch mechanism
        ('run_in_background' OR 'background-task' OR 'background task')."""
        instr = (self._instructions() or "").lower()
        background_terms = ("run_in_background", "background-task", "background task")
        self.assertTrue(
            any(term in instr for term in background_terms),
            f"SANDESH_INSTRUCTIONS must mention the background-task launch mechanism "
            f"(one of {background_terms}). Got (first 400 chars): "
            f"{(self._instructions() or '')[:400]!r}",
        )

    def test_ac1_instructions_mentions_sandesh_fetch_on_exit(self):
        """AC1: SANDESH_INSTRUCTIONS mentions calling sandesh_fetch after the watcher exits."""
        instr = self._instructions() or ""
        # Accept either 'sandesh_fetch' (MCP tool name) or 'sandesh fetch' (CLI form).
        fetch_terms = ("sandesh_fetch", "sandesh fetch")
        lowered = instr.lower()
        self.assertTrue(
            any(term in lowered for term in fetch_terms),
            f"SANDESH_INSTRUCTIONS must mention calling sandesh_fetch (or 'sandesh fetch') "
            f"after the watcher exits. Got (first 400 chars): {instr[:400]!r}",
        )

    def test_ac1_instructions_mentions_relaunch(self):
        """AC1: SANDESH_INSTRUCTIONS mentions relaunching the watcher after fetching
        ('relaunch' OR 'restart' OR 'launch again' OR 'repeat')."""
        instr = (self._instructions() or "").lower()
        relaunch_terms = ("relaunch", "restart", "launch again", "repeat")
        self.assertTrue(
            any(term in instr for term in relaunch_terms),
            f"SANDESH_INSTRUCTIONS must mention relaunching/restarting the watcher "
            f"(one of {relaunch_terms}). Got (first 500 chars): "
            f"{(self._instructions() or '')[:500]!r}",
        )

    def test_ac1_instructions_imperative_must_directive(self):
        """AC1: SANDESH_INSTRUCTIONS must use an imperative form ('must' OR 'MUST')
        in the enable-listening context — not buried as passing prose."""
        instr = self._instructions() or ""
        lowered = instr.lower()
        # The directive must contain 'must' near the listening instruction.
        # We accept 'must' anywhere — it should appear in the imperative block.
        self.assertIn(
            "must",
            lowered,
            "SANDESH_INSTRUCTIONS must use imperative 'must' to convey the "
            f"enable-listening directive. Got (first 500 chars): {instr[:500]!r}",
        )

    async def test_ac1_server_instructions_attribute_matches_constant(self):
        """AC1: mcp_server.mcp.instructions matches SANDESH_INSTRUCTIONS (delivered on connect)."""
        server_instr = mcp_server.mcp.instructions
        constant_instr = self._instructions()
        self.assertIsNotNone(
            server_instr,
            "mcp.instructions must not be None — SANDESH_INSTRUCTIONS must be set",
        )
        self.assertIsInstance(server_instr, str, "mcp.instructions must be a str")
        self.assertEqual(
            server_instr,
            constant_instr,
            "mcp.instructions must equal mcp_server.SANDESH_INSTRUCTIONS",
        )


# ---------------------------------------------------------------------------
# AC2 — tool description pointers in register and setup
# ---------------------------------------------------------------------------


class AC2ToolDescriptionPointersTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC2: register and setup tool descriptions must mention enabling listening."""

    def _tool_by_name(self, tools, name):
        for t in tools:
            if t.name == name:
                return t
        self.fail(f"Tool '{name}' not found in list_tools(); available: {[t.name for t in tools]}")

    async def test_ac2_register_tool_description_mentions_sandesh_notify(self):
        """AC2: sandesh_register.description mentions 'sandesh notify' (the listen command)."""
        tools = await mcp_server.mcp.list_tools()
        reg_tool = self._tool_by_name(tools, "sandesh_register")
        desc = (reg_tool.description or "").lower()
        self.assertIn(
            "sandesh notify",
            desc,
            f"sandesh_register description must mention 'sandesh notify' so the agent "
            f"knows to enable listening after registering. Got: {reg_tool.description!r}",
        )

    async def test_ac2_register_tool_description_mentions_listening(self):
        """AC2: sandesh_register.description mentions enabling listening (background mention)."""
        tools = await mcp_server.mcp.list_tools()
        reg_tool = self._tool_by_name(tools, "sandesh_register")
        desc = (reg_tool.description or "").lower()
        listening_terms = ("listen", "background", "notify", "wake")
        self.assertTrue(
            any(term in desc for term in listening_terms),
            f"sandesh_register description must mention enabling listening "
            f"(one of {listening_terms}). Got: {reg_tool.description!r}",
        )

    async def test_ac2_setup_tool_description_mentions_sandesh_notify(self):
        """AC2: sandesh_setup.description contains a pointer to 'sandesh notify'."""
        tools = await mcp_server.mcp.list_tools()
        setup_tool = self._tool_by_name(tools, "sandesh_setup")
        desc = (setup_tool.description or "").lower()
        self.assertIn(
            "sandesh notify",
            desc,
            f"sandesh_setup description must include a brief pointer to 'sandesh notify'. "
            f"Got: {setup_tool.description!r}",
        )


# ---------------------------------------------------------------------------
# AC3 — exactly 5 prompts with correct names and argument lists
# ---------------------------------------------------------------------------


class AC3ExactlyFivePromptsTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC3: server exposes exactly 5 prompts; each has the expected argument names."""

    async def test_ac3_list_prompts_returns_exactly_five(self):
        """AC3: list_prompts() returns exactly 5 prompts."""
        prompts = await mcp_server.mcp.list_prompts()
        self.assertEqual(
            len(prompts),
            5,
            f"Expected exactly 5 prompts, got {len(prompts)}: "
            f"{[p.name for p in prompts]}",
        )

    async def test_ac3_prompt_names_match_exactly(self):
        """AC3: prompt names are exactly {setup, register, unregister, archive, unarchive}."""
        prompts = await mcp_server.mcp.list_prompts()
        names = {p.name for p in prompts}
        self.assertEqual(
            names,
            EXPECTED_PROMPT_NAMES,
            f"Prompt names mismatch. Got={sorted(names)}, "
            f"expected={sorted(EXPECTED_PROMPT_NAMES)}",
        )

    async def _prompt_by_name(self, name):
        prompts = await mcp_server.mcp.list_prompts()
        for p in prompts:
            if p.name == name:
                return p
        self.fail(
            f"Prompt '{name}' not found. Available: {[p.name for p in prompts]}"
        )

    def _arg_names(self, prompt):
        """Return the set of argument names for a prompt object."""
        if prompt.arguments is None:
            return set()
        return {a.name for a in prompt.arguments}

    async def test_ac3_setup_prompt_has_project_id_arg(self):
        """AC3: setup prompt accepts a project_id argument."""
        p = await self._prompt_by_name("setup")
        arg_names = self._arg_names(p)
        self.assertIn(
            "project_id",
            arg_names,
            f"setup prompt must have a 'project_id' argument. Got args: {sorted(arg_names)}",
        )

    async def test_ac3_register_prompt_has_addr_and_project_id_args(self):
        """AC3: register prompt accepts addr and project_id arguments."""
        p = await self._prompt_by_name("register")
        arg_names = self._arg_names(p)
        for expected_arg in ("addr", "project_id"):
            self.assertIn(
                expected_arg,
                arg_names,
                f"register prompt must have '{expected_arg}' argument. "
                f"Got args: {sorted(arg_names)}",
            )

    async def test_ac3_unregister_prompt_has_recipient_and_requester_args(self):
        """AC3: unregister prompt accepts recipient and requester arguments."""
        p = await self._prompt_by_name("unregister")
        arg_names = self._arg_names(p)
        for expected_arg in ("recipient", "requester"):
            self.assertIn(
                expected_arg,
                arg_names,
                f"unregister prompt must have '{expected_arg}' argument. "
                f"Got args: {sorted(arg_names)}",
            )

    async def test_ac3_archive_prompt_has_project_id_and_by_args(self):
        """AC3: archive prompt accepts project_id and by arguments."""
        p = await self._prompt_by_name("archive")
        arg_names = self._arg_names(p)
        for expected_arg in ("project_id", "by"):
            self.assertIn(
                expected_arg,
                arg_names,
                f"archive prompt must have '{expected_arg}' argument. "
                f"Got args: {sorted(arg_names)}",
            )

    async def test_ac3_unarchive_prompt_has_project_id_and_by_args(self):
        """AC3: unarchive prompt accepts project_id and by arguments."""
        p = await self._prompt_by_name("unarchive")
        arg_names = self._arg_names(p)
        for expected_arg in ("project_id", "by"):
            self.assertIn(
                expected_arg,
                arg_names,
                f"unarchive prompt must have '{expected_arg}' argument. "
                f"Got args: {sorted(arg_names)}",
            )


# ---------------------------------------------------------------------------
# AC4 — register prompt rendered text nudges listening
# ---------------------------------------------------------------------------


class AC4RegisterPromptNudgesListeningTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC4: register prompt rendered text must instruct the agent to enable listening."""

    async def test_ac4_register_prompt_rendered_mentions_sandesh_notify(self):
        """AC4: get_prompt('register', {...}) rendered text mentions 'sandesh notify'."""
        result = await mcp_server.mcp.get_prompt(
            "register",
            {"addr": MAINLINE, "project_id": PROJ},
        )
        # Collect all text content from the rendered messages.
        text_parts = []
        for msg in result.messages:
            content = msg.content
            if hasattr(content, "text"):
                text_parts.append(content.text)
            elif isinstance(content, str):
                text_parts.append(content)
        full_text = " ".join(text_parts).lower()

        self.assertIn(
            "sandesh notify",
            full_text,
            f"register prompt rendered text must mention 'sandesh notify' to nudge "
            f"the agent to enable listening. Rendered (first 500 chars): "
            f"{' '.join(text_parts)[:500]!r}",
        )

    async def test_ac4_register_prompt_rendered_mentions_background_launch(self):
        """AC4: register prompt rendered text mentions the background-task launch."""
        result = await mcp_server.mcp.get_prompt(
            "register",
            {"addr": MAINLINE, "project_id": PROJ},
        )
        text_parts = []
        for msg in result.messages:
            content = msg.content
            if hasattr(content, "text"):
                text_parts.append(content.text)
            elif isinstance(content, str):
                text_parts.append(content)
        full_text = " ".join(text_parts).lower()

        background_terms = ("run_in_background", "background-task", "background task", "background")
        self.assertTrue(
            any(term in full_text for term in background_terms),
            f"register prompt rendered text must mention the background-task launch "
            f"(one of {background_terms}). Rendered (first 500 chars): "
            f"{' '.join(text_parts)[:500]!r}",
        )

    async def test_ac4_register_prompt_rendered_has_messages(self):
        """AC4: get_prompt('register', {...}) returns a result with at least one message."""
        result = await mcp_server.mcp.get_prompt(
            "register",
            {"addr": MAINLINE, "project_id": PROJ},
        )
        self.assertIsNotNone(result, "get_prompt('register', ...) must not return None")
        self.assertGreater(
            len(result.messages),
            0,
            "register prompt must render at least one message",
        )


# ---------------------------------------------------------------------------
# AC5 — boundary: no forbidden prompts; still exactly 12 tools
# ---------------------------------------------------------------------------


class AC5BoundaryTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC5: no prompt for agent-only/admin verbs; tools unchanged at 12."""

    async def test_ac5_no_forbidden_prompts_exist(self):
        """AC5: none of send/reply/fetch/inbox/thread/search/addressbook/tombstone/grant/revoke
        appear as prompts."""
        prompts = await mcp_server.mcp.list_prompts()
        names = {p.name for p in prompts}
        found_forbidden = names & FORBIDDEN_PROMPT_NAMES
        self.assertEqual(
            found_forbidden,
            set(),
            f"Forbidden prompts must NOT be registered. Found: {sorted(found_forbidden)}",
        )

    async def test_ac5_tools_still_twelve(self):
        """AC5: list_tools() still returns exactly 12 tools (unchanged by this CR)."""
        tools = await mcp_server.mcp.list_tools()
        self.assertEqual(
            len(tools),
            EXPECTED_TOOL_COUNT,
            f"Tool count must remain {EXPECTED_TOOL_COUNT}. "
            f"Got {len(tools)}: {sorted(t.name for t in tools)}",
        )

    async def test_ac5_all_twelve_expected_tool_names_present(self):
        """AC5: the 12 expected tool names are all present (name contract unchanged)."""
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        expected = {
            "sandesh_setup",
            "sandesh_register",
            "sandesh_unregister",
            "sandesh_addressbook",
            "sandesh_send",
            "sandesh_reply",
            "sandesh_inbox",
            "sandesh_fetch",
            "sandesh_thread",
            "sandesh_search",
            "sandesh_archive",
            "sandesh_unarchive",
        }
        missing = expected - names
        self.assertEqual(
            missing,
            set(),
            f"Missing expected tools after CR-SAN-041: {sorted(missing)}",
        )


# ---------------------------------------------------------------------------
# AC6 — all 5 prompts render without error and reference their verb
# ---------------------------------------------------------------------------


class AC6AllPromptsRenderTest(McpPromptsSetupMixin, unittest.IsolatedAsyncioTestCase):
    """AC6: each of the 5 prompts renders without error and references its matching verb."""

    def _collect_text(self, result):
        """Collect all text content from a GetPromptResult's messages."""
        parts = []
        for msg in result.messages:
            content = msg.content
            if hasattr(content, "text"):
                parts.append(content.text)
            elif isinstance(content, str):
                parts.append(content)
        return " ".join(parts)

    async def test_ac6_setup_prompt_renders_and_references_setup(self):
        """AC6: setup prompt renders without error and references 'setup' or 'sandesh_setup'."""
        result = await mcp_server.mcp.get_prompt(
            "setup",
            {"project_id": PROJ},
        )
        self.assertIsNotNone(result, "get_prompt('setup', ...) must not return None")
        self.assertGreater(len(result.messages), 0, "setup prompt must have at least one message")
        text = self._collect_text(result).lower()
        setup_terms = ("setup", "sandesh_setup", "provision")
        self.assertTrue(
            any(term in text for term in setup_terms),
            f"setup prompt rendered text must reference its verb "
            f"(one of {setup_terms}). Got (first 400 chars): "
            f"{self._collect_text(result)[:400]!r}",
        )

    async def test_ac6_register_prompt_renders_and_references_register(self):
        """AC6: register prompt renders without error and references 'register' or 'sandesh_register'."""
        result = await mcp_server.mcp.get_prompt(
            "register",
            {"addr": MAINLINE, "project_id": PROJ},
        )
        self.assertIsNotNone(result, "get_prompt('register', ...) must not return None")
        self.assertGreater(len(result.messages), 0, "register prompt must have at least one message")
        text = self._collect_text(result).lower()
        self.assertTrue(
            "register" in text or "sandesh_register" in text,
            f"register prompt rendered text must reference its verb. "
            f"Got (first 400 chars): {self._collect_text(result)[:400]!r}",
        )

    async def test_ac6_unregister_prompt_renders_and_references_unregister(self):
        """AC6: unregister prompt renders without error and references 'unregister'."""
        result = await mcp_server.mcp.get_prompt(
            "unregister",
            {"recipient": MAINLINE, "requester": MAINLINE},
        )
        self.assertIsNotNone(result, "get_prompt('unregister', ...) must not return None")
        self.assertGreater(len(result.messages), 0, "unregister prompt must have at least one message")
        text = self._collect_text(result).lower()
        self.assertTrue(
            "unregister" in text or "sandesh_unregister" in text,
            f"unregister prompt rendered text must reference its verb. "
            f"Got (first 400 chars): {self._collect_text(result)[:400]!r}",
        )

    async def test_ac6_archive_prompt_renders_and_references_archive(self):
        """AC6: archive prompt renders without error and references 'archive'."""
        result = await mcp_server.mcp.get_prompt(
            "archive",
            {"project_id": PROJ, "by": MAINLINE},
        )
        self.assertIsNotNone(result, "get_prompt('archive', ...) must not return None")
        self.assertGreater(len(result.messages), 0, "archive prompt must have at least one message")
        text = self._collect_text(result).lower()
        self.assertTrue(
            "archive" in text or "sandesh_archive" in text,
            f"archive prompt rendered text must reference its verb. "
            f"Got (first 400 chars): {self._collect_text(result)[:400]!r}",
        )

    async def test_ac6_unarchive_prompt_renders_and_references_unarchive(self):
        """AC6: unarchive prompt renders without error and references 'unarchive'."""
        result = await mcp_server.mcp.get_prompt(
            "unarchive",
            {"project_id": PROJ, "by": MAINLINE},
        )
        self.assertIsNotNone(result, "get_prompt('unarchive', ...) must not return None")
        self.assertGreater(len(result.messages), 0, "unarchive prompt must have at least one message")
        text = self._collect_text(result).lower()
        self.assertTrue(
            "unarchive" in text or "sandesh_unarchive" in text,
            f"unarchive prompt rendered text must reference its verb. "
            f"Got (first 400 chars): {self._collect_text(result)[:400]!r}",
        )

    async def test_ac6_list_prompts_returns_five_and_tools_list_returns_twelve(self):
        """AC6 integration: prompts/list returns 5; tools/list returns 12 (in one call)."""
        prompts = await mcp_server.mcp.list_prompts()
        tools = await mcp_server.mcp.list_tools()
        self.assertEqual(
            len(prompts),
            5,
            f"prompts/list must return 5, got {len(prompts)}: {[p.name for p in prompts]}",
        )
        self.assertEqual(
            len(tools),
            EXPECTED_TOOL_COUNT,
            f"tools/list must return {EXPECTED_TOOL_COUNT}, got {len(tools)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
