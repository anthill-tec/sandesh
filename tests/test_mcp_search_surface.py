"""test_mcp_search_surface.py — RED tests for CR-SAN-028 Cycle 1.

Covers AC1–AC5 (in-process MCP harness; no stdio E2E — that is AC6/C2):

  AC1 — tool count + names: exactly 12 tools, sandesh_search present,
         readOnlyHint=True on sandesh_search, no forbidden names.
  AC2 — filtered inbox via MCP: sandesh_inbox gains sender_project (and other
         filter params); filtered call returns only matching rows; unfiltered
         returns the superset.
  AC3 — search via MCP: sandesh_search(recipient, query) returns hits with
         envelope + snippet and correct total; offset pages; results identical
         with and without an explicit project_id.
  AC4 — boundary + errors: search for another recipient's exclusive mail returns
         0 hits; malformed FTS5 query → ToolError (not a crash); read-state
         untouched after MCP search.
  AC5 — docs: SANDESH_INSTRUCTIONS and usage resource contain proxy-stream +
         search/pagination markers.

Expected RED:
  AC1 — list_tools() returns 11 (not 12); sandesh_search absent; the existing
         test_mcp_lifecycle_tools.py "exactly 11" assertion is also updated here.
  AC2 — sandesh_inbox call with sender_project kwarg → pydantic unknown-field
         ToolError (param not yet exposed on the MCP tool).
  AC3 — sandesh_search call → ToolError("Unknown tool: sandesh_search").
  AC4 — same; malformed-query test similarly fails at "unknown tool" before the
         error-mapping path is exercised.
  AC5 — assertIn("proxy" / "search" / "pagination") fails on the current text.

Harness style mirrors test_mcp_read_tools.py and test_mcp_lifecycle_tools.py.
"""

import json
import os
import shutil
import tempfile
import unittest

from sandesh import sandesh_db as sdb
from sandesh import mcp_server
from mcp.server.fastmcp.exceptions import ToolError


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

PROJ1 = "SearchP1"
PROJ2 = "SearchP2"

ML_P1 = "Mainline - SearchP1"
T1_P1 = "Track 1 - SearchP1"
ML_P2 = "Mainline - SearchP2"


# ---------------------------------------------------------------------------
# Result unwrapping helpers (same pattern as sibling test files)
# ---------------------------------------------------------------------------

def _data(result):
    """Unwrap FastMCP.call_tool return for list/dict-returning tools."""
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


def _unwrap_search(result):
    """Unwrap a sandesh_search call_tool result → the dict {hits, total, limit, offset}.

    FastMCP serialises a dict return as (list[TextContent], {"result": {...}}).
    """
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        # Fall back: first TextContent carries JSON
        if content:
            text = getattr(content[0], "text", content[0])
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    # Bare list of TextContent (older fastmcp)
    if isinstance(result, list) and result:
        text = getattr(result[0], "text", result[0])
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return result


# ---------------------------------------------------------------------------
# Fixture base — two projects, cross-project grant, seeded corpus
# ---------------------------------------------------------------------------

