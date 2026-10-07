"""test_mcp_thread_requester.py — RED tests for CR-SAN-054 Cycle 3 (§S3, AC6).

Covers the MCP `sandesh_thread` tool gaining an optional `requester: str | None`
parameter: with a valid requester that is a party (sender or recipient) of a
message that has a body, the returned chain dict for that message gains a
`body` key with the full text. Subject-only messages never gain `body`. A
non-party valid requester, or no requester at all, leaves the result without
any `body` keys. A malformed requester, or a well-formed requester whose
address does not belong to the thread's project, raises `ToolError` (AC6:
`requester="Mainline - Other"` -> `ToolError`).

  python-crucible.py test --tests tests.test_mcp_thread_requester --agent CR-SAN-054-C3-RED

ESCALATION (documented, not guessed): the dispatch prompt's case 6 wording
("Mainline - Other" (well-formed, registered, not a party) -> no body, no
error) DEVIATES from AC6, which states explicitly:
    `requester="Mainline - Other"` -> `ToolError`.
This test file follows AC6 (the authoritative acceptance criterion) and
asserts ToolError for `requester="Mainline - Other"`, not the dispatch
prompt's alternative "no body, no error" outcome. §S3's prose ("An invalid
`requester` raises `ToolError` with the `validate_address` message") does not
by itself explain why a well-formed, registered address in a different
project would be "invalid" — the only coherent reading that satisfies AC6 is
that the implementation validates `requester` against a project derived from
the thread (as the CLI's `--as` does against `--project`, see `cli.py`
`axi_thread` l.1171-1176), so a cross-project well-formed address still fails
`validate_address(requester, project)`'s project-mismatch branch. Both
candidate error messages from `sandesh_db.validate_address` (the malformed-
format branch and the project-mismatch branch) are asserted precisely
below, each for the case that actually produces it.

Expected RED (confirmed by the actual first run — see below): FastMCP's
`call_tool` does NOT reject an unknown `requester` kwarg on `sandesh_thread`
(unlike `sandesh_inbox`'s `sender_project`, whose rejection other sibling
tests rely on) — the extra field is silently ignored, so every call that
passes `requester=...` today returns the SAME `thread()` output as if
`requester` had been omitted: no `body` key anywhere. This makes the
"body present for a party" tests (and the two merged withheld/omitted
control-case assertions) fail for the right reason — the missing `body` key
— and the two ToolError tests fail because no `ToolError` is raised at all
yet (`validate_address` on `requester` is never reached). The schema test
fails because `requester` is absent from `sandesh_thread`'s `inputSchema`.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import json
import os
import shutil
import tempfile
import unittest

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp.exceptions import ToolError

PROJ = "Demo"
OTHER_PROJ = "Other"

MAINLINE = "Mainline - Demo"
TRACK1 = "Track 1 - Demo"
TRACK2 = "Track 2 - Demo"
MAINLINE_OTHER = "Mainline - Other"

BODY_TERM = "gateway timeout"


def _data(result):
    """Unwrap FastMCP.call_tool's converted return for list/dict-returning tools.

    Same pattern as tests/test_mcp_read_tools.py and test_mcp_search_surface.py.
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


