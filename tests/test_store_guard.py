"""test_store_guard.py — RED tests for CR-SAN-049 cycle 210 (§S2, AC2, AC4b),
against docs/changes/CR-SAN-049-dev-tooling-pins-and-store-guard.md.

Target contract — the not-yet-existing `tests/_store_guard.py`:
  * On import: creates ONE per-process `tempfile.TemporaryDirectory(
    prefix="sandesh-tests-")` under `tempfile.gettempdir()`, registers an
    `atexit` cleanup, and re-points `os.environ["XDG_DATA_HOME"]` to it —
    UNLESS the incoming value already resolves under the system temp root
    (respected) or `SANDESH_TESTS_ALLOW_REAL_STORE=1` is set (bypass,
    unchanged). Exposes `GUARD_TMP` (the per-process dir path) and
    `temp_root()` (== `os.path.realpath(tempfile.gettempdir())`).
  * Exposes `TempStore`, a `unittest.TestCase` mixin: `setUp` creates its own
    `tempfile.TemporaryDirectory(prefix="sandesh-<something>-")` under the
    temp root and points `XDG_DATA_HOME` at it; `self.connect()` opens (and
    tracks) a `sandesh_db.connect()` connection; `tearDown` closes tracked
    connections, restores the previous `XDG_DATA_HOME`, and removes the dir
    — even if the test body raised (AC4b).

WHY THIS MATTERS (the memory rule behind §S2): `~/.local/share/sandesh/
sandesh.db` is the ONE global store shared by every running Sandesh project
on this machine (Model B, Crucible, …) — `XDG_DATA_HOME` is exported globally
in this dev shell, so any test that forgets to override it writes into that
shared store. EVERY scenario below therefore runs in a subprocess with a
FULLY-CONTROLLED env (`_minimal_env()` below builds a minimal dict from
scratch — never `{**os.environ, ...}` — so the ambient `XDG_DATA_HOME` can
never leak in by accident), never in-process, and this test module itself
never writes to the real store (test 8's real-store check opens it
STRICTLY read-only via a `file:...?mode=ro` URI).

DESIGN NOTE (deliberate, non-silent deviation from the dispatch prompt's
parenthetical for item 6): the prompt says "import tests._store_guard at the
top of this test file too". Doing that literally — as an unconditional
top-level `import tests._store_guard` — would make the ENTIRE module fail to
import while `tests/_store_guard.py` doesn't exist yet (RED today), which
would prevent unittest from even COLLECTING the other 7 tests (a single
module-load error, not 8 independent results) and would contradict the
dispatch prompt's own expectation that "test 8 may pass — that's a pin".
Instead, `tests._store_guard` is imported lazily, INSIDE each test that
needs it (module-level for the subprocess snippets — which is fine, each
snippet is its own throwaway interpreter; method-level for the one in-process
test, #6) so the ModuleNotFoundError is scoped to exactly the tests that
depend on the guard, and #8 (which needs no guard at all) is unaffected.

Expected RED (against current code — `tests/_store_guard.py` does not exist):
  tests 1-5, 7  -> FAIL: the subprocess's `import tests._store_guard` raises
                   ModuleNotFoundError; the parent-side JSON-stdout parse
                   surfaces that traceback as the assertion message.
  test 6        -> ERROR: `import tests._store_guard as guard` inside the
                   test body raises ModuleNotFoundError directly.
  test 8        -> may PASS (a pin — it only reads the real store read-only
                   via setUpModule/tearDownModule and does not depend on the
                   guard existing at all).

Run targeted (Crucible client resolves the venv interpreter):
  python3 ~/Documents/data_projects/crucible/clients/python-crucible.py \\
      test --tests tests.test_store_guard --agent CR-SAN-049-C1-RED
"""

import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable


def _local_temp_root():
    """Mirrors the guard's expected `temp_root()` — computed independently
    here (not imported from the not-yet-existing guard module) so the
    subprocess-reported value can be cross-checked against it."""
    return os.path.realpath(tempfile.gettempdir())


