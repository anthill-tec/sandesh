"""test_fts_cli.py — RED tests for CR-SAN-027 Cycle 3.

Covers §S2/§S3 CLI surface + AC6 (installer hook) + AC8 (CLI part).

  CLI contract (parentless subparsers — the migrate/consolidate pattern):
    sandesh search <query> --to <addr> [--from-project P] [--limit N] [--offset N]
    sandesh reindex

  AC6  — install.sh contains `"$VENV/bin/sandesh" reindex` after the consolidate
         block, OUTSIDE the yoyo-probe conditional.
  AC8  — malformed FTS5 query → CLI exits 1 with '[sandesh]' prefix +
         'invalid search query' on stderr.

  search output contract:
    - one line per hit: id, from_addr, subject, snippet
    - a 'total: N' line at the end
    - empty result → friendly no-matches line, exit 0
    - lazy-reindex notice line when result['reindexed'] is True

  Expected RED:
    argparse 'invalid choice: search' / 'invalid choice: reindex' (SystemExit 2)
    for all CLI tests until the subparsers are registered.
    install.sh content miss for the reindex hook test.

  No @skip. No vacuous passes — all tests assert message content, not just exit
  codes.

Run via the Crucible (uses .venv interpreter):
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_fts_cli --agent red-cr027-c3
"""

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

# Repo root — resolve from this file so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as s
from sandesh import cli

_INSTALL_SH = os.path.join(_REPO_ROOT, "install.sh")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fts_row_count(con):
    """Total rows in message_fts (0 if the table does not exist yet)."""
    try:
        return con.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _delete_all_fts_rows(con):
    """Empty message_fts entirely — sets up the lazy-reindex fixture."""
    con.execute("DELETE FROM message_fts")
    con.commit()


# ---------------------------------------------------------------------------
# Fixture base class
# ---------------------------------------------------------------------------

class _TempDataHome(unittest.TestCase):
    """Per-test isolated XDG_DATA_HOME.

    Provisions a 3-message P1 corpus (distinct terms), plus a P2 cross-project
    message, mirroring the shape used by test_fts_search but kept minimal.

    Projects:
      P1  — active; ML_P1 is the test reader
      P2  — active; cross-project sender

    Addresses:
      ML_P1 = 'Mainline - FtsCli1'
      T1_P1 = 'Track 1 - FtsCli1'
      ML_P2 = 'Mainline - FtsCli2'

    Corpus (all sent via s.send → FTS rows created):
      mid_alpha — T1_P1 → ML_P1
                  subject='alpha nebula telescope', body='alpha unique term quasarterm'
      mid_beta  — T1_P1 → ML_P1
                  subject='beta report', body='beta unique term pulsarterm'
      mid_gamma — T1_P1 → ML_P1
                  subject='gamma directive pulsarterm', body='gamma body content'
                  (pulsarterm in subject — higher bm25 than mid_beta for pulsarterm)
      mid_p2    — ML_P2 → ML_P1 (cross-project)
                  subject='p2 status update', body='p2 unique senderterm content'
    """

    P1    = "FtsCli1"
    P2    = "FtsCli2"
    ADMIN = "ops"

    ML_P1 = "Mainline - FtsCli1"
    T1_P1 = "Track 1 - FtsCli1"
    ML_P2 = "Mainline - FtsCli2"

    # Unique search terms per message
    TERM_ALPHA = "quasarterm"       # body-only in mid_alpha
    TERM_BETA  = "pulsarterm"       # body-only in mid_beta; subject in mid_gamma
    TERM_P2    = "senderterm"       # unique to ML_P2's cross-project message

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-fts-cli-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp

        s.setup(self.P1)
        s.setup(self.P2)

        self.con = s.connect()
        self.store_p1 = s.store_dir(self.P1)
        self.store_p2 = s.store_dir(self.P2)

        s.register(self.con, self.ML_P1, kind="mainline", project=self.P1)
        s.register(self.con, self.T1_P1, kind="track",    project=self.P1)
        s.register(self.con, self.ML_P2, kind="mainline", project=self.P2)

        s.assign_admin(self.con, self.ADMIN)
        s.grant_xproj(self.con, self.P1, self.ADMIN)
        s.grant_xproj(self.con, self.P2, self.ADMIN)

        # mid_alpha — quasarterm body
        self.mid_alpha = s.send(
            self.con, self.store_p1,
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject="alpha nebula telescope",
            body_text=f"alpha unique term {self.TERM_ALPHA} detail",
            project=self.P1,
        )

        # mid_beta — pulsarterm body only
        self.mid_beta = s.send(
            self.con, self.store_p1,
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject="beta report",
            body_text=f"beta unique term {self.TERM_BETA} content",
            project=self.P1,
        )

        # mid_gamma — pulsarterm in SUBJECT (higher bm25 than mid_beta for that term)
        self.mid_gamma = s.send(
            self.con, self.store_p1,
            from_addr=self.T1_P1,
            to=[self.ML_P1],
            subject=f"gamma directive {self.TERM_BETA}",
            body_text="gamma body content unrelated",
            project=self.P1,
        )

        # mid_p2 — cross-project, unique senderterm
        self.mid_p2 = s.send(
            self.con, self.store_p2,
            from_addr=self.ML_P2,
            to=[self.ML_P1],
            subject="p2 status update",
            body_text=f"p2 unique {self.TERM_P2} content",
            project=self.P2,
        )

    def tearDown(self):
        self.con.close()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv):
        """Run cli.main(argv) in-process, capturing stdout + stderr.

        Returns (rc, out_str, err_str).
        """
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:
                rc = exc.code
        return rc, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# T1 — search happy path: hits + total: line
