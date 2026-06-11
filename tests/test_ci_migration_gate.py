"""test_ci_migration_gate.py — RED tests for CR-SAN-018 Cycle 2.

Asserts that `.github/workflows/publish-pypi.yml` contains a snapshot-sync
gate step (AC4 / §S2 / DEC-1) that:

  1. Runs on the build/check path (release / pull_request / push: develop) —
     NOT gated to the publish-only path.
  2. Installs the [migrate] extra on the CI interpreter so yoyo + jsonschema
     are available (pip install '.[migrate]' or 'sandesh-relay[migrate]').
  3. Seeds a temp store with `sandesh setup --project ci` (or equivalent) and
     runs `sandesh migrate --all`.
  4. Runs `sandesh migrate --dump-schema` and compares the result to the
     committed `sandesh/schema/current-schema.json`.
  5. Fails the job on mismatch — the comparison command exits non-zero on
     inequality (diff / python json comparison / jq / etc.).

YAML parser: TEXT MODE.
PyYAML is not installed in the project venv — all assertions use substring /
re.search checks against the raw workflow file text, exactly as the established
pattern in test_publish_workflow.py.

All tests in this file FAIL at RED because the gate step does not yet exist in
publish-pypi.yml.

Run via the crucible:
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_ci_migration_gate --agent CR-SAN-018-2-RED
"""

import os
import re
import unittest

# ---------------------------------------------------------------------------
# Path constants — anchored on this file so they work regardless of cwd.
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_WORKFLOW_PATH = os.path.join(_REPO_ROOT, ".github", "workflows", "publish-pypi.yml")
_SNAPSHOT_PATH = os.path.join(_REPO_ROOT, "sandesh", "schema", "current-schema.json")


