"""test_user_guide.py — CR-SAN-040 C1 RED tests.

Asserts the user-facing documentation additions introduced by CR-SAN-040:
  docs/USER_GUIDE.md   (new, plain-language operational guide)
  README.md            (leaner + must-read link to USER_GUIDE.md)
  sandesh notify --help epilog  (stop-reasons / exit-code explanations)

Acceptance criteria tested:
  AC1 — docs/USER_GUIDE.md exists with an MCP-users section AND a Pi-extension
         section (the Pi section must mention 'pi install npm:@anthill-tec/sandesh-pi'
         and that the extension wakes the session itself — native wake, no manual notify)
  AC2 — USER_GUIDE contains a "why the listener stopped" table covering mail-arrived,
         timeout, project-retired/don't-restart, and taken-over
  AC3 — README links docs/USER_GUIDE.md prominently (must-read) AND is shorter
         than its pre-CR size of 216 lines / 11458 bytes (Roadmap condensed/removed)
  AC4 — 'sandesh notify --help' output contains stop-reason / exit-code explanations
         (substrings for mail, timeout, tombstoned/don't-relaunch)
  AC5 — USER_GUIDE mentions the 5 /mcp__sandesh__… lifecycle commands AND notes
         that messaging tools are model-callable / appear under /mcp (not /)
  AC6 — README still contains 'mcp-name: io.github.anthill-tec/sandesh' (guard)

Run:
  PYTHONPATH=. .venv/bin/python tests/test_user_guide.py
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_user_guide \\
      --agent CR-SAN-040-C1-RED
"""

import io
import os
import subprocess
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_README_PATH = os.path.join(_REPO_ROOT, "README.md")
_USER_GUIDE_PATH = os.path.join(_REPO_ROOT, "docs", "USER_GUIDE.md")

# Anchored at test-write time (RED) — GREEN must produce a shorter README.
_README_LINE_CEILING = 216   # current line count (must strictly decrease)
_README_BYTE_CEILING = 11458  # current byte count (must strictly decrease)

# Ownership marker that must survive all edits (AC6 guard).
_MCP_MARKER = "mcp-name: io.github.anthill-tec/sandesh"

# The 5 lifecycle /mcp__ commands (AC5).
_LIFECYCLE_COMMANDS = [
    "/mcp__sandesh__setup",
    "/mcp__sandesh__register",
    "/mcp__sandesh__unregister",
    "/mcp__sandesh__archive",
    "/mcp__sandesh__unarchive",
]


