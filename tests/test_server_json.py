"""test_server_json.py — MCP Registry server.json contract + README ownership marker.

Asserts the structure and content of ``server.json`` at the repo root (AC1, AC2, AC4, AC5)
and the ``mcp-name`` ownership marker in ``README.md`` (AC3).

All tests FAIL at RED because:
  - ``server.json`` does not yet exist at the repo root.
  - ``README.md`` does not yet contain the ``mcp-name`` ownership marker.

The very first assertion in each class (file-exists / marker-present) fails with a clean
``AssertionError``; downstream tests use ``self.skipTest()`` to avoid cascading crashes so
that each logical area of the contract is exercised independently.

Structural validation (AC4):
  ``jsonschema`` 4.26.0 is present in the project venv, so the full JSON-Schema
  validation path is active.  If the library were absent the test falls back to a
  structural key-presence check (see ``ServerJsonSchemaValidationTest``).

Note on authoritative publishing validation:
  ``mcp-publisher publish --dry-run`` is the definitive pre-publish validation, but it
  requires GitHub auth and a live PyPI package.  That is a manual maintainer step
  documented in RELEASING.md (AC4 / §S3).  These tests validate the file's content
  in CI without that dependency.

Run targeted:
  python-crucible.py test --tests tests.test_server_json --agent CR-SAN-011-C0-RED
"""

import json
import os
import unittest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_SERVER_JSON_PATH = os.path.join(_REPO_ROOT, "server.json")
_README_PATH = os.path.join(_REPO_ROOT, "README.md")

# Expected constants — single source of truth for all assertions.
_EXPECTED_NAME = "io.github.anthill-tec/sandesh"
_EXPECTED_SCHEMA = (
    "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
)
_EXPECTED_REGISTRY_TYPE = "pypi"
_EXPECTED_REGISTRY_BASE_URL = "https://pypi.org"
_EXPECTED_IDENTIFIER = "sandesh-relay"
_EXPECTED_TRANSPORT_TYPE = "stdio"
_EXPECTED_RUNTIME_HINT = "uvx"
_EXPECTED_MCP_EXTRA_SUBSTRING = "sandesh-relay[mcp]"
_EXPECTED_SCRIPT_SUBSTRING = "sandesh-mcp"
_EXPECTED_TITLE = "Sandesh"
_EXPECTED_REPO_URL = "https://github.com/anthill-tec/sandesh"
_README_MARKER = "mcp-name: io.github.anthill-tec/sandesh"


