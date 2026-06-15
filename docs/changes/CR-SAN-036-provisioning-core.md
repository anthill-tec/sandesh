# CR-SAN-036 — provisioning core: `sandesh init` + lazy auto-migrate

**Status:** PENDING
**Priority:** High (the capability every install route depends on)
**Depends on:** —
**Labels:** provisioning, migration, cli, lifecycle
**Wave:** provisioning-lifecycle (0.3.0)
**Design reference:** PRD-provisioning-lifecycle §4.1, §4.2, §4.0.

## Context
Provisioning (migrate→consolidate→reindex→admin) lives only in `install.sh`, so package-manager
installs leave an unprovisioned, possibly schema-behind store. This CR moves provisioning into the
package as a CLI command and makes migration self-healing on store open — so every route benefits.

## Scope
- **§S1 — lazy auto-migrate on open (lib).** In `connect()`/`setup()`, cheaply detect a
  schema-behind store; if `[migrate]` is importable, apply pending migrations automatically; else
  raise an **actionable error** whose message names the install-method-appropriate remediation
  (`uv tool install --with` / `pipx inject` / `pip install 'sandesh-relay[migrate]'` / pacman) —
  **never** self-`pip`. Current/empty store → no-op.
- **§S2 — install-method detection helper** producing the right remediation string (best-effort
  from the running interpreter path: uv-tool / pipx / venv / system).
- **§S3 — `sandesh init [--admin <name>] [--yes]` (CLI).** Idempotent provisioning sweep: migrate
  (if `[migrate]`, else the §S1 notice) → `consolidate` → `reindex` → admin assignment
  (`--admin` > `$SANDESH_ADMIN` > interactive prompt when tty and not `--yes` > skip-with-notice;
  the existing different-name-re-assign refusal holds). **CLI-only — never an MCP tool.**

## Acceptance criteria
- [ ] **AC1 — lazy migrate applies.** Opening a schema-behind store with `[migrate]` present
      auto-applies pending migrations (store ends current); a current store is untouched (no-op).
- [ ] **AC2 — actionable error, no self-pip.** A schema-behind store with `[migrate]` absent raises
      an error containing an install-method-specific remediation command; no `pip`/`uv` subprocess
      is spawned.
- [ ] **AC3 — `init` idempotent.** First `sandesh init` provisions (migrate+consolidate+reindex,
      admin if provided); a second run exits 0 as a clean no-op.
- [ ] **AC4 — `init` admin.** `sandesh init --admin X` assigns admin `X`; a later
      `--admin Y` is refused with the existing error; `admin_name` unchanged.
- [ ] **AC5 — `init` without `[migrate]`.** On a current/empty store, `sandesh init` (no migrate
      extra) runs consolidate+reindex and exits 0 with a migrate-skipped notice; on a *behind* store
      it exits non-zero with the §S1 remediation.
- [ ] **AC6 — no MCP surface.** `tools/list` contains no `init`/`admin`/`migrate` tool (grep).

## Estimated size
Medium — lib auto-migrate + detection + a CLI subcommand; focused tests.

## Non-goals
- install.sh integration (CR-SAN-037); Pi nudge (CR-SAN-038); making `[migrate]` a core dep.
