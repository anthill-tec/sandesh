"""test_docs_restructure.py — CR-SAN-039 C1 RED tests.

Asserts the docs restructure: README slimmed (multi-route install blocks removed,
pointer to install guide added), docs/INSTALL.md created with per-surface sections,
AUR install route removed from README, uninstall matrix present in install guide,
and the mcp-name ownership marker preserved in README.

AC1 — README slimmed (FAILS now — install command blocks still present, no link)
AC2 — docs/INSTALL.md exists with per-surface content (FAILS now — file absent)
AC3 — AUR install route removed from README (FAILS now — yay/paru/AUR section present)
AC4 — uninstall matrix present in docs/INSTALL.md (FAILS now — file absent)
AC6 — mcp-name marker preserved in README (PASSES now — guard; must stay green)

Run targeted:
  python3 -m unittest tests.test_docs_restructure -v
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_docs_restructure \\
      --agent CR-SAN-039-C1-RED
"""

import os
import re
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_README_PATH = os.path.join(_REPO_ROOT, "README.md")
_INSTALL_MD_PATH = os.path.join(_REPO_ROOT, "docs", "INSTALL.md")

# ── Constants ─────────────────────────────────────────────────────────────────

# README ownership marker — must be preserved at all times (AC6 guard)
_MCP_MARKER = "mcp-name: io.github.anthill-tec/sandesh"

# Install-command tokens that must NOT appear in a slimmed README (AC1)
_README_FORBIDDEN_INSTALL_TOKENS = [
    "uv tool install 'sandesh-relay",
    "pipx install 'sandesh-relay",
]

# AUR tokens that must NOT appear in README (AC3)
_AUR_FORBIDDEN_TOKENS = [
    "yay ",
    "paru ",
    "### Arch Linux (AUR)",
]

# Tokens that must appear in docs/INSTALL.md (AC2)
_INSTALL_SURFACE_TOKENS = [
    "Claude",        # Claude surface section
    "Pi",            # Pi surface section
]
_INSTALL_LIFECYCLE_TOKENS = [
    "install",       # install section
    "sandesh init",  # provisioning command
    "uninstall",     # uninstall section
]
_INSTALL_EXTRA_TOKENS = [
    "[mcp]",                             # Claude path — mcp extra
    "uvx --from 'sandesh-relay[migrate]'",  # Pi path — uvx on-demand (no mcp)
]

# Tokens required in docs/INSTALL.md uninstall matrix (AC4)
_UNINSTALL_MATRIX_TOKENS = [
    "install.sh --uninstall",   # install.sh route uninstall
    "uv tool uninstall",        # uv route uninstall
    "pipx uninstall",           # pipx route uninstall
    "pip uninstall",            # pip route uninstall
    "claude mcp remove sandesh",  # manual MCP step
]
# Data store — either literal path or XDG var
_UNINSTALL_DATA_STORE_PATTERNS = [
    r"~/\.local/share/sandesh",
    r"\$XDG_DATA_HOME",
]


def _read(path: str) -> str:
    """Return full text of a file, or '' if it does not exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


# ── AC6 — mcp-name marker preserved (guard — PASSES now) ─────────────────────

class ReadmeMcpMarkerPreservedTest(unittest.TestCase):
    """AC6: README.md must STILL contain the mcp-name ownership marker.

    This is a regression guard — it must stay GREEN before and after the docs
    restructure.  GREEN must not lose the marker while slimming the README.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = _read(_README_PATH)

    def test_readme_contains_mcp_name_marker(self):
        """AC6: README must contain 'mcp-name: io.github.anthill-tec/sandesh'.

        PASSES now (guard); must continue to pass after GREEN.
        """
        self.assertTrue(
            os.path.isfile(_README_PATH),
            f"README.md not found at {_README_PATH}",
        )
        self.assertIn(
            _MCP_MARKER,
            self.readme,
            f"README.md must contain the ownership marker {_MCP_MARKER!r}. "
            "It must be preserved through the docs restructure. "
            "An HTML comment form is acceptable: "
            "<!-- mcp-name: io.github.anthill-tec/sandesh -->",
        )


# ── AC1 — README slimmed ──────────────────────────────────────────────────────

