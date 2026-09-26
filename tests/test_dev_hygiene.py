"""test_dev_hygiene.py — RED tests for CR-SAN-049 cycle 211 (§S1, §S3, §S3b,
AC1, AC3, AC4, AC5), against
docs/changes/CR-SAN-049-dev-tooling-pins-and-store-guard.md.

Target contract (not-yet-built by GREEN):
  * §S1/AC1 — pyproject.toml gains `[project.optional-dependencies] dev =
    ["twine>=7.0", "packaging>=26.3", "build>=1.2",
    "unittest-xml-reporting>=3.2", "coverage>=7.6"]`; `[mcp]`/`[migrate]`
    stay byte-for-byte unchanged; the dev venv (once reinstalled with
    `pip install -e '.[mcp,migrate,dev]'`) reports twine>=7.0/packaging>=26.3.
  * §S1/AC5 — `.github/workflows/publish-pypi.yml`'s `build` job pins
    `twine>=7.0` explicitly and still runs `twine check dist/*`.
  * §S3/AC3 — every `tests/test_*.py` imports `tests._store_guard` (a plain
    `import tests._store_guard`, alias allowed) before any other
    non-bootstrap top-level import (a leading `import sys`/`import os`/
    `import pathlib` + `sys.path.insert(...)` block is allowed ahead of it);
    every `subprocess.run/Popen/check_output/check_call/call` call whose
    argv mentions `sandesh` passes an `env=` that carries `XDG_DATA_HOME`.
  * §S3b — `sandesh/axi.py` imports the vendored TOON module via
    `import sandesh._toon as _toon`, never `from sandesh import _toon`.
  * AC4 (this cycle's slice) — three representative test files
    (test_sandesh.py, test_global_store.py, test_axi_verbs.py), each run
    standalone in a subprocess with `XDG_DATA_HOME` REMOVED from the env,
    exit 0, and the shared real store (`~/.local/share/sandesh/sandesh.db`
    + its `projects/` dir) is left byte-for-byte unchanged — plus no
    `sandesh-*` temp dir leaks under the system temp root afterwards.

INTERPRETATION CHOICES (documented per dispatch prompt):
  1. Import-first scan: only `import sys`, `import os`, `import pathlib`
     (as bare `Import` nodes) and `__future__` imports are allowed ahead of
     `import tests._store_guard`; a `sys.path.insert(...)` call is not an
     `Import` node at all so it never interrupts the scan. A
     `from tests import _store_guard` form does NOT satisfy the contract —
     the spec requires the `import tests._store_guard` binding form
     specifically (matches what `tests/test_store_guard.py` already does).
  2. Subprocess env scan: "argv mentions sandesh" is decided (a) from the
     first positional arg if it `ast.literal_eval`s to a str/list/tuple
     containing the substring "sandesh" (catches literal module paths like
     `"sandesh.cli"` and spawned-test-module names like `"tests.test_sandesh"`
     — the latter is a deliberate over-match: a subprocess that re-runs a
     *sandesh* test module is still "spawning sandesh" in the sense this
     scan cares about), else (b) best-effort from the whole call's own
     source segment (`ast.get_source_segment`) — this means a call built
     from a `code = "...sandesh..."` string assembled in *earlier*
     statements (e.g. `test_mcp_missing_extra.py`'s `sys.executable -c code`
     snippets) is NOT flagged, because the "sandesh" mention never appears
     in the call statement's own source. `env=` satisfies the rule if its
     own source segment contains "XDG_DATA_HOME" literally, or — for a
     `env=some_name` reference — if the literal text "XDG_DATA_HOME" appears
     anywhere in the 30 source lines immediately above the call (best
     effort per dispatch prompt; a `self.env` built far above the call, as
     in `test_axi_notify.py`, will NOT be picked up by this window and is
     therefore reported as an offender — a conservative false positive the
     dispatch prompt explicitly anticipated and authorized).
  3. This module's OWN AC4 harness (`RepresentativeSuiteXdgUnsetTest`) is
     EXCLUDED from the subprocess-env scan: its whole purpose is to spawn
     representative test files with `XDG_DATA_HOME` deliberately ABSENT
     from the env, to prove the in-process guard supplies it without the
     caller's help — flagging that call as a "violation" of the same rule
     it is testing would be self-contradictory. It is not excluded from the
     import-first scan (this file's own imports are guard-first-compliant).

Expected RED (against current code, before GREEN):
  DevExtraFloorsTest                    -> FAIL (no `dev` extra in pyproject.toml yet)
  DevVenvSatisfiesFloorsTest             -> FAIL (installed twine 6.2.0 < 7.0,
                                              packaging 26.2 < 26.3)
  CiTwineFloorPinnedTest.*pins_twine_floor -> FAIL (no explicit twine>=7.0 in
                                              publish-pypi.yml yet)
  CiTwineFloorPinnedTest.*runs_twine_check -> PASS (already there; a pin)
  ImportGuardFirstTest                   -> FAIL (today ZERO test files import
                                              tests._store_guard as their
                                              first non-bootstrap import —
                                              ~64 offenders, all but this file)
  SubprocessXdgEnvGuardTest              -> FAIL (a handful of offenders —
                                              test_axi_notify.py x2,
                                              test_migrate.py x2,
                                              test_user_guide.py x1, per a
                                              live scan against this branch)
  RepresentativeSuiteXdgUnsetTest        -> PASS (a pin — the three files
                                              already manage their own temp
                                              store in setUp, independent of
                                              ambient XDG_DATA_HOME)
  AxiImportFormTest                      -> FAIL (axi.py still says
                                              `from sandesh import _toon`)

Run targeted (Crucible client resolves the venv interpreter):
  python3 ~/Documents/data_projects/crucible/clients/python-crucible.py \\
      test --tests tests.test_dev_hygiene --agent CR-SAN-049-C2-RED
"""

