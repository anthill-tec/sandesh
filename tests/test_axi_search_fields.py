"""test_axi_search_fields.py — RED tests for CR-SAN-054 cycle 1 (§S1 — search
returns its snippet in machine mode), against:
  docs/changes/CR-SAN-054-read-mail-readable.md  §S1, AC1

Scope for THIS cycle (AC1 only):
  1. `sandesh --format json search "gateway" --to "Mainline - Demo"` → each hit's
     keys are exactly {id, from, subject, snippet}, and the snippet contains
     "[gateway]" (the FTS5 highlight).
  2. `--fields id,snippet` → keys exactly {id, snippet}. The full field set
     `id,from,subject,kind,created,role,snippet` is accepted and returned.
  3. `--fields bogus` → `ok:false`, exit 2, naming the bad field AND the full
     valid set (mirrors tests/test_axi_verbs.py's
     test_bogus_fields_value_exits_2_ok_false_names_field_and_valid_set for
     `addressbook`).
  4. Toon mode decodes to the same default keys as JSON mode (round-trip,
     mirrors tests/test_axi_cli_format.py's
     test_format_json_decodes_to_the_same_dict_as_the_toon_output).

Expected RED (confirmed empirically against the current tree before writing
these tests): `cli.py`'s `search` subparser has no `--fields` argument at all
(`sandesh/cli.py` l.1515-1524), so every `--fields ...` invocation here fails
argparse's own "unrecognized arguments" check (exit 2) rather than the
feature's field-selection/validation logic; and `SEARCH_DEFAULT` is still
`("id", "from", "subject")` (`sandesh/cli.py` l.905) with no `snippet` key, so
the default-fields test fails on `assertEqual(set(row.keys()), ...)` (the
`snippet` key is simply absent), not on an import/collection error.

Fixture (per AC1's prose): project `Demo`, `Mainline - Demo` + `Track 1 -
Demo` registered, one message `#m` from `Track 1 - Demo` to `Mainline - Demo`
whose body contains "gateway timeout", already fetched (read) via
`sdb.fetch` before any search runs (AC1: "already fetched (read)").

Conventions follow tests/test_axi_disclosure.py: `run_cli()`/`_toon.decode`,
XDG-temp store per test, in-process `cli.main`.

Run (from the repo root):
  python3 -m unittest -v tests.test_axi_search_fields
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_search_fields.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import _toon  # noqa: E402
from sandesh import cli  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr). Mirrors
    tests/test_axi_disclosure.py's run_cli — handles both a plain int return
    and SystemExit (argparse -> exit(2); the house
    `sys.exit(f"[sandesh] {msg}")` single-arg idiom -> exit 1)."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    prev = {}
    if env:
        for k, v in env.items():
            prev[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
                if rc is None:
                    rc = 0
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    rc = 0
                elif isinstance(code, int):
                    rc = code
                else:
                    text = str(code)
                    err_buf.write(text if text.endswith("\n") else text + "\n")
                    rc = 1
    finally:
        if env:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return rc, out_buf.getvalue(), err_buf.getvalue()


# The full SEARCH_FIELDS set per §S1: "id", "from", "subject", "kind",
# "created", "role", "snippet".
_FULL_SEARCH_FIELDS = ("id", "from", "subject", "kind", "created", "role", "snippet")


class _SearchFixture(unittest.TestCase):
    """project 'Demo', Mainline - Demo + Track 1 - Demo registered, one
    message from Track 1 - Demo to Mainline - Demo whose body contains
    'gateway timeout', already fetched (read) before any test body runs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-search-fields-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_format = os.environ.pop("SANDESH_FORMAT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)

        sdb.setup("Demo")
        self.con = sdb.connect()
        self.store = sdb.store_dir("Demo")
        sdb.register(self.con, "Mainline - Demo", kind="mainline", project="Demo")
        sdb.register(self.con, "Track 1 - Demo", kind="track", project="Demo")
        self.mid = sdb.send(
            self.con, self.store, "Track 1 - Demo", to=["Mainline - Demo"],
            subject="incident-42", kind="request",
            body_text="Please investigate the gateway timeout quickly.",
            project="Demo")
        # "already fetched (read)" per AC1's fixture prose.
        sdb.fetch(self.con, self.store, "Mainline - Demo")

    def tearDown(self):
        self.con.close()
        for var, prev in (("XDG_DATA_HOME", self._prev_xdg),
                          ("SANDESH_FORMAT", self._prev_format),
                          ("SANDESH_ADDRESS", self._prev_address),
                          ("SANDESH_PROJECT", self._prev_project)):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, argv, env=None):
        return run_cli(argv, env=env)


