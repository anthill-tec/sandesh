"""test_package.py — package surface contract tests (CR-SAN-008 §S1/AC1/AC5).

Asserts that the `sandesh` package is importable and that the four modules
(cli, sandesh_db, notify, mcp_server) are all accessible as submodules, and
that the entry-point callables (cli.main, mcp_server.main) exist.

These tests FAIL at RED (the `sandesh/` package does not exist yet) with a
ModuleNotFoundError at collection time, which is the correct RED signal.

Runs under the [mcp] venv (same as the other MCP suites) because importing
mcp_server pulls in `mcp`.

  python-crucible.py test --tests tests.test_package --agent CR-SAN-008-C0-RED
"""

import unittest


class PackageImportableTest(unittest.TestCase):
    """AC1 + AC5: the sandesh package and all four modules are importable."""

    def test_sandesh_package_is_importable(self):
        """AC1: `import sandesh` succeeds and sandesh.__name__ == 'sandesh'."""
        import sandesh
        self.assertEqual(sandesh.__name__, "sandesh")

    def test_sandesh_cli_submodule_importable(self):
        """AC1: `from sandesh import cli` succeeds."""
        from sandesh import cli  # noqa: F401
        self.assertIsNotNone(cli)

    def test_sandesh_sandesh_db_submodule_importable(self):
        """AC1: `from sandesh import sandesh_db` succeeds."""
        from sandesh import sandesh_db  # noqa: F401
        self.assertIsNotNone(sandesh_db)

    def test_sandesh_notify_submodule_importable(self):
        """AC1: `from sandesh import notify` succeeds."""
        from sandesh import notify  # noqa: F401
        self.assertIsNotNone(notify)

    def test_sandesh_mcp_server_submodule_importable(self):
        """AC1: `from sandesh import mcp_server` succeeds."""
        from sandesh import mcp_server  # noqa: F401
        self.assertIsNotNone(mcp_server)


class EntryPointCallablesTest(unittest.TestCase):
    """AC5: cli.main and mcp_server.main are callable entry-point targets."""

    def test_cli_main_is_callable(self):
        """AC5: cli.main is importable and callable (entry point for the `sandesh` script)."""
        from sandesh import cli
        self.assertTrue(
            callable(cli.main),
            f"sandesh.cli.main must be callable; got {type(cli.main)!r}",
        )

    def test_mcp_server_main_is_callable(self):
        """AC5: mcp_server.main is importable and callable (entry point for the `sandesh-mcp` script)."""
        from sandesh import mcp_server
        self.assertTrue(
            callable(mcp_server.main),
            f"sandesh.mcp_server.main must be callable; got {type(mcp_server.main)!r}",
        )

    def test_cli_main_is_not_none(self):
        """AC5: cli.main is a defined (not None) callable — not an empty attribute."""
        from sandesh import cli
        self.assertIsNotNone(
            cli.main,
            "sandesh.cli.main must not be None",
        )

    def test_mcp_server_main_is_not_none(self):
        """AC5: mcp_server.main is a defined (not None) callable."""
        from sandesh import mcp_server
        self.assertIsNotNone(
            mcp_server.main,
            "sandesh.mcp_server.main must not be None",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
