# CR-SAN-017 — DB schema migration engine + first real migration (proving case)

**Status:** PENDING
**Priority:** High (unblocks every future schema change; the `message.status` retirement is blocked on it)
**Depends on:** CR-SAN-008 (the `pyproject.toml` extras mechanism), Python core (`sandesh_db`/`cli`)
**Labels:** wave-5, migration, python, cli
**Wave:** Wave 5 (schema evolution)
**Design reference:** docs/research/PRD-db-migration.md (D1–D8) — yoyo runner, `[migrate]` extra, Option B
(yoyo steps = source of truth; JSON = derived `--check` snapshot), CLI/installer only (no MCP/Pi).

## Context

Today the per-project SQLite schema is created with `CREATE TABLE IF NOT EXISTS` (`sandesh_db._SCHEMA`).
That covers **new** installs but does nothing for **existing** `projects/<id>/sandesh.db` stores when the
schema changes. This CR builds the versioned, tracked, idempotent, reversible migration subsystem the PRD
specifies — and **proves it end-to-end** by carrying out the first real schema change (the deferred
CR-SAN-012 work: dropping `message.status`/the disposition machine, which needs the SQLite 12-step table
rebuild — there is no plain `DROP COLUMN` before SQLite 3.35 and we don't want to assume it).

Per the PRD: the **runner is `yoyo-migrations`** (we do NOT hand-roll apply/track/rollback); the ordered
steps in `migrations/` ARE the canonical schema history; a derived `current-schema.json` snapshot is used
ONLY by `--check`; versioning is yoyo's applied-list (no `_schema_meta`/`PRAGMA user_version`); the whole
surface is the **`[migrate]` extra** and is **CLI/installer only — never MCP/Pi**.

> ⚠️ **Verify the real yoyo API at gap-analysis** (PRD §7): the `sqlite:///<path>` connection form,
> whether yoyo wraps each step in a transaction, the `read_migrations`/`apply`/`rollback`/mark-applied
> API, and how it records applied steps. Do NOT assume from memory — read yoyo's actual docs/source
> (the project's stdlib-purity rule makes this the one deliberate new dependency).

## Scope

### §S1 — `[migrate]` optional extra + friendly-absent guard
- Add a **`[migrate]`** extra to `pyproject.toml` pulling `yoyo-migrations` **and** `jsonschema`
  (pin or lower-bound per gap-analysis once the real versions are checked), mirroring the `[mcp]`
  isolation (CR-SAN-008 §S6).
- The **core stays pure-stdlib**: `sandesh_db` / `cli` (its base) / `notify` / `mcp_server` MUST NOT
  import `yoyo` or `jsonschema` at module load. `sandesh/migrate.py` **lazy-imports** them inside the
  command path.
- When the extra is absent, `sandesh migrate …` prints a friendly "install the `[migrate]` extra
  (`pip install 'sandesh-relay[migrate]'`)" message and exits **non-zero** (the
  `sandesh-mcp`-without-`[mcp]` pattern).

### §S2 — `sandesh/migrate.py` (the engine) + yoyo wiring
- New module `sandesh/migrate.py` holding all yoyo/jsonschema use. It resolves a project store's DB
  path via the existing `sandesh_db.store_dir(project_id)` and opens it with yoyo as `sqlite:///<abs-path>`.
- yoyo `read_migrations` over `sandesh/migrations/`; `apply` (pending), `rollback` (one step).
- **`migrations/0001-baseline`** reproduces today's `_SCHEMA` exactly (the four tables + indexes/PKs as
  currently created) so a brand-new store provisioned through migrations is byte-for-table identical to
  one created by `sandesh_db.setup`.
- **Baseline adoption glue** (PRD §7): a store that predates yoyo already has the four tables and an
  empty/absent `_yoyo_migration`. `migrate` MUST detect this (tables exist + baseline not recorded) and
  **record `0001-baseline` as already-applied** rather than re-running it (which would fail on existing
  tables). New empty stores run `0001` normally.

