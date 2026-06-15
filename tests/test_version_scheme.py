"""test_version_scheme.py — RED tests for CR-SAN-034 §S1 / AC1.

Asserts that pyproject.toml is configured with
``raw-options = { local_scheme = "no-local-version" }`` so that untagged
builds produce a PEP 440-clean ``X.Y.Z.devN`` version (no ``+`` local
segment) that PyPI/TestPyPI accept on upload.

Two test classes:

VersionSchemeConfigTest (RED driver)
    Reads pyproject.toml via tomllib and asserts that
    [tool.hatch.version] contains raw-options.local_scheme == "no-local-version".
    FAILS NOW because that key is absent.  Passes once GREEN adds it.

VersionSchemeBehaviourTest (behavioural)
    Creates a temp git repo with a v1.2.3 tag, calls
    setuptools_scm.get_version(local_scheme="no-local-version"), and asserts:
      (a) at an exact vX.Y.Z tag  → version is exactly "1.2.3"
      (b) commits past the tag    → version matches ^\\d+\\.\\d+\\.\\d+\\.dev\\d+$
                                    AND contains no "+"
    PASSES NOW (verifies the scheme works; stays GREEN after §S1 is applied).

Run per-file (discovery is broken):
    PYTHONPATH=. .venv/bin/python tests/test_version_scheme.py
or via crucible:
    python3 ~/.claude/scripts/python-crucible.py test \\
        --tests tests.test_version_scheme --agent CR-SAN-034-A-RED
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Locate the repo root (the directory containing pyproject.toml).
# Tests are in <repo>/tests/; __file__ is <repo>/tests/test_version_scheme.py
# ---------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(REPO, "pyproject.toml")

# tomllib is stdlib from Python 3.11+; fall back to tomli for 3.10.
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


class VersionSchemeConfigTest(unittest.TestCase):
    """AC1 (config half) — pyproject.toml must declare local_scheme = "no-local-version".

    This is the RED driver for CR-SAN-034 §S1: the assertion fails until GREEN
    adds ``raw-options = { local_scheme = "no-local-version" }`` under
    ``[tool.hatch.version]``.
    """

    def setUp(self):
        if tomllib is None:
            self.skipTest(
                "tomllib/tomli not available — cannot parse pyproject.toml. "
                "Python 3.11+ has tomllib in stdlib; 3.10 needs 'pip install tomli'."
            )
        self.assertTrue(
            os.path.isfile(PYPROJECT),
            msg=f"pyproject.toml not found at expected path: {PYPROJECT}",
        )

    def _load_pyproject(self):
        with open(PYPROJECT, "rb") as fh:
            return tomllib.load(fh)

    def test_ac1_hatch_version_section_exists(self):
        """[tool.hatch.version] section must exist in pyproject.toml."""
        cfg = self._load_pyproject()
        hatch_version = cfg.get("tool", {}).get("hatch", {}).get("version", None)
        self.assertIsNotNone(
            hatch_version,
            msg="[tool.hatch.version] section is absent from pyproject.toml.",
        )

    def test_ac1_raw_options_key_present(self):
        """[tool.hatch.version] must contain a 'raw-options' table.

        FAILS NOW (RED): pyproject.toml has no raw-options under [tool.hatch.version].
        """
        cfg = self._load_pyproject()
        hatch_version = cfg.get("tool", {}).get("hatch", {}).get("version", {})
        self.assertIn(
            "raw-options",
            hatch_version,
            msg=(
                "'raw-options' key is absent from [tool.hatch.version] in pyproject.toml. "
                "GREEN must add: raw-options = { local_scheme = \"no-local-version\" }"
            ),
        )

    def test_ac1_local_scheme_is_no_local_version(self):
        """raw-options.local_scheme must equal "no-local-version".

        FAILS NOW (RED): the raw-options table doesn't exist yet, so
        local_scheme is also absent.  Passes once GREEN adds both.
        """
        cfg = self._load_pyproject()
        hatch_version = cfg.get("tool", {}).get("hatch", {}).get("version", {})
        raw_options = hatch_version.get("raw-options", {})
        actual = raw_options.get("local_scheme", "<absent>")
        self.assertEqual(
            actual,
            "no-local-version",
            msg=(
                f"[tool.hatch.version].raw-options.local_scheme is {actual!r}; "
                "expected \"no-local-version\". "
                "GREEN must add: raw-options = { local_scheme = \"no-local-version\" } "
                "under [tool.hatch.version] in pyproject.toml."
            ),
        )

    def test_ac1_no_plus_in_current_repo_version(self):
        """After §S1, the version derived from THIS repo must not contain '+'.

        This is a forward-looking guard: once GREEN adds raw-options the
        version derived from the current (untagged hotfix) commit must match
        ^\\d+\\.\\d+\\.\\d+\\.dev\\d+$ with no + local segment.

        FAILS NOW (RED) only if we also assert the config is set; the version
        assertion itself is wrapped to skip gracefully when setuptools_scm is
        unavailable — but the config check above will catch the missing key first.
        """
        cfg = self._load_pyproject()
        hatch_version = cfg.get("tool", {}).get("hatch", {}).get("version", {})
        raw_options = hatch_version.get("raw-options", {})
        local_scheme = raw_options.get("local_scheme", None)

        # Only assert version derivation if the config is in place.
        # (If not, the tests above already fail for the right reason.)
        if local_scheme != "no-local-version":
            self.skipTest(
                "Skipping version-derivation check: raw-options.local_scheme "
                "is not yet 'no-local-version' — the config tests above capture the RED."
            )

        try:
            import setuptools_scm
        except ImportError:
            self.skipTest("setuptools_scm not importable — skipping derivation check.")

        version = setuptools_scm.get_version(
            root=REPO,
            local_scheme="no-local-version",
        )
        self.assertNotIn(
            "+",
            version,
            msg=(
                f"Derived version {version!r} still contains '+' local segment "
                "even after local_scheme='no-local-version'. "
                "This should not happen — investigate setuptools_scm configuration."
            ),
        )
        dev_pattern = re.compile(r"^\d+\.\d+\.\d+\.dev\d+$")
        exact_pattern = re.compile(r"^\d+\.\d+\.\d+$")
        self.assertTrue(
            dev_pattern.match(version) or exact_pattern.match(version),
            msg=(
                f"Derived version {version!r} matches neither "
                r"^\d+\.\d+\.\d+\.dev\d+$ nor ^\d+\.\d+\.\d+$. "
                "Expected a clean PEP 440 version with no local segment."
            ),
        )


class VersionSchemeBehaviourTest(unittest.TestCase):
    """AC1 (behaviour half) — setuptools_scm with local_scheme="no-local-version".

    Uses a temp git repo to verify the chosen scheme emits upload-valid versions:
      (a) commits past a vX.Y.Z tag  → X.Y.(Z+1).devN  (no '+')
      (b) at the exact vX.Y.Z tag    → exactly "X.Y.Z"

    PASSES NOW and must remain GREEN — this documents that the scheme works
    correctly before it is wired into pyproject.toml.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import setuptools_scm as _scm
            cls.scm = _scm
        except ImportError:
            cls.scm = None

        cls.tmp = tempfile.mkdtemp(prefix="sandesh-version-scheme-test-")
        cls._setup_error = None

        if cls.scm is None:
            cls._setup_error = "setuptools_scm not importable"
            return

        try:
            repo = cls.tmp

            def _git(*args):
                result = subprocess.run(
                    ["git", "-C", repo, *args],
                    check=True,
                    capture_output=True,
                )
                return result

            _git("init")
            _git("config", "user.email", "test@sandesh.test")
            _git("config", "user.name", "Sandesh Test")

            # Initial commit + v1.2.3 tag
            init_file = os.path.join(repo, "README.md")
            with open(init_file, "w") as fh:
                fh.write("sandesh version scheme test\n")
            _git("add", ".")
            _git("commit", "-m", "initial commit")
            _git("tag", "v1.2.3")
            cls.version_at_tag = cls.scm.get_version(
                root=repo,
                local_scheme="no-local-version",
            )

            # One more commit past the tag
            extra_file = os.path.join(repo, "extra.txt")
            with open(extra_file, "w") as fh:
                fh.write("extra commit past tag\n")
            _git("add", ".")
            _git("commit", "-m", "commit past v1.2.3 tag")
            cls.version_past_tag = cls.scm.get_version(
                root=repo,
                local_scheme="no-local-version",
            )

        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _require_setup(self):
        if self.scm is None:
            self.skipTest("setuptools_scm not importable.")
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")

    def test_ac1_version_at_exact_tag_is_clean(self):
        """At the exact vX.Y.Z tag, version must be exactly 'X.Y.Z' (no suffix)."""
        self._require_setup()
        self.assertEqual(
            self.version_at_tag,
            "1.2.3",
            msg=(
                f"Version at exact v1.2.3 tag is {self.version_at_tag!r}; "
                "expected exactly '1.2.3'. "
                "local_scheme='no-local-version' must suppress the local segment."
            ),
        )

    def test_ac1_version_past_tag_matches_dev_pattern(self):
        """Past a tag, version must match ^\\d+\\.\\d+\\.\\d+\\.dev\\d+$ (no '+')."""
        self._require_setup()
        dev_pattern = re.compile(r"^\d+\.\d+\.\d+\.dev\d+$")
        self.assertRegex(
            self.version_past_tag,
            dev_pattern,
            msg=(
                f"Version past v1.2.3 tag is {self.version_past_tag!r}; "
                r"expected to match ^\d+\.\d+\.\d+\.dev\d+$ "
                "(e.g. '1.2.4.dev1'). "
                "local_scheme='no-local-version' must strip the '+g...' local segment."
            ),
        )

    def test_ac1_version_past_tag_contains_no_plus(self):
        """Past a tag, the derived version must contain no '+' local segment."""
        self._require_setup()
        self.assertNotIn(
            "+",
            self.version_past_tag,
            msg=(
                f"Version past v1.2.3 tag is {self.version_past_tag!r} — "
                "it still contains a '+' local segment. "
                "PyPI/TestPyPI reject versions with PEP 440 local segments. "
                "local_scheme='no-local-version' must eliminate the '+g...' suffix."
            ),
        )

    def test_ac1_version_past_tag_has_dev_component(self):
        """Past a tag, the derived version must contain '.dev' (marks untagged commit)."""
        self._require_setup()
        self.assertIn(
            ".dev",
            self.version_past_tag,
            msg=(
                f"Version past v1.2.3 tag is {self.version_past_tag!r}; "
                "expected a '.dev' component marking the untagged commit. "
                "The version_scheme default ('guess-next-dev') should produce "
                "e.g. '1.2.4.dev1' when one commit past the tag."
            ),
        )

    def test_ac1_version_past_tag_starts_with_next_patch(self):
        """Past v1.2.3 by one commit, version should start with '1.2.4'."""
        self._require_setup()
        self.assertTrue(
            self.version_past_tag.startswith("1.2.4"),
            msg=(
                f"Version past v1.2.3 tag is {self.version_past_tag!r}; "
                "expected it to start with '1.2.4' (guess-next-dev bumps the patch). "
                f"Full derived version: {self.version_past_tag!r}"
            ),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
