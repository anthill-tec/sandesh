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


# ---------------------------------------------------------------------------
# CR-SAN-020 — PyPI packaging metadata hardening (AC1, AC2 floors, AC3, AC5)
# ---------------------------------------------------------------------------


class LicenseFilesTest(unittest.TestCase):
    """AC1 — [project] must declare license-files and the referenced file must exist."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping license-files assertions")
        cls.data = _load_pyproject()
        cls.project = cls.data.get("project", {})

    def test_license_files_key_present(self):
        """AC1: [project].license-files must be declared."""
        self.assertIn(
            "license-files",
            self.project,
            "[project].license-files must be declared (PEP 639); currently absent",
        )

    def test_license_files_is_list(self):
        """AC1: [project].license-files must be a list."""
        lf = self.project.get("license-files")
        if lf is None:
            self.skipTest("license-files absent — covered by test_license_files_key_present")
        self.assertIsInstance(
            lf,
            list,
            f"[project].license-files must be a list; got {type(lf)!r}: {lf!r}",
        )

    def test_license_files_contains_LICENSE(self):
        """AC1: [project].license-files must include 'LICENSE'."""
        lf = self.project.get("license-files", [])
        if not isinstance(lf, list):
            self.skipTest("license-files is not a list — covered by test_license_files_is_list")
        self.assertIn(
            "LICENSE",
            lf,
            f"[project].license-files must contain 'LICENSE'; got {lf!r}",
        )

    def test_license_file_exists_on_disk(self):
        """AC1: the repo-root LICENSE file referenced in license-files must exist."""
        license_path = os.path.join(_REPO_ROOT, "LICENSE")
        self.assertTrue(
            os.path.isfile(license_path),
            f"LICENSE file must exist at {license_path}",
        )


class BuildSystemPinnedRequiresTest(unittest.TestCase):
    """AC2 (floors) — [build-system].requires must pin lower bounds >= hatchling>=1.27 and hatch-vcs>=0.4."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping build-system pin assertions")
        cls.data = _load_pyproject()
        cls.requires = cls.data.get("build-system", {}).get("requires", [])

    def _find_entry(self, pkg_name):
        """Return the first requires entry whose text contains pkg_name, or None."""
        return next(
            (r for r in self.requires if isinstance(r, str) and pkg_name in r),
            None,
        )

    def test_hatchling_entry_has_version_constraint(self):
        """AC2: hatchling entry in [build-system].requires must not be bare (must carry a constraint)."""
        entry = self._find_entry("hatchling")
        self.assertIsNotNone(entry, "[build-system].requires must contain a hatchling entry")
        # A bare entry equals exactly "hatchling" (no constraint characters)
        self.assertNotEqual(
            entry.strip(),
            "hatchling",
            f"[build-system].requires hatchling entry must carry a version constraint; got {entry!r}",
        )

    def test_hatch_vcs_entry_has_version_constraint(self):
        """AC2: hatch-vcs entry in [build-system].requires must not be bare (must carry a constraint)."""
        entry = self._find_entry("hatch-vcs")
        self.assertIsNotNone(entry, "[build-system].requires must contain a hatch-vcs entry")
        self.assertNotEqual(
            entry.strip(),
            "hatch-vcs",
            f"[build-system].requires hatch-vcs entry must carry a version constraint; got {entry!r}",
        )

    def test_hatchling_lower_bound_is_at_least_1_27(self):
        """AC2: hatchling lower bound must be >= 1.27 (PEP 639 / core-metadata-2.4 floor)."""
        import re
        entry = self._find_entry("hatchling")
        self.assertIsNotNone(entry, "[build-system].requires must contain a hatchling entry")
        # Accept any specifier that includes >=X.Y where X.Y >= 1.27 via substring check.
        # We look for ">=1.27" or any higher floor (>=1.28, >=2, etc.) as a string match.
        # The simplest and most tolerant check: assert the entry contains ">=1.27" or
        # a version number that is numerically >= 1.27 after ">=".
        m = re.search(r">=\s*(\d+\.\d+)", entry)
        self.assertIsNotNone(
            m,
            f"hatchling entry must include a '>=' lower bound; got {entry!r}",
        )
        floor = tuple(int(x) for x in m.group(1).split("."))
        self.assertGreaterEqual(
            floor,
            (1, 27),
            f"hatchling lower bound must be >= 1.27 (PEP 639 floor); got {entry!r}",
        )

    def test_hatch_vcs_lower_bound_is_at_least_0_4(self):
        """AC2: hatch-vcs lower bound must be >= 0.4."""
        import re
        entry = self._find_entry("hatch-vcs")
        self.assertIsNotNone(entry, "[build-system].requires must contain a hatch-vcs entry")
        m = re.search(r">=\s*(\d+\.\d+)", entry)
        self.assertIsNotNone(
            m,
            f"hatch-vcs entry must include a '>=' lower bound; got {entry!r}",
        )
        floor = tuple(int(x) for x in m.group(1).split("."))
        self.assertGreaterEqual(
            floor,
            (0, 4),
            f"hatch-vcs lower bound must be >= 0.4; got {entry!r}",
        )


class GranularClassifiersTest(unittest.TestCase):
    """AC3 — classifiers must include granular Python minor-version trove classifiers 3.10–3.13."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PYPROJECT_PATH):
            raise unittest.SkipTest("pyproject.toml missing — skipping classifier assertions")
        cls.data = _load_pyproject()
        cls.classifiers = cls.data.get("project", {}).get("classifiers", [])

    def test_classifier_python_310(self):
        """AC3: classifiers includes 'Programming Language :: Python :: 3.10'."""
        self.assertIn(
            "Programming Language :: Python :: 3.10",
            self.classifiers,
            f"classifiers must include 'Programming Language :: Python :: 3.10'; got {self.classifiers!r}",
        )

    def test_classifier_python_311(self):
        """AC3: classifiers includes 'Programming Language :: Python :: 3.11'."""
        self.assertIn(
            "Programming Language :: Python :: 3.11",
            self.classifiers,
            f"classifiers must include 'Programming Language :: Python :: 3.11'; got {self.classifiers!r}",
        )

    def test_classifier_python_312(self):
        """AC3: classifiers includes 'Programming Language :: Python :: 3.12'."""
        self.assertIn(
            "Programming Language :: Python :: 3.12",
            self.classifiers,
            f"classifiers must include 'Programming Language :: Python :: 3.12'; got {self.classifiers!r}",
        )

    def test_classifier_python_313(self):
        """AC3: classifiers includes 'Programming Language :: Python :: 3.13'."""
        self.assertIn(
            "Programming Language :: Python :: 3.13",
            self.classifiers,
            f"classifiers must include 'Programming Language :: Python :: 3.13'; got {self.classifiers!r}",
        )


class PyTypedAbsentGuardTest(unittest.TestCase):
    """AC5 (guard) — sandesh/py.typed must NOT exist (rejected audit item P3)."""

    def test_py_typed_does_not_exist(self):
        """AC5: sandesh/py.typed must NOT be present (not a library distributing type stubs)."""
        py_typed_path = os.path.join(_REPO_ROOT, "sandesh", "py.typed")
        self.assertFalse(
            os.path.exists(py_typed_path),
            f"sandesh/py.typed must NOT exist (audit item P3 rejected); found at {py_typed_path}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