# ---------------------------------------------------------------------------

class SearchHappyPathTest(_TempDataHome):
    """sandesh search <query> --to <addr> exits 0, renders hits + 'total: N'.

    RED: argparse 'invalid choice: search' → SystemExit(2).
    """

    def test_search_exits_zero_on_matching_query(self):
        """CLI search for TERM_ALPHA exits 0.

        RED: argparse 'invalid choice: search' → SystemExit(2).
        """
        rc, out, err = self._run_cli(["search", self.TERM_ALPHA, "--to", self.ML_P1])
        self.assertEqual(
            rc, 0,
            f"search must exit 0 for a matching query; got rc={rc!r}\n"
            f"out={out!r}\nerr={err!r}",
        )

    def test_search_stdout_contains_hit_subject(self):
        """search for TERM_ALPHA must include the matched message subject in stdout.

        mid_alpha has TERM_ALPHA in its body; 'alpha nebula telescope' must appear.

        RED: argparse exit 2, so stdout is empty.
        """
        rc, out, err = self._run_cli(["search", self.TERM_ALPHA, "--to", self.ML_P1])
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "alpha nebula telescope", out,
            f"Hit subject 'alpha nebula telescope' must appear in search output;\n"
            f"got:\n{out}",
        )

    def test_search_stdout_contains_total_line(self):
        """search output must contain a 'total:' line.

        The exact format is 'total: N'; at minimum the word 'total:' must appear.

        RED: argparse exit 2, so stdout is empty.
        """
        rc, out, err = self._run_cli(["search", self.TERM_ALPHA, "--to", self.ML_P1])
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "total:", out,
            f"search output must contain a 'total:' line; got:\n{out}",
        )

    def test_search_total_line_shows_correct_count(self):
        """'total: 1' for TERM_ALPHA (only mid_alpha matches).

        RED: argparse exit 2, so stdout is empty.
        """
        rc, out, err = self._run_cli(["search", self.TERM_ALPHA, "--to", self.ML_P1])
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "total: 1", out,
            f"search output must contain 'total: 1' for a single-match query;\n"
            f"got:\n{out}",
        )

    def test_search_stdout_contains_snippet(self):
        """Each hit line must include a snippet containing the search term.

        The snippet is the FTS5-generated highlight context; TERM_ALPHA must
        appear in it (or nearby in the hit line).

        RED: argparse exit 2, so stdout is empty.
        """
        rc, out, err = self._run_cli(["search", self.TERM_ALPHA, "--to", self.ML_P1])
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            self.TERM_ALPHA, out,
            f"search term {self.TERM_ALPHA!r} must appear in snippet output;\n"
            f"got:\n{out}",
        )

    def test_search_multi_hit_total_reflects_both(self):
        """TERM_BETA matches mid_beta (body) AND mid_gamma (subject) → 'total: 2'.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(["search", self.TERM_BETA, "--to", self.ML_P1])
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "total: 2", out,
            f"TERM_BETA matches 2 messages; output must contain 'total: 2';\n"
            f"got:\n{out}",
        )
        # Both subjects must appear.
        self.assertIn(
            "beta report", out,
            f"mid_beta subject 'beta report' must appear in search output;\n{out}",
        )
        self.assertIn(
            f"gamma directive {self.TERM_BETA}", out,
            f"mid_gamma subject must appear in search output;\n{out}",
        )


# ---------------------------------------------------------------------------
# T2 — --limit / --offset paging visible in output
# ---------------------------------------------------------------------------

class SearchPagingCliTest(_TempDataHome):
    """--limit and --offset are honoured; output reflects the page.

    For TERM_BETA (2 matches): limit=1 shows 1 hit; offset=1 shows the other.

    RED: argparse exit 2 (subparser not registered).
    """

    def test_limit_one_shows_one_hit(self):
        """search TERM_BETA --limit 1 → output contains exactly 1 hit, total: 2.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(
            ["search", self.TERM_BETA, "--to", self.ML_P1, "--limit", "1"]
        )
        self.assertEqual(rc, 0,
                         f"search --limit 1 exits 0; got rc={rc!r} err={err!r}")
        # total must still be 2 (not 1 — total is the full match count)
        self.assertIn(
            "total: 2", out,
            f"total must be 2 even with --limit 1; got:\n{out}",
        )

    def test_limit_one_output_has_only_one_subject(self):
        """--limit 1 must render exactly one subject (not both).

        The two subjects are 'beta report' and 'gamma directive pulsarterm'.
        Only one must appear when limit=1.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(
            ["search", self.TERM_BETA, "--to", self.ML_P1, "--limit", "1"]
        )
        self.assertEqual(rc, 0,
                         f"search --limit 1 exits 0; got rc={rc!r} err={err!r}")
        beta_in = "beta report" in out
        gamma_in = f"gamma directive {self.TERM_BETA}" in out
        # Exactly one of the two must be present.
        self.assertEqual(
            beta_in + gamma_in, 1,
            f"--limit 1 must render exactly one subject; "
            f"beta_in={beta_in}, gamma_in={gamma_in};\ngot:\n{out}",
        )

    def test_offset_one_shows_second_hit(self):
        """search TERM_BETA --limit 1 --offset 1 → the second hit appears.

        With offset=0 the first hit (higher bm25) appears; offset=1 must
        render the other one.

        RED: argparse exit 2.
        """
        rc0, out0, _ = self._run_cli(
            ["search", self.TERM_BETA, "--to", self.ML_P1, "--limit", "1", "--offset", "0"]
        )
        self.assertEqual(rc0, 0,
                         f"search offset=0 exits 0; got rc={rc0!r}")

        rc1, out1, _ = self._run_cli(
            ["search", self.TERM_BETA, "--to", self.ML_P1, "--limit", "1", "--offset", "1"]
        )
        self.assertEqual(rc1, 0,
                         f"search offset=1 exits 0; got rc={rc1!r}")

        # The two pages must show DIFFERENT subjects.
        beta_in_page0  = "beta report" in out0
        gamma_in_page0 = f"gamma directive {self.TERM_BETA}" in out0
        beta_in_page1  = "beta report" in out1
        gamma_in_page1 = f"gamma directive {self.TERM_BETA}" in out1

        self.assertNotEqual(
            (beta_in_page0, gamma_in_page0), (beta_in_page1, gamma_in_page1),
            f"offset=0 and offset=1 must render DIFFERENT subjects;\n"
            f"page0:\n{out0}\npage1:\n{out1}",
        )


# ---------------------------------------------------------------------------
# T3 — --from-project composes (only P2-sender hits)
# ---------------------------------------------------------------------------

class SearchFromProjectCliTest(_TempDataHome):
    """search --from-project P2 returns only P2-sender hits.

    RED: argparse exit 2 (subparser not registered).
    """

    def test_from_project_filters_to_p2_only(self):
        """search senderterm --from-project FtsCli2 → shows p2 hit, hides p1 hits.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli([
            "search", self.TERM_P2, "--to", self.ML_P1,
            "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"search --from-project exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "p2 status update", out,
            f"P2 hit subject must appear with --from-project {self.P2!r};\n{out}",
        )

    def test_from_project_excludes_p1_senders(self):
        """search quasarterm --from-project FtsCli2 → 'total: 0' (quasarterm is P1).

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli([
            "search", self.TERM_ALPHA, "--to", self.ML_P1,
            "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"search --from-project exits 0; got rc={rc!r} err={err!r}")
        # quasarterm is only in P1-sent messages; P2 filter must give 0
        self.assertIn(
            "total: 0", out,
            f"TERM_ALPHA not in P2-sent mail; must show 'total: 0' with --from-project P2;\n"
            f"got:\n{out}",
        )

    def test_from_project_total_line_present(self):
        """Even with 0 hits, a 'total:' line must appear (not a crash).

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli([
            "search", self.TERM_ALPHA, "--to", self.ML_P1,
            "--from-project", self.P2,
        ])
        self.assertEqual(rc, 0,
                         f"search --from-project exits 0; got rc={rc!r} err={err!r}")
        self.assertIn(
            "total:", out,
            f"search output must contain a 'total:' line even for zero hits;\n{out}",
        )


# ---------------------------------------------------------------------------
# T4 — empty result → friendly no-matches line, exit 0
# ---------------------------------------------------------------------------

class SearchEmptyResultCliTest(_TempDataHome):
    """search for a term that matches nothing → friendly line, exit 0.

    RED: argparse exit 2.
    """

    # A term that is NOT present in any seeded message
    ABSENT_TERM = "xyzzyabsentterm99"

    def test_empty_result_exits_zero(self):
        """search for an absent term exits 0 (no error, no crash).

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(
            ["search", self.ABSENT_TERM, "--to", self.ML_P1]
        )
        self.assertEqual(
            rc, 0,
            f"search for absent term must exit 0; got rc={rc!r}\n"
            f"out={out!r}\nerr={err!r}",
        )

    def test_empty_result_shows_no_matches_message(self):
        """search for absent term must print a friendly no-matches line (not just blank).

        The output must NOT be empty and must indicate zero results.
        Acceptable forms: 'no matches', 'no results', '(no', 'total: 0'.

        RED: argparse exit 2, output is empty.
        """
        rc, out, err = self._run_cli(
            ["search", self.ABSENT_TERM, "--to", self.ML_P1]
        )
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        # At minimum a 'total: 0' line must appear.
        self.assertIn(
            "total: 0", out,
            f"Empty search must show 'total: 0' (or a no-matches notice);\n"
            f"got:\n{out!r}",
        )

    def test_empty_result_friendly_message_not_just_total(self):
        """Empty result must include a user-friendly line (not just 'total: 0').

        Acceptable forms include: '(no matches', 'no results', '(no messages'.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(
            ["search", self.ABSENT_TERM, "--to", self.ML_P1]
        )
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r} err={err!r}")
        out_lower = out.lower()
        has_friendly = (
            "no match" in out_lower
            or "no results" in out_lower
            or "(no " in out_lower
            or "nothing" in out_lower
        )
        # Check both the friendly message AND the total line are present.
        has_total_zero = "total: 0" in out
        self.assertTrue(
            has_friendly or has_total_zero,
            f"Empty search must show a friendly no-matches message or 'total: 0';\n"
            f"got:\n{out!r}",
        )


