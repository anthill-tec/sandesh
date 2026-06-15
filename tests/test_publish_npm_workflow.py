"""test_publish_npm_workflow.py — CR-SAN-034 §S3 / AC4 contract for publish-npm.yml.

Asserts the YAML structure and content of ``.github/workflows/publish-npm.yml``
against AC4: the ``publish-npm`` job must have the same v*-tag-on-main guard step
(ref-regex + merge-base) before ``npm publish``, and must NOT contain a
``create-release`` job (that job lives only in ``publish-pypi.yml``).

Tests 1–3 FAIL at RED because the guard step has not been added yet.
Test 4 is a preserve-check (no create-release job — correct already); it
passes at RED to confirm the structural invariant is already satisfied.
Test 5 is a preserve-check (existing release-event gating is still present);
it also passes at RED.

PyYAML is NOT present in the project venv; all assertions use text/regex on
the raw YAML file so there is no third-party dependency.  String searches are
done on the NORMALISED file content (leading/trailing whitespace stripped per
line) to be tolerant of YAML indentation differences while still verifying
ordering via character-index comparisons.

Run targeted:
  PYTHONPATH=. .venv/bin/python tests/test_publish_npm_workflow.py
"""

import os
import re
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_WORKFLOW_PATH = os.path.join(
    _REPO_ROOT, ".github", "workflows", "publish-npm.yml"
)

# ---------------------------------------------------------------------------
# Helpers — match the style used in test_publish_pypi_workflow.py
# ---------------------------------------------------------------------------


def _read_workflow() -> str:
    with open(_WORKFLOW_PATH, encoding="utf-8") as fh:
        return fh.read()


def _normalised_lines(text: str):
    """Return list of lines with leading/trailing whitespace stripped."""
    return [line.strip() for line in text.splitlines()]


def _char_index(pattern: str, text: str, flags: int = 0) -> int:
    """Return the character index of the first occurrence of *pattern* in *text*.

    Returns -1 if not found.
    """
    m = re.search(pattern, text, flags)
    return m.start() if m else -1


# ---------------------------------------------------------------------------
# AC4 — publish-npm guard parity
# ---------------------------------------------------------------------------


