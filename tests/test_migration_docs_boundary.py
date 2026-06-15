"""test_migration_docs_boundary.py — CR-SAN-018 C3 RED tests.

## AC5 — docs present (FAILS now — content not yet written)

Asserts that README.md, RELEASING.md, and CLAUDE.md each contain the
required documentation tokens for the migration subsystem.

## AC6 — boundary intact (PASSES now — regression guard)

Asserts that the message hot path (sandesh_db.py), MCP server
(mcp_server.py), the notify watcher (notify.py), and the Pi extension
(integrations/pi/src/*.ts) contain NO migration-engine calls:
no `import.*migrate`, no `migrate(`, no `yoyo` reference.

Run targeted:
  python3 -m unittest tests.test_migration_docs_boundary -v
  python3 ~/.claude/scripts/python-crucible.py test \\
      --tests tests.test_migration_docs_boundary \\
      --agent CR-SAN-018-3-RED
"""

import ast
import os
import re
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

_README = os.path.join(_REPO_ROOT, "README.md")
_RELEASING = os.path.join(_REPO_ROOT, "RELEASING.md")
_CLAUDE_MD = os.path.join(_REPO_ROOT, "CLAUDE.md")

_SANDESH_DB = os.path.join(_REPO_ROOT, "sandesh", "sandesh_db.py")
_MCP_SERVER = os.path.join(_REPO_ROOT, "sandesh", "mcp_server.py")
_NOTIFY = os.path.join(_REPO_ROOT, "sandesh", "notify.py")
_PI_SRC_DIR = os.path.join(_REPO_ROOT, "integrations", "pi", "src")


def _read(path: str) -> str:
    """Read a file as text; return '' if it doesn't exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _pi_ts_sources() -> list[tuple[str, str]]:
    """Return [(filename, contents), ...] for all *.ts files in the Pi src dir."""
    results = []
    if not os.path.isdir(_PI_SRC_DIR):
        return results
    for name in os.listdir(_PI_SRC_DIR):
        if name.endswith(".ts"):
            path = os.path.join(_PI_SRC_DIR, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    results.append((name, fh.read()))
            except OSError:
                pass
    return results


# ---------------------------------------------------------------------------
# AC5 — README.md migration section: REMOVED (CR-SAN-034)
# The README no longer documents the migration subsystem — an owner decision to
# trim release-internal detail out of the README. The authoritative migration
# docs now live in RELEASING.md and CLAUDE.md (asserted below); the migration
# *boundary* guards (AC6) remain fully in force.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC5 — RELEASING.md: migration release steps
# ---------------------------------------------------------------------------


class ReleasingMigrationStepsTest(unittest.TestCase):
    """AC5: RELEASING.md must document the maintainer migration steps for releases."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_RELEASING)

    def test_releasing_mentions_current_schema_json(self):
        """RELEASING.md must mention `current-schema.json` as part of release prep.

        RED: RELEASING.md has no migration content yet.
        """
        self.assertTrue(
            os.path.isfile(_RELEASING),
            f"RELEASING.md not found at {_RELEASING}",
        )
        self.assertIn(
            "current-schema.json",
            self.text,
            "RELEASING.md must contain 'current-schema.json' to document that "
            "maintainers must ensure the snapshot is in sync before tagging. "
            "GREEN must add the migration release steps.",
        )

    def test_releasing_mentions_migrations_directory(self):
        """RELEASING.md must mention the `migrations/` directory or `migrate`.

        RED: RELEASING.md has no migration content yet.
        """
        has_token = (
            "migrations/" in self.text
            or "migrate" in self.text
        )
        self.assertTrue(
            has_token,
            "RELEASING.md must contain 'migrations/' or 'migrate' to document "
            "that the migrations directory must be in sync with the snapshot. "
            "GREEN must add the migration release steps.",
        )

    def test_releasing_mentions_snapshot_sync_gate(self):
        """RELEASING.md must mention the snapshot-sync CI gate.

        RED: RELEASING.md has no migration content yet.
        Token check: 'snapshot' or 'sync' near migration context.
        Note: 'gate' alone is too broad (existing human-gate text would match).
        """
        has_token = (
            "snapshot" in self.text.lower()
            or bool(re.search(r"\bsync\b", self.text, re.IGNORECASE))
        )
        self.assertTrue(
            has_token,
            "RELEASING.md must mention the snapshot-sync gate "
            "('snapshot' or 'sync') so maintainers know the CI gate "
            "checks schema consistency before a release. "
            "GREEN must add the migration release steps.",
        )

    def test_releasing_mentions_installer_migrates_on_update(self):
        """RELEASING.md must mention that the installer migrates existing stores on update.

        RED: RELEASING.md has no migration content yet.
        """
        has_token = (
            "migrate" in self.text
            or "migration" in self.text.lower()
        )
        self.assertTrue(
            has_token,
            "RELEASING.md must mention 'migrate' or 'migration' — documenting that "
            "the installer auto-migrates existing stores on update. "
            "GREEN must add the migration release steps.",
        )


