"""test_pyproject.py — pyproject.toml packaging contract tests (CR-SAN-008 AC2 + AC2b).

Asserts that `pyproject.toml` at the repo root declares the correct metadata,
entry points, optional-dependencies extra, and git-tag-driven versioning via
hatch-vcs.

These tests FAIL at RED because `pyproject.toml` does not yet exist — the very
first assertion (`test_pyproject_exists`) fails with a clean AssertionError,
not an unhandled exception.

Parsed with `tomllib` (Python 3.11+ stdlib; the project venv is 3.14).

  python-crucible.py test --tests tests.test_pyproject --agent CR-SAN-008-C1-RED
"""

import os
import tomllib
import unittest

# Resolve the repo root relative to this test file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")


def _load_pyproject():
    """Load and return the parsed pyproject.toml dict. Caller must guard with exists check."""
    with open(_PYPROJECT_PATH, "rb") as fh:
        return tomllib.load(fh)


class PyprojectExistsTest(unittest.TestCase):
    """Gate: the file must exist before any key-level assertions make sense."""

    def test_pyproject_exists(self):
        """AC2: pyproject.toml must be present at the repo root."""
        self.assertTrue(
            os.path.isfile(_PYPROJECT_PATH),
            f"pyproject.toml missing — expected at {_PYPROJECT_PATH}",
        )


class ProjectMetadataTest(unittest.TestCase):
    """AC2 — [project] metadata contract."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping metadata assertions")
        cls.data = _load_pyproject()
        cls.project = cls.data.get("project", {})

    def test_project_name_is_sandesh_relay(self):
        """AC2: [project].name == 'sandesh-relay' (PyPI distribution name)."""
        self.assertEqual(
            self.project.get("name"),
            "sandesh-relay",
            "[project].name must be 'sandesh-relay'",
        )

    def test_requires_python_present(self):
        """AC2: [project].requires-python is declared."""
        self.assertIn(
            "requires-python",
            self.project,
            "[project].requires-python must be present",
        )

    def test_requires_python_gte_310(self):
        """AC2: [project].requires-python expresses >= 3.10."""
        rp = self.project.get("requires-python", "")
        self.assertIn(
            "3.10",
            rp,
            f"[project].requires-python must mention 3.10; got {rp!r}",
        )
        self.assertIn(
            ">=",
            rp,
            f"[project].requires-python must use '>=' constraint; got {rp!r}",
        )

    def test_license_indicates_gpl3(self):
        """AC2: [project].license indicates GPL-3.0 (SPDX string or table text)."""
        raw = self.project.get("license")
        self.assertIsNotNone(raw, "[project].license must be declared")

        # Tolerant: accept both `license = "GPL-3.0-only"` (string)
        # and `license = {text = "..."}` / `license = {file = "..."}` (table).
        if isinstance(raw, str):
            license_str = raw
        elif isinstance(raw, dict):
            # SPDX 'text' key, or 'file' key (less common)
            license_str = raw.get("text", "") or raw.get("file", "")
        else:
            self.fail(f"[project].license has unexpected type {type(raw)!r}: {raw!r}")

        self.assertIn(
            "GPL-3.0",
            license_str,
            f"[project].license must contain 'GPL-3.0'; got {license_str!r}",
        )


class ProjectScriptsTest(unittest.TestCase):
    """AC2 — [project.scripts] entry-point contract."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping scripts assertions")
        cls.data = _load_pyproject()
        cls.scripts = cls.data.get("project", {}).get("scripts", {})

    def test_sandesh_script_entry_point(self):
        """AC2: [project.scripts] sandesh == 'sandesh.cli:main'."""
        self.assertEqual(
            self.scripts.get("sandesh"),
            "sandesh.cli:main",
            f"[project.scripts].sandesh must be 'sandesh.cli:main'; got {self.scripts.get('sandesh')!r}",
        )

    def test_sandesh_mcp_script_entry_point(self):
        """AC2: [project.scripts] sandesh-mcp == 'sandesh.mcp_server:main'."""
        self.assertEqual(
            self.scripts.get("sandesh-mcp"),
            "sandesh.mcp_server:main",
            f"[project.scripts].sandesh-mcp must be 'sandesh.mcp_server:main'; got {self.scripts.get('sandesh-mcp')!r}",
        )


