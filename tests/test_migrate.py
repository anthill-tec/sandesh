"""test_migrate.py — RED tests for CR-SAN-017 Cycles 1 and 2.

Covers:
  AC1  — import purity: sandesh_db / cli / notify / mcp_server / migrate.py must
          not pull yoyo or jsonschema into sys.modules at module load (all five
  AC2  — friendly-absent guard (system python3, no [migrate] deps)
  AC3  — 0001-baseline == _SCHEMA: migrations-provisioned store is byte-for-table
          identical to sandesh_db.setup-provisioned store (PRAGMA table_info)
  AC4  — baseline adoption: pre-yoyo store (tables present, no _yoyo_migration row
          for 0001) is handled by marking 0001 applied without re-running it
  DRIFT-2 packaging — pyproject.toml declares the [migrate] optional-dependency entry
          assertions use clean subprocesses so the parent process's already-imported
          modules cannot pollute the check).
  AC2  — friendly-absent guard: when [migrate] deps are unavailable (system python3),
          `python3 -m sandesh.cli migrate --status --project X` exits non-zero and
          mentions the [migrate] extra install hint in its output.
  DRIFT-2 packaging — pyproject.toml declares the `migrate` optional-dependency entry
          with exact floors, and hatchling force-include rules cover sandesh/migrations/
          and sandesh/schema/.

Run via the crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_migrate --agent CR-SAN-017-1-RED
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")

# The SYSTEM python3 (not the venv) — used for AC2 friendly-absent test.
# python-crucible uses the venv; the sys.executable here IS the venv interpreter.
# We need bare system python3 which has no yoyo/jsonschema.
_SYSTEM_PYTHON = "/usr/bin/python3"


# ---------------------------------------------------------------------------
# AC1 — import purity (clean subprocess per module to avoid cross-contamination)
# ---------------------------------------------------------------------------

class MigrateImportPurityTest(unittest.TestCase):
    """AC1: none of the five Sandesh modules pull yoyo/jsonschema at module load."""

    def _assert_no_yoyo_jsonschema_on_import(self, import_snippet, label):
        """Run `import_snippet` in a clean subprocess and assert neither yoyo nor
        jsonschema appear in sys.modules afterwards.

        Uses the VENV interpreter (sys.executable) so all sandesh modules are
        importable; the key property being checked is WHAT those imports bring in,
        not whether they can be imported at all.
        """
        script_lines = [
            "import sys",
            f"sys.path.insert(0, {_REPO_ROOT!r})",
            import_snippet,
            "yoyo_present = any(k.startswith('yoyo') for k in sys.modules)",
            "jsonschema_present = any(k.startswith('jsonschema') for k in sys.modules)",
            "if yoyo_present or jsonschema_present:",
            "    tainted = [k for k in sys.modules if k.startswith('yoyo') or k.startswith('jsonschema')]",
            "    print(f'TAINTED: {tainted}', flush=True)",
            "    sys.exit(1)",
            "print('CLEAN', flush=True)",
        ]
        script = "\n".join(script_lines)
        # Write to a temp file to avoid -c restriction
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name
        try:
            r = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": _REPO_ROOT},
            )
        finally:
            os.unlink(script_path)

        combined = (r.stdout + r.stderr).strip()
        self.assertEqual(
            r.returncode,
            0,
            f"[{label}] importing the module pulled yoyo/jsonschema into sys.modules.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        self.assertIn(
            "CLEAN",
            r.stdout,
            f"[{label}] expected 'CLEAN' confirmation in stdout.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_sandesh_db_does_not_import_yoyo_or_jsonschema(self):
        """AC1: importing sandesh.sandesh_db must not pull yoyo or jsonschema."""
        self._assert_no_yoyo_jsonschema_on_import(
            "from sandesh import sandesh_db",
            "sandesh_db",
        )

    def test_cli_does_not_import_yoyo_or_jsonschema(self):
        """AC1: importing sandesh.cli (base parser build) must not pull yoyo or jsonschema."""
        # Import cli module — this builds the base parser; must stay stdlib-pure.
        self._assert_no_yoyo_jsonschema_on_import(
            "from sandesh import cli",
            "sandesh.cli",
        )

    def test_notify_does_not_import_yoyo_or_jsonschema(self):
        """AC1: importing sandesh.notify must not pull yoyo or jsonschema."""
        self._assert_no_yoyo_jsonschema_on_import(
            "from sandesh import notify",
            "sandesh.notify",
        )

    def test_mcp_server_does_not_import_yoyo_or_jsonschema(self):
        """AC1: importing sandesh.mcp_server must not pull yoyo or jsonschema via its OWN code.

        mcp_server.py imports `mcp` at module top in a try/except (the known pattern —
        spec gap-analysis §Dim3 note). When mcp IS installed, mcp itself transitively
        imports jsonschema. To test mcp_server's OWN purity we block mcp from loading
        (sys.modules['mcp'] = None) so the try/except falls through, leaving mcp_server
        in the mcp-absent path. yoyo/jsonschema must still be absent after that import.
        """
        script_lines = [
            "import sys",
            f"sys.path.insert(0, {_REPO_ROOT!r})",
            # Block mcp so its transitive jsonschema dep doesn't pollute the check.
            "sys.modules['mcp'] = None",
            "from sandesh import mcp_server",
            "yoyo_present = any(k.startswith('yoyo') for k in sys.modules)",
            "jsonschema_present = any(k.startswith('jsonschema') for k in sys.modules)",
            "if yoyo_present or jsonschema_present:",
            "    tainted = [k for k in sys.modules if k.startswith('yoyo') or k.startswith('jsonschema')]",
            "    print(f'TAINTED: {tainted}', flush=True)",
            "    sys.exit(1)",
            "print('CLEAN', flush=True)",
        ]
        script = "\n".join(script_lines)
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name
        try:
            r = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": _REPO_ROOT},
            )
        finally:
            os.unlink(script_path)

        self.assertEqual(
            r.returncode,
            0,
            "[sandesh.mcp_server] mcp_server's own code pulled yoyo/jsonschema into "
            "sys.modules (mcp was blocked to isolate mcp_server's own imports).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        self.assertIn(
            "CLEAN",
            r.stdout,
            "[sandesh.mcp_server] expected 'CLEAN' confirmation.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_migrate_module_does_not_import_yoyo_or_jsonschema_at_load(self):
        """AC1: importing sandesh.migrate must not pull yoyo/jsonschema at module load
        (they must be lazy-imported inside function bodies only).

        RED: this test fails with ModuleNotFoundError / ImportError because
        sandesh/migrate.py does not yet exist.
        """
        self._assert_no_yoyo_jsonschema_on_import(
            "from sandesh import migrate",
            "sandesh.migrate",
        )


# ---------------------------------------------------------------------------
# AC2 — friendly-absent guard (system python3 has no yoyo/jsonschema)
# ---------------------------------------------------------------------------

class MigrateFriendlyAbsentTest(unittest.TestCase):
    """AC2: when [migrate] deps are absent, sandesh migrate --status exits non-zero
    and mentions the [migrate] extra install hint.

    Uses the SYSTEM python3 (/usr/bin/python3) which does not have yoyo-migrations
    or jsonschema installed — no env manipulation needed.
    """

    def _run_migrate_status_on_system_python(self):
        """Invoke `python3 -m sandesh.cli migrate --status --project SomeProj`
        under the system python3 (no [migrate] deps) from the repo root.
        Returns the CompletedProcess.
        """
        import tempfile
        # Write a minimal launcher script to avoid -c restriction.
        script = (
            "import sys\n"
            f"sys.path.insert(0, {_REPO_ROOT!r})\n"
            "sys.argv = ['sandesh', '--project', 'SomeProj', 'migrate', '--status']\n"
            "from sandesh import cli\n"
            "cli.main()\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name
        try:
            r = subprocess.run(
                [_SYSTEM_PYTHON, script_path],
                capture_output=True,
                text=True,
                cwd=_REPO_ROOT,
            )
        finally:
            os.unlink(script_path)
        return r

    def test_migrate_absent_exits_nonzero(self):
        """AC2: sandesh migrate --status on system python3 (no [migrate] deps) must exit non-zero.

        RED: fails because there is no 'migrate' subcommand yet — argparse will exit
        with a usage error rather than the friendly [migrate] extra message.
        """
        r = self._run_migrate_status_on_system_python()
        self.assertNotEqual(
            r.returncode,
            0,
            "sandesh migrate --status must exit non-zero when [migrate] deps are absent;\n"
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_migrate_absent_mentions_migrate_extra(self):
        """AC2: the output must mention the '[migrate]' extra so the user knows what to install.

        RED: fails because there is no 'migrate' subcommand — the argparse error
        message does not mention '[migrate]'.
        """
        r = self._run_migrate_status_on_system_python()
        combined = (r.stdout + r.stderr).lower()
        self.assertIn(
            "[migrate]",
            combined,
            "Output must mention '[migrate]' (the extra name) when deps are absent;\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_migrate_absent_mentions_sandesh_relay(self):
        """AC2: the output must mention 'sandesh-relay[migrate]' (the full install target).

        RED: no 'migrate' subcommand → no friendly message → assertion fails.
        """
        r = self._run_migrate_status_on_system_python()
        combined = (r.stdout + r.stderr).lower()
        # Accept any casing / hyphen variant of the package name.
        has_package_hint = (
            "sandesh-relay[migrate]" in combined
            or "sandesh_relay[migrate]" in combined
            or ("sandesh" in combined and "relay" in combined and "[migrate]" in combined)
        )
        self.assertTrue(
            has_package_hint,
            "Output must mention 'sandesh-relay[migrate]' (the install target) when deps are absent;\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_migrate_absent_no_raw_traceback(self):
        """AC2: a raw Python traceback must NOT appear — only the friendly message.

        RED: if the subcommand is missing, argparse may or may not produce a traceback;
        in any case the friendly guard message is absent, so the earlier assertions
        already cover the RED. This guards the GREEN shape.
        """
        r = self._run_migrate_status_on_system_python()
        combined = r.stdout + r.stderr
        self.assertNotIn(
            "Traceback (most recent call last)",
            combined,
            "A raw Python traceback must NOT appear — only the friendly install hint.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ---------------------------------------------------------------------------
# DRIFT-2 packaging — pyproject.toml [migrate] extra + force-include rules
# ---------------------------------------------------------------------------

def _load_pyproject():
    with open(_PYPROJECT_PATH, "rb") as fh:
        return tomllib.load(fh)


class MigrateOptionalDependencyTest(unittest.TestCase):
    """DRIFT-2 / §S1: pyproject.toml must declare the [migrate] optional-dependency
    with the user-decided exact floors: yoyo-migrations>=9,<10 and jsonschema>=4.26.

    RED: the 'migrate' key does not yet exist in [project.optional-dependencies].
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest(f"pyproject.toml missing at {_PYPROJECT_PATH}")
        cls.data = _load_pyproject()
        cls.opt_deps = cls.data.get("project", {}).get("optional-dependencies", {})

    def test_migrate_extra_present(self):
        """[project.optional-dependencies].migrate must exist and be a non-empty list.

        RED: key absent.
        """
        self.assertIn(
            "migrate",
            self.opt_deps,
            "[project.optional-dependencies] must declare a 'migrate' key",
        )
        self.assertIsInstance(
            self.opt_deps["migrate"],
            list,
            "[project.optional-dependencies].migrate must be a list",
        )
        self.assertGreater(
            len(self.opt_deps.get("migrate", [])),
            0,
            "[project.optional-dependencies].migrate must not be empty",
        )

    def test_migrate_extra_contains_yoyo_lower_bound(self):
        """migrate extra must contain 'yoyo-migrations>=9' (user-decided floor).

        RED: key absent.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        matches = [
            e for e in migrate_list
            if isinstance(e, str) and "yoyo-migrations" in e and ">=9" in e
        ]
        self.assertTrue(
            len(matches) >= 1,
            f"migrate extra must contain 'yoyo-migrations>=9,...'; got {migrate_list!r}",
        )

    def test_migrate_extra_contains_yoyo_upper_bound(self):
        """migrate extra must contain 'yoyo-migrations...<10' (major-pin guard).

        RED: key absent.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        matches = [
            e for e in migrate_list
            if isinstance(e, str) and "yoyo-migrations" in e and "<10" in e
        ]
        self.assertTrue(
            len(matches) >= 1,
            f"migrate extra must contain 'yoyo-migrations...<10' upper bound; got {migrate_list!r}",
        )

    def test_migrate_extra_exact_yoyo_specifier(self):
        """migrate extra yoyo entry must be exactly 'yoyo-migrations>=9,<10' (§S1 spec).

        RED: key absent.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        # Normalise whitespace for comparison.
        normalised = ["".join(e.split()) for e in migrate_list if isinstance(e, str)]
        self.assertIn(
            "yoyo-migrations>=9,<10",
            normalised,
            f"migrate extra must contain exactly 'yoyo-migrations>=9,<10'; got {migrate_list!r}",
        )

    def test_migrate_extra_contains_jsonschema_lower_bound(self):
        """migrate extra must contain 'jsonschema>=4.26' (user-decided floor).

        RED: key absent.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        matches = [
            e for e in migrate_list
            if isinstance(e, str) and "jsonschema" in e and ">=4.26" in e
        ]
        self.assertTrue(
            len(matches) >= 1,
            f"migrate extra must contain 'jsonschema>=4.26'; got {migrate_list!r}",
        )

    def test_migrate_extra_exact_jsonschema_specifier(self):
        """migrate extra jsonschema entry must be exactly 'jsonschema>=4.26' (§S1 spec).

        RED: key absent.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        normalised = ["".join(e.split()) for e in migrate_list if isinstance(e, str)]
        self.assertIn(
            "jsonschema>=4.26",
            normalised,
            f"migrate extra must contain exactly 'jsonschema>=4.26'; got {migrate_list!r}",
        )

    def test_migrate_extra_has_exactly_two_entries(self):
        """migrate extra must have exactly 2 entries: yoyo-migrations and jsonschema.

        RED: key absent; also guards against accidental additions.
        """
        migrate_list = self.opt_deps.get("migrate", [])
        self.assertEqual(
            len(migrate_list),
            2,
            f"migrate extra must have exactly 2 entries; got {len(migrate_list)}: {migrate_list!r}",
        )


class MigrateForceIncludeTest(unittest.TestCase):
    """DRIFT-2 / §S1: hatchling must force-include sandesh/migrations/ and sandesh/schema/
    in the wheel — otherwise an installed sandesh-relay[migrate] can't find its migrations.

    RED: neither force-include entry exists yet in pyproject.toml.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest(f"pyproject.toml missing at {_PYPROJECT_PATH}")
        cls.data = _load_pyproject()
        # [tool.hatch.build.targets.wheel.force-include]
        cls.force_include = (
            cls.data
            .get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("force-include", {})
        )

    def test_force_include_section_exists(self):
        """[tool.hatch.build.targets.wheel.force-include] section must exist.

        RED: section absent (no migrations/schema entries yet).
        """
        self.assertIsInstance(
            self.force_include,
            dict,
            "[tool.hatch.build.targets.wheel.force-include] must be a dict; "
            f"got {type(self.force_include)!r}",
        )

    def test_force_include_covers_migrations_dir(self):
        """sandesh/migrations/ must appear as a source key in force-include.

        RED: entry absent.
        """
        keys = list(self.force_include.keys())
        has_migrations = any(
            "sandesh/migrations" in k or "sandesh\\migrations" in k
            for k in keys
        )
        self.assertTrue(
            has_migrations,
            "force-include must have an entry covering 'sandesh/migrations/'; "
            f"current keys: {keys!r}",
        )

    def test_force_include_covers_schema_dir(self):
        """sandesh/schema/ must appear as a source key in force-include.

        RED: entry absent.
        """
        keys = list(self.force_include.keys())
        has_schema = any(
            "sandesh/schema" in k or "sandesh\\schema" in k
            for k in keys
        )
        self.assertTrue(
            has_schema,
            "force-include must have an entry covering 'sandesh/schema/'; "
            f"current keys: {keys!r}",
        )

    def test_force_include_migrations_maps_to_package_path(self):
        """The sandesh/migrations/ force-include value must map to the correct wheel path.

        RED: entry absent.
        """
        # Find the migrations entry (key contains 'sandesh/migrations').
        migrations_entry = {
            k: v for k, v in self.force_include.items()
            if "sandesh/migrations" in k or "sandesh\\migrations" in k
        }
        self.assertTrue(
            len(migrations_entry) >= 1,
            f"No sandesh/migrations entry in force-include; keys: {list(self.force_include.keys())!r}",
        )
        # The wheel destination must also be under sandesh/migrations.
        for src, dst in migrations_entry.items():
            self.assertIn(
                "sandesh/migrations",
                dst,
                f"force-include destination for '{src}' must map to 'sandesh/migrations/...'; got {dst!r}",
            )

    def test_force_include_schema_maps_to_package_path(self):
        """The sandesh/schema/ force-include value must map to the correct wheel path.

        RED: entry absent.
        """
        schema_entry = {
            k: v for k, v in self.force_include.items()
            if "sandesh/schema" in k or "sandesh\\schema" in k
        }
        self.assertTrue(
            len(schema_entry) >= 1,
            f"No sandesh/schema entry in force-include; keys: {list(self.force_include.keys())!r}",
        )
        for src, dst in schema_entry.items():
            self.assertIn(
                "sandesh/schema",
                dst,
                f"force-include destination for '{src}' must map to 'sandesh/schema/...'; got {dst!r}",
            )