class ReadmeSlimmedTest(unittest.TestCase):
    """AC1: README must no longer contain multi-route install command blocks,
    and must contain a pointer/link to the install guide.

    FAILS now — the README still has the full install blocks and no link to
    docs/INSTALL.md.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = _read(_README_PATH)

    def test_readme_exists(self):
        """Sanity: README.md must exist."""
        self.assertTrue(
            os.path.isfile(_README_PATH),
            f"README.md not found at {_README_PATH}",
        )

    def test_readme_no_uv_install_command_block(self):
        """AC1: README must NOT contain 'uv tool install 'sandesh-relay' install blocks.

        FAILS now — the full install section with uv tool install is still present.
        After GREEN this line must be removed (moved to docs/INSTALL.md).
        """
        token = "uv tool install 'sandesh-relay"
        self.assertNotIn(
            token,
            self.readme,
            f"README.md must NOT contain the uv install command block "
            f"({token!r}) — it should be in docs/INSTALL.md, "
            "with only a pointer/link remaining in README.",
        )

    def test_readme_no_pipx_install_command_block(self):
        """AC1: README must NOT contain 'pipx install 'sandesh-relay' install blocks.

        FAILS now — the full pipx install section is still present.
        After GREEN this must be removed (moved to docs/INSTALL.md).
        """
        token = "pipx install 'sandesh-relay"
        self.assertNotIn(
            token,
            self.readme,
            f"README.md must NOT contain the pipx install command block "
            f"({token!r}) — it should be in docs/INSTALL.md, "
            "with only a pointer/link remaining in README.",
        )

    def test_readme_contains_install_guide_pointer(self):
        """AC1: README must contain a pointer/link to the install guide.

        FAILS now — there is no reference to docs/INSTALL.md in README.
        The pointer can be a markdown link [Install guide](docs/INSTALL.md)
        or bare text 'INSTALL.md' / 'docs/INSTALL.md'.
        """
        has_pointer = (
            "docs/INSTALL.md" in self.readme
            or "INSTALL.md" in self.readme
        )
        self.assertTrue(
            has_pointer,
            "README.md must contain a pointer/link to the install guide "
            "(e.g. 'docs/INSTALL.md' or '[Install guide](docs/INSTALL.md)'). "
            "GREEN must add this after removing the multi-route install blocks.",
        )

    def test_readme_retains_what_why_model(self):
        """AC1 (quality gate): README must still convey what/why/model content.

        PASSES now (regression guard) — the product description and Model section
        must survive the slim-down.  Checks for 'Model-B' or 'mailbox' (why section)
        and at least one table row for the data model.
        """
        # Check for Model-B / mailbox in the why/context section
        has_why = (
            "Model-B" in self.readme
            or "mailbox" in self.readme
        )
        self.assertTrue(
            has_why,
            "README.md must retain the why/context content "
            "('Model-B' or 'mailbox') after slimming.",
        )

        # Check the model table is intact (at least one | address | row)
        has_model_table = "| `address`" in self.readme
        self.assertTrue(
            has_model_table,
            "README.md must retain the data model table "
            "(| `address` | row) after slimming.",
        )


# ── AC2 — docs/INSTALL.md exists with per-surface content ────────────────────

class InstallGuideExistsTest(unittest.TestCase):
    """AC2: docs/INSTALL.md must exist and contain per-route × per-surface sections.

    FAILS now — the file does not exist.
    """

    @classmethod
    def setUpClass(cls):
        cls.install_md = _read(_INSTALL_MD_PATH)
        cls.exists = os.path.isfile(_INSTALL_MD_PATH)

    def test_install_md_exists(self):
        """AC2 (gate): docs/INSTALL.md must be present.

        FAILS now — file absent.  GREEN must create docs/INSTALL.md.
        """
        self.assertTrue(
            self.exists,
            f"docs/INSTALL.md not found at {_INSTALL_MD_PATH}. "
            "GREEN must create this file with per-route × per-surface install "
            "instructions (install → sandesh init → manage → uninstall).",
        )

    def test_install_md_mentions_claude_surface(self):
        """AC2: install guide must have a section for the Claude surface.

        FAILS now (file absent).  Claude uses the [mcp] extra.
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check Claude surface. "
                "GREEN must create docs/INSTALL.md with a Claude section."
            )
        self.assertIn(
            "Claude",
            self.install_md,
            "docs/INSTALL.md must contain a section or heading for the "
            "Claude surface (e.g. '## Claude' or '### Claude Code').",
        )

    def test_install_md_mentions_pi_surface(self):
        """AC2: install guide must have a section for the Pi surface.

        FAILS now (file absent).  Pi uses uvx on-demand (no [mcp]).
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check Pi surface. "
                "GREEN must create docs/INSTALL.md with a Pi section."
            )
        self.assertIn(
            "Pi",
            self.install_md,
            "docs/INSTALL.md must contain a section or heading for the "
            "Pi surface (e.g. '## Pi' or '### Pi extension').",
        )

    def test_install_md_mentions_sandesh_init(self):
        """AC2: install guide must document the 'sandesh init' provisioning step.

        FAILS now (file absent).  Each surface section must include init.
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check sandesh init. "
                "GREEN must create docs/INSTALL.md."
            )
        self.assertIn(
            "sandesh init",
            self.install_md,
            "docs/INSTALL.md must contain 'sandesh init' — the provisioning "
            "command that every surface uses after install.",
        )

    def test_install_md_mentions_install_and_uninstall(self):
        """AC2: install guide must contain both install and uninstall coverage.

        FAILS now (file absent).
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check lifecycle coverage. "
                "GREEN must create docs/INSTALL.md."
            )
        self.assertIn(
            "install",
            self.install_md.lower(),
            "docs/INSTALL.md must contain an install section.",
        )
        self.assertIn(
            "uninstall",
            self.install_md.lower(),
            "docs/INSTALL.md must contain an uninstall section.",
        )

    def test_install_md_claude_path_uses_mcp_extra(self):
        """AC2: Claude path in install guide must reference the [mcp] extra.

        FAILS now (file absent).  '[mcp]' only on the Claude path.
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check [mcp] extra. "
                "GREEN must create docs/INSTALL.md."
            )
        self.assertIn(
            "[mcp]",
            self.install_md,
            "docs/INSTALL.md must contain '[mcp]' for the Claude install path "
            "(e.g. 'sandesh-relay[mcp]').",
        )

    def test_install_md_pi_path_uses_uvx_from_migrate(self):
        """AC2: Pi path must use uvx on-demand with [migrate] only (no [mcp]).

        FAILS now (file absent).
        Per spec: Pi uses 'uvx --from 'sandesh-relay[migrate]' sandesh' — no mcp.
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check Pi uvx invocation. "
                "GREEN must create docs/INSTALL.md."
            )
        self.assertIn(
            "uvx --from 'sandesh-relay[migrate]'",
            self.install_md,
            "docs/INSTALL.md must contain the Pi on-demand invocation "
            "'uvx --from 'sandesh-relay[migrate]' sandesh' "
            "(Pi uses [migrate], NOT [mcp]).",
        )


# ── AC3 — AUR install route removed from README ──────────────────────────────

class ReadmeAurRouteRemovedTest(unittest.TestCase):
    """AC3: README must contain no AUR install tokens.

    FAILS now — the README contains '### Arch Linux (AUR)', 'yay -S sandesh-relay',
    and 'paru'.  GREEN must remove the entire AUR install section.

    Note: a surviving 'pacman -S uv' or 'pacman -S python-pipx' (non-AUR bootstrap
    hint for uv/pipx) is ALLOWED in the install guide (not README) per spec §S3.
    We do NOT assert pacman is fully absent — only the AUR install route tokens.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = _read(_README_PATH)

    def test_readme_no_yay_token(self):
        """AC3: README must NOT contain 'yay ' (AUR helper install invocation).

        FAILS now — README line ~90: 'yay -S sandesh-relay'.
        """
        self.assertNotIn(
            "yay ",
            self.readme,
            "README.md must NOT contain 'yay ' — the AUR install route using "
            "'yay -S sandesh-relay' must be removed from README. "
            "GREEN must remove the '### Arch Linux (AUR)' section.",
        )

    def test_readme_no_paru_token(self):
        """AC3: README must NOT contain 'paru ' (AUR helper install invocation).

        FAILS now — README line ~90: 'or: paru -S sandesh-relay'.
        """
        self.assertNotIn(
            "paru ",
            self.readme,
            "README.md must NOT contain 'paru ' — the AUR install route using "
            "'paru -S sandesh-relay' must be removed from README. "
            "GREEN must remove the '### Arch Linux (AUR)' section.",
        )

    def test_readme_no_arch_linux_aur_section_heading(self):
        """AC3: README must NOT contain the '### Arch Linux (AUR)' section heading.

        FAILS now — the section heading is present at line ~87.
        """
        self.assertNotIn(
            "### Arch Linux (AUR)",
            self.readme,
            "README.md must NOT contain the '### Arch Linux (AUR)' section heading. "
            "GREEN must remove the entire AUR install section from README.",
        )


