"""test_mcp_missing_extra.py — AC8: friendly error when the [mcp] extra is absent.

§S6 of CR-SAN-008 requires that when `sandesh-mcp` (i.e. `sandesh.mcp_server:main`)
is invoked in an environment without the `mcp` package, the program:
  - exits non-zero, AND
  - prints a clear, actionable message naming the install fix (mentioning '[mcp]'
    and an install command), AND
  - does NOT let a raw ImportError / traceback surface to the user.

The `sandesh` CLI must remain working in the same mcp-absent environment.

Technique: inject ``sys.modules['mcp'] = None`` before importing the server module.
Setting a module key to None makes any subsequent ``import mcp`` / ``from mcp ...``
raise ImportError, faithfully simulating a base (no-[mcp]) install without needing
a separate interpreter.

Run this suite against the project venv (same interpreter the entry point uses):
  python-crucible.py test --tests tests.test_mcp_missing_extra --agent CR-SAN-008-C2-RED
"""

import os
import subprocess
import sys
import unittest

# Resolve the repo root relative to this test file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class McpMissingExtraFriendlyErrorTest(unittest.TestCase):
    """AC8a — sandesh-mcp prints a friendly, actionable message when mcp is absent."""

    def test_mcp_absent_exits_nonzero(self):
        """AC8a: invoking mcp_server.main() without mcp installed must exit non-zero."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"        # force ImportError on any mcp import
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertNotEqual(
            r.returncode,
            0,
            f"sandesh-mcp must exit non-zero when mcp is absent; got exit code {r.returncode}.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_mcp_absent_mentions_mcp_extra_bracket(self):
        """AC8a: the friendly message must contain '[mcp]' (names the extra to install)."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = (r.stdout + r.stderr).lower()
        self.assertIn(
            "[mcp]",
            combined,
            "Friendly error must mention '[mcp]' (the extra name) so the user knows what to install.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_mcp_absent_mentions_install_command(self):
        """AC8a: the friendly message must mention an install command ('install', 'pipx', or 'pip install')."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = (r.stdout + r.stderr).lower()
        has_install_hint = (
            "install" in combined
            or "pipx" in combined
            or "pip install" in combined
        )
        self.assertTrue(
            has_install_hint,
            "Friendly error must name an install fix ('install', 'pipx', or 'pip install').\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_mcp_absent_no_raw_traceback(self):
        """AC8a: the output must NOT contain a raw Python traceback."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = r.stdout + r.stderr
        self.assertNotIn(
            "Traceback (most recent call last)",
            combined,
            "A raw Python traceback must NOT surface when mcp is absent — only the friendly message.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_mcp_absent_no_raw_module_not_found_error(self):
        """AC8a: 'ModuleNotFoundError' must NOT appear in the user-visible output."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = r.stdout + r.stderr
        self.assertNotIn(
            "ModuleNotFoundError",
            combined,
            "'ModuleNotFoundError' must not be visible — it reveals the raw exception, not the friendly message.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_mcp_absent_no_raw_import_error(self):
        """AC8a: 'ImportError' must NOT appear in the user-visible output."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import mcp_server\n"
            "mcp_server.main()\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = r.stdout + r.stderr
        self.assertNotIn(
            "ImportError",
            combined,
            "'ImportError' must not be visible — the friendly message should replace the raw exception.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


class McpMissingExtraCliUnaffectedTest(unittest.TestCase):
    """AC8b — the sandesh CLI still works when mcp is absent."""

    def test_cli_help_exits_zero_with_mcp_absent(self):
        """AC8b: `sandesh --help` exits 0 even when sys.modules['mcp'] = None."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "sys.argv = ['sandesh', '--help']\n"
            "from sandesh import cli\n"
            "try:\n"
            "    cli.main()\n"
            "except SystemExit as e:\n"
            "    sys.exit(e.code)\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(
            r.returncode,
            0,
            "sandesh CLI must exit 0 for --help even when mcp is absent "
            "(CLI must be import-clean of mcp).\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_cli_help_output_contains_usage_with_mcp_absent(self):
        """AC8b: --help output contains usage/help text (CLI is fully functional without mcp)."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "sys.argv = ['sandesh', '--help']\n"
            "from sandesh import cli\n"
            "try:\n"
            "    cli.main()\n"
            "except SystemExit as e:\n"
            "    sys.exit(e.code)\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        combined = r.stdout + r.stderr
        # argparse --help prints "usage:" to stdout
        self.assertIn(
            "usage",
            combined.lower(),
            "CLI --help must produce usage text when mcp is absent.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )

    def test_cli_does_not_import_mcp_at_module_level(self):
        """AC8b: importing sandesh.cli with mcp blocked must not raise any error."""
        code = (
            "import sys\n"
            "sys.modules['mcp'] = None\n"
            "from sandesh import cli\n"
            "print('cli imported ok')\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": _REPO_ROOT},
        )
        self.assertEqual(
            r.returncode,
            0,
            "sandesh.cli must be importable without the mcp package.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )
        self.assertIn(
            "cli imported ok",
            r.stdout,
            "Expected 'cli imported ok' in stdout to confirm clean import.\n"
            f"stdout: {r.stdout!r}\nstderr: {r.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