# ---------------------------------------------------------------------------
# Cycle 2 — AC3 + AC4: engine apply/status/migrations_dir + 0001-baseline
# ---------------------------------------------------------------------------

_FOUR_TABLES = ("address", "message", "message_recipient", "notifier")


def _pragma_table_info(db_path, table):
    """Return PRAGMA table_info rows for `table` as a sorted list of dicts
    with keys: name, type, notnull, dflt_value, pk.  The cid column is
    excluded so comparisons are position-independent.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    con.close()
    return sorted(
        [
            {
                "name": r["name"],
                "type": r["type"],
                "notnull": r["notnull"],
                "dflt_value": r["dflt_value"],
                "pk": r["pk"],
            }
            for r in rows
        ],
        key=lambda d: d["name"],
    )


def _list_user_tables(db_path):
    """Return the set of user-defined table names in `db_path`."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    con.close()
    return {r[0] for r in rows}


def _setup_store(project_id, data_home):
    """Provision a project store via sandesh_db.setup with XDG_DATA_HOME overridden."""
    import os
    os.environ["XDG_DATA_HOME"] = data_home
    import sys
    sys.path.insert(0, _REPO_ROOT)
    from sandesh import sandesh_db
    sandesh_db.setup(project_id)
    store = sandesh_db.store_dir(project_id)
    return store