import sys
import os

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import tests._store_guard as guard  # noqa: E402,F401 — AC3: first import, re-points XDG_DATA_HOME to tmpfs

import ast  # noqa: E402
import glob  # noqa: E402
import importlib.metadata  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import tomllib  # noqa: E402
import unittest  # noqa: E402

from packaging.requirements import Requirement  # noqa: E402
from packaging.version import Version  # noqa: E402

_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

_PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")
_PUBLISH_WORKFLOW_PATH = os.path.join(_REPO_ROOT, ".github", "workflows", "publish-pypi.yml")
_AXI_PATH = os.path.join(_REPO_ROOT, "sandesh", "axi.py")

# --------------------------------------------------------------------------- #
# AC1 helpers — requirement-string parsing (packaging.requirements/version;
# always available here since `packaging` is a hard dependency of this test's
# own AC1 assertions — no regex fallback needed).

def _load_pyproject():
    with open(_PYPROJECT_PATH, "rb") as fh:
        return tomllib.load(fh)


def _req_name(req_str):
    return Requirement(req_str).name


def _floor_at_least(req_str, floor):
    """True if `req_str` (a PEP 508 requirement string) declares a `>=` floor
    that is itself >= `floor`."""
    req = Requirement(req_str)
    floor_v = Version(floor)
    return any(
        spec.operator == ">=" and Version(spec.version) >= floor_v
        for spec in req.specifier
    )


def _installed_version_at_least(pkg, floor):
    version = importlib.metadata.version(pkg)
    return Version(version) >= Version(floor), version


# --------------------------------------------------------------------------- #
# AC3 helpers — the import-first scan + the subprocess env scan

_BOOTSTRAP_IMPORT_MODULES = {"sys", "os", "pathlib"}
_SUBPROC_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


def _test_files(exclude_self=False):
    paths = sorted(glob.glob(os.path.join(_REPO_ROOT, "tests", "test_*.py")))
    if exclude_self:
        paths = [p for p in paths if os.path.basename(p) != os.path.basename(__file__)]
    return paths


