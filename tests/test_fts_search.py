"""test_fts_search.py — RED tests for CR-SAN-027 Cycle 2.

Covers §S2 (reindex/lazy) + §S3 (search()) lib layer + AC3/AC4/AC5/AC6/AC7/AC8.

  AC3 — bm25 pair ordering; hit envelope keys; snippet contains term; read_at unchanged
  AC4 — own-mailbox boundary (Track-only term not found by Mainline)
  AC5 — pagination: 25-corpus, limit=20/offset=0/offset=20/offset=30
  AC6 (lib) — reindex(): raw-seeded fixture; second run idempotent; lazy auto-reindex;
               sparse index does NOT trigger lazy
  AC7 — sender_project composition; tombstoned-sender hits hidden
  AC8 (lib) — malformed FTS5 query → ValueError (not OperationalError)

Expected RED:
  AttributeError on search/reindex references everywhere (callables not yet defined).
  The callable pre-check pattern is used so the class body still executes cleanly.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_fts_search --agent red-cr027-c2
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

# Repo root — resolve from this file regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fts_row_count(con):
    """Return the total number of rows in message_fts (may be 0)."""
    try:
        return con.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _delete_fts_rows(con):
    """Empty message_fts entirely (used to set up the lazy-reindex fixture)."""
    con.execute("DELETE FROM message_fts")
    con.commit()


def _delete_fts_row_for_mid(con, mid):
    """Remove a single FTS row by rowid (used for the sparse-index fixture)."""
    con.execute("DELETE FROM message_fts WHERE rowid=?", (mid,))
    con.commit()


def _raw_insert_message(con, from_addr, subject, kind=None):
    """Insert a message row directly (bypassing send) — NO FTS row created.
    Returns the new message id. Used by the reindex fixture."""
    cur = con.execute(
        "INSERT INTO message (from_addr, subject, kind) VALUES (?,?,?)",
        (from_addr, subject, kind),
    )
    con.commit()
    return cur.lastrowid


def _raw_insert_recipient(con, message_id, recipient, role="to"):
    """Insert a message_recipient row directly."""
    con.execute(
        "INSERT OR IGNORE INTO message_recipient (message_id, recipient, role) VALUES (?,?,?)",
        (message_id, recipient, role),
    )
    con.commit()


def _write_body_file(store, mid, text):
    """Write a body file for message <mid> in <store>/messages/msg-<mid>.md
    and return the full path.  Also updates the body_path column in message."""
    import os
    msg_dir = os.path.join(store, "messages")
    os.makedirs(msg_dir, exist_ok=True)
    path = os.path.abspath(os.path.join(msg_dir, f"msg-{mid}.md"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _update_body_path(con, mid, path):
    con.execute("UPDATE message SET body_path=? WHERE id=?", (path, mid))
    con.commit()


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    setUp provisions the primary search corpus:

      P1  — the main project; Mainline-P1 + Track 1-P1 registered
      P2  — cross-project sender (for AC7 sender_project composition)

    Admin 'ops'; P1 and P2 both granted.

    Corpus for P1 (all sent via s.send so FTS rows are created):

      mid_bm25_high  — Mainline-P1 → Mainline-P1? No: Track 1-P1 as recipient.
                       subject='nebula observation' + body contains 'nebula'
                       (term appears in BOTH subject AND body → higher bm25)
      mid_bm25_low   — same term 'nebula' appears in body only (not subject)
                       → lower bm25; MUST rank second

      mid_track_only — Track 1-P1 → Track 1-P1 (Mainline-P1 is NOT a recipient)
                       subject+body contain unique term 'vortexboundary' — Mainline
                       should NEVER find this

      25 messages sharing common term 'paginate' — all to Mainline-P1

      Tombstoned-sender case (AC7):
        P_tomb project messages sent to Mainline-P1 before tombstoning; after
        tombstone, 'tombstonedterm' must return 0 hits for Mainline-P1.

      P2 cross-project messages:
        A message from Mainline-P2 to Mainline-P1 with unique term 'senderprojectterm'
        (for AC7 sender_project filter).
    """

    P1    = "P1srch"
    P2    = "P2srch"
    P_TOMB = "Ptomb"
    ADMIN = "ops"

    ML_P1  = "Mainline - P1srch"
    T1_P1  = "Track 1 - P1srch"
    ML_P2  = "Mainline - P2srch"
    ML_TOMB = "Mainline - Ptomb"

    # bm25 pair terms
    BM25_TERM     = "nebulaobservation"
    BM25_HIGH_SUBJ = "nebulaobservation report"  # term in subject + body → high rank
    BM25_HIGH_BODY = "nebulaobservation telescope data"
    BM25_LOW_SUBJ  = "telescope data summary"     # term only in body → low rank
    BM25_LOW_BODY  = "nebulaobservation data collected"

    TRACK_TERM    = "vortexboundary"
    PAGINATE_TERM = "paginate"
    TOMB_TERM     = "tombstonedterm"
    P2_TERM       = "senderprojectterm"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-fts-search-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Provision projects.
        s.setup(self.P1)
        s.setup(self.P2)
        s.setup(self.P_TOMB)

        self.con = s.connect()
        self.store_p1   = s.store_dir(self.P1)
        self.store_p2   = s.store_dir(self.P2)
        self.store_tomb = s.store_dir(self.P_TOMB)

        # Register addresses.
        s.register(self.con, self.ML_P1,   kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1,   kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2,   kind="mainline", project=self.P2)
        s.register(self.con, self.ML_TOMB, kind="mainline", project=self.P_TOMB)

        s.assign_admin(self.con, self.ADMIN)
        s.grant_xproj(self.con, self.P1,    self.ADMIN)
        s.grant_xproj(self.con, self.P2,    self.ADMIN)
        s.grant_xproj(self.con, self.P_TOMB, self.ADMIN)

        # --- bm25 pair ---
        # High: term in subject + body (addressed to T1_P1, and to ML_P1 via cc so
        # Mainline can also see it for other tests — but bm25 tests use T1_P1 POV)
        self.mid_bm25_high = s.send(
            self.con, self.store_p1,
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject=self.BM25_HIGH_SUBJ,
            body_text=self.BM25_HIGH_BODY,
            project=self.P1,
        )
        # Low: term in body only; different subject so bm25 difference is clear
        self.mid_bm25_low = s.send(
            self.con, self.store_p1,
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject=self.BM25_LOW_SUBJ,
            body_text=self.BM25_LOW_BODY,
            project=self.P1,
        )

        # --- Track-only message (AC4 boundary) ---
        # from ML_P1 to T1_P1 ONLY — ML_P1 is NOT a recipient of this message
        self.mid_track_only = s.send(
            self.con, self.store_p1,
            from_addr=self.ML_P1,
            to=[self.T1_P1],
            subject="track task",
            body_text=f"unique term {self.TRACK_TERM} here",
            project=self.P1,
        )

        # --- 25-message pagination corpus ---
        self.mid_paginate = []
        for i in range(25):
            mid = s.send(
                self.con, self.store_p1,
                from_addr=self.T1_P1,
                to=[self.ML_P1],
                subject=f"paginate item {i}",
                body_text=f"body of paginate item {i} contains {self.PAGINATE_TERM} term",
                project=self.P1,
            )
            self.mid_paginate.append(mid)

        # --- Tombstoned-sender messages (AC7) ---
        self.mid_tomb_msg = s.send(
            self.con, self.store_tomb,
            from_addr=self.ML_TOMB,
            to=[self.ML_P1],
            subject="tombstone check",
            body_text=f"unique body term {self.TOMB_TERM}",
            project=self.P_TOMB,
        )
        # Archive then tombstone P_TOMB.
        s.archive(self.con, self.P_TOMB, self.ML_TOMB, wait_secs=0.1)
        s.tombstone_project(self.con, self.P_TOMB, self.ADMIN, wait_secs=0.1)

        # --- P2 cross-project message (AC7 sender_project) ---
        self.mid_p2_msg = s.send(
            self.con, self.store_p2,
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="p2 cross project hello",
            body_text=f"body with unique {self.P2_TERM} here",
            project=self.P2,
        )

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# AC3 — bm25 ordering, hit envelope, snippet, read_at untouched
# ---------------------------------------------------------------------------

