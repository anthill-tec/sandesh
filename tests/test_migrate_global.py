"""test_migrate_global.py — RED tests for CR-SAN-022 Cycle 3.

Asserts the post-C3 contract for the migration engine:
  A1  — migrate.apply/status/rollback/check/dump_schema/diff take NO project_id
          and act on sandesh_db.db_path() (the single global DB).
  A2  — `migrate --project X` (after subcommand) is CLI error exit-2 (DEC-B);
          bare `migrate` with no $SANDESH_PROJECT works fine (no exit-2 "pass --project").
  A3  — dump_schema() output includes project table + address.project column;
          excludes sqlite_* / _yoyo* / yoyo* internal tables.
  A4  — dump_schema() == current-schema.json on a fully-migrated fresh store
          (FAILS now: committed snapshot lacks project table; GREEN regenerates it).
  A5  — `migrate --all` and bare `migrate` produce identical end state.
  A6  — check() gate semantics on the global DB: fully applied → 0;
          pending (via SANDESH_MIGRATIONS_DIR fixture) → non-zero.

All tests FAIL at RED because migrate.py still routes through _db_path(project_id)
and the CLI still accepts --project on the migrate subcommand.

Run via the crucible:
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_migrate_global --agent red-cr022-c3
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "sandesh", "schema", "current-schema.json")

# Ensure the sandesh package is importable from the source tree.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _make_xdg_home():
    """Create a fresh temp dir as an isolated XDG_DATA_HOME."""
    return tempfile.mkdtemp(prefix="sandesh_test_global_")


def _patch_xdg(tmp_dir):
    """Set XDG_DATA_HOME to tmp_dir, return the old value (or None)."""
    old = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = tmp_dir
    return old


def _restore_xdg(old):
    if old is None:
        os.environ.pop("XDG_DATA_HOME", None)
    else:
        os.environ["XDG_DATA_HOME"] = old


def _sandbox_migrate():
    """Import (or re-access) sandesh.migrate with a fresh module import.
    Returns the module; callers must have XDG_DATA_HOME set before calling
    any migrate function (sandesh_db resolves paths lazily).
    """
    from sandesh import migrate
    return migrate


def _sandbox_db():
    """Import sandesh_db."""
    from sandesh import sandesh_db
    return sandesh_db


# ---------------------------------------------------------------------------
# A1 — no-arg API: apply/status/rollback/check/dump_schema/diff act on global DB
# ---------------------------------------------------------------------------

class MigrateGlobalTargetTest(unittest.TestCase):
    """A1: after C3 the engine functions take no project_id argument and operate
    on sandesh_db.db_path() — the single global sandesh.db."""

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        # Remove any stale SANDESH_PROJECT so project-routing can't sneak in.
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        sdb = _sandbox_db()
        # Provision one project so apply has something to migrate.
        sdb.setup("P1")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def test_apply_takes_no_project_id(self):
        """apply() accepts zero arguments and does not raise TypeError."""
        # RED: currently apply(project_id) requires one positional arg → TypeError.
        self._migrate.apply()   # must not raise

    def test_apply_targets_global_db_file(self):
        """After apply(), _yoyo_migration tracking table exists in db_path(), NOT
        in a per-project sandesh.db."""
        self._migrate.apply()
        global_db = self._sdb.db_path()
        self.assertTrue(
            os.path.isfile(global_db),
            f"Global DB not found at {global_db} after apply()"
        )
        con = sqlite3.connect(global_db)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        finally:
            con.close()
        self.assertIn(
            "_yoyo_migration", tables,
            "_yoyo_migration tracking table must exist in the global DB after apply(); "
            "currently migrate routes to a per-project DB so the global DB is untouched."
        )
        # Confirm the yoyo table is NOT in a stale per-project file.
        per_project_db = os.path.join(
            self._sdb.store_dir("P1"), self._sdb.DB_FILE
        )
        self.assertFalse(
            os.path.isfile(per_project_db),
            "Per-project sandesh.db must NOT exist after apply() — the global DB is the sole target."
        )

    def test_status_takes_no_project_id(self):
        """status() accepts zero arguments and returns (applied_ids, pending_ids)."""
        self._migrate.apply()
        applied_ids, pending_ids = self._migrate.status()   # no project_id — RED: TypeError
        self.assertIn("0001-baseline", applied_ids)
        self.assertIn("0002-drop-message-status", applied_ids)
        self.assertIn("0003-project-tracker", applied_ids)
        self.assertEqual(pending_ids, [],
                         "All three migrations must be applied — 0 pending.")

    def test_status_reports_chain_applied_zero_pending(self):
        """After setup('P1') + apply(), status reports the chain applied, 0 pending.

        CR-SAN-023 C1: deliberately un-pinned from len(applied)==3 — membership
        of the known steps + an empty pending list survives future migrations.
        """
        self._migrate.apply()
        applied_ids, pending_ids = self._migrate.status()
        for mid in ("0001-baseline", "0002-drop-message-status", "0003-project-tracker"):
            self.assertIn(mid, applied_ids,
                          f"Expected {mid} in applied, got {applied_ids}")
        self.assertEqual(pending_ids, [],
                         f"Expected 0 pending, got {pending_ids}")

    def test_rollback_takes_no_project_id(self):
        """rollback() accepts zero arguments (post-apply undo of one step)."""
        self._migrate.apply()
        self._migrate.rollback()   # no project_id — RED: TypeError

    def test_check_takes_no_project_id(self):
        """check() accepts zero arguments and returns an exit code (int)."""
        self._migrate.apply()
        result = self._migrate.check()   # no project_id — RED: TypeError
        self.assertIsInstance(result, int)

    def test_dump_schema_takes_no_project_id(self):
        """dump_schema() accepts zero arguments and returns a dict."""
        self._migrate.apply()
        schema = self._migrate.dump_schema()   # no project_id — RED: TypeError
        self.assertIsInstance(schema, dict)

    def test_diff_takes_no_old_snapshot_path(self):
        """diff(old_snapshot_path) accepts exactly one argument (no project_id)."""
        self._migrate.apply()
        result = self._migrate.diff(_SNAPSHOT_PATH)  # no project_id — RED: TypeError
        self.assertIsInstance(result, dict)
        self.assertIn("added", result)
        self.assertIn("removed", result)
        self.assertIn("changed", result)


# ---------------------------------------------------------------------------
# A2 — DEC-B: `migrate --project X` rejected; bare `migrate` needs no project
# ---------------------------------------------------------------------------

class MigrateCliProjectFlagTest(unittest.TestCase):
    """A2 (DEC-B): the migrate subcommand must NOT accept --project after C3.

    Contract:
      - cli.main(['migrate', '--project', 'X'])  → SystemExit(2)
      - cli.main(['migrate'])                     → no exit-2 "pass --project" error
          (bare migrate works against the global DB with no project routing needed).
      - cli.main(['--project', 'X', 'migrate'])   → migrate still runs (shared parent
          --project must not break OTHER subcommands; migrate itself ignores it).
    """

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        sdb = _sandbox_db()
        sdb.setup("P1")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()
        # Pre-apply so migrate is a fast no-op.
        self._migrate.apply()
        from sandesh import cli as _cli
        self._cli = _cli

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def test_migrate_project_flag_after_subcommand_is_exit2(self):
        """migrate --project X must be rejected with SystemExit(2) (unknown argument).

        RED: currently accepted — exits 0 and routes to the per-project DB.
        GREEN must remove --project from the migrate subparser so argparse rejects it.
        """
        with self.assertRaises(SystemExit) as ctx:
            self._cli.main(["migrate", "--project", "X"])
        self.assertEqual(
            ctx.exception.code, 2,
            f"Expected SystemExit(2) for 'migrate --project X' (unrecognised argument "
            f"after removing --project from the migrate subparser), got {ctx.exception.code!r}."
        )

    def test_bare_migrate_no_project_env_does_not_exit2(self):
        """bare `migrate` with no $SANDESH_PROJECT must NOT exit-2 with 'pass --project'.

        RED: currently _project_from_args() fires → exits 2 with the 'pass --project' error.
        GREEN removes _project_from_args() from migrate paths so no project is needed.
        """
        # Confirm $SANDESH_PROJECT is absent.
        self.assertNotIn("SANDESH_PROJECT", os.environ)
        # Should complete without SystemExit.
        try:
            result = self._cli.main(["migrate"])
        except SystemExit as exc:
            self.fail(
                f"bare `migrate` with no $SANDESH_PROJECT raised SystemExit({exc.code!r}) — "
                "after C3 migrate must not require a project (it targets the global DB)."
            )
        self.assertEqual(result, 0,
                         f"bare `migrate` should return 0 (fully migrated, nothing to do); got {result}")

    def test_project_flag_before_subcommand_still_works_for_other_subcommands(self):
        """--project X before the subcommand must still work for subcommands that need it.

        The shared-parent --project MUST survive in the argparse topology so that
        `sandesh --project X setup` etc. continue to work. This test asserts that
        `--project X migrate` at minimum does NOT raise SystemExit(2) on the flag
        itself — migrate may ignore the project value, but the flag must be parseable
        in the pre-subcommand position.
        """
        try:
            result = self._cli.main(["--project", "X", "migrate"])
        except SystemExit as exc:
            # Exit 0 is acceptable (fully migrated); exit non-2 is acceptable.
            # Exit 2 would mean the shared-parent --project broke.
            self.assertNotEqual(
                exc.code, 2,
                f"'sandesh --project X migrate' raised SystemExit(2) — "
                f"the shared-parent --project must still be parseable in the pre-subcommand "
                f"position so other subcommands (setup, send, …) are unaffected."
            )


# ---------------------------------------------------------------------------
# A3 — dynamic shape: dump_schema includes project table, excludes yoyo internals
# ---------------------------------------------------------------------------

class MigrateDumpSchemaDynamicShapeTest(unittest.TestCase):
    """A3 (DRIFT-4): dump_schema() must enumerate sqlite_master dynamically so the
    project table (and future tables) are visible, and must exclude all yoyo/sqlite
    internal tables."""

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        sdb = _sandbox_db()
        sdb.setup("GlobalDyn")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()
        self._migrate.apply()

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def test_dump_schema_includes_project_table(self):
        """dump_schema() must include the 'project' table.

        RED: _live_shape() hard-codes _CORE_TABLES (4 tables, no 'project')
        so project is invisible to dump_schema.
        """
        schema = self._migrate.dump_schema()
        tables = schema.get("tables", {})
        self.assertIn(
            "project", tables,
            "dump_schema() output must include the 'project' table — "
            "currently _live_shape() only iterates _CORE_TABLES (4 tables) "
            "so 'project' is invisible. GREEN must enumerate sqlite_master dynamically."
        )

    def test_dump_schema_includes_address_table_with_project_column(self):
        """dump_schema() must include address.project column."""
        schema = self._migrate.dump_schema()
        address_cols = schema.get("tables", {}).get("address", {}).get("columns", {})
        self.assertIn(
            "project", address_cols,
            "dump_schema() must include the 'project' column in the 'address' table — "
            "it was added by 0003 but _CORE_TABLES-based enumeration may miss it if the "
            "column list is also hard-coded."
        )

    def test_dump_schema_includes_all_five_business_tables(self):
        """dump_schema() must include all five business tables after apply()."""
        schema = self._migrate.dump_schema()
        tables = set(schema.get("tables", {}).keys())
        expected = {"address", "message", "message_recipient", "notifier", "project"}
        for t in expected:
            self.assertIn(
                t, tables,
                f"dump_schema() missing expected table '{t}'; got tables: {sorted(tables)}"
            )

    def test_dump_schema_excludes_sqlite_internal_tables(self):
        """dump_schema() must exclude all sqlite_* internal tables."""
        schema = self._migrate.dump_schema()
        tables = list(schema.get("tables", {}).keys())
        for t in tables:
            self.assertFalse(
                t.startswith("sqlite_"),
                f"dump_schema() must exclude sqlite_* internal tables but found '{t}'"
            )

    def test_dump_schema_excludes_yoyo_internal_tables(self):
        """dump_schema() must exclude all _yoyo* and yoyo* internal tables.

        yoyo 9.0.0 creates: _yoyo_migration, _yoyo_log, _yoyo_version, yoyo_lock.
        None must appear in dump_schema() output.
        """
        schema = self._migrate.dump_schema()
        tables = list(schema.get("tables", {}).keys())
        for t in tables:
            self.assertFalse(
                t.startswith("_yoyo") or t.startswith("yoyo"),
                f"dump_schema() must exclude yoyo internal tables but found '{t}'"
            )

    def test_dump_schema_only_business_tables(self):
        """Every table in dump_schema() must be a business table (no internal tables)."""
        schema = self._migrate.dump_schema()
        tables = list(schema.get("tables", {}).keys())
        for t in tables:
            self.assertFalse(
                t.startswith("sqlite_") or t.startswith("_yoyo") or t.startswith("yoyo"),
                f"dump_schema() emitted an internal table name '{t}' — "
                "only business tables (address, message, message_recipient, notifier, project) "
                "must appear."
            )


# ---------------------------------------------------------------------------
# A4 — snapshot equality: dump_schema() == current-schema.json on fresh store
# ---------------------------------------------------------------------------

class MigrateSnapshotEqualityTest(unittest.TestCase):
    """A4: dump_schema() on a fully-migrated fresh store must equal the committed
    sandesh/schema/current-schema.json snapshot.

    FAILS at RED because current-schema.json has no 'project' table. GREEN regenerates
    the snapshot so it matches the post-0003 schema.
    """

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        sdb = _sandbox_db()
        sdb.setup("SnapTest")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()
        self._migrate.apply()

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def test_dump_schema_equals_committed_snapshot(self):
        """dump_schema() == current-schema.json on a fully-migrated fresh store.

        RED failure modes:
          1. current-schema.json lacks 'project' table (committed before 0003).
          2. dump_schema() still uses _CORE_TABLES and misses 'project'.
        GREEN must regenerate current-schema.json AND make dump_schema() dynamic.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"current-schema.json not found at {_SNAPSHOT_PATH}"
        )
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            committed = json.load(fh)

        live = self._migrate.dump_schema()

        # Compare table sets.
        committed_tables = set(committed.get("tables", {}).keys())
        live_tables = set(live.get("tables", {}).keys())

        self.assertEqual(
            committed_tables, live_tables,
            f"Table sets differ between dump_schema() and current-schema.json.\n"
            f"  committed tables: {sorted(committed_tables)}\n"
            f"  live tables:      {sorted(live_tables)}\n"
            "GREEN must regenerate current-schema.json to include the 'project' table "
            "and remove _yoyo_migration (if it was previously included)."
        )

        # Deep equality.
        self.assertEqual(
            live, committed,
            "dump_schema() output does not match current-schema.json.\n"
            "GREEN must regenerate the snapshot with `sandesh migrate --dump-schema` "
            "on a fully-migrated store and commit the result."
        )


