"""test_consolidation.py — RED tests for CR-SAN-022 Cycle 5.

Covers AC5 + AC1 (backfill clause) + installer hook:
  AC5 — sandesh_db.consolidate() imports legacy per-project stores into the
         global DB with correct id remapping, in_reply_to chain preservation,
         dangling-ref → NULL, body_path verbatim (files unmoved), addresses
         with project populated, projects enrolled active, legacy files renamed
         .pre-global, second run is a no-op, notifier rows NOT imported.
  CLI  — `sandesh consolidate` subcommand: exit 0, summary, empty data-home
         → clean no-op.
  install.sh — contains a `consolidate` invocation placed AFTER the migrate
               block, NOT inside the yoyo-probe conditional.

Expected RED failures (against current code, pre-GREEN C5):
  - sandesh_db.consolidate does not exist → AttributeError at call time
  - CLI subcommand 'consolidate' not registered → argparse error / exit 2
  - install.sh content assertions fail (no consolidate call present)

Run via the Crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_consolidation --agent red-cr022-c5
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli


# ---------------------------------------------------------------------------
# Pre-0003 legacy DDL fixtures (verbatim old schema WITHOUT address.project)
# ---------------------------------------------------------------------------
# This is the DDL that existed BEFORE CR-SAN-022 C1 — used to build legacy
# stores to exercise the consolidation path.

# Store A — pre-0002 shape: message table HAS a status column.
_LEGACY_DDL_PRE0002 = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    kind        TEXT,
    in_reply_to INTEGER REFERENCES message(id),
    body_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',
    read_at    TEXT,
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,
    pid          INTEGER,
    token        TEXT,
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE
);
"""

# Store B — pre-0003 shape: message table does NOT have status (0002 already
# applied), but address table still has NO project column.
_LEGACY_DDL_PRE0003 = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    kind        TEXT,
    in_reply_to INTEGER REFERENCES message(id),
    body_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',
    read_at    TEXT,
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,
    pid          INTEGER,
    token        TEXT,
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def _legacy_db_path(data_home, project_id):
    """Legacy per-project DB path: <data_home>/sandesh/projects/<id>/sandesh.db"""
    return os.path.join(data_home, "sandesh", "projects", project_id, "sandesh.db")


def _legacy_msg_dir(data_home, project_id):
    """Legacy per-project messages dir."""
    return os.path.join(data_home, "sandesh", "projects", project_id, "messages")


def _build_legacy_store_alpha(data_home):
    """Build legacy store for project 'Alpha'.

    Uses pre-0002 DDL (status column present). Seeds:
      - 2 addresses: 'Mainline - Alpha', 'Track 1 - Alpha'
      - 3 messages with ids 1, 2, 3:
          #1 top-level from Mainline (subject-only)
          #2 top-level from Track 1 with a body file
          #3 reply to #1 (in_reply_to=1) — internal chain
      - message_recipient rows including one cc and one with read_at set
      - 1 notifier row (must NOT be imported)
    Returns (db_path, body_file_path).
    """
    project_id = "Alpha"
    db_path = _legacy_db_path(data_home, project_id)
    msg_dir = _legacy_msg_dir(data_home, project_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(msg_dir, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(_LEGACY_DDL_PRE0002)

    # Addresses — no project column in this DDL
    con.execute("INSERT INTO address (address, kind, active) VALUES (?,?,?)",
                ("Mainline - Alpha", "mainline", 1))
    con.execute("INSERT INTO address (address, kind, active) VALUES (?,?,?)",
                ("Track 1 - Alpha", "track", 1))

    # Body file for message #2
    body_path = os.path.join(msg_dir, "msg-2.md")
    with open(body_path, "w", encoding="utf-8") as fh:
        fh.write("Alpha body content for message 2.\n")

    # Messages with explicit ids (collide with Beta: both stores use 1,2,3)
    con.execute(
        "INSERT INTO message (id, from_addr, subject, status, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?,?)",
        (1, "Mainline - Alpha", "Alpha top-level request", "open", "request", None, None))
    con.execute(
        "INSERT INTO message (id, from_addr, subject, status, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?,?)",
        (2, "Track 1 - Alpha", "Alpha track report", "open", "fyi", None, body_path))
    con.execute(
        "INSERT INTO message (id, from_addr, subject, status, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?,?)",
        (3, "Track 1 - Alpha", "Re: Alpha top-level request", "open", "reply", 1, None))

    # message_recipient rows
    # #1 → Mainline - Alpha (to, unread)
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (1, "Mainline - Alpha", "to", None))
    # #1 → Track 1 - Alpha (cc, unread)
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (1, "Track 1 - Alpha", "cc", None))
    # #2 → Mainline - Alpha (to, READ)
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (2, "Mainline - Alpha", "to", "2026-01-01 10:00:00"))
    # #3 → Mainline - Alpha (to, unread) — the reply
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (3, "Mainline - Alpha", "to", None))

    # Notifier row — must NOT be imported by consolidate()
    con.execute(
        "INSERT INTO notifier (recipient, pid, token, tombstone) VALUES (?,?,?,?)",
        ("Mainline - Alpha", 99999, "tok-alpha", 0))

    con.commit()
    con.close()
    return db_path, body_path


