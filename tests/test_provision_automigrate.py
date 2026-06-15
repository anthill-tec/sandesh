"""test_provision_automigrate.py — RED tests for CR-SAN-036 §S1/§S2, C1.

Covers:
  AC1 — lazy migrate applies: connect() on a schema-behind store (with [migrate]
        deps present) auto-applies pending migrations; the store ends current.
  AC2 — actionable error, no self-pip: connect() on a behind store with [migrate]
        absent raises MigrationRequired whose message carries an install-method-
        appropriate remediation string; no pip/uv subprocess is spawned.
  AC7 — fresh store is not "behind": a store freshly created by connect() has no
        _yoyo_migration table; a second connect() attempts no migration and raises
        nothing, even with [migrate] simulated absent.

Also covers §S2: the install-method detection helper returns the right hint for
uv-tool / pipx / plain-venv executable path patterns.

Expected RED: all tests FAIL because:
  - sandesh_db.connect() has no lazy-migrate logic yet
  - MigrationRequired exception does not exist yet
  - the install-method detection helper does not exist yet

Run:
    PYTHONPATH=. .venv/bin/python tests/test_provision_automigrate.py
or via crucible:
    python3 ~/.claude/scripts/python-crucible.py test \\
        --tests tests.test_provision_automigrate --agent CR-SAN-036-C1-RED
"""

import builtins
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

# ---------------------------------------------------------------------------
# Path bootstrap — match the project's per-file runner pattern
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import migrate


# ---------------------------------------------------------------------------
# DDL helpers — _yoyo_migration bookkeeping table schema (from test_consolidate_skip.py)
# ---------------------------------------------------------------------------

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
        # Only forward-migration SQL files (exclude .rollback.sql)
        if fname.endswith(".sql") and ".rollback." not in fname:
            ids.append(fname[: -len(".sql")])
    return ids


def _newest_migration_id():
    """Return the id of the newest (last) packaged migration."""
    return _migration_ids()[-1]


# ---------------------------------------------------------------------------
# Base fixture — isolated XDG_DATA_HOME per test
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME so no real store is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-automigrate-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_behind_store(self):
        """Build a schema-behind store fixture.

        Strategy:
          1. Call migrate.apply() to bring the store fully current (all
             migration ids recorded in _yoyo_migration).
          2. DELETE the newest migration's row from _yoyo_migration so that
             one migration is "pending" again — making the store behind.

        Returns the db path.
        """
        # First, call connect() to create the DB file and apply _SCHEMA.
        con = s.connect()
        con.close()

        # Apply all migrations so the yoyo bookkeeping table exists and all
        # ids are recorded.
        migrate.apply()

        # Now delete the newest migration row to make the store behind.
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

        # Sanity: the id should now appear in pending.
        _applied, pending = migrate.status()
        self.assertIn(
            newest_id,
            pending,
            f"Setup error: {newest_id!r} should be pending after row deletion "
            f"but pending={pending}",
        )
        return db


# ---------------------------------------------------------------------------
# AC1 — lazy migrate applies (behind + deps present)
# ---------------------------------------------------------------------------

class AC1LazyMigrateAppliesTest(_TempDataHome):
    """AC1: connect() on a schema-behind store with [migrate] deps present
    auto-applies the pending migration. After connect() returns, the store
    ends current (pending list is empty).

    FAILS NOW (RED): connect() has no lazy-migrate logic; it just runs
    executescript(_SCHEMA) and returns — the pending migration stays pending.
    """

    def test_ac1_behind_store_with_deps_auto_applies(self):
        """connect() auto-applies the pending migration on a behind store."""
        self._make_behind_store()

        newest_id = _newest_migration_id()

        # Verify [migrate] deps are importable (test pre-condition).
        try:
            import yoyo  # noqa: F401
        except ImportError:
            self.skipTest("[migrate] deps not installed in this venv — skip AC1")

        # Now call connect() — this should trigger lazy auto-migration.
        con = s.connect()
        con.close()

        # Assert the store is now current: no pending migrations.
        _applied, pending = migrate.status()
        self.assertEqual(
            pending,
            [],
            f"After connect() on a behind store, expected 0 pending migrations "
            f"but got pending={pending!r}. "
            f"connect() must auto-apply when [migrate] is importable and store is behind.",
        )

    def test_ac1_newest_id_reappears_in_applied(self):
        """After lazy auto-apply, the previously-pending id is in the applied list."""
        self._make_behind_store()
        newest_id = _newest_migration_id()

        try:
            import yoyo  # noqa: F401
        except ImportError:
            self.skipTest("[migrate] deps not installed in this venv — skip AC1")

        con = s.connect()
        con.close()

        applied, _pending = migrate.status()
        self.assertIn(
            newest_id,
            applied,
            f"After connect() lazy auto-apply, {newest_id!r} should be in applied "
            f"but applied={applied!r}.",
        )

    def test_ac1_current_store_is_untouched(self):
        """connect() on an already-current store is a no-op (raises nothing,
        returns a working connection).

        PASSES in both RED and GREEN: the current store path should be safe.
        This test is included to guard against regressions.
        """
        try:
            import yoyo  # noqa: F401
        except ImportError:
            self.skipTest("[migrate] deps not installed — skip")

        # First: bring the store fully current.
        con = s.connect()
        con.close()
        migrate.apply()

        # Confirm no pending.
        _applied, pending = migrate.status()
        self.assertEqual(pending, [], "Setup: store should be current")

        # Second connect() on a current store: must not raise.
        con2 = s.connect()
        con2.close()

        # Still no pending.
        _applied2, pending2 = migrate.status()
        self.assertEqual(
            pending2,
            [],
            "connect() on a current store should leave it current (no-op).",
        )


