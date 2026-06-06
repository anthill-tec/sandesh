"""test_install.py — integration tests for install.sh venv + sandesh-mcp wrapper.

Tests AC1-AC3 from CR-SAN-001 §S1:
  AC1: install.sh creates <DEST>/.venv and installs mcp (>=1.27,<2) into it.
  AC2: install.sh writes bin/sandesh-mcp that execs the venv python on
       app/mcp_server.py, symlinks it onto PATH; running it starts the server.
  AC3: python3 -c "import cli" (or running bin/sandesh) succeeds with NO
       third-party package available — only mcp_server.py imports mcp.

Run once from repo root:
  python3 -m unittest tests.test_install -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")


class InstallTest(unittest.TestCase):
    """Integration tests for install.sh.  setUpClass runs install.sh once."""

    tmp = None
    dest = None
    install_result = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sandesh-install-test-")
        xdg_data = os.path.join(cls.tmp, "data")
        cls.dest = os.path.join(xdg_data, "sandesh")
        env = {
            **os.environ,
            "HOME": cls.tmp,
            "XDG_DATA_HOME": xdg_data,
        }
        cls.install_result = subprocess.run(
            ["bash", INSTALL_SH],
            env=env,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls):
        if cls.tmp and os.path.isdir(cls.tmp):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _install_ok(self):
        """Assert install.sh exited 0, printing diagnostics on failure."""
        self.assertEqual(
            self.install_result.returncode,
            0,
            msg=(
                f"install.sh exited {self.install_result.returncode}\n"
                f"STDOUT:\n{self.install_result.stdout}\n"
                f"STDERR:\n{self.install_result.stderr}"
            ),
        )

    # ------------------------------------------------------------------ #
    # AC1 — venv created and mcp importable from it
    # ------------------------------------------------------------------ #

    def test_ac1_install_exits_zero(self):
        """install.sh must complete without error."""
        self._install_ok()

    def test_ac1_venv_python_exists(self):
        """install.sh must create <DEST>/.venv with a python interpreter."""
        self._install_ok()
        venv_python = os.path.join(self.dest, ".venv", "bin", "python")
        self.assertTrue(
            os.path.isfile(venv_python),
            msg=f"Expected venv python at {venv_python}; DEST contents: {os.listdir(self.dest) if os.path.isdir(self.dest) else 'DEST missing'}",
        )

    def test_ac1_mcp_importable_from_venv(self):
        """The venv python must be able to import the mcp package."""
        self._install_ok()
        venv_python = os.path.join(self.dest, ".venv", "bin", "python")
        result = subprocess.run(
            [venv_python, "-c", "import mcp; print('ok')"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"'import mcp' failed in venv python.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
        )

    def test_ac1_mcp_version_in_range(self):
        """mcp installed in venv must satisfy >=1.27,<2."""
        self._install_ok()
        venv_python = os.path.join(self.dest, ".venv", "bin", "python")
        result = subprocess.run(
            [
                venv_python,
                "-c",
                (
                    "import importlib.metadata, sys; "
                    "v = importlib.metadata.version('mcp'); "
                    "parts = [int(x) for x in v.split('.')[:2]]; "
                    "ok = (parts[0] == 1 and parts[1] >= 27); "
                    "print(v); sys.exit(0 if ok else 1)"
                ),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"mcp version not in >=1.27,<2. stdout={result.stdout.strip()} stderr={result.stderr.strip()}",
        )

    # ------------------------------------------------------------------ #
    # AC2 — sandesh-mcp wrapper written, executable, symlinked onto PATH
    # ------------------------------------------------------------------ #

    def test_ac2_sandesh_mcp_file_exists(self):
        """install.sh must write bin/sandesh-mcp inside DEST."""
        self._install_ok()
        wrapper = os.path.join(self.dest, "bin", "sandesh-mcp")
        self.assertTrue(
            os.path.isfile(wrapper),
            msg=f"Expected {wrapper}; bin/ contents: {os.listdir(os.path.join(self.dest, 'bin')) if os.path.isdir(os.path.join(self.dest, 'bin')) else 'bin/ missing'}",
        )

    def test_ac2_sandesh_mcp_is_executable(self):
        """bin/sandesh-mcp must be executable."""
        self._install_ok()
        wrapper = os.path.join(self.dest, "bin", "sandesh-mcp")
        self.assertTrue(
            os.access(wrapper, os.X_OK),
            msg=f"{wrapper} exists but is not executable",
        )

    def test_ac2_wrapper_references_venv_python(self):
        """The wrapper script must reference the venv python interpreter."""
        self._install_ok()
        wrapper = os.path.join(self.dest, "bin", "sandesh-mcp")
        with open(wrapper) as fh:
            content = fh.read()
        # Must contain either an explicit reference to .venv/bin/python
        # or use a shebang that resolves to it.
        self.assertIn(
            ".venv",
            content,
            msg=f"wrapper content does not reference .venv:\n{content}",
        )

    def test_ac2_wrapper_references_mcp_server(self):
        """The wrapper script must reference app/mcp_server.py."""
        self._install_ok()
        wrapper = os.path.join(self.dest, "bin", "sandesh-mcp")
        with open(wrapper) as fh:
            content = fh.read()
        self.assertIn(
            "mcp_server.py",
            content,
            msg=f"wrapper content does not reference mcp_server.py:\n{content}",
        )

    def test_ac2_path_symlink_exists(self):
        """install.sh must create a ~/.local/bin/sandesh-mcp symlink."""
        self._install_ok()
        # Under our sandboxed HOME, ~/.local/bin = <tmp>/.local/bin
        local_bin_symlink = os.path.join(self.tmp, ".local", "bin", "sandesh-mcp")
        self.assertTrue(
            os.path.lexists(local_bin_symlink),
            msg=f"Expected symlink at {local_bin_symlink}; .local/bin contents: "
            f"{os.listdir(os.path.join(self.tmp, '.local', 'bin')) if os.path.isdir(os.path.join(self.tmp, '.local', 'bin')) else 'dir missing'}",
        )

    def test_ac2_launch_smoke_no_traceback(self):
        """Running sandesh-mcp must not produce a Traceback or ModuleNotFoundError.

        The server is a stdio MCP server — it will block waiting for JSON-RPC
        input. We launch it with DEVNULL stdin and a short timeout; a timeout
        means it started and is waiting (acceptable pass). Only import-time
        crashes count as failure.
        """
        self._install_ok()
        wrapper = os.path.join(self.dest, "bin", "sandesh-mcp")
        if not os.path.isfile(wrapper):
            self.skipTest("sandesh-mcp wrapper missing (covered by earlier tests)")
        try:
            result = subprocess.run(
                [wrapper],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            # If it actually exited within 5 s, check stderr for crash markers
            stderr = result.stderr
        except subprocess.TimeoutExpired as exc:
            # Server started and is waiting for input — this is the expected path
            stderr = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")

        self.assertNotIn(
            "Traceback",
            stderr,
            msg=f"sandesh-mcp produced a Traceback on startup:\n{stderr}",
        )
        self.assertNotIn(
            "ModuleNotFoundError",
            stderr,
            msg=f"sandesh-mcp hit ModuleNotFoundError on startup:\n{stderr}",
        )

    # ------------------------------------------------------------------ #
    # AC3 — CLI works with system python (no third-party packages needed)
    # ------------------------------------------------------------------ #

    def test_ac3_cli_help_needs_no_third_party(self):
        """The installed CLI must run with the system python3 (no venv).

        app/ is the script dir so sandesh_db imports work via relative path.
        This proves only mcp_server.py imports mcp, not the core CLI.
        """
        self._install_ok()
        cli_path = os.path.join(self.dest, "app", "cli.py")
        # Use the system python3 explicitly — NOT the venv python
        system_python = sys.executable
        result = subprocess.run(
            [system_python, cli_path, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"CLI --help failed with system python (returncode={result.returncode}).\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}\n"
                "This means cli.py or a module it imports requires a third-party package."
            ),
        )
        # Also verify no mcp import is attempted by the CLI path
        self.assertNotIn(
            "ModuleNotFoundError",
            result.stderr,
            msg=f"CLI --help hit ModuleNotFoundError:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
