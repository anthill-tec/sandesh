# CR-SAN-018 — Migration installer & CI integration

**Status:** COMPLETED (2026-06-10)
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
- **Default extras → `[mcp,migrate]` (user-decided DEC-2).** `install.sh` `EXTRAS` default becomes
  `[mcp,migrate]` so its own venv gets `yoyo`+`jsonschema` and `migrate --all` auto-runs on update
  out-of-the-box. (`$SANDESH_INSTALL_EXTRAS` still overrides; the existing best-effort install-extra /
  fall-back-to-base block is unchanged.)
- After the venv install + console-script symlinks, the installer invokes `"$VENV/bin/sandesh" migrate
  --all` so every existing `projects/<id>/sandesh.db` is brought to the latest schema. (A fresh install
  with no stores is a clean no-op — `migrate --all` over zero stores exits 0.)
- **Abort policy (user-decided DEC-3): distinguish by the KNOWN install outcome, not the exit code**
  (both the missing-extra guard and a real `--all` error exit 1):
  - if the `[*,migrate]` extra install **succeeded** (deps present) → run `migrate --all` **under
    `set -e`** so a genuine migration failure (corrupt store / bad step) **aborts the install** non-zero;
  - if the extra install **fell back to base** (`[migrate]` absent) → **skip** `migrate --all`, print a
    clear "migrations skipped — install `[migrate]`" notice (`pip install 'sandesh-relay[migrate]' &&
    sandesh migrate --all`), and let the install **complete successfully** (AC2). Do NOT invoke `migrate`
    in this branch (avoids the friendly-absent non-zero tripping `set -e`).
- Idempotent: re-running the installer re-runs `migrate --all` harmlessly (yoyo skips applied steps).