def _read(path: str) -> str:
    """Return full text of a file, or '' if it does not exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _venv_python() -> str:
    """Return the project venv python path."""
    return os.path.join(_REPO_ROOT, ".venv", "bin", "python")


def _run_notify_help() -> str:
    """Run 'sandesh notify --help' via the project venv and return stdout + stderr."""
    venv_python = _venv_python()
    result = subprocess.run(
        [venv_python, "-m", "sandesh.cli", "notify", "--help"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# AC6 — mcp-name marker preserved (guard; PASSES now, must stay green)
# ---------------------------------------------------------------------------

class AC6McpNameMarkerPreservedTest(unittest.TestCase):
    """AC6: README.md must contain the mcp-name ownership marker.

    This is a regression guard — it must stay GREEN before and after the
    docs changes.  The marker is used for MCP Registry ownership verification.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = _read(_README_PATH)

    def test_ac6_readme_contains_mcp_name_marker(self):
        """AC6: README must contain 'mcp-name: io.github.anthill-tec/sandesh'.

        PASSES now (guard); must continue to pass after GREEN.
        An HTML comment form is acceptable:
          <!-- mcp-name: io.github.anthill-tec/sandesh -->
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


# ---------------------------------------------------------------------------
# AC1 — USER_GUIDE.md exists with both MCP and Pi sections
# ---------------------------------------------------------------------------

class AC1UserGuideExistsTest(unittest.TestCase):
    """AC1 (gate): docs/USER_GUIDE.md must exist.

    FAILS now — the file does not exist.
    """

    @classmethod
    def setUpClass(cls):
        cls.guide = _read(_USER_GUIDE_PATH)
        cls.exists = os.path.isfile(_USER_GUIDE_PATH)

    def test_ac1_user_guide_exists(self):
        """AC1: docs/USER_GUIDE.md must be present.

        FAILS now — file absent.  GREEN must create docs/USER_GUIDE.md.
        """
        self.assertTrue(
            self.exists,
            f"docs/USER_GUIDE.md not found at {_USER_GUIDE_PATH}. "
            "GREEN must create docs/USER_GUIDE.md with MCP and Pi sections.",
        )

    def test_ac1_user_guide_is_non_empty(self):
        """AC1: docs/USER_GUIDE.md must be non-empty."""
        if not self.exists:
            self.fail(
                "docs/USER_GUIDE.md does not exist — cannot check content. "
                "GREEN must create docs/USER_GUIDE.md."
            )
        self.assertGreater(
            len(self.guide.strip()),
            100,
            "docs/USER_GUIDE.md is nearly empty — must contain meaningful content.",
        )


class AC1McpSectionTest(unittest.TestCase):
    """AC1: USER_GUIDE.md must contain an MCP-users section.

    The MCP section must:
    - Mention 'sandesh-mcp' (the MCP server command / package)
    - Tell the reader to run 'sandesh notify' in the background
    - Describe the listen→fetch→reply→relaunch loop

    FAILS now — file absent.
    """

    @classmethod
    def setUpClass(cls):
        cls.guide = _read(_USER_GUIDE_PATH)
        cls.exists = os.path.isfile(_USER_GUIDE_PATH)

    def _require_exists(self):
        if not self.exists:
            self.fail(
                "docs/USER_GUIDE.md does not exist — cannot check MCP section. "
                "GREEN must create docs/USER_GUIDE.md with an MCP-users section."
            )

    def test_ac1_mcp_section_mentions_sandesh_mcp(self):
        """AC1: USER_GUIDE MCP section must mention 'sandesh-mcp' (the MCP server).

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "sandesh-mcp",
            self.guide,
            "docs/USER_GUIDE.md must mention 'sandesh-mcp' in the MCP-users section "
            "(the MCP server command / extra name).",
        )

    def test_ac1_mcp_section_tells_reader_to_run_notify_in_background(self):
        """AC1: USER_GUIDE MCP section must tell the reader to run 'sandesh notify' in the background.

        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        has_notify = "sandesh notify" in guide_lower
        has_background = "background" in guide_lower
        self.assertTrue(
            has_notify,
            "docs/USER_GUIDE.md must mention 'sandesh notify' (the listener command) "
            "in the MCP-users section.",
        )
        self.assertTrue(
            has_background,
            "docs/USER_GUIDE.md must tell the reader to run 'sandesh notify' "
            "in the background (background-task mechanism).",
        )

    def test_ac1_mcp_section_describes_listen_fetch_reply_relaunch_loop(self):
        """AC1: USER_GUIDE MCP section must describe the listen→fetch→reply→relaunch loop.

        Each of the four verbs in the loop must appear in the guide.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        for verb in ("listen", "fetch", "reply", "relaunch"):
            self.assertIn(
                verb,
                guide_lower,
                f"docs/USER_GUIDE.md must mention '{verb}' as part of the "
                "listen→fetch→reply→relaunch loop in the MCP-users section.",
            )