def _build_legacy_store_beta(data_home):
    """Build legacy store for project 'Beta'.

    Uses pre-0003 DDL (no status column). Seeds:
      - 2 addresses: 'Mainline - Beta', 'Track 1 - Beta'
      - 3 messages with ids 1, 2, 3 (COLLIDE with Alpha):
          #1 top-level from Mainline
          #2 top-level from Track 1
          #3 reply to #1 (in_reply_to=1) — internal chain
          ALSO: message #3 has a DANGLING in_reply_to=99 (set explicitly)
            → we use a SEPARATE fixture for this; here we set one normal chain
            and let the dangling-ref test override it.
      Wait — the spec says "store B has one dangling in_reply_to=99".
      We place the dangling ref on message #2 in Beta (in_reply_to=99, which
      does NOT exist in the legacy store).
      #3 is the normal reply to #1.
    Returns db_path.
    """
    project_id = "Beta"
    db_path = _legacy_db_path(data_home, project_id)
    msg_dir = _legacy_msg_dir(data_home, project_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(msg_dir, exist_ok=True)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(_LEGACY_DDL_PRE0003)

    # Addresses
    con.execute("INSERT INTO address (address, kind, active) VALUES (?,?,?)",
                ("Mainline - Beta", "mainline", 1))
    con.execute("INSERT INTO address (address, kind, active) VALUES (?,?,?)",
                ("Track 1 - Beta", "track", 1))

    # Messages: same ids as Alpha (1,2,3) — the remap must separate them
    # #2 has a dangling in_reply_to=99 (parent absent in this legacy store)
    con.execute(
        "INSERT INTO message (id, from_addr, subject, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?)",
        (1, "Mainline - Beta", "Beta top-level directive", "directive", None, None))
    con.execute(
        "INSERT INTO message (id, from_addr, subject, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?)",
        (2, "Track 1 - Beta", "Beta dangling reply", "reply", 99, None))
    con.execute(
        "INSERT INTO message (id, from_addr, subject, kind, in_reply_to, body_path) "
        "VALUES (?,?,?,?,?,?)",
        (3, "Mainline - Beta", "Re: Beta top-level directive", "reply", 1, None))

    # message_recipient rows
    # #1 → Track 1 - Beta (to, unread)
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (1, "Track 1 - Beta", "to", None))
    # #2 → Mainline - Beta (to, unread)
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (2, "Mainline - Beta", "to", None))
    # #3 → Track 1 - Beta (cc, unread) — a cc to test role verbatim
    con.execute(
        "INSERT INTO message_recipient (message_id, recipient, role, read_at) VALUES (?,?,?,?)",
        (3, "Track 1 - Beta", "cc", None))

    con.commit()
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Mixin: per-test isolated XDG_DATA_HOME with two legacy stores (Alpha + Beta)
    and a clean global DB (no prior setup — consolidate must create it).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-consolidation-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        # Point XDG at our temp dir — the sandesh root is tmp/sandesh/
        os.environ["XDG_DATA_HOME"] = self.tmp

        # Build legacy stores BEFORE initialising the global DB so that
        # consolidate() finds them as pre-existing legacy files.
        self._alpha_db, self._alpha_body = _build_legacy_store_alpha(self.tmp)
        self._beta_db = _build_legacy_store_beta(self.tmp)

        # Initialise the global DB (creates the schema) without any setup()
        # calls so the projects table starts empty — consolidate must enroll.
        self.con = s.connect()

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process and capture stdout. Returns (rc, output_str)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, buf.getvalue()

    def _pre_global_path(self, project_id):
        return os.path.join(
            self.tmp, "sandesh", "projects", project_id, "sandesh.db.pre-global")

    def _legacy_db_active_path(self, project_id):
        return _legacy_db_path(self.tmp, project_id)

    def _msg_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message").fetchone()[0]

    def _recip_count(self):
        return self.con.execute("SELECT COUNT(*) FROM message_recipient").fetchone()[0]

    def _addr_count(self):
        return self.con.execute("SELECT COUNT(*) FROM address").fetchone()[0]

    def _project_count(self):
        return self.con.execute("SELECT COUNT(*) FROM project").fetchone()[0]

    def _notifier_count(self):
        return self.con.execute("SELECT COUNT(*) FROM notifier").fetchone()[0]


