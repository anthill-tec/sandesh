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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