# ---------------------------------------------------------------------------
# A5 — --all alias: identical end state to bare migrate
# ---------------------------------------------------------------------------

class MigrateAllAliasTest(unittest.TestCase):
    """A5: `migrate --all` and bare `migrate` produce identical end state.

    Both fully migrate the one global DB; --all is an alias (idempotent).
    """

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        sdb = _sandbox_db()
        sdb.setup("AllTest")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()
        from sandesh import cli as _cli
        self._cli = _cli

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def _applied_ids(self):
        applied, _ = self._migrate.status()
        return set(applied)

    # CR-SAN-023 C1: the known steps every "fully migrated" store must contain.
    # Deliberately a SUBSET check (not equality) so the assertion survives
    # future chain growth; "fully migrated" itself is asserted via pending==[].
    _KNOWN_STEPS = {"0001-baseline", "0002-drop-message-status", "0003-project-tracker"}

    def test_bare_migrate_applies_all_migrations(self):
        """bare migrate (no --all) fully migrates the global DB."""
        try:
            self._cli.main(["migrate"])
        except SystemExit as exc:
            if exc.code != 0:
                self.fail(f"`migrate` raised SystemExit({exc.code}) — expected 0")
        applied, pending = self._migrate.status()
        self.assertTrue(
            self._KNOWN_STEPS.issubset(set(applied)),
            f"bare migrate must apply at least {sorted(self._KNOWN_STEPS)}; applied: {applied}"
        )
        self.assertEqual(
            pending, [],
            f"bare migrate must leave nothing pending; pending: {pending}"
        )

    def test_migrate_all_applies_all_migrations(self):
        """migrate --all fully migrates the global DB (same result as bare migrate)."""
        try:
            self._cli.main(["migrate", "--all"])
        except SystemExit as exc:
            if exc.code != 0:
                self.fail(f"`migrate --all` raised SystemExit({exc.code}) — expected 0")
        applied, pending = self._migrate.status()
        self.assertTrue(
            self._KNOWN_STEPS.issubset(set(applied)),
            f"migrate --all must apply at least {sorted(self._KNOWN_STEPS)}; applied: {applied}"
        )
        self.assertEqual(
            pending, [],
            f"migrate --all must leave nothing pending; pending: {pending}"
        )

    def test_bare_and_all_produce_identical_state(self):
        """Running bare migrate then --all again is idempotent (same applied set)."""
        try:
            self._cli.main(["migrate"])
        except SystemExit:
            pass
        applied_after_bare = self._applied_ids()

        try:
            self._cli.main(["migrate", "--all"])
        except SystemExit:
            pass
        applied_after_all = self._applied_ids()

        self.assertEqual(
            applied_after_bare, applied_after_all,
            f"bare migrate and migrate --all must produce identical applied sets.\n"
            f"  after bare:  {sorted(applied_after_bare)}\n"
            f"  after --all: {sorted(applied_after_all)}"
        )

    def test_migrate_all_is_idempotent(self):
        """Running migrate --all twice is idempotent."""
        try:
            self._cli.main(["migrate", "--all"])
        except SystemExit:
            pass
        applied_first = self._applied_ids()

        try:
            self._cli.main(["migrate", "--all"])
        except SystemExit:
            pass
        applied_second = self._applied_ids()

        self.assertEqual(
            applied_first, applied_second,
            "migrate --all must be idempotent — second run must not change the applied set."
        )


