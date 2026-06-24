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


    # ------------------------------------------------------------------ #
    # Tests 7–9 — CR-SAN-042 §S3 / AC7: set-version + finish guard docs  #
    # ------------------------------------------------------------------ #

    def test_set_version_step_documented(self):
        """AC7: RELEASING.md must document the 'set-version' subcommand.

        §S3 requires that RELEASING.md instructs maintainers to run
        ``scripts/release.sh set-version <X.Y.Z>`` on the release/hotfix branch
        before calling ``finish``.  The literal subcommand name ``set-version``
        must appear in the file.

        FAILS at RED — 'set-version' is not currently present in RELEASING.md.
        """
        self.assertIn(
            "set-version",
            self.text,
            "RELEASING.md must document the 'set-version' subcommand "
            "(scripts/release.sh set-version <X.Y.Z>) as the step that bumps the "
            "manual manifests (package.json, server.json) before calling finish. "
            "'set-version' is not currently present.",
        )

    def test_set_version_tied_to_manifest_files(self):
        """AC7: RELEASING.md must tie 'set-version' to the manual manifest files.

        The doc must mention 'set-version' in a context that references at least
        one of the manual manifests it operates on: 'package.json' (the Pi
        extension) or 'server.json' (the MCP registry manifest).  Both tokens
        must appear anywhere in the document AND 'set-version' must also appear
        (the combination documents that set-version is the bump command for those
        manifests).

        FAILS at RED — 'set-version' is not currently present in RELEASING.md.
        """
        has_set_version = "set-version" in self.text
        has_manifest_ref = "package.json" in self.text or "server.json" in self.text
        self.assertTrue(
            has_set_version and has_manifest_ref,
            "RELEASING.md must mention 'set-version' AND at least one of the "
            "manual manifests it targets ('package.json' or 'server.json'). "
            f"set-version present={has_set_version}, "
            f"manifest token present={has_manifest_ref}.",
        )

    def test_finish_guard_documented(self):
        """AC7: RELEASING.md must document that 'finish' guards on a manifest version mismatch.

        §S3 requires the doc to explain that ``scripts/release.sh finish`` refuses
        (exits 1) when the manual manifests have not been bumped to match the
        release version.  The lowercased text must contain BOTH 'set-version'
        (the remediation step) AND at least one guard-intent word: 'guard',
        'mismatch', 'refuse', or 'must match'.

        FAILS at RED — 'set-version' is not currently present in RELEASING.md,
        so the combined condition cannot be satisfied.
        """
        text_lower = self.text.lower()
        has_set_version = "set-version" in text_lower
        has_guard_intent = any(
            token in text_lower
            for token in ("guard", "mismatch", "refuse", "must match")
        )
        self.assertTrue(
            has_set_version and has_guard_intent,
            "RELEASING.md must document that 'finish' guards on a manifest "
            "version mismatch and instruct the maintainer to run 'set-version'. "
            f"set-version present={has_set_version}, "
            f"guard-intent word (guard/mismatch/refuse/must match) present={has_guard_intent}.",
        )

    def test_pypi_version_stays_tag_derived(self):
        """AC7: RELEASING.md must state that 'set-version' does NOT touch pyproject.toml / Python version.

        The PyPI/Python package version is owned by hatch-vcs (tag-derived); only
        the manual manifests (package.json, server.json) are touched by set-version.
        The doc must clarify this by mentioning 'set-version' in close proximity to
        'hatch-vcs' or 'tag-derived' or 'pyproject' so a maintainer understands
        the boundary.  Both 'set-version' AND one of ('hatch-vcs', 'tag-derived',
        'pyproject') must appear in the document.

        FAILS at RED — 'set-version' is not currently present in RELEASING.md,
        so the combined condition cannot be satisfied even though 'hatch-vcs' and
        'pyproject' are already present.
        """
        has_set_version = "set-version" in self.text
        has_tag_derivation_token = any(
            token in self.text
            for token in ("hatch-vcs", "tag-derived", "pyproject")
        )
        self.assertTrue(
            has_set_version and has_tag_derivation_token,
            "RELEASING.md must document that 'set-version' bumps only the manual "
            "manifests and that the PyPI/Python version remains tag-derived "
            "(hatch-vcs). Both 'set-version' and a tag-derivation token "
            "('hatch-vcs', 'tag-derived', or 'pyproject') must appear. "
            f"set-version present={has_set_version}, "
            f"tag-derivation token present={has_tag_derivation_token}.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
