"""test_cleanup_sweep.py — RED tests for CR-SAN-030 (pre-release cleanup sweep).

Covers AC1–AC6:

  AC1 — imports: sandesh_db.py has no in-function `import sqlite3`; module-level
        import is present. (Currently 2 in-function occurrences at lines ~135/1062 → RED.)

  AC2 — docstrings: `_tombstone_guards.__doc__` mentions state checks precede the
        admin/authz check; `search.__doc__` names the `[`/`]` snippet markers and
        `…` elision. (Currently absent → RED.)

  AC3 — reindexed passthrough via MCP: fixture with empty message_fts but populated
        message table; first sandesh_search call returns `reindexed` truthy + correct
        hits; second call returns same hits with NO `reindexed` key.
        (Production complete since CR-SAN-027/028 — coverage addition. If it FAILS,
        stop and report verbatim; do not fix production.)

  AC4 — sender/since/until via MCP: sandesh_inbox filtered by sender=, since=,
        until= (including date-only until with end-of-day normalisation).
        (Production complete — coverage addition. Same stop-and-report rule.)

  AC5 — installer heredoc: install.sh has no `$'import os` escaped-blob opener;
        a heredoc python invocation marker (<<'PY') is present in the admin block.
        (Currently the `$'...'` blob exists → RED.)

  AC6 — warning-clean install suite: subprocess-run `PYTHONPATH=. .venv/bin/python
        tests/test_install.py` produces ZERO lines matching ResourceWarning AND the
        run reports OK. (~45 s runtime accepted; generous timeout.)
        (Currently 3+ ResourceWarning occurrences → RED.)

Run via crucible:
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_cleanup_sweep --agent CR-SAN-030-A-RED
"""

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Repo root + path bootstrap (mirror sibling test style)
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_SANDESH_DB_PATH = os.path.join(_REPO_ROOT, "sandesh", "sandesh_db.py")
_INSTALL_SH_PATH = os.path.join(_REPO_ROOT, "install.sh")
_TESTS_DIR = os.path.join(_REPO_ROOT, "tests")
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")

from sandesh import sandesh_db as sdb
from sandesh import mcp_server


# ---------------------------------------------------------------------------
# Unwrap helpers — mirror test_mcp_search_surface.py pattern
# ---------------------------------------------------------------------------

def _data(result):
    """Unwrap FastMCP.call_tool result for list/dict-returning tools."""
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
    """Unwrap a sandesh_search call_tool result → the dict {hits, total, limit, offset}."""
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        if content:
            text = getattr(content[0], "text", content[0])
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                pass
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
# AC1 — in-function import sqlite3 must not exist in sandesh_db.py
# ---------------------------------------------------------------------------