### §S3 — derived JSON snapshot + meta-schema (for `--check`)
- `sandesh/schema/current-schema.json` — a **derived** snapshot of the expected shape (tables → columns
  → type/notnull/pk/default), representing the schema **after the latest migration**.
- `sandesh/schema/schema.meta.json` — a JSON-Schema meta-schema that `jsonschema` validates
  `current-schema.json` against (guards the snapshot's own format).
- It is **NOT** the source of truth and **NOT** a generator — its only consumer is `--check` (§S5) and
  it is regenerable from a live DB via `--dump-schema` (§S6).

### §S4 — `sandesh migrate` CLI subcommand (per-project + `--all`)
Wire `migrate` into `cli.py` (heavy imports stay lazy in `migrate.py`). Subcommands/flags:
- `sandesh migrate --project <id>` — apply pending migrations to that store (idempotent; yoyo skips
  applied steps).
- `--all` — iterate **every** `projects/<id>/sandesh.db` under the data home and apply to each.
- `--status` — report applied vs pending (from yoyo's `_yoyo_migration`), no writes.
- `--rollback` — roll back the most recent applied step (one step), per project.
- `--check` — §S5 (no writes).
- `--dump-schema` / `--diff` — §S6 (read-only authoring aids).
- `--project` accepted before or after `migrate` (the existing SUPPRESS pattern, CLAUDE.md gotcha).

### §S5 — `migrate --check` (pending + drift, no writes)
Two checks, no writes:
1. **pending** — are there unapplied yoyo migrations for the store(s)? **Pending ⇒ exit non-zero**
   (installer/CI gate).
2. **drift** — does the live DB shape (`PRAGMA table_info` per table) match `current-schema.json`?
   Report mismatches. (Strictness — drift = warning vs error — resolved at gap-analysis; PRD §7 lean:
   pending = error, drift = warning.)

### §S6 — read-only authoring aids (`--dump-schema`, `--diff`)
- `--dump-schema` — emit the live DB's shape (per the §S3 format) as JSON to stdout (regenerates the
  snapshot for a developer to commit). Read-only.
- `--diff <old-snapshot> [--json]` — structured added/removed/changed tables+columns between a stored
  snapshot file and the freshly-dumped current state; `--json` emits machine-readable output a
  developing agent (shelling to the CLI) uses to hand-write the next yoyo step. Read-only.
- **No MCP/Pi wrapper** (D8) — these are plain CLI flags only.

### §S7 — first real migration: drop `message.status` (the proving case; folds CR-SAN-012)
- **`migrations/0002-drop-message-status`** — a **hand-written** SQLite **12-step table rebuild** that
  drops `message.status` (and the disposition machinery columns the rebuild removes), inside yoyo's
  transaction: create the new `message` table without the column → `INSERT … SELECT` the carried columns
  → drop old → rename → recreate dependent indexes. Includes a yoyo **rollback** step.
- Update `sandesh_db._SCHEMA` (and any `message.status`-touching core code — `set_status`, the dormant
  CLI `actioned`, `reply --resolves`, status constants) to match the post-migration shape, so a NEW
  store (via baseline+0002 or via the updated `_SCHEMA`) and a MIGRATED store converge.
- Update `current-schema.json` to the post-`0002` shape.
- This exercises the engine end-to-end (apply on a populated store, `--check` clean after, `--rollback`
  restores) — the proof the engine works.

## Acceptance criteria

- [ ] **AC1 — extra isolation.** Importing `sandesh.sandesh_db`, `sandesh.cli` (base parse), `sandesh.notify`,
      and `sandesh.mcp_server` does **not** import `yoyo` or `jsonschema` (asserted, e.g. via
      `sys.modules` after a fresh import, or an import-linter style check). `sandesh/migrate.py` imports
      them only inside function bodies.
- [ ] **AC2 — friendly absence.** With the `[migrate]` deps uninstalled, `sandesh migrate --status
      --project X` exits **non-zero** and prints a message naming the `[migrate]` extra (asserted by
      simulating ImportError / a subprocess without the deps).
- [ ] **AC3 — baseline = current schema.** A store provisioned by applying migrations from empty
      (`0001`) has the **same tables/columns** as one created by `sandesh_db.setup` *before* `0002`
      (asserted by comparing `PRAGMA table_info` for all four tables) — i.e. `0001-baseline` faithfully
      reproduces `_SCHEMA`.
- [ ] **AC4 — baseline adoption.** Given a pre-yoyo store (four tables present, no `_yoyo_migration`),
      `migrate --project X` records `0001-baseline` as **applied without re-running it** (no "table
      already exists" error) and then applies `0002` (asserted: a store seeded via raw `sandesh_db.setup`
      migrates cleanly).
- [ ] **AC5 — apply + status + idempotency.** `migrate --project X` applies pending steps; a second run
      is a no-op; `migrate --status` reports the applied set (incl. `0001`,`0002`) and **0 pending**
      (asserted from yoyo's applied list).
- [ ] **AC6 — rollback.** After applying `0002`, `migrate --rollback --project X` restores the
      `message.status` column (asserted via `PRAGMA table_info` showing the column returns), and `--status`
      shows `0002` pending again.
- [ ] **AC7 — check.** `migrate --check` on a store with pending migrations exits **non-zero** and lists
      them; on a fully-migrated store whose shape matches `current-schema.json` it exits **zero**
      (asserted both directions). Drift handling per the gap-analysis decision is asserted to its chosen
      strictness.
- [ ] **AC8 — snapshot validity.** `current-schema.json` validates against `schema.meta.json` via
      `jsonschema` (asserted); and `--dump-schema` on a fully-migrated store produces JSON that **equals**
      the committed `current-schema.json` (modulo ordering) (asserted).
- [ ] **AC9 — diff.** `migrate --diff <old> --json` between the pre-`0002` snapshot and the current
      dump reports `message.status` as a **removed column** on `message` (asserted on the structured
      output).
- [ ] **AC10 — `--all`.** With ≥2 project stores present, `migrate --all` applies to **each** and
      `--status --all` reports both fully applied (asserted).
- [ ] **AC11 — status drop end-to-end.** After `0002`, a NEW store (`sandesh_db.setup`) and a MIGRATED
      store both **lack** `message.status`; sending/replying/fetching still work (the messaging suite
      stays green); no core code references the removed column (`grep` clean / tests green).
- [ ] **AC12 — core stdlib purity preserved.** The non-migrate test suite still runs under **system
      `python3`** with no third-party deps (`python3 tests/test_sandesh.py` green); the migration tests
      run under the `[migrate]`-provisioned interpreter.

## Gap-analysis findings
_To be completed by `/gap-analysis CR-SAN-017` before the feature branch — MUST resolve the PRD §7 open
questions against the real yoyo API (connection form, per-step transactions, mark-as-applied, applied-state
recording), confirm the `_SCHEMA` baseline contents, and decide `--check` drift strictness._

## Estimated size
Large — the engine (`migrate.py` + yoyo wiring + baseline + adoption glue), the snapshot + meta-schema,
the CLI surface (6 flags), AND the first real `0002` rebuild + core `message.status` removal. Expect
multiple RED→GREEN cycles (extra/guard → engine+baseline → CLI+check → dump/diff → `0002`+core removal).

## Risks / open questions
- **yoyo real API** (PRD §7) — must be verified at gap-analysis; everything downstream depends on it.
- **Baseline adoption** — the one bit of bespoke glue; get the "tables exist but unrecorded" detection
  right or existing stores break.
- **12-step rebuild correctness** — foreign-key/index recreation and row-carry on the `message` rebuild;
  populated-store test is mandatory.
- **`--check` strictness** — pending=error is firm; drift=warning vs error to be decided.

## Non-goals
- Any MCP/Pi migration tool (execution OR dump/diff) — CLI/installer only (PRD D8).
- A declarative diff→DDL generator (steps are hand-written; PRD D6).
- Multi-DB support; an ORM; replacing `sandesh_db`; auto-running migrations from the hot path.
- Installer/CI wiring of `migrate --all` / `--check` — that is **CR-SAN-018**.