# ---------------------------------------------------------------------------
# T1: consolidate() exists on sandesh_db (AttributeError = RED signal)
# ---------------------------------------------------------------------------

class ConsolidateFunctionExistsTest(_TempDataHome):
    """sandesh_db.consolidate must be callable.

    RED: the function does not exist yet → AttributeError at call time.
    The test will raise AttributeError which JUnit captures as an ERROR (RED).
    """

    def test_consolidate_attribute_exists(self):
        """sandesh_db.consolidate must be a callable attribute.

        RED: AttributeError — the function is not yet implemented.
        """
        self.assertTrue(
            callable(getattr(s, "consolidate", None)),
            "sandesh_db.consolidate is not callable — implement it (GREEN).",
        )

    def test_consolidate_returns_list(self):
        """consolidate() must return a list (of per-project summary dicts).

        RED: function absent → AttributeError.
        """
        result = s.consolidate()
        self.assertIsInstance(
            result, list,
            f"consolidate() must return a list, got {type(result).__name__!r}",
        )


# ---------------------------------------------------------------------------
# T2: All 6 messages in the global DB with unique ids
# ---------------------------------------------------------------------------

class MessageImportTest(_TempDataHome):
    """After consolidate(), all 6 legacy messages appear in the global DB
    with globally unique ids — no collisions between Alpha and Beta.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        self._summary = s.consolidate()

    def test_six_messages_imported(self):
        """Global DB must contain exactly 6 messages after consolidating two 3-msg stores."""
        count = self._msg_count()
        self.assertEqual(
            count, 6,
            f"Expected 6 messages in global DB after consolidation, got {count}.",
        )

    def test_all_message_ids_unique(self):
        """All 6 message ids must be distinct (no id collision between projects)."""
        rows = self.con.execute("SELECT id FROM message ORDER BY id").fetchall()
        ids = [r[0] for r in rows]
        self.assertEqual(
            len(ids), len(set(ids)),
            f"Duplicate message ids after consolidation: {ids}",
        )

    def test_alpha_chain_intact_after_remap(self):
        """Alpha's reply chain must survive remapping: the Re:… message's
        in_reply_to must point at the remapped Alpha top-level message id.

        The chain: msg with subject 'Re: Alpha top-level request' must have
        in_reply_to pointing at the msg with subject 'Alpha top-level request'.
        """
        # Find the two alpha messages by subject
        parent = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Alpha top-level request",),
        ).fetchone()
        reply_msg = self.con.execute(
            "SELECT id, in_reply_to FROM message WHERE subject=?",
            ("Re: Alpha top-level request",),
        ).fetchone()

        self.assertIsNotNone(parent, "Alpha top-level request not found in global DB")
        self.assertIsNotNone(reply_msg, "Re: Alpha top-level request not found in global DB")

        self.assertEqual(
            reply_msg["in_reply_to"], parent["id"],
            f"Alpha chain broken: reply in_reply_to={reply_msg['in_reply_to']} "
            f"but parent id={parent['id']}",
        )

    def test_beta_chain_intact_after_remap(self):
        """Beta's reply chain must survive remapping: the Re:… Beta message's
        in_reply_to must point at the remapped Beta top-level message id.
        """
        parent = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Beta top-level directive",),
        ).fetchone()
        reply_msg = self.con.execute(
            "SELECT id, in_reply_to FROM message WHERE subject=?",
            ("Re: Beta top-level directive",),
        ).fetchone()

        self.assertIsNotNone(parent, "Beta top-level directive not found in global DB")
        self.assertIsNotNone(reply_msg, "Re: Beta top-level directive not found in global DB")

        self.assertEqual(
            reply_msg["in_reply_to"], parent["id"],
            f"Beta chain broken: reply in_reply_to={reply_msg['in_reply_to']} "
            f"but parent id={parent['id']}",
        )

    def test_dangling_in_reply_to_becomes_null(self):
        """Beta message #2 (in_reply_to=99, absent from legacy store) must have
        in_reply_to=NULL after consolidation.
        """
        row = self.con.execute(
            "SELECT in_reply_to FROM message WHERE subject=?",
            ("Beta dangling reply",),
        ).fetchone()
        self.assertIsNotNone(row, "Beta dangling reply message not found in global DB")
        self.assertIsNone(
            row["in_reply_to"],
            f"Dangling in_reply_to must be NULL after consolidation, "
            f"got {row['in_reply_to']!r}",
        )

    def test_alpha_body_path_verbatim(self):
        """Alpha message #2's body_path must be retained verbatim (absolute path, file unmoved)."""
        row = self.con.execute(
            "SELECT body_path FROM message WHERE subject=?",
            ("Alpha track report",),
        ).fetchone()
        self.assertIsNotNone(row, "Alpha track report not found in global DB")
        self.assertIsNotNone(row["body_path"], "body_path must not be NULL for Alpha track report")
        self.assertTrue(
            os.path.isfile(row["body_path"]),
            f"body_path {row['body_path']!r} does not point to an existing file "
            f"— files must not move during consolidation",
        )

    def test_summary_has_required_keys(self):
        """Each summary dict must contain 'project_id', 'messages_imported',
        'addresses_imported' keys.
        """
        self.assertEqual(
            len(self._summary), 2,
            f"Expected 2 summary entries (Alpha + Beta), got {len(self._summary)}",
        )
        for entry in self._summary:
            for key in ("project_id", "messages_imported", "addresses_imported"):
                self.assertIn(
                    key, entry,
                    f"Summary entry missing key {key!r}: {entry}",
                )

    def test_summary_counts_correct(self):
        """Each summary entry must report 3 messages and 2 addresses imported."""
        by_project = {e["project_id"]: e for e in self._summary}
        for proj in ("Alpha", "Beta"):
            self.assertIn(proj, by_project, f"Missing summary entry for {proj!r}")
            entry = by_project[proj]
            self.assertEqual(
                entry["messages_imported"], 3,
                f"{proj}: expected 3 messages_imported, got {entry['messages_imported']}",
            )
            self.assertEqual(
                entry["addresses_imported"], 2,
                f"{proj}: expected 2 addresses_imported, got {entry['addresses_imported']}",
            )


