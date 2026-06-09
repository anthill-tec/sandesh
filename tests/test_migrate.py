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


if __name__ == "__main__":
    unittest.main(verbosity=2)