def _minimal_env(xdg=None, extra=None):
    """A FULLY-CONTROLLED subprocess env — built from scratch, never
    `{**os.environ, ...}` — so the machine-wide `XDG_DATA_HOME` export (see
    CLAUDE.md gotcha / CR-SAN-049 context) can never leak into a guard
    scenario by accident. `xdg=None` OMITS the key entirely (the AC2 'unset'
    case); pass a string to set it explicitly."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": _REPO_ROOT,
        "HOME": os.environ.get("HOME", ""),
    }
    if xdg is not None:
        env["XDG_DATA_HOME"] = xdg
    if extra:
        env.update(extra)
    return env


def _run_snippet(code, env, timeout=60):
    return subprocess.run(
        [_SUBPROCESS_PYTHON, "-c", code],
        cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


def _parse_json_stdout(proc):
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "subprocess did not emit JSON on stdout "
            f"(returncode={proc.returncode})\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        ) from exc


# --------------------------------------------------------------------------- #
# subprocess snippets (each is a throwaway interpreter — flush-left so they
# are valid Python when handed to `-c` verbatim)

_SNIPPET_UNSET = """
import json, os
import tests._store_guard as guard
import sandesh.sandesh_db as sdb
xdg = os.environ.get("XDG_DATA_HOME")
print(json.dumps({
    "xdg": xdg,
    "temp_root": guard.temp_root(),
    "exists_during": bool(xdg) and os.path.isdir(xdg),
    "db_path": sdb.db_path(),
    "guard_tmp": guard.GUARD_TMP,
}))
"""

_SNIPPET_REPORT_XDG_AND_DBPATH = """
import json, os
import tests._store_guard as guard
import sandesh.sandesh_db as sdb
print(json.dumps({
    "xdg_after": os.environ.get("XDG_DATA_HOME"),
    "db_path": sdb.db_path(),
}))
"""

_SNIPPET_RESPECTED = """
import json, os
import tests._store_guard as guard
print(json.dumps({
    "xdg_after": os.environ.get("XDG_DATA_HOME"),
    "guard_tmp": guard.GUARD_TMP,
    "guard_tmp_exists": os.path.isdir(guard.GUARD_TMP),
}))
"""

_SNIPPET_IMPORT_ORDER = """
import json, os
import sandesh.sandesh_db as sdb
before_xdg = os.environ.get("XDG_DATA_HOME")
import tests._store_guard as guard
after_xdg = os.environ.get("XDG_DATA_HOME")
print(json.dumps({
    "before_xdg": before_xdg,
    "after_xdg": after_xdg,
    "db_path_after": sdb.db_path(),
    "temp_root": guard.temp_root(),
}))
"""

_SNIPPET_AC4B_LIFECYCLE = """
import json, os, unittest

