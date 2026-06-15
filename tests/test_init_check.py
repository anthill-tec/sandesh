"""test_init_check.py — RED tests for CR-SAN-038 §S0 / C1.

Covers AC0a–AC0e: the `sandesh init --check` read-only status probe.

  AC0a — provisioned store (DB exists + admin set) → exit 0.
  AC0b — store exists but no admin → non-zero + admin-unset message + mentions `sandesh init`.
  AC0c — no DB at all → non-zero + store-absent message + DB NOT created by the probe.
  AC0d — provisioned store: bytes/mtime+size unchanged after `init --check` (no side effects).
  AC0e — no MCP `init`/`check` tool (tool count stays 12; static parse of mcp_server.py).

Expected RED: `sandesh init --check` does not exist yet — argparse rejects
`--check` with SystemExit(2) for all CLI tests (AC0a–AC0d).

Run:
    PYTHONPATH=. .venv/bin/python tests/test_init_check.py
or via Crucible:
    python3 ~/.claude/scripts/python-crucible.py test \\
        --tests tests.test_init_check --agent CR-SAN-038-C1-RED
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as sdb
from sandesh import cli


class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME so no real store is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-check-test-")
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

    def _provision_store(self, admin="ops"):
        """Create a fully-provisioned store: setup a project + assign admin.

        Returns the db path.
        """
        # setup() opens its own connection; assign_admin needs a separate one
        sdb.setup("TestProj")
        if admin:
            con = sdb.connect()
            try:
                sdb.assign_admin(con, admin)
            finally:
                con.close()
        return sdb.db_path()

    def _provision_store_no_admin(self):
        """Create a store with a project but NO admin assigned.

        Returns the db path.
        """
        sdb.setup("TestProj")
        return sdb.db_path()


# ---------------------------------------------------------------------------
# AC0a — provisioned store → exit 0
# ---------------------------------------------------------------------------

class AC0aProvisionedExitsZeroTest(_TempDataHome):
    """AC0a: on a fully-provisioned store, `sandesh init --check` exits 0.

    FAILS RED: `--check` is not a recognised argument for the `init` subparser
    — argparse raises SystemExit(2) until GREEN adds it.
    """

    def test_ac0a_provisioned_exits_zero(self):
        """`sandesh init --check` exits 0 when DB exists and admin is set."""
        self._provision_store(admin="ops")
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(
            rc,
            0,
            f"`sandesh init --check` must exit 0 on a provisioned store. "
            f"rc={rc!r}, stdout={out!r}, stderr={err!r}",
        )

    def test_ac0a_provisioned_stdout_contains_ok_signal(self):
        """`sandesh init --check` on a provisioned store emits some positive
        confirmation (output is non-empty and does not indicate failure)."""
        self._provision_store(admin="ops")
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(rc, 0, f"init --check exited non-zero: rc={rc!r}, err={err!r}")
        combined = (out + err).lower()
        # Must say *something* — the probe should not be silent on success
        self.assertTrue(
            combined.strip(),
            "Expected some output from `sandesh init --check` on a provisioned store, "
            f"but stdout and stderr were both empty. stdout={out!r}, stderr={err!r}",
        )


# ---------------------------------------------------------------------------
# AC0b — store exists but admin unset → non-zero + admin-unset message
# ---------------------------------------------------------------------------

class AC0bAdminUnsetTest(_TempDataHome):
    """AC0b: store present but no admin → non-zero + admin-unset message that
    mentions `sandesh init`.

    FAILS RED: argparse SystemExit(2) for `--check` until GREEN adds it.
    """

    def test_ac0b_no_admin_exits_nonzero(self):
        """`sandesh init --check` exits non-zero when admin is not assigned."""
        self._provision_store_no_admin()
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertNotEqual(
            rc,
            0,
            f"`sandesh init --check` must exit non-zero when admin is unset. "
            f"rc={rc!r}, stdout={out!r}, stderr={err!r}",
        )

    def test_ac0b_no_admin_message_names_admin_condition(self):
        """Output describes the admin-unset condition (not just a generic error)."""
        self._provision_store_no_admin()
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertNotEqual(rc, 0, f"init --check should be non-zero, got rc={rc!r}")
        combined = (out + err).lower()
        # Must name the admin-unset condition
        self.assertIn(
            "admin",
            combined,
            f"Output must name the 'admin' condition. "
            f"stdout={out!r}, stderr={err!r}",
        )

    def test_ac0b_no_admin_message_mentions_sandesh_init(self):
        """Output mentions `sandesh init` as the remediation action."""
        self._provision_store_no_admin()
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertNotEqual(rc, 0, f"init --check should be non-zero, got rc={rc!r}")
        combined = out + err
        # The spec says the message must mention `sandesh init`
        self.assertIn(
            "sandesh init",
            combined,
            f"Output must mention 'sandesh init' as the remediation action. "
            f"stdout={out!r}, stderr={err!r}",
        )

    def test_ac0b_distinguishes_admin_unset_from_store_absent(self):
        """The admin-unset message is distinct from the store-absent message
        (they are different non-zero conditions per the spec)."""
        # Store exists, no admin
        self._provision_store_no_admin()
        _, out_no_admin, err_no_admin = self._run_cli(["init", "--check"])
        combined_no_admin = (out_no_admin + err_no_admin).lower()

        # Now use a fresh temp dir with NO store at all
        fresh_tmp = tempfile.mkdtemp(prefix="sandesh-check-absent-")
        old_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = fresh_tmp
        try:
            _, out_absent, err_absent = self._run_cli(["init", "--check"])
            combined_absent = (out_absent + err_absent).lower()
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_xdg
            shutil.rmtree(fresh_tmp, ignore_errors=True)

        # The two messages must not be identical
        self.assertNotEqual(
            combined_no_admin,
            combined_absent,
            "The admin-unset message and store-absent message must be distinct "
            f"(AC0b vs AC0c). "
            f"admin-unset output={combined_no_admin!r}, "
            f"absent output={combined_absent!r}",
        )


# ---------------------------------------------------------------------------
# AC0c — no DB at all → non-zero + store-absent message + DB not created
# ---------------------------------------------------------------------------

class AC0cStoreAbsentTest(_TempDataHome):
    """AC0c: no sandesh.db at all → non-zero + store-absent message, and
    the probe must NOT create the DB file.

    FAILS RED: argparse SystemExit(2) for `--check` until GREEN adds it.
    """

    def test_ac0c_absent_store_exits_nonzero(self):
        """`sandesh init --check` exits non-zero when no DB exists."""
        # No setup call — tmp dir is empty
        db = sdb.db_path()
        self.assertFalse(
            os.path.exists(db),
            f"Pre-condition: DB must not exist before the probe. db={db!r}",
        )
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertNotEqual(
            rc,
            0,
            f"`sandesh init --check` must exit non-zero when store is absent. "
            f"rc={rc!r}, stdout={out!r}, stderr={err!r}",
        )

    def test_ac0c_absent_store_message_names_store_absent_condition(self):
        """Output names the store-absent condition (not just a generic error)."""
        rc, out, err = self._run_cli(["init", "--check"])
        self.assertNotEqual(rc, 0, f"init --check should be non-zero, got rc={rc!r}")
        combined = (out + err).lower()
        # Must indicate the store is missing — spec says "store-absent message"
        has_store_term = any(
            term in combined
            for term in ("store", "database", "db", "not found", "not provisioned",
                         "no sandesh", "does not exist", "missing")
        )
        self.assertTrue(
            has_store_term,
            f"Output must name the store-absent condition. "
            f"stdout={out!r}, stderr={err!r}",
        )

    def test_ac0c_probe_does_not_create_db(self):
        """The probe must NOT create sandesh.db — it is read-only."""
        db = sdb.db_path()
        self.assertFalse(
            os.path.exists(db),
            f"Pre-condition: DB must not exist. db={db!r}",
        )
        self._run_cli(["init", "--check"])
        self.assertFalse(
            os.path.exists(db),
            f"`sandesh init --check` must not create sandesh.db. "
            f"DB found at: {db!r}",
        )

    def test_ac0c_probe_does_not_create_any_sandesh_dir(self):
        """The probe must not create the sandesh data directory at all."""
        sandesh_dir = os.path.join(self.tmp, "sandesh")
        self.assertFalse(
            os.path.exists(sandesh_dir),
            f"Pre-condition: sandesh dir must not exist. dir={sandesh_dir!r}",
        )
        self._run_cli(["init", "--check"])
        self.assertFalse(
            os.path.exists(sandesh_dir),
            f"`sandesh init --check` must not create the sandesh data directory. "
            f"dir={sandesh_dir!r} exists after probe.",
        )


# ---------------------------------------------------------------------------
# AC0d — provisioned store: byte-unchanged after init --check
# ---------------------------------------------------------------------------

class AC0dWritesNothingOnProvisionedStoreTest(_TempDataHome):
    """AC0d: `sandesh init --check` on a provisioned store writes NOTHING —
    no DB mutation, no new files, no .pre-global, no migrate/consolidate/reindex
    side-effects.

    FAILS RED: argparse SystemExit(2) for `--check` until GREEN adds it.
    """

    def _db_snapshot(self, db_path):
        """Return (mtime, size, bytes) of the DB file as a snapshot."""
        stat = os.stat(db_path)
        with open(db_path, "rb") as fh:
            data = fh.read()
        return stat.st_mtime, stat.st_size, data

    def test_ac0d_db_bytes_unchanged(self):
        """DB bytes must be identical before and after `sandesh init --check`."""
        db = self._provision_store(admin="ops")
        _, _, before_bytes = self._db_snapshot(db)

        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(rc, 0, f"init --check should exit 0 on provisioned store: {err!r}")

        _, _, after_bytes = self._db_snapshot(db)
        self.assertEqual(
            before_bytes,
            after_bytes,
            "`sandesh init --check` must not modify sandesh.db bytes. "
            "The DB changed after the probe — check for migrate/reindex/consolidate "
            "side-effects.",
        )

    def test_ac0d_no_pre_global_file_created(self):
        """No `.pre-global` backup file is created by `init --check`."""
        db = self._provision_store(admin="ops")
        pre_global = db + ".pre-global"

        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(rc, 0, f"init --check should exit 0: {err!r}")

        self.assertFalse(
            os.path.exists(pre_global),
            f"`sandesh init --check` must not create a .pre-global backup file. "
            f"Found: {pre_global!r}",
        )

    def test_ac0d_no_new_files_in_sandesh_dir(self):
        """No new files appear under the sandesh data dir after `init --check`."""
        db = self._provision_store(admin="ops")
        sandesh_dir = os.path.join(self.tmp, "sandesh")

        # Snapshot all files before
        before_files = set()
        for root, _dirs, files in os.walk(sandesh_dir):
            for f in files:
                before_files.add(os.path.join(root, f))

        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(rc, 0, f"init --check should exit 0: {err!r}")

        # Snapshot all files after
        after_files = set()
        for root, _dirs, files in os.walk(sandesh_dir):
            for f in files:
                after_files.add(os.path.join(root, f))

        new_files = after_files - before_files
        self.assertEqual(
            new_files,
            set(),
            f"`sandesh init --check` must not create any new files. "
            f"New files found: {sorted(new_files)}",
        )

    def test_ac0d_message_count_unchanged(self):
        """Message count in the DB is unchanged after `init --check`."""
        self._provision_store(admin="ops")
        store = sdb.store_dir("TestProj")
        # Send a message so there is something to count
        con = sdb.connect()
        try:
            sdb.register(con, "Mainline - TestProj")
            sdb.register(con, "Track 1 - TestProj")
            sdb.send(
                con,
                store,
                "Mainline - TestProj",
                to=["Track 1 - TestProj"],
                subject="ping subject",
            )
            before_count = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        finally:
            con.close()

        rc, out, err = self._run_cli(["init", "--check"])
        self.assertEqual(rc, 0, f"init --check should exit 0: {err!r}")

        con = sdb.connect()
        try:
            after_count = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        finally:
            con.close()

        self.assertEqual(
            before_count,
            after_count,
            f"`sandesh init --check` must not alter message rows. "
            f"Before: {before_count}, after: {after_count}",
        )


# ---------------------------------------------------------------------------
# AC0e — no MCP init/check tool (static parse of mcp_server.py)
# ---------------------------------------------------------------------------

class AC0eNoMcpSurfaceTest(unittest.TestCase):
    """AC0e: mcp_server.py must NOT expose any tool for 'init' or 'check',
    and the total tool count must stay exactly 12.

    This is a STATIC test — reads mcp_server.py source without starting the
    server. It passes now (no init/check tool exists) and must continue to
    pass after GREEN adds the `--check` flag to cli.py without touching the
    MCP server.
    """

    _MCP_SERVER_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "sandesh",
        "mcp_server.py",
    )

    def _get_tool_function_names(self):
        """Parse mcp_server.py for @mcp.tool-decorated function names."""
        names = []
        with open(self._MCP_SERVER_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("@mcp.tool"):
                j = i + 1
                while j < len(lines):
                    def_line = lines[j].strip()
                    if def_line.startswith("def "):
                        name = def_line[4:].split("(")[0].strip()
                        names.append(name)
                        break
                    if def_line and not def_line.startswith("@") and not def_line.startswith("#"):
                        break
                    j += 1
            i += 1
        return names

    def test_ac0e_tool_count_is_exactly_12(self):
        """MCP server must still expose exactly 12 tools after GREEN adds --check."""
        names = self._get_tool_function_names()
        self.assertEqual(
            len(names),
            12,
            f"Expected exactly 12 @mcp.tool functions in mcp_server.py, "
            f"got {len(names)}: {sorted(names)}",
        )

    def test_ac0e_no_tool_name_contains_init(self):
        """No MCP tool name may contain 'init'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "init" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'init': {offenders}. "
            f"`init --check` is CLI-only — never an MCP tool.",
        )

    def test_ac0e_no_tool_name_contains_check(self):
        """No MCP tool name may contain 'check'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "check" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'check': {offenders}. "
            f"The status probe is CLI-only — never an MCP tool.",
        )

    def test_ac0e_no_tool_name_contains_provision(self):
        """No MCP tool name may contain 'provision'."""
        names = self._get_tool_function_names()
        offenders = [n for n in names if "provision" in n.lower()]
        self.assertEqual(
            offenders,
            [],
            f"Found MCP tool(s) containing 'provision': {offenders}.",
        )

    def test_ac0e_expected_12_tool_names_are_present(self):
        """The exact set of 12 expected tools is present (regression guard)."""
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