# ---------------------------------------------------------------------------
# A6 — check() gate semantics on the global DB
# ---------------------------------------------------------------------------

class MigrateCheckGateGlobalTest(unittest.TestCase):
    """A6: check() operates on the global DB.

    - Fully applied → returns 0.
    - Pending migration (fixture via SANDESH_MIGRATIONS_DIR) → returns non-zero.
    """

    def setUp(self):
        self._xdg = _make_xdg_home()
        self._old_xdg = _patch_xdg(self._xdg)
        self._old_proj = os.environ.pop("SANDESH_PROJECT", None)
        self._old_mig_dir = os.environ.pop("SANDESH_MIGRATIONS_DIR", None)
        sdb = _sandbox_db()
        sdb.setup("CheckTest")
        self._sdb = sdb
        self._migrate = _sandbox_migrate()

    def tearDown(self):
        _restore_xdg(self._old_xdg)
        if self._old_proj is not None:
            os.environ["SANDESH_PROJECT"] = self._old_proj
        if self._old_mig_dir is not None:
            os.environ["SANDESH_MIGRATIONS_DIR"] = self._old_mig_dir
        else:
            os.environ.pop("SANDESH_MIGRATIONS_DIR", None)
        import shutil
        shutil.rmtree(self._xdg, ignore_errors=True)

    def test_check_returns_zero_when_fully_migrated(self):
        """check() returns 0 on a fully-migrated global DB.

        RED: check(project_id) requires an arg → TypeError.
        """
        self._migrate.apply()
        result = self._migrate.check()   # no project_id
        self.assertEqual(
            result, 0,
            f"check() must return 0 on a fully-migrated store; got {result}"
        )

    def test_check_returns_nonzero_when_pending_migration_exists(self):
        """check() returns non-zero when there is a pending migration.

        Uses SANDESH_MIGRATIONS_DIR to point at a fixture dir that adds a
        9999-test migration beyond the standard three. After applying only the
        real migrations, the extra one is pending → check() must return non-zero.
        """
        import shutil

        # Build a fixture migrations dir: copy real migrations + add a pending one.
        fixture_dir = os.path.join(self._xdg, "fixture_migrations")
        real_dir = self._migrate.migrations_dir()
        shutil.copytree(real_dir, fixture_dir)

        # Add a harmless extra migration that will stay pending.
        extra_sql = os.path.join(fixture_dir, "9999-test-pending.sql")
        with open(extra_sql, "w", encoding="utf-8") as fh:
            fh.write("-- 9999-test-pending: intentionally never applied\n")
            fh.write("CREATE TABLE IF NOT EXISTS _test_pending_canary (id INTEGER);\n")

        # Apply only the real migrations (SANDESH_MIGRATIONS_DIR not yet set).
        self._migrate.apply()

        # Now point at the fixture dir so status/check see the extra pending migration.
        os.environ["SANDESH_MIGRATIONS_DIR"] = fixture_dir

        result = self._migrate.check()   # no project_id
        self.assertNotEqual(
            result, 0,
            "check() must return non-zero when there is a pending migration; "
            "got 0 — the global-DB check() is not seeing the extra pending step."
        )
        self.assertIsInstance(result, int,
                              f"check() must return an int exit code; got {type(result)}")

    def test_check_no_arg_call_signature(self):
        """check() must accept zero arguments (TypeError at RED confirms wrong signature)."""
        self._migrate.apply()
        try:
            result = self._migrate.check()
        except TypeError as exc:
            self.fail(
                f"check() raised TypeError — it still requires project_id: {exc}\n"
                "GREEN must change the signature to check() with no required args."
            )
        self.assertIsInstance(result, int)


