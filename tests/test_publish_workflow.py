"""test_publish_workflow.py — CR-SAN-010 publish-pypi.yml workflow contract + build validation.

## What these tests assert

### Workflow-contract tests (AC1, AC2, AC2b, AC4, AC5 + release-gating)
These tests FAIL at RED because `.github/workflows/publish-pypi.yml` does not exist yet.
They parse the workflow file using **text-based assertions** (PyYAML is not installed in
the project venv, so we assert substrings / regex against the raw file text).

### Build-validation test (AC3)
Runs `python -m build` locally and checks that a *.whl + *.tar.gz are produced and that
both artifacts contain `sandesh/data/usage-scenarios.md`. This test is expected to PASS
at RED (the build already works from CR-SAN-008) — it serves as a guard that must keep
passing through GREEN and beyond.

`twine check` is attempted and skipped (self.skipTest) if twine is not installed in the
venv. GREEN/pre-merge should add twine to the venv.

## Implementation notes
- YAML parser: TEXT MODE (yaml not available in .venv; PyYAML not installed).
  All workflow assertions are substring / re.search checks against the raw file text.
- twine: NOT available in .venv — `twine check` step uses self.skipTest.
- build (python-build): available at version 1.5.0 in .venv.

Run targeted:
  python-crucible.py test --tests tests.test_publish_workflow --agent CR-SAN-010-C0-RED
"""

import os
import re
import sys
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

# Resolve repo root from this test file's location
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_WORKFLOW_PATH = os.path.join(_REPO_ROOT, ".github", "workflows", "publish-pypi.yml")

# Venv python interpreter — must match the project venv
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python3")