# ---------------------------------------------------------------------------
# T3: message_recipient rows — remap + role/read_at verbatim
# ---------------------------------------------------------------------------

class RecipientImportTest(_TempDataHome):
    """message_recipient rows must be remapped (message_id) and carry
    role + read_at verbatim from the legacy store.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()

    def test_all_recipient_rows_imported(self):
        """Global DB must have exactly 7 message_recipient rows (4 Alpha + 3 Beta)."""
        count = self._recip_count()
        self.assertEqual(
            count, 7,
            f"Expected 7 message_recipient rows, got {count}.",
        )

    def test_read_at_preserved_for_read_message(self):
        """Alpha message #2 was read by Mainline - Alpha; read_at must carry over."""
        # Find the remapped message id for Alpha track report
        row = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Alpha track report",),
        ).fetchone()
        self.assertIsNotNone(row, "Alpha track report not found in global DB")

        recip_row = self.con.execute(
            "SELECT read_at FROM message_recipient WHERE message_id=? AND recipient=?",
            (row["id"], "Mainline - Alpha"),
        ).fetchone()
        self.assertIsNotNone(
            recip_row,
            "No message_recipient row for Mainline-Alpha on Alpha track report",
        )
        self.assertIsNotNone(
            recip_row["read_at"],
            "read_at must be non-NULL for already-read Alpha message #2",
        )

    def test_unread_stays_unread(self):
        """Alpha message #1 is unread for Mainline - Alpha; read_at must be NULL."""
        row = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Alpha top-level request",),
        ).fetchone()
        self.assertIsNotNone(row)

        recip_row = self.con.execute(
            "SELECT read_at FROM message_recipient WHERE message_id=? AND recipient=?",
            (row["id"], "Mainline - Alpha"),
        ).fetchone()
        self.assertIsNotNone(recip_row)
        self.assertIsNone(
            recip_row["read_at"],
            "Unread message must remain unread after consolidation",
        )

    def test_cc_role_preserved(self):
        """Alpha message #1 has a cc recipient (Track 1 - Alpha); role must be 'cc'."""
        row = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Alpha top-level request",),
        ).fetchone()
        self.assertIsNotNone(row)

        recip_row = self.con.execute(
            "SELECT role FROM message_recipient WHERE message_id=? AND recipient=?",
            (row["id"], "Track 1 - Alpha"),
        ).fetchone()
        self.assertIsNotNone(
            recip_row,
            "No cc recipient row for Track 1 - Alpha on Alpha top-level request",
        )
        self.assertEqual(
            recip_row["role"], "cc",
            f"Expected role='cc', got {recip_row['role']!r}",
        )

    def test_beta_cc_role_preserved(self):
        """Beta message #3 has a cc recipient (Track 1 - Beta); role must be 'cc'."""
        row = self.con.execute(
            "SELECT id FROM message WHERE subject=?",
            ("Re: Beta top-level directive",),
        ).fetchone()
        self.assertIsNotNone(row, "Re: Beta top-level directive not found")

        recip_row = self.con.execute(
            "SELECT role FROM message_recipient WHERE message_id=? AND recipient=?",
            (row["id"], "Track 1 - Beta"),
        ).fetchone()
        self.assertIsNotNone(
            recip_row,
            "No message_recipient row for Track 1 - Beta on Beta reply",
        )
        self.assertEqual(
            recip_row["role"], "cc",
            f"Expected role='cc' for Beta cc row, got {recip_row['role']!r}",
        )