# ---------------------------------------------------------------------------
# AC2 — behind + NO deps → raises MigrationRequired, no self-pip
# ---------------------------------------------------------------------------

class AC2BehindNoDepsRaisesTest(_TempDataHome):
    """AC2: connect() on a schema-behind store with [migrate] absent raises
    the custom MigrationRequired exception. Its message contains an
    install-method-specific remediation string. No pip/uv subprocess is spawned.

    FAILS NOW (RED):
      - MigrationRequired does not exist (ImportError/AttributeError).
      - connect() does not detect the behind condition.
      - connect() does not raise at all.
    """

    def _simulate_migrate_absent(self):
        """Context manager that makes yoyo/jsonschema un-importable by patching
        sys.modules with None sentinels, simulating [migrate] not installed.

        Restores the real modules on exit.
        """
        return _MigrateAbsentContext()

    def test_ac2_missing_migrate_exception_exists(self):
        """MigrationRequired must be importable from sandesh.sandesh_db."""
        # This import will fail (AttributeError/ImportError) in RED because
        # the exception class does not exist yet.
        from sandesh.sandesh_db import MigrationRequired  # noqa: F401

    def test_ac2_raises_migration_required_when_deps_absent(self):
        """connect() on a behind store with [migrate] absent raises MigrationRequired."""
        self._make_behind_store()

        # Import the (not-yet-existing) exception — this will error in RED.
        from sandesh.sandesh_db import MigrationRequired

        with self._simulate_migrate_absent():
            with self.assertRaises(MigrationRequired) as ctx:
                con = s.connect()
                con.close()

        exc_msg = str(ctx.exception)
        self.assertTrue(
            len(exc_msg) > 0,
            "MigrationRequired message must not be empty.",
        )

    def test_ac2_error_message_contains_remediation(self):
        """MigrationRequired message contains an install-method remediation command."""
        self._make_behind_store()

        from sandesh.sandesh_db import MigrationRequired

        with self._simulate_migrate_absent():
            with self.assertRaises(MigrationRequired) as ctx:
                con = s.connect()
                con.close()

        exc_msg = str(ctx.exception)
        # The message must contain at least one of the valid remediation forms
        # (uv tool install / pipx inject / pip install).
        valid_hints = [
            "uv tool install --with",
            "pipx inject",
            "pip install 'sandesh-relay[migrate]'",
        ]
        has_hint = any(hint in exc_msg for hint in valid_hints)
        self.assertTrue(
            has_hint,
            f"MigrationRequired message {exc_msg!r} must contain one of: "
            f"{valid_hints}. "
            f"AC2 requires an install-method-appropriate remediation command.",
        )

    def test_ac2_no_subprocess_spawned(self):
        """connect() must not spawn any pip/uv subprocess when raising MigrationRequired."""
        self._make_behind_store()

        from sandesh.sandesh_db import MigrationRequired

        with unittest.mock.patch("subprocess.run") as mock_run, \
             unittest.mock.patch("subprocess.Popen") as mock_popen, \
             unittest.mock.patch("subprocess.call") as mock_call, \
             self._simulate_migrate_absent():
            try:
                con = s.connect()
                con.close()
            except MigrationRequired:
                pass
            except Exception:
                pass  # Any other exception also acceptable — we're checking no subprocess

        mock_run.assert_not_called()
        mock_popen.assert_not_called()
        mock_call.assert_not_called()

    def test_ac2_raises_not_sys_exit(self):
        """connect() must RAISE MigrationRequired, not call sys.exit() (unlike _require_deps).

        connect() is library code (called from notify poll loop, MCP server) —
        it must never sys.exit().
        """
        self._make_behind_store()

        from sandesh.sandesh_db import MigrationRequired

        caught_system_exit = False
        caught_migration_required = False

        with self._simulate_migrate_absent():
            try:
                con = s.connect()
                con.close()
            except SystemExit:
                caught_system_exit = True
            except MigrationRequired:
                caught_migration_required = True
            except Exception:
                pass  # Other exceptions: the test for MigrationRequired will catch

        self.assertFalse(
            caught_system_exit,
            "connect() must not call sys.exit() — it is library code. "
            "MigrationRequired must be raised instead.",
        )