class _McpThreadRequesterBase(unittest.IsolatedAsyncioTestCase):
    """Fixture: project Demo (Mainline/Track 1/Track 2) + project Other (Mainline).

    Message #m: Track 1 - Demo -> Mainline - Demo, body contains 'gateway timeout',
    already fetched (read). A subject-only reply to #m: Mainline - Demo -> Track 1 - Demo.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-thread-requester-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ)
        sdb.setup(OTHER_PROJ)

        self.store = sdb.store_dir(PROJ)

        self.con = sdb.connect()
        sdb.register(self.con, MAINLINE, kind="mainline", project=PROJ)
        sdb.register(self.con, TRACK1, kind="track", project=PROJ)
        sdb.register(self.con, TRACK2, kind="track", project=PROJ)
        sdb.register(self.con, MAINLINE_OTHER, kind="mainline", project=OTHER_PROJ)

        self.mid_m = sdb.send(
            self.con, self.store,
            from_addr=TRACK1,
            to=[MAINLINE],
            subject="gateway timeout error",
            kind="fyi",
            body_text=f"encountered {BODY_TERM} during load test",
        )
        # Mark #m read (fetched) — the fixture's "already fetched (read)" state.
        sdb.fetch(self.con, self.store, MAINLINE)

        # Subject-only reply to #m (no body_text -> no body file).
        self.mid_reply = sdb.reply(
            self.con, self.store,
            parent_id=self.mid_m,
            from_addr=MAINLINE,
        )

    def tearDown(self):
        import contextlib
        with contextlib.suppress(Exception):
            self.con.close()
        for k, v in (("XDG_DATA_HOME", self._prev_xdg),
                     ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _chain_by_id(self, chain, msg_id):
        for d in chain:
            if d["id"] == msg_id:
                return d
        self.fail(f"message #{msg_id} not found in returned chain: {chain!r}")


# ===========================================================================
# Body visibility — valid requester that IS a party to #m
# ===========================================================================

class ThreadRequesterBodyForPartyTest(_McpThreadRequesterBase):

    async def test_requester_recipient_of_m_sees_its_body(self):
        """AC6: requester=Mainline - Demo (the recipient of #m) -> the chain dict
        for #m gains 'body' containing 'gateway timeout'.

        RED: 'requester' is not yet a declared parameter on sandesh_thread ->
        ToolError (unknown field) raised, not the expected body.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": MAINLINE},
        )
        chain = _data(result)
        dict_m = self._chain_by_id(chain, self.mid_m)
        self.assertIn(
            "body", dict_m,
            f"chain dict for #{self.mid_m} must have a 'body' key for the "
            f"recipient requester {MAINLINE!r}: {dict_m!r}",
        )
        self.assertIn(
            BODY_TERM, dict_m["body"],
            f"body for #{self.mid_m} must contain {BODY_TERM!r}; got: {dict_m['body']!r}",
        )

    async def test_requester_sender_of_m_sees_its_body(self):
        """AC6 (S3): requester=Track 1 - Demo (the SENDER of #m) -> body present too
        (S3: 'a message whose sender OR recipient is requester').

        RED: same unknown-field ToolError as above.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": TRACK1},
        )
        chain = _data(result)
        dict_m = self._chain_by_id(chain, self.mid_m)
        self.assertIn(
            "body", dict_m,
            f"chain dict for #{self.mid_m} must have a 'body' key for the "
            f"sender requester {TRACK1!r}: {dict_m!r}",
        )
        self.assertIn(
            BODY_TERM, dict_m["body"],
            f"body for #{self.mid_m} must contain {BODY_TERM!r}; got: {dict_m['body']!r}",
        )


# ===========================================================================
# Body visibility — valid requester that is NOT a party, or absent
# ===========================================================================

class ThreadRequesterBodyWithheldTest(_McpThreadRequesterBase):

    async def test_non_party_requester_sees_no_body_while_party_requester_does(self):
        """AC6/S3: requester=Track 2 - Demo (registered, neither sender nor recipient
        of #m) -> NO chain dict gains a 'body' key. Combined, in the SAME test, with
        the positive case (requester=Mainline - Demo, a party) so this test is not a
        vacuous pre-feature pass: it fails today because the party-requester call
        does not yet produce a body (proving the scoping logic is not implemented),
        while also pinning that a non-party requester must never see one.

        RED: the party-requester assertion fails ('body' absent for #m even though
        Mainline - Demo is the recipient) — 'requester' is not yet wired to body
        disclosure at all.
        """
        party_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": MAINLINE},
        )
        party_chain = _data(party_result)
        party_dict_m = self._chain_by_id(party_chain, self.mid_m)
        self.assertIn(
            "body", party_dict_m,
            f"control case: party requester {MAINLINE!r} must see a 'body' key "
            f"for #{self.mid_m}: {party_dict_m!r}",
        )

        non_party_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": TRACK2},
        )
        non_party_chain = _data(non_party_result)
        bodies = [d["id"] for d in non_party_chain if "body" in d]
        self.assertEqual(
            bodies, [],
            f"no chain dict may have a 'body' key for non-party requester "
            f"{TRACK2!r}; dicts with body: {bodies!r}",
        )

    async def test_omitted_requester_withholds_body_that_a_party_requester_would_see(self):
        """S3: 'Without requester the result is unchanged' -> no chain dict gains a
        'body' key when requester is omitted, even though #m has a real body and a
        valid party (Mainline - Demo) exists. Combined, in the SAME test, with the
        positive case so this is not a vacuous pre-feature pass.

        RED: the party-requester assertion fails ('body' absent for #m) — the whole
        requester-gated-disclosure mechanism this test contrasts against does not
        exist yet.
        """
        omitted_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m},
        )
        omitted_chain = _data(omitted_result)
        bodies = [d["id"] for d in omitted_chain if "body" in d]
        self.assertEqual(
            bodies, [],
            f"no chain dict may have a 'body' key when requester is omitted; "
            f"dicts with body: {bodies!r}",
        )

        party_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": MAINLINE},
        )
        party_chain = _data(party_result)
        party_dict_m = self._chain_by_id(party_chain, self.mid_m)
        self.assertIn(
            "body", party_dict_m,
            f"control case: passing requester={MAINLINE!r} (a party) must gain a "
            f"'body' key for #{self.mid_m}, in contrast to the omitted-requester "
            f"call above: {party_dict_m!r}",
        )


# ===========================================================================
# Subject-only messages never gain a body key
# ===========================================================================

class ThreadRequesterSubjectOnlyNeverGetsBodyTest(_McpThreadRequesterBase):

    async def test_subject_only_reply_has_no_body_key_even_for_valid_party_requester(self):
        """AC6/S3: 'Subject-only messages get no body key' — even with a valid
        requester that IS a party to the subject-only reply (Mainline - Demo is
        its sender), the reply's chain dict never gains 'body', while #m's dict
        (same requester, same call) DOES gain one — proving the distinction is
        per-message (has a body_path) and not an all-or-nothing flag.

        RED: ToolError (unknown 'requester' field) — the whole call fails before
        either distinction can be observed.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_reply, "requester": MAINLINE},
        )
        chain = _data(result)
        dict_m = self._chain_by_id(chain, self.mid_m)
        dict_reply = self._chain_by_id(chain, self.mid_reply)
        self.assertNotIn(
            "body", dict_reply,
            f"subject-only reply #{self.mid_reply} must never gain a 'body' key: "
            f"{dict_reply!r}",
        )
        self.assertIn(
            "body", dict_m,
            f"#{self.mid_m} (same call, has a real body, requester is a party) "
            f"must gain 'body': {dict_m!r}",
        )


