"""test_publish_pypi_workflow.py — CR-SAN-034 §S2 contract for publish-pypi.yml.

Asserts the YAML structure and content of ``.github/workflows/publish-pypi.yml``
against AC2 (create-release job), AC3 (publish-pypi guard ordering), and AC5
(testpypi version-sanity step).

All tests FAIL at RED because the required jobs/steps have not been added yet.

PyYAML is NOT present in the project venv; all assertions use text/regex on the raw
YAML file so there is no third-party dependency.  String searches are done on the
NORMALISED file content (leading/trailing whitespace stripped per line) to be tolerant
of YAML indentation differences while still verifying ordering via character-index
comparisons.

Run targeted:
  PYTHONPATH=. .venv/bin/python tests/test_publish_pypi_workflow.py
"""

import os
import re
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_WORKFLOW_PATH = os.path.join(_REPO_ROOT, ".github", "workflows", "publish-pypi.yml")


# ---------------------------------------------------------------------------
# Helper — load raw workflow text (cached on module import)
# ---------------------------------------------------------------------------

def _read_workflow() -> str:
    with open(_WORKFLOW_PATH, encoding="utf-8") as fh:
        return fh.read()


def _normalised_lines(text: str):
    """Return list of lines with leading/trailing whitespace stripped."""
    return [line.strip() for line in text.splitlines()]


def _find_first(pattern: str, text: str, flags: int = 0) -> re.Match | None:
    """Return the first match of *pattern* anywhere in *text*."""
    return re.search(pattern, text, flags)


def _char_index(pattern: str, text: str, flags: int = 0) -> int:
    """Return the character index of the first occurrence of *pattern* in *text*.

    Returns -1 if not found.
    """
    m = re.search(pattern, text, flags)
    return m.start() if m else -1


# ---------------------------------------------------------------------------
# AC2 — create-release job
# ---------------------------------------------------------------------------