# ---------------------------------------------------------------------------
# T4: body_path fetch() — file unmoved, content readable via fetch()
# ---------------------------------------------------------------------------

class BodyPathFetchTest(_TempDataHome):
    """After consolidation, fetch() for the recipient of the body message must
    return the body content from the (unmoved) file.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()
        # Re-open the connection to pick up consolidated data
        self.con.close()
        self.con = s.connect()

    def test_body_file_content_readable_via_fetch(self):
        """fetch() for Mainline - Alpha must include the body text from the
        Alpha track report message (body_path points to the unmoved file).

        The message was marked read in the legacy store; we verify the body_path
        is intact by opening it directly (since fetch() only returns unread msgs
        with mark=True by default — we use mark=False + unread_only=False via
        inbox with unread_only=False).
        """
        store_alpha = s.store_dir("Alpha")

        # Check via inbox(unread_only=False) to see ALL messages for Mainline-Alpha
        all_rows = s.inbox(self.con, "Mainline - Alpha", unread_only=False)
        body_msg = None
        for r in all_rows:
            if r["subject"] == "Alpha track report":
                body_msg = r
                break

        self.assertIsNotNone(
            body_msg,
            "Alpha track report not found in inbox for Mainline - Alpha",
        )
        self.assertIsNotNone(
            body_msg["body_path"],
            "body_path must not be NULL for Alpha track report",
        )
        # The file must still exist at the original absolute path
        self.assertTrue(
            os.path.isfile(body_msg["body_path"]),
            f"Body file not found at {body_msg['body_path']!r} after consolidation",
        )
        with open(body_msg["body_path"], encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn(
            "Alpha body content", content,
            f"Body file content incorrect: {content!r}",
        )

    def test_messages_dir_untouched(self):
        """The legacy messages/ directory (and the body file) must still exist
        after consolidation — files are NOT moved.
        """
        self.assertTrue(
            os.path.isfile(self._alpha_body),
            f"Alpha body file was removed during consolidation: {self._alpha_body!r}",
        )
        self.assertTrue(
            os.path.isdir(_legacy_msg_dir(self.tmp, "Alpha")),
            "Alpha messages dir was removed during consolidation",
        )


# ---------------------------------------------------------------------------
# T5: Addresses imported + project populated + projects enrolled
# ---------------------------------------------------------------------------

class AddressAndProjectImportTest(_TempDataHome):
    """After consolidate():
    - 4 address rows in the global DB (2 Alpha + 2 Beta)
    - Each has project populated ('Alpha' or 'Beta')
    - Both projects enrolled active in the tracker
    - list_projects() shows both

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()

    def test_four_addresses_imported(self):
        """Global DB must have exactly 4 address rows."""
        count = self._addr_count()
        self.assertEqual(
            count, 4,
            f"Expected 4 addresses, got {count}.",
        )

    def test_alpha_addresses_have_project_populated(self):
        """Alpha address rows must have project='Alpha' after consolidation."""
        rows = self.con.execute(
            "SELECT address, project FROM address WHERE address LIKE '% - Alpha'",
        ).fetchall()
        self.assertEqual(len(rows), 2, "Expected 2 Alpha address rows")
        for r in rows:
            self.assertEqual(
                r["project"], "Alpha",
                f"address {r['address']!r} has project={r['project']!r}, expected 'Alpha'",
            )

    def test_beta_addresses_have_project_populated(self):
        """Beta address rows must have project='Beta' after consolidation."""
        rows = self.con.execute(
            "SELECT address, project FROM address WHERE address LIKE '% - Beta'",
        ).fetchall()
        self.assertEqual(len(rows), 2, "Expected 2 Beta address rows")
        for r in rows:
            self.assertEqual(
                r["project"], "Beta",
                f"address {r['address']!r} has project={r['project']!r}, expected 'Beta'",
            )

    def test_both_projects_enrolled_active(self):
        """Both Alpha and Beta must appear in the project tracker with state='active'."""
        for proj in ("Alpha", "Beta"):
            row = self.con.execute(
                "SELECT state FROM project WHERE project_id=?", (proj,),
            ).fetchone()
            self.assertIsNotNone(
                row, f"Project {proj!r} not enrolled in tracker after consolidation",
            )
            self.assertEqual(
                row["state"], "active",
                f"Project {proj!r} has state={row['state']!r}, expected 'active'",
            )

    def test_list_projects_shows_both(self):
        """list_projects() must return both 'Alpha' and 'Beta' after consolidation."""
        projects = s.list_projects()
        self.assertIn("Alpha", projects, "Alpha not in list_projects() after consolidation")
        self.assertIn("Beta", projects, "Beta not in list_projects() after consolidation")
        # Exact count — no phantom projects
        self.assertEqual(
            len(projects), 2,
            f"Expected exactly 2 projects, got {len(projects)}: {projects}",
        )