# ---------------------------------------------------------------------------
# AC5 — CLAUDE.md: additive schema-migration note
# ---------------------------------------------------------------------------


class ClaudeMdSchemaMigrationNoteTest(unittest.TestCase):
    """AC5: CLAUDE.md must have an additive note about the migration subsystem.

    DRIFT-B: the old 'CREATE TABLE IF NOT EXISTS only covers new installs' caveat
    is NOT present in CLAUDE.md, so this is a pure additive check — we assert
    new tokens are PRESENT; we do NOT assert removal of any existing text.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_CLAUDE_MD)

    def test_claude_md_mentions_migration_subsystem_command(self):
        """CLAUDE.md must mention `sandesh migrate` as the way schema changes ship.

        RED: CLAUDE.md currently references 'migrate' only in passing
        (migrate off the old relay, 'migrated stores') — it does NOT describe
        the migration subsystem or the `sandesh migrate` CLI command.
        GREEN must add an additive note in the schema/Gotchas area.
        """
        self.assertTrue(
            os.path.isfile(_CLAUDE_MD),
            f"CLAUDE.md not found at {_CLAUDE_MD}",
        )
        # We need 'sandesh migrate' (the subsystem command) to appear.
        # The existing mention "migrate off the old relay" at line 57 is NOT
        # a reference to the migration subsystem — it means "switch away from".
        # So we check that `sandesh migrate` (with the prefix) appears.
        self.assertIn(
            "sandesh migrate",
            self.text,
            "CLAUDE.md must contain 'sandesh migrate' to document the migration CLI "
            "command. The existing mention of 'migrate' at CLAUDE.md:57 refers to "
            "switching away from the old relay, not the migration subsystem. "
            "GREEN must add an additive note about the migration subsystem.",
        )

    def test_claude_md_mentions_migrate_extra(self):
        """CLAUDE.md must mention the `[migrate]` extra.

        RED: CLAUDE.md does not currently mention '[migrate]'.
        GREEN must add the additive note.
        """
        self.assertIn(
            "[migrate]",
            self.text,
            "CLAUDE.md must contain '[migrate]' to document the optional extra "
            "that enables the migration subsystem. "
            "GREEN must add an additive note in the schema/Gotchas area.",
        )

    def test_claude_md_mentions_installer_auto_migrates(self):
        """CLAUDE.md must state that the installer auto-migrates existing stores.

        RED: CLAUDE.md does not document installer auto-migration.
        GREEN must add the additive note.
        Token check: 'auto-migrat' OR 'migrates existing' OR 'install' + 'migrate'
        appearing within 200 chars of each other (co-occurrence check).
        The bare word 'installer' is insufficient — it already appears in the
        Wave-1 roadmap line with no migration context.
        """
        has_token = (
            bool(re.search(r"auto.?migrat", self.text, re.IGNORECASE))
            or "migrates existing" in self.text
            or bool(re.search(r"install.*migrat|migrat.*install", self.text, re.IGNORECASE))
        )
        self.assertTrue(
            has_token,
            "CLAUDE.md must state that the installer auto-migrates existing stores "
            "on update (token: 'auto-migrate', 'migrates existing', or 'install' "
            "co-occurring with 'migrate'). "
            "GREEN must add this to the schema/Gotchas area.",
        )

    def test_claude_md_migration_note_is_additive_no_caveat_removed(self):
        """DRIFT-B guard: CLAUDE.md note is additive — the old caveat was never present.

        The CR spec says the edit is ADDITIVE because the old
        'CREATE TABLE IF NOT EXISTS only covers new installs' caveat is not
        in CLAUDE.md. Verify that key phrases from the Gotchas section that
        ARE present remain untouched — this test passes now and must stay GREEN.
        Specifically, the SQLite BOOLEAN and body-paths-are-absolute notes
        must still be in CLAUDE.md after GREEN's additive edit.
        """
        # These sentences are present in the current CLAUDE.md Gotchas section.
        self.assertIn(
            "SQLite has no real BOOLEAN",
            self.text,
            "CLAUDE.md Gotchas: 'SQLite has no real BOOLEAN' note must remain "
            "present after GREEN's additive migration note. "
            "GREEN must NOT remove or rewrite existing Gotchas content.",
        )
        self.assertIn(
            "Body paths are absolute",
            self.text,
            "CLAUDE.md Gotchas: 'Body paths are absolute' note must remain "
            "present after GREEN's additive migration note.",
        )


# ---------------------------------------------------------------------------
# AC6 — boundary intact: no migration calls in hot-path or Pi extension
# ---------------------------------------------------------------------------


class BoundaryGuardSandeshDbTest(unittest.TestCase):
    """Import-time boundary for sandesh_db.py (REVISED — PRD-provisioning-lifecycle §4.2,
    CR-SAN-036).

    sandesh_db.connect() now performs lazy auto-migrate, so sandesh_db.py MAY reference the
    migrate module / `_yoyo_migration` bookkeeping / yoyo LAZILY (inside function bodies). The
    boundary that still matters — and that this guard enforces — is that importing sandesh_db
    must NOT eagerly pull the migration engine: there must be NO MODULE-LEVEL import of
    sandesh.migrate, yoyo, or jsonschema (the import-time hot path stays stdlib-only; yoyo is
    imported only on the rare behind branch). Supersedes the old CR-SAN-018 'zero references'
    text check, which the PRD-mandated lazy path deliberately broke.
    """

    @classmethod
    def setUpClass(cls):
        cls.path = _SANDESH_DB
        with open(_SANDESH_DB, encoding="utf-8") as fh:
            cls.tree = ast.parse(fh.read())

    def _module_level_imports(self):
        """Names imported at MODULE scope only (top-level statements) — lazy in-function
        imports are excluded (and are the sanctioned mechanism)."""
        names = []
        for node in self.tree.body:  # module body == top level only
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names.append(mod)
                names += [f"{mod}.{a.name}" for a in node.names]
        return names

    def test_sandesh_db_no_module_level_migrate_import(self):
        """sandesh_db.py must not import the migrate module at MODULE level (lazy
        in-function import only)."""
        self.assertTrue(os.path.isfile(self.path), f"sandesh_db.py not found at {self.path}")
        offenders = [n for n in self._module_level_imports() if "migrate" in n]
        self.assertEqual(
            offenders, [],
            "sandesh_db.py must NOT import the migrate module at module level — the lazy "
            f"auto-migrate path imports it inside connect() only. Found: {offenders}",
        )

    def test_sandesh_db_no_module_level_yoyo_or_jsonschema(self):
        """sandesh_db.py must not import yoyo/jsonschema at MODULE level — importing
        sandesh_db must stay stdlib-only; yoyo is touched only on the behind branch."""
        offenders = [
            n for n in self._module_level_imports() if "yoyo" in n or "jsonschema" in n
        ]
        self.assertEqual(
            offenders, [],
            "sandesh_db.py must NOT import yoyo/jsonschema at module level (import-time hot "
            f"path stays stdlib-only). Found: {offenders}",
        )


class BoundaryGuardMcpServerTest(unittest.TestCase):
    """AC6: mcp_server.py must contain NO migration-engine references."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_MCP_SERVER)
        cls.path = _MCP_SERVER

    def test_mcp_server_no_migrate_import(self):
        """mcp_server.py must not import from sandesh.migrate / import migrate.

        Regression guard — PASSES now.
        """
        self.assertTrue(
            os.path.isfile(self.path),
            f"mcp_server.py not found at {self.path}",
        )
        self.assertFalse(
            bool(re.search(r"import.*migrate", self.text)),
            "mcp_server.py must NOT import migrate or sandesh.migrate. "
            f"Found: {[l for l in self.text.splitlines() if re.search(r'import.*migrate', l)]}",
        )

    def test_mcp_server_no_migrate_call(self):
        """mcp_server.py must not call migrate(...) directly.

        Regression guard — PASSES now.
        """
        self.assertFalse(
            bool(re.search(r"\bmigrate\(", self.text)),
            "mcp_server.py must NOT call migrate(...). "
            f"Found: {[l for l in self.text.splitlines() if 'migrate(' in l]}",
        )

    def test_mcp_server_no_yoyo_reference(self):
        """mcp_server.py must not reference yoyo.

        Regression guard — PASSES now.
        """
        self.assertFalse(
            "yoyo" in self.text,
            "mcp_server.py must NOT contain any 'yoyo' reference. "
            f"Found: {[l for l in self.text.splitlines() if 'yoyo' in l]}",
        )


