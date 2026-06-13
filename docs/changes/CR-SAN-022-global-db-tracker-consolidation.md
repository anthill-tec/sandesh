# CR-SAN-022 — Global DB, project tracker & store consolidation

**Status:** COMPLETED (shipped 2026-06-11 on develop)
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

### §S1 — schema: the `project` tracker migration + `address.project`
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
  and adding **`address.project TEXT`** (backfilled from the address suffix:
  `UPDATE address SET project = substr(address, instr(address,' - ')+3)` semantics), so every
  project-scoped query (022 scoping, 023 grant, 024 purge) is an indexed exact match, not string
  parsing. `register` populates it going forward; the rollback drops both.
- `sandesh/schema/current-schema.json` regenerated to include `project` + the new column (snapshot-sync
  CI gate stays green).
- `_SCHEMA` in `sandesh_db.py` gains the same `CREATE TABLE IF NOT EXISTS project (…)` and the
  `address.project` column (fresh-DB parity with the migration chain — 0002/0003 are harmless-rerun on a
  fresh `_SCHEMA` DB).

### §S2 — `sandesh_db` single-DB plumbing + explicit project scoping
- `connect()` becomes **no-arg** (the old `store` param is wrong-shaped for a
  global DB): it opens `db_path()` = `<root_dir()>/sandesh.db` and sets `PRAGMA journal_mode=WAL`. The
  four production callers updated (`cli._ctx`, `mcp_server._ctx`, `notify.run`, `setup`) + tests.
  `store_dir(project_id)` survives **only** as the body-folder helper (`projects/<id>/`).
- **Explicit project scoping (the same-project rule was previously EMERGENT from store isolation and
  must become code, or consolidation silently opens cross-project messaging ungated):**
  - `send`/`reply`: every non-broadcast recipient must satisfy `address.project == sender's project`
    (exact error: `cross-project sending is not enabled (CR-SAN-023)` — replaced by the grant check
    in 023);
  - `all-tracks` expansion (`active_addresses`) filtered to the sender's project;
  - `addressbook(con, project)` filters to the context project;
  - `unregister`: the recipient must belong to the requester's project (a foreign Mainline may NOT
    remove another project's address).
- `setup(project_id)` → **enrolls** in the tracker (INSERT `active` if absent; no-op if already `active`;
  **refuses a `tombstoned` id** with the exact error `project id retired (tombstoned) — choose a new id`
  per O1; behaviour for `archived` id = no-op, state untouched) + creates `projects/<id>/messages/`.
- `list_projects()` → reads the `project` table (returns active+archived; tombstoned listed only via an
  `include_tombstoned=True` flag), no longer a filesystem scan.
- New `project_state(con, project_id)` → `'active' | 'archived' | 'tombstoned' | None`.
- `cli.py` `_ctx`: one `connect()`; `--project` still resolves the *context* project (defaults unchanged).

### §S3 — one-time consolidation glue + installer hook (D10)
- `sandesh_db.consolidate()`: detect legacy
  `projects/<id>/sandesh.db` files; for each: import `address` rows verbatim (PK collisions impossible —
  addresses embed the project), import `message` rows with **remapped ids** (fix `in_reply_to` and
  `message_recipient.message_id`; `body_path` is absolute so files do not move), enroll the project
  `active`, then rename the legacy DB → `sandesh.db.pre-global`. Idempotent: `.pre-global` files are
  skipped on re-run. `notifier` rows are NOT imported (watchers re-acquire on next launch). The
  imported `address` rows get their `project` column populated; `message_recipient.read_at`/`role`
  carried verbatim. (Explicit-id inserts auto-advance `sqlite_sequence`, so remapped ids are safe.)
- `install.sh`: after `migrate --all`, run the consolidation (same venv-probe guard pattern as CR-SAN-018).
- `migrate --all` collapses to the single global target (flag kept as an alias of plain apply).
- **`migrate` drops `--project` entirely (breaking):** the parser no longer accepts
  it on the `migrate` subcommand (`migrate.py` loses `_project_from_args`/`_db_path(project_id)` in
  favour of the global `db_path()`); `.github/workflows/publish-pypi.yml` (the snapshot-sync gate step,
  currently `--dump-schema --project ci`) and `tests/test_ci_migration_gate.py` updated accordingly.
- **`migrate.py` shape functions go dynamic:** the 4-table `_CORE_TABLES` constant remains
  ONLY as the baseline-adoption probe (`_BASELINE_TABLES`); `_live_shape`/`_drift`/`dump_schema`
  enumerate `sqlite_master` (excluding `sqlite_*` + `_yoyo_migration`) so the `project` table and all
  future tables are visible to `--dump-schema`/`--check`/the CI gate.

### §S4 — docs
- `CLAUDE.md`: rewrite the store-layout section + locked semantics #2 (per-project stores → global DB,
  re-opened with reason, cite the PRD); note the `.pre-global` backups.

## Acceptance criteria

- [ ] **AC1 — `project` table + `address.project` shape.** After `migrate --all` on a fresh data home,
      the global DB contains table `project` with exactly the columns `project_id` (TEXT PK), `state`
      (TEXT NOT NULL, CHECK in `('active','archived','tombstoned')`, default `'active'`), `created_at`,
      `archived_at`, `tombstoned_at` (asserted via `PRAGMA table_info` + a CHECK-violation INSERT
      failing), and `address` has a `project` column that `register` populates (asserted) and `0003`
      backfills on a legacy store (asserted in AC5's fixture).
- [ ] **AC2 — single DB + WAL.** `connect()` (no-arg) opens `<data_home>/sandesh/sandesh.db` and
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
- [ ] **AC6 — engine single-target, `--project` gone, dynamic shape.** `migrate --all` and `migrate`
      (bare apply) act on the one global DB; `sandesh migrate --project X …` is a CLI **error** (the flag
      no longer exists on the subcommand); `--dump-schema` output includes the `project` table +
      `address.project` (dynamic `sqlite_master` enumeration, `_yoyo_migration` excluded) and equals the
      regenerated committed `current-schema.json`; `publish-pypi.yml`'s gate step contains no
      `--project` on its migrate invocations.
- [ ] **AC7 — explicit scoping preserved.** With P1+P2 both enrolled and populated in the
      global DB: `send(from='Mainline - P1', to=['Mainline - P2'])` fails with exactly
      `cross-project sending is not enabled (CR-SAN-023)` and writes no rows; `all-tracks` from a P1
      sender creates recipient rows only for P1 addresses; `addressbook(con, 'P1')` lists only P1
      addresses; `unregister` of a P2 address by `Mainline - P1` raises `PermissionError`.
- [ ] **AC8 — suite green on the new layout.** The full existing native suite passes against the
      global-DB plumbing (per-project test fixtures adapted; messaging behaviour otherwise unchanged).

## Estimated size
Large — schema + plumbing + the consolidation glue (the id-remap is the risky part) + broad test-fixture
adaptation. The largest CR of the wave; everything downstream depends on it.

## Risks / open questions
- Consolidation ordering vs `migrate --all` in the installer (global DB must be at latest schema first).
- Test-fixture blast radius: 9 of 17 test files assume the per-project layout.

## Non-goals
- Cross-project send semantics behind the grant / tracker-state enforcement on verbs (CR-SAN-023 — this
  CR *blocks* cross-project explicitly; 023 relaxes that behind the D11 grant).
- The lifecycle verbs archive/unarchive/tombstone (CR-SAN-024).
- Any MCP surface change (CR-SAN-025).
