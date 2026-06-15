"""test_init_command.py — RED tests for CR-SAN-036 §S3 / C2.

Covers AC3, AC4, AC5, AC6 — the `sandesh init [--admin <name>] [--yes]` subcommand.

  AC3 — idempotent.
    First `sandesh init` provisions (migrate+consolidate+reindex, admin if given);
    a second `sandesh init` exits 0 as a clean no-op (no error, admin unchanged).

  AC4 — admin assignment.
    `sandesh init --admin X` sets admin_name(con) == "X";
    a subsequent `sandesh init --admin Y` is refused with the existing
    "refusing to silently re-assign" error; admin_name stays "X".

  AC5 — without [migrate].
    On a current/empty store with [migrate] simulated absent, `sandesh init`
    runs consolidate+reindex and exits 0 with a migrate-skipped notice;
    on a *behind* store with [migrate] absent, `sandesh init` exits non-zero
    with the §S1 remediation text.

  AC6 — no MCP surface.
    `sandesh/mcp_server.py` registers no tool named/containing 'init',
    'admin', or 'migrate'; tool count stays exactly 12.

Expected RED: `sandesh init` subcommand does not exist — argparse raises
SystemExit(2) ("invalid choice: 'init'") for all CLI tests.

Run:
    PYTHONPATH=. .venv/bin/python tests/test_init_command.py
or via Crucible:
    python3 ~/.claude/scripts/python-crucible.py test \\
        --tests tests.test_init_command --agent CR-SAN-036-C2-RED
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout

# ── path bootstrap — match the project's per-file runner pattern ──────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import migrate
from sandesh import cli

# ── DDL helper: yoyo bookkeeping tables (from test_provision_automigrate.py) ──

_YOYO_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS _yoyo_migration (
    migration_hash TEXT,
    migration_id   TEXT,
    applied_at_utc TIMESTAMP
);
CREATE TABLE IF NOT EXISTS _yoyo_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_hash TEXT,
    migration_id   TEXT,
    operation      TEXT,
    username       TEXT,
    hostname       TEXT,
    comment        TEXT,
    created        TIMESTAMP
);
CREATE TABLE IF NOT EXISTS _yoyo_version (
    version INTEGER
);
CREATE TABLE IF NOT EXISTS yoyo_lock (
    locked      INTEGER DEFAULT 1,
    ctime       TIMESTAMP,
    pid         INTEGER
);
"""


def _migration_ids():
    """Return all packaged migration ids by scanning migrations_dir()."""
    mdir = migrate.migrations_dir()
    ids = []
    for fname in sorted(os.listdir(mdir)):
        if fname.endswith(".sql") and ".rollback." not in fname:
            ids.append(fname[: -len(".sql")])
    return ids


def _newest_migration_id():
    return _migration_ids()[-1]