# =========================================================================== #
# Item 1 — JSON mode: default keys exactly {id, from, subject, snippet},
# snippet contains the "[gateway]" highlight.
# =========================================================================== #

class SearchDefaultFieldsJsonTest(_SearchFixture):

    def test_json_default_fields_are_exactly_id_from_subject_snippet_with_highlight(self):
        rc, out, err = self.run_cli(
            ["--format", "json", "search", "gateway", "--to", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertIn("hits", axi)
        hits = axi["hits"]
        self.assertEqual(len(hits), 1, f"expected exactly 1 hit; got {hits!r}")
        row = hits[0]
        self.assertEqual(
            set(row.keys()), {"id", "from", "subject", "snippet"},
            f"AC1 default keys are exactly id,from,subject,snippet; got {sorted(row.keys())!r}")
        self.assertIn(
            "[gateway]", row["snippet"],
            f"the FTS5 highlight must wrap the matched term; got {row['snippet']!r}")
        self.assertEqual(row["id"], self.mid)
        self.assertEqual(row["from"], "Track 1 - Demo")
        self.assertEqual(row["subject"], "incident-42")


# =========================================================================== #
# Item 2 — --fields CSV: a named subset, and the full 7-column set.
# =========================================================================== #

class SearchFieldsSelectionTest(_SearchFixture):

    def test_fields_id_snippet_returns_exactly_those_two_keys(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "gateway", "--to", "Mainline - Demo",
             "--fields", "id,snippet"])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        hits = axi["hits"]
        self.assertEqual(len(hits), 1, f"expected exactly 1 hit; got {hits!r}")
        row = hits[0]
        self.assertEqual(set(row.keys()), {"id", "snippet"})
        self.assertEqual(row["id"], self.mid)
        self.assertIn("[gateway]", row["snippet"])

    def test_fields_full_set_is_accepted_and_returns_all_seven_columns(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "gateway", "--to", "Mainline - Demo",
             "--fields", ",".join(_FULL_SEARCH_FIELDS)])
        self.assertEqual(rc, 0, f"err={err!r}")
        axi = _toon.decode(out)["axi"]
        hits = axi["hits"]
        self.assertEqual(len(hits), 1, f"expected exactly 1 hit; got {hits!r}")
        row = hits[0]
        self.assertEqual(set(row.keys()), set(_FULL_SEARCH_FIELDS))
        self.assertEqual(row["id"], self.mid)
        self.assertEqual(row["from"], "Track 1 - Demo")
        self.assertEqual(row["subject"], "incident-42")
        self.assertEqual(row["kind"], "request")
        self.assertEqual(row["role"], "to")
        self.assertTrue(row["created"], "created must be a non-empty timestamp string")
        self.assertIn("[gateway]", row["snippet"])


# =========================================================================== #
# Item 3 — --fields bogus: ok:false, exit 2, names the bad field + valid set.
# =========================================================================== #

class SearchBogusFieldsTest(_SearchFixture):

    def test_bogus_fields_value_exits_2_ok_false_names_field_and_valid_set(self):
        rc, out, err = self.run_cli(
            ["--format", "toon", "search", "gateway", "--to", "Mainline - Demo",
             "--fields", "bogus"])
        self.assertEqual(rc, 2, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertIs(axi["ok"], False)
        self.assertIn("bogus", axi["error"],
                      f"error must name the offending field; got {axi['error']!r}")
        for name in _FULL_SEARCH_FIELDS:
            self.assertIn(name, axi["error"],
                          f"error must list the valid field set; got {axi['error']!r}")


# =========================================================================== #
# Item 4 — toon mode decodes to the same default keys as JSON mode.
# =========================================================================== #

class SearchToonJsonParityTest(_SearchFixture):

    def test_toon_default_fields_decode_to_the_same_dict_as_json(self):
        rc_toon, out_toon, err_toon = self.run_cli(
            ["--format", "toon", "search", "gateway", "--to", "Mainline - Demo"])
        rc_json, out_json, err_json = self.run_cli(
            ["--format", "json", "search", "gateway", "--to", "Mainline - Demo"])
        self.assertEqual(rc_toon, 0, f"err={err_toon!r}")
        self.assertEqual(rc_json, 0, f"err={err_json!r}")
        decoded_toon = _toon.decode(out_toon)
        decoded_json = json.loads(out_json)
        self.assertEqual(decoded_toon, decoded_json)
        row = decoded_toon["axi"]["hits"][0]
        self.assertEqual(set(row.keys()), {"id", "from", "subject", "snippet"})


if __name__ == "__main__":
    unittest.main()
