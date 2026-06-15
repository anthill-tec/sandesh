# CR-SAN-037 — install.sh: surface choice + delegate to `sandesh init` + mandatory migrate

**Status:** IN_PROGRESS
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
- **§S1 — surface choice.** Three precedence levels (gap-analysis DESIGN-2 — the tty prompt is not
  testable, so a flag is the primary CI mechanism):
  1. **`--surface claude|pi|both|none`** flag (highest) → resolves EXTRAS: claude|both ⇒ include
     `mcp`; pi|none ⇒ **no `mcp`**; `migrate` on all.
  2. else **`SANDESH_INSTALL_EXTRAS`** (existing env override) used verbatim.
  3. else **interactive prompt** — ONLY when stdin is a tty AND neither (1) nor (2) given.
  4. else (non-interactive, nothing given) → **default `[mcp,migrate]`** (preserves `InstallShTest`).
- **§S2 — delegate provisioning.** Replace the inline migrate→consolidate→reindex→admin block
  (current install.sh ~lines 142-193) with a single `sandesh init --yes [--admin "$SANDESH_ADMIN"]`
  call; no duplicated provisioning logic. **Refresh** the existing install-migrate tests that pin the
  OLD inline output (`MigrateExtraInstallTest` greps `migrations skipped`/`migrate --all`/`sandesh
  migrate` at test_install.py:832; `MigrateOnInstallTest`/`FreshInstallMigrateNoOpTest`) to the
  delegated `sandesh init` behaviour/output (DRIFT-1; behaviour preserved, strings change).
- **§S3 — mandatory migrate on existing DB.** If `$DEST/sandesh.db` exists (update), `[migrate]` is
  required: install it and migrate; if it cannot be installed (e.g. offline), **fail loudly**
  (non-zero) — no silent skip. Fresh install (no DB) → `[migrate]` best-effort (absent non-fatal).

## Acceptance criteria
- [ ] **AC1 — surface resolution.** `install.sh --surface claude` (and `both`) installs extras
      including `mcp`; `--surface pi` and `--surface none` install **without** `mcp`. With no flag,
      `SANDESH_INSTALL_EXTRAS` is honored verbatim; with neither (non-interactive), EXTRAS defaults
      to `[mcp,migrate]`.
- [ ] **AC2 — Pi excludes mcp.** `--surface pi` (or `SANDESH_INSTALL_EXTRAS='[migrate]'`) installs
      the CLI with **no** `mcp` — no `sandesh-mcp` console script/symlink present.
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
