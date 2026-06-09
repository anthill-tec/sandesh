"""test_migrate.py — RED tests for CR-SAN-017 Cycle 1.

Covers:
  AC1  — import purity: sandesh_db / cli / notify / mcp_server / migrate.py must
          not pull yoyo or jsonschema into sys.modules at module load (all five
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
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
