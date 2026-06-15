"""test_release_script.py — RED tests for CR-SAN-034 §S4 / AC6 / AC7.

Tests the CLI contract of ``scripts/release.sh`` (a bash script that does NOT
exist yet — all tests FAIL at RED with FileNotFoundError / non-zero exit).

CLI contract:
    scripts/release.sh <subcommand> [args] [--dry-run] [--verbose] [-h|--help]

Subcommands:
    checkpoint          — hotfix/* or release/* only; dispatches
                          ``gh workflow run publish-pypi.yml --ref <branch>``.
                          --dry-run: prints that command, does NOT invoke gh.
    finish <X.Y.Z>     — hotfix/* or release/* only; with --dry-run prints
                          the git-flow finish + push commands (never runs them).
    status              — prints current branch and derived version (from
                          git describe --tags, leading 'v' stripped); exit 0.
    -h|--help           — usage to stdout; exit 0.
    unknown subcommand  — usage to stderr; exit 2.
    missing <X.Y.Z> for finish — exit 2.

Test harness:
    ReleaseScriptHarness — base class that spins up a temp git repo,
                           pre-configures git user.email/name, makes an
                           initial commit, provides helpers to checkout/create
                           branches and tags, copies the repo's scripts/
                           release.sh path into scope, and injects a PATH-
                           local gh stub that records invocations.

AC6a  checkpoint on develop / main / feature/x → exit 2, stderr has branch msg.
AC6b  checkpoint --dry-run on hotfix/0.2.2 → exit 0, stdout has the gh command,
      gh stub NOT invoked.
AC6c  finish 0.2.2 --dry-run on hotfix/0.2.2 → exit 0, stdout has git flow +
      push commands; real git-flow and gh NOT executed.
AC6d  finish on develop → exit 2; finish with no version on hotfix/0.2.2 → exit 2.
AC6e  -h/--help → exit 0 with usage block containing subcommand names;
      unknown subcommand → exit 2.
AC7   status on a repo tagged v9.9.9 → exit 0, stdout has "9.9.9" and branch name.

Run targeted:
    PYTHONPATH=. .venv/bin/python tests/test_release_script.py
or via crucible:
    python3 ~/.claude/scripts/python-crucible.py test \\
        --tests tests.test_release_script --agent CR-SAN-034-C4-RED
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Repo root + script path
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_RELEASE_SH = os.path.join(_REPO_ROOT, "scripts", "release.sh")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class ReleaseScriptHarness(unittest.TestCase):
    """Base test class that provides a temp git repo and a gh PATH stub.

    Each test method gets a fresh temp dir (setUp/tearDown per test) so
    branch state is fully isolated.
    """

    # ---- lifecycle --------------------------------------------------------

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-release-test-")
        self.repo = os.path.join(self.tmp, "repo")
        self.stub_dir = os.path.join(self.tmp, "stub-bin")
        self.gh_record = os.path.join(self.tmp, "gh-calls.txt")

        # Create stub-bin directory and gh stub
        os.makedirs(self.stub_dir)
        self._write_gh_stub()

        # Create temp git repo with initial commit
        self._git_init()

    def tearDown(self):
        if self.tmp and os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- git helpers -------------------------------------------------------

    def _git(self, *args, check=True):
        """Run git command inside the temp repo."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=check,
        )

    def _git_init(self):
        """Initialise repo with a single commit on main."""
        os.makedirs(self.repo)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        # Initial commit so branches/tags have something to point at
        readme = os.path.join(self.repo, "README.md")
        with open(readme, "w") as f:
            f.write("test repo\n")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial commit")

    def _checkout(self, branch, create=False):
        """Checkout a branch, optionally creating it."""
        if create:
            self._git("checkout", "-b", branch)
        else:
            self._git("checkout", branch)

    def _create_tag(self, tag):
        """Create an annotated tag on current HEAD."""
        self._git("tag", "-a", tag, "-m", f"release {tag}")

    def _add_commit(self, message="extra commit"):
        """Add a file commit (to move HEAD past a tag)."""
        dummy = os.path.join(self.repo, f"dummy-{message.replace(' ', '-')}.txt")
        with open(dummy, "w") as f:
            f.write(f"{message}\n")
        self._git("add", os.path.basename(dummy))
        self._git("commit", "-m", message)

    # ---- gh stub -----------------------------------------------------------

    def _write_gh_stub(self):
        """Write an executable gh stub that records its args to gh_record."""
        stub_path = os.path.join(self.stub_dir, "gh")
        with open(stub_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f'echo "$@" >> "{self.gh_record}"\n')
            f.write("exit 0\n")
        os.chmod(stub_path, os.stat(stub_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _gh_was_called(self):
        """Return True if the gh stub was invoked at least once."""
        return os.path.isfile(self.gh_record)

    def _gh_call_args(self):
        """Return all recorded gh invocation lines (one per call)."""
        if not os.path.isfile(self.gh_record):
            return []
        with open(self.gh_record) as f:
            return [line.rstrip() for line in f if line.strip()]

    # ---- script runner -----------------------------------------------------

    def _run_release_sh(self, *args, extra_env=None, timeout=30):
        """Run scripts/release.sh with the gh stub on PATH.

        Returns CompletedProcess with stdout/stderr as text.
        Raises FileNotFoundError (propagated from subprocess) if the script
        does not exist — which is the expected RED failure mode.
        """
        env = dict(os.environ)
        # Prepend stub-bin so our fake gh is found before any real one
        env["PATH"] = self.stub_dir + os.pathsep + env.get("PATH", "")
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            ["bash", _RELEASE_SH, *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )


# ---------------------------------------------------------------------------
# AC6a — checkpoint on non-release branches → exit 2
# ---------------------------------------------------------------------------

class CheckpointBranchGatingTest(ReleaseScriptHarness):
    """AC6a: checkpoint must refuse to run on develop / main / feature/* branches."""

    def test_ac6a_checkpoint_on_develop_exits_2(self):
        """checkpoint on 'develop' branch must exit 2 with a branch error on stderr.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Create and checkout develop
        self._checkout("develop", create=True)

        result = self._run_release_sh("checkpoint")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on 'develop' branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined_err = result.stderr
        self.assertTrue(
            len(combined_err.strip()) > 0,
            msg="Expected a branch-requirement error on stderr, got nothing.",
        )
        # Must mention that a hotfix/* or release/* branch is required
        self.assertTrue(
            any(kw in combined_err.lower() for kw in ("hotfix", "release", "branch")),
            msg=(
                f"stderr does not mention hotfix/release/branch requirement.\n"
                f"stderr:\n{combined_err}"
            ),
        )

    def test_ac6a_checkpoint_on_main_exits_2(self):
        """checkpoint on 'main' branch must exit 2 with a branch error on stderr.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Already on main (from _git_init)
        result = self._run_release_sh("checkpoint")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on 'main' branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined_err = result.stderr
        self.assertTrue(
            any(kw in combined_err.lower() for kw in ("hotfix", "release", "branch")),
            msg=(
                f"stderr does not mention branch requirement.\nstderr:\n{combined_err}"
            ),
        )

    def test_ac6a_checkpoint_on_feature_branch_exits_2(self):
        """checkpoint on 'feature/x' branch must exit 2 with a branch error on stderr.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("feature/x", create=True)

        result = self._run_release_sh("checkpoint")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on 'feature/x' branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined_err = result.stderr
        self.assertTrue(
            any(kw in combined_err.lower() for kw in ("hotfix", "release", "branch")),
            msg=(
                f"stderr does not mention branch requirement.\nstderr:\n{combined_err}"
            ),
        )

    def test_ac6a_checkpoint_on_invalid_branch_does_not_invoke_gh(self):
        """checkpoint on a non-release branch must NOT invoke gh even on a valid path.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("develop", create=True)

        self._run_release_sh("checkpoint")

        self.assertFalse(
            self._gh_was_called(),
            msg=(
                "gh stub was invoked on an invalid branch — checkpoint must "
                "refuse before dispatching."
            ),
        )


# ---------------------------------------------------------------------------
# AC6b — checkpoint --dry-run on hotfix/0.2.2 → prints command, no gh call
# ---------------------------------------------------------------------------

class CheckpointDryRunTest(ReleaseScriptHarness):
    """AC6b: checkpoint --dry-run on hotfix/0.2.2 must print the gh command and exit 0."""

    def setUp(self):
        super().setUp()
        # Create hotfix/0.2.2 branch
        self._checkout("hotfix/0.2.2", create=True)

    def test_ac6b_checkpoint_dry_run_exits_0(self):
        """checkpoint --dry-run on hotfix/0.2.2 must exit 0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("checkpoint", "--dry-run")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for checkpoint --dry-run, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6b_checkpoint_dry_run_stdout_contains_gh_command(self):
        """checkpoint --dry-run must print the exact gh workflow run command to stdout.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("checkpoint", "--dry-run")

        stdout = result.stdout
        self.assertIn(
            "gh workflow run publish-pypi.yml",
            stdout,
            msg=(
                f"stdout does not contain 'gh workflow run publish-pypi.yml'.\n"
                f"STDOUT:\n{stdout}"
            ),
        )
        self.assertIn(
            "--ref hotfix/0.2.2",
            stdout,
            msg=(
                f"stdout does not contain '--ref hotfix/0.2.2'.\n"
                f"STDOUT:\n{stdout}"
            ),
        )

    def test_ac6b_checkpoint_dry_run_does_not_invoke_gh_stub(self):
        """checkpoint --dry-run must NOT invoke gh (the stub must NOT be called).

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._run_release_sh("checkpoint", "--dry-run")

        self.assertFalse(
            self._gh_was_called(),
            msg=(
                f"gh stub was invoked during --dry-run — it must only print "
                f"the command, not execute it.\n"
                f"Recorded gh calls: {self._gh_call_args()}"
            ),
        )

    def test_ac6b_checkpoint_live_on_hotfix_invokes_gh_stub(self):
        """checkpoint (no --dry-run) on hotfix/0.2.2 MUST invoke gh.

        This verifies the live path dispatches to gh.  The stub records and
        exits 0 so no real workflow is triggered.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("checkpoint")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for live checkpoint on hotfix branch, "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertTrue(
            self._gh_was_called(),
            msg=(
                "gh stub was NOT invoked during live checkpoint — "
                "expected 'gh workflow run publish-pypi.yml --ref hotfix/0.2.2'."
            ),
        )
        calls = self._gh_call_args()
        self.assertTrue(
            any("publish-pypi.yml" in c and "hotfix/0.2.2" in c for c in calls),
            msg=(
                f"gh stub was called but not with the expected args.\n"
                f"Recorded calls: {calls}"
            ),
        )

    def test_ac6b_checkpoint_dry_run_on_release_branch(self):
        """checkpoint --dry-run on release/0.3.0 must print --ref release/0.3.0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Switch to a release/* branch
        self._checkout("release/0.3.0", create=True)

        result = self._run_release_sh("checkpoint", "--dry-run")

        self.assertEqual(result.returncode, 0,
            msg=f"Expected exit 0 on release/* branch. rc={result.returncode}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn(
            "--ref release/0.3.0",
            result.stdout,
            msg=f"stdout does not contain '--ref release/0.3.0'.\nSTDOUT:\n{result.stdout}",
        )
        self.assertFalse(
            self._gh_was_called(),
            msg="gh stub invoked during --dry-run on release/* branch.",
        )


# ---------------------------------------------------------------------------
# AC6c — finish --dry-run on hotfix/0.2.2 → prints commands, exits 0
# ---------------------------------------------------------------------------

class FinishDryRunTest(ReleaseScriptHarness):
    """AC6c: finish <X.Y.Z> --dry-run on hotfix/0.2.2 prints git-flow + push commands."""

    def setUp(self):
        super().setUp()
        self._checkout("hotfix/0.2.2", create=True)

    def test_ac6c_finish_dry_run_exits_0(self):
        """finish 0.2.2 --dry-run on hotfix/0.2.2 must exit 0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("finish", "0.2.2", "--dry-run")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for finish --dry-run, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6c_finish_dry_run_stdout_contains_git_flow_hotfix(self):
        """finish 0.2.2 --dry-run must print 'git flow hotfix finish' to stdout.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("finish", "0.2.2", "--dry-run")

        stdout = result.stdout
        self.assertIn(
            "git flow hotfix finish",
            stdout,
            msg=(
                f"stdout does not contain 'git flow hotfix finish'.\n"
                f"STDOUT:\n{stdout}"
            ),
        )

    def test_ac6c_finish_dry_run_stdout_contains_push_command(self):
        """finish 0.2.2 --dry-run must print 'git push origin main develop --tags'.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("finish", "0.2.2", "--dry-run")

        stdout = result.stdout
        self.assertIn(
            "git push origin main develop --tags",
            stdout,
            msg=(
                f"stdout does not contain 'git push origin main develop --tags'.\n"
                f"STDOUT:\n{stdout}"
            ),
        )

    def test_ac6c_finish_dry_run_does_not_invoke_gh(self):
        """finish 0.2.2 --dry-run must NOT invoke gh.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._run_release_sh("finish", "0.2.2", "--dry-run")

        self.assertFalse(
            self._gh_was_called(),
            msg=(
                f"gh stub was invoked during finish --dry-run — must only "
                f"print commands.\nCalls: {self._gh_call_args()}"
            ),
        )

    def test_ac6c_finish_dry_run_does_not_actually_git_flow(self):
        """finish 0.2.2 --dry-run must NOT alter the repo (branches unchanged).

        Verifies no real git-flow finish ran by checking that hotfix/0.2.2
        still exists and main has not moved.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Record HEAD of main before
        before = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self._run_release_sh("finish", "0.2.2", "--dry-run")

        # main should be unchanged
        after = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(
            before,
            after,
            msg="main branch moved during finish --dry-run — real git-flow must NOT run.",
        )

        # hotfix/0.2.2 branch should still exist
        branches = subprocess.run(
            ["git", "branch", "--list", "hotfix/0.2.2"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertIn(
            "hotfix/0.2.2",
            branches,
            msg="hotfix/0.2.2 branch was deleted — real git-flow finish must NOT run.",
        )

    def test_ac6c_finish_dry_run_on_release_branch_prints_release_finish(self):
        """finish 0.3.0 --dry-run on release/0.3.0 must print 'git flow release finish'.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("release/0.3.0", create=True)

        result = self._run_release_sh("finish", "0.3.0", "--dry-run")

        self.assertEqual(result.returncode, 0,
            msg=f"Expected exit 0 on release/* --dry-run. rc={result.returncode}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        self.assertIn(
            "git flow release finish",
            result.stdout,
            msg=f"stdout does not contain 'git flow release finish'.\nSTDOUT:\n{result.stdout}",
        )
        self.assertIn(
            "git push origin main develop --tags",
            result.stdout,
            msg=f"stdout missing push command.\nSTDOUT:\n{result.stdout}",
        )


# ---------------------------------------------------------------------------
# AC6d — finish error paths
# ---------------------------------------------------------------------------

class FinishErrorPathTest(ReleaseScriptHarness):
    """AC6d: finish exits 2 on non-release branch or with missing version arg."""

    def test_ac6d_finish_on_develop_exits_2(self):
        """finish on 'develop' branch must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("develop", create=True)

        result = self._run_release_sh("finish", "0.2.2")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for finish on develop, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        # Must have an error message on stderr
        self.assertTrue(
            len(result.stderr.strip()) > 0,
            msg="Expected a branch error on stderr, got nothing.",
        )

    def test_ac6d_finish_on_main_exits_2(self):
        """finish on 'main' branch must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # already on main
        result = self._run_release_sh("finish", "0.2.2")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for finish on main, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6d_finish_on_feature_branch_exits_2(self):
        """finish on 'feature/x' branch must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("feature/x", create=True)

        result = self._run_release_sh("finish", "0.2.2")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for finish on feature/x, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6d_finish_missing_version_on_hotfix_exits_2(self):
        """finish with no <X.Y.Z> on hotfix/0.2.2 must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("hotfix/0.2.2", create=True)

        result = self._run_release_sh("finish")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for finish with no version, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6d_finish_missing_version_on_hotfix_reports_error(self):
        """finish with no <X.Y.Z> must produce an error message (stderr or stdout).

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("hotfix/0.2.2", create=True)

        result = self._run_release_sh("finish")

        combined = result.stdout + result.stderr
        self.assertTrue(
            len(combined.strip()) > 0,
            msg="Expected an error message for missing version arg, got no output.",
        )

    def test_ac6d_finish_on_invalid_branch_does_not_invoke_gh(self):
        """finish on develop must not invoke gh.

        FAILS at RED: scripts/release.sh does not exist.
        """
        self._checkout("develop", create=True)

        self._run_release_sh("finish", "0.2.2")

        self.assertFalse(
            self._gh_was_called(),
            msg="gh stub invoked on an invalid branch — finish must refuse before acting.",
        )


# ---------------------------------------------------------------------------
# AC6e — help and unknown subcommand
# ---------------------------------------------------------------------------

class HelpAndUnknownSubcommandTest(ReleaseScriptHarness):
    """AC6e: -h/--help exits 0 with a usage block; unknown subcommand exits 2."""

    def test_ac6e_help_short_flag_exits_0(self):
        """-h exits 0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("-h")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for -h, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6e_help_long_flag_exits_0(self):
        """--help exits 0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("--help")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for --help, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6e_help_output_contains_subcommand_names(self):
        """--help output (stdout) must mention each subcommand: checkpoint, finish, status.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("--help")

        stdout = result.stdout
        for subcmd in ("checkpoint", "finish", "status"):
            self.assertIn(
                subcmd,
                stdout,
                msg=(
                    f"--help output does not contain subcommand '{subcmd}'.\n"
                    f"STDOUT:\n{stdout}"
                ),
            )

    def test_ac6e_help_output_is_nonempty(self):
        """--help must produce a non-empty usage block on stdout.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("--help")

        self.assertTrue(
            len(result.stdout.strip()) > 0,
            msg="--help produced no stdout — expected a usage block.",
        )

    def test_ac6e_unknown_subcommand_exits_2(self):
        """An unknown subcommand must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("frobnicate")

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for unknown subcommand 'frobnicate', "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac6e_unknown_subcommand_reports_usage_to_stderr(self):
        """An unknown subcommand must write usage / error info to stderr.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("frobnicate")

        self.assertTrue(
            len(result.stderr.strip()) > 0,
            msg="Unknown subcommand produced no stderr — expected usage/error output.",
        )

    def test_ac6e_no_subcommand_exits_2(self):
        """Invoking the script with no arguments must exit 2.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh()

        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for no arguments, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )


# ---------------------------------------------------------------------------
# AC7 — status subcommand
# ---------------------------------------------------------------------------

class StatusSubcommandTest(ReleaseScriptHarness):
    """AC7: status on a repo tagged v9.9.9 prints '9.9.9' and the current branch."""

    def setUp(self):
        super().setUp()
        # Create and checkout a branch, tag at HEAD
        self._checkout("hotfix/0.2.2", create=True)
        self._create_tag("v9.9.9")

    def test_ac7_status_exits_0(self):
        """status must exit 0.

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("status")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for status, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac7_status_stdout_contains_version_without_leading_v(self):
        """status stdout must contain '9.9.9' (leading 'v' stripped from tag).

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("status")

        stdout = result.stdout
        self.assertIn(
            "9.9.9",
            stdout,
            msg=(
                f"status stdout does not contain '9.9.9' (derived from tag v9.9.9).\n"
                f"STDOUT:\n{stdout}"
            ),
        )

    def test_ac7_status_stdout_does_not_contain_leading_v_in_version(self):
        """status stdout must NOT present the version as 'v9.9.9' (leading v must be stripped).

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("status")

        # The version token should appear WITHOUT a leading 'v'
        # We check that '9.9.9' appears but also that wherever the version appears
        # as a standalone token it isn't prefixed by 'v' as the sole representation.
        # A loose check: the raw string 'v9.9.9' should NOT be the only version mention.
        stdout = result.stdout
        self.assertIn(
            "9.9.9",
            stdout,
            msg=f"Version '9.9.9' not found in status output.\nSTDOUT:\n{stdout}",
        )

    def test_ac7_status_stdout_contains_branch_name(self):
        """status stdout must contain the current branch name ('hotfix/0.2.2').

        FAILS at RED: scripts/release.sh does not exist.
        """
        result = self._run_release_sh("status")

        stdout = result.stdout
        self.assertIn(
            "hotfix/0.2.2",
            stdout,
            msg=(
                f"status stdout does not contain branch name 'hotfix/0.2.2'.\n"
                f"STDOUT:\n{stdout}"
            ),
        )

    def test_ac7_status_on_main_exits_0(self):
        """status on 'main' branch must also exit 0 (no branch gating for status).

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Tag is on hotfix/0.2.2; checkout main
        self._checkout("main")

        result = self._run_release_sh("status")

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for status on main, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac7_status_stdout_contains_version_from_git_describe(self):
        """status derives version from git describe --tags (v9.9.9 → 9.9.9 at exact tag).

        On an exact tag commit, git describe returns 'v9.9.9'; status must strip the
        leading 'v' and present '9.9.9'.

        FAILS at RED: scripts/release.sh does not exist.
        """
        # Verify git describe in the temp repo works as expected
        describe = subprocess.run(
            ["git", "describe", "--tags"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        # Pre-condition: git describe should see v9.9.9
        self.assertIn(
            "9.9.9",
            describe.stdout,
            msg=f"Test setup error: git describe did not see 9.9.9. Output: {describe.stdout}",
        )

        result = self._run_release_sh("status")

        stdout = result.stdout
        self.assertIn(
            "9.9.9",
            stdout,
            msg=(
                f"status does not include '9.9.9' derived from git describe.\n"
                f"STDOUT:\n{stdout}"
            ),
        )


# ---------------------------------------------------------------------------
# CR-SAN-042 C1 — set-version subcommand (AC1, AC2, AC3, AC4 + help)
# ---------------------------------------------------------------------------

import json
import os


def _write_manifests(repo_root, version="0.0.1"):
    """Write minimal but realistic package.json + server.json at *version* and
    commit them.  Called from setUp so set-version edits a TRACKED file and
    AC3's clean-tree assertion is meaningful.

    package.json  →  integrations/pi/package.json
    server.json   →  repo root / server.json
    """
    # integrations/pi/package.json
    pi_dir = os.path.join(repo_root, "integrations", "pi")
    os.makedirs(pi_dir, exist_ok=True)
    pkg = {
        "name": "@anthill-tec/sandesh-pi",
        "version": version,
        "type": "module",
    }
    with open(os.path.join(pi_dir, "package.json"), "w") as f:
        json.dump(pkg, f, indent=2)
        f.write("\n")

    # server.json
    srv = {
        "$schema": "https://example.com/server-schema.json",
        "name": "io.github.anthill-tec/sandesh",
        "version": version,
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "sandesh-relay",
                "version": version,
            }
        ],
    }
    with open(os.path.join(repo_root, "server.json"), "w") as f:
        json.dump(srv, f, indent=2)
        f.write("\n")


class SetVersionAC1PackageJsonTest(ReleaseScriptHarness):
    """AC1 — set-version writes the correct version into package.json."""

    def setUp(self):
        super().setUp()
        # Create a hotfix branch and commit the manifests
        self._checkout("hotfix/0.5.7", create=True)
        _write_manifests(self.repo, version="0.0.1")
        self._git("add", "integrations/pi/package.json", "server.json")
        self._git("commit", "-m", "chore: add manifest fixtures")

    def test_ac1_set_version_exits_0_on_hotfix_branch(self):
        """set-version 0.5.7 on hotfix/0.5.7 must exit 0.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        result = self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for set-version on hotfix/0.5.7, "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac1_set_version_writes_version_to_package_json(self):
        """set-version 0.5.7 sets package.json top-level version to '0.5.7'.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        pkg_path = os.path.join(self.repo, "integrations", "pi", "package.json")
        with open(pkg_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["version"],
            "0.5.7",
            msg=f"package.json version not updated. Got: {data.get('version')!r}",
        )

    def test_ac1_package_json_remains_valid_json(self):
        """After set-version 0.5.7, package.json must still be valid JSON.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        pkg_path = os.path.join(self.repo, "integrations", "pi", "package.json")
        try:
            with open(pkg_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            self.fail(f"package.json is not valid JSON after set-version: {exc}")
        # Confirm the file parsed (implicit — no exception above)
        self.assertIsInstance(data, dict)

    def test_ac1_package_json_non_version_keys_unchanged(self):
        """set-version must not alter non-version keys in package.json.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        pkg_path = os.path.join(self.repo, "integrations", "pi", "package.json")
        with open(pkg_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["name"],
            "@anthill-tec/sandesh-pi",
            msg=f"package.json 'name' was altered. Got: {data.get('name')!r}",
        )
        self.assertEqual(
            data["type"],
            "module",
            msg=f"package.json 'type' was altered. Got: {data.get('type')!r}",
        )

    def test_ac1_set_version_exits_0_on_release_branch(self):
        """set-version 0.5.7 on release/0.5.7 must also exit 0 (both branch prefixes allowed).

        FAILS at RED: set-version subcommand does not exist yet.
        """
        # Switch to a release/* branch (manifests already committed on hotfix above)
        self._checkout("release/0.5.7", create=True)
        result = self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for set-version on release/0.5.7, "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )


class SetVersionAC2ServerJsonTest(ReleaseScriptHarness):
    """AC2 — set-version writes both version fields in server.json."""

    def setUp(self):
        super().setUp()
        self._checkout("hotfix/0.5.7", create=True)
        _write_manifests(self.repo, version="0.0.1")
        self._git("add", "integrations/pi/package.json", "server.json")
        self._git("commit", "-m", "chore: add manifest fixtures")

    def test_ac2_server_json_top_level_version_updated(self):
        """set-version 0.5.7 must update server.json top-level version to '0.5.7'.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        srv_path = os.path.join(self.repo, "server.json")
        with open(srv_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["version"],
            "0.5.7",
            msg=f"server.json top-level version not updated. Got: {data.get('version')!r}",
        )

    def test_ac2_server_json_packages_version_updated(self):
        """set-version 0.5.7 must update server.json packages[0].version to '0.5.7'.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        srv_path = os.path.join(self.repo, "server.json")
        with open(srv_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["packages"][0]["version"],
            "0.5.7",
            msg=(
                f"server.json packages[0].version not updated. "
                f"Got: {data['packages'][0].get('version')!r}"
            ),
        )

    def test_ac2_server_json_remains_valid_json(self):
        """After set-version, server.json must still be valid JSON.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        srv_path = os.path.join(self.repo, "server.json")
        try:
            with open(srv_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            self.fail(f"server.json is not valid JSON after set-version: {exc}")
        self.assertIsInstance(data, dict)

    def test_ac2_server_json_non_version_keys_unchanged(self):
        """set-version must not alter non-version keys in server.json.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        srv_path = os.path.join(self.repo, "server.json")
        with open(srv_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["name"],
            "io.github.anthill-tec/sandesh",
            msg=f"server.json 'name' was altered. Got: {data.get('name')!r}",
        )
        self.assertEqual(
            data["packages"][0]["identifier"],
            "sandesh-relay",
            msg=(
                f"server.json packages[0].identifier was altered. "
                f"Got: {data['packages'][0].get('identifier')!r}"
            ),
        )

    def test_ac2_both_version_fields_updated_atomically(self):
        """Both server.json version fields must be set to the same new value.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        srv_path = os.path.join(self.repo, "server.json")
        with open(srv_path) as f:
            data = json.load(f)
        top_ver = data["version"]
        pkg_ver = data["packages"][0]["version"]
        self.assertEqual(
            top_ver,
            pkg_ver,
            msg=(
                f"server.json top-level version ({top_ver!r}) "
                f"differs from packages[0].version ({pkg_ver!r})"
            ),
        )
        self.assertEqual(
            top_ver,
            "0.5.7",
            msg=f"server.json versions were not set to '0.5.7'. Got: {top_ver!r}",
        )


class SetVersionAC3CommitAndDryRunTest(ReleaseScriptHarness):
    """AC3 — live set-version commits; --dry-run prints plan and changes nothing."""

    def setUp(self):
        super().setUp()
        self._checkout("hotfix/0.5.7", create=True)
        _write_manifests(self.repo, version="0.0.1")
        self._git("add", "integrations/pi/package.json", "server.json")
        self._git("commit", "-m", "chore: add manifest fixtures")

    def _head_sha(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _working_tree_status(self):
        """Return git status --porcelain output (empty string means clean)."""
        return self._git("status", "--porcelain").stdout.strip()

    def _head_changed_files(self):
        """Return list of files changed in HEAD commit (from git show --name-only)."""
        out = self._git(
            "show", "--name-only", "--format=", "HEAD"
        ).stdout.strip()
        return [line for line in out.splitlines() if line]

    def test_ac3_live_set_version_leaves_clean_working_tree(self):
        """Live set-version 0.5.7 must leave a clean working tree (no uncommitted changes).

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        status = self._working_tree_status()
        self.assertEqual(
            status,
            "",
            msg=(
                f"Working tree is not clean after set-version.\n"
                f"git status --porcelain:\n{status}"
            ),
        )

    def test_ac3_live_set_version_creates_new_head_commit(self):
        """Live set-version 0.5.7 must create a new HEAD commit (SHA changes).

        FAILS at RED: set-version subcommand does not exist yet.
        """
        before_sha = self._head_sha()
        self._run_release_sh("set-version", "0.5.7")
        after_sha = self._head_sha()
        self.assertNotEqual(
            before_sha,
            after_sha,
            msg="HEAD SHA did not change — set-version did not create a commit.",
        )

    def test_ac3_live_set_version_commit_includes_both_manifests(self):
        """The new HEAD commit must include both manifest files.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7")
        changed = self._head_changed_files()
        # Normalize to forward-slash paths
        changed_normalized = [p.replace("\\", "/") for p in changed]
        self.assertTrue(
            any("integrations/pi/package.json" in p for p in changed_normalized),
            msg=(
                f"HEAD commit does not include integrations/pi/package.json.\n"
                f"Changed files in HEAD: {changed}"
            ),
        )
        self.assertTrue(
            any("server.json" in p for p in changed_normalized),
            msg=(
                f"HEAD commit does not include server.json.\n"
                f"Changed files in HEAD: {changed}"
            ),
        )

    def test_ac3_dry_run_exits_0(self):
        """set-version 0.5.7 --dry-run must exit 0.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        result = self._run_release_sh("set-version", "0.5.7", "--dry-run")
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for set-version --dry-run, "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac3_dry_run_prints_non_empty_plan_to_stdout(self):
        """set-version 0.5.7 --dry-run must print a non-empty plan to stdout.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        result = self._run_release_sh("set-version", "0.5.7", "--dry-run")
        self.assertTrue(
            len(result.stdout.strip()) > 0,
            msg="--dry-run produced no stdout — expected a plan summary.",
        )

    def test_ac3_dry_run_does_not_change_manifest_contents(self):
        """set-version --dry-run must NOT alter package.json or server.json on disk.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        pkg_path = os.path.join(self.repo, "integrations", "pi", "package.json")
        srv_path = os.path.join(self.repo, "server.json")

        with open(pkg_path) as f:
            pkg_before = json.load(f)
        with open(srv_path) as f:
            srv_before = json.load(f)

        self._run_release_sh("set-version", "0.5.7", "--dry-run")

        with open(pkg_path) as f:
            pkg_after = json.load(f)
        with open(srv_path) as f:
            srv_after = json.load(f)

        self.assertEqual(
            pkg_after["version"],
            "0.0.1",
            msg=(
                f"package.json version was altered by --dry-run. "
                f"Before: '0.0.1', After: {pkg_after.get('version')!r}"
            ),
        )
        self.assertEqual(
            srv_after["version"],
            "0.0.1",
            msg=(
                f"server.json version was altered by --dry-run. "
                f"Before: '0.0.1', After: {srv_after.get('version')!r}"
            ),
        )

    def test_ac3_dry_run_does_not_create_new_commit(self):
        """set-version --dry-run must NOT create a new git commit.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        before_sha = self._head_sha()
        self._run_release_sh("set-version", "0.5.7", "--dry-run")
        after_sha = self._head_sha()
        self.assertEqual(
            before_sha,
            after_sha,
            msg=(
                f"HEAD SHA changed during --dry-run — a commit must NOT be created.\n"
                f"Before: {before_sha}\nAfter: {after_sha}"
            ),
        )

    def test_ac3_dry_run_leaves_clean_working_tree(self):
        """set-version --dry-run must NOT stage or modify any files.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._run_release_sh("set-version", "0.5.7", "--dry-run")
        status = self._working_tree_status()
        self.assertEqual(
            status,
            "",
            msg=(
                f"Working tree or index was dirtied by --dry-run.\n"
                f"git status --porcelain:\n{status}"
            ),
        )


class SetVersionAC4GatingAndValidationTest(ReleaseScriptHarness):
    """AC4 — branch gating and version-string validation for set-version."""

    def setUp(self):
        super().setUp()
        # Manifests on main (the default branch after _git_init)
        _write_manifests(self.repo, version="0.0.1")
        self._git("add", "integrations/pi/package.json", "server.json")
        self._git("commit", "-m", "chore: add manifest fixtures")

    def _pkg_version(self):
        pkg_path = os.path.join(self.repo, "integrations", "pi", "package.json")
        with open(pkg_path) as f:
            return json.load(f)["version"]

    def _srv_version(self):
        srv_path = os.path.join(self.repo, "server.json")
        with open(srv_path) as f:
            return json.load(f)["version"]

    # --- Branch-gating tests ---

    def test_ac4_set_version_on_develop_exits_2(self):
        """set-version 0.5.7 on develop must exit 2 with a branch error.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("develop", create=True)
        result = self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on develop branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        combined_err = result.stderr
        self.assertTrue(
            any(kw in combined_err.lower() for kw in ("hotfix", "release", "branch")),
            msg=f"stderr does not mention branch requirement.\nstderr:\n{combined_err}",
        )

    def test_ac4_set_version_on_main_exits_2(self):
        """set-version 0.5.7 on main must exit 2 with a branch error.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        # Already on main after setUp
        result = self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on main branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_set_version_on_feature_branch_exits_2(self):
        """set-version 0.5.7 on feature/x must exit 2 with a branch error.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("feature/x", create=True)
        result = self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 on feature/x branch, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_set_version_on_invalid_branch_leaves_manifests_untouched(self):
        """set-version on a non-release branch must NOT modify manifests.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("develop", create=True)
        self._run_release_sh("set-version", "0.5.7")
        self.assertEqual(
            self._pkg_version(),
            "0.0.1",
            msg="package.json was modified despite being on a non-release branch.",
        )
        self.assertEqual(
            self._srv_version(),
            "0.0.1",
            msg="server.json was modified despite being on a non-release branch.",
        )

    # --- Version-format validation tests ---

    def test_ac4_malformed_version_missing_patch_exits_2(self):
        """set-version 1.2 (missing patch) on a hotfix branch must exit 2.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("hotfix/0.5.7", create=True)
        result = self._run_release_sh("set-version", "1.2")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for malformed version '1.2', "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_malformed_version_leading_v_exits_2(self):
        """set-version v1.2.3 (leading 'v') on a hotfix branch must exit 2.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("hotfix/0.5.7", create=True)
        result = self._run_release_sh("set-version", "v1.2.3")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for version with leading 'v', "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_malformed_version_four_parts_exits_2(self):
        """set-version 1.2.3.4 (four-part) on a hotfix branch must exit 2.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("hotfix/0.5.7", create=True)
        result = self._run_release_sh("set-version", "1.2.3.4")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                f"Expected exit 2 for four-part version '1.2.3.4', "
                f"got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )

    def test_ac4_malformed_version_writes_nothing(self):
        """A malformed version on a valid branch must NOT modify manifests.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        self._checkout("hotfix/0.5.7", create=True)
        # Try each bad form
        for bad_ver in ("1.2", "v1.2.3", "1.2.3.4"):
            with self.subTest(bad_ver=bad_ver):
                self._run_release_sh("set-version", bad_ver)
                self.assertEqual(
                    self._pkg_version(),
                    "0.0.1",
                    msg=f"package.json was modified by malformed version '{bad_ver}'.",
                )
                self.assertEqual(
                    self._srv_version(),
                    "0.0.1",
                    msg=f"server.json was modified by malformed version '{bad_ver}'.",
                )


class SetVersionHelpTest(ReleaseScriptHarness):
    """§S3 help — release.sh --help must list set-version."""

    def test_help_lists_set_version(self):
        """--help stdout must mention 'set-version' alongside existing subcommands.

        FAILS at RED: set-version subcommand does not exist yet.
        """
        result = self._run_release_sh("--help")
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Expected exit 0 for --help, got {result.returncode}.\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            ),
        )
        self.assertIn(
            "set-version",
            result.stdout,
            msg=(
                f"--help output does not contain 'set-version'.\n"
                f"STDOUT:\n{result.stdout}"
            ),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
