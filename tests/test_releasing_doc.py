"""test_releasing_doc.py — RELEASING.md content gates for CR-SAN-034 §S5 / AC8.

Asserts that RELEASING.md:
  1. No longer contains the false claim 'fine for a TestPyPI rehearsal' (tied to an
     untagged dev version being acceptable — it is NOT: the version string is dirty
     and the published artifact is misleadingly named).
  2. Documents the RELEASE_PAT prerequisite (the Personal Access Token required for
     scripts/release.sh to create GitHub Releases).
  3. Documents the corrected version-scheme reality — no-local-version (the
     local_scheme = "no-local-version" pyproject.toml fix from C1).
  4. Documents the push-main → GitHub Release → publish chain: a push to main
     that carries a vX.Y.Z tag triggers ``gh release create``, which fires
     ``release: published``, which in turn triggers the publish-pypi job.
  5. References ``scripts/release.sh`` (the new release helper introduced in C4).
  6. Documents the branch / merge-base guard: publish is only triggered from a
     commit that is on main (merge-base or vX.Y.Z tag on main check).

All six tests FAIL at RED because the current RELEASING.md still contains the
false claim (#1) and is missing all of the new content (#2–#6).

Run targeted:
  python-crucible.py test --tests tests.test_releasing_doc --agent CR-SAN-034-C5-RED
"""

import os
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_RELEASING_MD_PATH = os.path.join(_REPO_ROOT, "RELEASING.md")


def _read_releasing_md() -> str:
    """Return the full text of RELEASING.md."""
    with open(_RELEASING_MD_PATH, encoding="utf-8") as fh:
        return fh.read()


