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
    """AC2 — installer tolerates a missing [migrate] extra.

    Run install.sh with SANDESH_INSTALL_EXTRAS="" (empty string forces a
    stdlib-only base install — no [migrate] deps).  Assert:
      - install exits 0 (completes successfully)
      - output contains a "migrations skipped" notice naming [migrate]
      - installer did NOT abort (the migrate step was skipped, not failed)

    This test exercises the DEC-3 "missing-extra path": when [migrate] is
    absent, install.sh must skip migrate --all, print the notice, and succeed.

    RED: install.sh does not yet print the skip notice (it prints a different
    NOTE about [mcp] only), so the notice assertion fails.
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

    def test_ac2_output_contains_migrations_skipped_notice(self):
        """install.sh output must contain a 'migrations skipped' notice that
        names the [migrate] extra and the sandesh migrate --all command.

        RED: install.sh does not yet emit this notice (it currently only
        prints the [mcp] fallback notice — no [migrate] skip message).
        """
        self._check_setup()
        combined = self.install_result.stdout + self.install_result.stderr
        # The notice must name the [migrate] extra.
        self.assertIn(
            "[migrate]",
            combined,
            msg=(
                "install.sh output does not contain '[migrate]' — expected a "
                "'migrations skipped — install [migrate]' notice.\n"
                f"Full output:\n{combined}"
            ),
        )
        # The notice must mention how to migrate later.
        self.assertTrue(
            any(
                phrase in combined
                for phrase in ("migrations skipped", "migrate --all", "sandesh migrate")
            ),
            msg=(
                "install.sh output does not contain a 'migrations skipped' / "
                "'sandesh migrate --all' hint.\n"
                f"Full output:\n{combined}"
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
    """AC1 — installer runs migrate --all and upgrades existing stores.

    Pre-creates a store in the temp XDG_DATA_HOME that has the old schema
    (message.status present, no _yoyo_migration).  Runs install.sh with
    SANDESH_INSTALL_EXTRAS=[mcp,migrate] so the installer venv gets the
    [migrate] deps (yoyo + jsonschema).  After install, asserts:
      - the pre-migration store is fully migrated (0 pending via installed
        `sandesh migrate --status`)
      - message.status column is GONE from the store
      - _yoyo_migration table exists (yoyo recorded the migrations)

    HARNESS NOTE: requires network/pip-cache for [mcp,migrate] deps.  If
    the pip install fails (offline / no cache), the test is SKIPPED — same
    pattern as McpExtraInstallTest.  Flag for orchestrator: if this env has
    no network/cache, AC1/AC3 will report as SKIPPED, not RED.  The RED
    assertions about migration status only fire when deps are available.

    RED: install.sh does not yet run migrate --all, so after install the
    store still has message.status (not migrated) and status reports pending.
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

        RED: install.sh does not yet run migrate --all → status column still
        present after install.
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

        RED: install.sh does not run migrate --all → no _yoyo_migration table.
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
        """After install.sh with [migrate], `sandesh migrate --status --project X`
        must report 0 pending migrations for the pre-migration store.

        RED: install.sh does not run migrate --all → status shows pending
        (0002-drop-message-status is pending on the store).
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
    """AC3 — fresh install with no existing stores: migrate --all is a clean no-op.

    Empty XDG_DATA_HOME (no projects/ stores at all).  Run install.sh with
    SANDESH_INSTALL_EXTRAS=[mcp,migrate].  Assert:
      - install exits 0
      - no error/traceback in output (the migrate step over zero stores is silent)
      - the projects/ dir is empty (no stores were created as a side effect)

    HARNESS NOTE: same offline/network caveat as MigrateOnInstallTest — skips
    if [mcp,migrate] pip install fails.

    RED: currently install.sh does not call migrate --all at all, so this test
    passes trivially (no migrate step = no migrate error).  It acts as a guard:
    after GREEN adds the hook, the fresh-install path must remain clean.
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

        RED (guard): install.sh currently has no migrate step — passes now.
        After GREEN adds the hook, this must remain GREEN (no-op path).
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

        RED (guard): no migrate step currently, so no migrate error possible.
        After GREEN adds the hook, `migrate --all` over zero stores exits 0
        silently — no traceback must appear.
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

        Checks that no 'migrate --all aborted' or 'Error:' line appeared —
        confirming the zero-stores path is truly a no-op.
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
        running migrate --all on an empty data-home.

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
