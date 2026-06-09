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


# ---------------------------------------------------------------------------
# Cycle 4 — AC8 (snapshot validity) + AC7 (--check: pending / drift / clean)
#
# §S3 defines the snapshot shape: a JSON file at sandesh/schema/current-schema.json
# whose top-level structure is:
#
#   {
#     "tables": {
#       "<table_name>": {
#         "columns": {
#           "<col_name>": {
#             "type": "<SQLite type string>",
#             "notnull": <0 or 1>,
#             "pk": <0 or 1>,
#             "default": <string or null>
#           }
#         }
#       }
#     }
#   }
#
# Column data is derived directly from PRAGMA table_info rows:
#   name → key in "columns"; type/notnull/pk/dflt_value → inner values.
#
# schema.meta.json is a JSON Schema (draft-07) that validates the above shape.
#
# current-schema.json represents the post-0001 (pre-0002) schema — four tables
# with message.status PRESENT.
#
# §S5 / AC7 — `migrate --check` checks two things:
#   1. pending — unapplied yoyo migrations → exit NON-ZERO, list them.
#   2. drift  — live DB shape ≠ current-schema.json → WARNING in output, exit ZERO.
#   Fully-migrated + no drift → exit ZERO, no warning.
#
# RED failure reasons:
#   - sandesh/schema/current-schema.json does not exist → FileNotFoundError in AC8 tests
#   - sandesh/schema/schema.meta.json does not exist → FileNotFoundError in AC8 tests
#   - --check is not a recognised argument in the migrate subparser → argparse exits 2
# ---------------------------------------------------------------------------

_SCHEMA_DIR = os.path.join(_REPO_ROOT, "sandesh", "schema")
_CURRENT_SCHEMA_PATH = os.path.join(_SCHEMA_DIR, "current-schema.json")
_META_SCHEMA_PATH = os.path.join(_SCHEMA_DIR, "schema.meta.json")


# ---------------------------------------------------------------------------
# AC8 — snapshot file existence + meta-schema validation
# ---------------------------------------------------------------------------

class MigrateSnapshotExistsTest(unittest.TestCase):
    """AC8 (partial — file existence): sandesh/schema/current-schema.json and
    sandesh/schema/schema.meta.json must exist as real files.

    RED: neither file exists yet (only .gitkeep in sandesh/schema/).
    """

    def test_current_schema_json_exists(self):
        """sandesh/schema/current-schema.json must exist as a file.

        RED: file absent — only .gitkeep in sandesh/schema/.
        """
        self.assertTrue(
            os.path.isfile(_CURRENT_SCHEMA_PATH),
            f"sandesh/schema/current-schema.json must exist; "
            f"not found at {_CURRENT_SCHEMA_PATH!r}.\n"
            "GREEN must create this file with the post-0001 schema snapshot.",
        )

    def test_meta_schema_json_exists(self):
        """sandesh/schema/schema.meta.json must exist as a file.

        RED: file absent.
        """
        self.assertTrue(
            os.path.isfile(_META_SCHEMA_PATH),
            f"sandesh/schema/schema.meta.json must exist; "
            f"not found at {_META_SCHEMA_PATH!r}.\n"
            "GREEN must create this JSON Schema meta-schema file.",
        )

    def test_current_schema_is_valid_json(self):
        """current-schema.json must be parseable as JSON (no syntax errors).

        RED: file absent → open() raises FileNotFoundError.
        """
        import json
        try:
            with open(_CURRENT_SCHEMA_PATH) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.fail(
                f"current-schema.json not found at {_CURRENT_SCHEMA_PATH!r}; "
                "GREEN must create it."
            )
        except json.JSONDecodeError as exc:
            self.fail(
                f"current-schema.json is not valid JSON: {exc}\n"
                f"Path: {_CURRENT_SCHEMA_PATH!r}"
            )
        self.assertIsInstance(data, dict, "current-schema.json must be a JSON object (dict)")

    def test_meta_schema_is_valid_json(self):
        """schema.meta.json must be parseable as JSON.

        RED: file absent → FileNotFoundError.
        """
        import json
        try:
            with open(_META_SCHEMA_PATH) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.fail(
                f"schema.meta.json not found at {_META_SCHEMA_PATH!r}; "
                "GREEN must create it."
            )
        except json.JSONDecodeError as exc:
            self.fail(
                f"schema.meta.json is not valid JSON: {exc}\n"
                f"Path: {_META_SCHEMA_PATH!r}"
            )
        self.assertIsInstance(data, dict, "schema.meta.json must be a JSON object (dict)")


class MigrateSnapshotStructureTest(unittest.TestCase):
    """AC8 — current-schema.json has the expected top-level structure.

    The snapshot format is:
      {"tables": {"<name>": {"columns": {"<col>": {"type": ..., "notnull": ...,
                                                    "pk": ..., "default": ...}}}}}

    Tests in this class check structure only (not specific column values);
    the column-content tests in MigrateSnapshotContentTest go deeper.

    RED: current-schema.json absent → FileNotFoundError on load.
    """

    @classmethod
    def setUpClass(cls):
        import json
        cls._schema_path = _CURRENT_SCHEMA_PATH
        if not os.path.isfile(cls._schema_path):
            # Skip all structure tests cleanly — the existence tests above
            # already capture the RED; we don't want noisy AttributeErrors here.
            raise unittest.SkipTest(
                f"current-schema.json not found at {cls._schema_path!r}; "
                "skipping structure tests (existence test already RED)."
            )
        with open(cls._schema_path) as fh:
            cls.snapshot = json.load(fh)

    def test_snapshot_has_tables_key(self):
        """current-schema.json must have a top-level 'tables' key.

        RED: file absent (SkipTest above); or file present but structure wrong.
        """
        self.assertIn(
            "tables", self.snapshot,
            f"current-schema.json must have a top-level 'tables' key; "
            f"keys found: {list(self.snapshot.keys())!r}",
        )

    def test_snapshot_tables_is_dict(self):
        """current-schema.json['tables'] must be a dict (table_name → column_map).

        RED: file absent (SkipTest); or tables not a dict.
        """
        tables = self.snapshot.get("tables", None)
        self.assertIsInstance(
            tables, dict,
            f"current-schema.json['tables'] must be a dict; got {type(tables)!r}",
        )

    def test_snapshot_contains_all_four_tables(self):
        """current-schema.json must include all four core tables:
        address, message, message_recipient, notifier.

        RED: file absent (SkipTest); or tables missing.
        """
        tables = self.snapshot.get("tables", {})
        for tbl in ("address", "message", "message_recipient", "notifier"):
            self.assertIn(
                tbl, tables,
                f"current-schema.json must include table '{tbl}'; "
                f"tables found: {list(tables.keys())!r}",
            )

    def test_snapshot_each_table_has_columns_key(self):
        """Each table entry in current-schema.json must have a 'columns' key.

        RED: file absent (SkipTest); or columns key missing.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            self.assertIn(
                "columns", tbl_data,
                f"Table '{tbl_name}' in current-schema.json must have a 'columns' key; "
                f"keys found: {list(tbl_data.keys())!r}",
            )

    def test_snapshot_columns_are_dicts(self):
        """Each column entry must be a dict (not a list or scalar).

        RED: file absent (SkipTest); or wrong column representation.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            columns = tbl_data.get("columns", {})
            self.assertIsInstance(
                columns, dict,
                f"Table '{tbl_name}'.columns must be a dict; got {type(columns)!r}",
            )
            for col_name, col_data in columns.items():
                self.assertIsInstance(
                    col_data, dict,
                    f"Table '{tbl_name}' column '{col_name}' must be a dict; "
                    f"got {type(col_data)!r}",
                )

    def test_snapshot_column_entries_have_required_keys(self):
        """Each column dict must have the four required keys: type, notnull, pk, default.

        RED: file absent (SkipTest); or missing keys.
        """
        required_keys = {"type", "notnull", "pk", "default"}
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            for col_name, col_data in tbl_data.get("columns", {}).items():
                for k in required_keys:
                    self.assertIn(
                        k, col_data,
                        f"Column '{tbl_name}.{col_name}' must have key '{k}'; "
                        f"keys found: {list(col_data.keys())!r}",
                    )

    def test_snapshot_column_type_is_string(self):
        """The 'type' field in each column dict must be a string.

        RED: file absent (SkipTest); or type field wrong shape.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            for col_name, col_data in tbl_data.get("columns", {}).items():
                self.assertIsInstance(
                    col_data.get("type"), str,
                    f"Column '{tbl_name}.{col_name}'.type must be a string; "
                    f"got {type(col_data.get('type'))!r}",
                )

    def test_snapshot_column_notnull_is_integer(self):
        """The 'notnull' field in each column dict must be an int (0 or 1).

        RED: file absent (SkipTest); or notnull field wrong shape.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            for col_name, col_data in tbl_data.get("columns", {}).items():
                val = col_data.get("notnull")
                self.assertIsInstance(
                    val, int,
                    f"Column '{tbl_name}.{col_name}'.notnull must be an int (0/1); "
                    f"got {type(val)!r}: {val!r}",
                )

    def test_snapshot_column_pk_is_integer(self):
        """The 'pk' field in each column dict must be an int (0 or 1).

        RED: file absent (SkipTest); or pk field wrong shape.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            for col_name, col_data in tbl_data.get("columns", {}).items():
                val = col_data.get("pk")
                self.assertIsInstance(
                    val, int,
                    f"Column '{tbl_name}.{col_name}'.pk must be an int (0/1); "
                    f"got {type(val)!r}: {val!r}",
                )

    def test_snapshot_column_default_is_string_or_null(self):
        """The 'default' field in each column dict must be a string or null/None.

        RED: file absent (SkipTest); or default field wrong type.
        """
        tables = self.snapshot.get("tables", {})
        for tbl_name, tbl_data in tables.items():
            for col_name, col_data in tbl_data.get("columns", {}).items():
                val = col_data.get("default")
                self.assertTrue(
                    val is None or isinstance(val, str),
                    f"Column '{tbl_name}.{col_name}'.default must be a string or null; "
                    f"got {type(val)!r}: {val!r}",
                )


class MigrateSnapshotContentTest(unittest.TestCase):
    """AC8 — current-schema.json content: the post-0001 snapshot must contain
    the columns defined by sandesh_db._SCHEMA.

    Spot-checks on specific columns that are load-bearing for --check:
      - message.status must be PRESENT (this is pre-0002)
      - address.address must be the primary key (pk=1)
      - message.id must be pk=1 and NOT NULL
      - message_recipient PK columns: message_id + recipient
      - notifier.tombstone must be NOT NULL (notnull=1)

    RED: current-schema.json absent (SkipTest); or columns missing/wrong.
    """

    @classmethod
    def setUpClass(cls):
        import json
        if not os.path.isfile(_CURRENT_SCHEMA_PATH):
            raise unittest.SkipTest(
                f"current-schema.json not found; skipping content tests."
            )
        with open(_CURRENT_SCHEMA_PATH) as fh:
            cls.snapshot = json.load(fh)
        cls.tables = cls.snapshot.get("tables", {})

    def _col(self, table, column):
        """Return the column dict for table.column, or None if missing."""
        return self.tables.get(table, {}).get("columns", {}).get(column)

    def test_message_status_column_present_pre_0002(self):
        """message.status must be present in the snapshot (this is the pre-0002 snapshot).

        RED: file absent (SkipTest); or status column missing from snapshot.
        """
        col = self._col("message", "status")
        self.assertIsNotNone(
            col,
            "current-schema.json must include message.status (pre-0002 snapshot; "
            "0002 drops it in a later cycle).\n"
            f"message columns: {list(self.tables.get('message', {}).get('columns', {}).keys())!r}",
        )

    def test_message_status_is_not_null(self):
        """message.status has notnull=1 (NOT NULL DEFAULT 'open' in _SCHEMA).

        RED: file absent (SkipTest); or notnull wrong.
        """
        col = self._col("message", "status")
        if col is None:
            self.skipTest("message.status absent — covered by test_message_status_column_present_pre_0002")
        self.assertEqual(
            col.get("notnull"), 1,
            f"message.status must have notnull=1; got {col!r}",
        )

    def test_address_address_is_primary_key(self):
        """address.address must have pk=1 (TEXT PRIMARY KEY in _SCHEMA).

        RED: file absent (SkipTest); or pk wrong.
        """
        col = self._col("address", "address")
        self.assertIsNotNone(
            col,
            "current-schema.json must include address.address column; "
            f"address columns: {list(self.tables.get('address', {}).get('columns', {}).keys())!r}",
        )
        self.assertEqual(
            col.get("pk"), 1,
            f"address.address must have pk=1 (PRIMARY KEY); got {col!r}",
        )

    def test_message_id_is_primary_key(self):
        """message.id must have pk=1 (INTEGER PRIMARY KEY AUTOINCREMENT in _SCHEMA).

        RED: file absent (SkipTest); or pk wrong.
        """
        col = self._col("message", "id")
        self.assertIsNotNone(
            col,
            "current-schema.json must include message.id column.",
        )
        self.assertEqual(
            col.get("pk"), 1,
            f"message.id must have pk=1; got {col!r}",
        )

    def test_notifier_tombstone_is_not_null(self):
        """notifier.tombstone must have notnull=1 (BOOLEAN NOT NULL DEFAULT FALSE in _SCHEMA).

        RED: file absent (SkipTest); or notnull wrong.
        """
        col = self._col("notifier", "tombstone")
        self.assertIsNotNone(
            col,
            "current-schema.json must include notifier.tombstone column.",
        )
        self.assertEqual(
            col.get("notnull"), 1,
            f"notifier.tombstone must have notnull=1; got {col!r}",
        )

    def test_message_recipient_has_message_id_column(self):
        """message_recipient.message_id must be present in the snapshot.

        RED: file absent (SkipTest); or column missing.
        """
        col = self._col("message_recipient", "message_id")
        self.assertIsNotNone(
            col,
            "current-schema.json must include message_recipient.message_id; "
            f"columns: {list(self.tables.get('message_recipient', {}).get('columns', {}).keys())!r}",
        )

    def test_message_recipient_has_recipient_column(self):
        """message_recipient.recipient must be present in the snapshot.

        RED: file absent (SkipTest); or column missing.
        """
        col = self._col("message_recipient", "recipient")
        self.assertIsNotNone(
            col,
            "current-schema.json must include message_recipient.recipient; "
            f"columns: {list(self.tables.get('message_recipient', {}).get('columns', {}).keys())!r}",
        )

    def test_exactly_four_tables_in_snapshot(self):
        """The snapshot must describe exactly 4 tables (pre-0002).

        RED: file absent (SkipTest); or wrong table count.
        """
        self.assertEqual(
            len(self.tables), 4,
            f"current-schema.json must have exactly 4 tables; "
            f"found {len(self.tables)}: {list(self.tables.keys())!r}",
        )


class MigrateMetaSchemaValidatesSnapshotTest(unittest.TestCase):
    """AC8 — the snapshot validates against the meta-schema.

    Loads both files and calls jsonschema.validate(instance=snapshot, schema=meta).
    If it raises ValidationError, the test fails.

    This is the core AC8 assertion. The companion tests above verify file
    existence; this test verifies the meta-schema is internally consistent and
    current-schema.json satisfies it.

    RED: either file absent → FileNotFoundError (or SkipTest from setUpClass);
         or meta-schema doesn't accept the snapshot → jsonschema.ValidationError.
    """

    @classmethod
    def setUpClass(cls):
        import json
        missing = []
        if not os.path.isfile(_CURRENT_SCHEMA_PATH):
            missing.append("current-schema.json")
        if not os.path.isfile(_META_SCHEMA_PATH):
            missing.append("schema.meta.json")
        if missing:
            raise unittest.SkipTest(
                f"Skipping meta-schema validation — file(s) absent: {missing!r}"
            )
        with open(_CURRENT_SCHEMA_PATH) as fh:
            cls.snapshot = json.load(fh)
        with open(_META_SCHEMA_PATH) as fh:
            cls.meta_schema = json.load(fh)

    def test_snapshot_validates_against_meta_schema(self):
        """jsonschema.validate(current-schema.json, schema.meta.json) must not raise.

        Uses jsonschema from the venv's [migrate] extra.

        RED: either file absent (SkipTest); or the snapshot doesn't satisfy the
        meta-schema (jsonschema.ValidationError).
        """
        try:
            import jsonschema
        except ImportError:
            self.skipTest(
                "jsonschema not installed — run under the [migrate] venv interpreter"
            )
        try:
            jsonschema.validate(instance=self.snapshot, schema=self.meta_schema)
        except jsonschema.ValidationError as exc:
            self.fail(
                f"current-schema.json does NOT validate against schema.meta.json:\n"
                f"  ValidationError: {exc.message}\n"
                f"  Path: {list(exc.absolute_path)}\n"
                f"  Schema path: {list(exc.absolute_schema_path)}"
            )
        except jsonschema.SchemaError as exc:
            self.fail(
                f"schema.meta.json itself is not a valid JSON Schema:\n"
                f"  SchemaError: {exc.message}"
            )

    def test_meta_schema_rejects_empty_object(self):
        """The meta-schema must reject an empty {} (not a valid snapshot).

        This guards that the meta-schema is actually constraining, not trivially
        accepting everything.

        RED: file absent (SkipTest); or meta-schema is too permissive.
        """
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with self.assertRaises(
            jsonschema.ValidationError,
            msg="schema.meta.json must reject {} (an empty object is not a valid snapshot)",
        ):
            jsonschema.validate(instance={}, schema=self.meta_schema)

    def test_meta_schema_rejects_snapshot_without_tables_key(self):
        """The meta-schema must reject a snapshot that lacks the 'tables' key.

        RED: file absent (SkipTest); or meta-schema too permissive.
        """
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with self.assertRaises(
            jsonschema.ValidationError,
            msg="schema.meta.json must reject a snapshot with no 'tables' key",
        ):
            jsonschema.validate(
                instance={"not_tables": {}},
                schema=self.meta_schema,
            )

    def test_meta_schema_rejects_column_with_missing_type(self):
        """The meta-schema must reject a column entry that lacks the 'type' field.

        RED: file absent (SkipTest); or meta-schema too permissive.
        """
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        bad_snapshot = {
            "tables": {
                "some_table": {
                    "columns": {
                        "some_col": {
                            # 'type' deliberately absent
                            "notnull": 0,
                            "pk": 0,
                            "default": None,
                        }
                    }
                }
            }
        }
        with self.assertRaises(
            jsonschema.ValidationError,
            msg="schema.meta.json must reject a column missing the 'type' field",
        ):
            jsonschema.validate(instance=bad_snapshot, schema=self.meta_schema)


# ---------------------------------------------------------------------------
# AC7 — `migrate --check` CLI: pending=non-zero, clean=zero, drift=warning+zero
# ---------------------------------------------------------------------------

class MigrateCheckCliInterfaceTest(unittest.TestCase):
    """Structural gate: `--check` must be a recognised argument in the migrate
    subparser and `migrate.check(project_id)` must be a callable on the module.

    RED: `--check` is not in the migrate subparser → argparse exits 2 with an
    'unrecognised arguments: --check' error.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c4_check_iface_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")
        # Set up a minimal project store so --check has something to work with
        data_home = os.path.join(self._tmpdir, "dh_iface")
        _setup_project("CheckIface", data_home)
        self._data_home = data_home

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_check_flag_recognised_by_parser(self):
        """--check must not cause an 'unrecognised arguments' argparse error (exit 2).

        RED: --check is not wired into the migrate subparser → argparse exits 2.
        """
        r = _run_cli(["migrate", "--check", "--project", "CheckIface"], self._data_home)
        self.assertNotEqual(
            r.returncode, 2,
            f"migrate --check must not exit 2 (argparse unrecognised-argument error).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}\n"
            "GREEN must add --check to the migrate subparser.",
        )

    def test_check_callable_exists_on_module(self):
        """sandesh.migrate.check must be a callable (takes project_id).

        RED: attribute absent on the module.
        """
        from sandesh import migrate
        self.assertTrue(
            callable(getattr(migrate, "check", None)),
            "sandesh.migrate.check must be a callable; attribute is absent or not callable.\n"
            "GREEN must add a check(project_id) function to migrate.py.",
        )