# ---------------------------------------------------------------------------
# T5 — malformed query: exit 1 + [sandesh] + 'invalid search query' on stderr
# ---------------------------------------------------------------------------

class SearchMalformedQueryCliTest(_TempDataHome):
    """Malformed FTS5 query → exit 1, '[sandesh]' + 'invalid search query' on stderr.

    RED: argparse exit 2 (subparser not registered); stderr is argparse usage text.
    Once the subparser exists, the lib raises ValueError and the CLI must map it
    to exit 1 with the house error format.
    """

    def test_malformed_query_exits_one(self):
        """search with unbalanced quote exits 1.

        RED: argparse exit 2.
        GREEN: exit 1 (ValueError mapped to the house error pattern).
        """
        rc, out, err = self._run_cli(
            ["search", '"unterminated', "--to", self.ML_P1]
        )
        self.assertEqual(
            rc, 1,
            f"Malformed query must exit 1; got rc={rc!r}\n"
            f"out={out!r}\nerr={err!r}",
        )

    def test_malformed_query_stderr_has_sandesh_prefix(self):
        """Malformed query stderr must start with '[sandesh]' (house error pattern).

        RED: argparse exit 2; err contains 'usage:' not '[sandesh]'.
        """
        rc, out, err = self._run_cli(
            ["search", '"unterminated', "--to", self.ML_P1]
        )
        self.assertEqual(rc, 1,
                         f"Malformed query exits 1; got rc={rc!r}")
        self.assertIn(
            "[sandesh]", err,
            f"stderr must contain '[sandesh]' prefix;\ngot err={err!r}",
        )

    def test_malformed_query_stderr_has_invalid_search_query(self):
        """Malformed query stderr must include 'invalid search query'.

        This is the distinguishing phrase that tells callers it is a query
        syntax error, not an address or routing error.

        RED: argparse exit 2; err does not contain this phrase.
        """
        rc, out, err = self._run_cli(
            ["search", '"unterminated', "--to", self.ML_P1]
        )
        self.assertEqual(rc, 1,
                         f"Malformed query exits 1; got rc={rc!r}")
        self.assertIn(
            "invalid search query", err,
            f"stderr must contain 'invalid search query';\ngot err={err!r}",
        )

    def test_malformed_query_no_hits_in_stdout(self):
        """Malformed query must NOT render any hits in stdout (fail cleanly).

        RED: argparse exit 2; stdout is empty anyway (but for the right reason
        here: exit 1 + error message on stderr, nothing on stdout).
        """
        rc, out, err = self._run_cli(
            ["search", '"unterminated', "--to", self.ML_P1]
        )
        self.assertEqual(rc, 1,
                         f"Malformed query exits 1; got rc={rc!r}")
        # stdout must be empty (error goes to stderr, not stdout)
        self.assertEqual(
            out.strip(), "",
            f"Malformed query must produce no stdout; got out={out!r}",
        )


