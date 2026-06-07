"""test_usage_resource_packaging.py — Package-data bundling tests for the
sandesh://usage resource (CR-SAN-008 §S7/AC10).

§S7/AC10 requires that docs/usage-scenarios.md is bundled as package data at
``sandesh/data/usage-scenarios.md`` and read via ``importlib.resources`` (not via a
filesystem walk from repo_root). These tests pin that mechanism.

RED state:
  - Assertions 1, 2, 4, 5 FAIL: the file is still at docs/usage-scenarios.md (not
    bundled), and mcp_server.py still uses the repo_root/docs path walk.
  - Assertion 3 MAY already pass: _read_usage_doc() finds docs/ via the path walk
    from a source checkout and returns real content (not the stub).

All five assertions must PASS after GREEN.

Run:
  python-crucible.py test --tests tests.test_usage_resource_packaging \
    --agent CR-SAN-008-C3-RED
"""

import inspect
import unittest
from importlib import resources

from sandesh import mcp_server


class UsageDocPackagingTest(unittest.IsolatedAsyncioTestCase):
    """AC10: usage-scenarios.md is bundled as package data and read via
    importlib.resources (CR-SAN-008 §S7)."""

    # -------------------------------------------------------------------------
    # Assertion 1 — file is bundled as package data
    # -------------------------------------------------------------------------

    def test_usage_doc_bundled_at_sandesh_data_path(self):
        """AC10 (1): importlib.resources.files('sandesh').joinpath('data/usage-scenarios.md')
        resolves to a readable file.

        RED: fails because sandesh/data/usage-scenarios.md does not exist yet
        (the file is still at docs/usage-scenarios.md, outside the package).
        """
        p = resources.files("sandesh").joinpath("data/usage-scenarios.md")
        self.assertTrue(
            p.is_file(),
            "usage-scenarios.md must be bundled at sandesh/data/usage-scenarios.md "
            "(importlib.resources.files('sandesh').joinpath('data/usage-scenarios.md').is_file()). "
            "The file is currently only at docs/usage-scenarios.md and is NOT packaged.",
        )

    # -------------------------------------------------------------------------
    # Assertion 2 — bundled file has real content
    # -------------------------------------------------------------------------

    def test_bundled_usage_doc_contains_real_content(self):
        """AC10 (2): the bundled package-data file contains at least 'Model-B' and
        a section marker ('Tool-by-tool' / 'tool-by-tool').

        RED: fails together with assertion 1 (the file does not exist to read).
        Will pass only once the file is bundled with real content.
        """
        p = resources.files("sandesh").joinpath("data/usage-scenarios.md")
        # If the file is missing, fail with a clear message rather than AttributeError.
        self.assertTrue(
            p.is_file(),
            "Precondition: sandesh/data/usage-scenarios.md must be bundled "
            "(see assertion 1). Skipping content check.",
        )
        text = p.read_text(encoding="utf-8")
        self.assertIn(
            "Model-B",
            text,
            "Bundled usage-scenarios.md must contain the string 'Model-B' "
            "(the real doc, not a stub).",
        )
        # Section 5 header "Tool-by-tool reference" appears in the real doc.
        self.assertIn(
            "ool-by-tool",
            text,
            "Bundled usage-scenarios.md must contain a 'Tool-by-tool' section heading "
            "(case-insensitive substring 'ool-by-tool'). The real doc has '## 5. Tool-by-tool reference'.",
        )

    # -------------------------------------------------------------------------
    # Assertion 3 — _read_usage_doc() returns real doc (may already pass)
    # -------------------------------------------------------------------------

    def test_read_usage_doc_returns_real_content_not_stub(self):
        """AC10 (3): mcp_server._read_usage_doc() returns content containing 'Model-B'
        and NOT the stub signature 'could not be located'.

        This may already PASS from a source checkout (the current docs/ path walk
        finds docs/usage-scenarios.md). It is kept as a guard that must still hold
        after GREEN (the importlib.resources read must also return the real doc).
        """
        text = mcp_server._read_usage_doc()
        self.assertIn(
            "Model-B",
            text,
            "_read_usage_doc() must return the real doc containing 'Model-B', "
            f"not a stub. Got (first 300 chars): {text[:300]!r}",
        )
        self.assertNotIn(
            "could not be located",
            text,
            "_read_usage_doc() returned the fallback stub (contains 'could not be located'). "
            "It must return the real usage-scenarios.md content.",
        )

    # -------------------------------------------------------------------------
    # Assertion 4 — resource read serves real content (async)
    # -------------------------------------------------------------------------

    async def test_usage_resource_read_returns_real_doc_content(self):
        """AC10 (4): await mcp_server.mcp.read_resource('sandesh://usage') returns
        content containing 'Model-B' and longer than 500 chars (real doc, not stub).

        RED: After GREEN switches to importlib.resources, this must still pass AND the
        mechanism must be the bundled file. Currently the resource returns real content
        from the docs/ path walk, so this assertion may happen to pass now — but
        assertion 5 will still be RED (the mechanism is wrong).
        Note: we assert length >= 500 chars as a proxy for 'real doc, not stub'.
        """
        contents = list(await mcp_server.mcp.read_resource("sandesh://usage"))
        self.assertGreater(
            len(contents),
            0,
            "read_resource('sandesh://usage') must return at least one content item.",
        )
        text = contents[0].content
        self.assertIsInstance(
            text,
            str,
            f"read_resource content item .content must be a str, "
            f"got {type(text).__name__!r}",
        )
        self.assertIn(
            "Model-B",
            text,
            "read_resource('sandesh://usage') content must contain 'Model-B' "
            "(the real doc, not the fallback stub). "
            f"Got (first 300 chars): {text[:300]!r}",
        )
        self.assertGreater(
            len(text),
            500,
            "read_resource('sandesh://usage') content must be longer than 500 chars "
            "(real doc is ~18 KB; stub is ~350 chars). "
            f"Got {len(text)} chars.",
        )

    # -------------------------------------------------------------------------
    # Assertion 5 — implementation uses importlib.resources (not the path walk)
    # -------------------------------------------------------------------------

    def test_mcp_server_uses_importlib_resources_not_repo_root_walk(self):
        """AC10 (5): sandesh/mcp_server.py source uses importlib.resources, NOT the old
        repo_root/docs path walk.

        RED now because:
          - 'importlib.resources' is NOT in the current source.
          - 'repo_root' IS in the current source (the old walk mechanism).

        After GREEN:
          - 'importlib' and 'resources' must appear in the source.
          - 'repo_root' must NOT appear (the docs-walk variable is gone).
        """
        src = inspect.getsource(mcp_server)

        # Must use importlib.resources (the new mechanism).
        self.assertIn(
            "importlib",
            src,
            "sandesh/mcp_server.py must import 'importlib' (for importlib.resources). "
            "The old docs/ path walk does not use importlib.",
        )
        self.assertIn(
            "resources",
            src,
            "sandesh/mcp_server.py must reference 'resources' (from importlib.resources). "
            "The current source uses os.path.join instead.",
        )
        # Must NOT use the old repo_root walk.
        self.assertNotIn(
            "repo_root",
            src,
            "sandesh/mcp_server.py must NOT contain 'repo_root' after GREEN. "
            "The old implementation walks os.path.dirname(here) to find docs/; "
            "that must be replaced by importlib.resources.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