def _import_scan_offender(path):
    """Return None if `path` is AC3-compliant (its first non-bootstrap
    top-level import is exactly `import tests._store_guard`), else a
    human-readable reason string."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "__future__" or mod in _BOOTSTRAP_IMPORT_MODULES:
                continue
            return f"line {node.lineno}: first non-bootstrap import is `from {mod} import ...`, expected `import tests._store_guard`"
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
            if all(n in _BOOTSTRAP_IMPORT_MODULES for n in names):
                continue
            if names == ["tests._store_guard"]:
                return None
            return f"line {node.lineno}: first non-bootstrap import is `import {', '.join(names)}`, expected `import tests._store_guard`"
    return "no non-bootstrap top-level import found — never imports tests._store_guard"


def _call_mentions_sandesh(source, call_node):
    args = call_node.args
    if args:
        first = args[0]
        literal = None
        if isinstance(first, (ast.List, ast.Tuple)):
            try:
                literal = ast.literal_eval(first)
            except Exception:
                literal = None
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            literal = first.value
        if literal is not None:
            text = " ".join(literal) if isinstance(literal, (list, tuple)) else literal
            if "sandesh" in text:
                return True
    seg = ast.get_source_segment(source, call_node) or ""
    return "sandesh" in seg


def _env_keyword_ok(source, lines, call_node):
    for kw in call_node.keywords:
        if kw.arg == "env":
            seg = ast.get_source_segment(source, kw.value) or ""
            if "XDG_DATA_HOME" in seg:
                return True
            start = max(0, call_node.lineno - 31)
            window = "\n".join(lines[start:call_node.lineno])
            return "XDG_DATA_HOME" in window
    return False


def _subprocess_env_offenders(path):
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    lines = source.splitlines()
    tree = ast.parse(source, filename=path)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROC_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        if not _call_mentions_sandesh(source, node):
            continue
        if not _env_keyword_ok(source, lines, node):
            offenders.append(
                f"line {node.lineno}: subprocess.{func.attr}(...) spawns sandesh without XDG_DATA_HOME in env="
            )
    return offenders


# --------------------------------------------------------------------------- #
# AC4 helpers — the real (shared) store + the representative-file run

_REAL_STORE_DB = os.path.expanduser("~/.local/share/sandesh/sandesh.db")
_REAL_STORE_PROJECTS = os.path.expanduser("~/.local/share/sandesh/projects")

_REPRESENTATIVE_FILES = [
    os.path.join("tests", "test_sandesh.py"),
    os.path.join("tests", "test_global_store.py"),
    os.path.join("tests", "test_axi_verbs.py"),
]


def _read_real_store_counts():
    """Read-only `project`/`address` row counts of the shared real store, or
    None if the store does not exist on this machine."""
    if not os.path.isfile(_REAL_STORE_DB):
        return None
    con = sqlite3.connect(f"file:{_REAL_STORE_DB}?mode=ro", uri=True)
    try:
        return {
            "project": con.execute("SELECT COUNT(*) FROM project").fetchone()[0],
            "address": con.execute("SELECT COUNT(*) FROM address").fetchone()[0],
        }
    finally:
        con.close()


def _list_real_store_projects():
    if not os.path.isdir(_REAL_STORE_PROJECTS):
        return None
    return sorted(os.listdir(_REAL_STORE_PROJECTS))


def _env_without_xdg():
    env = dict(os.environ)
    env.pop("XDG_DATA_HOME", None)
    return env


# --------------------------------------------------------------------------- #
# §S1 / AC1 — dev extra + floors

class DevExtraFloorsTest(unittest.TestCase):
    """§S1 — pyproject.toml declares a `dev` extra with the five required
    packages, floor-pinning twine>=7.0 and packaging>=26.3; `[mcp]`/
    `[migrate]` stay exactly as they are today."""

    def setUp(self):
        self.pyproject = _load_pyproject()
        self.optdeps = self.pyproject.get("project", {}).get("optional-dependencies", {})

    def test_dev_extra_exists_with_required_packages(self):
        self.assertIn(
            "dev", self.optdeps,
            "pyproject.toml [project.optional-dependencies] has no 'dev' extra yet",
        )
        dev = self.optdeps["dev"]
        by_name = {_req_name(r): r for r in dev}
        required = ("twine", "packaging", "build", "unittest-xml-reporting", "coverage")
        missing = [pkg for pkg in required if pkg not in by_name]
        self.assertEqual(
            missing, [],
            f"dev extra is missing requirement(s) for {missing}; dev={dev!r}",
        )

    def test_dev_extra_pins_twine_and_packaging_floors(self):
        dev = self.optdeps.get("dev", [])
        by_name = {_req_name(r): r for r in dev}
        self.assertIn("twine", by_name, "dev extra has no twine requirement to check a floor on")
        self.assertIn("packaging", by_name, "dev extra has no packaging requirement to check a floor on")
        self.assertTrue(
            _floor_at_least(by_name["twine"], "7.0"),
            f"twine requirement must declare a floor >= 7.0, got {by_name['twine']!r}",
        )
        self.assertTrue(
            _floor_at_least(by_name["packaging"], "26.3"),
            f"packaging requirement must declare a floor >= 26.3, got {by_name['packaging']!r}",
        )

    def test_mcp_and_migrate_extras_unchanged(self):
        self.assertEqual(
            self.optdeps.get("mcp"), ["mcp>=1.27,<2"],
            "the [mcp] extra must stay exactly ['mcp>=1.27,<2'] — §S1 does not touch it",
        )
        self.assertEqual(
            self.optdeps.get("migrate"), ["yoyo-migrations>=9,<10", "jsonschema>=4.26"],
            "the [migrate] extra must stay exactly unchanged — §S1 does not touch it",
        )


class DevVenvSatisfiesFloorsTest(unittest.TestCase):
    """AC1 — the RUNNING interpreter's installed twine/packaging both meet
    the floors (proves `pip install -e '.[mcp,migrate,dev]'` was actually
    run, not just declared in pyproject.toml). Expected RED today: this
    venv carries twine 6.2.0 / packaging 26.2, both below floor."""

    def test_installed_twine_meets_floor(self):
        ok, version = _installed_version_at_least("twine", "7.0")
        self.assertTrue(
            ok,
            f"installed twine {version} is below the >=7.0 floor — "
            "run pip install -e '.[mcp,migrate,dev]'",
        )

    def test_installed_packaging_meets_floor(self):
        ok, version = _installed_version_at_least("packaging", "26.3")
        self.assertTrue(
            ok,
            f"installed packaging {version} is below the >=26.3 floor — "
            "run pip install -e '.[mcp,migrate,dev]'",
        )


# --------------------------------------------------------------------------- #
# §S1 / AC5 — CI twine floor

class CiTwineFloorPinnedTest(unittest.TestCase):
    """AC5 — publish-pypi.yml's `build` job pins `twine>=7.0` explicitly and
    still runs `twine check dist/*` (outcome unchanged)."""

    def setUp(self):
        with open(_PUBLISH_WORKFLOW_PATH, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_build_job_pins_twine_floor(self):
        self.assertRegex(
            self.text, r"twine>=7\.0",
            "publish-pypi.yml's build-job install step must pin twine>=7.0 explicitly",
        )

    def test_build_job_still_runs_twine_check(self):
        self.assertIn(
            "twine check dist/*", self.text,
            "publish-pypi.yml's build job must still run 'twine check dist/*'",
        )


# --------------------------------------------------------------------------- #
# §S3 / AC3 — the import-first scan

class ImportGuardFirstTest(unittest.TestCase):
    """AC3 — every tests/test_*.py imports `tests._store_guard` (a plain
    `import tests._store_guard` statement, alias allowed) before any other
    non-bootstrap top-level import."""

    def test_every_test_file_imports_store_guard_first(self):
        offenders = []
        for path in _test_files():
            reason = _import_scan_offender(path)
            if reason is not None:
                offenders.append(f"{os.path.relpath(path, _REPO_ROOT)} — {reason}")
        offenders.sort()
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} test file(s) do not import tests._store_guard first:\n"
            + "\n".join(offenders),
        )


# --------------------------------------------------------------------------- #
# §S3 / AC3 — the subprocess env scan

class SubprocessXdgEnvGuardTest(unittest.TestCase):
    """§S3 / AC3 — every subprocess.run/Popen/check_output/check_call/call
    call whose argv mentions sandesh must pass an env= carrying
    XDG_DATA_HOME. See the module docstring's interpretation notes for the
    exact "mentions sandesh" / "env carries XDG_DATA_HOME" rules and why
    this module's own AC4 harness is excluded from this particular scan."""

    def test_every_sandesh_spawning_subprocess_call_carries_xdg_data_home(self):
        offenders = []
        for path in _test_files(exclude_self=True):
            for reason in _subprocess_env_offenders(path):
                offenders.append(f"{os.path.relpath(path, _REPO_ROOT)} {reason}")
        offenders.sort()
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} subprocess call(s) spawn sandesh without XDG_DATA_HOME in env=:\n"
            + "\n".join(offenders),
        )