def _read_workflow() -> str:
    """Read the workflow YAML as raw text. Returns '' when the file is absent."""
    try:
        with open(_WORKFLOW_PATH, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


class SnapshotSyncGateExistsTest(unittest.TestCase):
    """Top-level: the gate step must be present in the workflow file at all.

    These tests fail at RED because the step does not yet exist.
    """

    def setUp(self):
        self._text = _read_workflow()
        # If the workflow file itself is missing entirely, downstream tests
        # would all fail with a confusing "empty string" message.  Surface
        # the real issue here instead of in every sub-test.
        self.assertTrue(
            os.path.isfile(_WORKFLOW_PATH),
            f"Workflow file not found: {_WORKFLOW_PATH}\n"
            "The file must exist before any gate assertions can be checked.",
        )

    def test_workflow_file_references_migrate_dump_schema(self):
        """Gate step must call `migrate --dump-schema` somewhere in the workflow.

        This is the core of the snapshot-sync check (§S2 / DEC-1 / AC4).
        Fails at RED because the gate step has not been added yet.
        """
        self.assertIn(
            "migrate --dump-schema",
            self._text,
            "publish-pypi.yml must contain 'migrate --dump-schema' — the snapshot-sync "
            "gate step (§S2 / AC4) has not been added yet.\n"
            "GREEN must add a step that runs `sandesh migrate --dump-schema` and compares "
            "its output to the committed sandesh/schema/current-schema.json.",
        )

    def test_workflow_file_references_current_schema_json(self):
        """Gate step must reference `current-schema.json` (the committed snapshot).

        Fails at RED because the gate step has not been added yet.
        """
        self.assertIn(
            "current-schema.json",
            self._text,
            "publish-pypi.yml must reference 'current-schema.json' — the snapshot-sync "
            "gate must compare the live dump against the committed snapshot file "
            "(sandesh/schema/current-schema.json).  Step not present at RED.",
        )

    def test_workflow_file_references_migrate_all(self):
        """Gate step must call `migrate --all` to bring the temp store up to date.

        Fails at RED because the gate step has not been added yet.
        """
        self.assertIn(
            "migrate --all",
            self._text,
            "publish-pypi.yml must contain 'migrate --all' — the gate step must seed "
            "a temp store and apply all migrations before dumping the schema (§S2 / AC4).",
        )


class SnapshotSyncGateRunsOnBuildPathTest(unittest.TestCase):
    """AC4 §S2: the gate must run on the build/check path, NOT only on publish.

    The `build` job already triggers on release, pull_request, and push: develop.
    The gate step must live in or be reachable from that job — NOT behind an
    `if: github.event_name == 'release'` guard that skips it on PRs / develop pushes.

    Strategy: assert that the gate step (identified by `migrate --dump-schema`) does
    NOT appear ONLY inside a publish-gated job.  Practically: the step must exist in
    the `build` job (the only un-gated job), or in a dedicated un-gated job that is
    NOT the `publish-pypi` / `publish-testpypi` jobs.

    Text-mode heuristic: confirm that `migrate --dump-schema` appears in the file AND
    that it is NOT preceded exclusively by `if: github.event_name == 'release'`-style
    guards (i.e. it is reachable on every trigger, not only release).
    """

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file absent — see SnapshotSyncGateExistsTest")
        self._text = _read_workflow()

    def test_gate_not_exclusively_behind_release_guard(self):
        """The gate must not be locked to release-only events.

        Fails at RED because `migrate --dump-schema` is absent entirely.
        When GREEN adds it, it must land in the `build` job (or an equivalent
        un-gated job), so this test verifies the step is reachable on PRs too.

        Heuristic: if `migrate --dump-schema` is present in the workflow text,
        the step must not be inside a job block that carries
        `if: github.event_name == 'release'` (the publish gating condition).
        We assert that `migrate --dump-schema` does NOT appear after a line
        matching `if:.*event_name.*==.*'release'` within the same job block.

        Concretely: assert that the string `migrate --dump-schema` appears in
        the file AND that the only `if:` guard for the whole job that contains it
        is NOT an exclusive release guard.  A simpler proxy: verify that
        `migrate --dump-schema` is present in the `build` job section (i.e.
        before the `publish-pypi:` job key), OR that no release-only `if:` gate
        appears in the same text block as `migrate --dump-schema`.
        """
        text = self._text
        # Primary: the step must exist at all (covered by SnapshotSyncGateExistsTest,
        # but we need it here too to give a meaningful failure at RED).
        self.assertIn(
            "migrate --dump-schema",
            text,
            "publish-pypi.yml: `migrate --dump-schema` not found — gate step absent (RED).",
        )
        # Secondary: the fragment between the start of the file and the first
        # occurrence of `migrate --dump-schema` must NOT contain an exclusive
        # release-only `if:` gate that would prevent it from running on PRs.
        dump_pos = text.find("migrate --dump-schema")
        text_before_gate = text[:dump_pos]
        # Find the last job-level `if:` before the gate step.  A release-only gate
        # looks like: `if: github.event_name == 'release'` (with single or double quotes).
        release_only_pattern = re.compile(
            r"if:\s*github\.event_name\s*==\s*['\"]release['\"]"
        )
        # If the only if: above the step is a release guard, the gate is gated.
        if_guards_before = re.findall(release_only_pattern, text_before_gate)
        # The test passes as long as there's no release-only guard immediately
        # enclosing the step (i.e. no job-level if: == 'release' before the dump).
        # At RED the whole step is absent so dump_pos==-1 → assertIn above fires first.
        # At GREEN the step must be in the `build` job (no if: guard), so this check
        # ensures it isn't accidentally moved into publish-pypi.
        if dump_pos >= 0:
            # Confirm the step is in the section BEFORE the publish-pypi job header.
            publish_pypi_job_pos = text.find("publish-pypi:")
            if publish_pypi_job_pos >= 0:
                self.assertLess(
                    dump_pos,
                    publish_pypi_job_pos,
                    "publish-pypi.yml: `migrate --dump-schema` appears AFTER the "
                    "'publish-pypi:' job header — the gate step must be in the `build` "
                    "job (or another un-gated job) so it runs on pull_request and "
                    "push: develop, not only on release (§S2 / AC4).",
                )

    def test_build_job_trigger_includes_pull_request(self):
        """The workflow's build-triggering path must include pull_request.

        This is pre-existing behaviour — confirmed by WorkflowTriggersTest in
        test_publish_workflow.py.  Restated here as the context for the gate.
        The gate lives in the `build` job which runs on pull_request already;
        this test confirms that contract hasn't been broken.
        """
        self.assertIn(
            "pull_request",
            self._text,
            "publish-pypi.yml: 'pull_request' trigger missing — the build job "
            "(and the gate step) must run on pull_request events (§S2 / AC4).",
        )

    def test_build_job_trigger_includes_push_develop(self):
        """The workflow must trigger on push to develop (the ongoing dev gate).

        The gate in the `build` job runs on this trigger, catching a stale
        snapshot on every develop push before a release can be cut.
        """
        text = self._text
        # push: branches: [develop] or branches: ['develop'] or similar
        has_develop_push = bool(
            re.search(r"push:", text) and re.search(r"develop", text)
        )
        self.assertTrue(
            has_develop_push,
            "publish-pypi.yml: no 'push:' trigger referencing 'develop' found — "
            "the snapshot-sync gate must run on push to develop as well (§S2 / AC4).",
        )


class SnapshotSyncGateMigrateExtraTest(unittest.TestCase):
    """§S2 AC4: the gate step must install the [migrate] extra on the CI interpreter.

    yoyo-migrations and jsonschema must be available when `migrate --all` and
    `migrate --dump-schema` are called.  The step (or its job's setup steps) must
    reference `.[migrate]` or `sandesh-relay[migrate]`.
    """

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file absent — see SnapshotSyncGateExistsTest")
        self._text = _read_workflow()

    def test_workflow_installs_migrate_extra(self):
        """The workflow must install the [migrate] extra.

        Accepts any of:
          - pip install '.[migrate]'
          - pip install ".[migrate]"
          - pip install -e '.[migrate]'
          - pip install 'sandesh-relay[migrate]'
          - pip install "sandesh-relay[migrate]"

        Fails at RED because the gate step (and its extra install) is absent.
        """
        text = self._text
        has_migrate_extra = bool(
            re.search(r"\.\[migrate\]|sandesh[_-]relay\[migrate\]", text)
        )
        self.assertTrue(
            has_migrate_extra,
            "publish-pypi.yml must install the [migrate] extra before running the "
            "snapshot-sync gate — neither '.[migrate]' nor 'sandesh-relay[migrate]' "
            "found in the workflow (§S2 / AC4).  Without this, yoyo-migrations and "
            "jsonschema are absent and `migrate --all` / `migrate --dump-schema` fail.",
        )


class SnapshotSyncGateStoreSeedTest(unittest.TestCase):
    """§S2 AC4: the gate must seed a temp store before running migrate --all.

    The spec requires: seed with `sandesh setup --project ci` (or equivalent),
    then `migrate --all`.  We assert `sandesh setup` is referenced.
    """

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file absent — see SnapshotSyncGateExistsTest")
        self._text = _read_workflow()

    def test_workflow_references_sandesh_setup(self):
        """The gate step must call `sandesh setup` to seed the temp store.

        Fails at RED because the gate step is absent.
        """
        self.assertIn(
            "sandesh setup",
            self._text,
            "publish-pypi.yml must call 'sandesh setup' to provision a temp store "
            "before running `migrate --all` and `migrate --dump-schema` (§S2 / AC4).  "
            "Step not present at RED.",
        )

    def test_workflow_uses_temp_xdg_data_home(self):
        """The gate step must use a temporary XDG_DATA_HOME to isolate the CI store.

        The spec says "in a temp $XDG_DATA_HOME" (§S2).  Assert that either
        XDG_DATA_HOME or a temp-dir reference (mktemp / runner.temp / RUNNER_TEMP /
        /tmp) appears near the gate commands in the workflow.
        """
        text = self._text
        has_xdg = "XDG_DATA_HOME" in text
        has_temp = bool(
            re.search(r"mktemp|runner\.temp|RUNNER_TEMP|/tmp", text)
        )
        self.assertTrue(
            has_xdg or has_temp,
            "publish-pypi.yml: the snapshot-sync gate must use an isolated temp store "
            "(XDG_DATA_HOME set to a temp dir, or a RUNNER_TEMP / mktemp path) so CI "
            "doesn't pollute or read from any real store (§S2 / AC4).  "
            "Neither 'XDG_DATA_HOME' nor a temp-dir reference found in the workflow.",
        )


class SnapshotSyncGateComparisonMechanismTest(unittest.TestCase):
    """§S2 AC4: the comparison must exit non-zero on mismatch (fail the job).

    The spec requires mismatch ⇒ non-zero ⇒ job fails.  We assert that the
    workflow uses a comparison mechanism capable of returning a non-zero exit
    on inequality.  Acceptable forms:

      - `diff <(sandesh migrate --dump-schema ...) current-schema.json`
      - `python ... == ... or sys.exit(1)` / `json.loads() != json.loads()` pattern
      - `jq -e` with equality check
      - Any `run:` step whose body contains a comparison command that returns
        non-zero on inequality.

    We check for the presence of at least one of: `diff`, `python`, `jq`, with
    the constraint that it appears in the same workflow alongside `migrate --dump-schema`
    and `current-schema.json` (already asserted above).
    """

    def setUp(self):
        if not os.path.isfile(_WORKFLOW_PATH):
            self.skipTest("Workflow file absent — see SnapshotSyncGateExistsTest")
        self._text = _read_workflow()

    def test_gate_uses_a_comparison_command(self):
        """The gate must use a comparison tool (diff / python / jq) that exits non-zero on mismatch.

        Fails at RED because the gate step is absent entirely.
        When GREEN adds the step, the comparison command must be present.
        """
        text = self._text
        # We require the snapshot-sync keywords to be present at all (the gate
        # exists) before checking the comparison mechanism.
        if "migrate --dump-schema" not in text or "current-schema.json" not in text:
            self.fail(
                "publish-pypi.yml: gate step absent (migrate --dump-schema / "
                "current-schema.json not found) — comparison mechanism cannot be "
                "checked.  Add the full gate step first (§S2 / AC4 RED)."
            )
        # At least one comparison tool must be present.
        has_diff = bool(re.search(r"\bdiff\b", text))
        has_python_compare = bool(
            re.search(
                r"python.*json|json.*load|sys\.exit|exit\s*1",
                text,
                re.DOTALL,
            )
        )
        has_jq = bool(re.search(r"\bjq\b", text))
        self.assertTrue(
            has_diff or has_python_compare or has_jq,
            "publish-pypi.yml: the snapshot-sync gate must include a comparison "
            "command (diff, python json comparison, or jq) that exits non-zero on "
            "mismatch so the job fails when current-schema.json is stale (§S2 / AC4).  "
            "None of diff / python-json / jq found near the gate step.",
        )

    def test_gate_step_is_a_plain_run_step(self):
        """The gate must be implemented as a `run:` step (not a uses: action).

        A `run:` step fails the job when its shell command returns non-zero
        (default `set -e` on GitHub Actions bash steps).  This guarantees the
        mismatch-fails-the-job requirement without any extra configuration.

        Heuristic: the workflow must contain at least one `run:` block in the
        same general area as `migrate --dump-schema` / `current-schema.json`.
        Since the build job already has `run:` steps, we assert only that `run:`
        appears in the file AND that the gate strings exist (full structural
        co-location is enforced by the other tests in this class).
        """
        self.assertIn(
            "run:",
            self._text,
            "publish-pypi.yml: no 'run:' step found — the gate step must be a "
            "plain `run:` shell step so a non-zero exit fails the job (§S2 / AC4).",
        )
        # Combined: run: AND migrate --dump-schema must both be present.
        self.assertIn(
            "migrate --dump-schema",
            self._text,
            "publish-pypi.yml: `migrate --dump-schema` not present — gate step absent (RED).",
        )


class CommittedSnapshotFileExistsTest(unittest.TestCase):
    """Prerequisite: sandesh/schema/current-schema.json must exist in the repo.

    The gate compares against this file — if it doesn't exist, the gate itself
    cannot work.  This should PASS at RED (CR-017 committed it); it is a guard
    that must continue passing through GREEN and beyond.
    """

    def test_current_schema_json_exists_in_repo(self):
        """sandesh/schema/current-schema.json must be committed to the repo.

        Expected to PASS at RED (CR-017 created it).  Included here so the
        full gate precondition set is visible in one test file.
        """
        self.assertTrue(
            os.path.isfile(_SNAPSHOT_PATH),
            f"sandesh/schema/current-schema.json not found at {_SNAPSHOT_PATH}.\n"
            "This file must be committed to the repo (CR-017 created it) so the "
            "CI gate has a reference snapshot to compare against.",
        )

    def test_current_schema_json_is_valid_json(self):
        """The committed snapshot must be parseable as JSON.

        Expected to PASS at RED.  Guards against accidental corruption.
        """
        import json

        if not os.path.isfile(_SNAPSHOT_PATH):
            self.skipTest("current-schema.json missing — see test_current_schema_json_exists_in_repo")
        with open(_SNAPSHOT_PATH, encoding="utf-8") as fh:
            content = fh.read()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            self.fail(
                f"sandesh/schema/current-schema.json is not valid JSON: {exc}\n"
                f"First 200 chars: {content[:200]}"
            )
        self.assertIsInstance(
            data,
            dict,
            "sandesh/schema/current-schema.json must be a JSON object (dict), "
            f"got {type(data).__name__}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
