"""test_read_body.py — unit tests for `sandesh_db.read_body(path)` (CR-SAN-054 F1,
VERIFY S1): the one body-read idiom shared by `fetch()`, the CLI and the MCP server.

An existing file yields its utf-8 text; a missing path yields exactly
`(body file missing: <path>)`. File I/O only — no DB, nothing marked read.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import unittest

from sandesh import sandesh_db as sdb  # noqa: E402
from tests._store_guard import TempStore  # noqa: E402


class ReadBodyTests(TempStore, unittest.TestCase):

    def test_existing_file_returns_its_text(self):
        path = os.path.join(self._tmp.name, "msg-1.md")
        text = "line one\nzwei — drei ✓\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.assertEqual(sdb.read_body(path), text)

    def test_missing_file_returns_missing_marker(self):
        path = os.path.join(self._tmp.name, "no-such-msg.md")
        self.assertEqual(sdb.read_body(path), f"(body file missing: {path})")


if __name__ == "__main__":
    unittest.main()
