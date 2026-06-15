"""test_install.py — package-install integration tests (CR-SAN-008 §S4/AC3/AC4/AC8 + install.sh).

Replaces the old install.sh venv+wrapper test (CR-SAN-001 AC1-AC3, now obsolete after C0
moved the code into sandesh/ package and pyproject.toml added console scripts).

Tests:
  AC3  — `pip install .` (base, no [mcp]) → `sandesh` + `sandesh-mcp` console scripts on
          venv PATH; `sandesh --help` exits 0; `import mcp` fails (stdlib-only base).
  AC8  — In that same base venv, `sandesh-mcp` exits non-zero with a message containing
          "[mcp]" and an install hint, and NO "Traceback".
  AC4  — `pip install '.[mcp]'` → `import mcp` succeeds; `sandesh-mcp` starts the server
          (does NOT immediately print the missing-extra error).
  AC-install.sh — `install.sh` in an isolated HOME/XDG env installs a working `sandesh`;
          running `sandesh --help` exits 0 with usage text. This FAILS until GREEN rewrites
          install.sh to use the new package layout (it still copies app/*.py which no longer
          exists).

Run from repo root:
  python3 -m unittest tests.test_install -v
  # or targeted via python-crucible:
  python3 ~/.claude/scripts/python-crucible.py test --tests tests.test_install
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")


def _venv_python(venv_dir):
    return os.path.join(venv_dir, "bin", "python")


def _venv_script(venv_dir, name):
    return os.path.join(venv_dir, "bin", name)


def _make_venv(tmp_dir, name="venv"):
    """Create a fresh venv under tmp_dir/<name> using the project interpreter."""
    venv_path = os.path.join(tmp_dir, name)
    subprocess.run(
        [sys.executable, "-m", "venv", venv_path],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_path


def _pip_install(venv_dir, *args, cwd=None, timeout=300):
    """Run pip install <args> inside venv_dir. Returns CompletedProcess."""
    python = _venv_python(venv_dir)
    return subprocess.run(
        [python, "-m", "pip", "install", "--quiet", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# AC3 — base install: console scripts present; stdlib-only CLI works; mcp absent
# ---------------------------------------------------------------------------

class BaseInstallTest(unittest.TestCase):
    """AC3: `pip install .` (no extras) creates sandesh + sandesh-mcp on the venv PATH
    and the CLI works without any third-party package."""

    tmp = None
    venv_dir = None
    install_result = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-base-install-test-")
        try:
            cls.venv_dir = _make_venv(cls.tmp, "base_venv")
            cls.install_result = _pip_install(cls.venv_dir, ".", cwd=REPO)
        except Exception as exc:
            cls.install_result = None
            cls._setup_error = str(exc)
        else:
            cls._setup_error = None

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _assert_install_ok(self):
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")
        self.assertIsNotNone(self.install_result, "install_result is None")
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"pip install . exited {self.install_result.returncode}\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}"
            ),
        )

    def test_ac3_pip_install_base_exits_zero(self):
        """pip install . (base, no extras) must complete without error."""
        self._assert_install_ok()

    def test_ac3_sandesh_console_script_exists(self):
        """pip install . must place the `sandesh` console script in the venv bin dir."""
        self._assert_install_ok()
        script = _venv_script(self.venv_dir, "sandesh")
        self.assertTrue(
            os.path.isfile(script),
            msg=(
                f"Expected `sandesh` console script at {script}.\n"
                f"bin/ contents: {os.listdir(os.path.join(self.venv_dir, 'bin'))}"
            ),
        )

    def test_ac3_sandesh_mcp_console_script_exists(self):
        """pip install . must place the `sandesh-mcp` console script in the venv bin dir."""
        self._assert_install_ok()
        script = _venv_script(self.venv_dir, "sandesh-mcp")
        self.assertTrue(
            os.path.isfile(script),
            msg=(
                f"Expected `sandesh-mcp` console script at {script}.\n"
                f"bin/ contents: {os.listdir(os.path.join(self.venv_dir, 'bin'))}"
            ),
        )

    def test_ac3_sandesh_help_exits_zero(self):
        """In the base venv, `sandesh --help` must exit 0 and print usage text."""
        self._assert_install_ok()
        script = _venv_script(self.venv_dir, "sandesh")
        result = subprocess.run(
            [script, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh --help` exited {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            ),
        )
        combined = result.stdout + result.stderr
        self.assertTrue(
            len(combined.strip()) > 0,
            msg="`sandesh --help` produced no output — expected usage text.",
        )
        # At minimum the word "usage" or "sandesh" should appear in help output
        self.assertTrue(
            any(kw in combined.lower() for kw in ("usage", "sandesh", "project")),
            msg=f"`sandesh --help` output does not look like usage text:\n{combined}",
        )

    def test_ac3_sandesh_version_exits_zero(self):
        """In the base venv, `sandesh --version` must exit 0 and print a version string."""
        self._assert_install_ok()
        script = _venv_script(self.venv_dir, "sandesh")
        result = subprocess.run(
            [script, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh --version` exited {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            ),
        )
        combined = result.stdout + result.stderr
        # Should contain "sandesh" and a version-like token
        self.assertIn(
            "sandesh",
            combined.lower(),
            msg=f"`sandesh --version` output doesn't mention 'sandesh':\n{combined}",
        )

    def test_ac3_base_install_has_no_mcp(self):
        """In the base venv (no extras), `import mcp` must FAIL — it is not a dependency."""
        self._assert_install_ok()
        python = _venv_python(self.venv_dir)
        result = subprocess.run(
            [python, "-c", "import mcp"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(
            result.returncode,
            0,
            msg=(
                "`import mcp` unexpectedly SUCCEEDED in the base (no-[mcp]) venv.\n"
                "The base install must be stdlib-only; `mcp` must only be pulled in via "
                "'.[mcp]'.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )


# ---------------------------------------------------------------------------
# AC8 — base install: sandesh-mcp prints friendly error, no traceback
# ---------------------------------------------------------------------------

class FriendlyErrorTest(unittest.TestCase):
    """AC8: In a base (no-[mcp]) install, `sandesh-mcp` must print a clear message naming
    the fix, exit non-zero, and produce NO raw ImportError/Traceback."""

    tmp = None
    venv_dir = None
    _setup_error = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-friendly-error-test-")
        try:
            cls.venv_dir = _make_venv(cls.tmp, "base_venv")
            result = _pip_install(cls.venv_dir, ".", cwd=REPO)
            if result.returncode != 0:
                cls._setup_error = (
                    f"pip install . failed (rc={result.returncode}):\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                )
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _assert_setup_ok(self):
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")

    def test_ac8_sandesh_mcp_exits_nonzero_without_mcp_extra(self):
        """sandesh-mcp in a base install must exit non-zero (mcp is not available)."""
        self._assert_setup_ok()
        script = _venv_script(self.venv_dir, "sandesh-mcp")
        result = subprocess.run(
            [script],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh-mcp` (base install, no [mcp]) exited 0 — expected non-zero.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac8_sandesh_mcp_message_contains_mcp_marker(self):
        """sandesh-mcp's error output must contain '[mcp]' as the extra marker."""
        self._assert_setup_ok()
        script = _venv_script(self.venv_dir, "sandesh-mcp")
        result = subprocess.run(
            [script],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertIn(
            "[mcp]",
            combined,
            msg=(
                "sandesh-mcp error output does not contain '[mcp]'.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac8_sandesh_mcp_message_contains_install_hint(self):
        """sandesh-mcp's error output must name an install fix (install/pipx/pip)."""
        self._assert_setup_ok()
        script = _venv_script(self.venv_dir, "sandesh-mcp")
        result = subprocess.run(
            [script],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertTrue(
            any(hint in combined.lower() for hint in ("install", "pipx", "pip")),
            msg=(
                "sandesh-mcp error output does not contain an install hint (install/pipx/pip).\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac8_sandesh_mcp_no_traceback(self):
        """sandesh-mcp in a base install must NOT produce a Python traceback."""
        self._assert_setup_ok()
        script = _venv_script(self.venv_dir, "sandesh-mcp")
        result = subprocess.run(
            [script],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "Traceback",
            combined,
            msg=(
                "sandesh-mcp produced a Python traceback in the base (no-[mcp]) install.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac8_sandesh_still_works_in_base_install(self):
        """`sandesh --help` must still succeed in the base (no-[mcp]) install."""
        self._assert_setup_ok()
        script = _venv_script(self.venv_dir, "sandesh")
        result = subprocess.run(
            [script, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh --help` failed in base install (rc={result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )


# ---------------------------------------------------------------------------
# AC4 — [mcp] extra install: mcp importable; server starts (no friendly-error exit)
# ---------------------------------------------------------------------------

class McpExtraInstallTest(unittest.TestCase):
    """AC4: `pip install '.[mcp]'` makes `import mcp` succeed and `sandesh-mcp` launches
    the MCP server (does NOT exit immediately with the missing-extra error message)."""

    tmp = None
    venv_dir = None
    _setup_error = None
    _skipped = False
    _skip_reason = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-mcp-install-test-")
        try:
            cls.venv_dir = _make_venv(cls.tmp, "mcp_venv")
            result = _pip_install(cls.venv_dir, ".[mcp]", cwd=REPO, timeout=300)
            if result.returncode != 0:
                # [mcp] install may fail offline — skip rather than hard-fail
                cls._skipped = True
                cls._skip_reason = (
                    f"[mcp] install failed (likely no network/cache): "
                    f"rc={result.returncode} stderr={result.stderr[:200]}"
                )
        except subprocess.TimeoutExpired:
            cls._skipped = True
            cls._skip_reason = "[mcp] install timed out — likely no network."
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _assert_available(self):
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")
        if self._skipped:
            self.skipTest(self._skip_reason)

    def test_ac4_mcp_importable_after_mcp_extra_install(self):
        """After `pip install '.[mcp]'`, `import mcp` must succeed."""
        self._assert_available()
        python = _venv_python(self.venv_dir)
        result = subprocess.run(
            [python, "-c", "import mcp; print('mcp ok')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                "`import mcp` failed after `pip install '.[mcp]'`.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertIn(
            "mcp ok",
            result.stdout,
            msg="Expected 'mcp ok' in stdout after successful import.",
        )

    def test_ac4_sandesh_mcp_starts_server_does_not_print_friendly_error(self):
        """sandesh-mcp with [mcp] installed must NOT print the missing-extra message.

        The MCP stdio server blocks waiting for JSON-RPC input. We start it, verify
        it does not immediately exit non-zero with the friendly error, then terminate.
        Strategy: poll() after a brief moment — None means still running (good).
        If it exits quickly, check the output for the friendly-error marker.
        """
        self._assert_available()
        script = _venv_script(self.venv_dir, "sandesh-mcp")

        import time
        proc = subprocess.Popen(
            [script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Give the server a moment to either start or fail fast
            time.sleep(2)
            poll_rc = proc.poll()

            if poll_rc is None:
                # Still running — server started successfully (expected path)
                pass
            else:
                # Exited quickly — check it didn't print the friendly-error message
                stdout = proc.stdout.read() if proc.stdout else ""
                stderr = proc.stderr.read() if proc.stderr else ""
                combined = stdout + stderr
                self.assertNotIn(
                    "[mcp]",
                    combined,
                    msg=(
                        f"sandesh-mcp (with [mcp] installed) exited rc={poll_rc} "
                        "and printed the missing-extra message — the [mcp] install "
                        "did not take effect.\n"
                        f"Output:\n{combined}"
                    ),
                )
                self.assertNotIn(
                    "Traceback",
                    combined,
                    msg=(
                        f"sandesh-mcp (with [mcp] installed) exited rc={poll_rc} "
                        f"with a traceback.\nOutput:\n{combined}"
                    ),
                )
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            # Close the pipe wrappers — text=True Popen pipes are TextIOWrapper
            # objects that otherwise leak and fire ResourceWarning at GC time.
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()

    def test_ac4_sandesh_script_still_works_with_mcp_extra(self):
        """`sandesh --help` must exit 0 in the [mcp]-installed venv."""
        self._assert_available()
        script = _venv_script(self.venv_dir, "sandesh")
        result = subprocess.run(
            [script, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh --help` failed in [mcp]-installed venv (rc={result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )


# ---------------------------------------------------------------------------
# AC-install.sh — install.sh works on the new package layout (THE RED ASSERTION)
# ---------------------------------------------------------------------------

class InstallShTest(unittest.TestCase):
    """install.sh in an isolated HOME/XDG env installs a working `sandesh` command.

    This test MUST FAIL (RED) until GREEN rewrites install.sh to use the new
    sandesh/ package layout (currently install.sh copies app/*.py which no longer
    exists after CR-SAN-008 C0).

    The test asserts the end-state: the installed `sandesh --help` exits 0 and
    prints usage text. Any failure mode is acceptable as a RED signal (install.sh
    may error on the missing app/ directory, or the installed launcher may not work).
    """

    tmp = None
    xdg_data = None
    home_dir = None
    install_result = None
    _setup_error = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-install-sh-test-")
        cls.home_dir = os.path.join(cls.tmp, "home")
        cls.xdg_data = os.path.join(cls.home_dir, ".local", "share")
        os.makedirs(cls.home_dir, exist_ok=True)
        env = {
            **os.environ,
            "HOME": cls.home_dir,
            "XDG_DATA_HOME": cls.xdg_data,
            # Don't let install.sh's pip hit a network it might not have
            # (install.sh currently uses venv + pip install mcp — this can fail offline)
        }
        try:
            cls.install_result = subprocess.run(
                ["bash", INSTALL_SH],
                env=env,
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_install_sh_exits_zero(self):
        """install.sh must complete without error on the new package layout.

        FAILS (RED): install.sh still contains `cp "$SRC/app/"*.py ...` but app/
        no longer exists after C0 moved code to sandesh/.
        """
        if self._setup_error:
            self.fail(f"setUpClass failed to run install.sh: {self._setup_error}")
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"install.sh exited {self.install_result.returncode}\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}\n"
                "\nThis is the expected RED failure — install.sh still copies app/*.py "
                "which no longer exists. GREEN must rewrite install.sh."
            ),
        )

    def test_install_sh_sandesh_launcher_runs_help(self):
        """The installed `sandesh` launcher (from install.sh) must execute `--help` → exit 0.

        FAILS (RED): install.sh installs a launcher pointing to app/cli.py which
        does not exist; `sandesh --help` will fail until GREEN rewrites install.sh.
        """
        if self._setup_error:
            self.fail(f"setUpClass failed to run install.sh: {self._setup_error}")

        # Find the installed launcher — install.sh symlinks to ~/.local/bin/sandesh
        local_bin_sandesh = os.path.join(self.home_dir, ".local", "bin", "sandesh")

        # Also try the direct path in the sandesh data dir
        sandesh_dest = os.path.join(self.xdg_data, "sandesh")
        dest_launcher = os.path.join(sandesh_dest, "bin", "sandesh")

        launcher = None
        for candidate in (local_bin_sandesh, dest_launcher):
            if os.path.lexists(candidate):
                launcher = candidate
                break

        self.assertIsNotNone(
            launcher,
            msg=(
                f"No installed sandesh launcher found.\n"
                f"Checked: {local_bin_sandesh}\n"
                f"Checked: {dest_launcher}\n"
                f"install.sh stdout:\n{self.install_result.stdout}\n"
                f"install.sh stderr:\n{self.install_result.stderr}"
            ),
        )

        result = subprocess.run(
            [launcher, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HOME": self.home_dir},
        )
        combined = result.stdout + result.stderr
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Installed `sandesh --help` exited {result.returncode}.\n"
                f"Launcher: {launcher}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n"
                "\nThis is the expected RED failure — the installed launcher points to "
                "app/cli.py which no longer exists. GREEN must rewrite install.sh."
            ),
        )
        self.assertTrue(
            any(kw in combined.lower() for kw in ("usage", "sandesh", "project")),
            msg=(
                f"Installed `sandesh --help` output does not look like usage text.\n"
                f"Output:\n{combined}"
            ),
        )


# ---------------------------------------------------------------------------
# CR-SAN-018 C1 — install.sh migrate --all hook (RED tests)
# AC1 — installer migrates an existing store
# AC2 — installer tolerates missing [migrate] extra (skip + notice, exit 0)
# AC3 — fresh install no-op (empty data-home, migrate step is clean)
# ---------------------------------------------------------------------------

# Old schema SQL — the 4-table shape FROM BEFORE CR-SAN-017 that included
# message.status.  Constructed from sandesh_db._SCHEMA + a status column on
# the message table.  No _yoyo_migration table — this is a pre-engine store.
_PRE_MIGRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,
    kind          TEXT,
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    kind        TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    in_reply_to INTEGER REFERENCES message(id),
    body_path   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',
    read_at    TEXT,
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,
    pid          INTEGER,
    token        TEXT,
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def _make_pre_migration_store(xdg_data, project_id):
    """Provision a GLOBAL store that resembles a pre-CR-SAN-017 database.

    CR-SAN-022 C3: the migrate engine targets the single global DB, so the
    legacy fixture seeds <xdg_data>/sandesh/sandesh.db with the old 4-table
    schema (message.status present, NO _yoyo_migration table). The installer's
    `migrate --all` must adopt the baseline and bring it to the latest schema.
    Per-project legacy files are CR-SAN-022 C5 consolidation territory.
    Returns the path to the db file.
    """
    import sqlite3
    sandesh_dir = os.path.join(xdg_data, "sandesh")
    messages_dir = os.path.join(sandesh_dir, "projects", project_id, "messages")
    os.makedirs(messages_dir, exist_ok=True)
    db_path = os.path.join(sandesh_dir, "sandesh.db")
    con = sqlite3.connect(db_path)
    con.executescript(_PRE_MIGRATION_SCHEMA)
    con.commit()
    con.close()
    return db_path


def _db_has_status_column(db_path):
    """Return True if message.status column exists in the given db."""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("PRAGMA table_info(message)").fetchall()
        return any(row[1] == "status" for row in rows)
    finally:
        con.close()


def _db_has_yoyo_table(db_path):
    """Return True if _yoyo_migration table exists (i.e. yoyo has run)."""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_yoyo%'"
        ).fetchall()
        return len(rows) > 0
    finally:
        con.close()


class MigrateExtraInstallTest(unittest.TestCase):
    """AC2 — installer tolerates a missing [migrate] extra (delegated path).

    Run install.sh with SANDESH_INSTALL_EXTRAS="" (empty string forces a
    stdlib-only base install — no [migrate] deps).  Assert:
      - install exits 0 (completes successfully)
      - installer did NOT abort (the migrate step was skipped, not failed)
      - output does NOT contain a traceback
      - the venv python exists (install completed)

    After §S2 delegation: `sandesh init` emits "migrate: skipped — the
    [migrate] extra is not installed (store schema is current)." (or similar)
    when migrate is absent on a fresh/current store; the OLD inline strings
    "migrations skipped"/"migrate --all"/"sandesh migrate" are REMOVED from
    install.sh output.

    DRIFT-1 refresh (CR-SAN-037 C1): the old test asserted the inline notice
    strings which are removed by delegation to `sandesh init`. Updated to
    assert the delegated behaviour: install completes (exit 0), no traceback,
    venv present. Removed the assertion on the removed inline strings.

    RED now: install.sh still has the inline block and does NOT yet call
    `sandesh init`, so `test_ac2_install_invokes_sandesh_init` fails (the
    `sandesh init` token is absent from install.sh source).
    """

    tmp = None
    home_dir = None
    xdg_data = None
    install_result = None
    _setup_error = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-migrate-no-extra-test-")
        cls.home_dir = os.path.join(cls.tmp, "home")
        cls.xdg_data = os.path.join(cls.home_dir, ".local", "share")
        os.makedirs(cls.home_dir, exist_ok=True)
        env = {
            **os.environ,
            "HOME": cls.home_dir,
            "XDG_DATA_HOME": cls.xdg_data,
            # Force a base-only install — no [migrate] extra at all.
            "SANDESH_INSTALL_EXTRAS": "",
        }
        try:
            cls.install_result = subprocess.run(
                ["bash", INSTALL_SH],
                env=env,
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _check_setup(self):
        if self._setup_error:
            self.fail(f"setUpClass failed to run install.sh: {self._setup_error}")
        self.assertIsNotNone(self.install_result, "install_result is None")

    def test_ac2_install_exits_zero_without_migrate_extra(self):
        """install.sh must exit 0 when [migrate] deps are absent (base-only install).

        RED: install.sh does not yet distinguish the missing-migrate case
        (it only has the base-only path for [mcp]).  Once GREEN adds the
        migrate --all hook the missing-extra path must still exit 0.
        """
        self._check_setup()
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"install.sh exited {self.install_result.returncode} with "
                "SANDESH_INSTALL_EXTRAS='' — expected exit 0.\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}"
            ),
        )

    def test_ac2_output_no_traceback(self):
        """install.sh with SANDESH_INSTALL_EXTRAS='' must not produce a traceback.

        DRIFT-1 refresh (CR-SAN-037 C1): replaces the old assertion on the
        removed inline 'migrations skipped'/'sandesh migrate --all' strings.
        After delegation to `sandesh init` those strings are gone from
        install.sh output; this test asserts the delegated path is clean.
        """
        self._check_setup()
        combined = self.install_result.stdout + self.install_result.stderr
        self.assertNotIn(
            "Traceback",
            combined,
            msg=(
                "install.sh produced a Python traceback with SANDESH_INSTALL_EXTRAS=''.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac2_install_invokes_sandesh_init(self):
        """install.sh source must contain a 'sandesh init' invocation (delegation).

        After §S2: the inline provisioning block (migrate→consolidate→reindex→admin)
        is replaced by a single `sandesh init` call. This test reads install.sh
        source and asserts 'sandesh init' appears.

        RED: install.sh currently has the inline block with no 'sandesh init'.
        """
        with open(INSTALL_SH, "r") as fh:
            source = fh.read()
        self.assertIn(
            "sandesh init",
            source,
            msg=(
                "install.sh source does not contain 'sandesh init' — "
                "provisioning must be delegated to `sandesh init` (§S2).\n"
                "Current install.sh still has the inline migrate→consolidate→reindex→admin block."
            ),
        )
        # Negative: the old inline admin heredoc must be gone
        self.assertNotIn(
            "assign_admin(con",
            source,
            msg=(
                "install.sh source still contains 'assign_admin(con' — "
                "the inline admin heredoc must be removed when delegation is added (§S2)."
            ),
        )

    def test_ac2_install_did_not_abort_on_missing_migrate(self):
        """When [migrate] is absent, install.sh must complete (no abort).

        The sandesh launcher must have been installed (exit 0 and the venv
        was set up) — confirm the venv python exists as a proxy for completion.

        RED: install.sh currently has no migrate step, so this passes now
        as a guard; it will remain GREEN after the hook is added (the
        missing-extra path must still complete).
        """
        self._check_setup()
        # If the install aborted mid-way, the venv/bin/python would not exist.
        venv_python = os.path.join(
            self.xdg_data, "sandesh", ".venv", "bin", "python"
        )
        self.assertTrue(
            os.path.isfile(venv_python),
            msg=(
                "install.sh appears to have aborted — venv python not found at "
                f"{venv_python}.\n"
                f"install.sh stdout:\n{self.install_result.stdout}\n"
                f"install.sh stderr:\n{self.install_result.stderr}"
            ),
        )


class MigrateOnInstallTest(unittest.TestCase):
    """AC1 — installer migrates an existing store (delegated via sandesh init).

    Pre-creates a store in the temp XDG_DATA_HOME that has the old schema
    (message.status present, no _yoyo_migration).  Runs install.sh with
    SANDESH_INSTALL_EXTRAS=[mcp,migrate] so the installer venv gets the
    [migrate] deps (yoyo + jsonschema).  After install, asserts:
      - install exits 0
      - the pre-migration store is fully migrated (0 pending via installed
        `sandesh migrate --status`)
      - message.status column is GONE from the store
      - _yoyo_migration table exists (yoyo recorded the migrations)

    HARNESS NOTE: requires network/pip-cache for [mcp,migrate] deps.  If
    the pip install fails (offline / no cache), the test is SKIPPED — same
    pattern as McpExtraInstallTest.  Flag for orchestrator: if this env has
    no network/cache, AC1/AC3 will report as SKIPPED, not RED.  The RED
    assertions about migration status only fire when deps are available.

    DRIFT-1 refresh (CR-SAN-037 C1): behaviour assertions (status column
    dropped, yoyo table present, 0 pending) are preserved.  The old comment
    "RED: install.sh does not yet run migrate --all" is updated: after §S2
    delegation, install.sh calls `sandesh init` which in turn calls migrate.
    The core behaviour tests remain and are still RED until GREEN implements
    the delegation.
    """

    tmp = None
    home_dir = None
    xdg_data = None
    install_result = None
    _setup_error = None
    _skipped = False
    _skip_reason = None

    PROJECT_ID = "CR018AC1Test"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-migrate-on-install-test-")
        cls.home_dir = os.path.join(cls.tmp, "home")
        cls.xdg_data = os.path.join(cls.home_dir, ".local", "share")
        os.makedirs(cls.home_dir, exist_ok=True)

        # Pre-create a store with the old schema (message.status present).
        _make_pre_migration_store(cls.xdg_data, cls.PROJECT_ID)

        env = {
            **os.environ,
            "HOME": cls.home_dir,
            "XDG_DATA_HOME": cls.xdg_data,
            # Request [mcp,migrate] so the installer venv gets yoyo+jsonschema.
            "SANDESH_INSTALL_EXTRAS": "[mcp,migrate]",
        }
        try:
            cls.install_result = subprocess.run(
                ["bash", INSTALL_SH],
                env=env,
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=300,
            )
            # If the installer itself exited non-zero AND the venv python does
            # not exist, the pip install of [mcp,migrate] likely failed offline.
            venv_python = os.path.join(
                cls.xdg_data, "sandesh", ".venv", "bin", "python"
            )
            if cls.install_result.returncode != 0 and not os.path.isfile(venv_python):
                cls._skipped = True
                cls._skip_reason = (
                    "[mcp,migrate] pip install failed (likely offline/no cache); "
                    f"install.sh rc={cls.install_result.returncode} "
                    f"stderr={cls.install_result.stderr[:200]!r}"
                )
        except subprocess.TimeoutExpired:
            cls._skipped = True
            cls._skip_reason = "[mcp,migrate] install timed out — likely no network."
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _check_available(self):
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")
        if self._skipped:
            self.skipTest(self._skip_reason)

    def _db_path(self):
        return os.path.join(self.xdg_data, "sandesh", "sandesh.db")

    def _installed_sandesh(self):
        """Path to the installed sandesh console script in the installer venv."""
        return os.path.join(self.xdg_data, "sandesh", ".venv", "bin", "sandesh")

    def test_ac1_install_exits_zero_with_migrate_extra(self):
        """install.sh with SANDESH_INSTALL_EXTRAS=[mcp,migrate] must exit 0."""
        self._check_available()
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"install.sh exited {self.install_result.returncode}.\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}"
            ),
        )

    def test_ac1_store_has_no_status_column_after_install(self):
        """After install.sh with [migrate], the pre-migration store must have
        NO message.status column (migration 0002 must have been applied).

        RED: install.sh does not yet delegate to `sandesh init` → the inline
        block does not run migrate, so status column still present after install.
        """
        self._check_available()
        db_path = self._db_path()
        self.assertTrue(
            os.path.isfile(db_path),
            msg=f"Pre-migration store not found at {db_path} after install.",
        )
        has_status = _db_has_status_column(db_path)
        self.assertFalse(
            has_status,
            msg=(
                "message.status column is still present in the store after install.sh "
                "— install.sh must run `migrate --all` to apply 0002-drop-message-status.\n"
                f"DB: {db_path}"
            ),
        )

    def test_ac1_store_has_yoyo_migration_table_after_install(self):
        """After install.sh with [migrate], the store must have a _yoyo_migration
        table (yoyo has been run and recorded the applied migrations).

        RED: install.sh does not yet delegate to `sandesh init` → migration
        not run → no _yoyo_migration table.
        """
        self._check_available()
        db_path = self._db_path()
        self.assertTrue(
            os.path.isfile(db_path),
            msg=f"Pre-migration store not found at {db_path} after install.",
        )
        has_yoyo = _db_has_yoyo_table(db_path)
        self.assertTrue(
            has_yoyo,
            msg=(
                "_yoyo_migration table is absent from the store after install.sh — "
                "install.sh must run `migrate --all` so yoyo records applied migrations.\n"
                f"DB: {db_path}"
            ),
        )

    def test_ac1_migrate_status_shows_zero_pending_after_install(self):
        """After install.sh with [migrate], `sandesh migrate --status` must
        report 0 pending migrations for the pre-migration store.

        RED: install.sh does not yet delegate to `sandesh init` → migration
        not run → status shows pending (0002-drop-message-status is pending).
        """
        self._check_available()
        sandesh_bin = self._installed_sandesh()
        if not os.path.isfile(sandesh_bin):
            self.skipTest(
                f"Installed sandesh not found at {sandesh_bin} — "
                "install.sh may not have completed."
            )
        env = {
            **os.environ,
            "HOME": self.home_dir,
            "XDG_DATA_HOME": self.xdg_data,
        }
        result = subprocess.run(
            [sandesh_bin, "migrate", "--status"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"`sandesh migrate --status` exited {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined = result.stdout + result.stderr
        self.assertIn(
            "0 pending",
            combined,
            msg=(
                "`sandesh migrate --status` does not show '0 pending' after install — "
                "the pre-migration store was not migrated by install.sh.\n"
                f"Full output:\n{combined}"
            ),
        )
        # Negative bound: 'pending' must not appear without '0' prefix (no remaining).
        # Allow "0 pending" but not "1 pending", "2 pending", etc.
        import re as _re
        non_zero_pending = _re.search(r"[1-9]\d* pending", combined)
        self.assertIsNone(
            non_zero_pending,
            msg=(
                f"`sandesh migrate --status` reports non-zero pending after install.\n"
                f"Full output:\n{combined}"
            ),
        )


class FreshInstallMigrateNoOpTest(unittest.TestCase):
    """AC3 / AC5 — fresh install with no existing stores: migrate is a clean no-op.

    Empty XDG_DATA_HOME (no projects/ stores at all).  Run install.sh with
    SANDESH_INSTALL_EXTRAS=[mcp,migrate].  Assert:
      - install exits 0
      - no error/traceback in output (the migrate step over zero stores is silent)
      - the projects/ dir is empty (no stores were created as a side effect)

    HARNESS NOTE: same offline/network caveat as MigrateOnInstallTest — skips
    if [mcp,migrate] pip install fails.

    DRIFT-1 refresh (CR-SAN-037 C1): After §S2 delegation, `sandesh init`
    handles the no-op path.  The old comment "RED: install.sh does not call
    migrate --all at all" is updated: the guard assertions (exit 0, no
    traceback, no spurious project) are still the right post-delegation
    expectations and remain in place.  The removed inline strings
    ("migrate --all aborted") are unchanged — that string was never emitted
    by the old code either, so the negative assertion survives as-is.
    """

    tmp = None
    home_dir = None
    xdg_data = None
    install_result = None
    _setup_error = None
    _skipped = False
    _skip_reason = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-fresh-install-migrate-test-")
        cls.home_dir = os.path.join(cls.tmp, "home")
        cls.xdg_data = os.path.join(cls.home_dir, ".local", "share")
        # Deliberately empty — no projects/ at all.
        os.makedirs(cls.home_dir, exist_ok=True)

        env = {
            **os.environ,
            "HOME": cls.home_dir,
            "XDG_DATA_HOME": cls.xdg_data,
            "SANDESH_INSTALL_EXTRAS": "[mcp,migrate]",
        }
        try:
            cls.install_result = subprocess.run(
                ["bash", INSTALL_SH],
                env=env,
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=300,
            )
            venv_python = os.path.join(
                cls.xdg_data, "sandesh", ".venv", "bin", "python"
            )
            if cls.install_result.returncode != 0 and not os.path.isfile(venv_python):
                cls._skipped = True
                cls._skip_reason = (
                    "[mcp,migrate] pip install failed (likely offline/no cache); "
                    f"install.sh rc={cls.install_result.returncode} "
                    f"stderr={cls.install_result.stderr[:200]!r}"
                )
        except subprocess.TimeoutExpired:
            cls._skipped = True
            cls._skip_reason = "[mcp,migrate] install timed out — likely no network."
        except Exception as exc:
            cls._setup_error = str(exc)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def _check_available(self):
        if self._setup_error:
            self.fail(f"setUpClass failed: {self._setup_error}")
        if self._skipped:
            self.skipTest(self._skip_reason)

    def test_ac3_fresh_install_exits_zero(self):
        """install.sh on an empty data-home must exit 0 (no-op migrate is clean).

        DRIFT-1 guard: after §S2 delegation, `sandesh init` runs on a fresh
        store and must complete without error (migrate no-op, consolidate no-op,
        reindex 0 messages, admin assigned from $SANDESH_ADMIN or skipped).
        """
        self._check_available()
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"install.sh exited {self.install_result.returncode} on a fresh "
                "(empty) data-home.\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}"
            ),
        )

    def test_ac3_fresh_install_no_traceback_in_output(self):
        """install.sh on an empty data-home must not produce a traceback.

        DRIFT-1 guard: after §S2 delegation, `sandesh init` over a fresh
        empty store must exit 0 silently — no traceback must appear.
        """
        self._check_available()
        combined = self.install_result.stdout + self.install_result.stderr
        self.assertNotIn(
            "Traceback",
            combined,
            msg=(
                "install.sh produced a Python traceback on a fresh data-home.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac3_fresh_install_no_error_marker_in_output(self):
        """install.sh on an empty data-home must not print an error from migrate.

        DRIFT-1 guard: checks that no 'migrate --all aborted' or error marker
        appears in the delegated `sandesh init` output on a fresh store.
        """
        self._check_available()
        combined = self.install_result.stdout + self.install_result.stderr
        self.assertNotIn(
            "migrate --all aborted",
            combined,
            msg=(
                "install.sh printed a 'migrate --all aborted' error on a fresh "
                "data-home — the no-op path must be silent.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac3_fresh_install_no_spurious_project_created(self):
        """install.sh must not create any project stores as a side effect of
        running `sandesh init` on an empty data-home.

        The projects/ directory must either not exist or be empty.
        """
        self._check_available()
        projects_dir = os.path.join(self.xdg_data, "sandesh", "projects")
        if os.path.isdir(projects_dir):
            entries = os.listdir(projects_dir)
            self.assertEqual(
                entries,
                [],
                msg=(
                    f"install.sh created unexpected project entries in {projects_dir}: "
                    f"{entries}"
                ),
            )

    def test_ac3_init_done_emitted_on_fresh_install(self):
        """After delegation (§S2), install.sh must emit 'init: done.' on a
        fresh install (the `sandesh init` final line).

        DRIFT-1 / RED: install.sh currently has the inline block; it does not
        call `sandesh init` and therefore never emits 'init: done.'. This test
        fails until GREEN replaces the inline block with `sandesh init`.
        """
        self._check_available()
        combined = self.install_result.stdout + self.install_result.stderr
        self.assertIn(
            "init: done.",
            combined,
            msg=(
                "install.sh output does not contain 'init: done.' on a fresh install — "
                "expected `sandesh init` to be invoked (§S2 delegation).\n"
                f"Full output:\n{combined}"
            ),
        )


# ---------------------------------------------------------------------------
# CR-SAN-037 C1 — surface choice + delegate to init + mandatory migrate
# ---------------------------------------------------------------------------


def _make_behind_store(xdg_data):
    """Create a global sandesh.db that is schema-behind for AC4.

    The store has a _yoyo_migration table recording only the 0001-baseline
    migration — so _store_is_behind() returns True (packaged set contains
    0002..0005 which are not applied).

    Returns the db_path.
    """
    import sqlite3
    sandesh_dir = os.path.join(xdg_data, "sandesh")
    os.makedirs(sandesh_dir, exist_ok=True)
    db_path = os.path.join(sandesh_dir, "sandesh.db")
    con = sqlite3.connect(db_path)
    # Bootstrap the 4-table pre-migration schema (same as _PRE_MIGRATION_SCHEMA)
    con.executescript(_PRE_MIGRATION_SCHEMA)
    # Add a _yoyo_migration table with only the baseline applied — rest pending
    con.executescript("""
        CREATE TABLE IF NOT EXISTS _yoyo_migration (
            migration_id TEXT NOT NULL PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        );
        INSERT OR IGNORE INTO _yoyo_migration (migration_id, applied_at_utc)
            VALUES ('0001-baseline', datetime('now'));
    """)
    con.commit()
    con.close()
    return db_path


class SurfaceChoiceInstallTest(unittest.TestCase):
    """CR-SAN-037 §S1/§S2/§S3 — surface choice, delegation, mandatory migrate.

    All tests in this class are RED: install.sh does not yet accept --surface,
    does not delegate to `sandesh init`, and does not enforce mandatory migrate
    on an existing DB when [migrate] cannot be installed.
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _run_install(self, home_dir, xdg_data, extra_args=(), extra_env=None,
                     timeout=300):
        """Run install.sh with given args + env overrides. Returns CompletedProcess."""
        env = {
            **os.environ,
            "HOME": home_dir,
            "XDG_DATA_HOME": xdg_data,
            "SANDESH_ADMIN": "tester",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", INSTALL_SH] + list(extra_args),
            env=env,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _venv_bin(self, xdg_data):
        return os.path.join(xdg_data, "sandesh", ".venv", "bin")

    def _has_mcp_script(self, xdg_data):
        """Return True if sandesh-mcp console script exists in the venv bin."""
        return os.path.isfile(os.path.join(self._venv_bin(xdg_data), "sandesh-mcp"))

    def _has_mcp_symlink(self, home_dir):
        """Return True if the $HOME/.local/bin/sandesh-mcp symlink exists."""
        return os.path.lexists(os.path.join(home_dir, ".local", "bin", "sandesh-mcp"))

    # ------------------------------------------------------------------
    # AC1 / AC2 — surface resolution: --surface claude → mcp installed
    # ------------------------------------------------------------------

    def test_ac1_surface_claude_installs_mcp(self):
        """install.sh --surface claude → venv has sandesh-mcp console script.

        RED: install.sh rejects --surface flag (falls to the '*' case → exit 2).
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-claude-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(home_dir, xdg_data, ["--surface", "claude"])
            self.assertEqual(
                result.returncode, 0,
                msg=(
                    f"install.sh --surface claude exited {result.returncode}, expected 0.\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                ),
            )
            self.assertTrue(
                self._has_mcp_script(xdg_data),
                msg=(
                    "sandesh-mcp not found in venv bin after --surface claude — "
                    "mcp extra must be installed for surface=claude.\n"
                    f"venv bin: {self._venv_bin(xdg_data)}"
                ),
            )

    def test_ac1_surface_both_installs_mcp(self):
        """install.sh --surface both → venv has sandesh-mcp console script.

        RED: install.sh rejects --surface flag.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-both-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(home_dir, xdg_data, ["--surface", "both"])
            self.assertEqual(
                result.returncode, 0,
                msg=(
                    f"install.sh --surface both exited {result.returncode}, expected 0.\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                ),
            )
            self.assertTrue(
                self._has_mcp_script(xdg_data),
                msg=(
                    "sandesh-mcp not found in venv bin after --surface both — "
                    "mcp extra must be installed for surface=both."
                ),
            )

    def test_ac1_surface_pi_excludes_mcp_venv(self):
        """install.sh --surface pi → sandesh-mcp NOT in venv bin (AC2).

        RED: install.sh rejects --surface flag.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-pi-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(home_dir, xdg_data, ["--surface", "pi"])
        self.assertEqual(
            result.returncode, 0,
            msg=(
                f"install.sh --surface pi exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertFalse(
            self._has_mcp_script(xdg_data),
            msg=(
                "sandesh-mcp IS present in venv bin after --surface pi — "
                "mcp extra must NOT be installed for surface=pi (AC2).\n"
                f"venv bin: {self._venv_bin(xdg_data)}"
            ),
        )

    def test_ac2_surface_pi_excludes_mcp_symlink(self):
        """install.sh --surface pi → no $HOME/.local/bin/sandesh-mcp symlink (AC2).

        RED: install.sh rejects --surface flag.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-pi-sym-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._run_install(home_dir, xdg_data, ["--surface", "pi"])
        self.assertFalse(
            self._has_mcp_symlink(home_dir),
            msg=(
                "$HOME/.local/bin/sandesh-mcp symlink exists after --surface pi — "
                "no mcp symlink must be created for surface=pi (AC2)."
            ),
        )

    def test_ac1_surface_none_excludes_mcp(self):
        """install.sh --surface none → no sandesh-mcp in venv bin.

        RED: install.sh rejects --surface flag.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-none-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(home_dir, xdg_data, ["--surface", "none"])
        self.assertEqual(
            result.returncode, 0,
            msg=(
                f"install.sh --surface none exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertFalse(
            self._has_mcp_script(xdg_data),
            msg="sandesh-mcp IS present after --surface none — must not install mcp.",
        )

    def test_ac1_extras_env_honored_verbatim_no_mcp(self):
        """No --surface flag but SANDESH_INSTALL_EXTRAS='[migrate]' → no mcp (AC1).

        The env override is used verbatim (precedence level 2): without 'mcp'
        in the extras string, sandesh-mcp must not appear in the venv bin.

        RED: install.sh today always tries to install mcp via the fallback
        path regardless of SANDESH_INSTALL_EXTRAS content — the env is honored
        only when non-empty but the fallback still installs mcp.
        Actually the current fallback only fires if the EXTRAS install fails;
        with EXTRAS='[migrate]' the install succeeds without mcp, which IS
        the correct behaviour already for level-2 — but --surface is new (RED).
        This test pins the EXTRAS-only path: no --surface, EXTRAS='[migrate]'.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-extras-env-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(
                home_dir, xdg_data,
                extra_env={"SANDESH_INSTALL_EXTRAS": "[migrate]"},
            )
        self.assertEqual(
            result.returncode, 0,
            msg=(
                f"install.sh with SANDESH_INSTALL_EXTRAS='[migrate]' exited "
                f"{result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertFalse(
            self._has_mcp_script(xdg_data),
            msg=(
                "sandesh-mcp IS present with SANDESH_INSTALL_EXTRAS='[migrate]' — "
                "the env override must be used verbatim (no mcp in extras → no mcp)."
            ),
        )

    def test_ac1_default_no_flag_no_env_installs_mcp(self):
        """No --surface flag, no SANDESH_INSTALL_EXTRAS → default [mcp,migrate] → mcp present.

        Preserves AC6 (InstallShTest regression): the default non-interactive
        path still installs mcp.

        HARNESS NOTE: requires network/pip-cache for [mcp,migrate] deps. If the
        pip install of mcp fails (offline / no cache), the test is SKIPPED —
        same pattern as MigrateOnInstallTest. The regression guarantee only
        fires when deps are available.

        RED: this test is expected to PASS when network is available (install.sh
        default already installs [mcp,migrate]). It pins the regression guarantee.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-default-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(home_dir, xdg_data)
            # If install failed and the venv python doesn't exist → offline/no-cache skip
            venv_python = os.path.join(xdg_data, "sandesh", ".venv", "bin", "python")
            if not os.path.isfile(venv_python):
                self.skipTest(
                    "[mcp,migrate] pip install failed (likely offline/no cache) — "
                    f"install.sh rc={result.returncode}"
                )
            self.assertEqual(
                result.returncode, 0,
                msg=(
                    f"install.sh (no args, no EXTRAS env) exited {result.returncode}.\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                ),
            )
            self.assertTrue(
                self._has_mcp_script(xdg_data),
                msg=(
                    "sandesh-mcp not found after default install (no --surface, no EXTRAS) — "
                    "default must install [mcp,migrate] (AC6 regression)."
                ),
            )

    # ------------------------------------------------------------------
    # AC3 — delegates to init: admin provisioned + no inline heredoc
    # ------------------------------------------------------------------

    def test_ac3_fresh_install_admin_provisioned_via_init(self):
        """After install.sh --surface claude with SANDESH_ADMIN=tester, the store
        must have admin_name == 'tester' (provisioned by `sandesh init`).

        RED: install.sh does not yet call `sandesh init`; the inline heredoc
        runs `assign_admin` directly, so admin IS set — but via the wrong path.
        This test also asserts install.sh source contains 'sandesh init' and
        does NOT contain 'assign_admin(con', which still fails RED.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-ac3-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(
                home_dir, xdg_data, ["--surface", "claude"],
                extra_env={"SANDESH_ADMIN": "tester"},
            )
            # install must complete
            self.assertEqual(
                result.returncode, 0,
                msg=(
                    f"install.sh --surface claude exited {result.returncode}.\n"
                    f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                ),
            )
            # Verify admin was set in the store
            import sqlite3
            db_path = os.path.join(xdg_data, "sandesh", "sandesh.db")
            self.assertTrue(
                os.path.isfile(db_path),
                msg=f"sandesh.db not found at {db_path} after install.",
            )
            con = sqlite3.connect(db_path)
            try:
                row = con.execute("SELECT name FROM admin WHERE id=1").fetchone()
            finally:
                con.close()
            self.assertIsNotNone(
                row,
                msg="No admin row in sandesh.db after install with SANDESH_ADMIN=tester.",
            )
            self.assertEqual(
                row[0], "tester",
                msg=(
                    f"admin_name is {row[0]!r}, expected 'tester' after install "
                    "with SANDESH_ADMIN=tester."
                ),
            )

    def test_ac3_install_source_has_sandesh_init(self):
        """install.sh source must contain 'sandesh init' (delegation, §S2).

        RED: install.sh currently has the inline block; 'sandesh init' absent.
        """
        with open(INSTALL_SH, "r") as fh:
            source = fh.read()
        self.assertIn(
            "sandesh init",
            source,
            msg=(
                "install.sh source does not contain 'sandesh init' — "
                "provisioning must be delegated to `sandesh init` (§S2)."
            ),
        )

    def test_ac3_install_source_no_inline_admin_heredoc(self):
        """install.sh source must NOT contain 'assign_admin(con' (old inline heredoc).

        RED: install.sh currently has the inline admin heredoc with this string.
        """
        with open(INSTALL_SH, "r") as fh:
            source = fh.read()
        self.assertNotIn(
            "assign_admin(con",
            source,
            msg=(
                "install.sh source still contains 'assign_admin(con' — "
                "the inline admin heredoc must be removed when delegation is added (§S2)."
            ),
        )

    def test_ac3_fresh_install_fts_index_built(self):
        """After install.sh --surface claude with SANDESH_ADMIN=tester, the FTS
        index must exist in the store (reindex ran via `sandesh init`).

        RED: install.sh does not yet call `sandesh init` (uses --surface which
        is rejected today).
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-ac3-fts-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(
                home_dir, xdg_data, ["--surface", "claude"],
                extra_env={"SANDESH_ADMIN": "tester"},
            )
            self.assertEqual(result.returncode, 0,
                             msg=f"install.sh failed: {result.stderr[:300]}")
            import sqlite3
            db_path = os.path.join(xdg_data, "sandesh", "sandesh.db")
            con = sqlite3.connect(db_path)
            try:
                rows = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='message_fts'"
                ).fetchall()
            finally:
                con.close()
            self.assertEqual(
                len(rows), 1,
                msg=(
                    "message_fts FTS table not found in store after install — "
                    "`sandesh init` must run reindex (§S2).\n"
                    f"DB: {db_path}"
                ),
            )

    # ------------------------------------------------------------------
    # AC4 — mandatory migrate on existing behind DB
    # ------------------------------------------------------------------

    def test_ac4_existing_behind_db_no_migrate_extra_fails_loudly(self):
        """With an existing behind sandesh.db and SANDESH_INSTALL_EXTRAS=''
        (no [migrate] in venv), install.sh must exit non-zero AND the error
        must be surfaced via `sandesh init` (the delegated path).

        After §S2 delegation: `sandesh init` detects the behind store, cannot
        migrate (no [migrate] extra), prints '[sandesh] Sandesh store schema
        is behind...' to stderr, and exits 1 — install.sh propagates the exit.

        RED: install.sh does not yet call `sandesh init`. The current inline
        block's consolidate step incidentally triggers MigrationRequired via
        connect(), so the install does exit non-zero BUT the error message
        comes from the consolidate traceback, not from `sandesh init`. The
        assertion on '[sandesh]' prefix (the init error format) is what is RED:
        current output has 'sandesh.sandesh_db.MigrationRequired:' not
        '[sandesh] Sandesh store schema is behind'.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            # Pre-create a behind store (_yoyo_migration table with only baseline applied)
            _make_behind_store(xdg_data)
            result = self._run_install(
                home_dir, xdg_data,
                extra_env={"SANDESH_INSTALL_EXTRAS": ""},
            )
        # Must fail non-zero (existing behaviour; also required post-GREEN)
        self.assertNotEqual(
            result.returncode, 0,
            msg=(
                "install.sh exited 0 with a behind sandesh.db and no [migrate] extra — "
                "must fail loudly (non-zero) instead of silently skipping migration (AC4).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        # After delegation: the error must be surfaced via `sandesh init` with the
        # '[sandesh]' prefix that cmd_init uses (not a raw MigrationRequired traceback).
        combined = result.stdout + result.stderr
        self.assertIn(
            "[sandesh]",
            combined,
            msg=(
                "install.sh AC4 error output does not contain '[sandesh]' prefix — "
                "expected `sandesh init` to surface the migration-required error with "
                "its '[sandesh]' prefix (current code emits a raw traceback instead).\n"
                f"Full output:\n{combined}"
            ),
        )
        # Negative: no raw Python traceback (clean error, not incidental crash)
        self.assertNotIn(
            "Traceback (most recent call last)",
            combined,
            msg=(
                "install.sh AC4 error contains a Python traceback — "
                "after §S2 delegation the error must be a clean '[sandesh]' message, "
                "not a raw exception traceback.\n"
                f"Full output:\n{combined}"
            ),
        )

    # ------------------------------------------------------------------
    # AC5 — fresh best-effort: no DB + no [migrate] → exit 0
    # ------------------------------------------------------------------

    def test_ac5_fresh_no_db_no_migrate_extra_exits_zero(self):
        """Empty XDG + SANDESH_INSTALL_EXTRAS='' → install exits 0.

        No existing sandesh.db → `sandesh init` treats the store as fresh/current
        (no _yoyo_migration table → _store_is_behind returns False) → migrate
        skipped with notice → init continues → exit 0.

        RED: install.sh does not yet use --surface / sandesh init, so
        SANDESH_INSTALL_EXTRAS='' causes the old fallback to install base only —
        this currently exits 0 ALREADY (existing behaviour). However this test
        also implicitly requires that install.sh didn't call `sandesh init`
        (which would emit 'init: done.'). We assert exit 0 AND that 'init: done.'
        is present in output after GREEN delegates. Until delegation, this test
        is RED on the 'init: done.' assertion.
        """
        with tempfile.TemporaryDirectory(prefix="sandesh-sc-ac5-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install(
                home_dir, xdg_data,
                extra_env={"SANDESH_INSTALL_EXTRAS": ""},
            )
        self.assertEqual(
            result.returncode, 0,
            msg=(
                "install.sh exited non-zero on a fresh store with no [migrate] extra — "
                "must exit 0 (AC5 best-effort fresh install).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined = result.stdout + result.stderr
        # After delegation, `sandesh init` emits "init: done." on a fresh store
        # even without [migrate] (no behind → skips migrate, continues to completion).
        self.assertIn(
            "init: done.",
            combined,
            msg=(
                "install.sh output does not contain 'init: done.' on a fresh store "
                "with SANDESH_INSTALL_EXTRAS='' — after §S2 delegation, `sandesh init` "
                "must complete (skipping migrate) and emit 'init: done.'.\n"
                f"Full output:\n{combined}"
            ),
        )


class UninstallShTest(unittest.TestCase):
    """CR-SAN-035 C1 — install.sh --uninstall [--purge] / -h / bad-flag.

    Mirrors InstallShTest's isolated-env harness but FABRICATES a footprint
    instead of running a real pip install:
      - $XDG/sandesh/.venv/bin/  (dummy venv directory tree)
      - $XDG/sandesh/sandesh.db  (dummy data file)
      - $XDG/sandesh/projects/   (dummy data directory)
      - $HOME/.local/bin/sandesh       symlink → dummy venv/bin/sandesh
      - $HOME/.local/bin/sandesh-mcp   symlink → dummy venv/bin/sandesh-mcp
      - $HOME/.local/bin/other         plain sentinel file (must survive uninstall)

    All tests in this class are RED: install.sh today ignores --uninstall,
    --purge, -h, and --bogus, so none of the new behaviours exist yet.
    """

    def _make_footprint(self, home_dir, xdg_data):
        """Fabricate a minimal Sandesh install footprint under home_dir/xdg_data."""
        dest = os.path.join(xdg_data, "sandesh")
        venv_bin = os.path.join(dest, ".venv", "bin")
        os.makedirs(venv_bin, exist_ok=True)
        # Dummy venv executables (regular files — symlinks target these)
        for name in ("sandesh", "sandesh-mcp"):
            dummy = os.path.join(venv_bin, name)
            with open(dummy, "w") as f:
                f.write("#!/bin/sh\necho dummy\n")
            os.chmod(dummy, 0o755)
        # Dummy data store
        db_path = os.path.join(dest, "sandesh.db")
        with open(db_path, "w") as f:
            f.write("DUMMY DB\n")
        # Dummy projects directory
        projects_dir = os.path.join(dest, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        # Dummy project data so projects/ is non-empty
        proj_dir = os.path.join(projects_dir, "TestProject", "messages")
        os.makedirs(proj_dir, exist_ok=True)
        with open(os.path.join(proj_dir, "msg-001.md"), "w") as f:
            f.write("# test message\n")
        # Symlinks in $BINDIR
        bindir = os.path.join(home_dir, ".local", "bin")
        os.makedirs(bindir, exist_ok=True)
        os.symlink(os.path.join(venv_bin, "sandesh"), os.path.join(bindir, "sandesh"))
        os.symlink(os.path.join(venv_bin, "sandesh-mcp"), os.path.join(bindir, "sandesh-mcp"))
        # Sentinel sibling file that must NOT be removed by uninstall
        with open(os.path.join(bindir, "other"), "w") as f:
            f.write("sentinel\n")

    def _run_install_sh(self, home_dir, xdg_data, args, timeout=30):
        """Run install.sh with given args in an isolated env; return CompletedProcess."""
        env = {
            **os.environ,
            "HOME": home_dir,
            "XDG_DATA_HOME": xdg_data,
            # Prevent real pip/venv operations from running if install body fires
            "SANDESH_INSTALL_EXTRAS": "",
        }
        return subprocess.run(
            ["bash", INSTALL_SH] + args,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    # ------------------------------------------------------------------
    # AC1 — --uninstall removes software, keeps data
    # ------------------------------------------------------------------

    def test_ac1_uninstall_exits_zero(self):
        """install.sh --uninstall must exit 0."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"install.sh --uninstall exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac1_uninstall_removes_sandesh_symlink(self):
        """install.sh --uninstall must remove $BINDIR/sandesh."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            sandesh_link = os.path.join(home_dir, ".local", "bin", "sandesh")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.lexists(sandesh_link),
                msg=f"$BINDIR/sandesh still exists after --uninstall: {sandesh_link}",
            )

    def test_ac1_uninstall_removes_sandesh_mcp_symlink(self):
        """install.sh --uninstall must remove $BINDIR/sandesh-mcp."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            mcp_link = os.path.join(home_dir, ".local", "bin", "sandesh-mcp")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.lexists(mcp_link),
                msg=f"$BINDIR/sandesh-mcp still exists after --uninstall: {mcp_link}",
            )

    def test_ac1_uninstall_removes_venv(self):
        """install.sh --uninstall must remove $VENV ($DEST/.venv)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            venv_dir = os.path.join(xdg_data, "sandesh", ".venv")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(venv_dir),
                msg=f"$VENV still exists after --uninstall: {venv_dir}",
            )

    def test_ac1_uninstall_keeps_sandesh_db(self):
        """install.sh --uninstall (without --purge) must keep sandesh.db."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            db_path = os.path.join(xdg_data, "sandesh", "sandesh.db")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isfile(db_path),
                msg=f"sandesh.db was removed by --uninstall (without --purge); expected it to be kept: {db_path}",
            )

    def test_ac1_uninstall_keeps_projects_dir(self):
        """install.sh --uninstall (without --purge) must keep projects/."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            projects_dir = os.path.join(xdg_data, "sandesh", "projects")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isdir(projects_dir),
                msg=f"projects/ was removed by --uninstall (without --purge); expected it to be kept: {projects_dir}",
            )

    def test_ac1_uninstall_stdout_contains_mcp_remove_reminder(self):
        """install.sh --uninstall stdout must contain 'claude mcp remove sandesh'."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        combined = result.stdout + result.stderr
        self.assertIn(
            "claude mcp remove sandesh",
            combined,
            msg=(
                "install.sh --uninstall output does not contain the required "
                "'claude mcp remove sandesh' reminder.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac1_uninstall_stdout_contains_data_kept_note(self):
        """install.sh --uninstall stdout must note that the data store was kept."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac1-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        combined = result.stdout + result.stderr
        # The output must contain some phrase indicating data was preserved
        data_kept_phrases = ("data kept", "data store kept", "store kept", "--purge")
        self.assertTrue(
            any(phrase in combined.lower() for phrase in data_kept_phrases),
            msg=(
                "install.sh --uninstall output does not contain a 'data kept' note "
                f"(looked for: {data_kept_phrases}).\n"
                f"Full output:\n{combined}"
            ),
        )

    # ------------------------------------------------------------------
    # AC2 — --uninstall --purge removes data too
    # ------------------------------------------------------------------

    def test_ac2_purge_exits_zero(self):
        """install.sh --uninstall --purge must exit 0."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac2-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"install.sh --uninstall --purge exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac2_purge_removes_sandesh_symlink(self):
        """install.sh --uninstall --purge must remove $BINDIR/sandesh."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac2-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            sandesh_link = os.path.join(home_dir, ".local", "bin", "sandesh")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.lexists(sandesh_link),
                msg=f"$BINDIR/sandesh still exists after --uninstall --purge: {sandesh_link}",
            )

    def test_ac2_purge_removes_entire_dest(self):
        """install.sh --uninstall --purge must remove the entire $DEST directory."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac2-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            dest = os.path.join(xdg_data, "sandesh")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(dest),
                msg=(
                    f"$DEST ({dest}) still exists after --uninstall --purge; "
                    "expected the entire directory to be removed."
                ),
            )

    def test_ac2_purge_removes_sandesh_db(self):
        """install.sh --uninstall --purge must remove sandesh.db."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac2-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            db_path = os.path.join(xdg_data, "sandesh", "sandesh.db")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(db_path),
                msg=f"sandesh.db still exists after --uninstall --purge: {db_path}",
            )

    def test_ac2_purge_removes_projects_dir(self):
        """install.sh --uninstall --purge must remove the projects/ directory."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac2-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            projects_dir = os.path.join(xdg_data, "sandesh", "projects")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(projects_dir),
                msg=f"projects/ still exists after --uninstall --purge: {projects_dir}",
            )

    # ------------------------------------------------------------------
    # AC3 — idempotent: second --uninstall on already-clean env exits 0
    # ------------------------------------------------------------------

    def test_ac3_idempotent_exits_zero_on_clean_env(self):
        """install.sh --uninstall on an already-clean env must exit 0 (no error)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac3-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            # Create only BINDIR — no sandesh footprint at all
            bindir = os.path.join(home_dir, ".local", "bin")
            os.makedirs(bindir)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"install.sh --uninstall on a clean env exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac3_idempotent_second_run_exits_zero(self):
        """Running --uninstall twice must exit 0 both times (idempotent)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac3-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            # First uninstall
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            # Second uninstall — must also exit 0
            result2 = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        self.assertEqual(
            result2.returncode,
            0,
            msg=(
                f"Second install.sh --uninstall exited {result2.returncode}, expected 0.\n"
                f"STDOUT:\n{result2.stdout}\nSTDERR:\n{result2.stderr}"
            ),
        )

    def test_ac3_idempotent_stdout_contains_already_removed_notice(self):
        """install.sh --uninstall on a clean env must print an 'already removed' / 'nothing to do' notice."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac3-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            bindir = os.path.join(home_dir, ".local", "bin")
            os.makedirs(bindir)
            result = self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        combined = result.stdout + result.stderr
        already_phrases = ("already removed", "nothing to do", "already uninstalled", "not found")
        self.assertTrue(
            any(phrase in combined.lower() for phrase in already_phrases),
            msg=(
                "install.sh --uninstall on a clean env does not print an "
                f"'already removed / nothing to do' notice (looked for: {already_phrases}).\n"
                f"Full output:\n{combined}"
            ),
        )

    # ------------------------------------------------------------------
    # AC4 — -h / --help exits 0 with usage; unknown flag exits 2
    # ------------------------------------------------------------------

    def test_ac4_help_short_flag_exits_zero(self):
        """install.sh -h must exit 0."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["-h"])
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"install.sh -h exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_help_long_flag_exits_zero(self):
        """install.sh --help must exit 0."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["--help"])
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"install.sh --help exited {result.returncode}, expected 0.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_help_stdout_mentions_uninstall(self):
        """install.sh -h usage block must mention '--uninstall'."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["-h"])
        combined = result.stdout + result.stderr
        self.assertIn(
            "--uninstall",
            combined,
            msg=(
                "install.sh -h output does not contain '--uninstall'.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac4_help_stdout_mentions_purge(self):
        """install.sh -h usage block must mention '--purge'."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["-h"])
        combined = result.stdout + result.stderr
        self.assertIn(
            "--purge",
            combined,
            msg=(
                "install.sh -h output does not contain '--purge'.\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac4_help_stdout_mentions_install_default(self):
        """install.sh -h usage block must describe the default install mode."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["-h"])
        combined = result.stdout + result.stderr
        install_phrases = ("install.sh", "./install.sh", "install")
        self.assertTrue(
            any(phrase in combined for phrase in install_phrases),
            msg=(
                "install.sh -h output does not name the default install mode "
                f"(looked for: {install_phrases}).\n"
                f"Full output:\n{combined}"
            ),
        )

    def test_ac4_help_does_not_start_install(self):
        """install.sh -h must NOT build a venv (install body must not run)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._run_install_sh(home_dir, xdg_data, ["-h"])
            venv_dir = os.path.join(xdg_data, "sandesh", ".venv")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(venv_dir),
                msg=(
                    f"install.sh -h built a venv at {venv_dir} — the install body "
                    "must not run when -h is given."
                ),
            )

    def test_ac4_unknown_flag_exits_two(self):
        """install.sh --bogus must exit 2."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["--bogus"])
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"install.sh --bogus exited {result.returncode}, expected 2.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_unknown_flag_usage_on_stderr(self):
        """install.sh --bogus must print usage to stderr (not only stdout)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            result = self._run_install_sh(home_dir, xdg_data, ["--bogus"])
        self.assertTrue(
            len(result.stderr.strip()) > 0,
            msg=(
                "install.sh --bogus produced no stderr output — expected usage on stderr.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_unknown_flag_does_not_build_venv(self):
        """install.sh --bogus must NOT build a venv (install body must not run)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac4-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._run_install_sh(home_dir, xdg_data, ["--bogus"])
            venv_dir = os.path.join(xdg_data, "sandesh", ".venv")
            # Assert inside the with block while tmp still exists
            self.assertFalse(
                os.path.exists(venv_dir),
                msg=(
                    f"install.sh --bogus built a venv at {venv_dir} — the install body "
                    "must not run when an unknown flag is given."
                ),
            )

    # ------------------------------------------------------------------
    # AC6 — scoping safety: sentinel file + $BINDIR itself survive
    # ------------------------------------------------------------------

    def test_ac6_sentinel_other_file_survives_uninstall(self):
        """install.sh --uninstall must not remove $BINDIR/other (sibling file)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac6-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)  # creates $BINDIR/other
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            other_file = os.path.join(home_dir, ".local", "bin", "other")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isfile(other_file),
                msg=(
                    f"$BINDIR/other was removed by --uninstall — uninstall must only "
                    f"remove the two sandesh* symlinks.\n"
                    f"Expected sentinel at: {other_file}"
                ),
            )

    def test_ac6_bindir_itself_survives_uninstall(self):
        """install.sh --uninstall must not remove $BINDIR itself."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac6-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
            bindir = os.path.join(home_dir, ".local", "bin")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isdir(bindir),
                msg=(
                    f"$BINDIR ({bindir}) was removed by --uninstall — "
                    "uninstall must never delete $BINDIR itself."
                ),
            )

    def test_ac6_sentinel_other_file_survives_purge(self):
        """install.sh --uninstall --purge must not remove $BINDIR/other."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac6-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            other_file = os.path.join(home_dir, ".local", "bin", "other")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isfile(other_file),
                msg=(
                    f"$BINDIR/other was removed by --uninstall --purge — purge must "
                    f"only extend removal to $DEST, not to sibling files in $BINDIR.\n"
                    f"Expected sentinel at: {other_file}"
                ),
            )

    def test_ac6_bindir_itself_survives_purge(self):
        """install.sh --uninstall --purge must not remove $BINDIR itself."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac6-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall", "--purge"])
            bindir = os.path.join(home_dir, ".local", "bin")
            # Assert inside the with block — tmp survives until we exit
            self.assertTrue(
                os.path.isdir(bindir),
                msg=(
                    f"$BINDIR ({bindir}) was removed by --uninstall --purge — "
                    "purge must not delete $BINDIR itself."
                ),
            )

    def test_ac6_source_checkout_untouched_by_uninstall(self):
        """install.sh --uninstall must not remove files from the source checkout ($SRC)."""
        with tempfile.TemporaryDirectory(prefix="sandesh-uninstall-ac6-") as tmp:
            home_dir = os.path.join(tmp, "home")
            xdg_data = os.path.join(home_dir, ".local", "share")
            os.makedirs(home_dir)
            self._make_footprint(home_dir, xdg_data)
            self._run_install_sh(home_dir, xdg_data, ["--uninstall"])
        # install.sh itself must still exist in $SRC
        self.assertTrue(
            os.path.isfile(INSTALL_SH),
            msg=f"install.sh was removed from the source checkout: {INSTALL_SH}",
        )
        # The sandesh package directory must still exist
        pkg_dir = os.path.join(REPO, "sandesh")
        self.assertTrue(
            os.path.isdir(pkg_dir),
            msg=f"sandesh/ package dir was removed from the source checkout: {pkg_dir}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