class BoundaryGuardNotifyTest(unittest.TestCase):
    """AC6: notify.py must contain NO migration-engine references."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(_NOTIFY)
        cls.path = _NOTIFY

    def test_notify_no_migrate_import(self):
        """notify.py must not import from sandesh.migrate / import migrate.

        Regression guard — PASSES now.
        """
        self.assertTrue(
            os.path.isfile(self.path),
            f"notify.py not found at {self.path}",
        )
        self.assertFalse(
            bool(re.search(r"import.*migrate", self.text)),
            "notify.py must NOT import migrate or sandesh.migrate. "
            f"Found: {[l for l in self.text.splitlines() if re.search(r'import.*migrate', l)]}",
        )

    def test_notify_no_migrate_call(self):
        """notify.py must not call migrate(...) directly.

        Regression guard — PASSES now.
        """
        self.assertFalse(
            bool(re.search(r"\bmigrate\(", self.text)),
            "notify.py must NOT call migrate(...). "
            f"Found: {[l for l in self.text.splitlines() if 'migrate(' in l]}",
        )

    def test_notify_no_yoyo_reference(self):
        """notify.py must not reference yoyo.

        Regression guard — PASSES now.
        """
        self.assertFalse(
            "yoyo" in self.text,
            "notify.py must NOT contain any 'yoyo' reference. "
            f"Found: {[l for l in self.text.splitlines() if 'yoyo' in l]}",
        )


class BoundaryGuardPiExtensionTest(unittest.TestCase):
    """AC6: integrations/pi/src/*.ts must contain NO migration-engine references."""

    @classmethod
    def setUpClass(cls):
        cls.sources = _pi_ts_sources()

    def test_pi_src_dir_exists(self):
        """The Pi extension src directory must exist.

        Regression guard — PASSES now.
        """
        self.assertTrue(
            os.path.isdir(_PI_SRC_DIR),
            f"Pi extension src dir not found at {_PI_SRC_DIR}",
        )

    def test_pi_extension_no_migrate_import(self):
        """No Pi *.ts source may import migrate or sandesh.migrate.

        Regression guard — PASSES now.
        """
        violations = []
        for name, text in self.sources:
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"import.*migrate", line, re.IGNORECASE):
                    violations.append(f"  {name}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations,
            [],
            "Pi extension must NOT import migrate. Violations:\n"
            + "\n".join(violations),
        )

    def test_pi_extension_no_yoyo_reference(self):
        """No Pi *.ts source may reference yoyo.

        Regression guard — PASSES now.
        """
        violations = []
        for name, text in self.sources:
            for lineno, line in enumerate(text.splitlines(), 1):
                if "yoyo" in line.lower():
                    violations.append(f"  {name}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations,
            [],
            "Pi extension must NOT reference 'yoyo'. Violations:\n"
            + "\n".join(violations),
        )

    def test_pi_extension_no_migrate_call(self):
        """No Pi *.ts source may call migrate(...).

        Regression guard — PASSES now.
        """
        violations = []
        for name, text in self.sources:
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"\bmigrate\(", line):
                    violations.append(f"  {name}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations,
            [],
            "Pi extension must NOT call migrate(...). Violations:\n"
            + "\n".join(violations),
        )

    def test_pi_extension_no_migrate_all_string(self):
        """No Pi *.ts source may contain the literal 'migrate --all' string.

        Regression guard — PASSES now.
        """
        violations = []
        for name, text in self.sources:
            for lineno, line in enumerate(text.splitlines(), 1):
                if "migrate --all" in line:
                    violations.append(f"  {name}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations,
            [],
            "Pi extension must NOT contain 'migrate --all'. Violations:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
