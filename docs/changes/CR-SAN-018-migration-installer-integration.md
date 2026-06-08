# CR-SAN-018 — Migration installer & CI integration

**Status:** PENDING
**Priority:** Medium (makes the engine actually run on update; without it migrations exist but never fire)
**Depends on:** CR-SAN-017 (the migration engine + `sandesh migrate` CLI)
**Labels:** wave-5, migration, install, ci, docs
**Wave:** Wave 5 (schema evolution)
**Design reference:** docs/research/PRD-db-migration.md (D7 installer hook, D8 boundary, §5 CR breakdown)

## Context

CR-SAN-017 builds the migration engine and `sandesh migrate` CLI but does not wire it into the install
/ update path. This CR makes existing stores upgrade automatically on update and adds a `--check` gate so
a release/CI never ships with a store schema out of step with the committed migrations. Migration stays
**operator/installer-driven — never the message hot path, never MCP/Pi** (PRD D8).

## Scope

### §S1 — installer runs `migrate --all`
- `install.sh` (and/or the documented post-install step) invokes `sandesh migrate --all` after copying
  the code, so every existing `projects/<id>/sandesh.db` is brought up to the latest schema on update.
- The `[migrate]` extra must be available to the install-time interpreter for this to run; if it is
  **absent**, the installer **must not hard-fail the install** — it prints a clear notice that migrations
  were skipped and how to run them later (`pip install 'sandesh-relay[migrate]' && sandesh migrate --all`).
  (A fresh install with no existing stores has nothing to migrate.)
- Idempotent: re-running the installer re-runs `migrate --all` harmlessly (yoyo skips applied steps).

### §S2 — `--check` as a release/CI gate
- A CI/release step runs `sandesh migrate --check` (against a representative/seeded store) so a release
  with **pending** migrations relative to the committed `migrations/` fails the gate (pending ⇒ non-zero,
  per CR-017 AC7). Wire it into the existing CI workflow (or document it as a release-checklist step in
  `RELEASING.md` if a CI store fixture is out of scope — decide at gap-analysis).

### §S3 — docs
- `README.md` — a short "Schema migrations" section: what `sandesh migrate` is, that updates auto-migrate
  via the installer, the `[migrate]` extra, and the `--check`/`--status`/`--rollback` flags.
- `RELEASING.md` — the maintainer steps: ensure `current-schema.json` + `migrations/` are in sync before
  tagging; the `--check` gate; that the installer migrates on update.
- `CLAUDE.md` — update the schema/"Gotchas" notes to point at the migration subsystem as the way schema
  changes ship (replacing the "CREATE TABLE IF NOT EXISTS only covers new installs" caveat).

## Acceptance criteria

- [ ] **AC1 — installer migrates existing stores.** After `install.sh` on a data home containing a
      pre-migration store, that store is at the latest schema (asserted: `migrate --status` shows 0
      pending / the post-`0002` shape present). (Tested via a temp `$XDG_DATA_HOME`.)
- [ ] **AC2 — installer tolerates missing extra.** With the `[migrate]` deps absent, `install.sh`
      **completes successfully** (non-zero from `migrate` does not abort the install) and prints the
      "migrations skipped — install `[migrate]`" notice (asserted by running the installer path without
      the deps and checking exit 0 + the notice).
- [ ] **AC3 — fresh install no-op.** On an empty data home (no project stores), the installer's migrate
      step is a clean no-op (no error; asserted).
- [ ] **AC4 — `--check` gate wired.** The CI workflow (or documented release step) runs `migrate --check`
      and fails on pending migrations (asserted by the workflow file / a test that pending ⇒ non-zero is
      consumed as a gate).
- [ ] **AC5 — docs present.** `README.md`, `RELEASING.md`, and `CLAUDE.md` document the migration
      subsystem, the `[migrate]` extra, the installer auto-migrate, and the `--check` gate (asserted by
      content checks / grep markers).
- [ ] **AC6 — boundary intact.** No migration call is added to the message hot path, the messaging MCP
      server, or the Pi extension (asserted: `sandesh_db`/`mcp_server`/`integrations/pi` reference no
      `migrate` apply; the only new caller is the installer/CLI).

## Gap-analysis findings
_To be completed by `/gap-analysis CR-SAN-018` before the feature branch — confirm the installer's
interpreter/venv has access to the `[migrate]` extra (or document the manual fallback), and decide whether
the `--check` gate runs in CI against a fixture store or is a `RELEASING.md` checklist step._

## Estimated size
Small–medium — `install.sh` hook + a CI/release `--check` step + README/RELEASING/CLAUDE doc updates.
The substance is the install-time robustness (missing-extra tolerance) and the CI gate decision.

## Risks / open questions
- **Install-time deps** — the core install is stdlib-only; `migrate --all` needs the `[migrate]` extra.
  Resolve whether the installer provisions it (own venv, like `[mcp]`) or degrades gracefully (§S1/AC2).
- **CI store fixture** — `--check` needs a store to check against; decide CI fixture vs release-checklist.

## Non-goals
- The engine, CLI, migrations, or snapshot (all CR-SAN-017).
- Any MCP/Pi exposure of migration (PRD D8).
- Auto-migrating from the runtime/hot path (installer/operator-driven only).
