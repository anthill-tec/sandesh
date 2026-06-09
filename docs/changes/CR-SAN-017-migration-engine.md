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
- Add a **`[migrate]`** extra to `pyproject.toml` pulling `yoyo-migrations` **and** `jsonschema`,
  mirroring the `[mcp]` isolation (CR-SAN-008 §S6). **Floors (user-decided 2026-06-09):**
  `migrate = ["yoyo-migrations>=9,<10", "jsonschema>=4.26"]` — major-pin yoyo (mirrors `mcp>=1.27,<2`;
  guards a yoyo-10 API break), floor jsonschema at the installed modern-`referencing` line.
- The **core stays pure-stdlib**: `sandesh_db` / `cli` (its base) / `notify` / `mcp_server` MUST NOT
  import `yoyo` or `jsonschema` at module load. `sandesh/migrate.py` **lazy-imports** them inside the
  command path.
- When the extra is absent, `sandesh migrate …` prints a friendly "install the `[migrate]` extra
  (`pip install 'sandesh-relay[migrate]'`)" message and exits **non-zero** (the
  `sandesh-mcp`-without-`[mcp]` pattern).
- **Package the migration data dirs (gap-analysis DRIFT-2 — required).** hatchling does NOT auto-bundle
  non-`.py` files under the package (the existing `pyproject.toml` `force-include` for
  `sandesh/data/usage-scenarios.md` proves it). Add `force-include` (or an equivalent wheel-include rule)
  for **`sandesh/migrations/`** (the `.sql`/`.py` steps) and **`sandesh/schema/`** (`current-schema.json`
  + `schema.meta.json`), else an installed `sandesh-relay[migrate]` can't find its migrations at runtime.
  `migrate.py` locates them via the installed package dir (`importlib.resources` / `__file__`-relative),
  and **at least one test exercises the built/installed layout** (not only the source tree) so the
  packaging is actually verified.

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
- **Update `CLAUDE.md` to match (DRIFT-1 — user-decided 2026-06-09: fold into this CR).** Removing
  `message.status` contradicts the canonical design doc: revise **Locked-semantics #5** (no more
  `actioned`/disposition machine — read=seen is the only "seen" signal), the **four-tables table row**
  for `message` (drop the `status` column note), and the **`reply --resolves`** semantic (#6). Bring
  `CLAUDE.md` in line with `sandesh/data/usage-scenarios.md` (already status-free). (CR-SAN-018 §S3's
  separate `CLAUDE.md` edit is only the `CREATE TABLE`/migration gotcha — distinct from this.)
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
_Completed 2026-06-09 (orchestrator: vidushi-sandesh; gap-analysis skill). Verdict: **READY** — the two
SPEC_UPDATEs (DRIFT-1, DRIFT-2) are folded into §S7/§S1 above. The PRD §7 open questions were verified
against the **real yoyo 9.0.0 API** (read from installed source, not memory); the three genuine design
forks (`--check` strictness, the CLAUDE.md scope of DRIFT-1, the `[migrate]` floors) were **escalated to
and decided by the user on 2026-06-09** — see the tagged items below._

### Resolved — yoyo real API (PRD §7 #1), verified empirically against yoyo 9.0.0
- **Version present:** `yoyo-migrations==9.0.0`; `jsonschema==4.26.0` already in the dev venv.
- **Connection form:** `get_backend("sqlite:///<abs-path>")` → `SQLiteBackend`, default migration table
  `_yoyo_migration` (matches D4). `get_backend(uri, migration_table='_yoyo_migration')`.
- **API:** `yoyo.read_migrations(*sources)` over `sandesh/migrations/`; `backend.to_apply(migs)` /
  `backend.apply_migrations(migs)`; `backend.to_rollback(migs)` / `backend.rollback_migrations(migs)`;
  `backend.is_applied(m)`; **`backend.mark_migrations(migs)`** (the baseline-adoption primitive).
- **Per-step transaction:** `apply_one` wraps each migration's steps in a transaction (`with self.copy()
  as migration_backend: migration.process_steps(...)`), then marks it applied in its own transaction →
  the `0002` 12-step rebuild runs **atomically** (DDL rollback on failure). Confirmed in `backends/base.py`.
- **Applied-state record:** yoyo's `_yoyo_migration` table only (no `_schema_meta` — D4 holds).
- **Floors (resolved):** `[migrate] = ["yoyo-migrations>=9,<10", "jsonschema>=4.26"]` (major-pin yoyo like
  `mcp>=1.27,<2`; jsonschema floored at the installed modern-`referencing` line).

### Resolved — baseline adoption (PRD §7 #2)
Mechanism = yoyo `mark_migrations`. `migrate.py` detects a **pre-yoyo store** (the four tables exist AND
`0001-baseline` is not in `_yoyo_migration`) and calls `backend.mark_migrations([baseline])` to record it
**applied without running it**, then applies the rest (`0002…`). A brand-new/empty store has no tables →
`0001` applies normally. (This is the one bit of bespoke glue; AC4 covers it.)

### Resolved — `--check` strictness (PRD §7 #3)
**pending ⇒ exit non-zero** (installer/CI gate); **drift (live shape ≠ `current-schema.json`) ⇒ warning,
exit zero.** (PRD lean; AC7 asserts both: pending non-zero, and a clean fully-migrated store exits zero.)

### Dimension 1 (Spec vs PRD): consistent
Spec faithfully implements D1–D8 (yoyo runner + source-of-truth; `[migrate]` extra; derived JSON snapshot
for `--check` only; yoyo applied-list versioning; hand-written 12-step rebuild; CLI/installer only, no
MCP/Pi). Installer wiring + `--check` CI gate are correctly deferred to **CR-SAN-018** (verified its scope;
017 must NOT wire the installer — Non-goal holds).

### Dimension 2 (Spec vs Code)
- **§S7 status surface — VERIFIED ACCURATE.** Every `message.status` touchpoint the spec names exists as
  claimed: schema `status TEXT NOT NULL DEFAULT 'open'` (`sandesh_db.py:55`); `set_status` (`:280-284`);
  `reply(resolves=…)` → `set_status(parent,"actioned")` (`:256-277`); the `--resolves` CLI flag
  (`cli.py:259`); the `actioned` subcommand `cmd_actioned` (`cli.py:197-201`) + its subparser + `--status`
  (`cli.py:276-279`); the `thread` status print (`cli.py:192`); and `m.status` in the inbox/fetch/thread
  SELECTs (`sandesh_db.py:291`, …). All must be removed/updated by `0002` + core (AC11).
- **`_SCHEMA` baseline (AC3) — precision:** `_SCHEMA` is **exactly four `CREATE TABLE IF NOT EXISTS`
  statements with PK constraints and NO standalone `CREATE INDEX`** (`sandesh_db.py:41-76`). So
  `0001-baseline` reproduces just those four tables verbatim (the spec's "indexes/PKs" = PK constraints
  only; there are no separate indexes). `message.id` is `AUTOINCREMENT` (the harmless `sqlite_sequence`
  appears — CLAUDE.md gotcha).
- **DRIFT-2 (packaging) — SPEC_UPDATE (folded):** hatchling does **not** auto-bundle non-`.py` files under
  the package — proven by the existing `force-include` for `sandesh/data/usage-scenarios.md`
  (`pyproject.toml:42-44`). Therefore `sandesh/migrations/**` (`.sql`/`.py` steps) and `sandesh/schema/*.json`
  **must be explicitly packaged**, or an installed `sandesh-relay[migrate]` can't find its migrations at
  runtime. Added to §S1. `migrate.py` must locate them via the installed package dir (`__file__`-relative
  / `importlib.resources`), tested against the built/installed layout, not just the source tree.

### Dimension 3 (Code vs PRD)
- **DRIFT-1 (design-doc divergence) — SPEC_UPDATE (folded):** dropping `message.status` contradicts
  `CLAUDE.md`'s **Locked-semantics #5** ("Keep history + `actioned` … `message.status` (open→actioned→
  closed) is disposition … `reply --resolves` actions the parent"), the four-tables table row describing
  `status`, and the reply semantics (#6). **Neither CR-017 (originally) nor CR-018 §S3 covers these** —
  CR-018 only revises the "`CREATE TABLE IF NOT EXISTS` covers new installs" gotcha. Added to §S7: this CR
  must update those `CLAUDE.md` sections so code and the canonical design doc converge. (`sandesh/data/
  usage-scenarios.md` already describes the no-status world — it's ahead; only `CLAUDE.md` lags.)
- **AC1 import purity — implementation note (no drift):** unlike `mcp_server.py` (which imports `mcp` at
  module top in a `try/except`), `migrate.py` must import yoyo/jsonschema **inside function bodies only**,
  because `cli.py` wires `migrate` as a subcommand and importing `cli` must stay stdlib-pure (AC1). The
  spec already states this; GREEN must honor it (no top-level `import yoyo`).
- **Boundary:** CLI/installer only; the 9-verb messaging surface, `notify`, and `mcp_server` are untouched
  (the only `mcp_server` mentions of "status" are docstrings — no code change there).

### Summary table
| # | Dim | Finding | Fix scope | Blocking? |
|---|-----|---------|-----------|-----------|
| DRIFT-1 | 3 | `CLAUDE.md` locked-semantic #5 / tables row / reply semantics document `message.status`; not in 017's or 018's update scope | SPEC_UPDATE (folded into §S7) | Yes (doc↔code divergence) |
| DRIFT-2 | 2 | `migrations/**` + `schema/*.json` not auto-bundled by hatchling (force-include precedent) | SPEC_UPDATE (folded into §S1) | Yes (installed `[migrate]` can't find migrations) |
| — | 1 | PRD §7 open questions (yoyo API, baseline adoption, `--check` strictness) | RESOLVED above | — |

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
