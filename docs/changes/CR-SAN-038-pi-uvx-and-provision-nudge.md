# CR-SAN-038 — Pi surface: uvx-on-demand CLI + provision nudge

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-036 (`sandesh init`)
**Labels:** pi, lifecycle, dx
**Wave:** provisioning-lifecycle (0.3.0)
**Design reference:** PRD-provisioning-lifecycle §4.0, §4.6. Stack: bun (`integrations/pi/`).

## Context
The Pi extension is the Pi-path agent surface (a replacement for the MCP server — it needs no
`[mcp]`). Today a Pi user must separately install the `sandesh` CLI (two steps), and nothing nudges
them to provision the store.

## Scope
- **§S1 — uvx-on-demand CLI.** When a local `sandesh` is not on PATH, the extension invokes the CLI
  via `uvx --from 'sandesh-relay[migrate]' sandesh …` (**no `mcp`** in the extra); when a local
  `sandesh` is present, it uses that. (Configurable; documented.)
- **§S2 — provision nudge.** Extend the existing ≥0.2.0 CLI session gate to also detect an
  **unprovisioned store** (super-admin unset / store absent) and emit a one-line nudge to run
  `sandesh init`. The version gate is preserved.
- **§S3 — no new tools.** No `init`/`admin`/`migrate` tool is registered (Pi mirrors the MCP
  surface, which excludes them). The CLI's `[migrate]`-absent / provision error is passed through
  verbatim (existing error-passthrough).

## Acceptance criteria
- [ ] **AC1 — uvx invocation.** With no local `sandesh`, the extension builds
      `uvx --from 'sandesh-relay[migrate]' sandesh …` (extra contains `migrate`, NOT `mcp`); with a
      local `sandesh`, it uses the local binary.
- [ ] **AC2 — provision nudge.** Against an unprovisioned store (probe), the session gate emits a
      nudge naming `sandesh init`; against a provisioned store it emits none.
- [ ] **AC3 — version gate preserved.** The ≥0.2.0 CLI gate still fires on an out-of-date CLI.
- [ ] **AC4 — no init/admin/migrate tool.** The registered-tool inventory is unchanged (no new
      provisioning tools).
- [ ] **AC5 — error passthrough.** A `[migrate]`-absent / provision error from the CLI surfaces
      verbatim (no shim, no self-install).

## Estimated size
Small-medium — bun/TS; session-gate extension + CLI-acquisition path; vitest/bun-test coverage.

## Non-goals
- Installing/provisioning Sandesh-core from npm (Pi is a consumer surface); exposing init over Pi.
