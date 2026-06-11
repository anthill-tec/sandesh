"""test_pkgbuild.py — AUR PKGBUILD + .SRCINFO contract tests (CR-SAN-009 AC1–AC5).

Asserts that `packaging/aur/PKGBUILD` contains the correct metadata, build/package
functions, source line, and that the committed `.SRCINFO` matches `makepkg --printsrcinfo`.
Lint assertions use `namcap` and `shellcheck` when available.

These tests FAIL at RED because `packaging/aur/PKGBUILD` does not yet exist — the
`test_pkgbuild_exists` assertion fails with a clean AssertionError; all downstream
tests skip cleanly via `setUpClass` guards.

  python-crucible.py test --tests tests.test_pkgbuild --agent CR-SAN-009-C0-RED
"""

import os
import re
import subprocess
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKGBUILD_PATH = os.path.join(_REPO_ROOT, "packaging", "aur", "PKGBUILD")
_SRCINFO_PATH = os.path.join(_REPO_ROOT, "packaging", "aur", ".SRCINFO")


def _read_pkgbuild() -> str:
    """Return the raw PKGBUILD text. Caller must guard with exists check."""
    with open(_PKGBUILD_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# AC1 — file-exists gate
# ---------------------------------------------------------------------------
class PkgbuildExistsTest(unittest.TestCase):
    """Gate: PKGBUILD must exist before any content assertions make sense."""

    def test_pkgbuild_exists(self):
        """AC1: packaging/aur/PKGBUILD must be present."""
        self.assertTrue(
            os.path.isfile(_PKGBUILD_PATH),
            f"PKGBUILD missing — expected at {_PKGBUILD_PATH}",
        )


# ---------------------------------------------------------------------------
# AC1 — metadata fields
# ---------------------------------------------------------------------------
class PkgbuildMetadataTest(unittest.TestCase):
    """AC1 — metadata contract: pkgname, pkgver, arch, license, depends, optdepends, makedepends."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PKGBUILD_PATH):
            raise unittest.SkipTest("PKGBUILD missing — skipping metadata assertions")
        cls.text = _read_pkgbuild()

    def test_pkgname_is_sandesh_relay(self):
        """AC1: pkgname=sandesh-relay (exact value)."""
        self.assertRegex(
            self.text,
            r"(?m)^pkgname=sandesh-relay\s*$",
            "PKGBUILD must contain 'pkgname=sandesh-relay' on its own line",
        )

    def test_pkgver_line_present(self):
        """AC1: a pkgver= line is present."""
        self.assertRegex(
            self.text,
            r"(?m)^pkgver=\S",
            "PKGBUILD must contain a pkgver= line with a non-empty value",
        )

    def test_arch_is_any(self):
        """AC1: arch=('any') — pure Python, no architecture restriction."""
        # Tolerant of quoting/spacing variants.
        self.assertRegex(
            self.text,
            r"(?m)^arch=\(?['\"]?any['\"]?\)?",
            "PKGBUILD must contain arch=('any')",
        )

    def test_license_is_gpl3_only(self):
        """AC1: license=('GPL-3.0-only')."""
        # Tolerant of quoting and spacing.
        self.assertRegex(
            self.text,
            r"(?m)^license=.*GPL-3\.0-only",
            "PKGBUILD must declare license containing 'GPL-3.0-only'",
        )

    def test_depends_contains_python(self):
        """AC1: depends=('python') — the stdlib CLI requires only CPython."""
        self.assertRegex(
            self.text,
            r"(?m)^depends=.*['\"]?python['\"]?",
            "PKGBUILD must have a depends= line containing 'python'",
        )

    def test_optdepends_contains_python_mcp(self):
        """AC1: optdepends contains python-mcp for the MCP server extra."""
        self.assertIn(
            "python-mcp",
            self.text,
            "PKGBUILD optdepends must reference 'python-mcp'",
        )
        self.assertRegex(
            self.text,
            r"(?m)^optdepends=",
            "PKGBUILD must have an optdepends=( array",
        )

    def test_makedepends_contains_python_build(self):
        """AC1: makedepends includes python-build."""
        self.assertIn(
            "python-build",
            self.text,
            "PKGBUILD makedepends must include 'python-build'",
        )

    def test_makedepends_contains_python_installer(self):
        """AC1: makedepends includes python-installer."""
        self.assertIn(
            "python-installer",
            self.text,
            "PKGBUILD makedepends must include 'python-installer'",
        )

    def test_makedepends_contains_python_hatchling(self):
        """AC1: makedepends includes python-hatchling."""
        self.assertIn(
            "python-hatchling",
            self.text,
            "PKGBUILD makedepends must include 'python-hatchling'",
        )

    def test_makedepends_contains_python_hatch_vcs(self):
        """AC1: makedepends includes python-hatch-vcs."""
        self.assertIn(
            "python-hatch-vcs",
            self.text,
            "PKGBUILD makedepends must include 'python-hatch-vcs'",
        )

    def test_makedepends_array_present(self):
        """AC1: a makedepends=( array is declared."""
        self.assertRegex(
            self.text,
            r"(?m)^makedepends=\(",
            "PKGBUILD must have a makedepends=( array",
        )


# ---------------------------------------------------------------------------
# AC2 — build() and package() functions
# ---------------------------------------------------------------------------
class PkgbuildBuildPackageTest(unittest.TestCase):
    """AC2 — build() uses python -m build; package() installs via installer + LICENSE."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PKGBUILD_PATH):
            raise unittest.SkipTest("PKGBUILD missing — skipping build/package assertions")
        cls.text = _read_pkgbuild()

    def test_build_function_present(self):
        """AC2: build() function is declared."""
        self.assertRegex(
            self.text,
            r"(?m)^build\s*\(\s*\)",
            "PKGBUILD must define a build() function",
        )

    def test_build_uses_python_m_build(self):
        """AC2: build() contains 'python -m build'."""
        self.assertIn(
            "python -m build",
            self.text,
            "PKGBUILD build() must invoke 'python -m build'",
        )

    def test_package_function_present(self):
        """AC2: package() function is declared."""
        self.assertRegex(
            self.text,
            r"(?m)^package\s*\(\s*\)",
            "PKGBUILD must define a package() function",
        )

    def test_package_uses_python_m_installer(self):
        """AC2: package() contains 'python -m installer'."""
        self.assertIn(
            "python -m installer",
            self.text,
            "PKGBUILD package() must invoke 'python -m installer'",
        )

    def test_package_references_dist_whl(self):
        """AC2: package() references 'dist/*.whl'."""
        self.assertIn(
            "dist/*.whl",
            self.text,
            "PKGBUILD package() must reference 'dist/*.whl'",
        )

    def test_package_installs_license(self):
        """AC2: package() installs the LICENSE file under usr/share/licenses/."""
        self.assertIn(
            "usr/share/licenses/",
            self.text,
            "PKGBUILD package() must install LICENSE under 'usr/share/licenses/'",
        )
        self.assertIn(
            "LICENSE",
            self.text,
            "PKGBUILD package() must reference the 'LICENSE' file",
        )


# ---------------------------------------------------------------------------
# AC5 — source line and sha256sums
# ---------------------------------------------------------------------------
class PkgbuildSourceTest(unittest.TestCase):
    """AC5 — source resolves to PyPI sdist; sha256sums array present."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PKGBUILD_PATH):
            raise unittest.SkipTest("PKGBUILD missing — skipping source assertions")
        cls.text = _read_pkgbuild()

    def test_source_array_present(self):
        """AC5: a source=( array is declared."""
        self.assertRegex(
            self.text,
            r"(?m)^source=\(",
            "PKGBUILD must have a source=( array",
        )

    def test_source_references_sandesh_relay_sdist_prefix(self):
        """AC5: source references the PyPI sdist filename prefix 'sandesh_relay-'."""
        self.assertIn(
            "sandesh_relay-",
            self.text,
            "PKGBUILD source must reference the PyPI sdist 'sandesh_relay-'",
        )

    def test_source_references_tar_gz_suffix(self):
        """AC5: source filename ends with .tar.gz (sdist format)."""
        self.assertIn(
            ".tar.gz",
            self.text,
            "PKGBUILD source must reference a .tar.gz sdist",
        )

    def test_source_references_pkgver_template_or_pythonhosted(self):
        """AC5: source URL uses $pkgver template variable OR references pythonhosted.org."""
        has_pkgver_template = "$pkgver" in self.text or "${pkgver}" in self.text
        has_pythonhosted = "pythonhosted" in self.text
        self.assertTrue(
            has_pkgver_template or has_pythonhosted,
            "PKGBUILD source must use '$pkgver' template or reference 'pythonhosted.org'",
        )

    def test_sha256sums_array_present(self):
        """AC5: a sha256sums=( array is present (value may be hash or 'SKIP' placeholder)."""
        self.assertRegex(
            self.text,
            r"(?m)^sha256sums=\(",
            "PKGBUILD must have a sha256sums=( array",
        )


# ---------------------------------------------------------------------------
# AC3 — .SRCINFO sync (skip if files missing or makepkg unavailable)
# ---------------------------------------------------------------------------
class SrcInfoSyncTest(unittest.TestCase):
    """AC3 — committed .SRCINFO must match 'makepkg --printsrcinfo' output."""

    @classmethod
    def setUpClass(cls):
        missing = []
        if not os.path.isfile(_PKGBUILD_PATH):
            missing.append("PKGBUILD")
        if not os.path.isfile(_SRCINFO_PATH):
            missing.append(".SRCINFO")
        if missing:
            raise unittest.SkipTest(
                f"Skipping .SRCINFO sync check — missing: {', '.join(missing)}"
            )
        # Check makepkg availability
        result = subprocess.run(
            ["which", "makepkg"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("makepkg not available — skipping .SRCINFO sync check")

    def test_srcinfo_matches_makepkg_printsrcinfo(self):
        """AC3: committed .SRCINFO equals 'makepkg --printsrcinfo' output (normalized)."""
        result = subprocess.run(
            ["makepkg", "--printsrcinfo"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(_PKGBUILD_PATH),
        )
        self.assertEqual(
            result.returncode,
            0,
            f"makepkg --printsrcinfo failed: {result.stderr}",
        )
        with open(_SRCINFO_PATH, "r", encoding="utf-8") as fh:
            committed = fh.read()

        # Normalize: strip trailing whitespace per line, strip trailing newlines.
        def _normalize(s: str) -> str:
            return "\n".join(line.rstrip() for line in s.splitlines()).rstrip()

        self.assertEqual(
            _normalize(result.stdout),
            _normalize(committed),
            "Committed .SRCINFO does not match 'makepkg --printsrcinfo' output — "
            "regenerate with: cd packaging/aur && makepkg --printsrcinfo > .SRCINFO",
        )


# ---------------------------------------------------------------------------
# AC4 — namcap lint (skip if namcap absent or PKGBUILD missing)
# ---------------------------------------------------------------------------
class NamcapLintTest(unittest.TestCase):
    """AC4 — namcap PKGBUILD reports no errors (E:); warnings (W:) are allowed."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PKGBUILD_PATH):
            raise unittest.SkipTest("PKGBUILD missing — skipping namcap lint")
        result = subprocess.run(
            ["which", "namcap"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("namcap not available — skipping lint")

    def test_namcap_no_errors(self):
        """AC4: namcap produces no error-level (E:) findings on the PKGBUILD."""
        result = subprocess.run(
            ["namcap", _PKGBUILD_PATH],
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        error_lines = [ln for ln in output.splitlines() if " E: " in ln]
        warning_lines = [ln for ln in output.splitlines() if " W: " in ln]

        # Record warnings (informational — not a failure).
        if warning_lines:
            print(
                f"\nnamcap warnings (non-blocking):\n"
                + "\n".join(f"  {w}" for w in warning_lines)
            )

        self.assertEqual(
            error_lines,
            [],
            f"namcap reported errors on PKGBUILD:\n"
            + "\n".join(f"  {e}" for e in error_lines),
        )


# ---------------------------------------------------------------------------
# AC4 (optional) — shellcheck lint (skip if shellcheck absent or PKGBUILD missing)
# ---------------------------------------------------------------------------
class ShellcheckLintTest(unittest.TestCase):
    """AC4 (optional) — shellcheck on PKGBUILD with makepkg-global suppressions."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_PKGBUILD_PATH):
            raise unittest.SkipTest("PKGBUILD missing — skipping shellcheck lint")
        result = subprocess.run(
            ["which", "shellcheck"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("shellcheck not available — skipping lint")

    def test_shellcheck_clean(self):
        """AC4: shellcheck on PKGBUILD exits 0 (with SC2034/SC2154 suppressed for makepkg globals)."""
        result = subprocess.run(
            [
                "shellcheck",
                "-e", "SC2034,SC2154",  # makepkg sets many globals (pkgdir, srcdir, etc.)
                _PKGBUILD_PATH,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"shellcheck reported issues on PKGBUILD:\n{result.stdout}{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