class Ac1NoInFunctionImportSqlite3Test(unittest.TestCase):
    """AC1: sandesh/sandesh_db.py must have NO line matching `^    import sqlite3`
    (indented in-function import). The module-level `import sqlite3` at line ~42
    covers all callers.

    RED: currently two in-function occurrences (~lines 135 and 1062).
    """

    def _read_source(self):
        self.assertTrue(
            os.path.isfile(_SANDESH_DB_PATH),
            f"sandesh_db.py not found at {_SANDESH_DB_PATH}",
        )
        with open(_SANDESH_DB_PATH, encoding="utf-8") as fh:
            return fh.readlines()

    def test_ac1_no_indented_import_sqlite3_lines(self):
        """No line in sandesh_db.py must match r'^\\s+import sqlite3' (in-function import).

        RED: two such lines exist in connect() (~line 135) and
        _consolidate_store() (~line 1062).
        """
        lines = self._read_source()
        offending = [
            (i + 1, ln.rstrip())
            for i, ln in enumerate(lines)
            if ln.startswith("    ") and ln.strip() == "import sqlite3"
        ]
        self.assertEqual(
            offending, [],
            f"sandesh_db.py contains {len(offending)} in-function `import sqlite3` "
            f"line(s) — the module-level import (line ~42) covers all callers:\n"
            + "\n".join(f"  line {lineno}: {text!r}" for lineno, text in offending),
        )

    def test_ac1_module_level_import_sqlite3_present(self):
        """The module-level `import sqlite3` must still be present (line not deleted).

        Guards against accidentally removing the import entirely.
        """
        lines = self._read_source()
        module_level = [
            (i + 1, ln.rstrip())
            for i, ln in enumerate(lines)
            if ln.strip() == "import sqlite3" and not ln.startswith("    ")
        ]
        self.assertGreater(
            len(module_level), 0,
            "sandesh_db.py must retain a module-level `import sqlite3`; "
            "none found — the import must not have been deleted entirely.",
        )

    def test_ac1_no_indented_import_sqlite3_count_is_zero(self):
        """Stricter bound: exactly 0 indented `import sqlite3` occurrences.

        Complements the list check — ensures the assertion is not vacuously
        true on a truncated file.
        """
        lines = self._read_source()
        in_func_count = sum(
            1 for ln in lines
            if ln.startswith("    ") and ln.strip() == "import sqlite3"
        )
        self.assertEqual(
            in_func_count, 0,
            f"Expected 0 in-function `import sqlite3` occurrences; found {in_func_count}.",
        )


# ---------------------------------------------------------------------------
# AC2 — docstrings on _tombstone_guards and search
# ---------------------------------------------------------------------------

class Ac2DocstringsTest(unittest.TestCase):
    """AC2: _tombstone_guards.__doc__ mentions state checks precede the admin check;
    search.__doc__ names the `[`/`]` snippet markers and `…` elision.

    RED: both docstrings currently lack the required content.
    """

    def test_ac2_tombstone_guards_docstring_exists(self):
        """_tombstone_guards must have a non-empty docstring."""
        doc = sdb._tombstone_guards.__doc__
        self.assertIsNotNone(
            doc,
            "_tombstone_guards.__doc__ is None — the function has no docstring.",
        )
        self.assertGreater(
            len(doc.strip()), 0,
            "_tombstone_guards.__doc__ is empty.",
        )

    def test_ac2_tombstone_guards_docstring_mentions_state_checks_precede_admin(self):
        """_tombstone_guards.__doc__ must state that state checks run BEFORE
        the super-admin authz check.

        The spec requires a distinctive substring capturing the order. We pin
        the substring 'state' appearing before 'admin' in the docstring AND
        require one of: 'before', 'precede', 'first', 'prior'.

        RED: current docstring says 'archived-only (the two-step), super-admin-only
        `by`' — no ordering statement.
        """
        doc = sdb._tombstone_guards.__doc__ or ""
        doc_lower = doc.lower()
        # 1. Both concepts present.
        self.assertIn(
            "state",
            doc_lower,
            f"_tombstone_guards.__doc__ must mention 'state' (state checks); "
            f"got: {doc!r}",
        )
        self.assertIn(
            "admin",
            doc_lower,
            f"_tombstone_guards.__doc__ must mention 'admin' (authz check); "
            f"got: {doc!r}",
        )
        # 2. An ordering word must be present.
        ordering_words = ("before", "precede", "first", "prior")
        self.assertTrue(
            any(w in doc_lower for w in ordering_words),
            f"_tombstone_guards.__doc__ must contain an ordering word "
            f"(one of {ordering_words}) to indicate state checks run before admin check; "
            f"got: {doc!r}",
        )
        # 3. 'state' must appear before 'admin' (positional ordering in the text).
        state_pos = doc_lower.find("state")
        admin_pos = doc_lower.find("admin")
        self.assertLess(
            state_pos, admin_pos,
            f"In _tombstone_guards.__doc__, 'state' ({state_pos}) must appear "
            f"before 'admin' ({admin_pos}); got doc: {doc!r}",
        )

    def test_ac2_search_docstring_exists(self):
        """search.__doc__ must be a non-empty docstring."""
        doc = sdb.search.__doc__
        self.assertIsNotNone(
            doc,
            "sdb.search.__doc__ is None — the function has no docstring.",
        )
        self.assertGreater(
            len(doc.strip()), 0,
            "sdb.search.__doc__ is empty.",
        )

    def test_ac2_search_docstring_mentions_open_bracket_marker(self):
        """search.__doc__ must name '[' as the snippet open-highlight marker.

        The snippet() projection is: snippet(message_fts, -1, '[', ']', '…', 8).
        The docstring must document the `[` marker so callers understand the output.

        RED: current docstring does not name the bracket markers.
        """
        doc = sdb.search.__doc__ or ""
        self.assertIn(
            "[",
            doc,
            f"search.__doc__ must contain '[' (open snippet marker); got: {doc!r}",
        )

    def test_ac2_search_docstring_mentions_close_bracket_marker(self):
        """search.__doc__ must name ']' as the snippet close-highlight marker.

        RED: current docstring does not name the bracket markers.
        """
        doc = sdb.search.__doc__ or ""
        self.assertIn(
            "]",
            doc,
            f"search.__doc__ must contain ']' (close snippet marker); got: {doc!r}",
        )

    def test_ac2_search_docstring_mentions_ellipsis_elision(self):
        """search.__doc__ must name '…' as the elision character used in snippets.

        RED: current docstring does not name the elision character.
        """
        doc = sdb.search.__doc__ or ""
        self.assertIn(
            "…",
            doc,
            f"search.__doc__ must contain '…' (elision marker); got: {doc!r}",
        )

    def test_ac2_search_docstring_snippet_markers_together(self):
        """search.__doc__ must document the snippet highlight contract: '[' + ']' + '…'
        all present, confirming the projection parameters are documented.

        RED: all three absent from current docstring.
        """
        doc = sdb.search.__doc__ or ""
        missing = [m for m in ("[", "]", "…") if m not in doc]
        self.assertEqual(
            missing, [],
            f"search.__doc__ is missing snippet markers {missing!r}; "
            f"got doc: {doc!r}",
        )


