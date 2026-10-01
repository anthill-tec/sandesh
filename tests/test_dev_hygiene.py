"""CR-SAN-049 checks for dev dependency floors and isolated test-store runs."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import tests._store_guard as guard  # noqa: E402,F401

import importlib.metadata  # noqa: E402
import shlex  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import tomllib  # noqa: E402
import unittest  # noqa: E402

from packaging.requirements import InvalidRequirement, Requirement  # noqa: E402
from packaging.version import Version  # noqa: E402

_REPRESENTATIVE_FILES = (
    "tests/test_sandesh.py",
    "tests/test_global_store.py",
    "tests/test_axi_verbs.py",
)
_PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")
_PUBLISH_WORKFLOW_PATH = os.path.join(_REPO_ROOT, ".github", "workflows", "publish-pypi.yml")
_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable


def _load_pyproject():
    with open(_PYPROJECT_PATH, "rb") as fh:
        return tomllib.load(fh)


def _req_name(req_str):
    return Requirement(req_str).name


def _floor_at_least(req_str, floor):
    req = Requirement(req_str)
    floor_v = Version(floor)
    return any(spec.operator == ">=" and Version(spec.version) >= floor_v for spec in req.specifier)


def _installed_version_at_least(package, floor):
    version = importlib.metadata.version(package)
    return Version(version) >= Version(floor), version


def _workflow_job_lines(text, job):
    lines = text.splitlines()
    try:
        start = lines.index(f"  {job}:")
    except ValueError:
        return []
    body = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith("   "):
            break
        body.append(line)
    return body


def _workflow_run_commands(job_lines):
    commands, i = [], 0
    while i < len(job_lines):
        line = job_lines[i]
        if line.strip() != "run: |":
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        i += 1
        while i < len(job_lines):
            nxt = job_lines[i]
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            argv = shlex.split(nxt, comments=True)
            if argv:
                commands.append(argv)
            i += 1
    return commands


def _pip_install_requirements(commands):
    reqs = []
    for argv in commands:
        if "pip" not in argv or "install" not in argv:
            continue
        for token in argv[argv.index("install") + 1:]:
            if token.startswith("-"):
                continue
            try:
                reqs.append(Requirement(token))
            except InvalidRequirement:
                continue
    return reqs


class DevExtraFloorsTest(unittest.TestCase):
    def setUp(self):
        self.optional = _load_pyproject().get("project", {}).get("optional-dependencies", {})

    def test_dev_extra_contains_required_packages_and_floors(self):
        dev = self.optional.get("dev", [])
        by_name = {_req_name(req): req for req in dev}
        required = ("twine", "packaging", "build", "unittest-xml-reporting", "coverage")
        self.assertEqual([name for name in required if name not in by_name], [])
        self.assertTrue(_floor_at_least(by_name["twine"], "7.0"))
        self.assertTrue(_floor_at_least(by_name["packaging"], "26.3"))

    def test_mcp_and_migrate_extras_remain_unchanged(self):
        self.assertEqual(self.optional.get("mcp"), ["mcp>=1.27,<2"])
        self.assertEqual(self.optional.get("migrate"), ["yoyo-migrations>=9,<10", "jsonschema>=4.26"])


class DevVenvSatisfiesFloorsTest(unittest.TestCase):
    def test_installed_twine_and_packaging_meet_declared_floors(self):
        for package, floor in (("twine", "7.0"), ("packaging", "26.3")):
            ok, version = _installed_version_at_least(package, floor)
            self.assertTrue(ok, f"installed {package} {version} is below >={floor}")


class CiTwineFloorPinnedTest(unittest.TestCase):
    def setUp(self):
        with open(_PUBLISH_WORKFLOW_PATH, encoding="utf-8") as fh:
            workflow = fh.read()
        self.commands = _workflow_run_commands(_workflow_job_lines(workflow, "build"))
        self.assertTrue(self.commands)

    def test_build_job_pins_twine_floor(self):
        twine = [req for req in _pip_install_requirements(self.commands) if req.name == "twine"]
        self.assertEqual(len(twine), 1)
        spec = twine[0].specifier
        self.assertTrue(spec.contains("7.0"))
        self.assertFalse(spec.contains("6.2.0"))

    def test_build_job_runs_twine_check(self):
        self.assertIn(["twine", "check", "dist/*"], self.commands)


class RepresentativeSuitesIsolatedStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._private_tmp = tempfile.TemporaryDirectory(prefix="hygiene-private-tmp-")
        cls._isolated_xdg = tempfile.TemporaryDirectory(
            prefix="isolated-sandesh-hygiene-", dir=cls._private_tmp.name)
        env = dict(os.environ)
        env["TMPDIR"] = cls._private_tmp.name
        env["XDG_DATA_HOME"] = cls._isolated_xdg.name
        cls._results = {}
        for relative_path in _REPRESENTATIVE_FILES:
            path = os.path.join(_REPO_ROOT, relative_path)
            cls._results[relative_path] = subprocess.run(
                [_SUBPROCESS_PYTHON, path], cwd=_REPO_ROOT, env=env,
                capture_output=True, text=True, timeout=120,
            )

    @classmethod
    def tearDownClass(cls):
        cls._isolated_xdg.cleanup()
        cls._private_tmp.cleanup()

    def test_representative_suites_pass_with_explicit_isolated_store(self):
        failures = {
            path: (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
            for path, proc in self._results.items()
            if proc.returncode != 0
        }
        self.assertEqual(failures, {})

    def test_representative_suites_leave_no_guard_temp_directories(self):
        leftovers = [
            name for name in os.listdir(self._private_tmp.name)
            if name != os.path.basename(self._isolated_xdg.name)
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
