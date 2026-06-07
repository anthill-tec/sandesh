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


if __name__ == "__main__":
    unittest.main(verbosity=2)
