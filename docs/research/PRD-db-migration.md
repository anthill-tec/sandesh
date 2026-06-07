# PRD — Sandesh DB schema migration

**Status:** DRAFT (design contract — CRs spin from this)
**Owner:** Mainline - Sandesh
**Phase:** Phase 5 (schema evolution)
**Related:** `CLAUDE.md` (the four tables / `_SCHEMA`), CR-SAN-005 §S3 + the deferred **CR-SAN-012**
(core `message.status` retirement — now **folded into CR-SAN-017** as the engine's first real migration /
proving case), Python-backend audit #4.

Design contract (WHY + WHAT) for evolving the per-project SQLite schema safely — versioned, tracked,
repeatable, and reversible — **without hand-rolling a relational migration runner.** CRs derived from
this cite it via `**Design reference:**`.

---

## 1. Why

Today the schema is created with `CREATE TABLE IF NOT EXISTS` (`sandesh_db._SCHEMA`). That covers
**new** installs but does nothing for **existing** stores when the schema changes. The first concrete
case is the deferred **CR-SAN-012** (dropping `message.status` / the disposition machine) — removing a
column from a live SQLite DB needs a real migration (SQLite has no plain `DROP COLUMN` before 3.35;
the safe path is the 12-step table rebuild). Sandesh is multi-project (`projects/<id>/sandesh.db`),
so *every* project store must migrate. We need a versioned, tracked, idempotent migration mechanism —
and we should **not** reinvent the apply/track/rollback machinery.

## 2. What it is (and is not)

**Is:** a migration subsystem with three layers —
1. **Runner + source-of-truth = `yoyo-migrations`** (the apply/rollback/tracking engine; SQLite-
   supported) — the ordered migration steps in `migrations/` ARE the canonical schema history; we do
   NOT hand-code the runner and we do NOT hand-roll a second version store (yoyo's `_yoyo_migration`
   table is the applied-state record).
2. **A derived JSON schema *snapshot*** (the current schema shape, validated with JSON Schema) used
   ONLY by `migrate --check` to detect drift ("does the live DB match the expected shape?") — it is a
   verification reference, **not** the source of truth and **not** a migration generator (decision B).
3. **Invocation = the installer + a dev-assist `sandesh migrate` CLI** — a developer/operator tool and
   an installer patch step. The CLI also carries the **read-only authoring aids** (`--dump-schema`,
   `--diff [--json]`): a developing agent runs them via the shell to dump the live schema and diff it
   against the snapshot, then hand-writes the yoyo step. **No MCP/Pi wrapper** — the CLI is enough.

**Is not:** an ORM; a rewrite of `sandesh_db` (it stays the stdlib data layer); a runtime dependency
of the core (migration tooling is an **optional `[migrate]` extra**, off the hot path); any part of
the MCP/Pi surface at all (migration is CLI/installer only — no new MCP or Pi tools); a generic
multi-DB tool (SQLite only); a declarative diff→DDL *generator* (the agent writes the step).

## 3. Decisions

**D1 — `yoyo-migrations` is the runner.** It applies ordered migration steps, records what's applied
(its `_yoyo_migration` table), supports rollback, and supports SQLite (`sqlite:///<path>`). We write
migration steps (`.sql` or `.py` in a `migrations/` dir); yoyo owns ordering/locking/tracking. (Chosen
over Alembic — which drags in SQLAlchemy — and over a hand-rolled `PRAGMA user_version` loop.)

**D2 — Migration tooling is an OPTIONAL `[migrate]` extra, not a core runtime dep.**
`yoyo-migrations` **and** `jsonschema` live behind a **`[migrate]`** extra
(`pip install 'sandesh-relay[migrate]'`), mirroring the `[mcp]` isolation. The **core stays pure-stdlib**:
`sandesh_db` / `cli` / `notify` / `mcp_server` (the messaging server) never import yoyo or jsonschema.
`sandesh migrate` lazy-imports them and prints a friendly "install the `[migrate]` extra" message +
non-zero exit when absent (the `sandesh-mcp`-without-`[mcp]` pattern, CR-SAN-008 §S6). The **entire
migration surface — execution AND the read-only authoring aids (dump/diff) — is CLI + installer only,
never MCP/Pi.** A developing agent uses the aids by shelling to `sandesh migrate --dump-schema` /
`--diff` (no dedicated agent tool needed).