class _McpSearchBase(unittest.IsolatedAsyncioTestCase):
    """Base fixture: two active projects (P1 + P2), admin + cross-project grant.

    Corpus seeded in setUp (all via sdb.send so FTS rows are indexed):

      mid_p1_a  — T1_P1 → ML_P1, subject='deployment pipeline check', kind='request'
                  body='pipeline automation review needed'
                  (unique term: 'pipelineautomation')
      mid_p1_b  — T1_P1 → ML_P1, subject='gateway timeout error', kind='fyi'
                  body='encountered gateway timeout during load test'
                  (unique term: 'gatewaytimeout')
      mid_p2_x  — ML_P2 → ML_P1 (cross-project), subject='p2 proxy stream update', kind='fyi'
                  body='sender project proxy stream handoff complete'
                  (unique term: 'proxystreamhandoff')
      mid_t1_only — ML_P1 → T1_P1 ONLY (ML_P1 is NOT a recipient)
                   subject='track internal task', kind='directive'
                   body='exclusive track term trackinternaltask here'
                   (unique term: 'trackinternaltask')
    """

    # Unique search terms (no collisions across the corpus)
    TERM_P1_A   = "pipelineautomation"
    TERM_P1_B   = "gatewaytimeout"
    TERM_P2     = "proxystreamhandoff"
    TERM_T1ONLY = "trackinternaltask"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-search-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        # Provision both projects.
        sdb.setup(PROJ1)
        sdb.setup(PROJ2)

        self.store1 = sdb.store_dir(PROJ1)
        self.store2 = sdb.store_dir(PROJ2)

        self.con = sdb.connect()

        # Register addresses.
        sdb.register(self.con, ML_P1, kind="mainline", project=PROJ1)
        sdb.register(self.con, T1_P1, kind="track",    project=PROJ1)
        sdb.register(self.con, ML_P2, kind="mainline", project=PROJ2)

        # Admin + cross-project grant so P2 can send to P1.
        sdb.assign_admin(self.con, "ops")
        sdb.grant_xproj(self.con, PROJ1, "ops")
        sdb.grant_xproj(self.con, PROJ2, "ops")

        # Seed corpus.
        self.mid_p1_a = sdb.send(
            self.con, self.store1,
            from_addr=T1_P1,
            to=[ML_P1],
            subject="deployment pipeline check",
            kind="request",
            body_text=f"body: {self.TERM_P1_A} review needed",
        )
        self.mid_p1_b = sdb.send(
            self.con, self.store1,
            from_addr=T1_P1,
            to=[ML_P1],
            subject="gateway timeout error",
            kind="fyi",
            body_text=f"encountered {self.TERM_P1_B} during load test",
        )
        self.mid_p2_x = sdb.send(
            self.con, self.store2,
            from_addr=ML_P2,
            to=[ML_P1],
            subject="p2 proxy stream update",
            kind="fyi",
            body_text=f"sender project {self.TERM_P2} complete",
        )
        # T1-only: ML_P1 is the SENDER, T1_P1 is the ONLY recipient.
        # ML_P1 is NOT in message_recipient for this message.
        self.mid_t1_only = sdb.send(
            self.con, self.store1,
            from_addr=ML_P1,
            to=[T1_P1],
            subject="track internal task",
            kind="directive",
            body_text=f"exclusive {self.TERM_T1ONLY} here",
        )

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass
        for k, v in (("XDG_DATA_HOME", self._prev_xdg),
                     ("SANDESH_PROJECT", self._prev_proj)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tool_by_name(self, tools, name):
        for t in tools:
            if t.name == name:
                return t
        self.fail(f"Tool '{name}' not found in list_tools()")

    def _fresh_con(self):
        return sdb.connect()


# ===========================================================================
# AC1 — tool count + names + sandesh_search annotation
# ===========================================================================

class Ac1ToolInventoryTest(_McpSearchBase):
    """AC1: list_tools() returns exactly 12 tools; sandesh_search is present with
    readOnlyHint=True; no tool name contains 'reindex', 'tombstone', 'grant',
    'revoke', or 'admin'.

    RED: list_tools() currently returns 11 tools (sandesh_search absent).
    The existing test_mcp_lifecycle_tools.py 'exactly 11' assertion is updated
    below to pin the new expected count of 12.
    """

    async def test_ac1_list_tools_returns_exactly_twelve_tools(self):
        """AC1: list_tools() must return exactly 12 tools (11 existing + sandesh_search).

        RED: currently returns 11 — sandesh_search is not yet added.
        """
        tools = await mcp_server.mcp.list_tools()
        names = sorted(t.name for t in tools)
        self.assertEqual(
            len(tools), 12,
            f"Expected exactly 12 tools, got {len(tools)}: {names}",
        )

    async def test_ac1_sandesh_search_present_in_tool_list(self):
        """AC1: sandesh_search must appear in list_tools().

        RED: AttributeError / ToolError — tool not yet defined.
        """
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        self.assertIn(
            "sandesh_search", names,
            f"sandesh_search missing from list_tools(): {sorted(names)}",
        )

    async def test_ac1_sandesh_search_has_readonly_hint_true(self):
        """AC1: sandesh_search.annotations.readOnlyHint must be True
        (search never marks messages read — pure query).

        RED: tool absent; annotation test would also fail even if present without it.
        """
        tools = await mcp_server.mcp.list_tools()
        tool = self._tool_by_name(tools, "sandesh_search")
        self.assertIsNotNone(
            tool.annotations,
            "sandesh_search must have annotations (not None)",
        )
        self.assertIs(
            getattr(tool.annotations, "readOnlyHint", None), True,
            f"sandesh_search.annotations.readOnlyHint must be True; "
            f"got: {getattr(tool.annotations, 'readOnlyHint', None)!r}",
        )

    async def test_ac1_no_tool_name_contains_reindex(self):
        """AC1: no tool name contains 'reindex' (maintenance is CLI/installer-only).

        RED: tool absent (no reindex tool expected either way); pinning the contract.
        """
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "reindex" in t.name]
        self.assertEqual(
            forbidden, [],
            f"No tool name must contain 'reindex': {forbidden}",
        )

    async def test_ac1_no_tool_name_contains_tombstone(self):
        """AC1: no tool name contains 'tombstone'."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "tombstone" in t.name]
        self.assertEqual(forbidden, [], f"Forbidden tool names (tombstone): {forbidden}")

    async def test_ac1_no_tool_name_contains_grant(self):
        """AC1: no tool name contains 'grant'."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "grant" in t.name]
        self.assertEqual(forbidden, [], f"Forbidden tool names (grant): {forbidden}")

    async def test_ac1_no_tool_name_contains_revoke(self):
        """AC1: no tool name contains 'revoke'."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "revoke" in t.name]
        self.assertEqual(forbidden, [], f"Forbidden tool names (revoke): {forbidden}")

    async def test_ac1_no_tool_name_contains_admin(self):
        """AC1: no tool name contains 'admin'."""
        tools = await mcp_server.mcp.list_tools()
        forbidden = [t.name for t in tools if "admin" in t.name]
        self.assertEqual(forbidden, [], f"Forbidden tool names (admin): {forbidden}")

    async def test_ac1_all_eleven_existing_tools_still_present(self):
        """AC1 regression pin: all 11 tools from CR-SAN-025 must still be present.

        This replaces the 'exactly 11' assertion in test_mcp_lifecycle_tools.py with
        a membership check (the exact count is now 12, not 11).
        """
        tools = await mcp_server.mcp.list_tools()
        names = {t.name for t in tools}
        expected_11 = {
            "sandesh_setup",
            "sandesh_addressbook",
            "sandesh_inbox",
            "sandesh_fetch",
            "sandesh_thread",
            "sandesh_register",
            "sandesh_unregister",
            "sandesh_send",
            "sandesh_reply",
            "sandesh_archive",
            "sandesh_unarchive",
        }
        missing = sorted(expected_11 - names)
        self.assertEqual(
            missing, [],
            f"Existing 11 tools must all still be present; missing: {missing}",
        )


# ===========================================================================
# AC2 — filtered inbox via MCP (sandesh_inbox gains filter params)
# ===========================================================================

class Ac2FilteredInboxTest(_McpSearchBase):
    """AC2: sandesh_inbox grows sender_project (and the full filter set from
    CR-SAN-026). Filtered call returns only P2-sender rows; unfiltered call
    returns the superset (includes both P1 and P2 rows).

    RED: calling sandesh_inbox with sender_project raises a ToolError because
    the MCP tool does not yet declare that parameter.
    """

    async def test_ac2_inbox_filtered_by_sender_project_returns_only_p2_rows(self):
        """AC2: sandesh_inbox(recipient=ML_P1, sender_project=PROJ2) returns ONLY
        P2-sender rows (mid_p2_x); P1 rows must NOT appear.

        RED: ToolError — sandesh_inbox does not yet accept sender_project.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ1,
                "recipient": ML_P1,
                "unread_only": False,
                "sender_project": PROJ2,
            },
        )
        rows = _data(result)
        self.assertIsInstance(rows, list)
        ids = [r["id"] for r in rows]

        self.assertIn(
            self.mid_p2_x, ids,
            f"mid_p2_x (P2 sender) must appear with sender_project={PROJ2!r}; got ids={ids!r}",
        )
        # P1-sender rows must NOT appear.
        self.assertNotIn(
            self.mid_p1_a, ids,
            f"mid_p1_a (P1 sender) must not appear with sender_project={PROJ2!r}",
        )
        self.assertNotIn(
            self.mid_p1_b, ids,
            f"mid_p1_b (P1 sender) must not appear with sender_project={PROJ2!r}",
        )
        # Exactly one row (only mid_p2_x is a P2 sender to ML_P1).
        self.assertEqual(
            len(ids), 1,
            f"sender_project={PROJ2!r} filter must return exactly 1 row; "
            f"got {len(ids)}: {ids!r}",
        )

    async def test_ac2_inbox_unfiltered_returns_superset(self):
        """AC2: unfiltered sandesh_inbox(recipient=ML_P1, unread_only=False)
        returns more rows than the sender_project-filtered call — it IS the superset.

        RED: calls succeed at different counts only once both paths work; the
        filtered call RED is the primary failure here.
        """
        unfiltered = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {"project_id": PROJ1, "recipient": ML_P1, "unread_only": False},
        )
        filtered = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ1,
                "recipient": ML_P1,
                "unread_only": False,
                "sender_project": PROJ2,
            },
        )
        unfiltered_ids = {r["id"] for r in _data(unfiltered)}
        filtered_ids   = {r["id"] for r in _data(filtered)}

        # The filtered set must be a strict subset of the unfiltered set.
        self.assertLess(
            filtered_ids, unfiltered_ids,
            f"Filtered result must be a strict subset of unfiltered result; "
            f"filtered={sorted(filtered_ids)!r}, unfiltered={sorted(unfiltered_ids)!r}",
        )
        # Specifically: mid_p2_x is in both; mid_p1_a/b are only in unfiltered.
        self.assertIn(
            self.mid_p2_x, unfiltered_ids,
            f"mid_p2_x must appear in unfiltered inbox",
        )
        self.assertIn(
            self.mid_p1_a, unfiltered_ids,
            f"mid_p1_a (P1) must appear in unfiltered inbox",
        )
        self.assertNotIn(
            self.mid_p1_a, filtered_ids,
            f"mid_p1_a (P1) must NOT appear in sender_project={PROJ2!r} filtered inbox",
        )

    async def test_ac2_inbox_filtered_by_kind_returns_only_request_rows(self):
        """AC2: sandesh_inbox(recipient=ML_P1, kind='request') returns ONLY kind=request rows.

        mid_p1_a has kind='request'; mid_p1_b has kind='fyi'; mid_p2_x has kind='fyi'.
        Only mid_p1_a must appear.

        RED: ToolError — sandesh_inbox does not yet accept kind.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ1,
                "recipient": ML_P1,
                "unread_only": False,
                "kind": "request",
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]

        self.assertIn(
            self.mid_p1_a, ids,
            f"mid_p1_a (kind=request) must appear with kind='request' filter; got ids={ids!r}",
        )
        self.assertNotIn(
            self.mid_p1_b, ids,
            f"mid_p1_b (kind=fyi) must NOT appear with kind='request' filter",
        )
        self.assertNotIn(
            self.mid_p2_x, ids,
            f"mid_p2_x (kind=fyi) must NOT appear with kind='request' filter",
        )
        self.assertEqual(
            len(ids), 1,
            f"kind='request' filter must return exactly 1 row; got {len(ids)}: {ids!r}",
        )

    async def test_ac2_inbox_filtered_by_subject_like_returns_matching_rows(self):
        """AC2: sandesh_inbox(recipient=ML_P1, subject_like='gateway') matches
        'gateway timeout error' (mid_p1_b) only.

        RED: ToolError — sandesh_inbox does not yet accept subject_like.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ1,
                "recipient": ML_P1,
                "unread_only": False,
                "subject_like": "gateway",
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]

        self.assertIn(
            self.mid_p1_b, ids,
            f"mid_p1_b ('gateway timeout error') must appear with subject_like='gateway'; "
            f"got ids={ids!r}",
        )
        self.assertNotIn(
            self.mid_p1_a, ids,
            f"mid_p1_a ('deployment pipeline check') must NOT appear with subject_like='gateway'",
        )
        self.assertEqual(
            len(ids), 1,
            f"subject_like='gateway' must return exactly 1 row; got {len(ids)}: {ids!r}",
        )

    async def test_ac2_fetch_filtered_by_sender_project_marks_only_p2_mail(self):
        """AC2: sandesh_fetch(recipient=ML_P1, sender_project=PROJ2, mark=True) returns
        and marks ONLY mid_p2_x; mid_p1_a and mid_p1_b remain unread.

        RED: ToolError — sandesh_fetch does not yet accept sender_project.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_fetch",
            {
                "project_id": PROJ1,
                "recipient": ML_P1,
                "mark": True,
                "sender_project": PROJ2,
            },
        )
        items = _data(result)
        item_ids = [it["id"] for it in items]

        # Only mid_p2_x must be returned.
        self.assertIn(
            self.mid_p2_x, item_ids,
            f"mid_p2_x must be returned by filtered fetch(sender_project={PROJ2!r}); "
            f"got item_ids={item_ids!r}",
        )
        self.assertNotIn(
            self.mid_p1_a, item_ids,
            f"mid_p1_a must NOT be returned by filtered fetch(sender_project={PROJ2!r})",
        )
        self.assertEqual(
            len(item_ids), 1,
            f"Filtered fetch(sender_project={PROJ2!r}) must return exactly 1 item; "
            f"got {len(item_ids)}: {item_ids!r}",
        )

        # mid_p1_a and mid_p1_b must still be unread (read_at=NULL).
        con = self._fresh_con()
        try:
            for mid, label in [(self.mid_p1_a, "mid_p1_a"), (self.mid_p1_b, "mid_p1_b")]:
                row = con.execute(
                    "SELECT read_at FROM message_recipient "
                    "WHERE message_id=? AND recipient=?",
                    (mid, ML_P1),
                ).fetchone()
                self.assertIsNone(
                    row["read_at"],
                    f"{label} must remain unread after filtered fetch(sender_project={PROJ2!r}); "
                    f"got read_at={row['read_at']!r}",
                )
        finally:
            con.close()


# ===========================================================================
# AC3 — sandesh_search via MCP
# ===========================================================================

class Ac3SearchToolTest(_McpSearchBase):
    """AC3: sandesh_search(recipient, query) returns {hits, total, limit, offset}
    with envelope + snippet; offset pages; project_id accepted-and-unused
    (results identical with/without it).

    RED: ToolError("Unknown tool: sandesh_search") on every call.
    """

    async def test_ac3_search_returns_hit_with_envelope_and_snippet(self):
        """AC3: search for TERM_P1_A returns mid_p1_a with required envelope keys
        and a non-empty snippet.

        RED: ToolError — sandesh_search not yet defined.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P1_A},
        )
        data = _unwrap_search(result)

        self.assertIsInstance(data, dict, f"sandesh_search must return a dict; got {type(data)}")
        self.assertIn("hits",   data, f"result must have 'hits' key; got {sorted(data.keys())!r}")
        self.assertIn("total",  data, f"result must have 'total' key")
        self.assertIn("limit",  data, f"result must have 'limit' key")
        self.assertIn("offset", data, f"result must have 'offset' key")

        self.assertEqual(
            data["total"], 1,
            f"TERM_P1_A is unique to mid_p1_a; total must be 1; got {data['total']!r}",
        )
        self.assertEqual(len(data["hits"]), 1, "hits list must have 1 entry")

        hit = data["hits"][0]
        self.assertEqual(
            hit["id"], self.mid_p1_a,
            f"hit id must be mid_p1_a={self.mid_p1_a}; got {hit['id']!r}",
        )
        # Envelope keys: id, from, subject, kind, created_at, role, snippet
        for key in ("id", "from", "subject", "kind", "created_at", "role", "snippet"):
            self.assertIn(key, hit, f"hit must contain envelope key {key!r}; got {sorted(hit.keys())!r}")

        # Snippet must be non-empty and contain the term.
        snippet = hit["snippet"]
        self.assertIsInstance(snippet, str, f"snippet must be a string; got {type(snippet)}")
        self.assertGreater(len(snippet.strip()), 0, "snippet must not be empty")
        self.assertIn(
            self.TERM_P1_A.lower(), snippet.lower(),
            f"snippet must contain the search term {self.TERM_P1_A!r}; got {snippet!r}",
        )

    async def test_ac3_search_total_reflects_correct_match_count(self):
        """AC3: search for TERM_P1_B returns total=1 (only mid_p1_b matches).

        RED: ToolError — sandesh_search not yet defined.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P1_B},
        )
        data = _unwrap_search(result)
        self.assertEqual(
            data["total"], 1,
            f"TERM_P1_B unique to mid_p1_b; total must be 1; got {data['total']!r}",
        )
        ids = [h["id"] for h in data["hits"]]
        self.assertIn(
            self.mid_p1_b, ids,
            f"mid_p1_b must be in hits for TERM_P1_B; got ids={ids!r}",
        )

    async def test_ac3_search_offset_pages_correctly(self):
        """AC3: offset paging — seed 5 messages sharing a common term; page with
        limit=3, offset=0 returns 3 hits, total=5; limit=3, offset=3 returns 2 hits,
        total=5; offset=6 returns 0 hits, total=5.

        RED: ToolError — sandesh_search not yet defined.
        """
        PAGE_TERM = "paginatecheck"
        page_ids = []
        for i in range(5):
            mid = sdb.send(
                self.con, self.store1,
                from_addr=T1_P1,
                to=[ML_P1],
                subject=f"page item {i}",
                body_text=f"body {PAGE_TERM} item {i}",
            )
            page_ids.append(mid)

        # Page 1
        r1 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": PAGE_TERM, "limit": 3, "offset": 0},
        ))
        self.assertEqual(r1["total"], 5, f"total must be 5; got {r1['total']!r}")
        self.assertEqual(len(r1["hits"]), 3, f"page 1 must have 3 hits; got {len(r1['hits'])}")
        self.assertEqual(r1["limit"], 3)
        self.assertEqual(r1["offset"], 0)

        # Page 2
        r2 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": PAGE_TERM, "limit": 3, "offset": 3},
        ))
        self.assertEqual(r2["total"], 5, f"total must be 5 on page 2; got {r2['total']!r}")
        self.assertEqual(len(r2["hits"]), 2, f"page 2 must have 2 hits; got {len(r2['hits'])}")
        self.assertEqual(r2["offset"], 3)

        # Page 3 (beyond corpus)
        r3 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": PAGE_TERM, "limit": 3, "offset": 6},
        ))
        self.assertEqual(r3["total"], 5, f"total must be 5 at offset=6; got {r3['total']!r}")
        self.assertEqual(r3["hits"], [], f"hits must be [] at offset=6; got {r3['hits']!r}")

        # Pages have no overlap.
        ids1 = {h["id"] for h in r1["hits"]}
        ids2 = {h["id"] for h in r2["hits"]}
        self.assertEqual(ids1 & ids2, set(), f"pages must not overlap; overlap={ids1 & ids2!r}")

        # Combined pages cover all 5 corpus ids.
        self.assertEqual(
            ids1 | ids2, set(page_ids),
            f"page1+page2 must cover all 5 ids; "
            f"missing={set(page_ids) - (ids1 | ids2)!r}",
        )

    async def test_ac3_search_result_identical_with_and_without_project_id(self):
        """AC3: project_id is accepted-and-unused — results must be identical
        whether project_id is provided or omitted.

        RED: ToolError — sandesh_search not yet defined.
        """
        result_no_proj = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P1_A},
        )
        result_with_proj = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P1_A, "project_id": PROJ1},
        )
        data_no   = _unwrap_search(result_no_proj)
        data_with = _unwrap_search(result_with_proj)

        self.assertEqual(
            data_no["total"], data_with["total"],
            f"total must be identical with/without project_id; "
            f"no_proj={data_no['total']!r}, with_proj={data_with['total']!r}",
        )
        ids_no   = [h["id"] for h in data_no["hits"]]
        ids_with = [h["id"] for h in data_with["hits"]]
        self.assertEqual(
            ids_no, ids_with,
            f"hit ids must be identical with/without project_id; "
            f"no_proj={ids_no!r}, with_proj={ids_with!r}",
        )

    async def test_ac3_search_sender_project_filters_results(self):
        """AC3: sandesh_search(recipient=ML_P1, query=TERM_P2, sender_project=PROJ2)
        returns only the P2-sender hit; the same query without sender_project also
        returns it (but sender_project=PROJ1 would return 0 since TERM_P2 came from P2).

        RED: ToolError — sandesh_search not yet defined.
        """
        # Without sender_project filter — mid_p2_x is visible (P2 mail is accessible).
        r_all = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P2},
        ))
        ids_all = [h["id"] for h in r_all["hits"]]
        self.assertIn(
            self.mid_p2_x, ids_all,
            f"mid_p2_x must appear in unfiltered search for TERM_P2; got ids={ids_all!r}",
        )

        # With sender_project=PROJ2 — same result (P2 sender is the only match anyway).
        r_p2 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P2, "sender_project": PROJ2},
        ))
        self.assertEqual(
            r_p2["total"], 1,
            f"sender_project={PROJ2!r} filter must return 1 hit for TERM_P2; "
            f"got total={r_p2['total']!r}",
        )

        # With sender_project=PROJ1 — TERM_P2 did NOT come from P1, so 0 hits.
        r_p1_filter = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P2, "sender_project": PROJ1},
        ))
        self.assertEqual(
            r_p1_filter["total"], 0,
            f"sender_project={PROJ1!r} filter must return 0 for TERM_P2 "
            f"(not sent by P1); got total={r_p1_filter['total']!r}",
        )


# ===========================================================================
# AC4 — boundary + errors
# ===========================================================================

class Ac4BoundaryAndErrorsTest(_McpSearchBase):
    """AC4: search for another recipient's exclusive mail returns 0 hits;
    malformed FTS5 query → ToolError (not a crash); read-state untouched
    after MCP search.

    RED: all paths fail with ToolError("Unknown tool: sandesh_search").
    """

    async def test_ac4_search_returns_zero_hits_for_other_recipients_exclusive_mail(self):
        """AC4: ML_P1 searching for TERM_T1ONLY returns 0 hits — mid_t1_only has
        T1_P1 as the only recipient; ML_P1 is the sender (not a recipient).

        RED: ToolError — sandesh_search not yet defined.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_T1ONLY},
        )
        data = _unwrap_search(result)
        self.assertEqual(
            data["total"], 0,
            f"ML_P1 must find total=0 for {self.TERM_T1ONLY!r} "
            f"(ML_P1 is not a recipient of mid_t1_only); got total={data['total']!r}",
        )
        self.assertEqual(
            data["hits"], [],
            f"ML_P1 hits must be [] for {self.TERM_T1ONLY!r}; got hits={data['hits']!r}",
        )

    async def test_ac4_track1_can_find_its_own_exclusive_term(self):
        """AC4 positive: T1_P1 CAN find TERM_T1ONLY (it IS a recipient of mid_t1_only).

        RED: ToolError — sandesh_search not yet defined.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": T1_P1, "query": self.TERM_T1ONLY},
        )
        data = _unwrap_search(result)
        self.assertGreater(
            data["total"], 0,
            f"T1_P1 must find at least 1 result for {self.TERM_T1ONLY!r}; "
            f"got total={data['total']!r}",
        )
        ids = [h["id"] for h in data["hits"]]
        self.assertIn(
            self.mid_t1_only, ids,
            f"mid_t1_only must be in T1_P1 search results for {self.TERM_T1ONLY!r}; "
            f"got ids={ids!r}",
        )

    async def test_ac4_malformed_fts5_query_raises_toolerror_not_crash(self):
        """AC4: a malformed FTS5 query (unbalanced quote) surfaces as a ToolError,
        not a raw traceback / OperationalError escape.

        The lib raises ValueError; mcp_server.py must map it to ToolError.

        RED: ToolError("Unknown tool: sandesh_search") — incorrect reason, but still RED.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_search",
                {"recipient": ML_P1, "query": '"unterminated'},
            )
        err = str(ctx.exception)
        # At GREEN the error must mention the query problem, not "unknown tool".
        # We assert it's a ToolError (any message) — the malformed-query content
        # is verified in the next test once the tool exists.
        self.assertIsNotNone(ctx.exception, "A ToolError must be raised for malformed FTS5")

    async def test_ac4_malformed_query_toolerror_message_mentions_query_problem(self):
        """AC4: the ToolError for a malformed FTS5 query must contain context about
        the query problem ('invalid' or 'query' or 'fts' or 'unterminated').

        RED: ToolError says 'Unknown tool: sandesh_search' — assertion fails because
        'invalid'/'query'/'fts'/'unterminated' not in that message.
        """
        with self.assertRaises(ToolError) as ctx:
            await mcp_server.mcp.call_tool(
                "sandesh_search",
                {"recipient": ML_P1, "query": '"unterminated'},
            )
        err_lower = str(ctx.exception).lower()
        problem_terms = ("invalid", "query", "fts", "unterminated")
        self.assertTrue(
            any(term in err_lower for term in problem_terms),
            f"ToolError message must mention the query problem "
            f"(one of {problem_terms}); got: {str(ctx.exception)!r}",
        )

    async def test_ac4_search_does_not_alter_read_state(self):
        """AC4: after sandesh_search, all message_recipient read_at values for
        ML_P1 must remain NULL (search never marks messages read).

        RED: ToolError — sandesh_search not yet defined.
        """
        await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_P1, "query": self.TERM_P1_A},
        )
        con = self._fresh_con()
        try:
            for mid, label in [
                (self.mid_p1_a, "mid_p1_a"),
                (self.mid_p1_b, "mid_p1_b"),
                (self.mid_p2_x, "mid_p2_x"),
            ]:
                row = con.execute(
                    "SELECT read_at FROM message_recipient "
                    "WHERE message_id=? AND recipient=?",
                    (mid, ML_P1),
                ).fetchone()
                self.assertIsNotNone(
                    row,
                    f"message_recipient row for {label} + ML_P1 must exist",
                )
                self.assertIsNone(
                    row["read_at"],
                    f"search must NOT mark {label} read; got read_at={row['read_at']!r}",
                )
        finally:
            con.close()

    async def test_ac4_operator_only_malformed_query_raises_toolerror(self):
        """AC4: bare AND operator (no operands) is also malformed FTS5.

        RED: ToolError("Unknown tool: sandesh_search") — tool absent.
        """
        with self.assertRaises(ToolError):
            await mcp_server.mcp.call_tool(
                "sandesh_search",
                {"recipient": ML_P1, "query": "AND"},
            )

    async def test_ac4_valid_query_with_no_matches_does_not_raise(self):
        """AC4: a valid FTS5 query that matches nothing returns {hits:[], total:0, ...}
        without raising.

        RED: ToolError — sandesh_search not yet defined.
        """
        try:
            result = await mcp_server.mcp.call_tool(
                "sandesh_search",
                {"recipient": ML_P1, "query": "xyzzy_no_matches_ever"},
            )
        except ToolError as e:
            self.fail(
                f"A valid no-match query must not raise ToolError; got: {e}"
            )
        data = _unwrap_search(result)
        self.assertIsInstance(data, dict)
        self.assertEqual(
            data.get("total", -1), 0,
            f"No-match query must return total=0; got {data.get('total')!r}",
        )
        self.assertEqual(
            data.get("hits", None), [],
            f"No-match query must return hits=[]; got {data.get('hits')!r}",
        )