class MigrateEngineInterfaceTest(unittest.TestCase):
    """Structural gate: the three engine callables must exist on sandesh.migrate.

    These tests fail with AttributeError when GREEN hasn't implemented the
    functions yet — that is valid RED (the interface is absent).

    Not a behaviour test on its own, but the three behavioural suites below
    depend on these symbols existing.  Having explicit attribute tests means a
    collection failure here surfaces a clear diagnostic rather than a cryptic
    NameError inside a setUp.
    """

    def test_apply_callable_exists(self):
        """sandesh.migrate.apply must be a callable (takes project_id).
        RED: AttributeError because the function does not exist yet.
        """
        from sandesh import migrate  # noqa: F401 — may raise AttributeError
        self.assertTrue(
            callable(getattr(migrate, "apply", None)),
            "sandesh.migrate.apply must be a callable; attribute is absent or not callable",
        )

    def test_status_callable_exists(self):
        """sandesh.migrate.status must be a callable (takes project_id).
        RED: AttributeError because the function does not exist yet.
        """
        from sandesh import migrate  # noqa: F401
        self.assertTrue(
            callable(getattr(migrate, "status", None)),
            "sandesh.migrate.status must be a callable; attribute is absent or not callable",
        )

    def test_migrations_dir_callable_exists(self):
        """sandesh.migrate.migrations_dir must be a callable returning a path.
        RED: AttributeError because the function does not exist yet.
        """
        from sandesh import migrate  # noqa: F401
        self.assertTrue(
            callable(getattr(migrate, "migrations_dir", None)),
            "sandesh.migrate.migrations_dir must be a callable; attribute is absent or not callable",
        )


class MigrateMigrationsDirTest(unittest.TestCase):
    """migrations_dir() must return a real, readable path that contains
    0001-baseline (as a .sql file or a directory).
    RED: AttributeError/ImportError until the function is implemented.
    """

    def test_migrations_dir_returns_existing_path(self):
        """migrations_dir() must return an existing directory path.
        RED: function absent OR 0001-baseline not yet written.
        """
        import os
        from sandesh import migrate
        d = migrate.migrations_dir()
        self.assertIsInstance(d, str, "migrations_dir() must return a str path")
        self.assertTrue(
            os.path.isdir(d),
            f"migrations_dir() returned {d!r} which is not an existing directory",
        )

    def test_migrations_dir_contains_0001_baseline(self):
        """migrations_dir() must contain the 0001-baseline migration source.

        Accepted forms:
          - a file named '0001-baseline.sql' (plain SQL step)
          - a directory named '0001-baseline' (package-style step)

        RED: absent until GREEN creates the migration file.
        """
        import os
        from sandesh import migrate
        d = migrate.migrations_dir()
        entries = os.listdir(d)
        has_baseline = (
            "0001-baseline.sql" in entries
            or "0001-baseline" in entries
        )
        self.assertTrue(
            has_baseline,
            f"migrations_dir() ({d!r}) must contain '0001-baseline.sql' or '0001-baseline/'; "
            f"found: {entries!r}",
        )


# ---------------------------------------------------------------------------
# AC3 — 0001-baseline == _SCHEMA  (byte-for-table PRAGMA comparison)
# ---------------------------------------------------------------------------