class PublishNpmGuardParityTest(unittest.TestCase):
    """AC4 — publish-npm.yml's publish-npm job must have the same v*-tag guard
    step before npm publish; no create-release job is duplicated there.
    """

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_workflow()
        cls.lines = _normalised_lines(cls.raw)
        # Extract just the publish-npm job section for scoped assertions.
        # The job section starts at 'publish-npm:' and ends at the next top-level
        # job key (dry-run:) or end of file — whichever comes first.
        idx_job_start = _char_index(r"^\s{2}publish-npm\s*:", cls.raw, re.MULTILINE)
        idx_dry_run = _char_index(r"^\s{2}dry-run\s*:", cls.raw, re.MULTILINE)
        if idx_job_start == -1:
            cls.job_section = ""
        elif idx_dry_run != -1 and idx_dry_run > idx_job_start:
            cls.job_section = cls.raw[idx_job_start:idx_dry_run]
        else:
            cls.job_section = cls.raw[idx_job_start:]

    # -----------------------------------------------------------------------
    # Test 1 — guard step appears BEFORE npm publish (index-ordering)
    # -----------------------------------------------------------------------

    def test_guard_step_ordered_before_npm_publish(self):
        """AC4: a guard step (merge-base/ref-regex check) must appear before
        'npm publish' in the publish-npm job.

        FAILS at RED — no guard step exists; the version-sync step is the only
        gate before 'npm publish', and it does not check the branch ancestry.

        The guard must contain 'git merge-base --is-ancestor' and must have a
        character index less than the 'npm publish' step within the job section.
        """
        self.assertGreater(
            len(self.job_section),
            0,
            "Could not locate the 'publish-npm:' job in publish-npm.yml.",
        )

        # 'npm publish' (without --dry-run) must exist in the job section.
        idx_npm_publish = _char_index(
            r"\bnpm\s+publish\b(?!\s+--dry-run)", self.job_section
        )
        self.assertGreater(
            idx_npm_publish,
            -1,
            "publish-npm job must contain an 'npm publish' step (without --dry-run).",
        )

        # The guard step must also exist in the job section.
        idx_guard = _char_index(
            r"git\s+merge-base\s+--is-ancestor", self.job_section
        )
        self.assertGreater(
            idx_guard,
            -1,
            "publish-npm job must contain a guard step with "
            "'git merge-base --is-ancestor'; none found. "
            "The guard must verify the tagged commit is on origin/main before "
            "npm publish runs.",
        )

        # Guard must come BEFORE npm publish.
        self.assertLess(
            idx_guard,
            idx_npm_publish,
            "In publish-npm.yml's publish-npm job, the guard "
            "('git merge-base --is-ancestor') must appear BEFORE 'npm publish'; "
            f"guard at index {idx_guard}, npm publish at index {idx_npm_publish}.",
        )

    # -----------------------------------------------------------------------
    # Test 2 — guard asserts ref matches ^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$
    # -----------------------------------------------------------------------

    def test_guard_asserts_tag_ref_regex(self):
        r"""AC4: the guard step must assert github.ref (or GITHUB_REF) matches
        ``^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$`` — the same regex used in the
        publish-pypi guard (no rc suffix allowed on a release publish).

        FAILS at RED — no guard step exists; only the version-sync step (which
        checks tag vs package.json version) is present, and it does not contain
        the ref-regex literal.
        """
        self.assertGreater(
            len(self.job_section),
            0,
            "Could not locate the 'publish-npm:' job in publish-npm.yml.",
        )

        # The regex literal (or a close variant) must appear in the job section.
        # Accept grep/bash/shell forms; the invariant is the pattern itself.
        found = (
            bool(re.search(r"refs/tags/v\[0-9\]", self.job_section))
            or bool(re.search(r"\^refs/tags/v\d", self.job_section))
            or bool(re.search(r"\^refs/tags/v\[", self.job_section))
            or "refs/tags/v[0-9]" in self.job_section
        )
        self.assertTrue(
            found,
            "publish-npm.yml publish-npm guard step must assert that the ref "
            r"matches '^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' (no rc/dev suffix); "
            "the regex literal was not found in the publish-npm job section. "
            "Current file only has the version-sync step (package.json comparison), "
            "not a branch-ancestry / tag-format guard.",
        )

    # -----------------------------------------------------------------------
    # Test 3 — guard runs git merge-base --is-ancestor against origin/main
    # and fetches origin main first
    # -----------------------------------------------------------------------

    def test_guard_runs_merge_base_and_fetches_origin_main(self):
        """AC4: the guard step must fetch origin/main then run
        'git merge-base --is-ancestor' to verify the tag is on main.

        FAILS at RED — no guard step exists; the publish-npm job has no
        merge-base check anywhere in it.

        Two sub-assertions:
          (a) 'git fetch ... origin main' is present in the job section.
          (b) 'git merge-base --is-ancestor' is present in the job section.
        """
        self.assertGreater(
            len(self.job_section),
            0,
            "Could not locate the 'publish-npm:' job in publish-npm.yml.",
        )

        # (a) fetch origin main
        has_fetch = bool(
            re.search(
                r"git\s+fetch\s+.*origin\s+main|git\s+fetch\s+.*main",
                self.job_section,
            )
        )
        self.assertTrue(
            has_fetch,
            "publish-npm.yml publish-npm guard step must fetch origin/main "
            "(e.g. 'git fetch --no-tags origin main') before the merge-base check; "
            "no such fetch found in the publish-npm job section.",
        )

        # (b) merge-base --is-ancestor
        has_merge_base = bool(
            re.search(r"git\s+merge-base\s+--is-ancestor", self.job_section)
        )
        self.assertTrue(
            has_merge_base,
            "publish-npm.yml publish-npm guard step must call "
            "'git merge-base --is-ancestor' to verify the tagged commit "
            "is an ancestor of origin/main before npm publish; "
            "no such command found in the publish-npm job section.",
        )

    # -----------------------------------------------------------------------
    # Test 4 — NO create-release job in publish-npm.yml (preserve-check)
    # -----------------------------------------------------------------------

    def test_no_create_release_job_in_publish_npm(self):
        """AC4: publish-npm.yml must NOT contain a 'create-release' job.

        The single create-release job lives only in publish-pypi.yml; it serves
        both workflows via the shared 'release: published' event.  Duplicating it
        here would double-create the GitHub Release.

        This assertion is a preserve-check — it should PASS at RED (the job is
        correctly absent) and must remain passing through GREEN.
        """
        # A job key appears as 'create-release:' at the jobs-map level (indented
        # with 2 spaces in standard GitHub Actions YAML).
        self.assertNotIn(
            "create-release:",
            self.lines,
            "publish-npm.yml must NOT define a 'create-release:' job; "
            "that job belongs exclusively to publish-pypi.yml. "
            "Found 'create-release:' in publish-npm.yml — remove it.",
        )

        # Belt-and-suspenders: also check the raw text for the job key pattern.
        has_create_release_job = bool(
            re.search(r"^\s{2}create-release\s*:", self.raw, re.MULTILINE)
        )
        self.assertFalse(
            has_create_release_job,
            "publish-npm.yml must NOT define a 'create-release:' job key at the "
            "jobs level; found one in the raw file. Remove it — the shared Release "
            "is created by publish-pypi.yml's create-release job.",
        )

    # -----------------------------------------------------------------------
    # Test 5 — existing release-event gating is preserved (preserve-check)
    # -----------------------------------------------------------------------

    def test_publish_npm_if_release_event_preserved(self):
        """AC4 (preserve): the publish-npm job's existing
        ``if: github.event_name == 'release'`` condition must still be present.

        This is a preserve-check — it should PASS at RED (condition already
        exists) and must remain passing through GREEN.

        The guard step is additive; it must not remove the job-level condition.
        """
        self.assertGreater(
            len(self.job_section),
            0,
            "Could not locate the 'publish-npm:' job in publish-npm.yml.",
        )

        found = bool(
            re.search(
                r"github\.event_name\s*==\s*['\"]release['\"]",
                self.job_section,
            )
        )
        self.assertTrue(
            found,
            "publish-npm.yml publish-npm job must still have the job-level "
            "``if: github.event_name == 'release'`` condition; it has been "
            "removed or corrupted. The guard step is additive — it must not "
            "replace the existing event-type gate.",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