# ===========================================================================
# AC5 — agent-facing docs (SANDESH_INSTRUCTIONS + usage resource)
# ===========================================================================

class Ac5DocsMarkersTest(unittest.IsolatedAsyncioTestCase):
    """AC5: SANDESH_INSTRUCTIONS and the sandesh://usage resource must contain
    the proxy-stream + search/pagination content.

    Marker checks (case-insensitive substrings):
      SANDESH_INSTRUCTIONS must contain:
        - 'proxy' (the sender-project proxy-stream use case)
        - 'search' (FTS5 search capability)
        - 'pagination' OR 'offset' (pagination guidance)
      usage resource must contain:
        - 'proxy' OR 'sender_project' (proxy-stream mention)
        - 'search' (sandesh_search tool or FTS mention)
        - 'pagination' OR 'offset' (pagination guidance)

    RED: the current SANDESH_INSTRUCTIONS and usage-scenarios.md do not yet
    contain these markers.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-docs-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_ac5_instructions_contains_proxy_stream_mention(self):
        """AC5: SANDESH_INSTRUCTIONS (lowercased) must contain 'proxy'.

        The proxy-stream use case (sender_project as a cross-project stream filter)
        must be described so agents know how to use the parameter.

        RED: current SANDESH_INSTRUCTIONS does not contain 'proxy'.
        """
        instructions = mcp_server.mcp.instructions or ""
        self.assertIn(
            "proxy",
            instructions.lower(),
            f"SANDESH_INSTRUCTIONS must mention 'proxy' (proxy-stream use case); "
            f"got (first 500 chars): {instructions[:500]!r}",
        )

    async def test_ac5_instructions_contains_search_mention(self):
        """AC5: SANDESH_INSTRUCTIONS (lowercased) must contain 'search'.

        Agents must know sandesh_search exists and what it does.

        RED: current SANDESH_INSTRUCTIONS does not mention 'search'.
        """
        instructions = mcp_server.mcp.instructions or ""
        self.assertIn(
            "search",
            instructions.lower(),
            f"SANDESH_INSTRUCTIONS must mention 'search'; "
            f"got (first 500 chars): {instructions[:500]!r}",
        )

    async def test_ac5_instructions_contains_pagination_or_offset(self):
        """AC5: SANDESH_INSTRUCTIONS (lowercased) must contain 'pagination' or 'offset'.

        Agents need to know search results are pageable.

        RED: current SANDESH_INSTRUCTIONS does not contain either term.
        """
        instructions = mcp_server.mcp.instructions or ""
        lowered = instructions.lower()
        self.assertTrue(
            "pagination" in lowered or "offset" in lowered,
            f"SANDESH_INSTRUCTIONS must mention 'pagination' or 'offset'; "
            f"got (first 500 chars): {instructions[:500]!r}",
        )

    async def test_ac5_usage_resource_contains_proxy_or_sender_project(self):
        """AC5: sandesh://usage resource (lowercased) must contain 'proxy' or
        'sender_project'.

        RED: current usage-scenarios.md does not contain 'sender_project' in the
        tool-reference section; proxy-stream use case not yet documented there.
        """
        resources = await mcp_server.mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        self.assertIn(
            "sandesh://usage", uris,
            f"sandesh://usage must be listed; got: {sorted(uris)!r}",
        )
        contents = list(await mcp_server.mcp.read_resource("sandesh://usage"))
        self.assertGreater(len(contents), 0, "usage resource must return content")
        text = contents[0].content if isinstance(contents[0].content, str) else ""
        lowered = text.lower()
        self.assertTrue(
            "proxy" in lowered or "sender_project" in lowered,
            f"sandesh://usage must mention 'proxy' or 'sender_project' "
            f"(proxy-stream use case); "
            f"got (first 400 chars): {text[:400]!r}",
        )

    async def test_ac5_usage_resource_contains_search_mention(self):
        """AC5: sandesh://usage resource (lowercased) must contain 'search'.

        RED: current usage-scenarios.md does not describe sandesh_search.
        """
        contents = list(await mcp_server.mcp.read_resource("sandesh://usage"))
        self.assertGreater(len(contents), 0, "usage resource must return content")
        text = contents[0].content if isinstance(contents[0].content, str) else ""
        self.assertIn(
            "search",
            text.lower(),
            f"sandesh://usage must mention 'search' (FTS / sandesh_search); "
            f"got (first 400 chars): {text[:400]!r}",
        )

    async def test_ac5_usage_resource_contains_pagination_or_offset(self):
        """AC5: sandesh://usage resource (lowercased) must contain 'pagination' or
        'offset'.

        RED: current usage-scenarios.md does not describe search pagination.
        """
        contents = list(await mcp_server.mcp.read_resource("sandesh://usage"))
        self.assertGreater(len(contents), 0, "usage resource must return content")
        text = contents[0].content if isinstance(contents[0].content, str) else ""
        lowered = text.lower()
        self.assertTrue(
            "pagination" in lowered or "offset" in lowered,
            f"sandesh://usage must mention 'pagination' or 'offset'; "
            f"got (first 400 chars): {text[:400]!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