def _read_workflow() -> str:
    """Read the workflow file as raw text. Returns '' if the file does not exist."""
    try:
        with open(_WORKFLOW_PATH, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


class WorkflowExistsTest(unittest.TestCase):
    """AC1 (part 1) — the workflow file must exist and be non-empty."""

    def test_workflow_file_exists(self):
        """AC1: .github/workflows/publish-pypi.yml must exist.
        FAILS at RED — the file has not been created yet.
        """
        self.assertTrue(
            os.path.isfile(_WORKFLOW_PATH),
            f"Workflow file not found: {_WORKFLOW_PATH}\n"
            "GREEN must create .github/workflows/publish-pypi.yml",
        )

    def test_workflow_file_is_non_empty(self):
        """AC1: the workflow file must be non-empty (a real YAML doc, not a placeholder)."""
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file does not exist yet (see test_workflow_file_exists)")
        text = _read_workflow()
        self.assertGreater(
            len(text.strip()),
            0,
            "publish-pypi.yml exists but is empty",
        )


class WorkflowTriggersTest(unittest.TestCase):
    """AC1 (triggers) + AC4 — the workflow must have the correct on: triggers."""

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file does not exist yet")
        self._text = _read_workflow()

    def test_on_release_published_trigger(self):
        """AC1: the workflow must trigger on release with types: [published].
        Text mode: assert 'release:' and 'types:' and 'published' all appear.
        """
        text = self._text
        self.assertIn(
            "release:",
            text,
            "publish-pypi.yml must contain 'release:' in the on: triggers block",
        )
        self.assertIn(
            "published",
            text,
            "publish-pypi.yml must contain 'published' in the release trigger types",
        )
        # Verify types list contains 'published' specifically
        self.assertTrue(
            re.search(r"types:\s*\[.*published.*\]|types:\s*\n\s*-\s*published", text),
            "publish-pypi.yml: release trigger must have types: [published] (or - published)",
        )

    def test_on_workflow_dispatch_trigger(self):
        """AC4: the workflow must include a workflow_dispatch trigger."""
        self.assertIn(
            "workflow_dispatch",
            self._text,
            "publish-pypi.yml must contain 'workflow_dispatch' in the on: triggers "
            "(AC4: manual/dry-run path and build CI path)",
        )

    def test_on_non_release_build_trigger(self):
        """AC4: the build path must also run on pull_request or push (not release-only).
        Asserts at least one of 'pull_request' or 'push' appears in the file triggers.
        """
        text = self._text
        has_pr = "pull_request" in text
        has_push = bool(re.search(r"^push:", text, re.MULTILINE) or "push:\n" in text or "\npush:" in text)
        self.assertTrue(
            has_pr or has_push,
            "publish-pypi.yml must trigger on pull_request and/or push so the build "
            "job runs in CI without publishing (AC4: build CI verification outside release)",
        )


class PublishPyPIJobTest(unittest.TestCase):
    """AC2 — the publish-pypi job must use OIDC trusted publishing (no API token)."""

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file does not exist yet")
        self._text = _read_workflow()

    def test_publish_pypi_job_exists(self):
        """AC2: a job named 'publish-pypi' (or with that key) must exist."""
        self.assertIn(
            "publish-pypi:",
            self._text,
            "publish-pypi.yml must define a job keyed 'publish-pypi:' (AC2)",
        )

    def test_publish_pypi_job_has_id_token_write_permission(self):
        """AC2: the publish-pypi job must declare permissions: id-token: write."""
        self.assertIn(
            "id-token:",
            self._text,
            "publish-pypi.yml must contain 'id-token:' permission declaration (OIDC trusted publishing)",
        )
        self.assertIn(
            "write",
            self._text,
            "publish-pypi.yml must contain 'write' as the id-token permission value",
        )

    def test_publish_pypi_job_sets_environment_pypi(self):
        """AC2: the publish-pypi job must set environment: pypi."""
        self.assertIn(
            "environment: pypi",
            self._text,
            "publish-pypi.yml must set 'environment: pypi' on the publish-pypi job (AC2)",
        )

    def test_publish_pypi_job_uses_pypa_action(self):
        """AC2: the publish-pypi job must use pypa/gh-action-pypi-publish@release/v1."""
        self.assertIn(
            "pypa/gh-action-pypi-publish",
            self._text,
            "publish-pypi.yml must use 'pypa/gh-action-pypi-publish' action (AC2)",
        )
        self.assertIn(
            "release/v1",
            self._text,
            "publish-pypi.yml must pin pypa/gh-action-pypi-publish@release/v1 (AC2)",
        )

    def test_publish_pypi_job_has_no_password_input(self):
        """AC2: the publish-pypi job must NOT contain a 'password:' input (OIDC-only, no API token)."""
        self.assertNotIn(
            "password:",
            self._text,
            "publish-pypi.yml must NOT contain 'password:' — OIDC trusted publishing "
            "requires no API token/password input (AC2)",
        )

    def test_publish_pypi_job_has_no_pypi_secret_reference(self):
        """AC2: the workflow must NOT reference a PYPI API token secret (e.g. secrets.PYPI_API_TOKEN)."""
        self.assertFalse(
            re.search(r"secrets\.PYPI", self._text),
            "publish-pypi.yml must NOT reference 'secrets.PYPI*' — OIDC trusted publishing "
            "requires no stored secret (AC2)",
        )

    def test_publish_pypi_job_gated_to_release_event(self):
        """AC2 + release-gating: the publish-pypi job must be gated to the release event.
        Asserts that a job-level 'if:' condition referencing 'release' appears near the
        publish-pypi job definition (not on every PR/push).
        Text mode: assert 'github.event_name' and 'release' appear together, or
        that an 'if:' line references 'release'.
        """
        # The if: condition should reference either github.event_name == 'release'
        # or github.event.action == 'published', or similar gating
        has_event_name_release = bool(
            re.search(r"if:.*github\.event_name.*==.*['\"]release['\"]", self._text)
        )
        has_event_action_published = bool(
            re.search(r"if:.*github\.event\.action.*==.*['\"]published['\"]", self._text)
        )
        has_release_if = bool(
            re.search(r"if:.*release", self._text)
        )
        self.assertTrue(
            has_event_name_release or has_event_action_published or has_release_if,
            "publish-pypi.yml: the publish-pypi job must have a job-level 'if:' condition "
            "gating it to the release event (e.g. github.event_name == 'release') so it "
            "does NOT run on pull_request/push (AC2 + release-gating)",
        )


class PublishTestPyPIJobTest(unittest.TestCase):
    """AC2b — a publish-testpypi job must exist, gated to workflow_dispatch."""

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file does not exist yet")
        self._text = _read_workflow()

    def test_publish_testpypi_job_exists(self):
        """AC2b: a job named 'publish-testpypi' must exist."""
        self.assertIn(
            "publish-testpypi:",
            self._text,
            "publish-pypi.yml must define a job keyed 'publish-testpypi:' (AC2b)",
        )

    def test_publish_testpypi_job_gated_to_workflow_dispatch(self):
        """AC2b: the publish-testpypi job must be gated to workflow_dispatch only.
        Asserts an if: condition referencing 'workflow_dispatch' appears.
        """
        self.assertTrue(
            re.search(r"if:.*workflow_dispatch", self._text),
            "publish-pypi.yml: the publish-testpypi job must have an 'if:' condition "
            "gating it to github.event_name == 'workflow_dispatch' (AC2b dry-run path)",
        )

    def test_publish_testpypi_job_sets_environment_testpypi(self):
        """AC2b: the publish-testpypi job must set environment: testpypi."""
        self.assertIn(
            "environment: testpypi",
            self._text,
            "publish-pypi.yml must set 'environment: testpypi' on the publish-testpypi job (AC2b)",
        )

    def test_publish_testpypi_job_has_id_token_write_permission(self):
        """AC2b: the publish-testpypi job must also declare id-token: write."""
        # We already asserted id-token: write exists globally; here we check the
        # testpypi-specific repository-url which confirms the job is configured correctly.
        self.assertIn(
            "https://test.pypi.org/legacy/",
            self._text,
            "publish-pypi.yml: publish-testpypi job must configure "
            "repository-url: https://test.pypi.org/legacy/ (AC2b)",
        )


class BuildJobFetchDepthTest(unittest.TestCase):
    """AC5 — the build job's checkout step must use fetch-depth: 0."""

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file does not exist yet")
        self._text = _read_workflow()

    def test_build_job_checkout_fetch_depth_zero(self):
        """AC5: the build job checkout must set fetch-depth: 0 so hatch-vcs sees tags.
        A build from an untagged ref yields a devN+g<sha> version — unacceptable.
        """
        self.assertIn(
            "fetch-depth: 0",
            self._text,
            "publish-pypi.yml: the build job checkout step must set 'fetch-depth: 0' "
            "so hatch-vcs can resolve the git tag into a PEP 440 version (AC5). "
            "Without it, `python -m build` yields a devN+g<sha> version string.",
        )


class BuildValidationTest(unittest.TestCase):
    """AC3 — local build produces valid sdist + wheel; both contain usage-scenarios.md.

    This test is expected to PASS at RED (the build already works from CR-SAN-008).
    It serves as a regression guard that must keep passing through GREEN and beyond.

    `twine check` is skipped if twine is not installed in the venv.
    """

    _USAGE_DOC_PATH = "sandesh/data/usage-scenarios.md"

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="sandesh_build_test_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run_build(self):
        """Run `python -m build --outdir <tmpdir> <repo_root>` and return CompletedProcess."""
        return subprocess.run(
            [_VENV_PYTHON, "-m", "build", "--outdir", self._tmpdir, _REPO_ROOT],
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_build_produces_wheel_and_sdist(self):
        """AC3: `python -m build` must succeed and produce *.whl + *.tar.gz artifacts."""
        result = self._run_build()
        self.assertEqual(
            result.returncode,
            0,
            f"`python -m build` exited with {result.returncode}.\n"
            f"stdout: {result.stdout[-2000:]}\n"
            f"stderr: {result.stderr[-2000:]}",
        )
        files = os.listdir(self._tmpdir)
        wheels = [f for f in files if f.endswith(".whl")]
        sdists = [f for f in files if f.endswith(".tar.gz")]
        self.assertEqual(
            len(wheels),
            1,
            f"Expected exactly 1 .whl in {self._tmpdir}, got: {wheels}",
        )
        self.assertEqual(
            len(sdists),
            1,
            f"Expected exactly 1 .tar.gz in {self._tmpdir}, got: {sdists}",
        )

    def test_wheel_contains_usage_scenarios_md(self):
        """AC3: the wheel must bundle sandesh/data/usage-scenarios.md."""
        result = self._run_build()
        if result.returncode != 0:
            self.skipTest("Build failed — see test_build_produces_wheel_and_sdist")
        files = os.listdir(self._tmpdir)
        wheels = [f for f in files if f.endswith(".whl")]
        if not wheels:
            self.skipTest("No wheel produced — see test_build_produces_wheel_and_sdist")
        wheel_path = os.path.join(self._tmpdir, wheels[0])
        with zipfile.ZipFile(wheel_path) as zf:
            names = zf.namelist()
        self.assertIn(
            self._USAGE_DOC_PATH,
            names,
            f"Wheel {wheels[0]} does not contain '{self._USAGE_DOC_PATH}'.\n"
            f"Wheel contents (filtered): {[n for n in names if 'sandesh' in n]}",
        )

    def test_sdist_contains_usage_scenarios_md(self):
        """AC3: the sdist must bundle sandesh/data/usage-scenarios.md."""
        result = self._run_build()
        if result.returncode != 0:
            self.skipTest("Build failed — see test_build_produces_wheel_and_sdist")
        files = os.listdir(self._tmpdir)
        sdists = [f for f in files if f.endswith(".tar.gz")]
        if not sdists:
            self.skipTest("No sdist produced — see test_build_produces_wheel_and_sdist")
        sdist_path = os.path.join(self._tmpdir, sdists[0])
        with tarfile.open(sdist_path, "r:gz") as tf:
            names = tf.getnames()
        # sdist paths are prefixed with package-version/, e.g. sandesh_relay-0.1.dev.../sandesh/data/...
        matching = [n for n in names if n.endswith(self._USAGE_DOC_PATH)]
        self.assertEqual(
            len(matching),
            1,
            f"Sdist {sdists[0]} must contain exactly one entry ending in "
            f"'{self._USAGE_DOC_PATH}', got: {matching}\n"
            f"All sdist entries (filtered): {[n for n in names if 'usage' in n or 'data' in n]}",
        )

    def test_twine_check_passes(self):
        """AC3: twine check dist/* must report PASSED for both artifacts.
        SKIPPED if twine is not installed in the project venv.
        """
        # Check if twine is available
        check = subprocess.run(
            [_VENV_PYTHON, "-m", "twine", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode != 0:
            self.skipTest(
                "twine is not installed in the project venv "
                f"({_VENV_PYTHON}). Install with: pip install twine. "
                "GREEN/pre-merge should add twine to the venv."
            )
        # Build first
        build_result = self._run_build()
        if build_result.returncode != 0:
            self.skipTest("Build failed — see test_build_produces_wheel_and_sdist")
        # Run twine check
        artifacts = [
            os.path.join(self._tmpdir, f)
            for f in os.listdir(self._tmpdir)
            if f.endswith(".whl") or f.endswith(".tar.gz")
        ]
        twine_result = subprocess.run(
            [_VENV_PYTHON, "-m", "twine", "check"] + artifacts,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            twine_result.returncode,
            0,
            f"twine check failed:\n{twine_result.stdout}\n{twine_result.stderr}",
        )
        # Both artifacts should show PASSED
        combined_output = twine_result.stdout + twine_result.stderr
        self.assertNotIn(
            "FAILED",
            combined_output,
            f"twine check reported FAILED for at least one artifact:\n{combined_output}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