### §S2 — release-integrity gate: snapshot-sync in CI (user-decided DEC-1)
- **The gate is a snapshot-sync check, not a literal `migrate --check`** (gap-analysis Dim-3: a freshly
  migrated store has 0 pending and `--check` drift only warns, so `--check` would gate nothing). Add a
  step to **`.github/workflows/publish-pypi.yml`** (the only existing workflow — DRIFT-A) that runs on
  `release` / `pull_request` / `push: develop`: in a temp `$XDG_DATA_HOME`, `sandesh setup --project ci`,
  `sandesh migrate --all`, then assert `sandesh migrate --dump-schema --project ci` **equals** the
  committed `sandesh/schema/current-schema.json` (compare as JSON, modulo key ordering). **Mismatch ⇒
  non-zero ⇒ the job fails** — catching a migration added without regenerating the snapshot, before a
  release ships. The step needs the `[migrate]` extra on the CI interpreter (`pip install -e '.[migrate]'`
  or the built wheel's extra).
- The job must run BEFORE/independently of the publish step (a stale snapshot blocks the release).

### §S3 — docs
- `README.md` — a short "Schema migrations" section: what `sandesh migrate` is, that updates auto-migrate
  via the installer, the `[migrate]` extra, and the `--check`/`--status`/`--rollback` flags.
- `RELEASING.md` — the maintainer steps: ensure `current-schema.json` + `migrations/` are in sync before
  tagging; the `--check` gate; that the installer migrates on update.
- `CLAUDE.md` — **additively** (DRIFT-B: the old "`CREATE TABLE IF NOT EXISTS` only covers new installs"
  caveat isn't present) add a short note in the schema/Gotchas area: schema changes ship via the migration
  subsystem (`sandesh migrate` + the `[migrate]` extra), the installer auto-migrates existing stores on
  update, and `current-schema.json` must stay in sync (the CI gate).

## Acceptance criteria

- [x] **AC1 — installer migrates existing stores.** After `install.sh` on a data home containing a
      pre-migration store, that store is at the latest schema (asserted: `migrate --status` shows 0
      pending / the post-`0002` shape present). (Tested via a temp `$XDG_DATA_HOME`.)
- [x] **AC2 — installer tolerates missing extra.** With the `[migrate]` deps absent, `install.sh`
      **completes successfully** (non-zero from `migrate` does not abort the install) and prints the
      "migrations skipped — install `[migrate]`" notice (asserted by running the installer path without
      the deps and checking exit 0 + the notice).
- [x] **AC3 — fresh install no-op.** On an empty data home (no project stores), the installer's migrate
      step is a clean no-op (no error; asserted).
- [x] **AC4 — snapshot-sync release gate wired (DEC-1).** `.github/workflows/publish-pypi.yml` has a step
      (on `release`/`pull_request`/`push: develop`) that seeds a temp store, `migrate --all`, and asserts
      `migrate --dump-schema` **equals** the committed `current-schema.json`, **failing non-zero on
      mismatch** (asserted by parsing the workflow YAML for the step + its commands). A unit-level test
      may additionally prove the equality holds for the current committed snapshot (a stale-snapshot guard
      runnable outside CI).
- [x] **AC5 — docs present.** `README.md`, `RELEASING.md`, and `CLAUDE.md` document the migration
      subsystem, the `[migrate]` extra, the installer auto-migrate, and the `--check` gate (asserted by
      content checks / grep markers).
- [x] **AC6 — boundary intact.** No migration call is added to the message hot path, the messaging MCP
      server, or the Pi extension (asserted: `sandesh_db`/`mcp_server`/`integrations/pi` reference no
      `migrate` apply; the only new caller is the installer/CLI).

## Close-out
_Completed 2026-06-10 (orchestrator: vidushi-sandesh). 3 cycles + VERIFY. Closes Wave 5._
- **C1** `26b701b`/`6435e1f` — `install.sh`: `EXTRAS` default → `[mcp,migrate]` (DEC-2); after console-script
  symlinks, venv-probe (`import yoyo,jsonschema`) → run `migrate --all` under `set -e` (real error aborts,
  DEC-3) else print the "migrations skipped — install `[migrate]`" notice + complete (AC2). AC1/AC2/AC3.
- **C2** `e7b1dad`/`de63fcb` — `publish-pypi.yml` **snapshot-sync gate** in the un-gated `build` job
  (install `.[migrate]`, seed temp store, `migrate --all`, `migrate --dump-schema` **==** committed
  `current-schema.json` via a python dict-`==` compare, non-zero on mismatch). AC4 (DEC-1).
- **C3** `d6c3a4a`/`a5baf17` — README/RELEASING/CLAUDE schema-migration sections (CLAUDE additive, DRIFT-B)
  + AC6 boundary guard. AC5/AC6.
- **VERIFY** (python-verify-agent) — PASS on all 6 ACs + DEC-3 + scope; the gate independently confirmed
  to currently pass (committed snapshot genuinely in sync). One should-fix is a **pre-existing**
  `ResourceWarning` in `test_install.py` (CR-SAN-008 scope — unclosed subprocess pipes), noted for a future
  cleanup, not folded (scope).
- **Independent verification (orchestrator):** the CI gate logic run locally → committed `current-schema.json`
  matches a freshly-migrated dump (exit 0); install/gate/docs suites green; AC6 boundary clean.
- **Pre-merge gate:** `python-crucible.py pre-merge-gate` → **458 passed / 0 failed**, `py_compile` clean,
  `ok=True`, coverage 52.1% lines / 58.4% funcs (`--cov-source sandesh`).
- **Wave 5 complete** — CR-SAN-017 (engine) + CR-SAN-018 (installer/CI/docs) shipped.

## Gap-analysis findings
_Completed 2026-06-10 (orchestrator: vidushi-sandesh; gap-analysis skill). Verdict: **READY** — the open
questions were escalated and **user-decided 2026-06-10**, and folded into Scope/ACs below. CR-017 (the
engine) is merged on `develop`; this CR only adds the installer hook + CI gate + docs._

### Dimension 1 (Spec vs PRD): consistent
PRD-db-migration **D7** (installer / documented post-install runs `migrate --all` so existing stores
upgrade on update; idempotent), **D8** (CLI/installer only — never the hot path, never MCP/Pi), and **§5**
(CR-018 = installer integration) all match the spec. No gap.

### Dimension 2 (Spec vs Code)
- **`install.sh` already has the §S1 machinery:** it builds its OWN venv at `<data_home>/sandesh/.venv`,
  pip-installs `sandesh-relay$EXTRAS` (default `EXTRAS=[mcp]`, overridable via `$SANDESH_INSTALL_EXTRAS`),
  with a **best-effort try-extra-then-fall-back-to-base** block, then symlinks the console scripts. So the
  installer can provision `[migrate]` the same way, and it already KNOWS whether the extra install
  succeeded or fell back to base — that branch is how we distinguish AC2's missing-extra case.
- **`migrate --all` over an empty store list `return 0`** (clean no-op — AC3 holds; `cmd_migrate`
  `migrate.py:454-472`). Friendly-absent is `_require_deps()` → exit non-zero (`migrate.py:419`) — AC2
  mechanism present.
- **`set -euo pipefail`:** a non-zero `migrate` would abort the install unless guarded — relevant to the
  AC2 (tolerate missing-extra) vs decision-3 (abort on real error) split below.
- **DRIFT-A (CI gate target) — §S2 SPEC_UPDATE:** §S2 says "wire into the **existing CI workflow**," but
  there is **no general/test CI** — only `publish-npm.yml` + `publish-pypi.yml` (release-publish). So the
  gate lands in `publish-pypi.yml`.
- **DRIFT-B (CLAUDE.md target) — §S3 minor:** §S3 says "replace the *`CREATE TABLE IF NOT EXISTS` only
  covers new installs* caveat," but that exact caveat is **not present** in CLAUDE.md (it already carries
  the post-0002 note from CR-017 C6). So the CLAUDE.md edit is **additive** (a "schema changes ship via
  the migration subsystem" note in the schema/Gotchas area), not a replacement.

### Dimension 3 (Code vs PRD)
- **AC6 boundary — clean baseline:** no `migrate`/`yoyo` references in `sandesh_db.py`, `mcp_server.py`,
  `notify.py`, or `integrations/pi/src/*.ts`. The only `migrate` caller is `cli.py`. CR-018 adds only the
  installer (+ CI) caller — boundary holds.
- **The `--check`-as-gate semantics (resolved):** AC4 originally said the gate runs `migrate --check` and
  "fails on pending migrations." But a freshly-seeded+migrated CI store has **0 pending**, and `--check`'s
  drift is a **warning (exit 0)** (CR-017 user-decided strictness) — so a literal `migrate --check` in CI
  gates ~nothing. The real release-integrity check is **snapshot-sync**: a fully-migrated store's
  `--dump-schema` must **equal** the committed `current-schema.json` (CR-017 AC8). **Decided below.**

### Resolved decisions (escalated → user-decided 2026-06-10)
1. **CI gate = snapshot-sync (not literal `--check`).** In `publish-pypi.yml` (a step that runs on
   release / PR / push `develop`): seed an empty store, `sandesh migrate --all`, then assert
   `sandesh migrate --dump-schema` **==** the committed `sandesh/schema/current-schema.json` (modulo key
   ordering); **mismatch ⇒ non-zero ⇒ fails the release** (catches a migration added without regenerating
   the snapshot). This supersedes AC4's weak `--check`-pending wording. (§S2 + AC4 updated.)
2. **Default install extras = `[mcp,migrate]`.** The installer's own venv gets `yoyo`+`jsonschema` so
   `migrate --all` auto-runs on update out-of-the-box. (§S1 updated.)
3. **Installer abort policy: tolerate ONLY missing-extra; abort on a real migrate error.** Distinguish by
   the KNOWN install outcome (not exit code, since both are 1): if the `[mcp,migrate]` install **succeeded**
   → run `migrate --all` and let a non-zero **abort** the install (real-error surfacing, decision #3); if
   it **fell back to base** ([migrate] absent) → **skip** `migrate --all`, print the "migrations skipped —
   install `[migrate]`" notice, and let the install **complete** (AC2). (§S1 updated.)

### Summary table
| # | Dim | Finding | Fix scope | Blocking? |
|---|-----|---------|-----------|-----------|
| DRIFT-A | 2 | §S2 "existing CI workflow" — none exists; only `publish-*.yml` | SPEC_UPDATE (gate → `publish-pypi.yml`) | No |
| DRIFT-B | 2 | §S3 CLAUDE.md caveat to "replace" isn't present → edit is additive | SPEC_UPDATE (minor) | No |
| DEC-1 | 3 | `--check`-pending gate is weak → snapshot-sync (`--dump-schema`==snapshot) | user-decided | No |
| DEC-2 | 2 | install extras `[mcp]` → `[mcp,migrate]` | user-decided | No |
| DEC-3 | 2 | installer abort policy (missing-extra tolerate / real-error abort) | user-decided | No |

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
