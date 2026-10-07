"""test_read_mail_docs.py — CR-SAN-054 C8 RED tests (AC10, docs pins).

Pins the doc-side requirements of CR-SAN-054 §S6/AC10:
  - docs/research/PRD-inbox-search.md gains a Change Control section mentioning
    the search snippet and the `--fields` flag.
  - docs/research/PRD-axi-toon.md gains a 1.6 Change Control row; its §4.0 P2
    row lists the new `search` default `id,from,subject,snippet`; its §4.0 P3
    row mentions `--as`; its §4.4 table gains dedicated rows for `search`
    (full field set) and `thread` (withheld bodies).
  - docs/USER_GUIDE.md and sandesh/data/usage-scenarios.md document re-reading
    read mail: `thread --id` together with `--as`, and `--with-body`.

RED today: none of the above doc text exists yet (verified against develop @
aa3268d — PRD-inbox-search has no Change Control section at all; PRD-axi-toon's
Change Control table stops at 1.5; its P2/P4.4 rows list the OLD `search`
default `id,from,subject` with no snippet; USER_GUIDE/usage-scenarios.md have
no `--with-body`, and USER_GUIDE's one `thread --id` occurrence has no `--as`
nearby).

Run:
  PYTHONPATH=. .venv/bin/python tests/test_read_mail_docs.py
  python3 ~/.crucible/clients/python-crucible.py test \\
      --tests tests.test_read_mail_docs \\
      --agent CR-SAN-054-C8-RED
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

_PRD_INBOX_SEARCH_PATH = os.path.join(_REPO_ROOT, "docs", "research", "PRD-inbox-search.md")
_PRD_AXI_TOON_PATH = os.path.join(_REPO_ROOT, "docs", "research", "PRD-axi-toon.md")
_USER_GUIDE_PATH = os.path.join(_REPO_ROOT, "docs", "USER_GUIDE.md")
_USAGE_SCENARIOS_PATH = os.path.join(_REPO_ROOT, "sandesh", "data", "usage-scenarios.md")


def _read(path: str) -> str:
    """Return full text of a file, or '' if it does not exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _section(markdown: str, heading: str) -> str:
    """Slice out a '## <heading>' section (up to the next level-2 heading).

    Raises AssertionError (via a failed find) semantics by returning '' when the
    heading is absent — callers assert on the heading's presence separately.
    """
    start = markdown.find(heading)
    if start == -1:
        return ""
    rest = markdown[start + len(heading):]
    import re
    m = re.search(r"\n## ", rest)
    return rest if m is None else rest[: m.start()]


def _table_row(markdown: str, prefix: str) -> str:
    """Return the first markdown table row (line) starting with `prefix`, or ''."""
    for line in markdown.splitlines():
        if line.startswith(prefix):
            return line
    return ""


class PrdInboxSearchChangeControlTest(unittest.TestCase):
    """AC10 — PRD-inbox-search Change Control mentions `snippet` and `--fields`."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_PRD_INBOX_SEARCH_PATH)
        cls.section = _section(cls.text, "## Change Control")

    def test_change_control_section_exists(self):
        self.assertIn(
            "## Change Control",
            self.text,
            "PRD-inbox-search.md is missing a '## Change Control' section",
        )

    def test_change_control_mentions_snippet(self):
        self.assertIn(
            "snippet",
            self.section,
            "PRD-inbox-search.md's Change Control section does not mention 'snippet'",
        )

    def test_change_control_mentions_fields_flag(self):
        self.assertIn(
            "--fields",
            self.section,
            "PRD-inbox-search.md's Change Control section does not mention '--fields'",
        )


class PrdAxiToonChangeControlTest(unittest.TestCase):
    """AC10 — PRD-axi-toon gains a 1.6 Change Control row."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_PRD_AXI_TOON_PATH)

    def test_change_control_has_1_6_row(self):
        self.assertIn(
            "| 1.6 |",
            self.text,
            "PRD-axi-toon.md's Change Control table has no '| 1.6 |' row",
        )


