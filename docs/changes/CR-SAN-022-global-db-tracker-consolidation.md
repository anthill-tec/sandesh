# CR-SAN-022 — Global DB, project tracker & store consolidation

**Status:** PENDING
**Priority:** High (the Wave 6 foundation — everything else in the wave builds on the single DB)
**Depends on:** CR-SAN-017/018 (migration engine + installer hook)
**Labels:** wave-6, global-store, schema, migration, installer
**Wave:** Wave 6 (global store)
**Design reference:** docs/research/PRD-global-store.md (D1, D2, D10, O1, O2; §4 layout; §5 CR breakdown)

## Context

Sandesh today keeps one SQLite DB per project; cross-project communication is structurally impossible and
the migration engine maintains N store lineages. PRD-global-store D1 moves ALL tables into **one global
`<data_home>/sandesh/sandesh.db`** (WAL), keeping per-project folders **only for message bodies**, and D2
adds a global **`project` tracker** table. D10 consolidates existing legacy stores into the global DB via
one-time installer glue. This CR delivers exactly that foundation — **no behavioural change to messaging
semantics yet** (cross-project send stays blocked until CR-SAN-023; the validation code is untouched here
except where the single-DB plumbing requires it).

## Scope

### §S1 — schema: the `project` tracker migration
- New yoyo step `0003-project-tracker.sql` (+ `.rollback.sql`) creating:
  ```sql
  CREATE TABLE IF NOT EXISTS project (
    project_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','archived','tombstoned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at   TEXT,
    tombstoned_at TEXT
  );
  ```
- `sandesh/schema/current-schema.json` regenerated to include `project` (snapshot-sync CI gate stays green).
- `_SCHEMA` in `sandesh_db.py` gains the same `CREATE TABLE IF NOT EXISTS project (…)` (fresh-DB parity
  with the migration chain, as for the existing four tables).

### §S2 — `sandesh_db` single-DB plumbing
- `connect()` signature unchanged BUT the canonical DB path becomes `<root_dir()>/sandesh.db`; it sets
  `PRAGMA journal_mode=WAL`. A new `db_path()` helper returns that path. `store_dir(project_id)` survives
  **only** as the body-folder helper (`projects/<id>/`).
- `setup(project_id)` → **enrolls** in the tracker (INSERT `active` if absent; no-op if already `active`;
  **refuses a `tombstoned` id** with the exact error `project id retired (tombstoned) — choose a new id`
  per O1; behaviour for `archived` id = no-op, state untouched) + creates `projects/<id>/messages/`.
- `list_projects()` → reads the `project` table (returns active+archived; tombstoned listed only via an
  `include_tombstoned=True` flag), no longer a filesystem scan.
- New `project_state(con, project_id)` → `'active' | 'archived' | 'tombstoned' | None`.
- `cli.py` `_ctx`: one `connect()`; `--project` still resolves the *context* project (defaults unchanged).

### §S3 — one-time consolidation glue + installer hook (D10)
- `sandesh_db.consolidate(data_home)` (or a `migrate.py` sibling — decide at gap-analysis): detect legacy
  `projects/<id>/sandesh.db` files; for each: import `address` rows verbatim (PK collisions impossible —
  addresses embed the project), import `message` rows with **remapped ids** (fix `in_reply_to` and
  `message_recipient.message_id`; `body_path` is absolute so files do not move), enroll the project
  `active`, then rename the legacy DB → `sandesh.db.pre-global`. Idempotent: `.pre-global` files are
  skipped on re-run. `notifier` rows are NOT imported (watchers re-acquire on next launch).
- `install.sh`: after `migrate --all`, run the consolidation (same venv-probe guard pattern as CR-SAN-018).
- `migrate --all` collapses to the single global target (flag kept as an alias of plain apply).

### §S4 — docs
- `CLAUDE.md`: rewrite the store-layout section + locked semantics #2 (per-project stores → global DB,
  re-opened with reason, cite the PRD); note the `.pre-global` backups.

## Acceptance criteria

- [ ] **AC1 — `project` table shape.** After `migrate --all` on a fresh data home, the global DB contains
      table `project` with exactly the columns `project_id` (TEXT PK), `state` (TEXT NOT NULL, CHECK in
      `('active','archived','tombstoned')`, default `'active'`), `created_at`, `archived_at`,
      `tombstoned_at` (asserted via `PRAGMA table_info` + a CHECK-violation INSERT failing).
- [ ] **AC2 — single DB + WAL.** `connect()` opens `<data_home>/sandesh/sandesh.db` and
      `PRAGMA journal_mode` returns `wal`. Two different `project_id`s' operations land in the same DB file.
- [ ] **AC3 — `setup` enrolls + O1 refusal.** `setup("P1")` inserts `project_id='P1', state='active'` and
      creates `projects/P1/messages/`; re-`setup` is a no-op; `setup` of a row with `state='tombstoned'`
      raises with message containing `retired (tombstoned)` and leaves the row unchanged.
- [ ] **AC4 — `list_projects` from the tracker.** Returns enrolled active+archived ids sorted; a
      tombstoned id appears only with `include_tombstoned=True`; no filesystem scan (a stray
      `projects/X/` dir without a tracker row is NOT listed).
- [ ] **AC5 — consolidation correctness.** Fixture: two legacy stores with **colliding message ids** and an
      internal reply chain each. After `consolidate`: all messages present in the global DB with unique ids;
      every `in_reply_to` and `message_recipient.message_id` points at the correct remapped parent; every
      `body_path` opens (files unmoved); both projects enrolled `active`; both legacy files renamed
      `sandesh.db.pre-global`; a second `consolidate` run is a no-op.
- [ ] **AC6 — engine single-target.** `migrate --all` and `migrate` (bare apply) act on the one global DB;
      `migrate --status` reports its applied list; the snapshot-sync check (`--dump-schema` == committed
      `current-schema.json`) passes with the new `project` table included.
- [ ] **AC7 — suite green on the new layout.** The full existing native suite passes against the global-DB
      plumbing (per-project test fixtures adapted; messaging behaviour unchanged in this CR).

## Estimated size
Large — schema + plumbing + the consolidation glue (the id-remap is the risky part) + broad test-fixture
adaptation. The largest CR of the wave; everything downstream depends on it.

## Risks / open questions
- **O2 (PRD):** `sqlite_sequence`/AUTOINCREMENT behaviour after bulk insert with explicit ids, and WAL
  rollout on pre-existing files — verify real SQLite behaviour at gap-analysis, don't assume.
- Consolidation ordering vs `migrate --all` in the installer (global DB must be at latest schema first).
- Test-fixture blast radius: 9 of 17 test files assume the per-project layout.

## Non-goals
- Cross-project send semantics / tracker-state enforcement on verbs (CR-SAN-023).
- The lifecycle verbs archive/unarchive/tombstone (CR-SAN-024).
- Any MCP surface change (CR-SAN-025).