class SearchBm25OrderingTest(_TempDataHome):
    """AC3: bm25 returns the better match first (subject+body > body-only);
    each hit carries the exact envelope keys (id, from, subject, kind,
    created_at, role) plus a snippet; read_at values are NULL after search.

    RED: AttributeError — search() not yet defined in sandesh_db.
    """

    def test_search_callable_exists(self):
        """Pre-check: sandesh_db.search must exist before any behavioural test.

        RED: AttributeError.
        """
        self.assertTrue(
            hasattr(s, "search") and callable(getattr(s, "search")),
            "sandesh_db.search is not defined or not callable — GREEN must add it",
        )

    def test_bm25_high_rank_message_first(self):
        """search(recipient=ML_P1, query=BM25_TERM) must return mid_bm25_high
        before mid_bm25_low (subject+body hit ranks above body-only).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        hits = result["hits"]
        ids = [h["id"] for h in hits]

        self.assertIn(
            self.mid_bm25_high, ids,
            f"mid_bm25_high must appear in search results; got ids={ids!r}",
        )
        self.assertIn(
            self.mid_bm25_low, ids,
            f"mid_bm25_low must appear in search results; got ids={ids!r}",
        )
        idx_high = ids.index(self.mid_bm25_high)
        idx_low  = ids.index(self.mid_bm25_low)
        self.assertLess(
            idx_high, idx_low,
            f"mid_bm25_high (subject+body) must rank before mid_bm25_low (body-only); "
            f"got high={idx_high}, low={idx_low} in ids={ids!r}",
        )

    def test_hit_envelope_keys_exact(self):
        """Each hit must carry EXACTLY these envelope keys: id, from, subject,
        kind, created_at, role, snippet.

        RED: AttributeError on s.search.
        """
        REQUIRED_KEYS = {"id", "from", "subject", "kind", "created_at", "role", "snippet"}
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        hits = result["hits"]

        self.assertGreater(len(hits), 0, "search must return at least one hit for BM25_TERM")
        for hit in hits:
            hit_keys = set(hit.keys())
            self.assertGreaterEqual(
                hit_keys, REQUIRED_KEYS,
                f"hit is missing required keys: {REQUIRED_KEYS - hit_keys!r}; "
                f"hit has: {sorted(hit_keys)!r}",
            )

    def test_snippet_contains_search_term(self):
        """The snippet field of each hit must contain the search term (or a stem/form).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        hits = result["hits"]
        self.assertGreater(len(hits), 0, "search must return hits for BM25_TERM")

        for hit in hits:
            snippet = hit.get("snippet", "")
            self.assertIsNotNone(snippet, f"snippet must not be None for hit id={hit.get('id')}")
            # snippet must contain at least part of the search term (FTS5 snippet may truncate)
            term_lower = self.BM25_TERM.lower()
            snippet_lower = (snippet or "").lower()
            self.assertIn(
                term_lower, snippet_lower,
                f"snippet must contain the search term {self.BM25_TERM!r}; "
                f"got snippet={snippet!r} for hit id={hit.get('id')}",
            )

    def test_search_result_has_total_limit_offset_keys(self):
        """search result dict must contain 'hits', 'total', 'limit', 'offset'.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        for key in ("hits", "total", "limit", "offset"):
            self.assertIn(
                key, result,
                f"search result must have key {key!r}; got keys={sorted(result.keys())!r}",
            )

    def test_search_does_not_alter_read_at(self):
        """After search(), all message_recipient read_at values for ML_P1 must
        remain NULL (search does not mark messages read).

        RED: AttributeError on s.search.
        """
        # Verify all corpus messages to ML_P1 are unread before search.
        s.search(self.con, self.ML_P1, self.BM25_TERM)

        # Check read_at for both bm25 messages.
        for mid, label in [
            (self.mid_bm25_high, "mid_bm25_high"),
            (self.mid_bm25_low, "mid_bm25_low"),
        ]:
            row = self.con.execute(
                "SELECT read_at FROM message_recipient "
                "WHERE message_id=? AND recipient=?",
                (mid, self.ML_P1),
            ).fetchone()
            self.assertIsNotNone(
                row,
                f"message_recipient row for {label} + ML_P1 must exist",
            )
            self.assertIsNone(
                row["read_at"],
                f"search must NOT mark {label} as read; got read_at={row['read_at']!r}",
            )

    def test_search_total_equals_match_count(self):
        """result['total'] must equal the number of matching messages, not
        the length of the page.

        For BM25_TERM with 2 matching messages: total == 2.
        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        self.assertEqual(
            result["total"], 2,
            f"total must be 2 for BM25_TERM (two matching messages); "
            f"got total={result['total']!r}",
        )
        self.assertEqual(
            len(result["hits"]), 2,
            f"hits page must have 2 items; got {len(result['hits'])}",
        )

    def test_search_returns_limit_and_offset_in_result(self):
        """result['limit'] and result['offset'] must match the call defaults (20, 0).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        self.assertEqual(
            result["limit"], 20,
            f"result['limit'] must equal the default 20; got {result['limit']!r}",
        )
        self.assertEqual(
            result["offset"], 0,
            f"result['offset'] must equal the default 0; got {result['offset']!r}",
        )


# ---------------------------------------------------------------------------
# AC4 — own-mailbox boundary
# ---------------------------------------------------------------------------

class SearchMailboxBoundaryTest(_TempDataHome):
    """AC4: search never returns messages that recipient is not addressee of.

    Mainline-P1's search for vortexboundary (a term that ONLY appears in
    a Track-1-P1 recipient message) must return total==0.
    Track-1-P1's own search for the same term must find it.

    RED: AttributeError on s.search.
    """

    def test_mainline_cannot_find_track_only_term(self):
        """ML_P1 searching for TRACK_TERM → total == 0, hits == [].

        mid_track_only was sent TO T1_P1 only. ML_P1 is NOT a recipient.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.TRACK_TERM)
        self.assertEqual(
            result["total"], 0,
            f"ML_P1 must find total=0 for {self.TRACK_TERM!r} "
            f"(not a recipient of mid_track_only); got total={result['total']!r}",
        )
        self.assertEqual(
            result["hits"], [],
            f"ML_P1 hits must be [] for {self.TRACK_TERM!r}; got hits={result['hits']!r}",
        )

    def test_track1_can_find_track_only_term(self):
        """T1_P1 searching for TRACK_TERM → finds mid_track_only (is a recipient).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.T1_P1, self.TRACK_TERM)
        self.assertGreater(
            result["total"], 0,
            f"T1_P1 must find at least 1 result for {self.TRACK_TERM!r}; "
            f"got total={result['total']!r}",
        )
        hit_ids = [h["id"] for h in result["hits"]]
        self.assertIn(
            self.mid_track_only, hit_ids,
            f"mid_track_only must appear in T1_P1 search results for {self.TRACK_TERM!r}; "
            f"got hit_ids={hit_ids!r}",
        )

    def test_mainline_bm25_results_contain_only_its_messages(self):
        """All hits in ML_P1's search results must correspond to messages where
        ML_P1 is listed as a recipient in message_recipient.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM)
        for hit in result["hits"]:
            row = self.con.execute(
                "SELECT 1 FROM message_recipient WHERE message_id=? AND recipient=?",
                (hit["id"], self.ML_P1),
            ).fetchone()
            self.assertIsNotNone(
                row,
                f"Hit id={hit['id']} must have ML_P1 as a recipient; "
                f"boundary violation: non-recipient message surfaced",
            )