# ── AC4 — uninstall matrix in docs/INSTALL.md ────────────────────────────────

class UninstallMatrixTest(unittest.TestCase):
    """AC4: docs/INSTALL.md must contain the full uninstall matrix.

    Covers all routes (install.sh / uv / pipx / pip) + Pi extension removal
    + the two manual steps every route shares (data store + claude mcp remove).

    FAILS now — the file does not exist.
    """

    @classmethod
    def setUpClass(cls):
        cls.install_md = _read(_INSTALL_MD_PATH)
        cls.exists = os.path.isfile(_INSTALL_MD_PATH)

    def _check_token(self, token: str, description: str) -> None:
        """Assert token is present in install guide, with a clear failure message."""
        if not self.exists:
            self.fail(
                f"docs/INSTALL.md does not exist — cannot check {description!r}. "
                "GREEN must create docs/INSTALL.md with the uninstall matrix."
            )
        self.assertIn(
            token,
            self.install_md,
            f"docs/INSTALL.md must contain {token!r} in the uninstall matrix "
            f"({description}).",
        )

    def test_uninstall_matrix_install_sh_uninstall(self):
        """AC4: install.sh --uninstall must be documented in the uninstall matrix.

        FAILS now (file absent).
        """
        self._check_token(
            "install.sh --uninstall",
            "install.sh route uninstall command",
        )

    def test_uninstall_matrix_uv_tool_uninstall(self):
        """AC4: 'uv tool uninstall' must be documented in the uninstall matrix.

        FAILS now (file absent).
        """
        self._check_token(
            "uv tool uninstall",
            "uv route uninstall command",
        )

    def test_uninstall_matrix_pipx_uninstall(self):
        """AC4: 'pipx uninstall' must be documented in the uninstall matrix.

        FAILS now (file absent).
        """
        self._check_token(
            "pipx uninstall",
            "pipx route uninstall command",
        )

    def test_uninstall_matrix_pip_uninstall(self):
        """AC4: 'pip uninstall' must be documented in the uninstall matrix.

        FAILS now (file absent).
        """
        self._check_token(
            "pip uninstall",
            "pip route uninstall command (with orphans caveat)",
        )

    def test_uninstall_matrix_claude_mcp_remove(self):
        """AC4: 'claude mcp remove sandesh' must be documented as a manual step.

        FAILS now (file absent).  This is a shared manual step for all routes
        that used 'claude mcp add sandesh' to register the MCP server.
        """
        self._check_token(
            "claude mcp remove sandesh",
            "manual step: remove sandesh MCP server from Claude Code",
        )

    def test_uninstall_matrix_data_store_path(self):
        """AC4: the data store path must be documented as a manual removal step.

        FAILS now (file absent).  Accepts either the literal path
        '~/.local/share/sandesh' or the XDG variable '$XDG_DATA_HOME'.
        """
        if not self.exists:
            self.fail(
                "docs/INSTALL.md does not exist — cannot check data store path. "
                "GREEN must create docs/INSTALL.md with the uninstall matrix."
            )
        has_data_store = any(
            re.search(pat, self.install_md)
            for pat in _UNINSTALL_DATA_STORE_PATTERNS
        )
        self.assertTrue(
            has_data_store,
            "docs/INSTALL.md must document the data store removal path as a manual "
            "uninstall step. Acceptable forms: '~/.local/share/sandesh' or "
            "'$XDG_DATA_HOME' (followed by '/sandesh' context). "
            "GREEN must add this to the uninstall matrix.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