class AC1PiSectionTest(unittest.TestCase):
    """AC1: USER_GUIDE.md must contain a Pi-extension-users section.

    The Pi section must:
    - Mention 'pi install npm:@anthill-tec/sandesh-pi'
    - State that the extension wakes the session itself (native wake)
    - State that no manual 'sandesh notify' is needed

    FAILS now — file absent.
    """

    @classmethod
    def setUpClass(cls):
        cls.guide = _read(_USER_GUIDE_PATH)
        cls.exists = os.path.isfile(_USER_GUIDE_PATH)

    def _require_exists(self):
        if not self.exists:
            self.fail(
                "docs/USER_GUIDE.md does not exist — cannot check Pi section. "
                "GREEN must create docs/USER_GUIDE.md with a Pi-extension-users section."
            )

    def test_ac1_pi_section_mentions_install_command(self):
        """AC1: USER_GUIDE Pi section must mention 'pi install npm:@anthill-tec/sandesh-pi'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "pi install npm:@anthill-tec/sandesh-pi",
            self.guide,
            "docs/USER_GUIDE.md must contain the Pi install command "
            "'pi install npm:@anthill-tec/sandesh-pi' in the Pi-extension section.",
        )

    def test_ac1_pi_section_states_native_wake(self):
        """AC1: USER_GUIDE Pi section must state the extension provides native wake.

        Acceptable phrases: 'native wake', 'wakes the session itself', or
        'wakes itself' — any of these conveys that the extension handles the wake.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        native_wake_phrases = (
            "native wake",
            "wakes the session itself",
            "wakes itself",
            "wakes your session",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in native_wake_phrases),
            f"docs/USER_GUIDE.md Pi section must convey that the extension provides "
            f"a native wake (one of {native_wake_phrases}). "
            f"Got guide (first 500 chars): {self.guide[:500]!r}",
        )

    def test_ac1_pi_section_states_no_manual_notify(self):
        """AC1: USER_GUIDE Pi section must state no manual 'sandesh notify' is needed.

        Acceptable phrases include: 'no manual', 'you do not run sandesh notify',
        'do not need to run sandesh notify', 'without running sandesh notify'.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        no_manual_phrases = (
            "no manual",
            "do not run sandesh notify",
            "do not need to run",
            "you do not run",
            "without running sandesh notify",
            "don't run sandesh notify",
            "not run sandesh notify",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in no_manual_phrases),
            f"docs/USER_GUIDE.md Pi section must state that no manual 'sandesh notify' "
            f"is needed (one of {no_manual_phrases}). "
            f"The extension provides native wake — Pi users should NOT run notify manually.",
        )


# ---------------------------------------------------------------------------
# AC2 — stop-reasons table in USER_GUIDE
# ---------------------------------------------------------------------------

class AC2StopReasonsTableTest(unittest.TestCase):
    """AC2: USER_GUIDE must contain a 'why the listener stopped' table.

    The table must cover:
    - mail arrived (exit 0)
    - timeout (exit 2)
    - project retired / don't restart (exit 3 — tombstoned)
    - taken over (exit 4 — evicted)

    FAILS now — file absent.
    """

    @classmethod
    def setUpClass(cls):
        cls.guide = _read(_USER_GUIDE_PATH)
        cls.exists = os.path.isfile(_USER_GUIDE_PATH)

    def _require_exists(self):
        if not self.exists:
            self.fail(
                "docs/USER_GUIDE.md does not exist — cannot check stop-reasons table. "
                "GREEN must create docs/USER_GUIDE.md with a stop-reasons table."
            )

    def test_ac2_stop_reasons_table_present(self):
        """AC2: USER_GUIDE must contain a stop-reasons / listener-stopped table.

        The table must have a heading like 'why' + 'stopped' (case-insensitive).
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        has_stopped_context = (
            "why" in guide_lower and "stopped" in guide_lower
        ) or "listener stopped" in guide_lower or "stop reason" in guide_lower
        self.assertTrue(
            has_stopped_context,
            "docs/USER_GUIDE.md must contain a 'why the listener stopped' table "
            "(with 'why' + 'stopped' or 'listener stopped' or 'stop reason' in the text).",
        )

    def test_ac2_stop_reasons_covers_mail_arrived(self):
        """AC2: stop-reasons table must cover the mail-arrived stop reason.

        Acceptable: 'mail arrived', 'mail landed', 'new mail', 'unread mail'.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        mail_phrases = (
            "mail arrived",
            "mail landed",
            "new mail",
            "unread mail",
            "mail was received",
            "message arrived",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in mail_phrases),
            f"docs/USER_GUIDE.md stop-reasons table must cover mail-arrived "
            f"(one of {mail_phrases}).",
        )

    def test_ac2_stop_reasons_covers_timeout(self):
        """AC2: stop-reasons table must cover the timeout stop reason.

        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        self.assertIn(
            "timeout",
            guide_lower,
            "docs/USER_GUIDE.md stop-reasons table must cover timeout "
            "('timeout' must appear in the guide).",
        )

    def test_ac2_stop_reasons_covers_project_retired_dont_restart(self):
        """AC2: stop-reasons table must cover the project-retired / don't-restart reason.

        The guide must convey: if the project was retired/tombstoned, do NOT relaunch.
        Acceptable: 'retired', 'tombstoned', 'do not restart', 'don't relaunch'.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        retired_phrases = (
            "retired",
            "tombstoned",
            "do not restart",
            "don't restart",
            "do not relaunch",
            "don't relaunch",
            "project retired",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in retired_phrases),
            f"docs/USER_GUIDE.md stop-reasons table must cover project-retired "
            f"and instruct the reader NOT to relaunch (one of {retired_phrases}).",
        )

    def test_ac2_stop_reasons_covers_taken_over(self):
        """AC2: stop-reasons table must cover the taken-over / evicted stop reason.

        Acceptable: 'taken over', 'evicted', 'another listener', 'displaced'.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        taken_over_phrases = (
            "taken over",
            "evicted",
            "another listener",
            "another notifier",
            "displaced",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in taken_over_phrases),
            f"docs/USER_GUIDE.md stop-reasons table must cover taken-over/evicted "
            f"(one of {taken_over_phrases}).",
        )

    def test_ac2_stop_reasons_does_not_use_bare_exit_code_as_heading(self):
        """AC2 / AC6 plain-language gate: the guide must not use 'exit code' as a
        user-facing section heading.

        Stop reasons are framed in plain language (e.g. 'why the listener stopped'),
        not as bare 'Exit codes' headings.  An 'exit code' mention inside a row/cell
        of a table (as additional detail) is acceptable — but the HEADING must not
        be bare 'Exit code(s)' or '## Exit codes'.
        FAILS now (file absent — vacuously, but once the file exists this guards
        against a heading like '## Exit codes').
        """
        self._require_exists()
        import re
        # A heading line that is ONLY about exit codes (e.g. "## Exit codes",
        # "### Exit Codes", "# exit codes").  Plain-language framing required.
        bare_exit_code_heading = re.search(
            r"^#{1,6}\s+exit\s+codes?\s*$",
            self.guide,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        found_heading = bare_exit_code_heading.group(0) if bare_exit_code_heading else None
        self.assertIsNone(
            bare_exit_code_heading,
            "docs/USER_GUIDE.md must NOT use 'Exit code(s)' as a bare section heading. "
            "Use plain language like 'Why the listener stopped' instead. "
            f"Found: {found_heading!r}",
        )


# ---------------------------------------------------------------------------
# AC3 — README leaner + must-read link to USER_GUIDE.md
# ---------------------------------------------------------------------------

class AC3ReadmeLeanerAndLinksGuideTest(unittest.TestCase):
    """AC3: README must link docs/USER_GUIDE.md prominently AND be shorter than before.

    Current size (anchored at RED):
      - Lines: 216
      - Bytes: 11458

    FAILS now — no link to USER_GUIDE.md exists in README.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = _read(_README_PATH)

    def test_ac3_readme_exists(self):
        """Sanity: README.md must exist."""
        self.assertTrue(
            os.path.isfile(_README_PATH),
            f"README.md not found at {_README_PATH}",
        )

    def test_ac3_readme_contains_user_guide_link(self):
        """AC3: README must link to docs/USER_GUIDE.md.

        The link must be present (markdown link or bare path reference).
        FAILS now — README does not mention USER_GUIDE.md.
        """
        has_link = (
            "docs/USER_GUIDE.md" in self.readme
            or "USER_GUIDE.md" in self.readme
        )
        self.assertTrue(
            has_link,
            "README.md must contain a link to docs/USER_GUIDE.md "
            "(e.g. '[User Guide](docs/USER_GUIDE.md)' or 'docs/USER_GUIDE.md'). "
            "GREEN must add a prominent must-read link near the top of README.",
        )

    def test_ac3_readme_user_guide_link_is_prominent(self):
        """AC3: the USER_GUIDE.md link must be near the top of README (prominent).

        Accept: link appears in the first 40 lines of README.md.
        FAILS now — README does not mention USER_GUIDE.md at all.
        """
        lines = self.readme.splitlines()
        first_40 = "\n".join(lines[:40])
        has_link_near_top = (
            "docs/USER_GUIDE.md" in first_40
            or "USER_GUIDE.md" in first_40
        )
        self.assertTrue(
            has_link_near_top,
            "README.md must link to docs/USER_GUIDE.md near the top (first 40 lines). "
            "The link should be a prominent 'must-read / start here' style pointer. "
            "GREEN must place the link high in the README, not buried at the bottom.",
        )

    def test_ac3_readme_is_shorter_than_before_in_lines(self):
        """AC3: README must be strictly shorter than 216 lines (the pre-CR line count).

        The verbose Roadmap section must be removed or condensed.
        FAILS now — README is 216 lines (at or above the ceiling).
        """
        actual_lines = len(self.readme.splitlines())
        self.assertLess(
            actual_lines,
            _README_LINE_CEILING,
            f"README.md must be shorter than {_README_LINE_CEILING} lines after slimming. "
            f"Got {actual_lines} lines. "
            "GREEN must condense/remove the verbose Roadmap section.",
        )

    def test_ac3_readme_is_shorter_than_before_in_bytes(self):
        """AC3: README must be strictly smaller than 11458 bytes (the pre-CR byte count).

        FAILS now — README is 11458 bytes (at or above the ceiling).
        """
        actual_bytes = len(self.readme.encode("utf-8"))
        self.assertLess(
            actual_bytes,
            _README_BYTE_CEILING,
            f"README.md must be smaller than {_README_BYTE_CEILING} bytes after slimming. "
            f"Got {actual_bytes} bytes. "
            "GREEN must trim the README (remove/condense verbose sections).",
        )

    def test_ac3_readme_retains_what_why_model(self):
        """AC3 (quality gate): README must still convey what/why/model content.

        Slimming must not gut the essential content — what/why/the data model
        must survive.
        PASSES now (regression guard).
        """
        readme_lower = self.readme.lower()
        has_why = (
            "model-b" in readme_lower
            or "mailbox" in readme_lower
            or "cooperating" in readme_lower
        )
        self.assertTrue(
            has_why,
            "README.md must retain the why/context content "
            "('Model-B', 'mailbox', or 'cooperating') after slimming.",
        )

    def test_ac3_readme_retains_mcp_name_marker(self):
        """AC3: README must still contain the mcp-name ownership marker after slimming.

        PASSES now (regression guard — same as AC6, cross-checked here).
        """
        self.assertIn(
            _MCP_MARKER,
            self.readme,
            f"README.md must retain {_MCP_MARKER!r} even after slimming. "
            "The marker is required for MCP Registry ownership verification.",
        )


# ---------------------------------------------------------------------------
# AC4 — notify --help epilog contains stop-reason explanations
# ---------------------------------------------------------------------------

class AC4NotifyHelpEpilogTest(unittest.TestCase):
    """AC4: 'sandesh notify --help' output must contain stop-reason / exit-code explanations.

    Currently the notify subparser has NO epilog — help shows only the flags.
    GREEN must add a RawDescriptionHelpFormatter epilog to the notify subparser in cli.py.

    FAILS now — help output contains no stop-reason / exit-code content.
    """

    @classmethod
    def setUpClass(cls):
        cls.help_output = _run_notify_help()

    def test_ac4_notify_help_runs_without_error(self):
        """AC4 (sanity): 'sandesh notify --help' must exit cleanly and produce output."""
        self.assertGreater(
            len(self.help_output.strip()),
            10,
            "'sandesh notify --help' produced no output — CLI may be broken.",
        )

    def test_ac4_notify_help_mentions_mail_arrived(self):
        """AC4: notify --help epilog must mention the mail-arrived stop reason.

        Acceptable: 'mail', 'message arrived', 'unread'.
        FAILS now — no epilog exists.
        """
        help_lower = self.help_output.lower()
        mail_phrases = ("mail", "message arrived", "unread")
        self.assertTrue(
            any(phrase in help_lower for phrase in mail_phrases),
            f"'sandesh notify --help' must explain the mail-arrived stop reason "
            f"(one of {mail_phrases} in epilog). "
            f"Got help output: {self.help_output!r}",
        )

    def test_ac4_notify_help_mentions_timeout(self):
        """AC4: notify --help epilog must mention the timeout stop reason.

        FAILS now — no epilog exists (timeout only appears as --timeout flag).
        The epilog must explain timeout as a stop reason, not just the flag.
        """
        # 'timeout' already appears as a flag name; the epilog should describe
        # it as a stop reason with additional context.  We check that the word
        # appears in the epilog context — i.e. described as a stop reason.
        # A plain 'timed out' or 'timeout' in the epilog satisfies this.
        help_lower = self.help_output.lower()
        timeout_phrases = ("timed out", "timeout expired", "no mail arrived")
        self.assertTrue(
            any(phrase in help_lower for phrase in timeout_phrases),
            f"'sandesh notify --help' epilog must describe the timeout stop reason "
            f"(one of {timeout_phrases}). "
            f"Got help output: {self.help_output!r}",
        )

    def test_ac4_notify_help_mentions_tombstoned_dont_relaunch(self):
        """AC4: notify --help epilog must mention tombstoned / project-retired stop reason.

        The epilog must convey: if tombstoned (project retired), do NOT relaunch.
        Acceptable: 'tombstoned', 'retired', 'do not relaunch', 'don't relaunch'.
        FAILS now — no epilog exists.
        """
        help_lower = self.help_output.lower()
        tombstone_phrases = (
            "tombstoned",
            "retired",
            "do not relaunch",
            "don't relaunch",
            "do not restart",
        )
        self.assertTrue(
            any(phrase in help_lower for phrase in tombstone_phrases),
            f"'sandesh notify --help' epilog must explain tombstoned / project-retired "
            f"stop reason and warn the user NOT to relaunch "
            f"(one of {tombstone_phrases}). "
            f"Got help output: {self.help_output!r}",
        )

    def test_ac4_notify_help_mentions_evicted_taken_over(self):
        """AC4: notify --help epilog must mention the evicted / taken-over stop reason.

        Acceptable: 'evicted', 'taken over', 'another listener'.
        FAILS now — no epilog exists.
        """
        help_lower = self.help_output.lower()
        evicted_phrases = ("evicted", "taken over", "another listener", "another notifier")
        self.assertTrue(
            any(phrase in help_lower for phrase in evicted_phrases),
            f"'sandesh notify --help' epilog must explain the evicted/taken-over "
            f"stop reason (one of {evicted_phrases}). "
            f"Got help output: {self.help_output!r}",
        )

    def test_ac4_notify_help_epilog_is_substantial(self):
        """AC4: notify --help output must be substantially longer than a bare flag list.

        The epilog must add meaningful content; a bare flag listing is insufficient.
        Current bare help is ~5 lines; with epilog it should be at least 10 lines.
        FAILS now — no epilog exists, help is only ~5 lines.
        """
        line_count = len(self.help_output.strip().splitlines())
        self.assertGreaterEqual(
            line_count,
            10,
            f"'sandesh notify --help' must be at least 10 lines with the epilog. "
            f"Got {line_count} lines — the epilog has not been added yet.",
        )


# ---------------------------------------------------------------------------
# AC5 — USER_GUIDE mentions 5 /mcp__sandesh__… commands + /mcp not /
# ---------------------------------------------------------------------------

class AC5LifecycleCommandsAndMcpPanelTest(unittest.TestCase):
    """AC5: USER_GUIDE must mention the 5 /mcp__sandesh__… lifecycle commands AND
    note that messaging tools are model-callable / visible under /mcp (not /).

    FAILS now — file absent.
    """

    @classmethod
    def setUpClass(cls):
        cls.guide = _read(_USER_GUIDE_PATH)
        cls.exists = os.path.isfile(_USER_GUIDE_PATH)

    def _require_exists(self):
        if not self.exists:
            self.fail(
                "docs/USER_GUIDE.md does not exist — cannot check lifecycle commands. "
                "GREEN must create docs/USER_GUIDE.md."
            )

    def test_ac5_guide_mentions_setup_lifecycle_command(self):
        """AC5: USER_GUIDE must mention '/mcp__sandesh__setup'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "/mcp__sandesh__setup",
            self.guide,
            "docs/USER_GUIDE.md must mention the lifecycle command "
            "'/mcp__sandesh__setup' (one of the 5 human on-ramp prompts).",
        )

    def test_ac5_guide_mentions_register_lifecycle_command(self):
        """AC5: USER_GUIDE must mention '/mcp__sandesh__register'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "/mcp__sandesh__register",
            self.guide,
            "docs/USER_GUIDE.md must mention '/mcp__sandesh__register'.",
        )

    def test_ac5_guide_mentions_unregister_lifecycle_command(self):
        """AC5: USER_GUIDE must mention '/mcp__sandesh__unregister'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "/mcp__sandesh__unregister",
            self.guide,
            "docs/USER_GUIDE.md must mention '/mcp__sandesh__unregister'.",
        )

    def test_ac5_guide_mentions_archive_lifecycle_command(self):
        """AC5: USER_GUIDE must mention '/mcp__sandesh__archive'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "/mcp__sandesh__archive",
            self.guide,
            "docs/USER_GUIDE.md must mention '/mcp__sandesh__archive'.",
        )

    def test_ac5_guide_mentions_unarchive_lifecycle_command(self):
        """AC5: USER_GUIDE must mention '/mcp__sandesh__unarchive'.

        FAILS now (file absent).
        """
        self._require_exists()
        self.assertIn(
            "/mcp__sandesh__unarchive",
            self.guide,
            "docs/USER_GUIDE.md must mention '/mcp__sandesh__unarchive'.",
        )

    def test_ac5_guide_notes_messaging_tools_under_mcp_not_slash(self):
        """AC5: USER_GUIDE must note that messaging tools appear under /mcp (not /).

        The guide must convey that the messaging tools are model-callable and
        are confirmed under /mcp (not /), since they are not user-facing prompts.
        Acceptable phrases: '/mcp', 'under /mcp', 'confirm under /mcp',
        'model-callable', 'not under /'.
        FAILS now (file absent).
        """
        self._require_exists()
        guide_lower = self.guide.lower()
        mcp_panel_phrases = (
            "/mcp",
            "under /mcp",
            "confirm under /mcp",
            "model-callable",
            "not under /",
            "under the /mcp",
        )
        self.assertTrue(
            any(phrase in guide_lower for phrase in mcp_panel_phrases),
            f"docs/USER_GUIDE.md must note that messaging tools appear under /mcp "
            f"(one of {mcp_panel_phrases}). "
            f"The guide should clarify: lifecycle commands appear under /, "
            "messaging tools are model-callable (visible under /mcp, not /).",
        )

    def test_ac5_guide_all_five_lifecycle_commands_present(self):
        """AC5 (integration): all 5 /mcp__sandesh__… commands must appear in USER_GUIDE.

        FAILS now (file absent).
        """
        self._require_exists()
        missing = [cmd for cmd in _LIFECYCLE_COMMANDS if cmd not in self.guide]
        self.assertEqual(
            missing,
            [],
            f"docs/USER_GUIDE.md is missing these lifecycle commands: {missing}. "
            "All 5 must be present: "
            + ", ".join(_LIFECYCLE_COMMANDS),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