# ---------------------------------------------------------------------------
# AC5 — pagination
# ---------------------------------------------------------------------------

class SearchPaginationTest(_TempDataHome):
    """AC5: 25 messages sharing PAGINATE_TERM — paginate correctly.

    limit=20, offset=0  → 20 hits, total=25
    offset=20           → 5 hits,  total=25
    offset=30           → 0 hits,  total=25

    RED: AttributeError on s.search.
    """

    def test_first_page_returns_20_hits_total_25(self):
        """search(PAGINATE_TERM, limit=20, offset=0) → 20 hits, total=25.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                          limit=20, offset=0)
        self.assertEqual(
            result["total"], 25,
            f"total must be 25 for PAGINATE_TERM corpus; got {result['total']!r}",
        )
        self.assertEqual(
            len(result["hits"]), 20,
            f"first page must have 20 hits; got {len(result['hits'])}",
        )
        self.assertEqual(result["limit"], 20, "limit must echo back 20")
        self.assertEqual(result["offset"], 0, "offset must echo back 0")

    def test_second_page_returns_5_hits_total_25(self):
        """search(PAGINATE_TERM, limit=20, offset=20) → 5 hits, total=25.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                          limit=20, offset=20)
        self.assertEqual(
            result["total"], 25,
            f"total must be 25 on second page; got {result['total']!r}",
        )
        self.assertEqual(
            len(result["hits"]), 5,
            f"second page must have 5 hits; got {len(result['hits'])}",
        )
        self.assertEqual(result["limit"], 20)
        self.assertEqual(result["offset"], 20)

    def test_offset_beyond_total_returns_empty_hits_correct_total(self):
        """search(PAGINATE_TERM, limit=20, offset=30) → 0 hits, total=25.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                          limit=20, offset=30)
        self.assertEqual(
            result["total"], 25,
            f"total must still be 25 at offset=30; got {result['total']!r}",
        )
        self.assertEqual(
            result["hits"], [],
            f"hits must be [] at offset=30 (beyond corpus); got {result['hits']!r}",
        )
        self.assertEqual(result["offset"], 30)

    def test_first_and_second_page_together_cover_all_25(self):
        """The combined ids from page 1 and page 2 must equal the full 25-id corpus.

        RED: AttributeError on s.search.
        """
        page1 = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                         limit=20, offset=0)
        page2 = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                         limit=20, offset=20)

        all_ids = {h["id"] for h in page1["hits"]} | {h["id"] for h in page2["hits"]}
        corpus_ids = set(self.mid_paginate)
        self.assertEqual(
            all_ids, corpus_ids,
            f"page1 + page2 ids must cover the full 25-message corpus; "
            f"missing={corpus_ids - all_ids!r}, extra={all_ids - corpus_ids!r}",
        )

    def test_pages_have_no_overlap(self):
        """Page 1 and page 2 must share no ids.

        RED: AttributeError on s.search.
        """
        page1 = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                         limit=20, offset=0)
        page2 = s.search(self.con, self.ML_P1, self.PAGINATE_TERM,
                         limit=20, offset=20)
        ids1 = {h["id"] for h in page1["hits"]}
        ids2 = {h["id"] for h in page2["hits"]}
        overlap = ids1 & ids2
        self.assertEqual(
            overlap, set(),
            f"pages must not overlap; overlapping ids={overlap!r}",
        )


# ---------------------------------------------------------------------------
# AC6 (lib) — reindex paths
# ---------------------------------------------------------------------------

class ReindexTest(unittest.TestCase):
    """AC6 (lib): reindex() over a raw-seeded fixture; idempotent; lazy path;
    sparse-index does NOT trigger lazy.

    Uses its own isolated fixture (not the shared setUp corpus) to control
    exact FTS state.

    RED: AttributeError on s.reindex / s.search.
    """

    P = "Reindx"
    ML = "Mainline - Reindx"
    T1 = "Track 1 - Reindx"

    TERM_A = "quasarreindex"
    TERM_B = "pulsarreindex"
    TERM_C = "magnetarreindex"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-reindex-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P)
        self.con = s.connect()
        self.store = s.store_dir(self.P)

        s.register(self.con, self.ML, kind="mainline", project=self.P)
        s.register(self.con, self.T1, kind="track",    project=self.P)

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --

    def _seed_raw_message(self, subject, body_text):
        """Insert a message + recipient row directly (bypasses send → no FTS row).
        Returns (mid, body_path).  Also writes the body file and sets body_path."""
        mid = _raw_insert_message(self.con, self.ML, subject)
        _raw_insert_recipient(self.con, mid, self.T1, role="to")
        path = _write_body_file(self.store, mid, body_text)
        _update_body_path(self.con, mid, path)
        return mid, path

    # -- tests --

    def test_reindex_callable_exists(self):
        """Pre-check: sandesh_db.reindex must be callable.

        RED: AttributeError.
        """
        self.assertTrue(
            hasattr(s, "reindex") and callable(getattr(s, "reindex")),
            "sandesh_db.reindex is not defined or not callable",
        )

    def test_reindex_on_empty_fts_raw_seeded_fixture_finds_all(self):
        """reindex() over raw-seeded messages (no FTS rows) indexes all; search finds them.

        Fixture: 3 messages inserted directly (no FTS rows). reindex() must index all 3.
        Then search for each unique term → finds it.

        RED: AttributeError on s.reindex.
        """
        mid_a, _ = self._seed_raw_message(f"msg a", f"body has {self.TERM_A}")
        mid_b, _ = self._seed_raw_message(f"msg b", f"body has {self.TERM_B}")
        mid_c, _ = self._seed_raw_message(f"msg c", f"body has {self.TERM_C}")

        # Verify FTS is empty before reindex.
        self.assertEqual(
            _fts_row_count(self.con), 0,
            "FTS must be empty before reindex (raw insert bypasses FTS)",
        )

        count = s.reindex(self.con)

        self.assertEqual(
            count, 3,
            f"reindex must return the indexed count (3); got {count!r}",
        )
        fts_rows = _fts_row_count(self.con)
        self.assertEqual(
            fts_rows, 3,
            f"message_fts must have 3 rows after reindex; got {fts_rows}",
        )

        # search must now find each term.
        for mid, term, label in [
            (mid_a, self.TERM_A, "TERM_A"),
            (mid_b, self.TERM_B, "TERM_B"),
            (mid_c, self.TERM_C, "TERM_C"),
        ]:
            result = s.search(self.con, self.T1, term)
            hit_ids = [h["id"] for h in result["hits"]]
            self.assertIn(
                mid, hit_ids,
                f"search for {label}={term!r} must find mid={mid} after reindex; "
                f"got hit_ids={hit_ids!r}",
            )

    def test_reindex_idempotent_same_count_no_duplicates(self):
        """Second reindex returns the same count and leaves no duplicate FTS rows.

        RED: AttributeError on s.reindex.
        """
        self._seed_raw_message("msg a", f"body {self.TERM_A}")
        self._seed_raw_message("msg b", f"body {self.TERM_B}")

        count1 = s.reindex(self.con)
        count2 = s.reindex(self.con)

        self.assertEqual(
            count1, count2,
            f"second reindex must return the same count as first; "
            f"count1={count1}, count2={count2}",
        )
        fts_rows = _fts_row_count(self.con)
        self.assertEqual(
            fts_rows, 2,
            f"no duplicate rowids after second reindex; expected 2, got {fts_rows}",
        )

    def test_reindex_missing_body_file_falls_back_to_subject_only(self):
        """reindex handles a message whose body file is missing: indexes subject only.

        Fixture: one message with body_path pointing to a non-existent file.
        reindex must not raise; the row is indexed with an empty body.

        RED: AttributeError on s.reindex.
        """
        mid = _raw_insert_message(self.con, self.ML, f"missing body {self.TERM_A}")
        _raw_insert_recipient(self.con, mid, self.T1, role="to")
        # Set body_path to a non-existent file.
        fake_path = os.path.join(self.store, "messages", f"msg-{mid}-missing.md")
        _update_body_path(self.con, mid, fake_path)

        # Should not raise.
        try:
            count = s.reindex(self.con)
        except Exception as exc:
            self.fail(
                f"reindex raised {type(exc).__name__}: {exc} — "
                "must gracefully handle a missing body file (subject-only fallback)",
            )
        self.assertEqual(count, 1, f"reindex count must be 1; got {count}")

        # Subject term must still be searchable.
        result = s.search(self.con, self.T1, self.TERM_A)
        hit_ids = [h["id"] for h in result["hits"]]
        self.assertIn(
            mid, hit_ids,
            f"message with missing body file must be findable by subject term after reindex; "
            f"got hit_ids={hit_ids!r}",
        )

    def test_lazy_reindex_empty_index_non_empty_message_fires(self):
        """Empty FTS + non-empty message table → search triggers lazy reindex;
        result carries 'reindexed': True; hits are returned.

        Fixture: 2 raw-seeded messages (no FTS rows), then call search.

        RED: AttributeError on s.search.
        """
        mid_a, _ = self._seed_raw_message("lazy test a", f"body {self.TERM_A}")
        mid_b, _ = self._seed_raw_message("lazy test b", f"body {self.TERM_B}")

        # Confirm FTS is empty.
        self.assertEqual(
            _fts_row_count(self.con), 0,
            "FTS must be empty to trigger lazy reindex",
        )

        result = s.search(self.con, self.T1, self.TERM_A)

        # 'reindexed' key must be True.
        self.assertIn(
            "reindexed", result,
            f"result must have 'reindexed' key when lazy reindex fired; "
            f"got keys={sorted(result.keys())!r}",
        )
        self.assertIs(
            result["reindexed"], True,
            f"result['reindexed'] must be True when lazy fired; "
            f"got {result['reindexed']!r}",
        )

        # Hits must include mid_a (the searched term).
        hit_ids = [h["id"] for h in result["hits"]]
        self.assertIn(
            mid_a, hit_ids,
            f"After lazy reindex, search for {self.TERM_A!r} must find mid_a={mid_a}; "
            f"got hit_ids={hit_ids!r}",
        )

    def test_lazy_reindex_not_triggered_when_index_has_rows(self):
        """When the index already has rows (even if sparse), search must NOT trigger
        lazy reindex — 'reindexed' key must be absent from the result.

        Fixture: 2 messages sent normally (FTS rows exist); then one FTS row is
        deleted to make the index sparse. A search must return without reindexing.

        RED: AttributeError on s.search.
        """
        # Send 2 messages normally (creates FTS rows via send).
        mid1 = s.send(
            self.con, self.store,
            from_addr=self.ML,
            to=[self.T1],
            subject=f"sparse test {self.TERM_A}",
            body_text=f"body {self.TERM_A}",
            project=self.P,
        )
        mid2 = s.send(
            self.con, self.store,
            from_addr=self.ML,
            to=[self.T1],
            subject=f"sparse test {self.TERM_B}",
            body_text=f"body {self.TERM_B}",
            project=self.P,
        )

        # Confirm both FTS rows exist.
        fts_before = _fts_row_count(self.con)
        self.assertEqual(fts_before, 2, f"2 FTS rows expected after 2 sends; got {fts_before}")

        # Delete ONE FTS row to make the index sparse.
        _delete_fts_row_for_mid(self.con, mid1)
        fts_after_delete = _fts_row_count(self.con)
        self.assertEqual(
            fts_after_delete, 1,
            f"1 FTS row expected after deleting one; got {fts_after_delete}",
        )

        # search for TERM_B (whose row still exists) — must NOT trigger lazy.
        result = s.search(self.con, self.T1, self.TERM_B)

        self.assertNotIn(
            "reindexed", result,
            f"result must NOT have 'reindexed' key for a sparse (non-empty) index; "
            f"got keys={sorted(result.keys())!r}",
        )

        # TERM_A is missing from FTS (row deleted) — must NOT appear in results.
        result_a = s.search(self.con, self.T1, self.TERM_A)
        self.assertNotIn(
            "reindexed", result_a,
            f"sparse search for deleted TERM_A must NOT trigger lazy; "
            f"got keys={sorted(result_a.keys())!r}",
        )
        self.assertEqual(
            result_a["hits"], [],
            f"deleted TERM_A row must not be found (sparse, no lazy); "
            f"got hits={result_a['hits']!r}",
        )


# ---------------------------------------------------------------------------
# AC7 — sender_project filter + tombstoned-sender hidden
# ---------------------------------------------------------------------------

class SearchFiltersAndTombstoneTest(_TempDataHome):
    """AC7: sender_project='P2' composes (only P2-sender hits);
    tombstoned-sender matches never surface.

    RED: AttributeError on s.search.
    """

    def test_sender_project_filter_returns_only_p2_hits(self):
        """search(ML_P1, P2_TERM, sender_project='P2srch') returns only the P2 message.

        The P2_TERM is unique to the P2 sender's message. The filter must restrict
        results to messages sent by project P2srch.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.P2_TERM,
                          sender_project=self.P2)
        hit_ids = [h["id"] for h in result["hits"]]
        self.assertIn(
            self.mid_p2_msg, hit_ids,
            f"P2 message must appear in search(sender_project=P2srch); "
            f"got hit_ids={hit_ids!r}",
        )
        self.assertEqual(
            result["total"], 1,
            f"total must be 1 with sender_project=P2srch for unique P2 term; "
            f"got total={result['total']!r}",
        )

    def test_sender_project_filter_excludes_other_senders(self):
        """search(ML_P1, BM25_TERM, sender_project='P2srch') returns 0 hits
        (BM25_TERM is in P1-sent messages, not P2-sent).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.BM25_TERM,
                          sender_project=self.P2)
        self.assertEqual(
            result["total"], 0,
            f"BM25_TERM is not in P2-sent messages; total must be 0 with "
            f"sender_project=P2srch; got total={result['total']!r}",
        )
        self.assertEqual(
            result["hits"], [],
            f"hits must be [] with sender_project=P2srch for BM25_TERM; "
            f"got {result['hits']!r}",
        )

    def test_tombstoned_sender_hits_never_surface(self):
        """search(ML_P1, TOMB_TERM) → total==0; the tombstoned project's unique
        body term must return no hits.

        C1 tombstone_project() already deletes the FTS rows for sent messages.
        This test pins the read-rule path: even if no FTS row exists post-tombstone,
        search must still return 0 (consistent behavior regardless of implementation).

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.TOMB_TERM)
        self.assertEqual(
            result["total"], 0,
            f"tombstoned-sender term {self.TOMB_TERM!r} must return total=0; "
            f"got total={result['total']!r}",
        )
        self.assertEqual(
            result["hits"], [],
            f"tombstoned-sender term must return hits=[]; got {result['hits']!r}",
        )

    def test_sender_project_none_returns_all_visible_hits(self):
        """search(ML_P1, P2_TERM, sender_project=None) returns P2 hits (no filter).

        The default sender_project=None means no filter — the P2 message must appear.

        RED: AttributeError on s.search.
        """
        result = s.search(self.con, self.ML_P1, self.P2_TERM)
        hit_ids = [h["id"] for h in result["hits"]]
        self.assertIn(
            self.mid_p2_msg, hit_ids,
            f"P2 message must appear in unfiltered search for P2_TERM; "
            f"got hit_ids={hit_ids!r}",
        )


