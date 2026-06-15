# CR-SAN-038 — Pi surface: uvx-on-demand CLI + provision nudge

**Status:** COMPLETED (implemented on feature/CR-SAN-038; ships in 0.3.0)
**Priority:** Medium
**Depends on:** CR-SAN-036 (`sandesh init`)
**Labels:** pi, lifecycle, dx
**Wave:** provisioning-lifecycle (0.3.0)
**Design reference:** PRD-provisioning-lifecycle §4.0, §4.6. Stack: bun (`integrations/pi/`).

## Context
The Pi extension is the Pi-path agent surface (a replacement for the MCP server — it needs no
`[mcp]`). Today a Pi user must separately install the `sandesh` CLI (two steps), and nothing nudges
them to provision the store.

**Spans two stacks:** a tiny **python** CLI add (`sandesh init --check`) + the **bun** Pi changes.

## Scope
- **§S0 — `sandesh init --check` probe (python, cli.py).** A read-only, idempotent, **non-mutating**
  status probe (gap-analysis DRIFT-2 — admin is non-listable, so Pi has no way to detect
  "unprovisioned" without it). `sandesh init --check`: **exit 0** when the store is provisioned
  (store/DB exists AND a super-admin is assigned), **non-zero + a one-line message** otherwise
  (distinguishing "store absent" vs "admin unset"). Writes nothing; mutually exclusive with the
  normal provisioning run. Still CLI-only — never MCP.
- **§S1 — uvx-on-demand CLI (bun).** When a local `sandesh` is not on PATH, the extension invokes
  the CLI via `uvx --from 'sandesh-relay[migrate]' sandesh …` (**no `mcp`** in the extra); when a
  local `sandesh` is present, it uses that. A shared binary-resolution helper applied at ALL exec
  sites (`runSandesh`, the `--version` probe, the `notify` wake).
- **§S2 — provision nudge (bun).** Extend the `session_start` gate: after the ≥0.2.0 version check,
  run `sandesh init --check`; on non-zero, emit a one-line nudge to run `sandesh init` (surfacing
  the probe's store-absent vs admin-unset message). The version gate is preserved; provisioned
  stores get no nudge.
- **§S3 — no new tools (bun).** No `init`/`admin`/`migrate` tool is registered (Pi mirrors the MCP
  surface, which excludes them) — `init --check` is used only by the session gate, never as a Pi
  tool. The CLI's `[migrate]`-absent / provision error passes through verbatim (existing error-passthrough).

## Acceptance criteria
- [x] **AC0 — `init --check` probe (python).** On a provisioned store (exists + admin set)
      `sandesh init --check` exits 0; on a store with no admin it exits non-zero with an
      admin-unset message; on an absent store it exits non-zero with a store-absent message. It
      writes NOTHING (no migrate/consolidate/reindex/admin side-effects — verify the store is
      byte-unchanged), and there is no MCP `init`/`check` tool.
- [x] **AC1 — uvx invocation (bun).** With no local `sandesh`, the extension builds
      `uvx --from 'sandesh-relay[migrate]' sandesh …` (extra contains `migrate`, NOT `mcp`); with a
      local `sandesh`, it uses the local binary.
- [x] **AC2 — provision nudge (bun).** The session gate runs `sandesh init --check`; on non-zero it
      emits a one-line nudge naming `sandesh init`; on exit 0 (provisioned) it emits none.
- [x] **AC3 — version gate preserved.** The ≥0.2.0 CLI gate still fires on an out-of-date CLI.
- [x] **AC4 — no init/admin/migrate tool.** The registered-tool inventory is unchanged (no new
      provisioning tools).
- [x] **AC5 — error passthrough.** A `[migrate]`-absent / provision error from the CLI surfaces
      verbatim (no shim, no self-install).

## Estimated size
Small-medium — bun/TS; session-gate extension + CLI-acquisition path; vitest/bun-test coverage.

## Non-goals
- Installing/provisioning Sandesh-core from npm (Pi is a consumer surface); exposing init over Pi.