result = {"ok": False, "error": None}
try:
    import tests._store_guard as guard

    class PassCase(guard.TempStore, unittest.TestCase):
        def test_pass(self):
            pass

    class FailCase(guard.TempStore, unittest.TestCase):
        def test_fail(self):
            raise AssertionError("deliberate RED failure for AC4b lifecycle")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromTestCase(PassCase),
        loader.loadTestsFromTestCase(FailCase),
    ])
    runner_result = unittest.main(
        module=None, exit=False, argv=["ac4b"],
        testRunner=unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0),
        defaultTest=None,
    ) if False else unittest.TextTestRunner(
        stream=open(os.devnull, "w"), verbosity=0,
    ).run(suite)

    result["ok"] = True
    result["tests_run"] = runner_result.testsRun
    result["failures"] = len(runner_result.failures)
    result["temp_root"] = guard.temp_root()
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(result))
"""


# --------------------------------------------------------------------------- #
# AC2 — the guard's four env scenarios (each a fresh subprocess)

class Ac2UnsetXdgTest(unittest.TestCase):
    """AC2 'unset' case: no XDG_DATA_HOME at all -> the guard re-points it to
    a fresh temp dir under the system temp root, removed at interpreter
    exit."""

    def test_unset_xdg_repoints_to_fresh_temp_dir_removed_at_process_exit(self):
        env = _minimal_env()  # XDG_DATA_HOME intentionally absent
        proc = _run_snippet(_SNIPPET_UNSET, env)
        data = _parse_json_stdout(proc)

        root = _local_temp_root()
        self.assertEqual(data["temp_root"], root)
        self.assertTrue(
            data["xdg"] and data["xdg"].startswith(root),
            f"expected XDG_DATA_HOME under {root!r}, got {data['xdg']!r}",
        )
        self.assertIn("sandesh-tests-", os.path.basename(data["xdg"]))
        self.assertTrue(
            data["db_path"].startswith(data["xdg"]),
            f"db_path {data['db_path']!r} not under xdg {data['xdg']!r}",
        )
        self.assertTrue(data["exists_during"], "temp dir must exist while the process runs")
        self.assertEqual(data["guard_tmp"], data["xdg"])

        # AC4b: atexit cleanup — by the time subprocess.run() returns, the
        # child interpreter has fully exited, so its atexit handlers ran.
        self.assertFalse(
            os.path.isdir(data["xdg"]),
            f"guard temp dir {data['xdg']!r} survived process exit — atexit cleanup missing",
        )


class Ac2RealStorePathTest(unittest.TestCase):
    """AC2 'real path' case: XDG_DATA_HOME set to a non-temp-rooted existing
    dir (a stand-in for `~/.local/share`, per the NON-NEGOTIABLE rule never to
    point a test at the real store) -> re-pointed to a DIFFERENT temp path;
    the stand-in dir is never written to."""

    def setUp(self):
        self.standin = os.path.join(
            os.path.expanduser("~"), ".cache", f"sandesh-guard-test-{os.getpid()}-realpath")
        os.makedirs(self.standin, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.standin, ignore_errors=True)

    def test_non_temp_xdg_is_repointed_and_standin_dir_stays_empty(self):
        env = _minimal_env(xdg=self.standin)
        proc = _run_snippet(_SNIPPET_REPORT_XDG_AND_DBPATH, env)
        data = _parse_json_stdout(proc)

        self.assertNotEqual(
            data["xdg_after"], self.standin,
            "a non-temp-rooted XDG_DATA_HOME must be overridden, not kept",
        )
        root = _local_temp_root()
        self.assertTrue(
            data["xdg_after"] and data["xdg_after"].startswith(root),
            f"expected the re-pointed value under {root!r}, got {data['xdg_after']!r}",
        )
        self.assertTrue(data["db_path"].startswith(data["xdg_after"]))
        self.assertEqual(
            os.listdir(self.standin), [],
            "nothing must ever be written under the stand-in (real-store-like) dir",
        )


class Ac2TempRootedValueRespectedTest(unittest.TestCase):
    """AC2 'respected' case: XDG_DATA_HOME already resolves under the system
    temp root (e.g. a harness-supplied temp store) -> left UNCHANGED, even
    though the guard still creates its own per-process GUARD_TMP dir."""

    def setUp(self):
        self.existing = tempfile.mkdtemp(prefix="sandesh-preexisting-", dir=tempfile.gettempdir())

    def tearDown(self):
        shutil.rmtree(self.existing, ignore_errors=True)

    def test_temp_rooted_xdg_is_left_unchanged_but_guard_tmp_still_created(self):
        env = _minimal_env(xdg=self.existing)
        proc = _run_snippet(_SNIPPET_RESPECTED, env)
        data = _parse_json_stdout(proc)

        self.assertEqual(
            data["xdg_after"], self.existing,
            "a temp-rooted XDG_DATA_HOME must be respected, not overridden",
        )
        self.assertTrue(
            data["guard_tmp_exists"],
            "the guard must still create its own per-process temp dir even when respecting XDG",
        )
        self.assertNotEqual(
            data["guard_tmp"], self.existing,
            "GUARD_TMP is the guard's OWN dir, distinct from the respected XDG value",
        )


class Ac2BypassEnvVarTest(unittest.TestCase):
    """AC2 'bypass' case: SANDESH_TESTS_ALLOW_REAL_STORE=1 -> XDG_DATA_HOME is
    left completely untouched, even when it points at a real-store-like
    (non-temp) path."""

    def setUp(self):
        self.standin = os.path.join(
            os.path.expanduser("~"), ".cache", f"sandesh-guard-test-{os.getpid()}-bypass")
        os.makedirs(self.standin, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.standin, ignore_errors=True)

    def test_bypass_var_leaves_real_store_like_path_untouched(self):
        env = _minimal_env(xdg=self.standin, extra={"SANDESH_TESTS_ALLOW_REAL_STORE": "1"})
        proc = _run_snippet(_SNIPPET_REPORT_XDG_AND_DBPATH, env)
        data = _parse_json_stdout(proc)

        self.assertEqual(
            data["xdg_after"], self.standin,
            "SANDESH_TESTS_ALLOW_REAL_STORE=1 must bypass the guard entirely",
        )
        self.assertTrue(data["db_path"].startswith(self.standin))
        self.assertEqual(os.listdir(self.standin), [])


class ImportOrderGuardTest(unittest.TestCase):
    """`sandesh.sandesh_db` imported BEFORE `tests._store_guard` must still
    observe the re-pointed XDG_DATA_HOME — db_path() has no cached path, it
    reads os.environ at call time, so import order must not matter."""

    def setUp(self):
        self.standin = os.path.join(
            os.path.expanduser("~"), ".cache", f"sandesh-guard-test-{os.getpid()}-order")
        os.makedirs(self.standin, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.standin, ignore_errors=True)

    def test_guard_repoints_env_even_when_sandesh_db_imported_first(self):
        env = _minimal_env(xdg=self.standin)
        proc = _run_snippet(_SNIPPET_IMPORT_ORDER, env)
        data = _parse_json_stdout(proc)

        self.assertEqual(data["before_xdg"], self.standin)
        self.assertNotEqual(
            data["after_xdg"], self.standin,
            "the guard must re-point XDG_DATA_HOME regardless of import order",
        )
        root = _local_temp_root()
        self.assertTrue(data["after_xdg"].startswith(root))
        self.assertEqual(data["temp_root"], root)
        self.assertTrue(
            data["db_path_after"].startswith(data["after_xdg"]),
            "db_path() must follow the NEW env value, proving it reads env at call time",
        )


# --------------------------------------------------------------------------- #
# TempStore mixin — exercised in-process

class TempStoreMixinLifecycleTest(unittest.TestCase):
    """Contract: `tests._store_guard.TempStore` is a `unittest.TestCase`
    mixin — `setUp` points XDG_DATA_HOME at a fresh temp dir under the
    guard's temp root; `self.connect()` opens (and tracks) a
    `sandesh_db.connect()` connection; `tearDown` closes tracked connections,
    restores the previous XDG_DATA_HOME, and removes the temp dir.

    Exercised HERE, in-process, via a throwaway TestCase run through a
    private TestSuite/TestResult (never registered with THIS module's own
    test runner) — safe because (a) the mixin never points at the real store
    and (b) while it doesn't exist yet, the ModuleNotFoundError below fires
    before any sandesh_db call is ever made.
    """

    def test_mixin_isolates_store_and_tears_down_completely_even_after_use(self):
        prev_xdg = os.environ.get("XDG_DATA_HOME")
        captured = {}

        # RED trigger: tests._store_guard does not exist yet.
        import tests._store_guard as guard  # noqa: F401
        import sandesh.sandesh_db as sdb

        class _Probe(guard.TempStore, unittest.TestCase):
            def test_body(self):
                captured["xdg_during"] = os.environ.get("XDG_DATA_HOME")
                captured["tmp_name"] = self._tmp.name
                captured["temp_root"] = guard.temp_root()
                sdb.setup("Demo")
                captured["db_created"] = os.path.isfile(sdb.db_path())
                captured["con"] = self.connect()

        suite = unittest.TestSuite([_Probe("test_body")])
        result = unittest.TestResult()
        suite.run(result)

        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [], result.errors)
        self.assertEqual(result.failures, [], result.failures)

        self.assertEqual(
            captured["xdg_during"], captured["tmp_name"],
            "setUp must point XDG_DATA_HOME at self._tmp.name",
        )
        self.assertTrue(captured["tmp_name"].startswith(captured["temp_root"]))
        self.assertTrue(captured["db_created"], "sandesh_db.setup() must have created sandesh.db under the temp store")

        # tearDown must have run: dir gone, env restored, connection closed.
        self.assertFalse(
            os.path.isdir(captured["tmp_name"]),
            "TempStore.tearDown must remove its temp dir",
        )
        self.assertEqual(
            os.environ.get("XDG_DATA_HOME"), prev_xdg,
            "TempStore.tearDown must restore the prior XDG_DATA_HOME value",
        )
        with self.assertRaises(sqlite3.ProgrammingError):
            captured["con"].execute("SELECT 1")


# --------------------------------------------------------------------------- #
# AC4b — full lifecycle: no leftover temp dirs, even with a failing test

class Ac4bLifecycleTest(unittest.TestCase):
    """After a suite that mixes a passing and a deliberately-failing
    TempStore-based TestCase, NO `sandesh-*` dir remains under the system
    temp root (both the guard's own dir and every TempStore dir were
    cleaned up — tearDown ran even for the failing test), and none leaked
    under $HOME either."""

    def test_no_leftover_temp_dirs_after_suite_with_a_failing_test(self):
        start = time.time() - 1  # 1s slack for filesystem mtime granularity
        env = _minimal_env()
        proc = _run_snippet(_SNIPPET_AC4B_LIFECYCLE, env)
        data = _parse_json_stdout(proc)

        self.assertTrue(data["ok"], f"lifecycle subprocess errored: {data.get('error')}")
        self.assertEqual(data["tests_run"], 2)
        self.assertEqual(data["failures"], 1)

        root = _local_temp_root()
        leftover_temp = [
            p for p in glob.glob(os.path.join(root, "sandesh-*"))
            if os.path.isdir(p) and os.path.getmtime(p) >= start
        ]
        self.assertEqual(
            leftover_temp, [],
            f"leftover sandesh-* dirs under the temp root after the run: {leftover_temp}",
        )

        home_leftover = glob.glob(os.path.join(os.path.expanduser("~"), "sandesh-tests-*"))
        self.assertEqual(
            home_leftover, [],
            f"a sandesh-tests-* dir leaked under $HOME: {home_leftover}",
        )


# --------------------------------------------------------------------------- #
# Real-store invariant (a pin — may already PASS; needs no guard at all)

_REAL_STORE_PATH = os.path.expanduser("~/.local/share/sandesh/sandesh.db")
_real_store_counts = {}


def _read_real_store_counts():
    if not os.path.isfile(_REAL_STORE_PATH):
        return None
    con = sqlite3.connect(f"file:{_REAL_STORE_PATH}?mode=ro", uri=True)
    try:
        project_count = con.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        address_count = con.execute("SELECT COUNT(*) FROM address").fetchone()[0]
        return {"project": project_count, "address": address_count}
    finally:
        con.close()


def setUpModule():
    _real_store_counts["before"] = _read_real_store_counts()


def tearDownModule():
    before = _real_store_counts.get("before")
    if before is None:
        return
    after = _read_real_store_counts()
    if after != before:
        raise AssertionError(
            "the shared real store changed while tests/test_store_guard.py ran "
            f"(memory rule violation): before={before} after={after}"
        )


class RealStoreInvariantTest(unittest.TestCase):
    """AC4b / the memory rule: `~/.local/share/sandesh/sandesh.db` (the ONE
    global store shared by every running Sandesh project on this machine)
    must never be written by this test module. The actual before/after
    `COUNT(*)` comparison lives in `tearDownModule` above (per the dispatch
    prompt: 'Implement as setUpModule/tearDownModule') and runs a read-only
    (`mode=ro`) query; this test just asserts the fixture actually observed
    real numbers (or records absence), so a silently-skipped invariant isn't
    mistaken for a real pass in the report."""

    def test_real_store_counts_were_observed_or_store_absent(self):
        before = _real_store_counts.get("before")
        if before is None:
            self.skipTest(f"real store {_REAL_STORE_PATH!r} does not exist on this machine")
        self.assertIn("project", before)
        self.assertIn("address", before)
        self.assertGreaterEqual(before["project"], 0)
        self.assertGreaterEqual(before["address"], 0)


if __name__ == "__main__":
    unittest.main()