class PrdAxiToonP2RowTest(unittest.TestCase):
    """AC10 — the §4.0 P2 row's `search` default becomes `id,from,subject,snippet`."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_PRD_AXI_TOON_PATH)
        cls.p2_row = _table_row(cls.text, "| 2 | Minimal default schemas")

    def test_p2_row_exists(self):
        self.assertNotEqual(
            self.p2_row,
            "",
            "PRD-axi-toon.md is missing the §4.0 P2 ('Minimal default schemas') row",
        )

    def test_p2_row_lists_new_search_default_with_snippet(self):
        self.assertIn(
            "id,from,subject,snippet",
            self.p2_row,
            "PRD-axi-toon.md's P2 row does not list the new search default 'id,from,subject,snippet'",
        )


class PrdAxiToonP3RowTest(unittest.TestCase):
    """AC10 — the §4.0 P3 row documents the `--as` caller and withheld bodies."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_PRD_AXI_TOON_PATH)
        cls.p3_row = _table_row(cls.text, "| 3 | Content truncation")

    def test_p3_row_exists(self):
        self.assertNotEqual(
            self.p3_row,
            "",
            "PRD-axi-toon.md is missing the §4.0 P3 ('Content truncation') row",
        )

    def test_p3_row_mentions_as_flag(self):
        self.assertIn(
            "--as",
            self.p3_row,
            "PRD-axi-toon.md's P3 row does not mention '--as'",
        )


class PrdAxiToonSection44Test(unittest.TestCase):
    """AC10 — §4.4 gains dedicated `search` and `thread` rows."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_PRD_AXI_TOON_PATH)
        cls.section = _section(cls.text, "### 4.4 Per-verb result fields")
        cls.search_row = _table_row(cls.section, "| `search`")
        cls.thread_row = _table_row(cls.section, "| `thread`")

    def test_search_row_present_with_full_field_set(self):
        self.assertNotEqual(
            self.search_row,
            "",
            "PRD-axi-toon.md §4.4 has no dedicated '| `search`' row",
        )
        self.assertIn(
            "id,from,subject,kind,created,role,snippet",
            self.search_row,
            "PRD-axi-toon.md §4.4's search row does not list the full field set",
        )

    def test_thread_row_present_with_withheld(self):
        self.assertNotEqual(
            self.thread_row,
            "",
            "PRD-axi-toon.md §4.4 has no dedicated '| `thread`' row",
        )
        self.assertIn(
            "withheld",
            self.thread_row,
            "PRD-axi-toon.md §4.4's thread row does not mention 'withheld'",
        )


class UserGuideReadMailTest(unittest.TestCase):
    """AC10 — USER_GUIDE documents re-reading read mail: `thread --id` + `--as`,
    and `--with-body` paired with a `--limit` mention."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_USER_GUIDE_PATH)

    def test_thread_id_example_mentions_as_flag(self):
        idx = self.text.find("thread --id")
        self.assertNotEqual(
            idx,
            -1,
            "USER_GUIDE.md has no 'thread --id' occurrence",
        )
        # The --as flag must appear near the thread --id example (same guidance block).
        window = self.text[max(0, idx - 400) : idx + 400]
        self.assertIn(
            "--as",
            window,
            "USER_GUIDE.md's 'thread --id' guidance does not mention '--as' nearby",
        )

    def test_with_body_documented_with_limit_mention(self):
        idx = self.text.find("--with-body")
        self.assertNotEqual(
            idx,
            -1,
            "USER_GUIDE.md does not document '--with-body'",
        )
        window = self.text[max(0, idx - 400) : idx + 400]
        self.assertIn(
            "--limit",
            window,
            "USER_GUIDE.md's '--with-body' guidance does not mention '--limit' nearby",
        )


class UsageScenariosReadMailTest(unittest.TestCase):
    """AC10 — the sandesh://usage resource documents `--as` and `--with-body`."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_USAGE_SCENARIOS_PATH)

    def test_mentions_as_flag_for_thread(self):
        idx = self.text.find("thread --id")
        self.assertNotEqual(
            idx,
            -1,
            "usage-scenarios.md has no 'thread --id' occurrence",
        )
        window = self.text[max(0, idx - 400) : idx + 400]
        self.assertIn(
            "--as",
            window,
            "usage-scenarios.md's 'thread --id' guidance does not mention '--as' nearby",
        )

    def test_mentions_with_body_flag(self):
        self.assertIn(
            "--with-body",
            self.text,
            "usage-scenarios.md does not document '--with-body'",
        )


if __name__ == "__main__":
    unittest.main()