class ReleasingDocTest(unittest.TestCase):
    """AC8 — RELEASING.md content gates (CR-SAN-034 §S5)."""

    @classmethod
    def setUpClass(cls):
        """Read RELEASING.md once for all assertions."""
        if not os.path.isfile(_RELEASING_MD_PATH):
            raise unittest.SkipTest(
                f"RELEASING.md missing — expected at {_RELEASING_MD_PATH}"
            )
        cls.text = _read_releasing_md()

    # ------------------------------------------------------------------
    # Test 1 — NEGATIVE: the false claim must be removed
    # ------------------------------------------------------------------

    def test_false_testpypi_claim_removed(self):
        """AC8 (negative): RELEASING.md must NOT contain 'fine for a TestPyPI rehearsal'.

        The phrase 'fine for a TestPyPI rehearsal' (tied to an untagged dev version)
        is a false claim: the published version string is a dirty devN+g<sha> artefact
        that will be rejected or misleadingly named, not a clean X.Y.Z rehearsal.
        GREEN must remove this claim and replace it with the corrected guidance.

        FAILS at RED — the string is currently present in RELEASING.md.
        """
        self.assertNotIn(
            "fine for a TestPyPI rehearsal",
            self.text,
            "RELEASING.md still contains the false claim "
            "'fine for a TestPyPI rehearsal' (tied to an untagged dev version). "
            "GREEN must remove this phrase and document that an untagged "
            "workflow_dispatch produces a dirty devN+g<sha> version, not a "
            "clean rehearsal artefact.",
        )

    # ------------------------------------------------------------------
    # Test 2 — POSITIVE: RELEASE_PAT prerequisite
    # ------------------------------------------------------------------

    def test_release_pat_prerequisite_documented(self):
        """AC8 (positive): RELEASING.md must document the RELEASE_PAT prerequisite.

        scripts/release.sh (C4) needs a GitHub Personal Access Token with
        ``repo`` + ``workflow`` scopes stored as the ``RELEASE_PAT`` secret
        (or env var) to create GitHub Releases via ``gh release create``.
        This prerequisite must appear in RELEASING.md.

        FAILS at RED — 'RELEASE_PAT' is not currently present.
        """
        self.assertIn(
            "RELEASE_PAT",
            self.text,
            "RELEASING.md must document the RELEASE_PAT prerequisite "
            "(the PAT required by scripts/release.sh to create GitHub Releases). "
            "Add it to the one-time maintainer prerequisites section.",
        )

    # ------------------------------------------------------------------
    # Test 3 — POSITIVE: no-local-version corrected version-scheme reality
    # ------------------------------------------------------------------

    def test_no_local_version_scheme_documented(self):
        """AC8 (positive): RELEASING.md must reference 'no-local-version'.

        C1 fixed pyproject.toml to set ``local_scheme = 'no-local-version'``
        so that workflow_dispatch / develop builds produce a clean devN+gSHA
        version (without the ``+local`` suffix that TestPyPI rejects).
        RELEASING.md must acknowledge this scheme so maintainers understand
        the version format they will see in CI artefacts.

        FAILS at RED — 'no-local-version' is not currently present.
        """
        self.assertIn(
            "no-local-version",
            self.text,
            "RELEASING.md must document the 'no-local-version' local_scheme "
            "(the pyproject.toml fix from C1 that produces clean devN+gSHA "
            "version strings on untagged builds instead of the +local suffix "
            "that TestPyPI rejects). Reference it in the version-scheme "
            "explanation or prerequisites.",
        )

    # ------------------------------------------------------------------
    # Test 4 — POSITIVE: push-main → GitHub Release → publish chain
    # ------------------------------------------------------------------

    def test_push_main_to_release_publish_chain_documented(self):
        """AC8 (positive): RELEASING.md must document the push-main → Release → publish chain.

        The correct flow is:
          git push origin main --tags
            → ``gh release create vX.Y.Z`` (or GitHub web UI)
            → fires ``release: published`` event
            → triggers the publish-pypi job

        Both ``gh release create`` (the command) and an explicit tie between
        pushing to main and the release being created must appear in the doc.

        FAILS at RED — 'gh release create' is not currently present.
        """
        self.assertIn(
            "gh release create",
            self.text,
            "RELEASING.md must document the push-main → GitHub Release → publish "
            "chain using 'gh release create'. The current doc only describes the "
            "GitHub web UI path ('Draft a new release'). Add the CLI command and "
            "explain that publishing to PyPI is triggered by the release: published "
            "event that gh release create fires.",
        )
        # Also assert that the chain ties 'main' to the release so the causal
        # relationship is clear — tolerant substring: both 'main' and 'release' must
        # appear within 200 characters of each other in the document.
        idx_main = self.text.find("main")
        idx_release = self.text.find("release")
        self.assertNotEqual(
            idx_main,
            -1,
            "RELEASING.md must mention 'main' in the context of the release chain.",
        )
        self.assertNotEqual(
            idx_release,
            -1,
            "RELEASING.md must mention 'release' in the context of the publish chain.",
        )
        # Find the closest pair of occurrences.
        import re
        main_positions = [m.start() for m in re.finditer(r"\bmain\b", self.text)]
        release_positions = [m.start() for m in re.finditer(r"\brelease\b", self.text, re.IGNORECASE)]
        min_gap = min(
            abs(m - r)
            for m in main_positions
            for r in release_positions
        )
        self.assertLessEqual(
            min_gap,
            200,
            "RELEASING.md must tie 'main' and 'release' in close proximity "
            f"(within 200 chars) to document the push-main → Release chain; "
            f"closest occurrence gap found: {min_gap} chars.",
        )

    # ------------------------------------------------------------------
    # Test 5 — POSITIVE: scripts/release.sh helper
    # ------------------------------------------------------------------

    def test_scripts_release_sh_documented(self):
        """AC8 (positive): RELEASING.md must reference 'scripts/release.sh'.

        C4 introduced scripts/release.sh (checkpoint / finish / status commands)
        to guide the maintainer through the release steps safely. RELEASING.md
        must document this helper so maintainers know to use it.

        FAILS at RED — 'scripts/release.sh' is not currently present.
        """
        self.assertIn(
            "scripts/release.sh",
            self.text,
            "RELEASING.md must reference 'scripts/release.sh' (the release "
            "helper introduced in C4 with checkpoint/finish/status sub-commands). "
            "Add a section or note directing maintainers to use it.",
        )

    # ------------------------------------------------------------------
    # Test 6 — POSITIVE: branch guard / merge-base / vX.Y.Z-on-main check
    # ------------------------------------------------------------------

    def test_branch_guard_documented(self):
        """AC8 (positive): RELEASING.md must document the publish branch guard.

        The publish-pypi.yml workflow (C2) guards the publish job so it only
        fires when the triggering commit is reachable from main (merge-base
        check or vX.Y.Z tag on main). This prevents a tag pushed to a non-main
        branch from accidentally triggering a PyPI publish.

        The doc must mention either 'merge-base' or a phrase indicating that the
        vX.Y.Z tag must be on main (e.g. 'tag on main', 'tagged commit on main').

        FAILS at RED — neither 'merge-base' nor the tag-on-main guard is
        currently documented.
        """
        has_merge_base = "merge-base" in self.text
        has_tag_on_main = (
            "tag on main" in self.text.lower()
            or "tagged commit on main" in self.text.lower()
            or "vX.Y.Z tag on main" in self.text
            or "v*.*.* tag on main" in self.text
        )
        self.assertTrue(
            has_merge_base or has_tag_on_main,
            "RELEASING.md must document the publish branch guard — either 'merge-base' "
            "(the git merge-base check) or the equivalent constraint that the vX.Y.Z "
            "tag must be on main before PyPI publish fires. "
            "Neither phrase is currently present.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