class MigrateCheckPendingExitsNonZeroTest(unittest.TestCase):
    """AC7 — pending store: `migrate --check --project X` exits NON-ZERO and
    names the pending migration(s) in its output.

    A 'pending' store is one where yoyo has not yet been applied — e.g. a store
    provisioned by sandesh_db.setup() (four tables, no _yoyo_migration).

    RED: --check not recognised → argparse exits 2; OR --check not dispatched
    correctly → exits 0 (wrong).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c4_pending_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_unmigrated_store(self, project_id, data_home):
        """Provision a store via sandesh_db.setup() — tables exist, no _yoyo_migration."""
        _setup_project(project_id, data_home)

    def test_check_on_pending_store_exits_nonzero(self):
        """--check on a store with pending migrations must exit NON-ZERO.

        The store is provisioned by sandesh_db.setup() (tables present, 0001-baseline
        NOT recorded in _yoyo_migration) → 0001-baseline is pending.

        RED: --check not wired → argparse exits 2 (also non-zero, but wrong reason);
             OR if wired but incorrectly → might exit 0.
        """
        data_home = os.path.join(self._tmpdir, "dh_pending_exit")
        self._make_unmigrated_store("CheckPendingExit", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckPendingExit"], data_home)
        self.assertNotEqual(
            r.returncode, 0,
            f"migrate --check must exit NON-ZERO when there are pending migrations; "
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_pending_store_output_names_pending_migration(self):
        """--check on a pending store must name the pending migration(s) in its output.

        0001-baseline is the pending migration in this cycle.

        RED: --check not wired → argparse error output (no migration names);
             OR wired but output doesn't name the migration.
        """
        data_home = os.path.join(self._tmpdir, "dh_pending_output")
        self._make_unmigrated_store("CheckPendingOutput", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckPendingOutput"], data_home)
        combined = r.stdout + r.stderr
        self.assertIn(
            "0001-baseline", combined,
            f"--check output must name the pending migration '0001-baseline'; "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_pending_store_is_nondestructive(self):
        """--check must be read-only: the store must still be un-migrated after --check.

        RED: --check not wired → store state unchanged anyway (argparse exits early).
        This test is the GREEN correctness guard.
        """
        data_home = os.path.join(self._tmpdir, "dh_pending_ro")
        self._make_unmigrated_store("CheckPendingRO", data_home)
        # Run --check
        _run_cli(["migrate", "--check", "--project", "CheckPendingRO"], data_home)
        # The store must still be un-migrated (0001-baseline still pending)
        applied, pending = _status_api("CheckPendingRO", data_home)
        self.assertNotIn(
            "0001-baseline", applied,
            f"--check must not apply migrations; 0001-baseline must remain NOT applied; "
            f"got applied={applied!r}",
        )
        self.assertIn(
            "0001-baseline", pending,
            f"After --check (read-only), 0001-baseline must still be pending; "
            f"got pending={pending!r}",
        )

    def test_check_on_pending_store_project_before_subcommand(self):
        """--project before `migrate --check` (SUPPRESS pattern) must also exit non-zero.

        sandesh --project X migrate --check → non-zero (pending).

        RED: --check not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_pending_pos")
        self._make_unmigrated_store("CheckPendingPos", data_home)
        # --project before migrate
        r = _run_cli(["--project", "CheckPendingPos", "migrate", "--check"], data_home)
        self.assertNotEqual(
            r.returncode, 0,
            f"--project before subcommand: migrate --check must exit non-zero on pending store; "
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


class MigrateCheckCleanExitsZeroTest(unittest.TestCase):
    """AC7 — fully-migrated store whose live shape MATCHES current-schema.json:
    `migrate --check --project X` must exit ZERO (no pending, no drift).

    RED: --check not wired → argparse exits 2 (non-zero for wrong reason);
         OR if wired: might not exit 0 on a clean store.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c4_clean_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_fully_migrated_store(self, project_id, data_home):
        """Provision via sandesh_db.setup() then apply migrations → fully migrated."""
        _setup_project(project_id, data_home)
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply(project_id)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

    def test_check_on_fully_migrated_store_exits_zero(self):
        """--check on a fully-migrated store whose live shape matches the snapshot
        must exit ZERO.

        If current-schema.json is absent, --check may exit non-zero for the right
        reason (can't load snapshot) — but the test itself will fail because we
        assert exit 0. This is intentional: if the snapshot doesn't exist, GREEN
        is not complete.

        RED: --check not wired → argparse exits 2 (non-zero).
        """
        data_home = os.path.join(self._tmpdir, "dh_clean_exit")
        self._make_fully_migrated_store("CheckCleanExit", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckCleanExit"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --check must exit 0 on a fully-migrated store with no drift; "
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_fully_migrated_store_no_pending_in_output(self):
        """--check output must NOT indicate any pending migrations on a clean store.

        RED: --check not wired → no meaningful output.
        """
        data_home = os.path.join(self._tmpdir, "dh_clean_output")
        self._make_fully_migrated_store("CheckCleanOutput", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckCleanOutput"], data_home)
        combined = r.stdout + r.stderr
        # Must not warn about pending migrations
        self.assertNotIn(
            "pending", combined.lower(),
            f"--check output must not mention 'pending' on a fully-migrated clean store; "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_fully_migrated_store_no_drift_warning_in_output(self):
        """--check output must NOT contain a drift warning on a shape-matching store.

        RED: --check not wired → no output → this passes vacuously, but the
        exit-code test above is the real RED gate.
        """
        data_home = os.path.join(self._tmpdir, "dh_clean_nodrift")
        self._make_fully_migrated_store("CheckCleanNoDrift", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckCleanNoDrift"], data_home)
        combined = r.stdout + r.stderr
        self.assertNotIn(
            "drift", combined.lower(),
            f"--check output must not mention 'drift' on a clean matching store; "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_is_read_only_on_clean_store(self):
        """--check must not change the migration state of a fully-migrated store.

        RED: --check not wired → state unchanged (vacuously OK), exit-code test fails.
        """
        data_home = os.path.join(self._tmpdir, "dh_clean_ro")
        self._make_fully_migrated_store("CheckCleanRO", data_home)
        # State before
        applied_before, pending_before = _status_api("CheckCleanRO", data_home)
        # Run --check
        _run_cli(["migrate", "--check", "--project", "CheckCleanRO"], data_home)
        # State after — must be identical
        applied_after, pending_after = _status_api("CheckCleanRO", data_home)
        self.assertEqual(
            applied_before, applied_after,
            f"--check must not change applied set; before={applied_before!r}, after={applied_after!r}",
        )
        self.assertEqual(
            pending_before, pending_after,
            f"--check must not change pending set; before={pending_before!r}, after={pending_after!r}",
        )


class MigrateCheckDriftWarningTest(unittest.TestCase):
    """AC7 — drift: when the live DB shape differs from current-schema.json,
    `migrate --check` must print a WARNING naming the drift but exit ZERO.

    This is the user-decided strictness from the gap-analysis:
      pending = error (non-zero); drift = warning (non-fatal, exit zero).

    Drift injection: after fully migrating a store, add an extra column to
    `message` via raw SQL (ALTER TABLE message ADD COLUMN extra_col TEXT).
    The live shape now differs from the snapshot → drift.

    RED: --check not wired → argparse exits 2; OR wired but exits non-zero on
    drift (wrong strictness) / doesn't print a warning.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c4_drift_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _db_path(self, project_id, data_home):
        return os.path.join(
            data_home, "sandesh", "projects", project_id, "sandesh.db"
        )

    def _make_drifted_store(self, project_id, data_home):
        """Fully migrate a store, then add an extra column to introduce drift."""
        _setup_project(project_id, data_home)
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply(project_id)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig
        # Inject drift: add an extra column that is NOT in current-schema.json
        import sqlite3
        db_path = self._db_path(project_id, data_home)
        con = sqlite3.connect(db_path)
        con.execute("ALTER TABLE message ADD COLUMN extra_drift_col TEXT")
        con.commit()
        con.close()
        return db_path

    def test_check_on_drifted_store_exits_zero(self):
        """--check on a drifted store must exit ZERO (drift is a warning, not an error).

        User-decided strictness: drift = warning (exit 0). Only pending = error.

        RED: --check not wired → argparse exits 2 (non-zero);
             OR wired but treats drift as error → exits non-zero (wrong).
        """
        data_home = os.path.join(self._tmpdir, "dh_drift_exit")
        self._make_drifted_store("CheckDriftExit", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckDriftExit"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"migrate --check must exit 0 when there is drift (drift = warning, not error); "
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}\n"
            "Per the gap-analysis decision: pending=error (non-zero), drift=warning (zero).",
        )

    def test_check_on_drifted_store_prints_drift_warning(self):
        """--check on a drifted store must print a WARNING message naming the drift.

        The output must contain the word 'drift' or 'warning' (case-insensitive)
        AND must name the drifted table ('message') or the extra column
        ('extra_drift_col').

        RED: --check not wired → no output; OR wired but no warning emitted.
        """
        data_home = os.path.join(self._tmpdir, "dh_drift_warn")
        self._make_drifted_store("CheckDriftWarn", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckDriftWarn"], data_home)
        combined = r.stdout + r.stderr
        has_warning_keyword = (
            "drift" in combined.lower()
            or "warning" in combined.lower()
            or "warn" in combined.lower()
        )
        self.assertTrue(
            has_warning_keyword,
            f"--check must print a drift warning (containing 'drift' or 'warning'); "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_drifted_store_names_the_drift(self):
        """--check drift warning must name the drifting table or column.

        The injected drift is 'extra_drift_col' on table 'message'.
        The output must name at least one of: 'message', 'extra_drift_col'.

        RED: --check not wired → no output; OR wired but drift details absent.
        """
        data_home = os.path.join(self._tmpdir, "dh_drift_name")
        self._make_drifted_store("CheckDriftName", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckDriftName"], data_home)
        combined = r.stdout + r.stderr
        names_drift = (
            "extra_drift_col" in combined
            or "message" in combined
        )
        self.assertTrue(
            names_drift,
            f"--check drift output must name the drifted table/column "
            f"(expected 'extra_drift_col' or 'message'); "
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_check_on_drifted_store_is_read_only(self):
        """--check on a drifted store must not alter the migration state.

        The extra column introduced as drift must still be there after --check
        (no ALTER TABLE / DROP to 'fix' the drift — --check is read-only).

        RED: --check not wired → state unchanged (vacuously); exit-code test is real RED.
        """
        data_home = os.path.join(self._tmpdir, "dh_drift_ro")
        db_path = self._make_drifted_store("CheckDriftRO", data_home)
        # --check must not modify the DB
        _run_cli(["migrate", "--check", "--project", "CheckDriftRO"], data_home)
        # The extra column must still exist (--check is read-only)
        import sqlite3
        con = sqlite3.connect(db_path)
        cols = [row[1] for row in con.execute("PRAGMA table_info(message)").fetchall()]
        con.close()
        self.assertIn(
            "extra_drift_col", cols,
            f"--check must be read-only; 'extra_drift_col' must still exist after --check; "
            f"found columns: {cols!r}",
        )

    def test_check_drift_does_not_affect_pending_check(self):
        """Drift does not make --check exit non-zero; only pending does.

        Contrast: a drifted + fully-migrated store → exit 0 (drift is warning).
        A pending store → exit non-zero.

        This test asserts the combination: fully-migrated + drifted → still exit 0.

        RED: --check not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_drift_combo")
        self._make_drifted_store("CheckDriftCombo", data_home)
        r = _run_cli(["migrate", "--check", "--project", "CheckDriftCombo"], data_home)
        self.assertEqual(
            r.returncode, 0,
            f"Drifted + fully-migrated store: --check must exit 0 (drift is not an error); "
            f"got returncode={r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ---------------------------------------------------------------------------
# Cycle 5 — AC8 (remaining half: --dump-schema) + --diff mechanics (AC9 machinery)
#
# §S6 defines two read-only authoring aids:
#
#   --dump-schema
#       Emits the live DB shape as JSON to stdout in the §S3 snapshot format:
#         {"tables": {"<table>": {"columns": {"<col>": {"type":…,"notnull":…,
#                                                        "pk":…,"default":…}}}}}
#       The emitted JSON must EQUAL current-schema.json (modulo key ordering)
#       when run against a fully-migrated store.  Read-only; exit 0.
#
#   --diff <old-snapshot-file> [--json]
#       Compares the OLD snapshot file against the freshly-dumped CURRENT live
#       shape of the given project store.  Reports:
#         added   — column present in current but absent in old
#         removed — column present in old but absent in current
#         changed — column present in both but with different type/notnull/pk/default
#       With --json, the output is machine-parseable JSON.
#       Read-only; exit 0 (it is a reporting aid, not a gate).
#
# --diff interface decision:
#   `--diff <old-snapshot-file>` REQUIRES `--project` to identify the live store
#   to dump for comparison.  Rationale: AC9 says "the current dump" (live DB),
#   and the gap-analysis confirms §S6 spec intent ("freshly-dumped current state
#   of store X").  Tests are written for the live-store interpretation.
#
#   INTERFACE-FLAG for orchestrator: the `--diff` flag takes ONE positional value
#   (the path to the old snapshot JSON file) and optionally `--json`.  It needs
#   `--project` to know which live store to compare against.
#
# RED failure reasons:
#   - `--dump-schema` is not a recognised argument → argparse exits 2
#   - `--diff` is not a recognised argument → argparse exits 2
#   - Even if wired, functions not implemented → assertion failures on output
# ---------------------------------------------------------------------------

import json as _json


def _make_fully_migrated_store(project_id, data_home):
    """Provision a fresh project store and apply all migrations.

    Returns the absolute db_path.
    """
    orig = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = data_home
    try:
        from sandesh import sandesh_db, migrate
        sandesh_db.setup(project_id)
        migrate.apply(project_id)
        store = sandesh_db.store_dir(project_id)
        return os.path.join(store, sandesh_db.DB_FILE)
    finally:
        if orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = orig


# ---------------------------------------------------------------------------
# AC8 (remaining half) — --dump-schema: emits JSON equal to current-schema.json
# ---------------------------------------------------------------------------

class MigrateDumpSchemaTest(unittest.TestCase):
    """AC8 (--dump-schema): `migrate --dump-schema --project X` on a fully-migrated
    store emits JSON to stdout that equals the committed current-schema.json (modulo
    key ordering).  Exit 0.  Read-only (store state unchanged).

    RED: --dump-schema is not wired in the CLI migrate subparser → argparse exits 2
    with an unrecognised-argument error.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_dump_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_dump_schema_exits_zero(self):
        """--dump-schema on a fully-migrated store must exit 0.

        RED: --dump-schema not wired → argparse exits 2 (unrecognised argument).
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_exit")
        _make_fully_migrated_store("DumpExit", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpExit"],
            data_home,
        )
        self.assertEqual(
            r.returncode,
            0,
            f"migrate --dump-schema must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_dump_schema_stdout_is_valid_json(self):
        """--dump-schema stdout must be parseable as JSON.

        RED: --dump-schema not wired → no JSON output (argparse error on stderr).
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_json")
        _make_fully_migrated_store("DumpJson", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpJson"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"migrate --dump-schema must exit 0 before JSON check.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        try:
            parsed = _json.loads(r.stdout)
        except _json.JSONDecodeError as exc:
            self.fail(
                f"--dump-schema stdout is not valid JSON: {exc}\n"
                f"stdout: {r.stdout!r}",
            )
        self.assertIsInstance(
            parsed,
            dict,
            f"--dump-schema stdout must be a JSON object; got {type(parsed)!r}",
        )

    def test_dump_schema_has_tables_key(self):
        """--dump-schema output JSON must have a top-level 'tables' key.

        RED: --dump-schema not wired → no output.
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_tables_key")
        _make_fully_migrated_store("DumpTablesKey", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpTablesKey"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        self.assertIn(
            "tables",
            parsed,
            f"--dump-schema output must have a top-level 'tables' key; got keys: {list(parsed.keys())!r}",
        )

    def test_dump_schema_equals_current_schema_json(self):
        """--dump-schema output, parsed as dict, must equal current-schema.json parsed as dict.

        Comparison is key-order-independent (both parsed as Python dicts).

        RED: --dump-schema not wired → argparse error; no JSON to compare.
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_equals")
        _make_fully_migrated_store("DumpEquals", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpEquals"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        dumped = _json.loads(r.stdout)

        with open(_CURRENT_SCHEMA_PATH) as fh:
            committed = _json.load(fh)

        self.assertEqual(
            dumped,
            committed,
            f"--dump-schema output must equal current-schema.json (modulo key ordering).\n"
            f"DIFF — keys in dumped but not committed: "
            f"{set(str(k) for k in dumped.get('tables', {}).keys()) - set(str(k) for k in committed.get('tables', {}).keys())}\n"
            f"keys in committed but not dumped: "
            f"{set(str(k) for k in committed.get('tables', {}).keys()) - set(str(k) for k in dumped.get('tables', {}).keys())}",
        )

    def test_dump_schema_contains_all_four_tables(self):
        """--dump-schema output must contain all four core tables.

        RED: --dump-schema not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_four_tables")
        _make_fully_migrated_store("DumpFourTables", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpFourTables"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        tables = parsed.get("tables", {})
        for t in _FOUR_TABLES:
            self.assertIn(
                t,
                tables,
                f"--dump-schema output must include table '{t}'; "
                f"found tables: {list(tables.keys())!r}",
            )

    def test_dump_schema_message_has_status_column_pre_0002(self):
        """In this cycle (before 0002), --dump-schema must include message.status.

        RED: --dump-schema not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_status_pre0002")
        _make_fully_migrated_store("DumpStatusPre0002", data_home)
        r = _run_cli(
            ["migrate", "--dump-schema", "--project", "DumpStatusPre0002"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        msg_cols = parsed.get("tables", {}).get("message", {}).get("columns", {})
        self.assertIn(
            "status",
            msg_cols,
            f"--dump-schema must include message.status in this pre-0002 cycle; "
            f"message columns: {list(msg_cols.keys())!r}",
        )

    def test_dump_schema_is_read_only(self):
        """--dump-schema must not alter migration state (applied/pending unchanged).

        RED: --dump-schema not wired (trivially read-only when not wired, but we
        assert the state is unchanged relative to the post-apply baseline).
        """
        data_home = os.path.join(self._tmpdir, "dh_dump_readonly")
        _make_fully_migrated_store("DumpReadOnly", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            applied_before, pending_before = migrate.status("DumpReadOnly")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        _run_cli(["migrate", "--dump-schema", "--project", "DumpReadOnly"], data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            applied_after, pending_after = migrate.status("DumpReadOnly")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        self.assertEqual(
            applied_before,
            applied_after,
            f"--dump-schema must not alter applied set; "
            f"before={applied_before!r}, after={applied_after!r}",
        )
        self.assertEqual(
            pending_before,
            pending_after,
            f"--dump-schema must not alter pending set; "
            f"before={pending_before!r}, after={pending_after!r}",
        )


# ---------------------------------------------------------------------------
# --diff mechanics — AC9 machinery tested with synthetic snapshot fixtures
#
# Fixture format (same as current-schema.json):
#   {"tables": {"<table>": {"columns": {"<col>": {"type":…,"notnull":…,
#                                                  "pk":…,"default":…}}}}}
#
# Diff report format (JSON with --json flag):
#   {
#     "added":   [{"table": "<t>", "column": "<c>", "current": {…}}],
#     "removed": [{"table": "<t>", "column": "<c>", "old": {…}}],
#     "changed": [{"table": "<t>", "column": "<c>", "old": {…}, "current": {…}}]
#   }
#
# RED failure reasons:
#   - --diff not wired → argparse exits 2 (unrecognised argument)
#   - even if wired, diff logic not implemented → assertion failures on the
#     JSON output structure / content
# ---------------------------------------------------------------------------

def _write_snapshot_fixture(path, tables_dict):
    """Write a snapshot fixture file to `path`.

    ``tables_dict`` must be in the §S3 format:
      {table_name: {col_name: {type, notnull, pk, default}}}
    The function wraps it into the full snapshot structure
      {"tables": {table: {"columns": {col: {…}}}}}
    and writes it as JSON.
    """
    snapshot = {
        "tables": {
            table: {"columns": cols}
            for table, cols in tables_dict.items()
        }
    }
    with open(path, "w") as fh:
        _json.dump(snapshot, fh, indent=2)


def _current_snapshot_as_tables_dict():
    """Load current-schema.json and return its tables as a flat tables_dict.

    Returns: {table_name: {col_name: {type, notnull, pk, default}}}
    This is convenient for building synthetic fixtures derived from the
    current committed snapshot.
    """
    with open(_CURRENT_SCHEMA_PATH) as fh:
        data = _json.load(fh)
    tables_dict = {}
    for table, table_data in data.get("tables", {}).items():
        tables_dict[table] = dict(table_data.get("columns", {}))
    return tables_dict


class MigrateDiffStructuralTest(unittest.TestCase):
    """Structural gate: --diff is accepted by the CLI migrate subparser.

    RED: --diff not wired → argparse exits 2 with 'unrecognised arguments'.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_struct_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _write_current_snapshot_fixture(self):
        """Write a fixture identical to current-schema.json to a temp file.
        Returns the path.
        """
        fixture_path = os.path.join(self._tmpdir, "old_snapshot.json")
        import shutil
        shutil.copy2(_CURRENT_SCHEMA_PATH, fixture_path)
        return fixture_path

    def test_diff_flag_accepted_by_cli(self):
        """migrate --diff <file> --project X must be accepted (not exit 2).

        RED: --diff not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_diff_accept")
        _make_fully_migrated_store("DiffAccept", data_home)
        fixture_path = self._write_current_snapshot_fixture()

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--project", "DiffAccept"],
            data_home,
        )
        # Exit code 0 or any non-2 value is acceptable; argparse exits 2 for
        # unrecognised args. The real gate is returncode != 2.
        self.assertNotEqual(
            r.returncode,
            2,
            f"migrate --diff must be accepted by the CLI (not exit 2 for unrecognised arg).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_diff_json_flag_accepted_by_cli(self):
        """migrate --diff <file> --json --project X must be accepted (not exit 2).

        RED: --diff/--json not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_diff_json_accept")
        _make_fully_migrated_store("DiffJsonAccept", data_home)
        fixture_path = self._write_current_snapshot_fixture()

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffJsonAccept"],
            data_home,
        )
        self.assertNotEqual(
            r.returncode,
            2,
            f"migrate --diff --json must be accepted by the CLI (not exit 2).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


class MigrateDiffNoDifferenceTest(unittest.TestCase):
    """--diff against an identical snapshot (old == current) must report no differences.

    When old-snapshot = current-schema.json and the live store is also at that
    state, the diff must report: added=[], removed=[], changed=[].

    RED: --diff not wired → argparse exits 2.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_nodiff_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _run_diff_json(self, project_id, data_home, old_snapshot_path):
        """Run migrate --diff <old> --json --project X; return the CompletedProcess."""
        return _run_cli(
            ["migrate", "--diff", old_snapshot_path, "--json", "--project", project_id],
            data_home,
        )

    def test_diff_no_difference_exits_zero(self):
        """--diff with identical old and current must exit 0.

        RED: --diff not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_no_diff_exit")
        _make_fully_migrated_store("NoDiffExit", data_home)
        fixture_path = os.path.join(self._tmpdir, "same_snapshot.json")
        import shutil
        shutil.copy2(_CURRENT_SCHEMA_PATH, fixture_path)

        r = self._run_diff_json("NoDiffExit", data_home, fixture_path)
        self.assertEqual(
            r.returncode,
            0,
            f"--diff against identical snapshot must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_diff_no_difference_json_has_empty_lists(self):
        """--diff --json against identical old/current must return added=[], removed=[], changed=[].

        RED: --diff not wired → no JSON output.
        """
        data_home = os.path.join(self._tmpdir, "dh_no_diff_json")
        _make_fully_migrated_store("NoDiffJson", data_home)
        fixture_path = os.path.join(self._tmpdir, "same_snapshot2.json")
        import shutil
        shutil.copy2(_CURRENT_SCHEMA_PATH, fixture_path)

        r = self._run_diff_json("NoDiffJson", data_home, fixture_path)
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        self.assertEqual(
            parsed.get("added", "MISSING"),
            [],
            f"No added columns expected (identical snapshots); got: {parsed.get('added')!r}",
        )
        self.assertEqual(
            parsed.get("removed", "MISSING"),
            [],
            f"No removed columns expected (identical snapshots); got: {parsed.get('removed')!r}",
        )
        self.assertEqual(
            parsed.get("changed", "MISSING"),
            [],
            f"No changed columns expected (identical snapshots); got: {parsed.get('changed')!r}",
        )


class MigrateDiffRemovedColumnTest(unittest.TestCase):
    """--diff: a column present in OLD but absent in current → reported as 'removed'.

    Fixture: old_snapshot has message.legacy_flag (a synthetic column NOT in the
    live DB).  The live DB has no legacy_flag → diff should report it removed.

    This tests the machinery AC9 uses: in Cycle 6, message.status will be the
    'removed' column when diffing the pre-0002 snapshot against the post-0002 dump.

    RED: --diff not wired → argparse exits 2.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_rm_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_old_snapshot_with_extra_col(self, fixture_path):
        """Write an old snapshot that includes message.legacy_flag (extra vs current live)."""
        tables_dict = _current_snapshot_as_tables_dict()
        # Add a column that doesn't exist in the live DB
        tables_dict["message"]["legacy_flag"] = {
            "type": "INTEGER",
            "notnull": 0,
            "pk": 0,
            "default": None,
        }
        _write_snapshot_fixture(fixture_path, tables_dict)

    def test_removed_col_diff_exits_zero(self):
        """--diff with a removed column must exit 0 (diff is a reporting aid, not a gate).

        RED: --diff not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_rm_exit")
        _make_fully_migrated_store("DiffRmExit", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_rm.json")
        self._make_old_snapshot_with_extra_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffRmExit"],
            data_home,
        )
        self.assertEqual(
            r.returncode,
            0,
            f"--diff with removed column must exit 0 (reporting aid).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_removed_col_appears_in_json_removed_list(self):
        """message.legacy_flag (in old, absent in current) must appear in JSON 'removed' list.

        RED: --diff not wired → no JSON output.
        """
        data_home = os.path.join(self._tmpdir, "dh_rm_json")
        _make_fully_migrated_store("DiffRmJson", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_rm2.json")
        self._make_old_snapshot_with_extra_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffRmJson"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        removed = parsed.get("removed", [])
        removed_cols = [
            (e.get("table"), e.get("column"))
            for e in removed
            if isinstance(e, dict)
        ]
        self.assertIn(
            ("message", "legacy_flag"),
            removed_cols,
            f"message.legacy_flag (in old, absent in current) must appear in 'removed'.\n"
            f"removed list: {removed!r}\nfull JSON: {parsed!r}",
        )

    def test_removed_col_not_in_added_or_changed(self):
        """message.legacy_flag (removed) must NOT appear in 'added' or 'changed'.

        RED: --diff not wired → no JSON output.
        """
        data_home = os.path.join(self._tmpdir, "dh_rm_not_in_other")
        _make_fully_migrated_store("DiffRmNotOther", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_rm3.json")
        self._make_old_snapshot_with_extra_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffRmNotOther"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        added_cols = [
            (e.get("table"), e.get("column"))
            for e in parsed.get("added", [])
            if isinstance(e, dict)
        ]
        changed_cols = [
            (e.get("table"), e.get("column"))
            for e in parsed.get("changed", [])
            if isinstance(e, dict)
        ]
        self.assertNotIn(
            ("message", "legacy_flag"),
            added_cols,
            f"message.legacy_flag must NOT appear in 'added' (it was removed); "
            f"added: {added_cols!r}",
        )
        self.assertNotIn(
            ("message", "legacy_flag"),
            changed_cols,
            f"message.legacy_flag must NOT appear in 'changed' (it was removed); "
            f"changed: {changed_cols!r}",
        )

    def test_removed_col_added_list_is_empty(self):
        """When old has one extra column vs current, 'added' list must be empty.

        The live store is the same as current-schema.json; the only difference
        is the old snapshot has one extra column.  Nothing was added to current.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_rm_added_empty")
        _make_fully_migrated_store("DiffRmAddedEmpty", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_rm4.json")
        self._make_old_snapshot_with_extra_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffRmAddedEmpty"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        self.assertEqual(
            parsed.get("added", "MISSING"),
            [],
            f"'added' must be empty when only old has an extra col; got: {parsed.get('added')!r}",
        )


class MigrateDiffAddedColumnTest(unittest.TestCase):
    """--diff: a column present in current but absent in OLD → reported as 'added'.

    Fixture: old_snapshot is missing notifier.host (present in the live DB).
    The diff must report notifier.host as 'added'.

    This models the case where a migration added a column that wasn't in the
    old snapshot — the developer sees what's new.

    RED: --diff not wired → argparse exits 2.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_add_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_old_snapshot_missing_col(self, fixture_path):
        """Write an old snapshot that OMITS notifier.host (present in live DB)."""
        tables_dict = _current_snapshot_as_tables_dict()
        # Remove notifier.host from the old snapshot
        tables_dict["notifier"].pop("host", None)
        _write_snapshot_fixture(fixture_path, tables_dict)

    def test_added_col_appears_in_json_added_list(self):
        """notifier.host (absent in old, present in current) must appear in 'added'.

        RED: --diff not wired → argparse exits 2, no JSON.
        """
        data_home = os.path.join(self._tmpdir, "dh_add_json")
        _make_fully_migrated_store("DiffAddJson", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_add.json")
        self._make_old_snapshot_missing_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffAddJson"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        parsed = _json.loads(r.stdout)
        added = parsed.get("added", [])
        added_cols = [
            (e.get("table"), e.get("column"))
            for e in added
            if isinstance(e, dict)
        ]
        self.assertIn(
            ("notifier", "host"),
            added_cols,
            f"notifier.host (absent in old, present in current) must appear in 'added'.\n"
            f"added list: {added!r}\nfull JSON: {parsed!r}",
        )

    def test_added_col_entry_contains_current_descriptor(self):
        """The 'added' entry for notifier.host must include the current column descriptor.

        The entry shape must be at minimum {"table": "notifier", "column": "host",
        "current": {…column descriptor…}}.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_add_descriptor")
        _make_fully_migrated_store("DiffAddDesc", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_add2.json")
        self._make_old_snapshot_missing_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffAddDesc"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        added = parsed.get("added", [])
        host_entries = [
            e for e in added
            if isinstance(e, dict)
            and e.get("table") == "notifier"
            and e.get("column") == "host"
        ]
        self.assertEqual(
            len(host_entries),
            1,
            f"Exactly 1 'added' entry for (notifier, host); got {len(host_entries)}: {added!r}",
        )
        entry = host_entries[0]
        self.assertIn(
            "current",
            entry,
            f"'added' entry must have a 'current' key with the column descriptor; got {entry!r}",
        )
        current_desc = entry["current"]
        self.assertIsInstance(
            current_desc,
            dict,
            f"'current' in added entry must be a dict; got {type(current_desc)!r}",
        )
        # Must contain the standard column descriptor keys
        for key in ("type", "notnull", "pk", "default"):
            self.assertIn(
                key,
                current_desc,
                f"'current' descriptor must contain '{key}'; got keys: {list(current_desc.keys())!r}",
            )

    def test_added_col_removed_list_is_empty(self):
        """When old is missing one column vs current, 'removed' list must be empty.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_add_removed_empty")
        _make_fully_migrated_store("DiffAddRmEmpty", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_add3.json")
        self._make_old_snapshot_missing_col(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffAddRmEmpty"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        self.assertEqual(
            parsed.get("removed", "MISSING"),
            [],
            f"'removed' must be empty when only old is missing a col; got: {parsed.get('removed')!r}",
        )


class MigrateDiffChangedColumnTest(unittest.TestCase):
    """--diff: a column present in both old and current but with different descriptor
    → reported as 'changed'.

    Fixture: old_snapshot has message.subject with notnull=0 (the live DB has
    notnull=1, matching current-schema.json).  Diff must report message.subject changed.

    Also tests a type change: old has notifier.pid as TEXT (live has INTEGER).

    RED: --diff not wired → argparse exits 2.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_chg_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _make_old_snapshot_notnull_change(self, fixture_path):
        """Old snapshot: message.subject has notnull=0 (live has notnull=1)."""
        tables_dict = _current_snapshot_as_tables_dict()
        # Change notnull on message.subject
        tables_dict["message"]["subject"] = dict(
            tables_dict["message"]["subject"]
        )
        tables_dict["message"]["subject"]["notnull"] = 0
        _write_snapshot_fixture(fixture_path, tables_dict)

    def _make_old_snapshot_type_change(self, fixture_path):
        """Old snapshot: notifier.pid has type='TEXT' (live has type='INTEGER')."""
        tables_dict = _current_snapshot_as_tables_dict()
        tables_dict["notifier"]["pid"] = dict(tables_dict["notifier"]["pid"])
        tables_dict["notifier"]["pid"]["type"] = "TEXT"
        _write_snapshot_fixture(fixture_path, tables_dict)

    def test_changed_col_notnull_appears_in_changed_list(self):
        """message.subject with different notnull must appear in JSON 'changed'.

        RED: --diff not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_chg_notnull")
        _make_fully_migrated_store("DiffChgNotnull", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_chg_notnull.json")
        self._make_old_snapshot_notnull_change(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffChgNotnull"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        parsed = _json.loads(r.stdout)
        changed = parsed.get("changed", [])
        changed_cols = [
            (e.get("table"), e.get("column"))
            for e in changed
            if isinstance(e, dict)
        ]
        self.assertIn(
            ("message", "subject"),
            changed_cols,
            f"message.subject (notnull differs: old=0, current=1) must appear in 'changed'.\n"
            f"changed list: {changed!r}\nfull JSON: {parsed!r}",
        )

    def test_changed_col_entry_has_old_and_current(self):
        """'changed' entry must include both 'old' and 'current' column descriptors.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_chg_entry")
        _make_fully_migrated_store("DiffChgEntry", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_chg_entry.json")
        self._make_old_snapshot_notnull_change(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffChgEntry"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        changed = parsed.get("changed", [])
        subject_entries = [
            e for e in changed
            if isinstance(e, dict)
            and e.get("table") == "message"
            and e.get("column") == "subject"
        ]
        self.assertEqual(
            len(subject_entries),
            1,
            f"Exactly 1 'changed' entry for (message, subject); got {len(subject_entries)}: {changed!r}",
        )
        entry = subject_entries[0]
        self.assertIn(
            "old",
            entry,
            f"'changed' entry must have 'old' key; got keys: {list(entry.keys())!r}",
        )
        self.assertIn(
            "current",
            entry,
            f"'changed' entry must have 'current' key; got keys: {list(entry.keys())!r}",
        )
        # The old descriptor has notnull=0; current has notnull=1
        self.assertEqual(
            entry["old"].get("notnull"),
            0,
            f"'old' descriptor for message.subject must have notnull=0; got {entry['old']!r}",
        )
        self.assertEqual(
            entry["current"].get("notnull"),
            1,
            f"'current' descriptor for message.subject must have notnull=1; got {entry['current']!r}",
        )

    def test_changed_col_type_diff_appears_in_changed_list(self):
        """notifier.pid with different type (old=TEXT vs current=INTEGER) must appear in 'changed'.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_chg_type")
        _make_fully_migrated_store("DiffChgType", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_chg_type.json")
        self._make_old_snapshot_type_change(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffChgType"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        parsed = _json.loads(r.stdout)
        changed = parsed.get("changed", [])
        changed_cols = [
            (e.get("table"), e.get("column"))
            for e in changed
            if isinstance(e, dict)
        ]
        self.assertIn(
            ("notifier", "pid"),
            changed_cols,
            f"notifier.pid (type: old=TEXT, current=INTEGER) must appear in 'changed'.\n"
            f"changed list: {changed!r}\nfull JSON: {parsed!r}",
        )

    def test_changed_col_others_unchanged(self):
        """When only one column has a notnull difference, no other column appears in 'changed'.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_chg_only_one")
        _make_fully_migrated_store("DiffChgOnlyOne", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_chg_only.json")
        self._make_old_snapshot_notnull_change(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffChgOnlyOne"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        changed = parsed.get("changed", [])
        changed_cols = [
            (e.get("table"), e.get("column"))
            for e in changed
            if isinstance(e, dict)
        ]
        # Only (message, subject) should appear; no other column should show as changed
        self.assertEqual(
            len(changed_cols),
            1,
            f"Only (message, subject) should be in 'changed' (one notnull diff); "
            f"got {len(changed_cols)}: {changed_cols!r}",
        )
        self.assertEqual(
            changed_cols[0],
            ("message", "subject"),
            f"The single 'changed' entry must be (message, subject); got {changed_cols[0]!r}",
        )


class MigrateDiffReadOnlyTest(unittest.TestCase):
    """--diff must be read-only: store state (applied/pending) unchanged after call.

    RED: --diff not wired → argparse exits 2 (trivially read-only; but the exit-code
    assertion in the structural test is the real RED here).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_ro_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_diff_is_read_only(self):
        """--diff must not alter migration state.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_diff_ro")
        _make_fully_migrated_store("DiffReadOnly", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            applied_before, pending_before = migrate.status("DiffReadOnly")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        # Write an old snapshot with one difference to exercise the diff path
        fixture_path = os.path.join(self._tmpdir, "old_ro.json")
        tables_dict = _current_snapshot_as_tables_dict()
        tables_dict["message"]["legacy_flag_ro"] = {
            "type": "INTEGER",
            "notnull": 0,
            "pk": 0,
            "default": None,
        }
        _write_snapshot_fixture(fixture_path, tables_dict)

        _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "DiffReadOnly"],
            data_home,
        )

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            applied_after, pending_after = migrate.status("DiffReadOnly")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        self.assertEqual(
            applied_before,
            applied_after,
            f"--diff must not alter applied set; "
            f"before={applied_before!r}, after={applied_after!r}",
        )
        self.assertEqual(
            pending_before,
            pending_after,
            f"--diff must not alter pending set; "
            f"before={pending_before!r}, after={pending_after!r}",
        )


class MigrateDiffHumanReadableTest(unittest.TestCase):
    """--diff without --json must produce human-readable text output (not JSON).

    The text output must mention the differing table/column names, giving the
    developer enough context to hand-write the next migration step.

    RED: --diff not wired → argparse exits 2.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c5_diff_human_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_diff_human_output_mentions_removed_column(self):
        """Without --json, removed column name must appear in text output.

        RED: --diff not wired.
        """
        data_home = os.path.join(self._tmpdir, "dh_human_rm")
        _make_fully_migrated_store("DiffHumanRm", data_home)

        fixture_path = os.path.join(self._tmpdir, "old_human_rm.json")
        tables_dict = _current_snapshot_as_tables_dict()
        tables_dict["message"]["legacy_human"] = {
            "type": "TEXT",
            "notnull": 0,
            "pk": 0,
            "default": None,
        }
        _write_snapshot_fixture(fixture_path, tables_dict)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--project", "DiffHumanRm"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        combined = r.stdout + r.stderr
        self.assertIn(
            "legacy_human",
            combined,
            f"Human-readable --diff output must mention 'legacy_human' (the removed column).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_diff_human_output_exits_zero(self):
        """--diff (no --json) must exit 0.

        RED: --diff not wired → argparse exits 2.
        """
        data_home = os.path.join(self._tmpdir, "dh_human_exit")
        _make_fully_migrated_store("DiffHumanExit", data_home)
        fixture_path = os.path.join(self._tmpdir, "old_human_exit.json")
        import shutil
        shutil.copy2(_CURRENT_SCHEMA_PATH, fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--project", "DiffHumanExit"],
            data_home,
        )
        self.assertEqual(
            r.returncode,
            0,
            f"--diff (no --json) must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ===========================================================================
# Cycle 6 — AC6, AC9, AC11, AC12 + --rollback CLI structural gate
#
# §S7: the proving case — 0002-drop-message-status + core removal.
#
# Key facts used throughout this cycle's tests:
#   - sandesh does NOT enforce PRAGMA foreign_keys (no call in connect()), so the
#     12-step rebuild needs no FK toggling, but the rebuilt message table must
#     still DECLARE  in_reply_to INTEGER REFERENCES message(id).
#   - The "pre-0002 snapshot" fixture is the WITH-status shape (7 columns on
#     message: id, from_addr, subject, kind, status, in_reply_to, body_path,
#     created_at).  The post-0002 shape drops status (7 → 6 carrying columns:
#     id, from_addr, subject, kind, in_reply_to, body_path, created_at — the
#     "status" column is absent).
#   - _make_fully_migrated_store (Cycle 5 helper, module-level) provisions a
#     store and applies ALL available migrations.  After GREEN ships 0002, that
#     store will be at post-0002 state (no status).  At RED time the function
#     applies only 0001, so the post-0002 assertions fail with a clear reason.
#
# _CURRENT_SCHEMA_PATH (defined in the Cycle 4 section at line ~2384) and
# _SCHEMA_DIR are already in scope; _run_cli / _status_api / _VENV_PYTHON /
# _write_snapshot_fixture / _current_snapshot_as_tables_dict are all in scope
# from Cycles 3–5.  The helper _pragma_table_info / _list_user_tables / _REPO_ROOT
# / _SYSTEM_PYTHON are defined earlier.
# ===========================================================================


# ---------------------------------------------------------------------------
# §C6 shared helper — build the "pre-0002" snapshot fixture WITH status
#
# The fixture represents the schema BEFORE 0002 is applied (i.e. after 0001
# only).  It is derived from the live database shape of a 0001-only store
# rather than hard-coding magic values, so it stays accurate as long as the
# 0001-baseline faithfully reproduces _SCHEMA.
# ---------------------------------------------------------------------------

def _pre0002_snapshot_tables_dict():
    """Return the §S3 tables-dict for the PRE-0002 schema (message HAS status).

    This is derived from a real 0001-only store provisioned in a tmp dir, so
    it reflects the actual PRAGMA table_info shape rather than hand-wired values.
    """
    import tempfile
    import os
    from sandesh import sandesh_db, migrate

    tmp = tempfile.mkdtemp(prefix="sandesh_pre0002_fixture_")
    orig = os.environ.get("XDG_DATA_HOME")
    try:
        os.environ["XDG_DATA_HOME"] = tmp
        sandesh_db.setup("_pre0002fixture")
        # Apply only 0001: point the engine at just 0001 by using the standard
        # migrations dir (which currently has only 0001 at RED time; when 0002
        # is added, we need a different approach — see below).
        #
        # Safer: build the tables_dict directly from PRAGMA on a setup()-store,
        # since setup() == 0001-baseline by AC3.  That's always the pre-0002 shape.
        store = sandesh_db.store_dir("_pre0002fixture")
        db_path = os.path.join(store, sandesh_db.DB_FILE)
        import sqlite3
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        tables_dict = {}
        for table in ("address", "message", "message_recipient", "notifier"):
            cols = {}
            for r in con.execute(f"PRAGMA table_info({table})").fetchall():
                cols[r["name"]] = {
                    "type": r["type"],
                    "notnull": r["notnull"],
                    "pk": r["pk"],
                    "default": r["dflt_value"],
                }
            tables_dict[table] = cols
        con.close()
        return tables_dict
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        if orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = orig


def _apply_with_two_migrations(project_id, data_home, mig_dir):
    """Provision a store via sandesh_db.setup() then apply ALL migrations from
    `mig_dir` (which must contain both 0001 and 0002 at GREEN time).

    Used by rollback tests that need a fully-post-0002 store.
    """
    import os
    from sandesh import sandesh_db, migrate
    orig = os.environ.get("XDG_DATA_HOME")
    orig_mdir = os.environ.get("SANDESH_MIGRATIONS_DIR")
    os.environ["XDG_DATA_HOME"] = data_home
    if mig_dir is not None:
        os.environ["SANDESH_MIGRATIONS_DIR"] = mig_dir
    try:
        sandesh_db.setup(project_id)
        migrate.apply(project_id)
        store = sandesh_db.store_dir(project_id)
        return os.path.join(store, sandesh_db.DB_FILE)
    finally:
        if orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = orig
        if orig_mdir is None:
            os.environ.pop("SANDESH_MIGRATIONS_DIR", None)
        else:
            os.environ["SANDESH_MIGRATIONS_DIR"] = orig_mdir


# ---------------------------------------------------------------------------
# §C6-STRUCT — --rollback CLI structural gate
#
# AC6 requires migrate --rollback --project X.  Before it is wired in
# cli.py's migrate subparser the flag is unrecognised → argparse exits 2.
# This gate must pass (no exit-2) after GREEN wires the flag.
# ---------------------------------------------------------------------------

class MigrateRollbackFlagAcceptedTest(unittest.TestCase):
    """Structural gate: migrate --rollback --project X is accepted by the CLI.

    RED: --rollback is NOT in the migrate subparser → argparse exits 2
    (unrecognised argument error).

    GREEN criterion: the flag is added to the subparser regardless of whether
    a migration is present; the actual rollback behaviour is tested below.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c6_rb_struct_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def test_rollback_flag_not_rejected_by_argparse(self):
        """migrate --rollback --project X must NOT exit 2 (argparse unrecognised).

        RED: --rollback absent from the subparser → argparse exits 2.
        Exit codes 0 or 1 are both acceptable here; only 2 is the argparse signal
        for an unrecognised argument.
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_struct")
        _setup_project("RbStruct", data_home)
        r = _run_cli(["migrate", "--rollback", "--project", "RbStruct"], data_home)
        self.assertNotEqual(
            r.returncode,
            2,
            "migrate --rollback must not exit 2 (argparse unrecognised-argument error);\n"
            "this means --rollback is missing from the migrate subparser.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


# ---------------------------------------------------------------------------
# AC6 — rollback: after 0002 applied, --rollback restores message.status
# ---------------------------------------------------------------------------

class MigrateRollbackTest(unittest.TestCase):
    """AC6: after applying 0001 + 0002, migrate --rollback --project X rolls
    back 0002 and restores message.status.

    Verified via:
      (a) PRAGMA table_info(message) shows 'status' column RETURNS after rollback
      (b) migrate --status --project X shows 0002 as pending again (not applied)
      (c) migrate --status --project X shows 0001-baseline still applied

    RED failure reasons:
      1. --rollback flag absent from CLI → argparse exits 2
      2. migrate.rollback() function absent → AttributeError / not dispatched
      3. 0002-drop-message-status migration absent → no migration to roll back
         (nothing to test; 0002 simply doesn't exist yet)
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c6_rollback_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _apply_0001_and_0002(self, project_id, data_home):
        """Provision + apply all migrations (0001 + 0002) to project_id.

        Returns (db_path, store).  Uses the packaged migrations dir so the
        standard yoyo pipeline is exercised (not a custom dir).
        """
        import os
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup(project_id)
            migrate.apply(project_id)
            store = sandesh_db.store_dir(project_id)
            return os.path.join(store, sandesh_db.DB_FILE), store
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

    def _rollback_via_api(self, project_id, data_home):
        """Call migrate.rollback(project_id) directly (Python API, not CLI).

        Returns whatever rollback() returns (no return value asserted here).
        """
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            return migrate.rollback(project_id)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

    def test_rollback_function_callable_on_migrate_module(self):
        """sandesh.migrate.rollback must be a callable (takes project_id).

        RED: function absent → AttributeError.
        """
        from sandesh import migrate
        self.assertTrue(
            callable(getattr(migrate, "rollback", None)),
            "sandesh.migrate.rollback must be a callable; attribute is absent or not callable",
        )

    def test_rollback_restores_status_column_pragma(self):
        """After apply(0001+0002) + rollback, PRAGMA table_info(message) must
        include 'status' again.

        RED:
          - 0002 absent → after apply() only 0001 is applied; no 0002 to roll
            back; rollback() may raise or be a no-op; status column state
            doesn't change (was already present from 0001); but the FOLLOWING
            test (status shows 0002 pending) will catch the real missing piece.
          - rollback() absent → AttributeError.
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_pragma")
        db_path, _ = self._apply_0001_and_0002("RbPragma", data_home)

        # Verify 0002 was actually applied (status absent) before rollback
        col_names_before = [
            c["name"] for c in _pragma_table_info(db_path, "message")
        ]
        self.assertNotIn(
            "status",
            col_names_before,
            "Pre-condition: after applying 0001+0002, message.status must be ABSENT "
            f"(so the rollback test is meaningful); columns: {col_names_before!r}",
        )

        # Rollback 0002
        self._rollback_via_api("RbPragma", data_home)

        col_names_after = [
            c["name"] for c in _pragma_table_info(db_path, "message")
        ]
        self.assertIn(
            "status",
            col_names_after,
            "After rollback of 0002, message.status must be RESTORED in PRAGMA table_info;\n"
            f"columns found: {col_names_after!r}",
        )

    def test_rollback_makes_0002_pending_again_via_api(self):
        """After rollback, migrate.status() must show 0002 as pending (not applied).

        RED: rollback() absent / 0002 absent.
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_pending_api")
        self._apply_0001_and_0002("RbPendApi", data_home)
        self._rollback_via_api("RbPendApi", data_home)

        applied, pending = _status_api("RbPendApi", data_home)
        self.assertIn(
            "0002-drop-message-status",
            pending,
            "After rollback, '0002-drop-message-status' must be in the pending set;\n"
            f"applied={applied!r}, pending={pending!r}",
        )

    def test_rollback_keeps_0001_applied(self):
        """After rollback of 0002, 0001-baseline must remain applied (rollback=one step).

        RED: rollback() absent / 0002 absent.
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_keeps_0001")
        self._apply_0001_and_0002("RbKeeps0001", data_home)
        self._rollback_via_api("RbKeeps0001", data_home)

        applied, pending = _status_api("RbKeeps0001", data_home)
        self.assertIn(
            "0001-baseline",
            applied,
            "After rollback of 0002, '0001-baseline' must remain applied (one-step rollback);\n"
            f"applied={applied!r}",
        )

    def test_rollback_via_cli_makes_status_pending(self):
        """CLI: migrate --rollback --project X followed by migrate --status must
        show 0002-drop-message-status as pending.

        RED:
          1. --rollback not in subparser → argparse exits 2
          2. even if accepted: rollback not dispatched / 0002 absent
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_cli_status")
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup("RbCliStatus")
            migrate.apply("RbCliStatus")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        r_rb = _run_cli(["migrate", "--rollback", "--project", "RbCliStatus"], data_home)
        self.assertNotEqual(
            r_rb.returncode, 2,
            "migrate --rollback must not exit 2 (argparse unrecognised).\n"
            f"stdout: {r_rb.stdout!r}\nstderr: {r_rb.stderr!r}",
        )
        self.assertEqual(
            r_rb.returncode, 0,
            "migrate --rollback must exit 0 on success.\n"
            f"stdout: {r_rb.stdout!r}\nstderr: {r_rb.stderr!r}",
        )

        r_st = _run_cli(["migrate", "--status", "--project", "RbCliStatus"], data_home)
        combined = r_st.stdout + r_st.stderr
        self.assertIn(
            "0002-drop-message-status",
            combined,
            "After --rollback, migrate --status output must mention '0002-drop-message-status';\n"
            f"stdout: {r_st.stdout!r}\nstderr: {r_st.stderr!r}",
        )
        # Must convey it is PENDING (not applied)
        has_pending = (
            "pending" in combined.lower()
        )
        self.assertTrue(
            has_pending,
            "After --rollback, migrate --status output must indicate 0002 is pending;\n"
            f"stdout: {r_st.stdout!r}\nstderr: {r_st.stderr!r}",
        )

    def test_rollback_via_cli_restores_status_column(self):
        """CLI: migrate --rollback restores message.status column (PRAGMA check).

        RED: --rollback absent / 0002 absent.
        """
        data_home = os.path.join(self._tmpdir, "dh_rb_cli_pragma")
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            sandesh_db.setup("RbCliPragma")
            migrate.apply("RbCliPragma")
            store = sandesh_db.store_dir("RbCliPragma")
            db_path = os.path.join(store, sandesh_db.DB_FILE)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        _run_cli(["migrate", "--rollback", "--project", "RbCliPragma"], data_home)

        col_names = [c["name"] for c in _pragma_table_info(db_path, "message")]
        self.assertIn(
            "status",
            col_names,
            "After CLI --rollback, message.status must be present in PRAGMA table_info;\n"
            f"columns: {col_names!r}",
        )


# ---------------------------------------------------------------------------
# AC11 — status drop end-to-end
# ---------------------------------------------------------------------------

class MigrateStatusDropEndToEndTest(unittest.TestCase):
    """AC11: the headline test — after 0001+0002, message.status is gone from
    both a migrated store AND a brand-new setup() store; messaging still works.

    Part 1 — Schema shape:
      (a) Migrated store (0001+0002 applied): message has NO status column
      (b) New store (sandesh_db.setup()): message has NO status column
          This is the new≡migrated convergence test.

    Part 2 — Data carried + messaging intact:
      Seed a store with:
        - Two top-level messages (to + cc recipients, one with file body)
        - One threaded reply (in_reply_to set)
      Apply 0002 (via the adoption path: setup() + apply()).
      Assert:
        - All message rows survive with their carried columns intact
          (id, from_addr, subject, kind, in_reply_to, body_path, created_at)
        - message_recipient rows survive (per-recipient, role, read_at)
        - sandesh_db.thread() still works on the threaded reply
        - sandesh_db.inbox() still returns unread messages
        - sandesh_db.fetch() still works and marks messages read
        - No reference to 'status' in any of the above paths

    RED failure reasons:
      1. 0002 absent → apply() only applies 0001 → migrated store still HAS status
         → assertNotIn("status", col_names) fails
      2. _SCHEMA still has status column → new store via setup() still HAS status
         → assertNotIn("status", col_names) fails
      3. Even if both schema changes were made: sandesh_db.send/reply/thread/
         fetch reference status → they raise OperationalError (no such column)
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c6_ac11_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    # ---- Part 1a: migrated store has no status ----

    def test_migrated_store_message_has_no_status_column(self):
        """After applying 0001+0002, PRAGMA table_info(message) must NOT include 'status'.

        RED: 0002 absent → apply() only applies 0001 → status still present.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_mig")
        db_path = _make_fully_migrated_store("AC11Mig", data_home)

        col_names = [c["name"] for c in _pragma_table_info(db_path, "message")]
        self.assertNotIn(
            "status",
            col_names,
            "After applying all migrations (0001+0002), message.status must be ABSENT;\n"
            f"columns present: {col_names!r}",
        )

    def test_migrated_store_message_has_expected_columns(self):
        """After 0001+0002, message table has exactly the expected columns
        (id, from_addr, subject, kind, in_reply_to, body_path, created_at).

        RED: 0002 absent → status still present; count/set mismatch.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_cols")
        db_path = _make_fully_migrated_store("AC11Cols", data_home)

        col_names = sorted(c["name"] for c in _pragma_table_info(db_path, "message"))
        expected = sorted([
            "id", "from_addr", "subject", "kind", "in_reply_to", "body_path", "created_at"
        ])
        self.assertEqual(
            col_names,
            expected,
            "After 0001+0002, message columns must be exactly "
            f"{expected!r};\nfound: {col_names!r}",
        )

    def test_migrated_store_in_reply_to_declared(self):
        """After 0001+0002 rebuild, message.in_reply_to must still be declared
        (the 12-step rebuild must carry it — FK declared even though not enforced).

        RED: 0002 absent / rebuild omits in_reply_to.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_fk")
        db_path = _make_fully_migrated_store("AC11FK", data_home)

        col_names = [c["name"] for c in _pragma_table_info(db_path, "message")]
        self.assertIn(
            "in_reply_to",
            col_names,
            "After 0001+0002 rebuild, message.in_reply_to must still be present;\n"
            f"columns: {col_names!r}",
        )

    # ---- Part 1b: new store has no status (new≡migrated convergence) ----

    def test_new_store_via_setup_has_no_status_column(self):
        """A brand-new store provisioned via sandesh_db.setup() must NOT have
        message.status (the _SCHEMA must be updated to match post-0002 shape).

        RED: _SCHEMA still has 'status TEXT NOT NULL DEFAULT 'open'' →
             setup()-provisioned store still has status column.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_new")
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db
            sandesh_db.setup("AC11New")
            store = sandesh_db.store_dir("AC11New")
            db_path = os.path.join(store, sandesh_db.DB_FILE)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        col_names = [c["name"] for c in _pragma_table_info(db_path, "message")]
        self.assertNotIn(
            "status",
            col_names,
            "A new store via sandesh_db.setup() must NOT have message.status "
            "(the _SCHEMA must be updated to post-0002 shape);\n"
            f"columns present: {col_names!r}",
        )

    def test_new_store_and_migrated_store_message_columns_match(self):
        """New≡migrated convergence: the message table columns must be identical
        between a sandesh_db.setup()-provisioned store and a fully-migrated store.

        RED:
          - If 0002 absent: migrated store has status, new store has status →
            both have status → they "match" but the assertNotIn tests above fail.
          - If _SCHEMA updated but 0002 absent: new store has no status, migrated
            store still has status → mismatch → THIS test fails.
          - If both updated: should match → passes only on GREEN.
        """
        data_home_new = os.path.join(self._tmpdir, "dh_ac11_conv_new")
        data_home_mig = os.path.join(self._tmpdir, "dh_ac11_conv_mig")

        # New store via setup()
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home_new
        try:
            from sandesh import sandesh_db
            sandesh_db.setup("AC11ConvNew")
            store_new = sandesh_db.store_dir("AC11ConvNew")
            db_new = os.path.join(store_new, sandesh_db.DB_FILE)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        # Migrated store
        db_mig = _make_fully_migrated_store("AC11ConvMig", data_home_mig)

        cols_new = _pragma_table_info(db_new, "message")
        cols_mig = _pragma_table_info(db_mig, "message")
        self.assertEqual(
            cols_new,
            cols_mig,
            "New store (setup) and migrated store must have identical message columns "
            "(new≡migrated convergence);\n"
            f"  new store:      {cols_new!r}\n"
            f"  migrated store: {cols_mig!r}",
        )

    # ---- Part 2: data carried + messaging intact after 0002 ----

    def _seed_store(self, project_id, data_home):
        """Seed a store (provisioned via setup()) with messages and return the
        db_path, store dir, and the seeded message ids.

        Seeds:
          - Register addresses: Mainline-AC11Data, Track1-AC11Data, Track2-AC11Data
          - send() msg1: from Track1, to=[Mainline], cc=[Track2], subject="ping",
            body_text="hello world" (file body)
          - send() msg2: from Track2, to=[Mainline], subject="fyi" (subject-only)
          - reply() msg3: from Mainline to Track1, re: msg1, in_reply_to=msg1
            subject="Re: ping", body_text="ack"

        Returns (db_path, store, msg1_id, msg2_id, msg3_id)
        """
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db
            sandesh_db.setup(project_id)
            store = sandesh_db.store_dir(project_id)
            db_path = os.path.join(store, sandesh_db.DB_FILE)

            con = sandesh_db.connect(store)
            # Register addresses (validate=False avoids project_id check in send)
            sandesh_db.register(con, f"Mainline - {project_id}", kind="mainline",
                                project=project_id)
            sandesh_db.register(con, f"Track 1 - {project_id}", kind="track",
                                project=project_id)
            sandesh_db.register(con, f"Track 2 - {project_id}", kind="track",
                                project=project_id)

            mainline = f"Mainline - {project_id}"
            track1 = f"Track 1 - {project_id}"
            track2 = f"Track 2 - {project_id}"

            # msg1: from Track1, to=[Mainline], cc=[Track2], with file body
            mid1 = sandesh_db.send(
                con, store, track1,
                to=[mainline], cc=[track2],
                subject="ping", kind="request",
                body_text="hello world",
                project=project_id,
            )
            # msg2: from Track2, to=[Mainline], subject-only
            mid2 = sandesh_db.send(
                con, store, track2,
                to=[mainline],
                subject="fyi", kind="fyi",
                project=project_id,
            )
            # msg3: from Mainline, reply to msg1, to=[Track1], file body
            mid3 = sandesh_db.reply(
                con, store, mid1, mainline,
                body_text="ack",
                project=project_id,
            )
            con.close()
            return db_path, store, mid1, mid2, mid3
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

    def test_data_rows_survive_0002_message_ids_intact(self):
        """After seeding + applying 0002, all three message rows must still exist.

        RED: 0002's INSERT…SELECT drops rows (rebuild bug) → count wrong.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_data_ids")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11DataIds", data_home)

        # Apply 0002 via the adoption path (setup() already done, apply marks 0001 + runs 0002)
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply("AC11DataIds")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT id FROM message ORDER BY id").fetchall()
        con.close()
        ids = [r["id"] for r in rows]
        self.assertEqual(
            sorted(ids),
            sorted([mid1, mid2, mid3]),
            f"All three message ids must survive 0002; expected {sorted([mid1, mid2, mid3])!r}, "
            f"got {sorted(ids)!r}",
        )

    def test_data_rows_survive_0002_carried_columns_intact(self):
        """After 0002, carried columns (from_addr, subject, kind, in_reply_to,
        body_path, created_at) must be intact for all message rows.

        RED: 0002's INSERT…SELECT omits a column or maps it wrong.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_data_cols")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11DataCols", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply("AC11DataCols")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        # msg1: has a body_path (file body)
        row1 = con.execute("SELECT * FROM message WHERE id=?", (mid1,)).fetchone()
        self.assertIsNotNone(row1, f"message #{mid1} must survive 0002")
        self.assertEqual(row1["from_addr"], f"Track 1 - AC11DataCols",
                         f"msg1 from_addr wrong: {row1['from_addr']!r}")
        self.assertEqual(row1["subject"], "ping",
                         f"msg1 subject wrong: {row1['subject']!r}")
        self.assertEqual(row1["kind"], "request",
                         f"msg1 kind wrong: {row1['kind']!r}")
        self.assertIsNone(row1["in_reply_to"],
                          f"msg1 in_reply_to must be None (top-level): {row1['in_reply_to']!r}")
        self.assertIsNotNone(row1["body_path"],
                             "msg1 body_path must not be None (file body was provided)")

        # msg2: subject-only (no body_path)
        row2 = con.execute("SELECT * FROM message WHERE id=?", (mid2,)).fetchone()
        self.assertIsNotNone(row2, f"message #{mid2} must survive 0002")
        self.assertIsNone(row2["body_path"],
                          f"msg2 body_path must be None (subject-only): {row2['body_path']!r}")
        self.assertEqual(row2["subject"], "fyi",
                         f"msg2 subject wrong: {row2['subject']!r}")

        # msg3: reply — in_reply_to must point to msg1
        row3 = con.execute("SELECT * FROM message WHERE id=?", (mid3,)).fetchone()
        self.assertIsNotNone(row3, f"message #{mid3} must survive 0002")
        self.assertEqual(row3["in_reply_to"], mid1,
                         f"msg3 in_reply_to must be {mid1}; got {row3['in_reply_to']!r}")

        con.close()

    def test_data_rows_no_status_column_after_0002(self):
        """After 0002, SELECT * FROM message must not include a 'status' key
        (not in column names, not accessible via row[]).

        RED: 0002 absent → status column still present → keys include 'status'.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_no_status_col")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11NoStatusCol", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply("AC11NoStatusCol")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM message WHERE id=?", (mid1,)).fetchone()
        con.close()
        self.assertIsNotNone(row, f"message #{mid1} must exist")
        col_keys = list(row.keys())
        self.assertNotIn(
            "status",
            col_keys,
            "After 0002, message rows must NOT have a 'status' key;\n"
            f"row keys: {col_keys!r}",
        )

    def test_recipients_intact_after_0002(self):
        """After 0002, message_recipient rows must survive (role + read_at intact).

        msg1 was sent to Mainline (to) and cc'd Track2 (cc): both rows must exist.
        RED: 0002 drops message_recipient rows (shouldn't — only message is rebuilt).
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_recips")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11Recips", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import migrate
            migrate.apply("AC11Recips")
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        recips = con.execute(
            "SELECT recipient, role, read_at FROM message_recipient WHERE message_id=? "
            "ORDER BY recipient",
            (mid1,),
        ).fetchall()
        con.close()

        self.assertEqual(len(recips), 2,
                         f"msg1 must have exactly 2 recipients; got {len(recips)}: "
                         f"{[(r['recipient'], r['role']) for r in recips]!r}")

        roles = {r["recipient"].split(" - ")[0]: r["role"] for r in recips}
        self.assertEqual(roles.get("Mainline"), "to",
                         f"Mainline must have role='to'; roles: {roles!r}")
        self.assertEqual(roles.get("Track 2"), "cc",
                         f"Track 2 must have role='cc'; roles: {roles!r}")

        # Both unread (read_at=None) since no fetch has occurred
        for r in recips:
            self.assertIsNone(r["read_at"],
                              f"{r['recipient']} read_at must be None (unread); got {r['read_at']!r}")

    def test_thread_still_works_after_0002(self):
        """After 0002, sandesh_db.thread(msg3_id) must still return the chain
        [msg1, msg3] (ascending by id, from root to reply).

        RED: 0002 absent → sandesh_db.thread() SELECT references m.status → works;
             but if status removed from sandesh_db without removing the SELECT
             reference → OperationalError: no such column: m.status.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_thread")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11Thread", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            migrate.apply("AC11Thread")
            con = sandesh_db.connect(store)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        try:
            chain = sandesh_db.thread(con, mid3)
        except Exception as exc:
            self.fail(
                f"sandesh_db.thread() raised after 0002: {type(exc).__name__}: {exc}\n"
                "Likely cause: 'status' column reference not removed from thread()/its SELECT."
            )
        finally:
            con.close()

        self.assertEqual(
            len(chain), 2,
            f"thread(msg3) must return [msg1, msg3] (2 entries); got {len(chain)!r}",
        )
        self.assertEqual(chain[0]["id"], mid1,
                         f"chain[0] must be msg1 (root); got id={chain[0]['id']!r}")
        self.assertEqual(chain[1]["id"], mid3,
                         f"chain[1] must be msg3 (reply); got id={chain[1]['id']!r}")

    def test_inbox_still_works_after_0002(self):
        """After 0002, sandesh_db.inbox(mainline) must return the unread messages
        for Mainline (msg1 and msg2 sent to Mainline).

        RED: 'status' reference in inbox() SELECT → OperationalError after 0002.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_inbox")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11Inbox", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            migrate.apply("AC11Inbox")
            con = sandesh_db.connect(store)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        mainline = "Mainline - AC11Inbox"
        try:
            rows = sandesh_db.inbox(con, mainline, unread_only=True)
        except Exception as exc:
            self.fail(
                f"sandesh_db.inbox() raised after 0002: {type(exc).__name__}: {exc}\n"
                "Likely cause: 'status' column reference not removed from inbox() SELECT."
            )
        finally:
            con.close()

        # Mainline is a 'to' recipient of msg1 and msg2
        inbox_ids = sorted(r["id"] for r in rows)
        self.assertIn(mid1, inbox_ids,
                      f"msg1 must be in Mainline's inbox; inbox_ids={inbox_ids!r}")
        self.assertIn(mid2, inbox_ids,
                      f"msg2 must be in Mainline's inbox; inbox_ids={inbox_ids!r}")
        # Exactly 2 unread (msg3 was sent FROM Mainline so Mainline is not a recipient)
        self.assertEqual(
            len(rows), 2,
            f"Mainline must have exactly 2 unread messages (msg1, msg2); "
            f"got {len(rows)}: {inbox_ids!r}",
        )

    def test_fetch_still_works_after_0002(self):
        """After 0002, sandesh_db.fetch(mainline) must return messages and mark them read.

        RED: 'status' reference not removed from fetch() / inbox() SELECT path.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_fetch")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11Fetch", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            migrate.apply("AC11Fetch")
            con = sandesh_db.connect(store)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        mainline = "Mainline - AC11Fetch"
        try:
            items = sandesh_db.fetch(con, store, mainline, mark=True)
        except Exception as exc:
            self.fail(
                f"sandesh_db.fetch() raised after 0002: {type(exc).__name__}: {exc}\n"
                "Likely cause: 'status' column reference not removed."
            )
        finally:
            con.close()

        self.assertEqual(
            len(items), 2,
            f"fetch() must return 2 items for Mainline (msg1 + msg2); got {len(items)!r}",
        )
        fetched_ids = sorted(it["id"] for it in items)
        self.assertEqual(fetched_ids, sorted([mid1, mid2]),
                         f"fetch() items must be msg1+msg2; got {fetched_ids!r}")

        # Verify marked read: inbox should now be empty
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db
            con2 = sandesh_db.connect(store)
            remaining = sandesh_db.inbox(con2, mainline, unread_only=True)
            con2.close()
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        self.assertEqual(
            len(remaining), 0,
            f"After fetch(mark=True), inbox must be empty; got {len(remaining)} unread",
        )

    def test_cc_recipient_stays_unread_after_fetch_by_to(self):
        """After 0002, a cc recipient (Track 2) stays unread after Mainline fetches msg1.

        Per-recipient read semantics must survive the schema change (read_at on
        message_recipient, not on message).

        RED: 0002 absent — but if status is also removed from sandesh_db without
        fixing the SELECT, this raises OperationalError (caught by other tests).
        At RED time this test fails because 0002 has not been applied.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac11_cc_unread")
        db_path, store, mid1, mid2, mid3 = self._seed_store("AC11CCUnread", data_home)

        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db, migrate
            migrate.apply("AC11CCUnread")
            con = sandesh_db.connect(store)
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        mainline = "Mainline - AC11CCUnread"
        track2 = "Track 2 - AC11CCUnread"

        # Mainline fetches (marks its copy read)
        sandesh_db.fetch(con, store, mainline, mark=True)
        con.close()

        # Track 2 should still have msg1 unread in its inbox
        orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = data_home
        try:
            from sandesh import sandesh_db
            con2 = sandesh_db.connect(store)
            track2_unread = sandesh_db.inbox(con2, track2, unread_only=True)
            con2.close()
        finally:
            if orig is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = orig

        unread_ids = [r["id"] for r in track2_unread]
        self.assertIn(
            mid1, unread_ids,
            "Track 2 (cc recipient) must still have msg1 unread after Mainline (to) fetched it;\n"
            f"Track 2 unread ids: {unread_ids!r}",
        )


# ---------------------------------------------------------------------------
# AC9 — diff reports message.status in 'removed' list
#
# Fixture: the "pre-0002" snapshot (a store provisioned only via setup(), which
# equals 0001-baseline by AC3).  That snapshot HAS status.  The fully-migrated
# store (0001+0002) does NOT have status.
#
# We construct the pre-0002 fixture from a real setup()-provisioned store so
# the column descriptors are accurate (not hand-wired).
# ---------------------------------------------------------------------------

class MigrateDiffStatusRemovedTest(unittest.TestCase):
    """AC9: migrate --diff <pre-0002-snapshot> --json --project X reports
    message.status in the 'removed' list.

    The pre-0002 snapshot is constructed from a sandesh_db.setup()-provisioned
    store (which has status by AC3 at this point in time).  The live store is
    fully migrated (0001+0002) so it lacks status.

    RED failure reasons:
      1. 0002 absent → fully-migrated store still has status → diff sees no
         difference between old (with status) and current (with status) → status
         NOT in 'removed' → assertion fails.
      2. --diff not accepted (but this was wired in Cycle 5; shouldn't be an issue).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_test_c6_ac9_")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if self._orig_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig_xdg

    def _write_pre0002_fixture(self, fixture_path):
        """Write the pre-0002 snapshot (WITH status) to fixture_path.

        The snapshot is derived from a real setup()-provisioned store so the
        column descriptors (type, notnull, pk, default) are accurate.
        """
        tables_dict = _pre0002_snapshot_tables_dict()
        # Sanity: the pre-0002 snapshot must contain message.status
        self.assertIn(
            "status",
            tables_dict.get("message", {}),
            "Pre-condition: the pre-0002 snapshot must contain message.status;\n"
            "This means sandesh_db._SCHEMA still has status (correct at RED time).",
        )
        _write_snapshot_fixture(fixture_path, tables_dict)

    def test_diff_status_removed_appears_in_removed_list(self):
        """migrate --diff <pre-0002-snapshot> --json on a fully-migrated store must
        include message.status in the 'removed' list.

        RED: 0002 absent → live store still has status → diff sees no removal.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac9_removed")
        _make_fully_migrated_store("AC9Removed", data_home)

        fixture_path = os.path.join(self._tmpdir, "pre0002_snapshot.json")
        self._write_pre0002_fixture(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "AC9Removed"],
            data_home,
        )
        self.assertEqual(
            r.returncode, 0,
            f"migrate --diff must exit 0.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        parsed = _json.loads(r.stdout)
        removed = parsed.get("removed", [])
        removed_cols = [
            (e.get("table"), e.get("column"))
            for e in removed
            if isinstance(e, dict)
        ]
        self.assertIn(
            ("message", "status"),
            removed_cols,
            "message.status must appear in the 'removed' list when diffing pre-0002 "
            "snapshot against a fully-migrated (post-0002) store;\n"
            f"removed list: {removed!r}\nfull JSON: {parsed!r}",
        )

    def test_diff_status_removed_entry_has_old_descriptor(self):
        """The 'removed' entry for message.status must include the 'old' descriptor.

        Entry shape: {"table": "message", "column": "status", "old": {type, notnull, pk, default}}

        RED: 0002 absent → status not in 'removed'.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac9_desc")
        _make_fully_migrated_store("AC9Desc", data_home)

        fixture_path = os.path.join(self._tmpdir, "pre0002_desc.json")
        self._write_pre0002_fixture(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "AC9Desc"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        removed = parsed.get("removed", [])
        status_entries = [
            e for e in removed
            if isinstance(e, dict)
            and e.get("table") == "message"
            and e.get("column") == "status"
        ]
        self.assertEqual(
            len(status_entries), 1,
            f"Exactly 1 'removed' entry for (message, status); got {len(status_entries)}: {removed!r}",
        )
        entry = status_entries[0]
        self.assertIn(
            "old",
            entry,
            f"'removed' entry for message.status must have 'old' descriptor; got {entry!r}",
        )
        old_desc = entry["old"]
        self.assertIsInstance(old_desc, dict,
                              f"'old' descriptor must be a dict; got {type(old_desc)!r}")
        for key in ("type", "notnull", "pk", "default"):
            self.assertIn(
                key, old_desc,
                f"'old' descriptor must contain '{key}'; got keys: {list(old_desc.keys())!r}",
            )

    def test_diff_status_type_is_text(self):
        """The removed message.status must have type='TEXT' in the old descriptor.

        Validates the fixture accurately captures the pre-0002 schema.
        RED: 0002 absent → not in 'removed' list.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac9_type")
        _make_fully_migrated_store("AC9Type", data_home)
        fixture_path = os.path.join(self._tmpdir, "pre0002_type.json")
        self._write_pre0002_fixture(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "AC9Type"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        removed = parsed.get("removed", [])
        status_entry = next(
            (e for e in removed if isinstance(e, dict)
             and e.get("table") == "message" and e.get("column") == "status"),
            None,
        )
        self.assertIsNotNone(
            status_entry,
            f"message.status must appear in 'removed'; removed={removed!r}",
        )
        self.assertEqual(
            status_entry.get("old", {}).get("type"),
            "TEXT",
            f"message.status old type must be 'TEXT'; got {status_entry.get('old', {})!r}",
        )

    def test_diff_added_and_changed_are_empty_for_pre0002_vs_post0002(self):
        """When the old snapshot is pre-0002 (only status removed), 'added' and
        'changed' lists must be empty (only a removal, nothing added or changed).

        RED: 0002 absent → live store still has status → diff shows no difference
        at all → added=[], removed=[], changed=[] → 'removed' is empty → the
        above tests fail, but this test would PASS (vacuously) at RED time.
        This test is therefore a GREEN-guaranteeing assertion: it should pass
        once 0002 exists AND the snapshot only changed by removing status.
        """
        data_home = os.path.join(self._tmpdir, "dh_ac9_add_chg")
        _make_fully_migrated_store("AC9AddChg", data_home)
        fixture_path = os.path.join(self._tmpdir, "pre0002_add_chg.json")
        self._write_pre0002_fixture(fixture_path)

        r = _run_cli(
            ["migrate", "--diff", fixture_path, "--json", "--project", "AC9AddChg"],
            data_home,
        )
        self.assertEqual(r.returncode, 0,
                         f"Exit 0 required.\nstdout: {r.stdout!r}\nstderr: {r.stderr!r}")
        parsed = _json.loads(r.stdout)
        self.assertEqual(
            parsed.get("added", "MISSING"),
            [],
            f"'added' must be empty when old=pre-0002 and current=post-0002; "
            f"got: {parsed.get('added')!r}",
        )
        self.assertEqual(
            parsed.get("changed", "MISSING"),
            [],
            f"'changed' must be empty when only status was removed; "
            f"got: {parsed.get('changed')!r}",
        )


# ---------------------------------------------------------------------------
# AC12 — core stdlib purity preserved
#
# The non-migrate test suite must still run under system python3 with no
# third-party deps installed.  This is a guard that GREEN must keep green.
# At RED time it PASSES (since nothing has been removed yet); if GREEN's
# sandesh_db edits accidentally import a third-party dep (or refer to a
# removed symbol in a way that breaks stdlib-only execution) this test fails.
# ---------------------------------------------------------------------------

class MigrateCorePurityStdlibTest(unittest.TestCase):
    """AC12: tests/test_sandesh.py must pass under the SYSTEM python3 (no third-party deps).

    Uses /usr/bin/python3 which has no yoyo, jsonschema, or other project venv deps.
    The test verifies:
      (a) the subprocess exits 0 (all tests passed)
      (b) no 'ERROR' or 'FAIL' in the output (no test errors)

    At RED time this test PASSES (status hasn't been removed from sandesh_db yet).
    Its job is to fail during GREEN if a removal breaks stdlib compatibility.
    It's included here as a guard assertion that GREEN must keep green.
    """

    def test_core_suite_passes_under_system_python3(self):
        """tests/test_sandesh.py passes under /usr/bin/python3 (no third-party deps).

        RED: this test PASSES at RED time (sandesh_db still has status).
        GREEN must keep it passing after status removal.
        If it fails after GREEN, the removal broke stdlib compatibility.
        """
        r = subprocess.run(
            [
                _SYSTEM_PYTHON,
                "-m", "unittest", "tests.test_sandesh",
            ],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(
            r.returncode,
            0,
            "tests/test_sandesh.py must pass under system python3 with no third-party deps.\n"
            "If this fails after GREEN's status removal, a stdlib-only module was broken.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_core_suite_no_errors_or_failures_under_system_python3(self):
        """tests/test_sandesh.py output must not contain ERROR or FAIL lines.

        Companion to the exit-code check: catches the case where subprocess
        exits non-zero due to test errors (which would already be caught above)
        but provides an explicit message about which failure kind occurred.
        """
        r = subprocess.run(
            [
                _SYSTEM_PYTHON,
                "-m", "unittest", "tests.test_sandesh",
            ],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = r.stdout + r.stderr
        # unittest outputs "ERROR:" for test errors and "FAIL:" for assertion failures
        has_error = "ERROR:" in combined or "\nERROR " in combined
        has_fail = "FAIL:" in combined or "\nFAIL " in combined
        self.assertFalse(
            has_error or has_fail,
            "tests/test_sandesh.py must have no ERRORs or FAILs under system python3.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