# ---------------------------------------------------------------------------
# §S2 — install-method detection helper parametrized tests
# ---------------------------------------------------------------------------

class InstallMethodHelperTest(unittest.TestCase):
    """§S2: the install-method detection helper returns the right remediation
    string based on the running interpreter path.

    The helper is expected to live at sandesh.sandesh_db.install_method_hint()
    (or similar — the CR spec calls it a helper that builds the remediation
    string).

    FAILS NOW (RED): the helper does not exist yet.
    """

    def _get_helper(self):
        """Return the install-method hint helper function.

        Tries sandesh_db.install_method_hint first. This will raise
        AttributeError in RED because the function doesn't exist yet.
        """
        # Expected location: sandesh.sandesh_db.install_method_hint
        # or sandesh.sandesh_db._install_method_hint
        fn = getattr(s, "install_method_hint", None) or getattr(
            s, "_install_method_hint", None
        )
        if fn is None:
            self.fail(
                "install_method_hint (or _install_method_hint) not found in sandesh_db. "
                "§S2 requires a helper that returns the remediation string based on "
                "the interpreter executable path."
            )
        return fn

    def _hint_for_exe(self, exe_path):
        """Call the helper with a fake interpreter executable path."""
        fn = self._get_helper()
        # The helper signature: install_method_hint(executable=sys.executable) or
        # install_method_hint(sys_prefix=sys.prefix) — try both.
        try:
            return fn(executable=exe_path)
        except TypeError:
            try:
                return fn(exe_path)
            except TypeError:
                self.fail(
                    f"install_method_hint does not accept a positional or 'executable' "
                    f"kwarg. Cannot parametrize. Exe tried: {exe_path!r}"
                )

    def test_s2_helper_exists(self):
        """install_method_hint (or _install_method_hint) must exist in sandesh_db."""
        self._get_helper()

    def test_s2_uv_tool_path_returns_uv_tool_hint(self):
        """An executable under ~/.local/share/uv/tools/ signals uv-tool install.

        Expected hint: contains 'uv tool install --with sandesh-relay[migrate]'
        or similar uv-tool form.
        """
        uv_tool_exe = os.path.expanduser(
            "~/.local/share/uv/tools/sandesh-relay/bin/sandesh"
        )
        hint = self._hint_for_exe(uv_tool_exe)
        self.assertIn(
            "uv tool install --with",
            hint,
            f"For uv-tool executable {uv_tool_exe!r}, hint should contain "
            f"'uv tool install --with' but got: {hint!r}",
        )
        # Must NOT suggest pipx or bare pip for a uv-tool install
        self.assertNotIn(
            "pipx inject",
            hint,
            f"uv-tool hint should not suggest 'pipx inject': {hint!r}",
        )

    def test_s2_pipx_path_returns_pipx_hint(self):
        """An executable under ~/.local/share/pipx/venvs/ signals pipx install.

        Expected hint: contains 'pipx inject sandesh-relay sandesh-relay[migrate]'
        or similar pipx form.
        """
        pipx_exe = os.path.expanduser(
            "~/.local/share/pipx/venvs/sandesh-relay/bin/sandesh"
        )
        hint = self._hint_for_exe(pipx_exe)
        self.assertIn(
            "pipx inject",
            hint,
            f"For pipx executable {pipx_exe!r}, hint should contain 'pipx inject' "
            f"but got: {hint!r}",
        )
        # Must NOT suggest uv tool for a pipx install
        self.assertNotIn(
            "uv tool install",
            hint,
            f"pipx hint should not suggest 'uv tool install': {hint!r}",
        )

    def test_s2_plain_venv_path_returns_pip_hint(self):
        """A plain venv executable returns the generic pip install hint."""
        venv_exe = "/home/user/projects/myapp/.venv/bin/python"
        hint = self._hint_for_exe(venv_exe)
        self.assertIn(
            "pip install",
            hint,
            f"For plain-venv executable {venv_exe!r}, hint should contain "
            f"'pip install' but got: {hint!r}",
        )
        # Must reference the sandesh-relay[migrate] extra
        self.assertIn(
            "sandesh-relay[migrate]",
            hint,
            f"pip hint must reference 'sandesh-relay[migrate]': {hint!r}",
        )

    def test_s2_system_python_path_returns_pip_hint(self):
        """A system Python path (/usr/bin/python3) returns the generic pip hint."""
        sys_exe = "/usr/bin/python3"
        hint = self._hint_for_exe(sys_exe)
        self.assertIn(
            "pip install",
            hint,
            f"For system Python {sys_exe!r}, hint should contain 'pip install' "
            f"but got: {hint!r}",
        )

    def test_s2_hint_is_nonempty_string(self):
        """The helper always returns a non-empty string."""
        hint = self._hint_for_exe(sys.executable)
        self.assertIsInstance(hint, str)
        self.assertGreater(
            len(hint.strip()),
            0,
            "install_method_hint must return a non-empty string.",
        )


