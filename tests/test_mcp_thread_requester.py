"""test_mcp_thread_requester.py — RED tests for CR-SAN-054 Cycle 3 (\u00a7S3, AC6).

Covers the MCP `sandesh_thread` tool gaining an optional `requester: str | None`
parameter: with a valid requester that is a party (sender or recipient) of a
message that has a body, the returned chain dict for that message gains a
`body` key with the full text. Subject-only messages never gain `body`. A
non-party valid requester, or no requester at all, leaves the result without
any `body` keys.

Amended (owner ruling 2026-10-07, spec commit 3c3ae9b): validation mirrors the
CLI's `--as` + `--project` \u2014 the existing, previously-unused `project_id`
parameter becomes the project check. With `project_id` set, `requester` must
pass `sandesh_db.validate_address(requester, project_id)`; a failure (bad
format, or a well-formed address in a different project) raises `ToolError`
with the `validate_address` message. Without `project_id`, only the address
FORMAT is checked \u2014 a well-formed requester in a different project is
accepted with no error, and (being a non-party) sees no `body`. The project is
never derived from the thread itself.

  python-crucible.py test --tests tests.test_mcp_thread_requester --agent CR-SAN-054-C3-RED

Expected RED (confirmed by the actual first run \u2014 see below): FastMCP's
`call_tool` does NOT reject an unknown `requester` kwarg on `sandesh_thread`
(unlike `sandesh_inbox`'s `sender_project`, whose rejection other sibling
tests rely on) \u2014 the extra field is silently ignored, so every call that
passes `requester=...` today returns the SAME `thread()` output as if
`requester` had been omitted: no `body` key anywhere. This makes the
"body present for a party" tests (and the two merged withheld/omitted
control-case assertions) fail for the right reason \u2014 the missing `body` key
\u2014 and the malformed-address ToolError test fails because no `ToolError` is
raised at all yet (`validate_address` on `requester` is never reached). The
project-mismatch ToolError test fails for the same reason (no error raised at
all, since `project_id` is still unused), and the no-project_id control test
fails on its party-requester control assertion (no `body` key disclosed yet).
The schema test fails because `requester` is absent from `sandesh_thread`'s
`inputSchema`.
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

    async def test_requester_well_formed_different_project_with_project_id_raises_toolerror(self):
        """AC6, amended 2026-10-07 owner ruling (\u00a7S3): requester='Mainline - Other'
        with project_id='Demo' -> ToolError naming the project mismatch
        (validate_address's project-mismatch branch: "address project 'Other' !=
        project_id 'Demo'").

        RED: currently NO ToolError is raised at all (project_id is still unused
        by sandesh_thread, and the unknown 'requester' field is silently ignored),
        so assertRaises(ToolError) itself fails to catch anything \u2014 the call
        succeeds and returns a chain instead.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_thread",
                {"msg_id": self.mid_m, "requester": MAINLINE_OTHER, "project_id": PROJ},
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "!= project_id", err_msg,
            f"ToolError for a well-formed requester outside project_id must carry "
            f"validate_address's project-mismatch wording; got: {err_msg!r}",
        )
        self.assertIn(
            repr(PROJ), err_msg,
            f"ToolError must name the project_id {PROJ!r} it was checked against; "
            f"got: {err_msg!r}",
        )

    async def test_requester_well_formed_different_project_without_project_id_no_error_no_body(self):
        """AC6, amended 2026-10-07 owner ruling (\u00a7S3): requester='Mainline - Other'
        with NO project_id -> no error, and \u2014 since Mainline - Other is not a
        party to #m \u2014 no chain dict gains a 'body' key. Paired, in the SAME
        test, with the positive party-requester control case (requester=Mainline -
        Demo) so this is not a vacuous pre-feature pass: it fails today on the
        control assertion, proving the body-disclosure mechanism itself is not
        implemented yet.

        RED: the non-party/no-project_id call raises no error today either (the
        'requester' field is silently ignored) so that half coincidentally holds,
        but the paired control assertion fails \u2014 'body' is absent for the party
        requester \u2014 because no body-disclosure logic exists yet.
        """
        other_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": MAINLINE_OTHER},
        )
        other_chain = _data(other_result)
        bodies = [d["id"] for d in other_chain if "body" in d]
        self.assertEqual(
            bodies, [],
            f"a well-formed, non-party, cross-project requester with no project_id "
            f"must raise no error and see no 'body' key anywhere; dicts with body: "
            f"{bodies!r}",
        )

        party_result = await mcp_server.mcp.call_tool(
            "sandesh_thread",
            {"msg_id": self.mid_m, "requester": MAINLINE},
        )
        party_chain = _data(party_result)
        party_dict_m = self._chain_by_id(party_chain, self.mid_m)
        self.assertIn(
            "body", party_dict_m,
            f"control case: party requester {MAINLINE!r} must see a 'body' key "
            f"for #{self.mid_m}, in contrast to the non-party call above: "
            f"{party_dict_m!r}",
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