def _fts_row_count(con):
    """Total rows in message_fts (0 if the table does not exist yet)."""
    try:
        return con.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# ── Fixture base ──────────────────────────────────────────────────────────────

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME so no real store is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-init-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process, capturing stdout + stderr.

        Returns (rc, out_str, err_str).
        """
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _make_behind_store(self):
        """Build a schema-behind fixture (same strategy as test_provision_automigrate).

        1. connect() to create the DB + _SCHEMA.
        2. migrate.apply() to write all yoyo bookkeeping rows.
        3. DELETE the newest migration's row so one migration is 'pending'.
        Returns the db path.
        """
        con = s.connect()
        con.close()

        migrate.apply()

        newest_id = _newest_migration_id()
        db = s.db_path()
        raw = sqlite3.connect(db)
        try:
            raw.execute(
                "DELETE FROM _yoyo_migration WHERE migration_id = ?",
                (newest_id,),
            )
            raw.commit()
        finally:
            raw.close()

        _applied, pending = migrate.status()
        self.assertIn(
            newest_id,
            pending,
            f"Setup error: {newest_id!r} should be pending but pending={pending}",
        )
        return db


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC3 — `sandesh init` subcommand is registered and idempotent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AC3InitIdempotentTest(_TempDataHome):
    """AC3: `sandesh init` provisions on first run; second run is a clean no-op.

    FAILS RED: SystemExit(2) — 'invalid choice: init' — until the subparser
    is wired in cli.py.
    """

    def test_ac3_first_init_exits_zero(self):
        """First `sandesh init` on a fresh store exits 0 (no error)."""
        rc, _out, err = self._run_cli(["init"])
        self.assertEqual(
            rc,
            0,
            f"`sandesh init` must exit 0 on a fresh store. "
            f"rc={rc!r}, stderr={err!r}",
        )

    def test_ac3_first_init_reindex_runs(self):
        """First `sandesh init` runs reindex so the FTS index table exists."""
        rc, _out, err = self._run_cli(["init"])
        self.assertEqual(rc, 0, f"init exited non-zero: rc={rc!r}, stderr={err!r}")

        con = s.connect()
        try:
            # The message_fts table must exist after init (reindex was run).
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn(
                "message_fts",
                tables,
                "After `sandesh init`, message_fts table must exist "
                "(reindex step provisions the FTS index).",
            )
        finally:
            con.close()

    def test_ac3_second_init_exits_zero_no_error(self):
        """Second `sandesh init` exits 0 as a clean no-op (no error on stderr)."""
        rc1, _out1, err1 = self._run_cli(["init"])
        self.assertEqual(rc1, 0, f"First init failed: rc={rc1!r}, err={err1!r}")

        rc2, _out2, err2 = self._run_cli(["init"])
        self.assertEqual(
            rc2,
            0,
            f"Second `sandesh init` must exit 0 (idempotent). "
            f"rc={rc2!r}, stderr={err2!r}",
        )
        # No error text on stderr from the second run.
        self.assertNotIn(
            "error",
            err2.lower(),
            f"Second `sandesh init` must not emit errors on stderr. "
            f"stderr={err2!r}",
        )

    def test_ac3_init_with_admin_then_no_admin_second_run_admin_unchanged(self):
        """First `sandesh init --admin ops` sets admin; second run without --admin
        leaves admin unchanged (no error, still 'ops')."""
        rc1, _out1, err1 = self._run_cli(["init", "--admin", "ops"])
        self.assertEqual(rc1, 0, f"First init --admin failed: rc={rc1!r}, err={err1!r}")

        rc2, _out2, err2 = self._run_cli(["init"])
        self.assertEqual(
            rc2,
            0,
            f"Second `sandesh init` (no --admin) must exit 0. "
            f"rc={rc2!r}, stderr={err2!r}",
        )

        con = s.connect()
        try:
            name = s.admin_name(con)
        finally:
            con.close()

        self.assertEqual(
            name,
            "ops",
            f"Admin must still be 'ops' after second init without --admin. "
            f"Got: {name!r}",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC4 — admin assignment and re-assign refusal
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AC4AdminAssignmentTest(_TempDataHome):
    """AC4: `sandesh init --admin X` assigns admin; `--admin Y` is then refused.

    FAILS RED: SystemExit(2) — 'invalid choice: init' — until the subparser
    is wired in cli.py.
    """

    def test_ac4_init_admin_assigns_name(self):
        """`sandesh init --admin alice` → admin_name(con) == 'alice'."""
        rc, _out, err = self._run_cli(["init", "--admin", "alice"])
        self.assertEqual(rc, 0, f"init --admin alice failed: rc={rc!r}, err={err!r}")

        con = s.connect()
        try:
            name = s.admin_name(con)
        finally:
            con.close()

        self.assertEqual(
            name,
            "alice",
            f"After `sandesh init --admin alice`, admin_name must be 'alice'. "
            f"Got: {name!r}",
        )

    def test_ac4_second_init_different_admin_is_refused(self):
        """A second `sandesh init --admin bob` after alice is set is refused
        (non-zero exit) with the 'refusing to silently re-assign' error."""
        rc1, _out1, err1 = self._run_cli(["init", "--admin", "alice"])
        self.assertEqual(rc1, 0, f"First init --admin failed: rc={rc1!r}, err={err1!r}")

        rc2, out2, err2 = self._run_cli(["init", "--admin", "bob"])
        self.assertNotEqual(
            rc2,
            0,
            "Second `sandesh init --admin bob` must exit non-zero "
            "(refusing to silently re-assign).",
        )
        combined = (out2 + err2).lower()
        self.assertIn(
            "refusing",
            combined,
            f"Error output must contain 'refusing' (the re-assign refusal). "
            f"stdout={out2!r}, stderr={err2!r}",
        )

    def test_ac4_refused_reassign_leaves_admin_unchanged(self):
        """After a refused re-assign attempt, admin_name stays 'alice'."""
        self._run_cli(["init", "--admin", "alice"])
        self._run_cli(["init", "--admin", "bob"])  # should be refused

        con = s.connect()
        try:
            name = s.admin_name(con)
        finally:
            con.close()

        self.assertEqual(
            name,
            "alice",
            f"Admin must stay 'alice' after refused reassign. Got: {name!r}",
        )

    def test_ac4_init_admin_same_name_twice_is_idempotent(self):
        """`sandesh init --admin alice` twice is idempotent (both exit 0)."""
        rc1, _out1, err1 = self._run_cli(["init", "--admin", "alice"])
        self.assertEqual(rc1, 0, f"First init failed: rc={rc1!r}, err={err1!r}")

        rc2, out2, err2 = self._run_cli(["init", "--admin", "alice"])
        self.assertEqual(
            rc2,
            0,
            f"Second `sandesh init --admin alice` (same name) must exit 0. "
            f"rc={rc2!r}, stderr={err2!r}",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC5 — behaviour without [migrate]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AC5WithoutMigrateOnCurrentStoreTest(_TempDataHome):
    """AC5a: on a current/empty store with [migrate] simulated absent,
    `sandesh init` runs consolidate+reindex and exits 0 with a migrate-skipped
    notice.

    FAILS RED: SystemExit(2) — 'invalid choice: init'.
    """

    def _run_cli_no_migrate(self, argv):
        """Run cli.main(argv) with [migrate] simulated absent by patching
        importlib.import_module so that 'yoyo' raises ImportError.

        This mirrors the AC5 condition: the store is current so
        connect() will NOT raise MigrationRequired, but cmd_init must
        detect [migrate] is absent and skip+notice the migrate step.
        """
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        import builtins

        original_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name in ("yoyo", "jsonschema"):
                raise ImportError(f"simulated missing dep: {name}")
            return original_import(name, *args, **kwargs)

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            redirect_stdout(out_buf),
            redirect_stderr(err_buf),
            unittest.mock.patch("builtins.__import__", side_effect=_blocking_import),
        ):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_ac5_current_store_no_migrate_exits_zero(self):
        """Current store + [migrate] absent → exit 0."""
        rc, out, err = self._run_cli_no_migrate(["init"])
        self.assertEqual(
            rc,
            0,
            f"`sandesh init` on current store without [migrate] must exit 0. "
            f"rc={rc!r}, out={out!r}, err={err!r}",
        )

    def test_ac5_current_store_no_migrate_emits_skipped_notice(self):
        """Current store + [migrate] absent → stdout contains a migrate-skipped
        notice (e.g. 'migrate' and 'skip', or '[migrate]' extra absent)."""
        rc, out, err = self._run_cli_no_migrate(["init"])
        self.assertEqual(rc, 0, f"init exited non-zero: rc={rc!r}, err={err!r}")

        combined = (out + err).lower()
        # Must mention migrate and skipped/skip/not available/absent
        self.assertIn(
            "migrat",
            combined,
            f"Output must mention 'migrate' or 'migration'. "
            f"stdout={out!r}, stderr={err!r}",
        )
        has_skip_term = any(
            term in combined
            for term in ("skip", "not installed", "absent", "unavailable", "without")
        )
        self.assertTrue(
            has_skip_term,
            f"Output must indicate migrate was skipped/unavailable. "
            f"stdout={out!r}, stderr={err!r}",
        )

    def test_ac5_current_store_no_migrate_consolidate_reindex_run(self):
        """Current store + [migrate] absent → consolidate+reindex still run
        (FTS table exists after init)."""
        rc, _out, err = self._run_cli_no_migrate(["init"])
        self.assertEqual(rc, 0, f"init exited non-zero: rc={rc!r}, err={err!r}")

        con = s.connect()
        try:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn(
                "message_fts",
                tables,
                "After `sandesh init` without [migrate], message_fts must exist "
                "(reindex step must still run).",
            )
        finally:
            con.close()


class AC5WithoutMigrateOnBehindStoreTest(_TempDataHome):
    """AC5b: on a *behind* store with [migrate] absent, `sandesh init` must
    exit non-zero with the §S1 remediation text.

    FAILS RED: SystemExit(2) — 'invalid choice: init'.
    """

    def test_ac5_behind_store_no_migrate_exits_nonzero(self):
        """Behind store + [migrate] absent → exit non-zero."""
        self._make_behind_store()

        rc, out, err = self._run_cli_no_migrate_with_behind_connect(["init"])
        self.assertNotEqual(
            rc,
            0,
            f"`sandesh init` on a behind store without [migrate] must exit non-zero. "
            f"rc={rc!r}, out={out!r}, err={err!r}",
        )

    def test_ac5_behind_store_no_migrate_shows_remediation(self):
        """Behind store + [migrate] absent → error output contains install-method
        remediation text (from §S1 MigrationRequired message)."""
        self._make_behind_store()

        rc, out, err = self._run_cli_no_migrate_with_behind_connect(["init"])
        self.assertNotEqual(rc, 0, f"init should have exited non-zero: rc={rc!r}")

        combined = out + err
        # The remediation must name a package manager or 'sandesh-relay'
        has_remediation = any(
            term in combined
            for term in ("pip", "uv tool", "pipx", "sandesh-relay", "install")
        )
        self.assertTrue(
            has_remediation,
            f"Error output must contain install-method remediation. "
            f"stdout={out!r}, stderr={err!r}",
        )

    def _run_cli_no_migrate_with_behind_connect(self, argv):
        """Run cli.main(argv) where connect() raises MigrationRequired
        (because the store is behind and [migrate] is absent).

        On a real behind store the [migrate] extra IS installed in our dev venv,
        so connect() would auto-apply. We simulate the absent-deps condition by
        patching builtins.__import__ to block yoyo/jsonschema — this makes
        connect()'s importability check fail and causes it to raise MigrationRequired,
        matching the AC5 behind+absent scenario.
        """
        import builtins
        original_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name in ("yoyo", "jsonschema"):
                raise ImportError(f"simulated missing dep: {name}")
            return original_import(name, *args, **kwargs)

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            redirect_stdout(out_buf),
            redirect_stderr(err_buf),
            unittest.mock.patch("builtins.__import__", side_effect=_blocking_import),
        ):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC6 — no MCP surface for init / admin / migrate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AC6NoMcpSurfaceTest(unittest.TestCase):
    """AC6: mcp_server.py must not expose any tool named/containing
    'init', 'admin', or 'migrate'; tool count stays exactly 12.

    This test is STATIC — it reads mcp_server.py source to find
    @mcp.tool-decorated functions without starting the server.
    It passes even now (the server already has 12 tools and no
    init/admin/migrate tool), and it MUST CONTINUE to pass after
    GREEN adds the `init` CLI without touching the MCP server.
    """

    _MCP_SERVER_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sandesh",
        "mcp_server.py",
    )

    def _get_tool_function_names(self):
        """Parse mcp_server.py for @mcp.tool-decorated function names.

        Reads lines sequentially: when we see '@mcp.tool', the next
        'def <name>(' line gives the tool function name.
        """
        names = []
        with open(self._MCP_SERVER_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("@mcp.tool"):
                # Look ahead for the def line (may be the very next non-blank line)
                j = i + 1
                while j < len(lines):
                    def_line = lines[j].strip()
                    if def_line.startswith("def "):
                        # Extract function name
                        name = def_line[4:].split("(")[0].strip()
                        names.append(name)
                        break
                    if def_line and not def_line.startswith("@") and not def_line.startswith("#"):
                        break
                    j += 1
            i += 1
        return names

    def test_ac6_tool_count_is_exactly_12(self):
        """The MCP server exposes exactly 12 tools (no new ones added for init)."""
        names = self._get_tool_function_names()
        self.assertEqual(
            len(names),
            12,
            f"Expected exactly 12 @mcp.tool functions in mcp_server.py, "
            f"got {len(names)}: {sorted(names)}",
        )

    def test_ac6_no_tool_name_contains_init(self):
        """No MCP tool name contains 'init'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "init" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'init': {offenders}. "
            f"The init command is CLI-only — never an MCP tool.",
        )

    def test_ac6_no_tool_name_contains_admin(self):
        """No MCP tool name contains 'admin'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "admin" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'admin': {offenders}. "
            f"Admin assignment is CLI-only (and only at install time).",
        )

    def test_ac6_no_tool_name_contains_migrate(self):
        """No MCP tool name contains 'migrate'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "migrat" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'migrate': {offenders}. "
            f"Migration is CLI-only.",
        )

    def test_ac6_expected_12_tool_names_are_present(self):
        """The exact set of expected 12 tools is present (regression guard)."""
        names = set(self._get_tool_function_names())
        expected = {
            "sandesh_setup",
            "sandesh_addressbook",
            "sandesh_inbox",
            "sandesh_fetch",
            "sandesh_thread",
            "sandesh_search",
            "sandesh_register",
            "sandesh_unregister",
            "sandesh_send",
            "sandesh_reply",
            "sandesh_archive",
            "sandesh_unarchive",
        }
        missing = expected - names
        extra = names - expected
        self.assertEqual(
            missing,
            set(),
            f"MCP tool(s) unexpectedly missing: {sorted(missing)}",
        )
        self.assertEqual(
            extra,
            set(),
            f"MCP tool(s) unexpectedly added: {sorted(extra)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