# ---------------------------------------------------------------------------
# AC7 — fresh store: no false-positive behind detection
# ---------------------------------------------------------------------------

class AC7FreshStoreNoFalsePositiveTest(_TempDataHome):
    """AC7: a store freshly created by connect() has no _yoyo_migration table.
    The behind-detector treats this as current, so a second connect() attempts
    no migration and raises nothing — even with [migrate] absent.

    FAILS NOW (RED): connect() has no lazy-migrate / behind-detection logic,
    so there's nothing to assert against; once GREEN adds the detection, this
    test ensures a fresh store is never mis-detected as behind.

    Note: AC7 tests the ABSENCE of incorrect behaviour. They should be RED
    because MigrationRequired doesn't exist yet (ImportError on the import
    from sandesh.sandesh_db) — which prevents the test from completing normally.
    We verify that the exception class is importable (it won't be in RED), and
    that the no-raise invariant holds once it exists.
    """

    def test_ac7_fresh_store_has_no_yoyo_table(self):
        """A store created only by connect() has no _yoyo_migration table.

        This is the precondition for AC7: fresh stores have no yoyo bookkeeping
        table, so the lazy detector must treat them as current.
        """
        con = s.connect()
        con.close()

        db = s.db_path()
        raw = sqlite3.connect(db)
        try:
            tables = {
                row[0]
                for row in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            raw.close()

        self.assertNotIn(
            "_yoyo_migration",
            tables,
            "A fresh store (connect() only, no migrate.apply()) must NOT have a "
            "_yoyo_migration table. The lazy behind-detector relies on this to "
            "avoid false-positive migration attempts on new stores.",
        )

    def test_ac7_second_connect_does_not_raise_migration_required(self):
        """Second connect() on a fresh store raises no MigrationRequired.

        FAILS NOW (RED): MigrationRequired doesn't exist yet — so this import
        will raise AttributeError. Once GREEN ships the exception and the lazy
        detector, this test must pass.
        """
        # Import the exception class — RED: AttributeError because it doesn't exist.
        from sandesh.sandesh_db import MigrationRequired  # noqa: F401

        # Create a fresh store.
        con = s.connect()
        con.close()

        # Simulate [migrate] absent so the path that would raise is exercised.
        with _MigrateAbsentContext():
            # Second connect() on a fresh (yoyo-table-less) store must NOT raise.
            try:
                con2 = s.connect()
                con2.close()
            except MigrationRequired as exc:
                self.fail(
                    f"connect() raised MigrationRequired on a FRESH store "
                    f"(no _yoyo_migration table): {exc}. "
                    "AC7: a fresh store must be treated as current — "
                    "the behind-detector must only fire when _yoyo_migration EXISTS "
                    "AND some packaged id is absent.",
                )

    def test_ac7_second_connect_returns_working_connection(self):
        """Second connect() on a fresh store returns a usable connection."""
        from sandesh.sandesh_db import MigrationRequired  # noqa: F401

        # First connect — creates the DB.
        con = s.connect()
        con.close()

        with _MigrateAbsentContext():
            try:
                con2 = s.connect()
            except MigrationRequired as exc:
                self.fail(
                    f"connect() raised MigrationRequired on fresh store: {exc}"
                )

        # The connection must be usable — can query sqlite_master.
        tables = con2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con2.close()
        self.assertGreater(
            len(tables),
            0,
            "connect() on a fresh store must return a working connection "
            "with at least one table.",
        )


# ---------------------------------------------------------------------------
# Helper: context manager to simulate [migrate] deps absent
# ---------------------------------------------------------------------------

class _MigrateAbsentContext:
    """Context manager that makes yoyo and jsonschema un-importable by
    inserting None sentinels into sys.modules.

    This simulates the [migrate] extra being absent without actually
    uninstalling anything. The real module objects are restored on __exit__.
    """

    _BLOCKED = ("yoyo", "yoyo.migrations", "jsonschema")

    def __enter__(self):
        self._saved = {}
        for mod in self._BLOCKED:
            self._saved[mod] = sys.modules.get(mod, _SENTINEL)
            sys.modules[mod] = None  # type: ignore[assignment]
        return self

    def __exit__(self, *_):
        for mod, val in self._saved.items():
            if val is _SENTINEL:
                sys.modules.pop(mod, None)
            else:
                sys.modules[mod] = val


_SENTINEL = object()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