# ===========================================================================
# Invalid requester -> ToolError
# ===========================================================================

class ThreadRequesterInvalidAddressTest(_McpThreadRequesterBase):

    async def test_malformed_requester_raises_toolerror_with_validate_address_message(self):
        """S3: 'An invalid requester raises ToolError with the validate_address
        message.' A malformed address string fails sandesh_db.validate_address's
        format check: 'bad address {addr!r}: expected ...'.

        RED: currently a ToolError too (unknown 'requester' field), but with the
        wrong message ('Unknown tool'/unexpected-field wording, not the library's
        bad-address wording) -> the message assertions below fail.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_thread",
                {"msg_id": self.mid_m, "requester": "not an address"},
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "not an address", err_msg,
            f"ToolError must carry validate_address's bad-address message "
            f"naming the malformed requester; got: {err_msg!r}",
        )
        self.assertIn(
            "expected '<Orchestrator> - <Project>'", err_msg,
            f"ToolError must carry validate_address's expected-format wording; "
            f"got: {err_msg!r}",
        )

    async def test_requester_well_formed_different_project_address_raises_toolerror(self):
        """AC6 (authoritative — see file-level ESCALATION docstring):
        requester='Mainline - Other' (well-formed, registered, but not of the
        thread's project) -> ToolError.

        RED: currently a ToolError too (unknown 'requester' field, since the
        parameter does not exist yet) — but for the wrong reason: the message
        does not name the 'Other' project the way validate_address's
        project-mismatch branch does, so the message assertion below fails
        until requester-project validation is implemented.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_thread",
                {"msg_id": self.mid_m, "requester": MAINLINE_OTHER},
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "Other", err_msg,
            f"ToolError for a well-formed but wrong-project requester must name "
            f"the project 'Other'; got: {err_msg!r}",
        )


# ===========================================================================
# list_tools() — requester param declared, described; tool count unchanged
# ===========================================================================

class ThreadRequesterToolSchemaTest(_McpThreadRequesterBase):

    def _tool_by_name(self, tools, name):
        for t in tools:
            if t.name == name:
                return t
        self.fail(f"Tool '{name}' not found in list_tools()")

    def _param_desc(self, tool, param_name):
        schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
        props = schema.get("properties", {})
        return props.get(param_name, {}).get("description", None)

    async def test_sandesh_thread_schema_declares_requester_with_description(self):
        """AC6/S3: sandesh_thread's inputSchema gains a 'requester' property with a
        non-empty description.

        RED: 'requester' is absent from sandesh_thread's current inputSchema
        (only project_id and msg_id are declared).
        """
        tools = await mcp_server.mcp.list_tools()
        thread_tool = self._tool_by_name(tools, "sandesh_thread")
        desc = self._param_desc(thread_tool, "requester")
        self.assertIsNotNone(
            desc,
            "sandesh_thread.inputSchema['properties']['requester']['description'] "
            "is missing",
        )
        self.assertIsInstance(desc, str, "'requester' description must be a string")
        self.assertGreater(
            len(desc.strip()), 0,
            "sandesh_thread 'requester' param description must not be empty",
        )

    async def test_tool_count_stays_at_twelve_tools(self):
        """Adding the 'requester' parameter to an existing tool must not change the
        tool COUNT (still 12 tools total — no new tool, just a new parameter).

        This is a contract pin, not expected to be RED by itself (the parameter
        is new, not a new tool) — included so a future regression that
        accidentally duplicates sandesh_thread as a new tool is caught.
        """
        tools = await mcp_server.mcp.list_tools()
        self.assertEqual(
            len(tools), 12,
            f"Expected exactly 12 tools (unchanged by the new 'requester' param), "
            f"got {len(tools)}: {sorted(t.name for t in tools)}",
        )


if __name__ == "__main__":
    unittest.main()