**D3 — yoyo migration steps are the SOURCE OF TRUTH; the JSON schema is a derived snapshot (decision B).**
The ordered steps in `migrations/` are the canonical schema history. A committed **`schema/current-schema.json`**
(tables → columns → type/constraints), validated by a committed JSON-Schema meta-schema (`jsonschema`),
is a **derived snapshot** of the expected shape — kept in sync with the latest migration (regenerable
*from* the live DB via `migrate --dump-schema`). It does **not** drive migrations and is **not** a
diff→DDL generator; its only job is to give `--check` (D5) a reference to compare the live DB against.

**D4 — Versioning = yoyo's applied-migration list (no second tracker).** yoyo's `_yoyo_migration` table
IS the version state (which ordered steps are applied). **No separate `_schema_meta`/`PRAGMA user_version`
table.** Schema "version" is just a **release label** (the migration filenames carry an ordinal, e.g.
`0001`, `0002`; the human-facing semver is the release tag `vX.Y.Z`). `migrate --status` reports applied
vs pending from yoyo. (Dropped the `_schema_meta` semver table — decision #3.)

**D5 — Drift / pending detection (`migrate --check`).** Two checks, no writes: (1) **pending** — are
there unapplied yoyo migrations for this store? (2) **drift** — does the live DB shape (`PRAGMA
table_info` per table) match `schema/current-schema.json`? Report both; exit non-zero if migrations are
pending (installer/CI gate). "Recognize a change is requested" = a non-empty pending set and/or drift.

**D6 — Field add/drop via HAND-WRITTEN yoyo steps; SQLite limits handled explicitly.** `ADD COLUMN` is a
one-liner; **drop/rename/type-change** use the SQLite **12-step table rebuild** (new table + `INSERT …
SELECT` + drop + rename, in yoyo's transaction) — **written by hand** per migration (no auto-generator;
decision #4). CR-SAN-012's `message.status` drop is the first such hand-written rebuild step.

**D7 — Invocation: installer + dev-assist `sandesh migrate` CLI, per-project.** CLI subcommands:
`sandesh migrate --project <id>` (apply pending), `--check` (D5, no writes), `--status`, `--rollback`
(one step), `--dump-schema` (emit the live DB shape as JSON), **`--diff <old-snapshot> [--json]`**
(structured added/removed/changed tables+columns between a stored snapshot and the freshly-dumped
current state — the read-only migration-authoring aid an agent shells to); `--all` iterates every
`projects/<id>/sandesh.db`. The **installer** (`install.sh` / documented post-install) runs `migrate
--all` so existing stores upgrade on update. Idempotent (yoyo skips applied steps). CLI/installer only.

**D8 — Boundary: CLI/installer only, NO MCP/Pi.** The whole migration surface — execution *and* the
read-only dump/diff aids — is a maintenance/installer/dev-CLI operation, never on the message hot path
and **never an MCP or Pi tool** (an MCP/Pi wrapper was considered and rejected as overkill — the CLI is
enough; a developing agent just shells to it). The core runtime never triggers migration implicitly; an
out-of-date store is the operator's signal to run `migrate` (`--check` warns). Keeps `sandesh_db` pure,
the 9-verb messaging surface untouched, and no-surprise-writes.

## 4. Architecture / layout

```
sandesh/
  migrate.py                 # the [migrate] entrypoint: lazy-imports yoyo+jsonschema; CLI glue (sandesh migrate …)
                             #   incl. dump_schema()/diff_schema() (read-only) backing --dump-schema/--diff
  migrations/                # yoyo steps (.sql/.py), ORDERED = source of truth. 0001-baseline, 0002-drop-status, …
  schema/
    schema.meta.json         # JSON-Schema meta-schema validating the snapshot's format
    current-schema.json      # DERIVED snapshot of the expected shape (for --check); regenerable via --dump-schema
```
- `sandesh migrate` is wired in `cli.py` as a subcommand, but the heavy imports live in `sandesh/migrate.py`
  (lazy) so the stdlib CLI / messaging-MCP / notify paths never import yoyo/jsonschema. **No part of the
  migration surface is an MCP/Pi tool** (D8) — the dump/diff aids are plain CLI flags an agent shells to.
- **No `_schema_meta` table** — yoyo's `_yoyo_migration` is the only applied-state record.
- The **baseline** migration (`0001`) reproduces today's `_SCHEMA`; existing pre-yoyo stores are adopted
  by recording the baseline as already-applied (see §7) rather than re-running it.

## 5. CR breakdown (spin from this)

| CR | Scope | Depends on |
|---|---|---|
| **CR-SAN-017** | **Migration engine + first real migration (proving case)**: `[migrate]` extra (yoyo + jsonschema) + friendly-absent error; `sandesh/migrate.py` + `sandesh migrate` CLI (`apply`/`--check`/`--status`/`--rollback`/`--dump-schema`/`--diff --json`, `--project`/`--all`); yoyo `migrations/` with the **baseline `0001`** matching today's `_SCHEMA` + the adopt-baseline glue; the derived `schema/current-schema.json` snapshot + `schema.meta.json` (jsonschema-validated); versioning = yoyo's applied list (no `_schema_meta`). **Proven by the first real schema change** — the deferred **CR-SAN-012** work folded in: a **hand-written** `0002` migration dropping `message.status`/disposition (the SQLite 12-step rebuild) + the `current-schema.json` bump + the core-code removal, exercised end-to-end through the engine. | CR-SAN-008 (extras), Python core |
| **CR-SAN-018** | **Installer integration**: `install.sh` / post-install runs `migrate --all`; `--check` release/CI gate; README/RELEASING docs. | CR-SAN-017 |

(Numbers provisional — allocated against the queue HEAD when scheduled, after this PRD is agreed.)

## 6. Non-goals / out of scope

- An ORM or replacing `sandesh_db` (stdlib data layer stays).
- Making migration a core runtime dependency (it's the `[migrate]` extra).
- Any MCP/Pi migration tool (execution OR the dump/diff aids) — migration is CLI/installer only; an
  MCP/Pi wrapper was considered and rejected as overkill (D8).
- A declarative diff→DDL generator (the agent hand-writes the yoyo step from the diff).
- Multi-database support (SQLite only).
- Auto-running migrations implicitly from the message hot path (operator/installer-driven; D8).
- Online/zero-downtime concerns (local single-writer SQLite; migrate when idle).

## 7. Open questions (resolve at CR gap-analysis)

- **yoyo SQLite specifics (verify real API at CR-017 gap-analysis)** — the `sqlite:///<path>` connection
  form, whether yoyo wraps each step in a transaction (DDL rollback), `read_migrations`/`apply`/`rollback`
  API, and how it records applied steps. Don't assume — read yoyo's docs/source.
- **Baseline adoption for existing stores** — `0001-baseline` must be recorded as already-applied on
  stores that predate yoyo (they already have the four tables) so it isn't re-run. Decide the mechanism
  (yoyo "mark as applied" / `migrate` detects existing tables + empty `_yoyo_migration` → record the
  baseline). This is the one bit of glue in `migrate.py`.
- **`--check` strictness** — does drift (live DB ≠ `current-schema.json`) exit non-zero or just warn,
  while *pending migrations* always exit non-zero? (Lean: pending = error, drift = warning.)

**Resolved by the PRD discussion (2026-06-07):** source-of-truth = **yoyo steps** (option B; JSON is a
derived `--check` snapshot, no generator); versioning = **yoyo's applied list only** (no `_schema_meta`);
rebuild steps **hand-written** (no generator); migration is the **`[migrate]` extra**, run by the
**installer + dev CLI**, **not** MCP.