class MigrateBaselineEqualsSchemaTest(unittest.TestCase):
    """AC3: a store provisioned by apply() from empty via 0001-baseline must have
    the SAME table structure as one provisioned by sandesh_db.setup().

    Verification is via PRAGMA table_info on each of the four tables:
    address, message, message_recipient, notifier.  Column name/type/notnull/
    dflt_value/pk are compared; row order is normalised (sorted by name) so
    schema column-declaration order differences don't matter.

    This cycle is BEFORE 0002, so message.status is PRESENT in both stores —
    the tables match WITH the status column.

    RED: fails because apply() does not exist and/or 0001-baseline does not exist.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_ac3_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _store_a_path(self):
        """DB file for a migrations-provisioned store."""
        import os
        data_home_a = os.path.join(self._tmpdir, "data_a")
        return os.path.join(data_home_a, "sandesh", "projects", "AC3Test", "sandesh.db"), data_home_a

    def _store_b_path(self):
        """DB file for a sandesh_db.setup()-provisioned store."""
        import os
        data_home_b = os.path.join(self._tmpdir, "data_b")
        return os.path.join(data_home_b, "sandesh", "projects", "AC3Test", "sandesh.db"), data_home_b

    def _provision_via_migrations(self, data_home_a):
        """Apply 0001-baseline to a brand-new empty store."""
        import os
        os.environ["XDG_DATA_HOME"] = data_home_a
        from sandesh import migrate
        migrate.apply("AC3Test")

    def _provision_via_setup(self, data_home_b):
        """Provision a store via sandesh_db.setup()."""
        import os
        os.environ["XDG_DATA_HOME"] = data_home_b
        from sandesh import sandesh_db
        sandesh_db.setup("AC3Test")

    def test_four_tables_exist_after_apply(self):
        """After apply() from empty, all four tables must exist in the DB.
        RED: apply() absent OR 0001-baseline not written.
        """
        db_path_a, data_home_a = self._store_a_path()
        self._provision_via_migrations(data_home_a)
        tables = _list_user_tables(db_path_a)
        for t in _FOUR_TABLES:
            self.assertIn(
                t,
                tables,
                f"Table '{t}' must exist after apply() with 0001-baseline; found: {tables!r}",
            )

    def test_address_table_columns_match(self):
        """address table: PRAGMA table_info must be identical between
        migrations-provisioned (A) and setup()-provisioned (B) stores.
        RED: apply()/0001-baseline absent.
        """
        db_path_a, data_home_a = self._store_a_path()
        db_path_b, data_home_b = self._store_b_path()
        self._provision_via_migrations(data_home_a)
        self._provision_via_setup(data_home_b)
        cols_a = _pragma_table_info(db_path_a, "address")
        cols_b = _pragma_table_info(db_path_b, "address")
        self.assertEqual(
            cols_a,
            cols_b,
            "address table PRAGMA table_info mismatch between "
            "migrations-provisioned (A) and setup()-provisioned (B):\n"
            f"  A: {cols_a}\n  B: {cols_b}",
        )

    def test_message_table_columns_match(self):
        """message table: PRAGMA table_info must be identical (including
        message.status which is present in this pre-0002 cycle).
        RED: apply()/0001-baseline absent.
        """
        db_path_a, data_home_a = self._store_a_path()
        db_path_b, data_home_b = self._store_b_path()
        self._provision_via_migrations(data_home_a)
        self._provision_via_setup(data_home_b)
        cols_a = _pragma_table_info(db_path_a, "message")
        cols_b = _pragma_table_info(db_path_b, "message")
        self.assertEqual(
            cols_a,
            cols_b,
            "message table PRAGMA table_info mismatch between "
            "migrations-provisioned (A) and setup()-provisioned (B):\n"
            f"  A: {cols_a}\n  B: {cols_b}",
        )

    def test_message_has_status_column_pre_0002(self):
        """In this cycle (before 0002), message.status must be present in the
        migrations-provisioned store — 0001-baseline reproduces _SCHEMA faithfully.
        RED: apply()/0001-baseline absent.
        """
        db_path_a, data_home_a = self._store_a_path()
        self._provision_via_migrations(data_home_a)
        cols_a = _pragma_table_info(db_path_a, "message")
        col_names = [c["name"] for c in cols_a]
        self.assertIn(
            "status",
            col_names,
            "message.status must exist in a 0001-only migrations store "
            f"(0002 not yet applied); columns found: {col_names!r}",
        )

    def test_message_recipient_table_columns_match(self):
        """message_recipient table: PRAGMA table_info must match.
        RED: apply()/0001-baseline absent.
        """
        db_path_a, data_home_a = self._store_a_path()
        db_path_b, data_home_b = self._store_b_path()
        self._provision_via_migrations(data_home_a)
        self._provision_via_setup(data_home_b)
        cols_a = _pragma_table_info(db_path_a, "message_recipient")
        cols_b = _pragma_table_info(db_path_b, "message_recipient")
        self.assertEqual(
            cols_a,
            cols_b,
            "message_recipient table PRAGMA table_info mismatch:\n"
            f"  A: {cols_a}\n  B: {cols_b}",
        )

    def test_notifier_table_columns_match(self):
        """notifier table: PRAGMA table_info must match.
        RED: apply()/0001-baseline absent.
        """
        db_path_a, data_home_a = self._store_a_path()
        db_path_b, data_home_b = self._store_b_path()
        self._provision_via_migrations(data_home_a)
        self._provision_via_setup(data_home_b)
        cols_a = _pragma_table_info(db_path_a, "notifier")
        cols_b = _pragma_table_info(db_path_b, "notifier")
        self.assertEqual(
            cols_a,
            cols_b,
            "notifier table PRAGMA table_info mismatch:\n"
            f"  A: {cols_a}\n  B: {cols_b}",
        )

    def test_status_returns_0001_applied_zero_pending_after_apply(self):
        """After apply() from empty, status() must report 0001-baseline applied
        and zero pending (only one migration exists this cycle).
        RED: apply()/status() absent OR 0001-baseline not written.
        """
        _, data_home_a = self._store_a_path()
        self._provision_via_migrations(data_home_a)
        import os
        os.environ["XDG_DATA_HOME"] = data_home_a
        from sandesh import migrate
        applied, pending = migrate.status("AC3Test")
        self.assertIn(
            "0001-baseline",
            applied,
            f"status() must report '0001-baseline' in applied after apply(); got applied={applied!r}",
        )
        self.assertEqual(
            len(pending),
            0,
            f"status() must report 0 pending after apply() with only 0001; got pending={pending!r}",
        )
        # Exact count: exactly 1 applied (only 0001 exists this cycle)
        self.assertEqual(
            len(applied),
            1,
            f"Exactly 1 migration must be applied (only 0001-baseline this cycle); got {applied!r}",
        )


# ---------------------------------------------------------------------------
# AC4 — baseline adoption (pre-yoyo store → mark 0001 applied, no re-run)
# ---------------------------------------------------------------------------

class MigrateBaselineAdoptionTest(unittest.TestCase):
    """AC4: a pre-yoyo store (four tables present, no _yoyo_migration entry for
    0001-baseline) is handled correctly by apply():

      (a) completes WITHOUT a 'table already exists' error
      (b) 0001-baseline is recorded as APPLIED (marked, not re-run)
      (c) status() reports 0001-baseline applied + 0 pending

    Contrast: a brand-new empty store → apply() runs 0001 normally and ends
    with four tables + 0001 applied.

    RED: apply()/status() absent, OR adoption glue not yet written.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_ac4_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_pre_yoyo_store(self, project_id, data_home):
        """Create a store via sandesh_db.setup() (four tables, no _yoyo_migration).
        Returns the db_path.
        """
        import os
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import sandesh_db
        sandesh_db.setup(project_id)
        db_path = os.path.join(
            data_home, "sandesh", "projects", project_id, "sandesh.db"
        )
        # Verify: _yoyo_migration must NOT exist at this point
        tables = _list_user_tables(db_path)
        if "_yoyo_migration" in tables:
            raise AssertionError(
                "_yoyo_migration should not exist in a sandesh_db.setup()-only store"
            )
        return db_path

    def test_adoption_does_not_raise_table_already_exists(self):
        """apply() on a pre-yoyo store must NOT raise any error (esp. 'table
        already exists' from trying to CREATE TABLE on existing tables).

        RED: apply() absent, OR adoption glue not written (yoyo would attempt
        to re-run CREATE TABLE IF NOT EXISTS — but raw SQL step would error
        on tables that already have data, or yoyo would error differently).

        Note: CREATE TABLE IF NOT EXISTS would silently succeed, but a
        migration that uses CREATE TABLE (without IF NOT EXISTS) would fail.
        The adoption glue must detect the pre-yoyo situation and mark, not run.
        """
        data_home = os.path.join(self._tmpdir, "data_pre_yoyo")
        self._make_pre_yoyo_store("AdoptTest", data_home)
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import migrate
        # Must not raise
        try:
            migrate.apply("AdoptTest")
        except Exception as e:
            self.fail(
                f"apply() on a pre-yoyo store raised {type(e).__name__}: {e}\n"
                "Expected: 0001-baseline is MARKED applied (not re-run); no error."
            )

    def test_adoption_records_0001_as_applied(self):
        """After apply() on a pre-yoyo store, 0001-baseline must be recorded as
        applied in the yoyo tracking table (_yoyo_migration).

        Verification is via backend.is_applied() or by checking _yoyo_migration
        directly — both forms are acceptable; the test uses status() which wraps
        the backend query.

        RED: adoption glue absent → 0001 not recorded OR apply() raises.
        """
        data_home = os.path.join(self._tmpdir, "data_adopt_rec")
        db_path = self._make_pre_yoyo_store("AdoptRec", data_home)
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import migrate
        migrate.apply("AdoptRec")
        applied, _pending = migrate.status("AdoptRec")
        self.assertIn(
            "0001-baseline",
            applied,
            f"After adoption apply(), '0001-baseline' must be in applied set; got {applied!r}",
        )

    def test_adoption_leaves_zero_pending(self):
        """After apply() on a pre-yoyo store, status() must report 0 pending.
        Only 0001-baseline exists this cycle; it was marked (not run), so there
        are no pending migrations.

        RED: adoption glue absent / status() absent.
        """
        data_home = os.path.join(self._tmpdir, "data_adopt_pend")
        self._make_pre_yoyo_store("AdoptPend", data_home)
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import migrate
        migrate.apply("AdoptPend")
        applied, pending = migrate.status("AdoptPend")
        self.assertEqual(
            len(pending),
            0,
            f"After adoption apply(), pending must be 0; got pending={pending!r}",
        )

    def test_adoption_preserves_four_tables(self):
        """The four tables must still exist and be structurally intact after
        adoption apply() — the marking must not drop or alter any table.

        RED: apply() absent OR adoption corrupts the schema.
        """
        data_home = os.path.join(self._tmpdir, "data_adopt_tables")
        db_path = self._make_pre_yoyo_store("AdoptTables", data_home)
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import migrate
        migrate.apply("AdoptTables")
        tables = _list_user_tables(db_path)
        for t in _FOUR_TABLES:
            self.assertIn(
                t,
                tables,
                f"Table '{t}' must still exist after adoption apply(); found: {tables!r}",
            )

    def test_adoption_yoyo_migration_table_now_exists(self):
        """After adoption apply(), the _yoyo_migration tracking table must exist
        (yoyo created it when recording 0001 as applied).

        RED: adoption glue absent → _yoyo_migration never created.
        """
        data_home = os.path.join(self._tmpdir, "data_adopt_yoyotbl")
        db_path = self._make_pre_yoyo_store("AdoptYoyoTbl", data_home)
        os.environ["XDG_DATA_HOME"] = data_home
        from sandesh import migrate
        migrate.apply("AdoptYoyoTbl")
        all_tables = _list_user_tables(db_path)
        # _yoyo_migration is a yoyo internal table; list_user_tables queries
        # sqlite_master for user tables — yoyo's tables ARE user-visible.
        # Also check via direct sqlite query for robustness.
        con = sqlite3.connect(db_path)
        yoyo_rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_yoyo_migration'"
        ).fetchall()
        con.close()
        self.assertEqual(
            len(yoyo_rows),
            1,
            f"_yoyo_migration table must exist after adoption apply(); "
            f"sqlite_master tables: {all_tables!r}",
        )

    def test_new_empty_store_runs_0001_normally(self):
        """Contrast case: a brand-new store (no tables at all) → apply() runs
        0001-baseline normally → four tables appear + 0001 applied.

        This confirms apply() distinguishes the pre-yoyo case (mark) from the
        fresh-store case (run).

        RED: apply() absent.
        """
        import os
        data_home = os.path.join(self._tmpdir, "data_new_store")
        os.environ["XDG_DATA_HOME"] = data_home

        # Fresh store: just create the directory; DO NOT call sandesh_db.setup()
        store_dir = os.path.join(data_home, "sandesh", "projects", "NewStore")
        os.makedirs(store_dir, exist_ok=True)

        from sandesh import migrate
        migrate.apply("NewStore")

        db_path = os.path.join(store_dir, "sandesh.db")
        tables = _list_user_tables(db_path)
        for t in _FOUR_TABLES:
            self.assertIn(
                t,
                tables,
                f"Table '{t}' must exist after apply() on a brand-new empty store; "
                f"found: {tables!r}",
            )

        applied, pending = migrate.status("NewStore")
        self.assertIn(
            "0001-baseline",
            applied,
            f"0001-baseline must be in applied after normal apply() on empty store; got {applied!r}",
        )
        self.assertEqual(
            len(pending),
            0,
            f"0 pending after apply() on empty store (only 0001); got {pending!r}",
        )


# ---------------------------------------------------------------------------
# Cycle 3 — AC5 + AC10: CLI apply / --status / --all / --project position
#
# All tests drive the CLI via subprocess using the repo .venv interpreter so
# that the full CLI parsing path is exercised (not just the Python API).
# $XDG_DATA_HOME is redirected to a per-test tmpdir to avoid touching real stores.
# ---------------------------------------------------------------------------

_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")


def _run_cli(argv, data_home, extra_env=None):
    """Run `python -m sandesh.cli <argv>` with XDG_DATA_HOME overridden.

    Returns the CompletedProcess (stdout + stderr captured as text).
    """
    env = {**os.environ, "XDG_DATA_HOME": data_home}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_VENV_PYTHON, "-m", "sandesh.cli"] + argv,
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
    )


def _setup_project(project_id, data_home):
    """Provision a project store via sandesh_db.setup() with XDG_DATA_HOME overridden."""
    import sys
    sys.path.insert(0, _REPO_ROOT)
    orig = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = data_home
    try:
        # Re-import to pick up the env change (or use direct call)
        from sandesh import sandesh_db
        sandesh_db.setup(project_id)
    finally:
        if orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = orig


def _status_api(project_id, data_home):
    """Call migrate.status(project_id) with XDG_DATA_HOME overridden.

    Returns (applied_ids, pending_ids).
    """
    orig = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = data_home
    try:
        from sandesh import migrate
        return migrate.status(project_id)
    finally:
        if orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = orig


# ---------------------------------------------------------------------------
# AC5 — apply (default action), idempotency, --status
# ---------------------------------------------------------------------------