# ---------------------------------------------------------------------------
# T6: Legacy files renamed .pre-global; original gone
# ---------------------------------------------------------------------------

class LegacyFileRenameTest(_TempDataHome):
    """After consolidate(), each legacy sandesh.db is renamed to sandesh.db.pre-global
    and the original sandesh.db path no longer exists.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()

    def test_alpha_pre_global_exists(self):
        """.pre-global file must exist for Alpha after consolidation."""
        pre = self._pre_global_path("Alpha")
        self.assertTrue(
            os.path.isfile(pre),
            f"Expected {pre!r} to exist after consolidation",
        )

    def test_alpha_legacy_db_gone(self):
        """Original Alpha sandesh.db must be absent (renamed away)."""
        legacy = self._legacy_db_active_path("Alpha")
        self.assertFalse(
            os.path.exists(legacy),
            f"Legacy DB {legacy!r} must not exist after consolidation (should be .pre-global)",
        )

    def test_beta_pre_global_exists(self):
        """.pre-global file must exist for Beta after consolidation."""
        pre = self._pre_global_path("Beta")
        self.assertTrue(
            os.path.isfile(pre),
            f"Expected {pre!r} to exist after consolidation",
        )

    def test_beta_legacy_db_gone(self):
        """Original Beta sandesh.db must be absent (renamed away)."""
        legacy = self._legacy_db_active_path("Beta")
        self.assertFalse(
            os.path.exists(legacy),
            f"Legacy DB {legacy!r} must not exist after consolidation (should be .pre-global)",
        )

    def test_messages_dir_untouched(self):
        """The messages/ dir (body files) must not be touched by consolidation."""
        for proj in ("Alpha", "Beta"):
            msg_dir = _legacy_msg_dir(self.tmp, proj)
            self.assertTrue(
                os.path.isdir(msg_dir),
                f"messages/ dir for {proj!r} was removed by consolidation: {msg_dir!r}",
            )


# ---------------------------------------------------------------------------
# T7: Idempotency — second run is a no-op
# ---------------------------------------------------------------------------

class IdempotencyTest(_TempDataHome):
    """A second consolidate() call must import nothing and leave row counts unchanged.

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()   # first run
        # Record counts after first run
        self._msg_after_first = self._msg_count()
        self._recip_after_first = self._recip_count()
        self._addr_after_first = self._addr_count()
        self._proj_after_first = self._project_count()
        # Second run
        self._second_summary = s.consolidate()

    def test_second_run_returns_empty_or_zero_imported(self):
        """Second consolidate() must report zero imported for all projects
        (either empty list or entries with 0 counts).
        """
        total_msgs = sum(e.get("messages_imported", 0) for e in self._second_summary)
        total_addrs = sum(e.get("addresses_imported", 0) for e in self._second_summary)
        self.assertEqual(
            total_msgs, 0,
            f"Second consolidate() imported {total_msgs} messages — must be 0 (idempotent)",
        )
        self.assertEqual(
            total_addrs, 0,
            f"Second consolidate() imported {total_addrs} addresses — must be 0 (idempotent)",
        )

    def test_message_count_unchanged_after_second_run(self):
        """Message count must be identical after the second consolidate() call."""
        count_after_second = self._msg_count()
        self.assertEqual(
            count_after_second, self._msg_after_first,
            f"Message count changed on second run: was {self._msg_after_first}, "
            f"now {count_after_second}",
        )

    def test_recipient_count_unchanged_after_second_run(self):
        """message_recipient count must be identical after the second run."""
        count_after_second = self._recip_count()
        self.assertEqual(
            count_after_second, self._recip_after_first,
            f"Recipient count changed on second run: was {self._recip_after_first}, "
            f"now {count_after_second}",
        )

    def test_address_count_unchanged_after_second_run(self):
        """Address count must be identical after the second run."""
        count_after_second = self._addr_count()
        self.assertEqual(
            count_after_second, self._addr_after_first,
            f"Address count changed on second run: was {self._addr_after_first}, "
            f"now {count_after_second}",
        )

    def test_project_count_unchanged_after_second_run(self):
        """Project tracker count must be identical after the second run."""
        count_after_second = self._project_count()
        self.assertEqual(
            count_after_second, self._proj_after_first,
            f"Project count changed on second run: was {self._proj_after_first}, "
            f"now {count_after_second}",
        )