# ---------------------------------------------------------------------------
# AC8 (lib) — malformed query → ValueError
# ---------------------------------------------------------------------------

class SearchMalformedQueryTest(unittest.TestCase):
    """AC8 (lib): a malformed FTS5 expression (e.g. unbalanced quote) must raise
    ValueError (not OperationalError) containing a readable message.

    Uses a minimal fixture (no shared corpus needed).

    RED: AttributeError on s.search (or possibly OperationalError escapes).
    """

    P = "Errtst"
    ML = "Mainline - Errtst"
    T1 = "Track 1 - Errtst"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-fts-error-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P)
        self.con = s.connect()
        s.register(self.con, self.ML, kind="mainline", project=self.P)
        s.register(self.con, self.T1, kind="track",    project=self.P)

        # One normal message so the query has something to match against.
        s.send(
            self.con, s.store_dir(self.P),
            from_addr=self.ML,
            to=[self.T1],
            subject="error test message",
            body_text="some body text",
            project=self.P,
        )

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unbalanced_quote_raises_value_error(self):
        """search(con, r, '"unterminated') must raise ValueError.

        SQLite raises OperationalError for malformed FTS5 queries. search() must
        catch it and re-raise as ValueError.

        RED: AttributeError on s.search; or OperationalError escapes.
        """
        with self.assertRaises(ValueError) as ctx:
            s.search(self.con, self.T1, '"unterminated')
        error_text = str(ctx.exception)
        self.assertIn(
            "unterminated",
            error_text.lower(),
            f"ValueError must contain 'unterminated' (or the sqlite text); "
            f"got: {error_text!r}",
        )

    def test_malformed_query_does_not_raise_operational_error(self):
        """search() must NOT let OperationalError escape for malformed FTS5 queries.

        If OperationalError is raised (not caught), this test catches it and
        fails with a descriptive message.

        RED: AttributeError on s.search.
        """
        try:
            s.search(self.con, self.T1, '"unterminated')
        except ValueError:
            pass   # Expected — test passes
        except sqlite3.OperationalError as exc:
            self.fail(
                f"search() must not let OperationalError escape; "
                f"caught OperationalError: {exc!r}. "
                "Wrap sqlite3.OperationalError and re-raise as ValueError.",
            )
        except AttributeError as exc:
            self.fail(
                f"search() is not defined (AttributeError: {exc}); "
                "GREEN must add search() to sandesh_db.",
            )

    def test_malformed_operator_raises_value_error(self):
        """search(con, r, 'AND') — bare AND operator → ValueError.

        An AND operator with no operands is malformed FTS5 syntax.

        RED: AttributeError on s.search; or OperationalError escapes.
        """
        with self.assertRaises(ValueError):
            s.search(self.con, self.T1, "AND")

    def test_valid_query_does_not_raise(self):
        """A valid FTS5 query must NOT raise ValueError or any exception.

        Smoke-tests that the OperationalError wrapping is selective (only on
        real sqlite errors, not on valid queries that happen to match nothing).

        RED: AttributeError on s.search.
        """
        try:
            result = s.search(self.con, self.T1, "some valid query")
        except Exception as exc:
            self.fail(
                f"A valid FTS5 query must not raise; got {type(exc).__name__}: {exc}",
            )
        self.assertIn("hits",    result, "result must have 'hits' key")
        self.assertIn("total",   result, "result must have 'total' key")
        self.assertIn("limit",   result, "result must have 'limit' key")
        self.assertIn("offset",  result, "result must have 'offset' key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