class CreateReleaseJobTest(unittest.TestCase):
    """AC2 — publish-pypi.yml must contain a correctly-gated create-release job."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_workflow()
        cls.lines = _normalised_lines(cls.raw)

    # --- trigger: push: branches: [main] ---

    def test_on_trigger_includes_push_to_main(self):
        """AC2: the ``on:`` block must include a push trigger with branches containing 'main'.

        FAILS at RED — no push:branches trigger exists; the file only has
        release/workflow_dispatch/pull_request.
        """
        # Find a 'branches:' entry (or inline list) that includes 'main'
        # Accept either YAML list form or inline form, e.g.:
        #   branches: [main]   or   - main
        # We also accept the variant where the push section is nearby.
        # Strategy: the file must contain 'push:' and somewhere after (or near) it 'main'.
        has_push = any("push:" in line for line in self.lines)
        self.assertTrue(
            has_push,
            "publish-pypi.yml must have a 'push:' trigger in the 'on:' block; "
            "currently only 'release', 'workflow_dispatch', and 'pull_request' exist.",
        )
        # The branches list under push must contain 'main'
        has_main_branch = bool(re.search(
            r"branches\s*:\s*\[.*main.*\]|branches\s*:\s*\n(?:\s+-\s+\S+\n)*\s+-\s+main",
            self.raw,
        ))
        self.assertTrue(
            has_main_branch,
            "publish-pypi.yml push trigger must list 'main' in its branches: block; "
            "e.g.  push:\\n  branches: [main]",
        )

    # --- job named create-release exists ---

    def test_create_release_job_exists(self):
        """AC2: a job named 'create-release' must exist.

        FAILS at RED — the job has not been added yet.
        """
        # YAML job keys appear as 'create-release:' at the job-map level.
        self.assertIn(
            "create-release:",
            self.lines,
            "publish-pypi.yml must define a 'create-release:' job; not found.",
        )

    # --- if condition on create-release job ---

    def test_create_release_if_requires_push_event(self):
        """AC2: create-release job's ``if`` must require github.event_name == 'push'.

        FAILS at RED — the job does not exist yet.
        """
        found = bool(re.search(
            r"github\.event_name\s*==\s*['\"]push['\"]",
            self.raw,
        ))
        self.assertTrue(
            found,
            "publish-pypi.yml create-release job must have an ``if`` condition "
            "that checks ``github.event_name == 'push'``.",
        )

    def test_create_release_if_requires_refs_heads_main(self):
        """AC2: create-release job's ``if`` must require github.ref == 'refs/heads/main'.

        FAILS at RED — the job does not exist yet.
        """
        found = bool(re.search(
            r"github\.ref\s*==\s*['\"]refs/heads/main['\"]",
            self.raw,
        ))
        self.assertTrue(
            found,
            "publish-pypi.yml create-release job must have an ``if`` condition "
            "that checks ``github.ref == 'refs/heads/main'``.",
        )

    # --- checkout with fetch-depth: 0 in create-release ---

    def test_create_release_checkout_fetch_depth_0(self):
        """AC2: create-release job must check out with fetch-depth: 0.

        FAILS at RED — the job does not exist yet.
        The file currently only has one checkout (in the build job).
        This test requires at least TWO occurrences of fetch-depth: 0
        — one in build (existing) and one in create-release (new).
        """
        occurrences = [
            m.start() for m in re.finditer(r"fetch-depth\s*:\s*0", self.raw)
        ]
        self.assertGreaterEqual(
            len(occurrences),
            2,
            "publish-pypi.yml must have at least two 'fetch-depth: 0' entries — "
            "one in the existing 'build' job and one in the new 'create-release' job; "
            f"found {len(occurrences)} occurrence(s).",
        )

    # --- version tag detection using ^v[0-9]+\.[0-9]+\.[0-9]+$ pattern ---

    def test_create_release_detects_version_tag_at_head(self):
        """AC2: create-release must detect a version tag at HEAD using git tag --points-at HEAD
        and match it against a ^v[0-9]+\\.[0-9]+\\.[0-9]+$ pattern.

        FAILS at RED — the job does not exist yet.
        """
        # The grep/match pattern for the tag regex must appear in the file.
        has_points_at = bool(re.search(r"git\s+tag\s+--points-at\s+HEAD", self.raw))
        self.assertTrue(
            has_points_at,
            "publish-pypi.yml create-release job must use "
            "'git tag --points-at HEAD' to read the tag at HEAD.",
        )
        # The version regex literal must also appear.
        has_tag_regex = bool(re.search(
            r"\^v\[0-9\]\+\\\.|\^v\[0-9\]\+\.\[0-9\]|\^v\[0-9\]\+\\\.",
            self.raw,
        ) or re.search(r"v\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+\$", self.raw)
          or re.search(r"'\^v\[0-9\]", self.raw)
          or re.search(r'"\^v\[0-9\]', self.raw)
          or "^v[0-9]" in self.raw)
        self.assertTrue(
            has_tag_regex,
            "publish-pypi.yml create-release job must filter the tag list against a "
            "'^v[0-9]+.[0-9]+.[0-9]+$'-style regex (e.g. via grep -E or grep -P).",
        )

    # --- idempotency: gh release view before gh release create ---

    def test_create_release_is_idempotent_via_release_view(self):
        """AC2: create-release must check for an existing Release via 'gh release view'
        before calling 'gh release create' (idempotency guard).

        FAILS at RED — the job does not exist yet.
        """
        has_view = bool(re.search(r"gh\s+release\s+view", self.raw))
        self.assertTrue(
            has_view,
            "publish-pypi.yml create-release job must call 'gh release view' "
            "before 'gh release create' to skip an already-existing release (idempotency).",
        )
        has_create = bool(re.search(r"gh\s+release\s+create", self.raw))
        self.assertTrue(
            has_create,
            "publish-pypi.yml create-release job must call 'gh release create'.",
        )
        # view must appear BEFORE create in the file
        idx_view = _char_index(r"gh\s+release\s+view", self.raw)
        idx_create = _char_index(r"gh\s+release\s+create", self.raw)
        self.assertLess(
            idx_view,
            idx_create,
            "In publish-pypi.yml, 'gh release view' must appear before "
            "'gh release create' (idempotency check must precede creation).",
        )

    # --- gh release create uses RELEASE_PAT secret ---

    def test_create_release_uses_release_pat_secret(self):
        """AC2: the create-release job must reference the RELEASE_PAT secret for GH_TOKEN.

        FAILS at RED — the job does not exist yet.
        The exact form expected:  GH_TOKEN: ${{ secrets.RELEASE_PAT }}
        """
        found = bool(re.search(r"secrets\.RELEASE_PAT", self.raw))
        self.assertTrue(
            found,
            "publish-pypi.yml create-release job must set "
            "GH_TOKEN: ${{ secrets.RELEASE_PAT }} so that the created Release "
            "triggers the 'release: published' event (default GITHUB_TOKEN cannot do this).",
        )


# ---------------------------------------------------------------------------
# AC3 — publish-pypi guard step (ordering + content)
# ---------------------------------------------------------------------------

class PublishPypiGuardTest(unittest.TestCase):
    """AC3 — publish-pypi job must contain a guard step before pypa/gh-action-pypi-publish."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_workflow()
        cls.lines = _normalised_lines(cls.raw)

    # --- guard step appears BEFORE pypa/gh-action-pypi-publish ---

    def test_guard_step_ordered_before_pypi_publish_action(self):
        """AC3: the guard step must appear before ``pypa/gh-action-pypi-publish`` in the
        publish-pypi job section.

        FAILS at RED — no guard step exists; the publish action is the only step in that job.
        """
        # The guard must check out (actions/checkout) and the pypi publish action must follow.
        # Scope: we look for checkout appearing before the publish action anywhere in the file;
        # since the build job already has a checkout, we need the guard checkout to be the
        # LATEST checkout before the publish action.
        idx_pypi_publish = _char_index(r"pypa/gh-action-pypi-publish", self.raw)
        self.assertGreater(
            idx_pypi_publish, -1,
            "publish-pypi.yml must contain 'pypa/gh-action-pypi-publish' step.",
        )
        # Find the guard checkout: a fetch-depth:0 checkout that is inside the publish-pypi job.
        # The publish-pypi job section starts after 'publish-pypi:' and ends before
        # 'publish-testpypi:'.  The guard checkout must appear between those markers.
        idx_publish_pypi_job = _char_index(r"publish-pypi\s*:", self.raw)
        idx_publish_testpypi_job = _char_index(r"publish-testpypi\s*:", self.raw)
        self.assertGreater(idx_publish_pypi_job, -1, "'publish-pypi:' job header missing.")
        self.assertGreater(idx_publish_testpypi_job, -1, "'publish-testpypi:' job header missing.")
        job_section = self.raw[idx_publish_pypi_job:idx_publish_testpypi_job]
        # The guard step in this section must contain a fetch-depth:0 checkout
        has_guard_checkout = bool(re.search(r"fetch-depth\s*:\s*0", job_section))
        self.assertTrue(
            has_guard_checkout,
            "publish-pypi.yml publish-pypi job must contain a guard step with "
            "'fetch-depth: 0' (actions/checkout) BEFORE the pypa/gh-action-pypi-publish step; "
            "currently the job has no checkout at all.",
        )
        # The guard checkout must appear before the publish action within that section
        idx_guard_co = _char_index(r"fetch-depth\s*:\s*0", job_section)
        idx_publish_in_section = _char_index(r"pypa/gh-action-pypi-publish", job_section)
        self.assertLess(
            idx_guard_co,
            idx_publish_in_section,
            "In the publish-pypi job section, the guard checkout (fetch-depth:0) "
            "must appear before 'pypa/gh-action-pypi-publish'.",
        )

    # --- guard asserts ref matches ^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$ ---

    def test_guard_asserts_tag_ref_regex(self):
        """AC3: the guard step must assert that github.ref matches
        ``^refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$`` (no rc suffix allowed on real PyPI).

        FAILS at RED — no guard step exists.
        """
        # The exact regex literal (or a close variant) must appear in the file.
        # Accept grep/bash/Python forms; the key invariant is the pattern itself.
        found = bool(re.search(r"refs/tags/v\[0-9\]|\^refs/tags/v\d|\^refs/tags/v\[", self.raw)
                     or "refs/tags/v[0-9]" in self.raw)
        self.assertTrue(
            found,
            "publish-pypi.yml publish-pypi guard step must assert "
            "github.ref matches '^refs/tags/v[0-9]+.[0-9]+.[0-9]+$' "
            "(no rc/dev suffix on the real index); pattern not found in file.",
        )

    # --- guard runs git merge-base --is-ancestor ---

    def test_guard_runs_merge_base_is_ancestor(self):
        """AC3: the guard step must run ``git merge-base --is-ancestor`` to verify the
        tag commit is on the main branch.

        FAILS at RED — no guard step exists.
        """
        found = bool(re.search(r"git\s+merge-base\s+--is-ancestor", self.raw))
        self.assertTrue(
            found,
            "publish-pypi.yml publish-pypi guard step must call "
            "'git merge-base --is-ancestor' to verify the tagged commit "
            "is an ancestor of origin/main before publishing.",
        )

    # --- guard fetches origin/main (for the is-ancestor check) ---

    def test_guard_fetches_origin_main(self):
        """AC3: the guard step must fetch origin/main before running is-ancestor.

        FAILS at RED — no guard step exists.
        Spec: 'git fetch --no-tags origin main' then 'git merge-base --is-ancestor'.
        """
        found = bool(re.search(r"git\s+fetch\s+.*origin\s+main|git\s+fetch\s+.*main", self.raw))
        self.assertTrue(
            found,
            "publish-pypi.yml publish-pypi guard step must fetch origin/main "
            "(e.g. 'git fetch --no-tags origin main') before the merge-base check.",
        )