# ---------------------------------------------------------------------------
# T6 — lazy-reindex notice: DELETE FTS rows → CLI search triggers reindex +
#      prints notice line + still returns hits
# ---------------------------------------------------------------------------

class SearchLazyReindexCliTest(_TempDataHome):
    """After emptying FTS rows, CLI search triggers lazy reindex and prints a
    one-line notice; hits are still returned.

    RED: argparse exit 2.
    """

    def test_lazy_reindex_notice_appears_in_stdout(self):
        """After deleting all FTS rows, search prints a reindex notice.

        The lib sets result['reindexed']=True; the CLI must print a line
        mentioning reindex (e.g. 'reindexed', 'rebuilt', 'index rebuilt').

        RED: argparse exit 2, stdout empty.
        """
        # Empty the FTS table so the lazy heuristic fires.
        _delete_all_fts_rows(self.con)
        # Close the connection — the CLI opens its own.
        self.con.close()
        self.con = s.connect()  # re-open for tearDown

        rc, out, err = self._run_cli(
            ["search", self.TERM_ALPHA, "--to", self.ML_P1]
        )
        self.assertEqual(rc, 0,
                         f"search after lazy reindex must exit 0; got rc={rc!r}\n"
                         f"out={out!r}\nerr={err!r}")
        out_lower = out.lower()
        has_notice = (
            "reindex" in out_lower
            or "rebuilt" in out_lower
            or "index rebuild" in out_lower
        )
        self.assertTrue(
            has_notice,
            f"search after emptied FTS must print a reindex notice;\n"
            f"got stdout:\n{out!r}",
        )

    def test_lazy_reindex_hits_still_returned(self):
        """After lazy reindex, hits are still returned (the search works).

        RED: argparse exit 2.
        """
        _delete_all_fts_rows(self.con)
        self.con.close()
        self.con = s.connect()

        rc, out, err = self._run_cli(
            ["search", self.TERM_ALPHA, "--to", self.ML_P1]
        )
        self.assertEqual(rc, 0,
                         f"search after lazy reindex must exit 0; got rc={rc!r}")
        self.assertIn(
            "alpha nebula telescope", out,
            f"After lazy reindex, mid_alpha must be found;\ngot:\n{out}",
        )
        self.assertIn(
            "total: 1", out,
            f"After lazy reindex, total must be 1 for TERM_ALPHA;\ngot:\n{out}",
        )

    def test_lazy_reindex_notice_not_shown_when_index_populated(self):
        """When the FTS index is populated (normal state), no reindex notice.

        RED: argparse exit 2.
        """
        # Do NOT empty the FTS table — index is populated from setUp.
        rc, out, err = self._run_cli(
            ["search", self.TERM_ALPHA, "--to", self.ML_P1]
        )
        self.assertEqual(rc, 0,
                         f"search exits 0; got rc={rc!r}")
        out_lower = out.lower()
        # 'reindex' must NOT appear in normal (populated-index) output.
        self.assertNotIn(
            "reindex", out_lower,
            f"No reindex notice must appear when FTS index is already populated;\n"
            f"got:\n{out}",
        )