# ---------------------------------------------------------------------------
# AC3 — reindexed passthrough via MCP (coverage addition, expected PASS)
# ---------------------------------------------------------------------------

PROJ_RE = "ReindexP"
ML_RE = "Mainline - ReindexP"
T1_RE = "Track 1 - ReindexP"


class Ac3ReindexedPassthroughMcpTest(unittest.IsolatedAsyncioTestCase):
    """AC3: sandesh_search via MCP: seeded store with empty message_fts triggers
    reindex on first call (result has `reindexed` truthy); second call returns
    same hits with NO `reindexed` key (index now populated).

    Production complete since CR-SAN-027/028 — this is a coverage addition.
    If this FAILS, stop and report verbatim; do NOT fix production code.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-ac3-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ_RE)
        self.store = sdb.store_dir(PROJ_RE)
        self.con = sdb.connect()

        sdb.register(self.con, ML_RE, kind="mainline", project=PROJ_RE)
        sdb.register(self.con, T1_RE, kind="track", project=PROJ_RE)

        # Seed a message via sdb.send so all normal rows (message, message_recipient,
        # body file) are present, THEN delete from message_fts to simulate pre-FTS history.
        self.unique_term = "reindextestuniqzephyr"
        self.mid = sdb.send(
            self.con, self.store,
            from_addr=T1_RE,
            to=[ML_RE],
            subject="reindex coverage test",
            body_text=f"body with {self.unique_term} for passthrough test",
        )
        # Hollow out the FTS index — simulates a store created before migration 0005.
        self.con.execute("DELETE FROM message_fts")
        self.con.commit()

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

    async def test_ac3_first_search_call_returns_reindexed_true_and_hits(self):
        """First sandesh_search call on empty FTS: result has reindexed=True
        and the seeded message appears in hits.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_RE, "query": self.unique_term},
        )
        data = _unwrap_search(result)
        self.assertIsInstance(
            data, dict,
            f"sandesh_search must return a dict; got {type(data)}: {data!r}",
        )
        # reindexed must be truthy on first call (empty FTS triggered reindex).
        self.assertTrue(
            data.get("reindexed"),
            f"First call on empty FTS must return reindexed=True; "
            f"got result: {data!r}",
        )
        # The seeded message must appear in hits.
        ids = [h["id"] for h in data.get("hits", [])]
        self.assertIn(
            self.mid, ids,
            f"Seeded mid={self.mid} must appear in hits after reindex; "
            f"got hits={data.get('hits', [])!r}",
        )
        # total must be at least 1.
        self.assertGreaterEqual(
            data.get("total", 0), 1,
            f"total must be >= 1 after reindex; got {data.get('total')!r}",
        )

    async def test_ac3_second_search_call_has_no_reindexed_key(self):
        """Second sandesh_search call: same hits returned, NO `reindexed` key
        (index is now populated so the reindex branch is not entered).
        """
        # First call populates the index.
        await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_RE, "query": self.unique_term},
        )
        # Second call — fresh tool invocation on now-populated index.
        result2 = await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_RE, "query": self.unique_term},
        )
        data2 = _unwrap_search(result2)
        self.assertIsInstance(data2, dict, f"second call must return a dict; got {data2!r}")
        # `reindexed` key must be ABSENT on second call.
        self.assertNotIn(
            "reindexed", data2,
            f"Second call must NOT have 'reindexed' key (index already populated); "
            f"got keys: {sorted(data2.keys())!r}",
        )
        # Hits must still be present (index intact).
        ids = [h["id"] for h in data2.get("hits", [])]
        self.assertIn(
            self.mid, ids,
            f"Seeded mid={self.mid} must still appear in hits on second call; "
            f"got hits={data2.get('hits', [])!r}",
        )

    async def test_ac3_total_and_hits_consistent_across_calls(self):
        """total and len(hits) must agree and be stable across both calls."""
        r1 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_RE, "query": self.unique_term},
        ))
        r2 = _unwrap_search(await mcp_server.mcp.call_tool(
            "sandesh_search",
            {"recipient": ML_RE, "query": self.unique_term},
        ))
        # First call total == second call total.
        self.assertEqual(
            r1.get("total"), r2.get("total"),
            f"total must be the same on both calls; call1={r1.get('total')!r}, "
            f"call2={r2.get('total')!r}",
        )
        # hits lists contain the same ids.
        ids1 = {h["id"] for h in r1.get("hits", [])}
        ids2 = {h["id"] for h in r2.get("hits", [])}
        self.assertEqual(
            ids1, ids2,
            f"Hit ids must be identical across calls; call1={ids1!r}, call2={ids2!r}",
        )