# ---------------------------------------------------------------------------
# T8: Notifier rows NOT imported
# ---------------------------------------------------------------------------

class NotifierNotImportedTest(_TempDataHome):
    """consolidate() must NOT import notifier rows from legacy stores.
    (Alpha has one notifier row seeded in its legacy DB.)

    RED: function absent → AttributeError.
    """

    def setUp(self):
        super().setUp()
        s.consolidate()

    def test_no_notifier_rows_in_global_db(self):
        """Global DB must have 0 notifier rows after consolidation."""
        count = self._notifier_count()
        self.assertEqual(
            count, 0,
            f"Expected 0 notifier rows in global DB after consolidation, got {count}. "
            f"Notifier rows must NOT be imported from legacy stores.",
        )


# ---------------------------------------------------------------------------
# T9: CLI subcommand `sandesh consolidate`
# ---------------------------------------------------------------------------

class CliConsolidateTest(_TempDataHome):
    """CLI `sandesh consolidate` (no --project required) must exit 0 and
    print a summary line per imported store.

    RED: the subcommand is not yet registered → argparse error / SystemExit(2).
    """

    def test_cli_consolidate_exits_zero(self):
        """cli.main(['consolidate']) must exit 0.

        RED: subcommand not registered → argparse error, SystemExit(2).
        """
        rc, _ = self._run_cli(["consolidate"])
        self.assertEqual(
            rc, 0,
            f"cli.main(['consolidate']) exited {rc!r}, expected 0. "
            f"The 'consolidate' subcommand is not yet registered (RED).",
        )

    def test_cli_consolidate_summary_mentions_both_projects(self):
        """CLI consolidate output must mention both 'Alpha' and 'Beta'."""
        _, output = self._run_cli(["consolidate"])
        self.assertIn(
            "Alpha", output,
            f"CLI consolidate output does not mention 'Alpha': {output!r}",
        )
        self.assertIn(
            "Beta", output,
            f"CLI consolidate output does not mention 'Beta': {output!r}",
        )

    def test_cli_consolidate_empty_data_home_exits_zero(self):
        """CLI consolidate on a data home with no legacy stores must exit 0 cleanly."""
        # Create a fresh temp dir with NO legacy stores
        fresh_tmp = tempfile.mkdtemp(prefix="sandesh-consolidation-empty-test-")
        orig_xdg = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = fresh_tmp
            # Initialise global DB (empty)
            empty_con = s.connect()
            empty_con.close()

            rc, output = self._run_cli(["consolidate"])
            self.assertEqual(
                rc, 0,
                f"cli.main(['consolidate']) on empty data home exited {rc!r}, expected 0. "
                f"Output: {output!r}",
            )
        finally:
            if orig_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig_xdg
            shutil.rmtree(fresh_tmp, ignore_errors=True)

    def test_cli_consolidate_no_crash_no_traceback(self):
        """CLI consolidate must not crash with a traceback."""
        # If it raises SystemExit(2) that is caught; a raw exception would
        # propagate and fail this test.
        try:
            buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    rc = cli.main(["consolidate"])
                except SystemExit as exc:
                    rc = exc.code
        except Exception as exc:
            self.fail(
                f"cli.main(['consolidate']) raised an unexpected exception: "
                f"{type(exc).__name__}: {exc}",
            )