# --------------------------------------------------------------------------- #
# AC4 — representative suite with XDG_DATA_HOME unset + real-store invariant

class RepresentativeSuiteXdgUnsetTest(unittest.TestCase):
    """AC4 — three representative test files, each run standalone in a
    subprocess with XDG_DATA_HOME REMOVED from the env, must exit 0; the
    shared real store's project/address counts and its projects/ directory
    listing must be unchanged before -> after; and no sandesh-* temp
    directory must leak under the system temp root as a result.

    NOTE: today these three files set their own temp store in setUp
    (independent of ambient XDG_DATA_HOME), so the exit-0 assertions are
    expected to PASS already — kept as an invariant pin per the dispatch
    prompt."""

    @classmethod
    def setUpClass(cls):
        cls._start_time = time.time() - 1  # 1s slack for mtime granularity
        cls._before_counts = _read_real_store_counts()
        cls._before_projects = _list_real_store_projects()

        env = _env_without_xdg()
        cls._results = {}
        for rel in _REPRESENTATIVE_FILES:
            path = os.path.join(_REPO_ROOT, rel)
            cls._results[rel] = subprocess.run(
                [_SUBPROCESS_PYTHON, path],
                cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
            )

        cls._after_counts = _read_real_store_counts()
        cls._after_projects = _list_real_store_projects()

    def test_each_representative_file_exits_zero_with_xdg_data_home_unset(self):
        failures = {
            rel: (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
            for rel, proc in self._results.items()
            if proc.returncode != 0
        }
        self.assertEqual(
            failures, {},
            f"representative test file(s) did not exit 0 with XDG_DATA_HOME unset: {failures}",
        )

    def test_real_store_project_and_address_counts_unchanged(self):
        self.assertEqual(
            self._before_counts, self._after_counts,
            "real store project/address row counts changed while running the "
            f"representative suite: before={self._before_counts} after={self._after_counts}",
        )

    def test_real_store_projects_directory_listing_unchanged(self):
        self.assertEqual(
            self._before_projects, self._after_projects,
            f"real store projects/ directory listing changed: before={self._before_projects} "
            f"after={self._after_projects}",
        )

    def test_no_leftover_sandesh_tmp_directory_under_system_temp_root(self):
        root = os.path.realpath(tempfile.gettempdir())
        # This module's own guard dir is live for the whole test run by design
        # (created at import time by `tests._store_guard`) — not a leftover.
        own_guard = os.path.realpath(guard.GUARD_TMP)
        leftover_dirs = [
            p for p in glob.glob(os.path.join(root, "sandesh-*"))
            if os.path.isdir(p) and os.path.getmtime(p) >= self._start_time
            and os.path.realpath(p) != own_guard
        ]
        self.assertEqual(
            leftover_dirs, [],
            f"leftover sandesh-* directories under {root!r} after the representative "
            f"subprocess runs: {leftover_dirs}",
        )


# --------------------------------------------------------------------------- #
# §S3b — axi.py import form

class AxiImportFormTest(unittest.TestCase):
    """§S3b — sandesh/axi.py imports the vendored TOON submodule via
    `import sandesh._toon as _toon` (clears a Pyright "unknown import
    symbol" false positive on underscore submodules), never
    `from sandesh import _toon`."""

    def setUp(self):
        with open(_AXI_PATH, encoding="utf-8") as fh:
            self.source = fh.read()

    def test_uses_import_as_form_not_from_import(self):
        self.assertIn(
            "import sandesh._toon as _toon", self.source,
            "sandesh/axi.py must import the vendored TOON module via "
            "`import sandesh._toon as _toon`",
        )
        self.assertNotIn(
            "from sandesh import _toon", self.source,
            "sandesh/axi.py must not use `from sandesh import _toon` "
            "(the form §S3b replaces)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