# ---------------------------------------------------------------------------
# B-REPOINT: test_ci_migration_gate.py contract tests for post-C3 yml
# (These are kept in test_ci_migration_gate.py; this block documents the
# cross-file expectation: no --project on migrate in publish-pypi.yml)
# ---------------------------------------------------------------------------

class MigrateYmlGateNoDumpSchemaProjectTest(unittest.TestCase):
    """Asserts the post-C3 contract on publish-pypi.yml:
    the dump-schema gate step must NOT carry --project on its migrate invocations.

    RED: the yml currently has `sandesh migrate --dump-schema --project ci`.
    GREEN removes the --project flag so the step aligns with the no-project API.
    """

    def setUp(self):
        self._workflow_path = os.path.join(
            _REPO_ROOT, ".github", "workflows", "publish-pypi.yml"
        )
        if not os.path.isfile(self._workflow_path):
            self.skipTest(f"Workflow file not found: {self._workflow_path}")
        with open(self._workflow_path, encoding="utf-8") as fh:
            self._text = fh.read()

    def test_migrate_dump_schema_has_no_project_flag(self):
        """publish-pypi.yml gate step: `migrate --dump-schema` must carry no --project.

        RED: current yml has `migrate --dump-schema --project ci` → this assertion fails.
        GREEN edits the yml to `migrate --dump-schema` (no project routing needed).
        """
        import re
        # Find every occurrence of migrate --dump-schema and assert no --project after it.
        pattern = re.compile(r"migrate\s+--dump-schema\s+--project\b")
        match = pattern.search(self._text)
        self.assertIsNone(
            match,
            "publish-pypi.yml: found `migrate --dump-schema --project` — "
            "after C3 the migrate engine requires no --project (global DB). "
            "GREEN must remove the --project flag from this invocation."
        )

    def test_migrate_all_has_no_project_flag_on_migrate(self):
        """publish-pypi.yml gate step: `migrate --all` must carry no --project flag
        in the migrate invocation (it may have `--project` on setup, which is fine).

        RED: if the yml has `migrate --all --project ci` or `migrate --project ci --all`.
        """
        import re
        # Look for migrate followed by --project (before or after --all).
        pattern = re.compile(r"migrate\s+(?:--all\s+--project|--project\s+\S+\s+--all)\b")
        match = pattern.search(self._text)
        self.assertIsNone(
            match,
            "publish-pypi.yml: found `migrate --all --project` or `migrate --project X --all` — "
            "after C3 migrate --all needs no --project. "
            "GREEN must remove --project from the migrate --all invocation."
        )

    def test_sandesh_setup_still_uses_project_flag(self):
        """publish-pypi.yml: `sandesh setup --project ci` is still expected (setup still
        takes a project). This must PASS at both RED and GREEN — it confirms the
        updated yml keeps the setup call intact while only removing --project from migrate.
        """
        self.assertIn(
            "sandesh setup --project ci",
            self._text,
            "publish-pypi.yml: `sandesh setup --project ci` not found — "
            "the gate step must still provision the project via setup (setup still "
            "takes a project_id). Only the migrate invocations lose --project."
        )

    def test_migrate_dump_schema_still_present_in_yml(self):
        """publish-pypi.yml must still contain a `migrate --dump-schema` call
        (the snapshot-sync gate must remain — just without --project)."""
        self.assertIn(
            "migrate --dump-schema",
            self._text,
            "publish-pypi.yml: `migrate --dump-schema` not found — "
            "the snapshot-sync gate step must still exist after C3."
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