class MigrateCliApplyTest(unittest.TestCase):
    """AC5 — apply (no flag): `sandesh migrate --project X` applies pending
    migrations; a second call is a no-op; exit 0 both times.

    RED: cmd_migrate() currently calls _require_deps() and returns 0 without
    dispatching to migrate.apply(), so the store stays un-migrated — the
    subsequent status() assertions fail because 0001-baseline is not applied.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_apply_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_apply_exits_zero_on_fresh_store(self):
        """First apply on a fresh (setup-provisioned) store exits 0.

        RED: cmd_migrate() returns 0 today, so this actually passes currently —
        the real RED is in the state-assertion tests below that check what
        apply() actually did.
        """
        data_home = os.path.join(self._tmpdir, "dh_apply_exit")
        _setup_project("ApplyExit", data_home)
        r = _run_cli(["--project", "ApplyExit", "migrate"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --project X must exit 0 on first apply.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_apply_records_0001_as_applied(self):
        """After `migrate --project X`, status() must report 0001-baseline applied.

        RED: cmd_migrate() does not call migrate.apply(), so the store stays
        un-migrated and status() returns applied=[] (or raises because _yoyo_migration
        doesn't exist); either way the assertion fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_apply_state")
        _setup_project("ApplyState", data_home)
        r = _run_cli(["--project", "ApplyState", "migrate"], data_home)
        self.assertEqual(r.returncode, 0,
                         f"migrate --project X must exit 0.\n"
                         f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        applied, pending = _status_api("ApplyState", data_home)
        self.assertIn(
            "0001-baseline", applied,
            f"After CLI apply, 0001-baseline must be in applied; got applied={applied!r}",
        )

    def test_apply_leaves_zero_pending(self):
        """After `migrate --project X`, status() must report 0 pending.

        RED: apply not dispatched → store un-migrated → pending=[0001-baseline].
        """
        data_home = os.path.join(self._tmpdir, "dh_apply_pending")
        _setup_project("ApplyPend", data_home)
        _run_cli(["--project", "ApplyPend", "migrate"], data_home)
        applied, pending = _status_api("ApplyPend", data_home)
        self.assertEqual(
            len(pending), 0,
            f"After CLI apply, pending must be 0; got pending={pending!r}",
        )

    def test_apply_idempotent_second_run_exits_zero(self):
        """A second `migrate --project X` must exit 0 (yoyo skips applied steps).

        RED: if apply isn't dispatched the first time the state is wrong, but
        the idempotency assertion catches the second run's side-effect too.
        """
        data_home = os.path.join(self._tmpdir, "dh_idempotent_exit")
        _setup_project("IdempotentExit", data_home)
        _run_cli(["--project", "IdempotentExit", "migrate"], data_home)
        r2 = _run_cli(["--project", "IdempotentExit", "migrate"], data_home)
        self.assertEqual(
            r2.returncode, 0,
            f"Second migrate --project X must exit 0 (idempotent).\n"
            f"stdout: {r2.stdout!r}\nstderr: {r2.stderr!r}",
        )

    def test_apply_idempotent_still_zero_pending_after_second_run(self):
        """After two applies, pending is still 0 and applied still contains 0001.

        RED: first apply not dispatched → state wrong → both assertions fail.
        """
        data_home = os.path.join(self._tmpdir, "dh_idempotent_state")
        _setup_project("IdempotentState", data_home)
        _run_cli(["--project", "IdempotentState", "migrate"], data_home)
        _run_cli(["--project", "IdempotentState", "migrate"], data_home)
        applied, pending = _status_api("IdempotentState", data_home)
        self.assertEqual(
            len(pending), 0,
            f"After 2 applies, pending must still be 0; got {pending!r}",
        )
        self.assertIn(
            "0001-baseline", applied,
            f"After 2 applies, 0001-baseline must still be applied; got {applied!r}",
        )
        # Exact count: exactly 1 applied (only 0001 exists this cycle)
        self.assertEqual(
            len(applied), 1,
            f"Exactly 1 migration applied (only 0001 this cycle); got {applied!r}",
        )


# ---------------------------------------------------------------------------
# AC5 — --status flag: report applied/pending, no writes
# ---------------------------------------------------------------------------

class MigrateCliStatusFlagTest(unittest.TestCase):
    """AC5 — `migrate --status --project X`: reports applied set and pending count;
    performs no writes (state is unchanged before and after the call).

    RED: --status flag is wired in the parser but cmd_migrate() doesn't check
    args.status — it just calls _require_deps() and returns 0 without printing
    anything, so the output assertions fail.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_status_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_status_flag_exits_zero(self):
        """--status --project X exits 0.

        RED: likely passes today (cmd_migrate returns 0), but the output tests below
        are the real gates.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_exit")
        _setup_project("StatusFlagExit", data_home)
        # First apply so there is something to report
        _run_cli(["--project", "StatusFlagExit", "migrate"], data_home)
        r = _run_cli(["--project", "StatusFlagExit", "migrate", "--status"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --status --project X must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_flag_output_mentions_0001_baseline(self):
        """--status output must name 0001-baseline as applied.

        RED: cmd_migrate() prints nothing today, so the assertIn fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_output")
        _setup_project("StatusFlagOutput", data_home)
        _run_cli(["--project", "StatusFlagOutput", "migrate"], data_home)
        r = _run_cli(["--project", "StatusFlagOutput", "migrate", "--status"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"--status output must mention '0001-baseline'.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_flag_output_mentions_zero_pending(self):
        """--status output must convey 0 pending (after a full apply).

        RED: no output from cmd_migrate() today.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_pending")
        _setup_project("StatusFlagPending", data_home)
        _run_cli(["--project", "StatusFlagPending", "migrate"], data_home)
        r = _run_cli(["--project", "StatusFlagPending", "migrate", "--status"], data_home)
        combined = r.stdout + r.stderr
        # Accept any reasonable phrasing: "0 pending", "pending: 0", "pending: []", etc.
        has_zero_pending = (
            "0 pending" in combined
            or "pending: 0" in combined
            or "pending: []" in combined
            or "pending=[]" in combined
            or ("pending" in combined and "0" in combined)
        )
        self.assertTrue(
            has_zero_pending,
            f"--status output must convey 0 pending migrations.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_flag_does_not_change_state(self):
        """--status must be read-only: calling it twice leaves state identical.

        Before --status: apply once (1 applied, 0 pending).
        After --status:  state must still be 1 applied, 0 pending.

        RED: cmd_migrate() doesn't call apply() so we can't even get to 1 applied
        via the CLI; the state assertions in the apply tests already catch this.
        For a standalone gate, we prime the store via the Python API directly,
        then call --status via CLI, then verify state unchanged via API.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_readonly")
        # Prime via Python API (bypasses CLI dispatch bug)
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup("StatusReadonly")
            migrate.apply("StatusReadonly")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        # State before --status
        applied_before, pending_before = _status_api("StatusReadonly", data_home)

        # Call --status via CLI
        _run_cli(["--project", "StatusReadonly", "migrate", "--status"], data_home)

        # State after --status — must be identical
        applied_after, pending_after = _status_api("StatusReadonly", data_home)
        self.assertEqual(
            applied_before, applied_after,
            f"--status must not change applied set; "
            f"before={applied_before!r}, after={applied_after!r}",
        )
        self.assertEqual(
            pending_before, pending_after,
            f"--status must not change pending set; "
            f"before={pending_before!r}, after={pending_after!r}",
        )

    def test_status_flag_on_fresh_store_shows_pending(self):
        """--status on a fresh (un-migrated) store must mention 0001-baseline as pending.

        RED: cmd_migrate() prints nothing; the pending mention is absent.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_fresh")
        _setup_project("StatusFresh", data_home)
        # Do NOT apply — store is un-migrated
        r = _run_cli(["--project", "StatusFresh", "migrate", "--status"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"--status on un-migrated store must mention '0001-baseline' as pending.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_flag_position_project_before_subcommand(self):
        """--project before `migrate` + --status works (SUPPRESS pattern).

        `sandesh --project X migrate --status` must exit 0 and produce status output.

        RED: --all is not wired yet, but this flag-position test exercises the
        parser's SUPPRESS parent; if migrate doesn't dispatch --status, no output.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_pos_before")
        # Prime store via API
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup("StatusPosBefore")
            migrate.apply("StatusPosBefore")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        r = _run_cli(["--project", "StatusPosBefore", "migrate", "--status"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --status --project X (project before subcommand) must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"Project-before-subcommand --status must output '0001-baseline'.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_flag_position_project_after_subcommand(self):
        """--project after `migrate` + --status works (SUPPRESS pattern).

        `sandesh migrate --status --project X` must exit 0 and produce status output.

        RED: as above — if dispatch is missing, no output → assertion fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_sf_pos_after")
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup("StatusPosAfter")
            migrate.apply("StatusPosAfter")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        r = _run_cli(["migrate", "--status", "--project", "StatusPosAfter"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --status --project X (project after subcommand) must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"Project-after-subcommand --status must output '0001-baseline'.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ---------------------------------------------------------------------------
# AC10 — --all: apply to / report on every project store under data home
# ---------------------------------------------------------------------------

class MigrateCliAllFlagTest(unittest.TestCase):
    """AC10 — `migrate --all` iterates every projects/<id>/sandesh.db under the
    data home and applies pending migrations to each.
    `migrate --status --all` reports each store's status.

    Store discovery: `sandesh_db.list_projects()` already iterates
    `projects/<id>/sandesh.db` under the data home root — `--all` should use
    the same mechanism.

    RED: `--all` is not a recognised argument in the migrate subparser (argparse
    will exit 2 with an unrecognised-argument error), so every test here fails
    at the exit-code assertion.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_all_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _prime_two_projects(self, data_home):
        """Set up two pre-yoyo project stores under data_home."""
        _setup_project("AllAlpha", data_home)
        _setup_project("AllBeta", data_home)

    def test_all_flag_recognised_by_parser_exits_zero(self):
        """--all must be a recognised argument (not an argparse unrecognised-arg error).

        RED: `--all` is not wired into the migrate subparser → argparse exits 2
        with 'unrecognised arguments: --all'.
        """
        data_home = os.path.join(self._tmpdir, "dh_all_recog")
        self._prime_two_projects(data_home)
        r = _run_cli(["migrate", "--all"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --all must exit 0 (recognised arg).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_all_apply_affects_first_project(self):
        """After `migrate --all`, the first project (AllAlpha) must have 0001-baseline applied.

        RED: --all unrecognised → argparse exits 2 before anything is applied.
        """
        data_home = os.path.join(self._tmpdir, "dh_all_alpha")
        self._prime_two_projects(data_home)
        r = _run_cli(["migrate", "--all"], data_home)
        self.assertEqual(r.returncode, 0,
                         f"migrate --all exit 0.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        applied, pending = _status_api("AllAlpha", data_home)
        self.assertIn(
            "0001-baseline", applied,
            f"AllAlpha: 0001-baseline must be applied after --all; applied={applied!r}",
        )
        self.assertEqual(
            len(pending), 0,
            f"AllAlpha: 0 pending after --all; pending={pending!r}",
        )

    def test_all_apply_affects_second_project(self):
        """After `migrate --all`, the second project (AllBeta) must also have 0001-baseline applied.

        RED: --all unrecognised → argparse exits 2; neither project is migrated.
        """
        data_home = os.path.join(self._tmpdir, "dh_all_beta")
        self._prime_two_projects(data_home)
        r = _run_cli(["migrate", "--all"], data_home)
        self.assertEqual(r.returncode, 0,
                         f"migrate --all exit 0.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        applied, pending = _status_api("AllBeta", data_home)
        self.assertIn(
            "0001-baseline", applied,
            f"AllBeta: 0001-baseline must be applied after --all; applied={applied!r}",
        )
        self.assertEqual(
            len(pending), 0,
            f"AllBeta: 0 pending after --all; pending={pending!r}",
        )

    def test_all_apply_zero_pending_both_projects(self):
        """After `migrate --all`, both stores report 0 pending (exact count check).

        RED: --all not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_all_both_pend")
        self._prime_two_projects(data_home)
        _run_cli(["migrate", "--all"], data_home)
        for pid in ("AllAlpha", "AllBeta"):
            applied, pending = _status_api(pid, data_home)
            self.assertEqual(
                len(pending), 0,
                f"{pid}: 0 pending expected after --all; got pending={pending!r}",
            )
            # Also confirm 0001-baseline is in the applied set (not just "no pending")
            self.assertIn(
                "0001-baseline", applied,
                f"{pid}: 0001-baseline must be applied; applied={applied!r}",
            )

    def test_all_apply_idempotent(self):
        """A second `migrate --all` is a no-op (exits 0, counts unchanged).

        RED: --all not wired → first run fails, second run also fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_all_idemp")
        self._prime_two_projects(data_home)
        _run_cli(["migrate", "--all"], data_home)
        r2 = _run_cli(["migrate", "--all"], data_home)
        self.assertEqual(
            r2.returncode, 0,
            f"Second migrate --all must exit 0 (idempotent).\n"
            f"stdout: {r2.stdout!r}\nstderr: {r2.stderr!r}",
        )
        for pid in ("AllAlpha", "AllBeta"):
            applied, pending = _status_api(pid, data_home)
            self.assertEqual(len(pending), 0,
                             f"{pid}: still 0 pending after 2nd --all; pending={pending!r}")
            self.assertEqual(len(applied), 1,
                             f"{pid}: exactly 1 applied after 2nd --all; applied={applied!r}")

    def test_status_all_exits_zero(self):
        """migrate --status --all exits 0.

        RED: --all not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_status_all_exit")
        self._prime_two_projects(data_home)
        # Prime via API so --status has something to report
        for pid in ("AllAlpha", "AllBeta"):
            orig = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = data_home
            try:
                from sandesh import migrate
                migrate.apply(pid)
            finally:
                if orig is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = orig
        r = _run_cli(["migrate", "--status", "--all"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --status --all must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_all_mentions_both_projects(self):
        """migrate --status --all output must mention both project ids.

        RED: --all not wired → argparse exits 2 with no output.
        """
        data_home = os.path.join(self._tmpdir, "dh_status_all_output")
        self._prime_two_projects(data_home)
        for pid in ("AllAlpha", "AllBeta"):
            orig = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = data_home
            try:
                from sandesh import migrate
                migrate.apply(pid)
            finally:
                if orig is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = orig
        r = _run_cli(["migrate", "--status", "--all"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "AllAlpha", combined,
            f"--status --all must mention 'AllAlpha' in output.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        self.assertIn(
            "AllBeta", combined,
            f"--status --all must mention 'AllBeta' in output.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_all_reports_zero_pending_for_both(self):
        """migrate --status --all output must convey 0 pending for each store.

        RED: --all not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_status_all_pend")
        self._prime_two_projects(data_home)
        for pid in ("AllAlpha", "AllBeta"):
            orig = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = data_home
            try:
                from sandesh import migrate
                migrate.apply(pid)
            finally:
                if orig is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = orig
        r = _run_cli(["migrate", "--status", "--all"], data_home)
        combined = r.stdout + r.stderr
        # The output must convey "0 pending" for each project — accept common phrasings
        has_zero_pending = (
            "0 pending" in combined
            or combined.count("pending: 0") >= 2
            or combined.count("pending: []") >= 2
        )
        self.assertTrue(
            has_zero_pending,
            f"--status --all must convey 0 pending for each store.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_status_all_does_not_change_state(self):
        """--status --all is read-only: state before and after must be identical.

        RED: --all not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_status_all_ro")
        self._prime_two_projects(data_home)
        for pid in ("AllAlpha", "AllBeta"):
            orig = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = data_home
            try:
                from sandesh import migrate
                migrate.apply(pid)
            finally:
                if orig is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = orig

        states_before = {pid: _status_api(pid, data_home) for pid in ("AllAlpha", "AllBeta")}
        _run_cli(["migrate", "--status", "--all"], data_home)
        states_after = {pid: _status_api(pid, data_home) for pid in ("AllAlpha", "AllBeta")}
        self.assertEqual(
            states_before, states_after,
            f"--status --all must not alter any store state.\n"
            f"before={states_before!r}\nafter={states_after!r}",
        )


# ---------------------------------------------------------------------------
# AC5 / §S4 — --project position regression: project before vs after subcommand
# ---------------------------------------------------------------------------

class MigrateCliProjectPositionTest(unittest.TestCase):
    """Regression: `--project` works whether placed before or after `migrate`
    (the SUPPRESS common-parent pattern documented in CLAUDE.md gotchas).

    RED: if cmd_migrate() doesn't dispatch on --status, the output assertions fail
    even though the position parsing itself may work.  This suite verifies BOTH
    the parsing AND the dispatch.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_pos_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _prime(self, project_id, data_home):
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup(project_id)
            migrate.apply(project_id)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

    def test_project_before_subcommand_apply_exits_zero(self):
        """sandesh --project X migrate (apply, project before subcommand) → exit 0.

        RED: parsing OK today, but state won't be updated → follow-on status
        assertions catch the real RED.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_before_apply")
        _setup_project("PosBefore", data_home)
        r = _run_cli(["--project", "PosBefore", "migrate"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"--project before subcommand apply must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_project_before_subcommand_apply_state(self):
        """sandesh --project X migrate → state: 0001-baseline applied, 0 pending.

        RED: cmd_migrate() doesn't dispatch apply → 0001 not applied → assertion fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_before_state")
        _setup_project("PosBeforeState", data_home)
        _run_cli(["--project", "PosBeforeState", "migrate"], data_home)
        applied, pending = _status_api("PosBeforeState", data_home)
        self.assertIn("0001-baseline", applied,
                      f"PosBeforeState: 0001-baseline applied; got {applied!r}")
        self.assertEqual(len(pending), 0,
                         f"PosBeforeState: 0 pending; got {pending!r}")

    def test_project_after_subcommand_apply_exits_zero(self):
        """sandesh migrate --project X (project after subcommand) → exit 0."""
        data_home = os.path.join(self._tmpdir, "dh_pos_after_apply")
        _setup_project("PosAfter", data_home)
        r = _run_cli(["migrate", "--project", "PosAfter"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"--project after subcommand apply must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_project_after_subcommand_apply_state(self):
        """sandesh migrate --project X → state: 0001-baseline applied, 0 pending.

        RED: cmd_migrate() doesn't dispatch apply.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_after_state")
        _setup_project("PosAfterState", data_home)
        _run_cli(["migrate", "--project", "PosAfterState"], data_home)
        applied, pending = _status_api("PosAfterState", data_home)
        self.assertIn("0001-baseline", applied,
                      f"PosAfterState: 0001-baseline applied; got {applied!r}")
        self.assertEqual(len(pending), 0,
                         f"PosAfterState: 0 pending; got {pending!r}")

    def test_project_before_subcommand_status_exits_zero(self):
        """sandesh --project X migrate --status → exit 0.

        RED: cmd_migrate() doesn't dispatch --status → possibly exits 0 but no output.
        The output test below is the real RED gate.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_before_status_exit")
        self._prime("PosBeforeStatus", data_home)
        r = _run_cli(["--project", "PosBeforeStatus", "migrate", "--status"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"--project before subcommand --status must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_project_before_subcommand_status_output(self):
        """sandesh --project X migrate --status → output contains 0001-baseline.

        RED: no dispatch → no output → assertion fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_before_status_out")
        self._prime("PosBeforeStatusOut", data_home)
        r = _run_cli(["--project", "PosBeforeStatusOut", "migrate", "--status"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"--project before subcommand --status must mention '0001-baseline'.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_project_after_subcommand_status_exits_zero(self):
        """sandesh migrate --status --project X → exit 0."""
        data_home = os.path.join(self._tmpdir, "dh_pos_after_status_exit")
        self._prime("PosAfterStatus", data_home)
        r = _run_cli(["migrate", "--status", "--project", "PosAfterStatus"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"--project after subcommand --status must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_project_after_subcommand_status_output(self):
        """sandesh migrate --status --project X → output contains 0001-baseline.

        RED: no dispatch → no output.
        """
        data_home = os.path.join(self._tmpdir, "dh_pos_after_status_out")
        self._prime("PosAfterStatusOut", data_home)
        r = _run_cli(["migrate", "--status", "--project", "PosAfterStatusOut"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"--project after subcommand --status must mention '0001-baseline'.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ---------------------------------------------------------------------------
# AC10 (addendum) — --all fail-fast + transactional per-migration rollback
#
# These tests were added in CR-SAN-017 Cycle 3 RED+ (after the failure policy
# was decided: transactional + fail-fast, per §S4 + AC10 updated 2026-06-09).
#
# Testability hook required by GREEN:
#   The transactional rollback test needs to point the engine at a CUSTOM
#   migrations dir (a temp dir containing a deliberately-failing migration).
#   Without this hook the only way to inject a bad migration is to mutate the
#   packaged sandesh/migrations/ dir — unacceptable in tests.
#
#   Required hook: `SANDESH_MIGRATIONS_DIR` environment variable.
#   When set, `migrate.migrations_dir()` returns its value instead of the
#   packaged dir, AND `apply(project_id)` / `status(project_id)` use it.
#   This follows the existing XDG_DATA_HOME / SANDESH_POLL_SECONDS env-override
#   pattern in the codebase.
#
#   Alternative: add an optional `migrations_dir` keyword argument to
#   `migrate.apply()` and `migrate.status()`.  Either form is acceptable;
#   the tests below use `SANDESH_MIGRATIONS_DIR` since it works through the
#   CLI subprocess path without modifying the Python API surface.
#
# Ordering assumption for fail-fast:
#   `sandesh_db.list_projects()` returns sorted project ids.  Tests that
#   depend on processing order use project ids where the failing store
#   sorts BEFORE the healthy store alphabetically (e.g. 'FailAlpha' before
#   'FailZeta') — so the implementation's natural iteration order triggers
#   fail-fast before touching the healthy store.
# ---------------------------------------------------------------------------


def _make_temp_migrations_dir(base_dir, migrations_sql):
    """Write a temporary migrations dir containing exactly the supplied SQL files.

    `migrations_sql` is a dict of {filename: sql_text}.
    Returns the path to the temp migrations dir.
    """
    import os
    mdir = os.path.join(base_dir, "migrations")
    os.makedirs(mdir, exist_ok=True)
    for fname, sql in migrations_sql.items():
        with open(os.path.join(mdir, fname), "w") as fh:
            fh.write(sql)
    return mdir


# ---------------------------------------------------------------------------
# 1. Fail-fast on --all
# ---------------------------------------------------------------------------

class MigrateCliAllFailFastTest(unittest.TestCase):
    """AC10 (fail-fast): when one store errors during --all, the whole run
    exits non-zero and stores ordered AFTER the failing one are left un-migrated.

    Failure injection: after setup, replace the failing store's sandesh.db
    with a garbage (non-SQLite) file so yoyo / sqlite3 raises when it tries
    to open it. The failing store ('FailAlpha') sorts before the healthy one
    ('FailZeta') — list_projects() returns sorted order — so the implementation
    processes FailAlpha first, hits the error, and must abort before touching
    FailZeta.

    RED: --all is not yet implemented (cmd_migrate() returns 0 without doing
    anything), so the fail-fast assertions fail:
      - the non-zero exit assertion fails (cmd_migrate returns 0)
      - the un-migrated assertion fails OR errors because --all isn't dispatched
    """

    _FAIL_PROJ = "FailAlpha"   # sorts first — will error
    _SAFE_PROJ = "FailZeta"    # sorts last — must be untouched after fail-fast

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_failfast_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _data_home(self):
        return os.path.join(self._tmpdir, "dh_failfast")

    def _db_path(self, project_id, data_home):
        return os.path.join(
            data_home, "sandesh", "projects", project_id, "sandesh.db"
        )

    def _prime_with_failure(self):
        """Set up two stores; corrupt FailAlpha's DB to force an error on apply."""
        data_home = self._data_home()
        # 1. Setup both stores (creates the directory layout and sandesh.db)
        _setup_project(self._FAIL_PROJ, data_home)
        _setup_project(self._SAFE_PROJ, data_home)

        # 2. Verify ordering — list_projects must return FailAlpha before FailZeta
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db
            projects = sandesh_db.list_projects()
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        fail_idx = projects.index(self._FAIL_PROJ) if self._FAIL_PROJ in projects else -1
        safe_idx = projects.index(self._SAFE_PROJ) if self._SAFE_PROJ in projects else -1
        self.assertIn(self._FAIL_PROJ, projects,
                      f"list_projects() did not include {self._FAIL_PROJ!r}: {projects!r}")
        self.assertIn(self._SAFE_PROJ, projects,
                      f"list_projects() did not include {self._SAFE_PROJ!r}: {projects!r}")
        self.assertLess(
            fail_idx, safe_idx,
            f"list_projects() must return {self._FAIL_PROJ!r} before {self._SAFE_PROJ!r} "
            f"(alphabetical); got order: {projects!r}",
        )

        # 3. Corrupt FailAlpha's DB (overwrite with garbage so sqlite3.connect raises)
        fail_db = self._db_path(self._FAIL_PROJ, data_home)
        self.assertTrue(os.path.isfile(fail_db),
                        f"Expected {fail_db!r} to exist after setup")
        with open(fail_db, "wb") as fh:
            fh.write(b"THIS IS NOT A SQLITE DATABASE -- garbage bytes to force an error")

        return data_home

    def test_all_exits_nonzero_when_store_errors(self):
        """--all must exit non-zero when any store fails to migrate.

        RED: cmd_migrate() returns 0 without dispatching --all, so no error
        is detected and the exit code is 0 — assertion fails.
        """
        data_home = self._prime_with_failure()
        r = _run_cli(["migrate", "--all"], data_home)
        self.assertNotEqual(
            r.returncode, 0,
            "migrate --all must exit non-zero when a store errors during apply; "
            f"got returncode=0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_all_failfast_leaves_subsequent_store_unmigrated(self):
        """After --all aborts on FailAlpha, FailZeta must be left un-migrated
        (0001-baseline pending, not applied).

        RED: cmd_migrate() returns 0 without dispatching --all; FailZeta's
        store is never touched by the CLI path, but the test independently
        checks FailZeta's state via the Python API and asserts it is un-migrated.
        Since --all is not dispatched at all, FailZeta stays un-migrated
        BUT the exit-code assertion in the sibling test already catches the real
        RED — this test additionally verifies the un-migrated invariant.
        """
        data_home = self._prime_with_failure()
        _run_cli(["migrate", "--all"], data_home)  # expected non-zero, but we check state

        # FailZeta was never migrated (either because --all aborted fail-fast,
        # or because --all was never dispatched). Either way it must be pending.
        applied, pending = _status_api(self._SAFE_PROJ, data_home)
        self.assertNotIn(
            "0001-baseline", applied,
            f"{self._SAFE_PROJ}: 0001-baseline must NOT be applied after a fail-fast "
            f"--all run (store after the failing one must be untouched); "
            f"got applied={applied!r}",
        )
        self.assertIn(
            "0001-baseline", pending,
            f"{self._SAFE_PROJ}: 0001-baseline must be PENDING after fail-fast --all; "
            f"got pending={pending!r}",
        )

    def test_all_failfast_mentions_failing_project_in_output(self):
        """--all error output must mention the failing project id so the user
        can diagnose which store failed.

        RED: cmd_migrate() returns 0 silently; no output mentions FailAlpha.
        """
        data_home = self._prime_with_failure()
        r = _run_cli(["migrate", "--all"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            self._FAIL_PROJ, combined,
            f"--all error output must mention the failing project ('{self._FAIL_PROJ}'); "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_all_failfast_corrupt_db_is_not_silently_skipped(self):
        """A store with a corrupt (non-SQLite) DB must cause --all to abort,
        not silently continue as if nothing happened.

        Verifies: exit non-zero AND FailZeta un-migrated in a single assertion
        pair (belt-and-suspenders for the central fail-fast invariant).

        RED: --all not dispatched → exit 0 (first assertion fails).
        """
        data_home = self._prime_with_failure()
        r = _run_cli(["migrate", "--all"], data_home)

        # The run must have failed
        self.assertNotEqual(
            r.returncode, 0,
            "migrate --all must not silently succeed when a store DB is corrupt.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

        # The store that came after the failure must still be un-migrated
        applied, pending = _status_api(self._SAFE_PROJ, data_home)
        self.assertEqual(
            len(applied), 0,
            f"After fail-fast, {self._SAFE_PROJ} must have 0 applied migrations "
            f"(it was skipped due to fail-fast); got applied={applied!r}",
        )


# ---------------------------------------------------------------------------
# 2. Transactional per-migration rollback (partial-failure leaves no artifact)
# ---------------------------------------------------------------------------

class MigrateTransactionalRollbackTest(unittest.TestCase):
    """AC10 (transactional): a migration that fails mid-way must leave the store
    at its PRIOR applied state — no partial tables or schema changes.

    Failure injection: a custom migrations dir (controlled via
    SANDESH_MIGRATIONS_DIR env var — see module docstring above) containing:
      - 0001-baseline.sql: the real baseline (so the store can be provisioned)
      - 0002-partial-fail.sql: a migration with a VALID first statement
        (CREATE TABLE good_tbl ...) followed by an INVALID second statement
        (deliberate SQL syntax error), all in a single migration file that
        yoyo treats atomically.

    After apply() fails on 0002, the store must NOT contain 'good_tbl' —
    the whole migration rolled back, store left at the 0001 state.

    Testability hook: tests use SANDESH_MIGRATIONS_DIR to point both the
    CLI and the Python API at the temp migrations dir.  GREEN must honour
    this env var in migrate.migrations_dir() (and therefore in apply/status).

    RED reasons:
      1. SANDESH_MIGRATIONS_DIR is not yet honoured → migrations_dir() returns
         the packaged dir → 0002-partial-fail is never seen → the test that
         asserts apply() raises / exits non-zero fails (it applies cleanly
         with only 0001 visible).
      2. Even if the env var were honoured, the transactional rollback is not
         yet tested — this is the first test covering it.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c3_txn_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")
        self._orig_mdir = os.environ.get("SANDESH_MIGRATIONS_DIR")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg
        if self._orig_mdir is None:
            os.environ.pop("SANDESH_MIGRATIONS_DIR", None)
        else:
            os.environ["SANDESH_MIGRATIONS_DIR"] = self._orig_mdir

    def _build_custom_migrations_dir(self):
        """Return a temp migrations dir with:
          - 0001-baseline.sql (copy of the real baseline SQL — needed to
            provision the store via the custom dir)
          - 0002-partial-fail.sql (valid CREATE TABLE followed by deliberate
            syntax error — yoyo must roll back the whole file atomically)
        """
        import shutil

        mdir = os.path.join(self._tmpdir, "custom_migrations")
        os.makedirs(mdir, exist_ok=True)

        # Copy the real 0001-baseline so the store can be bootstrapped.
        real_baseline = os.path.join(
            _REPO_ROOT, "sandesh", "migrations", "0001-baseline.sql"
        )
        shutil.copy2(real_baseline, os.path.join(mdir, "0001-baseline.sql"))

        # 0002-partial-fail: valid DDL followed by an invalid statement.
        # SQLite wraps this in a transaction via yoyo — the whole migration
        # must roll back, leaving 'good_tbl' absent.
        partial_fail_sql = (
            "-- 0002-partial-fail — deliberately fails mid-migration to test rollback\n"
            "CREATE TABLE good_tbl (id INTEGER PRIMARY KEY, val TEXT);\n"
            "THIS IS NOT VALID SQL AND SHOULD CAUSE A PARSE/EXEC ERROR;\n"
        )
        with open(os.path.join(mdir, "0002-partial-fail.sql"), "w") as fh:
            fh.write(partial_fail_sql)

        return mdir

    def _db_path_for(self, project_id, data_home):
        return os.path.join(
            data_home, "sandesh", "projects", project_id, "sandesh.db"
        )

    def _table_exists(self, db_path, table_name):
        import sqlite3
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchall()
        con.close()
        return len(rows) > 0

    def test_sandesh_migrations_dir_env_is_honoured(self):
        """SANDESH_MIGRATIONS_DIR must be honoured by migrate.migrations_dir().

        This is the testability-hook gate: if migrations_dir() ignores the env
        var, all downstream transactional tests are meaningless (they'd use the
        real packaged migrations, not the injected failing one).

        RED: migrations_dir() always returns the packaged dir regardless of
        SANDESH_MIGRATIONS_DIR → the assertion that the returned path matches
        the env var value fails.
        """
        custom_dir = os.path.join(self._tmpdir, "probe_dir")
        os.makedirs(custom_dir, exist_ok=True)

        os.environ["SANDESH_MIGRATIONS_DIR"] = custom_dir
        from sandesh import migrate
        returned = migrate.migrations_dir()
        self.assertEqual(
            returned, custom_dir,
            f"migrations_dir() must return SANDESH_MIGRATIONS_DIR={custom_dir!r} "
            f"when the env var is set; got {returned!r}.\n"
            "GREEN must honour SANDESH_MIGRATIONS_DIR in migrate.migrations_dir().",
        )

    def test_partial_fail_migration_exits_nonzero(self):
        """Applying a migration that fails mid-way must exit non-zero.

        Uses SANDESH_MIGRATIONS_DIR to point the CLI at the custom migrations
        dir (0001-baseline + 0002-partial-fail).  After 0001 is applied, the
        CLI attempts 0002 which fails → must exit non-zero.

        RED: SANDESH_MIGRATIONS_DIR not honoured → CLI uses packaged dir →
        only 0001 is visible → apply succeeds (exit 0) → assertion fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_txn_nonzero")
        _setup_project("TxnNonZero", data_home)

        custom_mdir = self._build_custom_migrations_dir()

        # Prime 0001 first (via real baseline to avoid applying through the custom dir
        # where 0001 also exists — apply 0001 then run again to attempt 0002)
        r_first = _run_cli(
            ["migrate", "--project", "TxnNonZero"],
            data_home,
            extra_env={"SANDESH_MIGRATIONS_DIR": custom_mdir},
        )
        # The first run applies 0001 (succeeds) then tries 0002 (fails)
        # OR just fails on 0002 immediately. Either way a non-zero exit is expected.
        # Note: if SANDESH_MIGRATIONS_DIR is not honoured, only 0001 runs → exit 0.
        self.assertNotEqual(
            r_first.returncode, 0,
            f"migrate with a partial-fail migration must exit non-zero; "
            f"got returncode={r_first.returncode}.\n"
            f"stdout: {r_first.stdout!r}\nstderr: {r_first.stderr!r}\n"
            "If SANDESH_MIGRATIONS_DIR is not being honoured, GREEN must implement it.",
        )

    def test_partial_fail_migration_good_tbl_is_absent(self):
        """After a failed 0002 apply, 'good_tbl' must NOT exist in the store.

        Rationale: if yoyo's per-migration transaction rolls back correctly,
        the CREATE TABLE good_tbl statement (which precedes the failing statement
        within the same migration file) must be undone.  The store is left at
        its prior state (0001 applied only, no good_tbl).

        RED: SANDESH_MIGRATIONS_DIR not honoured → only 0001 applies → good_tbl
        is never created anyway, but for the wrong reason.  The companion test
        (test_partial_fail_migration_exits_nonzero) is the real RED gate.
        This test adds the explicit absence assertion for the GREEN correctness check.
        """
        data_home = os.path.join(self._tmpdir, "dh_txn_absent")
        _setup_project("TxnAbsent", data_home)

        custom_mdir = self._build_custom_migrations_dir()
        db_path = self._db_path_for("TxnAbsent", data_home)

        # Attempt apply with the custom (failing) migrations dir
        _run_cli(
            ["migrate", "--project", "TxnAbsent"],
            data_home,
            extra_env={"SANDESH_MIGRATIONS_DIR": custom_mdir},
        )

        # good_tbl must NOT exist — the migration rolled back
        self.assertFalse(
            self._table_exists(db_path, "good_tbl"),
            f"'good_tbl' must NOT exist after a rolled-back migration; "
            f"if it does, the migration was only partially applied (not transactional).\n"
            f"DB path: {db_path!r}",
        )

    def test_partial_fail_migration_store_left_at_prior_state(self):
        """After 0002 fails and rolls back, the store must still show 0001 applied
        and 0002 as pending (i.e. not partially-applied and not corrupted).

        This tests the 'store left at prior applied state' invariant from AC10.

        RED: SANDESH_MIGRATIONS_DIR not honoured → only 0001 is visible → status
        shows 0001 applied + 0 pending (no 0002) → the assertIn('0002-partial-fail', pending)
        assertion fails, correctly flagging that the env var is not honoured.
        """
        data_home = os.path.join(self._tmpdir, "dh_txn_prior")
        _setup_project("TxnPrior", data_home)

        custom_mdir = self._build_custom_migrations_dir()

        # Attempt apply; expect it to fail on 0002
        _run_cli(
            ["migrate", "--project", "TxnPrior"],
            data_home,
            extra_env={"SANDESH_MIGRATIONS_DIR": custom_mdir},
        )

        # Check state via Python API with the same custom dir override
        orig_mdir = os.environ.get("SANDESH_MIGRATIONS_DIR")
        orig_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["SANDESH_MIGRATIONS_DIR"] = custom_mdir
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            applied, pending = migrate.status("TxnPrior")
        finally:
            if orig_mdir is None:
                os.environ.pop("SANDESH_MIGRATIONS_DIR", None)
            else:
                os.environ["SANDESH_MIGRATIONS_DIR"] = orig_mdir
            if orig_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig_xdg

        # 0001-baseline must still be applied (the prior state)
        self.assertIn(
            "0001-baseline", applied,
            f"After failed 0002, 0001-baseline must still be applied (prior state); "
            f"got applied={applied!r}",
        )
        # 0002-partial-fail must be PENDING (not recorded as applied, since it failed)
        self.assertIn(
            "0002-partial-fail", pending,
            f"After failed 0002, '0002-partial-fail' must be in pending (rolled back, "
            f"not applied); got pending={pending!r}.\n"
            "If SANDESH_MIGRATIONS_DIR is not honoured, pending shows only real migrations.",
        )
        # 0002 must NOT be in applied (it failed and rolled back)
        self.assertNotIn(
            "0002-partial-fail", applied,
            f"'0002-partial-fail' must NOT be in applied after it failed; "
            f"got applied={applied!r}",
        )

    def test_partial_fail_migration_four_core_tables_intact(self):
        """After a failed 0002, the four core tables must still be intact
        (the failure of 0002 must not damage the 0001 schema).

        RED: SANDESH_MIGRATIONS_DIR not honoured → test still passes vacuously
        (0001 was applied via packaged dir, core tables present). This test
        adds integrity insurance for the GREEN path.
        """
        data_home = os.path.join(self._tmpdir, "dh_txn_intact")
        _setup_project("TxnIntact", data_home)

        custom_mdir = self._build_custom_migrations_dir()
        db_path = self._db_path_for("TxnIntact", data_home)

        _run_cli(
            ["migrate", "--project", "TxnIntact"],
            data_home,
            extra_env={"SANDESH_MIGRATIONS_DIR": custom_mdir},
        )

        tables = _list_user_tables(db_path)
        for t in _FOUR_TABLES:
            self.assertIn(
                t, tables,
                f"Core table '{t}' must still exist after a failed 0002 migration; "
                f"found tables: {tables!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
