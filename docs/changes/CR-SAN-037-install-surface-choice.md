# CR-SAN-037 — install.sh: surface choice + delegate to `sandesh init` + mandatory migrate

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-036 (`sandesh init`)
**Labels:** installer, provisioning, lifecycle
**Wave:** provisioning-lifecycle (0.3.0)
**Design reference:** PRD-provisioning-lifecycle §4.0, §4.3, §4.4 (install part).

## Context
`install.sh` carries its own provisioning block and always installs `[mcp,migrate]`. Per the PRD
the agent surfaces are a user *choice* (Claude→MCP, Pi→no-mcp, both, none), provisioning must be the
single `sandesh init` entry point, and an update over an existing store must migrate (not silently
skip).

## Scope
- **§S1 — surface choice.** Interactive prompt: Claude(MCP) / Pi / both / none →
  Claude|both ⇒ extras include `mcp`; Pi|none ⇒ **no `mcp`**; `migrate` recommended on all. Honor a
  non-interactive override (`SANDESH_INSTALL_EXTRAS`, and/or a `--surface` flag) for CI.
- **§S2 — delegate provisioning.** Replace the inline migrate/consolidate/reindex/admin block with a
  single `sandesh init` call (admin via `$SANDESH_ADMIN`); no duplicated provisioning logic.
- **§S3 — mandatory migrate on existing DB.** If `$DEST/sandesh.db` exists (update), `[migrate]` is
  required: install it and migrate; if it cannot be installed (e.g. offline), **fail loudly**
  (non-zero) — no silent skip. Fresh install (no DB) → `[migrate]` best-effort (absent non-fatal).

## Acceptance criteria
- [ ] **AC1 — surface prompt.** An interactive run offers Claude/Pi/both/none and installs the
      matching extras; `mcp` present only for Claude/both. `SANDESH_INSTALL_EXTRAS` (non-interactive)
      is honored unchanged.
- [ ] **AC2 — Pi excludes mcp.** Choosing Pi installs the CLI with no `mcp` (no `sandesh-mcp`
      symlink/console script present).
- [ ] **AC3 — delegates to init.** `install.sh` invokes `sandesh init`; the provisioning steps are
      not duplicated inline; a fresh install ends provisioned (admin set from `$SANDESH_ADMIN`).
- [ ] **AC4 — mandatory migrate.** With an existing `sandesh.db` and `[migrate]` installable →
      migrated; with `[migrate]` NOT installable (simulated) → install exits non-zero with a clear
      message (no silent skip).
- [ ] **AC5 — fresh best-effort.** No `sandesh.db` + `[migrate]` unavailable → install still
      completes (migrate skipped with notice).
- [ ] **AC6 — regression.** The existing `InstallShTest` (default install) stays green.

## Estimated size
Medium — bash arg/prompt logic + delegation; test_install.py additions.

## Non-goals
- The `sandesh init` command itself (CR-SAN-036); uninstall (CR-SAN-035); docs (CR-SAN-039).