# ---------------------------------------------------------------------------
# AC5 — publish-testpypi job guards
# ---------------------------------------------------------------------------

class PublishTestpypiTest(unittest.TestCase):
    """AC5 — publish-testpypi must stay workflow_dispatch-only and gain a version-sanity step."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_workflow()
        cls.lines = _normalised_lines(cls.raw)

    # --- publish-testpypi is still workflow_dispatch-only ---

    def test_publish_testpypi_if_is_workflow_dispatch_only(self):
        """AC5: publish-testpypi job's ``if`` must be exactly
        ``github.event_name == 'workflow_dispatch'`` and must NOT add any other condition.

        This assertion checks the CURRENT file content — the existing condition must be
        preserved.  The test is GREEN-at-RED for the condition itself (it already exists),
        but combined with the version-sanity test below it verifies the full AC5 contract.
        """
        # Locate the publish-testpypi section
        idx_start = _char_index(r"publish-testpypi\s*:", self.raw)
        self.assertGreater(idx_start, -1, "'publish-testpypi:' job header missing.")
        # Extract the job section (to end of file — it's the last job)
        job_section = self.raw[idx_start:]
        found = bool(re.search(
            r"github\.event_name\s*==\s*['\"]workflow_dispatch['\"]",
            job_section,
        ))
        self.assertTrue(
            found,
            "publish-testpypi job must still have "
            "``if: github.event_name == 'workflow_dispatch'`` as its only condition; "
            "it must not be broadened to other event types.",
        )
        # Must NOT also include push or release in the same if expression
        has_push_in_if = bool(re.search(
            r"github\.event_name\s*==\s*['\"]push['\"].*workflow_dispatch"
            r"|workflow_dispatch.*github\.event_name\s*==\s*['\"]push['\"]",
            job_section,
        ))
        self.assertFalse(
            has_push_in_if,
            "publish-testpypi job's 'if' must NOT include 'push' — it must remain "
            "workflow_dispatch-only.",
        )

    # --- version-sanity step exists and checks for '+' local segment ---

    def test_publish_testpypi_has_version_sanity_step(self):
        """AC5: publish-testpypi must have a version-sanity step that fails when the
        derived version contains a '+' local segment.

        FAILS at RED — no version-sanity step exists in that job.
        """
        idx_start = _char_index(r"publish-testpypi\s*:", self.raw)
        self.assertGreater(idx_start, -1, "'publish-testpypi:' job header missing.")
        job_section = self.raw[idx_start:]
        # The step must reference '+' as the local-segment sentinel
        # Accept: '+' in shell, or "+" in an expression, or 'local' as context
        has_plus_check = bool(re.search(r"['\"\s]\+['\"\s]|'\+'|\"\+\"|\+.*local|local.*\+",
                                         job_section)
                              or "'+'" in job_section
                              or '"+' in job_section
                              or "contains '+'" in job_section
                              or "grep.*\\+" in job_section
                              or re.search(r'"\+"', job_section)
                              or "'+'" in job_section)
        self.assertTrue(
            has_plus_check,
            "publish-testpypi job must have a version-sanity step that explicitly "
            "checks for a '+' (local segment) in the derived version and fails if found; "
            "no such check found in the job section.",
        )

    def test_publish_testpypi_version_sanity_step_fails_on_local_segment(self):
        """AC5: the version-sanity step must be structured to FAIL (exit non-zero) when
        the version contains a '+'; a merely informational echo is not sufficient.

        FAILS at RED — no version-sanity step exists.
        Acceptable patterns: 'exit 1', 'false', 'grep -v +', expression evaluating to failure.
        """
        idx_start = _char_index(r"publish-testpypi\s*:", self.raw)
        self.assertGreater(idx_start, -1, "'publish-testpypi:' job header missing.")
        job_section = self.raw[idx_start:]
        # Must have an explicit failure mechanism
        has_failure_mechanism = bool(
            re.search(r"\bexit\s+1\b|\bfalse\b|\bgrep\s+-v\b", job_section)
            or "raise" in job_section   # python-based check
            or re.search(r"sys\.exit\(1\)", job_section)
            or re.search(r"\|\|\s*(false|exit 1)", job_section)
            or re.search(r"echo.*error.*&&.*exit|error.*exit", job_section, re.IGNORECASE)
        )
        self.assertTrue(
            has_failure_mechanism,
            "publish-testpypi version-sanity step must have an explicit failure "
            "mechanism (exit 1, false, sys.exit(1), or equivalent) when '+' is detected; "
            "a warning echo without exit is insufficient.",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