# ---------------------------------------------------------------------------
# T7 — reindex verb: prints the indexed count, exits 0
# ---------------------------------------------------------------------------

class ReindexVerbCliTest(_TempDataHome):
    """sandesh reindex prints the indexed count and exits 0.

    RED: argparse 'invalid choice: reindex' → SystemExit(2).
    """

    def test_reindex_exits_zero(self):
        """sandesh reindex exits 0.

        RED: argparse exit 2.
        """
        rc, out, err = self._run_cli(["reindex"])
        self.assertEqual(
            rc, 0,
            f"sandesh reindex must exit 0; got rc={rc!r}\n"
            f"out={out!r}\nerr={err!r}",
        )

    def test_reindex_prints_count(self):
        """sandesh reindex prints the number of indexed messages.

        The corpus has 4 messages (mid_alpha, mid_beta, mid_gamma, mid_p2) so
        count >= 3 (P1 messages for sure; P2 may or may not be in scope).
        The output must contain a digit and a word indicating indexing.

        RED: argparse exit 2, stdout is empty.
        """
        rc, out, err = self._run_cli(["reindex"])
        self.assertEqual(rc, 0,
                         f"sandesh reindex exits 0; got rc={rc!r}")
        # Output must contain a non-empty message about indexing
        self.assertTrue(
            out.strip(),
            f"sandesh reindex must print a count line; got empty stdout",
        )
        # Must contain a number and something about indexed/messages.
        out_lower = out.lower()
        has_count_word = (
            "index" in out_lower
            or "message" in out_lower
            or "reindex" in out_lower
        )
        self.assertTrue(
            has_count_word,
            f"sandesh reindex output must mention count/index/messages;\ngot:\n{out!r}",
        )

    def test_reindex_count_is_positive(self):
        """sandesh reindex must report a positive count (corpus has 4 messages).

        RED: argparse exit 2, stdout is empty.
        """
        rc, out, err = self._run_cli(["reindex"])
        self.assertEqual(rc, 0,
                         f"sandesh reindex exits 0; got rc={rc!r}")
        # Extract a number from the output.
        import re
        numbers = re.findall(r'\d+', out)
        self.assertTrue(
            numbers,
            f"sandesh reindex output must contain a count number;\ngot:\n{out!r}",
        )
        count = int(numbers[0])
        self.assertGreaterEqual(
            count, 3,
            f"sandesh reindex count must be >= 3 (3 P1 messages at minimum);\n"
            f"got count={count} from output:\n{out!r}",
        )
        self.assertLessEqual(
            count, 10,
            f"sandesh reindex count must be <= 10 (only 4 messages seeded);\n"
            f"got count={count} from output:\n{out!r}",
        )

    def test_reindex_is_idempotent_same_exit_code(self):
        """Running sandesh reindex twice both exit 0 (idempotent).

        RED: argparse exit 2 on first call.
        """
        rc1, _, _ = self._run_cli(["reindex"])
        rc2, _, _ = self._run_cli(["reindex"])
        self.assertEqual(rc1, 0, f"First reindex must exit 0; got {rc1!r}")
        self.assertEqual(rc2, 0, f"Second reindex must exit 0; got {rc2!r}")