class OptionalDependenciesTest(unittest.TestCase):
    """AC2 — [project.optional-dependencies] mcp extra contract."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping optional-deps assertions")
        cls.data = _load_pyproject()
        cls.opt_deps = cls.data.get("project", {}).get("optional-dependencies", {})

    def test_mcp_extra_present(self):
        """AC2: [project.optional-dependencies].mcp must exist and be a list."""
        self.assertIn(
            "mcp",
            self.opt_deps,
            "[project.optional-dependencies] must declare a 'mcp' extra",
        )
        self.assertIsInstance(
            self.opt_deps["mcp"],
            list,
            "[project.optional-dependencies].mcp must be a list",
        )

    def test_mcp_extra_not_empty(self):
        """AC2: [project.optional-dependencies].mcp list is non-empty."""
        mcp_list = self.opt_deps.get("mcp", [])
        self.assertGreater(
            len(mcp_list),
            0,
            "[project.optional-dependencies].mcp must not be empty",
        )

    def test_mcp_extra_pins_mcp_package_lower_bound(self):
        """AC2: the mcp extra contains an entry starting with 'mcp>=1.27'."""
        mcp_list = self.opt_deps.get("mcp", [])
        matches = [e for e in mcp_list if isinstance(e, str) and e.startswith("mcp>=1.27")]
        self.assertTrue(
            len(matches) >= 1,
            f"[project.optional-dependencies].mcp must contain an entry starting with 'mcp>=1.27'; "
            f"got {mcp_list!r}",
        )

    def test_mcp_extra_pins_mcp_package_upper_bound(self):
        """AC2: the mcp extra entry that pins mcp also includes '<2' upper bound."""
        mcp_list = self.opt_deps.get("mcp", [])
        matches = [e for e in mcp_list if isinstance(e, str) and "mcp" in e and "<2" in e]
        self.assertTrue(
            len(matches) >= 1,
            f"[project.optional-dependencies].mcp must contain an entry with '<2' upper bound; "
            f"got {mcp_list!r}",
        )


class HatchVcsVersioningTest(unittest.TestCase):
    """AC2b — git-tag-driven versioning via hatch-vcs contract."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping versioning assertions")
        cls.data = _load_pyproject()
        cls.project = cls.data.get("project", {})
        cls.build_system = cls.data.get("build-system", {})
        cls.tool_hatch = cls.data.get("tool", {}).get("hatch", {})

    def test_build_system_requires_hatchling(self):
        """AC2b: [build-system].requires includes hatchling."""
        requires = self.build_system.get("requires", [])
        self.assertIsInstance(requires, list, "[build-system].requires must be a list")
        has_hatchling = any("hatchling" in r for r in requires if isinstance(r, str))
        self.assertTrue(
            has_hatchling,
            f"[build-system].requires must include 'hatchling'; got {requires!r}",
        )

    def test_build_system_requires_hatch_vcs(self):
        """AC2b: [build-system].requires includes hatch-vcs."""
        requires = self.build_system.get("requires", [])
        self.assertIsInstance(requires, list, "[build-system].requires must be a list")
        has_hatch_vcs = any("hatch-vcs" in r for r in requires if isinstance(r, str))
        self.assertTrue(
            has_hatch_vcs,
            f"[build-system].requires must include 'hatch-vcs'; got {requires!r}",
        )

    def test_build_backend_is_hatchling(self):
        """AC2b: [build-system].build-backend == 'hatchling.build'."""
        backend = self.build_system.get("build-backend")
        self.assertEqual(
            backend,
            "hatchling.build",
            f"[build-system].build-backend must be 'hatchling.build'; got {backend!r}",
        )

    def test_project_dynamic_includes_version(self):
        """AC2b: [project].dynamic is a list that includes 'version'."""
        dynamic = self.project.get("dynamic", [])
        self.assertIsInstance(dynamic, list, "[project].dynamic must be a list")
        self.assertIn(
            "version",
            dynamic,
            f"[project].dynamic must include 'version'; got {dynamic!r}",
        )

    def test_no_static_version_in_project(self):
        """AC2b: there is NO static [project].version key (version is dynamic)."""
        self.assertNotIn(
            "version",
            self.project,
            "[project] must NOT have a static 'version' key — versioning is dynamic via hatch-vcs",
        )

    def test_tool_hatch_version_source_is_vcs(self):
        """AC2b: [tool.hatch.version].source == 'vcs'."""
        hatch_version = self.tool_hatch.get("version", {})
        source = hatch_version.get("source")
        self.assertEqual(
            source,
            "vcs",
            f"[tool.hatch.version].source must be 'vcs'; got {source!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