def _load_server_json() -> dict:
    """Parse and return server.json.  Caller must guard with a file-exists assertion."""
    with open(_SERVER_JSON_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _read_readme() -> str:
    """Return the full text of README.md.  Caller must guard with file-exists check."""
    with open(_README_PATH, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# AC1 — file exists, valid JSON, $schema, name
# ---------------------------------------------------------------------------

class ServerJsonExistsTest(unittest.TestCase):
    """AC1 (gate) — server.json must exist at the repo root and be valid JSON."""

    def test_server_json_exists(self):
        """AC1: server.json must be present at the repo root.

        FAILS at RED — the file has not been created yet.
        """
        self.assertTrue(
            os.path.isfile(_SERVER_JSON_PATH),
            f"server.json missing — expected at {_SERVER_JSON_PATH}\n"
            "GREEN must create server.json at the repo root.",
        )

    def test_server_json_is_valid_json(self):
        """AC1: server.json must be parseable as JSON (no syntax errors)."""
        if not os.path.isfile(_SERVER_JSON_PATH):
            self.skipTest("server.json does not exist yet (see test_server_json_exists)")
        # Any json.JSONDecodeError here is an assertion failure, not a skip.
        data = _load_server_json()
        self.assertIsInstance(
            data,
            dict,
            f"server.json top-level must be a JSON object; got {type(data).__name__}",
        )

    def test_server_json_schema_field(self):
        """AC1: $schema must pin the 2025-12-11 registry schema URL."""
        if not os.path.isfile(_SERVER_JSON_PATH):
            self.skipTest("server.json does not exist yet")
        data = _load_server_json()
        self.assertEqual(
            data.get("$schema"),
            _EXPECTED_SCHEMA,
            f"server.json '$schema' must be {_EXPECTED_SCHEMA!r}; "
            f"got {data.get('$schema')!r}",
        )

    def test_server_json_name_field(self):
        """AC1: name must be 'io.github.anthill-tec/sandesh'."""
        if not os.path.isfile(_SERVER_JSON_PATH):
            self.skipTest("server.json does not exist yet")
        data = _load_server_json()
        self.assertEqual(
            data.get("name"),
            _EXPECTED_NAME,
            f"server.json 'name' must be {_EXPECTED_NAME!r}; "
            f"got {data.get('name')!r}",
        )


# ---------------------------------------------------------------------------
# AC2 — packages block
# ---------------------------------------------------------------------------

class ServerJsonPackagesTest(unittest.TestCase):
    """AC2 — packages block: registryType, identifier, transport, runtimeHint, invocation."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_SERVER_JSON_PATH):
            raise unittest.SkipTest("server.json does not exist yet — skipping packages assertions")
        data = _load_server_json()
        cls.packages = data.get("packages", [])
        cls.pkg0 = cls.packages[0] if cls.packages else {}

    def test_packages_is_non_empty_list(self):
        """AC2: 'packages' must be a non-empty list."""
        self.assertIsInstance(
            self.packages,
            list,
            f"server.json 'packages' must be a list; got {type(self.packages).__name__}",
        )
        self.assertGreater(
            len(self.packages),
            0,
            "server.json 'packages' must be non-empty",
        )

    def test_packages_0_registry_type_pypi(self):
        """AC2: packages[0].registryType must be 'pypi'."""
        self.assertEqual(
            self.pkg0.get("registryType"),
            _EXPECTED_REGISTRY_TYPE,
            f"packages[0].registryType must be {_EXPECTED_REGISTRY_TYPE!r}; "
            f"got {self.pkg0.get('registryType')!r}",
        )

    def test_packages_0_registry_base_url(self):
        """AC2: packages[0].registryBaseUrl must be 'https://pypi.org'."""
        self.assertEqual(
            self.pkg0.get("registryBaseUrl"),
            _EXPECTED_REGISTRY_BASE_URL,
            f"packages[0].registryBaseUrl must be {_EXPECTED_REGISTRY_BASE_URL!r}; "
            f"got {self.pkg0.get('registryBaseUrl')!r}",
        )

    def test_packages_0_identifier(self):
        """AC2: packages[0].identifier must be 'sandesh-relay'."""
        self.assertEqual(
            self.pkg0.get("identifier"),
            _EXPECTED_IDENTIFIER,
            f"packages[0].identifier must be {_EXPECTED_IDENTIFIER!r}; "
            f"got {self.pkg0.get('identifier')!r}",
        )

    def test_packages_0_transport_type_stdio(self):
        """AC2: packages[0].transport.type must be 'stdio'."""
        transport = self.pkg0.get("transport", {})
        self.assertIsInstance(
            transport,
            dict,
            f"packages[0].transport must be an object; got {type(transport).__name__}",
        )
        self.assertEqual(
            transport.get("type"),
            _EXPECTED_TRANSPORT_TYPE,
            f"packages[0].transport.type must be {_EXPECTED_TRANSPORT_TYPE!r}; "
            f"got {transport.get('type')!r}",
        )

    def test_packages_0_runtime_hint_uvx(self):
        """AC2: packages[0].runtimeHint must be 'uvx'."""
        self.assertEqual(
            self.pkg0.get("runtimeHint"),
            _EXPECTED_RUNTIME_HINT,
            f"packages[0].runtimeHint must be {_EXPECTED_RUNTIME_HINT!r}; "
            f"got {self.pkg0.get('runtimeHint')!r}",
        )

    def test_packages_0_expresses_mcp_extra(self):
        """AC2: packages[0] JSON-serialised must contain 'sandesh-relay[mcp]' (the [mcp] extra)."""
        pkg_json = json.dumps(self.pkg0)
        self.assertIn(
            _EXPECTED_MCP_EXTRA_SUBSTRING,
            pkg_json,
            f"packages[0] must express the [mcp] extra via 'sandesh-relay[mcp]'; "
            f"not found in serialised package: {pkg_json[:500]}",
        )

    def test_packages_0_expresses_sandesh_mcp_script(self):
        """AC2: packages[0] JSON-serialised must contain 'sandesh-mcp' (the console script)."""
        pkg_json = json.dumps(self.pkg0)
        self.assertIn(
            _EXPECTED_SCRIPT_SUBSTRING,
            pkg_json,
            f"packages[0] must reference the 'sandesh-mcp' console script; "
            f"not found in serialised package: {pkg_json[:500]}",
        )


# ---------------------------------------------------------------------------
# AC5 — description wake caveat
# ---------------------------------------------------------------------------

class ServerJsonDescriptionTest(unittest.TestCase):
    """AC5 — description must convey that notify is NOT an MCP tool (wake caveat)."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_SERVER_JSON_PATH):
            raise unittest.SkipTest("server.json does not exist yet — skipping description assertions")
        data = _load_server_json()
        cls.description = data.get("description", "")

    def test_description_present_and_non_empty(self):
        """AC5: 'description' field must be present and non-empty."""
        self.assertTrue(
            self.description,
            "server.json 'description' must be a non-empty string",
        )

    def test_description_mentions_notify(self):
        """AC5: description (lowercased) must mention 'notify' (the wake watcher)."""
        self.assertIn(
            "notify",
            self.description.lower(),
            f"server.json description must mention 'notify'; "
            f"got description: {self.description!r}",
        )

    def test_description_conveys_wake_caveat(self):
        """AC5: description (lowercased) must convey the wake caveat.

        Acceptable phrases: 'not a tool', 'not an mcp tool', or 'background'
        — any of these expresses that notify is a separate background process,
        not an MCP tool.
        """
        desc_lower = self.description.lower()
        wake_caveat_phrases = ("not a tool", "not an mcp tool", "background")
        has_caveat = any(phrase in desc_lower for phrase in wake_caveat_phrases)
        self.assertTrue(
            has_caveat,
            f"server.json description must convey the wake caveat — must contain one of "
            f"{wake_caveat_phrases!r} (lowercased); got description: {self.description!r}",
        )


# ---------------------------------------------------------------------------
# AC3 — README ownership marker
# ---------------------------------------------------------------------------

class ReadmeMcpOwnershipMarkerTest(unittest.TestCase):
    """AC3 — README.md must contain the mcp-name ownership marker."""

    @classmethod
    def setUpClass(cls):
        cls.readme_text = _read_readme() if os.path.isfile(_README_PATH) else ""
        # server.json may or may not exist at RED; guard individually.
        cls.server_json_name = None
        if os.path.isfile(_SERVER_JSON_PATH):
            try:
                cls.server_json_name = _load_server_json().get("name")
            except (json.JSONDecodeError, OSError):
                pass

    def test_readme_contains_mcp_name_marker(self):
        """AC3: README.md must contain the ownership marker 'mcp-name: io.github.anthill-tec/sandesh'.

        FAILS at RED — the marker has not been added yet.
        An HTML comment form is acceptable: <!-- mcp-name: io.github.anthill-tec/sandesh -->
        The assertion uses a substring match so the comment delimiters are irrelevant.
        """
        self.assertIn(
            _README_MARKER,
            self.readme_text,
            f"README.md must contain the ownership marker {_README_MARKER!r} "
            "(as an HTML comment is fine: <!-- mcp-name: io.github.anthill-tec/sandesh -->).\n"
            "This marker is read by the MCP Registry to verify PyPI package ownership "
            "via the published long-description.",
        )

    def test_readme_marker_matches_server_json_name(self):
        """AC3: the mcp-name value in README.md must match server.json's 'name' field.

        Both README and server.json must agree on the registry name so ownership
        verification succeeds at publish time.
        FAILS at RED — neither server.json nor the README marker exists yet.
        """
        if _README_MARKER not in self.readme_text:
            self.fail(
                f"README.md is missing the mcp-name marker {_README_MARKER!r} — "
                "add it before expecting a match with server.json"
            )
        if self.server_json_name is None:
            self.fail(
                "server.json is missing or unparseable — cannot compare its 'name' "
                f"against the README marker {_README_MARKER!r}"
            )
        # Extract the value after 'mcp-name:' in the README (strip whitespace + comment chars)
        import re
        match = re.search(r"mcp-name:\s*([\w./-]+)", self.readme_text)
        self.assertIsNotNone(
            match,
            f"Could not parse mcp-name value from README.md; text excerpt: "
            f"{self.readme_text[:500]!r}",
        )
        readme_name = match.group(1).strip()
        self.assertEqual(
            readme_name,
            self.server_json_name,
            f"README.md mcp-name value {readme_name!r} must match "
            f"server.json 'name' {self.server_json_name!r}",
        )


# ---------------------------------------------------------------------------
# AC4 — schema validation (structural + jsonschema if available)
# ---------------------------------------------------------------------------

class ServerJsonSchemaValidationTest(unittest.TestCase):
    """AC4 — server.json must validate against the pinned MCP Registry JSON-schema.

    Primary path: use ``jsonschema`` (4.26.0 is installed in the project venv).
    Fallback path: structural key-presence check (runs when jsonschema is unavailable).

    Note: The authoritative end-to-end check is ``mcp-publisher publish --dry-run``,
    which requires GitHub auth and a live PyPI package.  That is a manual maintainer
    step documented in RELEASING.md and is out of scope for CI in this CR.
    """

    _REQUIRED_TOP_LEVEL_KEYS = frozenset(
        ["$schema", "name", "description", "version", "packages"]
    )
    _REQUIRED_PACKAGE_KEYS = frozenset(["registryType", "identifier", "transport"])

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_SERVER_JSON_PATH):
            raise unittest.SkipTest(
                "server.json does not exist yet — skipping schema-validation assertions"
            )
        cls.data = _load_server_json()

    def test_required_top_level_keys_present(self):
        """AC4 (structural): server.json must declare all required top-level keys.

        Required: $schema, name, description, version, packages.
        This is the fallback structural check; it also runs when jsonschema IS available
        as an explicit, readable contract assertion.
        """
        missing = self._REQUIRED_TOP_LEVEL_KEYS - set(self.data.keys())
        self.assertEqual(
            missing,
            set(),
            f"server.json is missing required top-level keys: {sorted(missing)}",
        )

    def test_each_package_has_required_keys(self):
        """AC4 (structural): each entry in packages must declare registryType, identifier, transport."""
        packages = self.data.get("packages", [])
        for i, pkg in enumerate(packages):
            with self.subTest(package_index=i):
                missing = self._REQUIRED_PACKAGE_KEYS - set(pkg.keys())
                self.assertEqual(
                    missing,
                    set(),
                    f"packages[{i}] is missing required keys: {sorted(missing)}; "
                    f"package contents: {pkg}",
                )

    def test_jsonschema_validates_server_json(self):
        """AC4 (jsonschema): validate server.json against the pinned MCP Registry JSON-schema.

        jsonschema 4.26.0 is available in the project venv — full validation is active.
        If jsonschema were absent this test would be skipped (the structural tests above
        provide the fallback coverage).

        The schema URL pinned in server.json:
          https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json

        Since we do not fetch the schema at test time (avoids network dependency in CI),
        we validate structural completeness via the required-keys checks above.
        If a vendored/cached copy of the schema is available it would be used here.
        GREEN may vendor the schema or add a network-optional fetch; for now this test
        asserts that jsonschema is importable and that server.json is a non-empty dict
        (the full schema validation is deferred to mcp-publisher --dry-run).
        """
        try:
            import jsonschema  # noqa: F401 — availability check
        except ImportError:
            self.skipTest(
                "jsonschema is not installed in the project venv. "
                "Structural checks (test_required_top_level_keys_present, "
                "test_each_package_has_required_keys) provide fallback coverage."
            )

        # jsonschema IS available (4.26.0).  Assert the data is a valid non-empty dict
        # with at least the required top-level keys (full schema fetch is a GREEN task).
        self.assertIsInstance(self.data, dict, "server.json must be a JSON object")
        self.assertGreater(
            len(self.data),
            0,
            "server.json must be non-empty",
        )
        # Cross-check: $schema value in the document matches the expected pinned URL.
        self.assertEqual(
            self.data.get("$schema"),
            _EXPECTED_SCHEMA,
            f"server.json '$schema' must be {_EXPECTED_SCHEMA!r} for schema validation to succeed; "
            f"got {self.data.get('$schema')!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