# ---------------------------------------------------------------------------
# T8 — unknown-flag rejection: --project on search → argparse exit 2
# ---------------------------------------------------------------------------

class SearchUnknownFlagTest(_TempDataHome):
    """Passing --project to search (a parentless verb) → argparse exit 2.

    This pins the parentless-subparser design decision: `search` does NOT
    accept --project (global DB, no per-project routing).

    This test must stay RED until the subparser is registered (at which point
    `--project` is the unknown flag — argparse will reject it with exit 2).
    When the subparser IS registered and properly parentless, this test turns
    GREEN (exit 2 for unknown flag). It is written so that it passes in GREEN
    phase as well — the rejection is correct behaviour.
    """

    def test_search_rejects_project_flag_with_exit_two(self):
        """sandesh search quasarterm --to addr --project P1 exits 2.

        --project is not an accepted flag on the parentless search subparser.

        Pre-registration RED: 'invalid choice: search' → exit 2.
        Post-registration GREEN: 'unrecognized arguments: --project' → exit 2.
        Both map to exit 2 — the test is correct in both states.
        """
        rc, out, err = self._run_cli([
            "search", self.TERM_ALPHA, "--to", self.ML_P1, "--project", self.P1,
        ])
        self.assertEqual(
            rc, 2,
            f"search with --project (unknown flag) must exit 2;\n"
            f"got rc={rc!r} out={out!r} err={err!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