# T11: GREEN pin — legacy address.project COLUMN honoured (post-0003 legacy store)

# Post-0003 legacy DDL: address table HAS the project column (0003 already
# applied to the per-project store before consolidation).
_LEGACY_DDL_POST0003 = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT,
    project       TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    kind        TEXT,
    in_reply_to INTEGER REFERENCES message(id),
    body_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',
    read_at    TEXT,
    PRIMARY KEY (message_id, recipient)
);
"""


def _build_legacy_store_gamma(data_home):
    """Build legacy store for project 'Gamma' with a post-0003 address table.

    Seeds two addresses:
      - 'Mainline - Gamma' with project COLUMN populated as 'GammaLegacy'
        (deliberately ≠ the derived suffix 'Gamma' so the test can prove the
        column value wins over text derivation), and
      - 'Track 1 - Gamma' with project NULL (must fall back to derivation).
    No messages — this fixture pins only the address.project branch.
    """
    project_id = "Gamma"
    db_path = _legacy_db_path(data_home, project_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(_legacy_msg_dir(data_home, project_id), exist_ok=True)

    con = sqlite3.connect(db_path)
    con.executescript(_LEGACY_DDL_POST0003)
    con.execute(
        "INSERT INTO address (address, kind, active, project) VALUES (?,?,?,?)",
        ("Mainline - Gamma", "mainline", 1, "GammaLegacy"))
    con.execute(
        "INSERT INTO address (address, kind, active, project) VALUES (?,?,?,?)",
        ("Track 1 - Gamma", "track", 1, None))
    con.commit()
    con.close()
    return db_path


class LegacyAddressProjectColumnTest(_TempDataHome):
    """Pin (green-on-arrival): `_legacy_address_project` must read the legacy
    `address.project` COLUMN when it is present and non-NULL (a post-0003
    legacy store), and fall back to deriving from the address text when the
    column value is NULL.
    """

    def setUp(self):
        super().setUp()
        _build_legacy_store_gamma(self.tmp)
        s.consolidate()

    def test_populated_project_column_carried_verbatim(self):
        """An address whose legacy project COLUMN is populated must carry that
        exact value into the global DB (column wins over text derivation)."""
        row = self.con.execute(
            "SELECT project FROM address WHERE address=?",
            ("Mainline - Gamma",),
        ).fetchone()
        self.assertIsNotNone(row, "Mainline - Gamma not imported into the global DB")
        self.assertEqual(
            row["project"], "GammaLegacy",
            f"Expected the legacy column value 'GammaLegacy' verbatim "
            f"(NOT the derived suffix), got {row['project']!r}",
        )

    def test_null_project_column_falls_back_to_derivation(self):
        """An address whose legacy project COLUMN is NULL must get the project
        derived from the address text (suffix after the first ' - ')."""
        row = self.con.execute(
            "SELECT project FROM address WHERE address=?",
            ("Track 1 - Gamma",),
        ).fetchone()
        self.assertIsNotNone(row, "Track 1 - Gamma not imported into the global DB")
        self.assertEqual(
            row["project"], "Gamma",
            f"Expected the derived project 'Gamma' for a NULL legacy column, "
            f"got {row['project']!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