# ---------------------------------------------------------------------------
# AC4 — sender/since/until via MCP (coverage addition, expected PASS)
# ---------------------------------------------------------------------------

PROJ_F = "FilterP"
ML_F   = "Mainline - FilterP"
T1_F   = "Track 1 - FilterP"
T2_F   = "Track 2 - FilterP"


class Ac4InboxFiltersMcpTest(unittest.IsolatedAsyncioTestCase):
    """AC4: sandesh_inbox with sender=, since=, until= filters correctly.

    Covers:
      - sender= returns only that sender's rows
      - since=/until= bracket a seeded timestamp spread
      - date-only until (e.g. '2026-06-12') matches a same-day-later-hour message
        (end-of-day normalisation)

    Production complete — coverage addition. Stop and report on failure.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-ac4-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_proj = os.environ.get("SANDESH_PROJECT")
        os.environ["XDG_DATA_HOME"] = self.tmp
        os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup(PROJ_F)
        self.store = sdb.store_dir(PROJ_F)
        self.con = sdb.connect()

        sdb.register(self.con, ML_F, kind="mainline", project=PROJ_F)
        sdb.register(self.con, T1_F, kind="track", project=PROJ_F)
        sdb.register(self.con, T2_F, kind="track", project=PROJ_F)

        # Seed two messages: one from T1_F, one from T2_F, both to ML_F.
        self.mid_from_t1 = sdb.send(
            self.con, self.store,
            from_addr=T1_F,
            to=[ML_F],
            subject="from track 1",
        )
        self.mid_from_t2 = sdb.send(
            self.con, self.store,
            from_addr=T2_F,
            to=[ML_F],
            subject="from track 2",
        )

        # Seed three messages with known timestamps for since/until testing.
        # Use raw UPDATE to fix created_at for determinism.
        self.mid_early = sdb.send(
            self.con, self.store,
            from_addr=T1_F,
            to=[ML_F],
            subject="early message",
        )
        self.mid_mid = sdb.send(
            self.con, self.store,
            from_addr=T1_F,
            to=[ML_F],
            subject="mid message",
        )
        self.mid_late = sdb.send(
            self.con, self.store,
            from_addr=T1_F,
            to=[ML_F],
            subject="late message",
        )
        # Pin timestamps: early=2026-06-10, mid=2026-06-11, late=2026-06-12 09:00
        self.con.execute(
            "UPDATE message SET created_at=? WHERE id=?",
            ("2026-06-10 10:00:00", self.mid_early),
        )
        self.con.execute(
            "UPDATE message SET created_at=? WHERE id=?",
            ("2026-06-11 12:00:00", self.mid_mid),
        )
        self.con.execute(
            "UPDATE message SET created_at=? WHERE id=?",
            ("2026-06-12 09:00:00", self.mid_late),
        )
        self.con.commit()

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

    async def test_ac4_inbox_sender_filter_returns_only_that_senders_rows(self):
        """sandesh_inbox with sender=T1_F returns only T1_F rows; T2_F rows absent."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "sender": T1_F,
            },
        )
        rows = _data(result)
        self.assertIsInstance(rows, list, f"inbox must return a list; got {type(rows)}")
        ids = [r["id"] for r in rows]
        # mid_from_t1 must be present.
        self.assertIn(
            self.mid_from_t1, ids,
            f"mid_from_t1 must appear with sender={T1_F!r}; got ids={ids!r}",
        )
        # mid_from_t2 (T2_F sender) must NOT be present.
        self.assertNotIn(
            self.mid_from_t2, ids,
            f"mid_from_t2 (T2_F sender) must NOT appear with sender={T1_F!r}; "
            f"got ids={ids!r}",
        )

    async def test_ac4_inbox_sender_filter_negative_bound(self):
        """sender=T2_F returns only T2_F rows; T1_F rows absent."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "sender": T2_F,
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]
        self.assertIn(
            self.mid_from_t2, ids,
            f"mid_from_t2 must appear with sender={T2_F!r}; got ids={ids!r}",
        )
        self.assertNotIn(
            self.mid_from_t1, ids,
            f"mid_from_t1 (T1_F sender) must NOT appear with sender={T2_F!r}",
        )

    async def test_ac4_inbox_since_filter_excludes_earlier_messages(self):
        """since='2026-06-11 00:00:00' must exclude the early (2026-06-10) message
        but include mid and late messages.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "since": "2026-06-11 00:00:00",
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]
        self.assertNotIn(
            self.mid_early, ids,
            f"mid_early (2026-06-10) must NOT appear with since='2026-06-11'; "
            f"got ids={ids!r}",
        )
        self.assertIn(
            self.mid_mid, ids,
            f"mid_mid (2026-06-11) must appear with since='2026-06-11'; "
            f"got ids={ids!r}",
        )
        self.assertIn(
            self.mid_late, ids,
            f"mid_late (2026-06-12) must appear with since='2026-06-11'; "
            f"got ids={ids!r}",
        )

    async def test_ac4_inbox_until_datetime_filter_excludes_later_messages(self):
        """until='2026-06-11 23:59:59' must exclude the late (2026-06-12) message."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "until": "2026-06-11 23:59:59",
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]
        self.assertNotIn(
            self.mid_late, ids,
            f"mid_late (2026-06-12 09:00) must NOT appear with until='2026-06-11 23:59:59'; "
            f"got ids={ids!r}",
        )
        self.assertIn(
            self.mid_mid, ids,
            f"mid_mid (2026-06-11) must appear with until='2026-06-11 23:59:59'; "
            f"got ids={ids!r}",
        )

    async def test_ac4_inbox_date_only_until_normalises_to_end_of_day(self):
        """until='2026-06-12' (date-only) must match mid_late (2026-06-12 09:00)
        because the end-of-day normalisation expands it to 2026-06-12 23:59:59.
        """
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "until": "2026-06-12",
            },
        )
        rows = _data(result)
        ids = [r["id"] for r in rows]
        self.assertIn(
            self.mid_late, ids,
            f"mid_late (2026-06-12 09:00) must appear with date-only "
            f"until='2026-06-12' (end-of-day normalisation); got ids={ids!r}",
        )
        # mid_early (2026-06-10) must also be present — it is before the cutoff.
        self.assertIn(
            self.mid_early, ids,
            f"mid_early (2026-06-10) must also appear with until='2026-06-12'; "
            f"got ids={ids!r}",
        )

    async def test_ac4_inbox_since_until_bracket_middle_only(self):
        """since='2026-06-11' and until='2026-06-11 23:59:59' returns only mid_mid."""
        result = await mcp_server.mcp.call_tool(
            "sandesh_inbox",
            {
                "project_id": PROJ_F,
                "recipient": ML_F,
                "unread_only": False,
                "since": "2026-06-11 00:00:00",
                "until": "2026-06-11 23:59:59",
            },
        )
        rows = _data(result)
        # Keep only the timestamp-seeded messages (exclude mid_from_t1 / mid_from_t2
        # which have current timestamps and may appear in the window).
        time_ids = {self.mid_early, self.mid_mid, self.mid_late}
        filtered = [r for r in rows if r["id"] in time_ids]
        ids = {r["id"] for r in filtered}
        self.assertIn(
            self.mid_mid, ids,
            f"mid_mid (2026-06-11) must appear in since/until bracket; got ids={ids!r}",
        )
        self.assertNotIn(
            self.mid_early, ids,
            f"mid_early (2026-06-10) must NOT appear in since/until bracket; "
            f"got ids={ids!r}",
        )
        self.assertNotIn(
            self.mid_late, ids,
            f"mid_late (2026-06-12) must NOT appear in since/until bracket; "
            f"got ids={ids!r}",
        )


# ---------------------------------------------------------------------------
# AC5 — install.sh has no $'import os escaped-blob; has heredoc marker
# ---------------------------------------------------------------------------

class Ac5InstallerHeredocTest(unittest.TestCase):
    """AC5: install.sh must not contain the `$'import os` escaped-blob opener;
    a heredoc python invocation (<<'PY') must be present in the admin block.

    RED: currently the `$'import os\\n...'` one-liner blob exists at line ~101.
    """

    def _read_install_sh(self):
        self.assertTrue(
            os.path.isfile(_INSTALL_SH_PATH),
            f"install.sh not found at {_INSTALL_SH_PATH}",
        )
        with open(_INSTALL_SH_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_ac5_no_escaped_blob_opener_in_install_sh(self):
        """install.sh must NOT contain `$'import os` (the escaped-blob opener).

        RED: the current admin-assignment block uses:
          "$VENV/bin/python" -c $'import os\\n...'
        which must be replaced by a heredoc form.
        """
        content = self._read_install_sh()
        self.assertNotIn(
            "$'import os",
            content,
            "install.sh contains the `$'import os` escaped-blob opener — "
            "must be replaced with a readable heredoc (`<<'PY'`).",
        )

    def test_ac5_heredoc_py_marker_present_in_install_sh(self):
        """install.sh must contain `<<'PY'` as the heredoc start marker in the
        admin-assignment block (or equivalent like `<<'PYCODE'`).

        RED: the current one-liner blob has no heredoc; <<'PY' is absent.
        """
        content = self._read_install_sh()
        # Accept any <<'PY...' marker (e.g. <<'PY', <<'PYCODE', <<'PYTHON').
        heredoc_present = "<<'PY" in content or '<< "PY' in content
        self.assertTrue(
            heredoc_present,
            "install.sh must contain a heredoc python invocation marker (<<'PY or "
            "similar) in the admin-assignment block; none found. "
            "The `$'...'` blob must be replaced with `\"$VENV/bin/python\" - <<'PY' ... PY`.",
        )

    def test_ac5_no_escaped_blob_and_heredoc_together(self):
        """After the rewrite neither the blob NOR the absence of heredoc should hold.

        Compound assertion: blob absent AND heredoc present (both gates satisfied).
        RED: blob present, heredoc absent.
        """
        content = self._read_install_sh()
        blob_absent = "$'import os" not in content
        heredoc_present = "<<'PY" in content or '<< "PY' in content
        self.assertTrue(
            blob_absent and heredoc_present,
            f"install.sh must have blob_absent={True} and heredoc_present={True}; "
            f"got blob_absent={blob_absent}, heredoc_present={heredoc_present}.",
        )


# ---------------------------------------------------------------------------
# AC6 — warning-clean install suite (subprocess-run test_install.py)
# ---------------------------------------------------------------------------

class Ac6WarningCleanInstallSuiteTest(unittest.TestCase):
    """AC6: running `PYTHONPATH=. .venv/bin/python tests/test_install.py` produces
    ZERO ResourceWarning lines and reports OK.

    RED: currently 3+ ResourceWarning occurrences from unclosed TextIOWrapper
    handles (subprocess pipes) in test_install.py.

    NOTE: this test runs the full install suite (~45 s). It is a single test method
    with a generous timeout; that is accepted per the spec.
    """

    def test_ac6_install_suite_has_zero_resource_warnings(self):
        """Combined stdout+stderr of `PYTHONPATH=. .venv/bin/python tests/test_install.py`
        must contain ZERO lines matching 'ResourceWarning'.

        Also asserts the suite itself reports OK (no regressions).

        RED: current test_install.py has unclosed TextIOWrapper handles that trigger
        ResourceWarning at GC time; the suite output currently contains 3+ occurrences.
        """
        result = subprocess.run(
            [_VENV_PYTHON, os.path.join(_TESTS_DIR, "test_install.py")],
            capture_output=True,
            cwd=_REPO_ROOT,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
            timeout=180,
        )
        combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")

        # Count ResourceWarning occurrences.
        rw_count = combined.count("ResourceWarning")
        self.assertEqual(
            rw_count, 0,
            f"test_install.py produced {rw_count} ResourceWarning occurrence(s); "
            f"must be 0. The unclosed TextIOWrapper handles (subprocess pipes) "
            f"must be closed in tearDownClass.\n"
            f"Relevant lines:\n"
            + "\n".join(
                ln for ln in combined.splitlines() if "ResourceWarning" in ln
            ),
        )

        # The suite must still be OK (no regressions from the fix).
        self.assertEqual(
            result.returncode, 0,
            f"test_install.py exited with rc={result.returncode} — "
            f"the suite must stay green.\n"
            f"Output (last 30 lines):\n"
            + "\n".join(combined.splitlines()[-30:]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
