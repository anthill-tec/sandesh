# CR Queue — Sandesh

Single source of truth for change requests. Pick the next `PENDING` CR by phase + dependencies.

| CR | Title | Phase | Status | Depends on | Shipped |
|---|---|---|---|---|---|
| [CR-SAN-001](CR-SAN-001-mcp-server.md) | MCP server foundation (venv+wrapper) & dependency isolation | Phase 2 | COMPLETED | — | 2026-06-06 |
| [CR-SAN-002](CR-SAN-002-read-tools.md) | MCP read/query tools | Phase 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-003](CR-SAN-003-mutating-tools.md) | MCP mutating tools & error mapping | Phase 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-004](CR-SAN-004-e2e-smoke.md) | MCP E2E: protocol + real-subprocess stdio smoke tests | Phase 2 | COMPLETED | CR-SAN-001..003 | 2026-06-06 |
| [CR-SAN-005](CR-SAN-005-retire-status.md) | Retire `status`/disposition tool: remove `sandesh_actioned` (10→9); lock no `resolves`/`reply_all` (PRD §10/D7) | Phase 2 | COMPLETED | CR-SAN-001..004 | 2026-06-07 |
| [CR-SAN-006](CR-SAN-006-docstrings-instructions.md) | Docstring & usability enrichment + server `instructions` + `sandesh://usage` resource (PRD §10) | Phase 2 | COMPLETED | CR-SAN-005 | 2026-06-07 |
| CR-SAN-007 | Install `$PATH` hardening | Phase 2 | SUPERSEDED by CR-SAN-008 | CR-SAN-001 | — |
| [CR-SAN-008](CR-SAN-008-packaging.md) | Packaging: `pyproject.toml` (hatchling + hatch-vcs), `sandesh/` package, console scripts, `[mcp]` extra, bundled usage doc | Phase 3 | COMPLETED | CR-SAN-001..006 | 2026-06-07 |
| [CR-SAN-009](CR-SAN-009-aur-pkgbuild.md) | AUR PKGBUILD (secondary, Arch) — derives from the package | Phase 3 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-010](CR-SAN-010-pypi-publish.md) | PyPI release (OIDC trusted publishing) — publishes `sandesh-relay`; enables uv/pipx/pipxu install | Phase 3 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-011](CR-SAN-011-mcp-registry.md) | Official MCP Registry listing (`server.json`, `io.github.anthill-tec/sandesh`) — discoverable by MCP clients/directories | Phase 3 | COMPLETED | CR-SAN-010 | 2026-06-07 |
| [CR-SAN-013](CR-SAN-013-pi-verbs-extension.md) | Pi extension: scaffold + verb tools (`integrations/pi/`, TS, `registerTool` over the `sandesh` CLI) | Phase 4 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-014](CR-SAN-014-pi-native-wake.md) | Pi native wake (background watcher → `sendUserMessage`; design W1 from DN-pi-wake) | Phase 4 | COMPLETED | CR-SAN-013 | 2026-06-07 |
| [CR-SAN-015](CR-SAN-015-pi-package-listing.md) | Pi extension packaging/listing — **npm** publish (`npm:@anthill-tec/sandesh-pi`) + pi.dev/packages gallery (git: can't target a subdir) | Phase 4 | COMPLETED | CR-SAN-013, CR-SAN-014 | 2026-06-07 |
| [CR-SAN-016](CR-SAN-016-pi-error-handling-promptsnippet.md) | Pi extension review fixes: tool `execute` **throws** on CLI failure (sets `isError`) + `promptSnippet`/`promptGuidelines` on all 9 tools (review #1/#2/#5; #3/#4 rejected) | Phase 4 | COMPLETED | CR-SAN-013 | 2026-06-07 |
| [CR-SAN-017](CR-SAN-017-migration-engine.md) | DB schema **migration engine** + first real migration: `[migrate]` extra (yoyo + jsonschema), `sandesh migrate` CLI (apply/check/status/rollback/dump-schema/diff), baseline `0001` + adoption glue, derived `current-schema.json`; **proven by** the `message.status` drop (`0002` 12-step rebuild — folds deferred CR-SAN-012). CLI/installer only, no MCP/Pi | Phase 5 | PENDING | CR-SAN-008 | — |
| [CR-SAN-018](CR-SAN-018-migration-installer-integration.md) | Migration **installer & CI integration**: `install.sh` runs `migrate --all` (tolerates missing extra), `--check` release/CI gate, README/RELEASING/CLAUDE docs | Phase 5 | PENDING | CR-SAN-017 | — |
| [CR-SAN-019](CR-SAN-019-pi-tombstone-and-smoke.md) | Pi audit fixes: **tombstone-aware `unregister`** (exit 3 → success result, not thrown; Option A) + **real-binary smoke test** (`--version` + send→fetch round-trip). Audit #1/#7 valid; #2/#3/#4/#5/#6 rejected w/ citations | Phase 4 | PENDING | CR-SAN-013, CR-SAN-016 | — |

Design contracts: [PRD-mcp-server](../research/PRD-mcp-server.md) · [PRD-distribution](../research/PRD-distribution.md) · [PRD-pi-extension](../research/PRD-pi-extension.md) · [PRD-db-migration](../research/PRD-db-migration.md)
Design notes: [DN-windows-notifier](../research/DN-windows-notifier.md) · [DN-pi-wake](../research/DN-pi-wake.md) (Pi wake spike — RESOLVED: native injection)
PyPI distribution name: **`sandesh-relay`** (`sandesh` is taken; import package + CLI stay `sandesh`).
Pi integration: see **[PRD-pi-extension](../research/PRD-pi-extension.md)** — a native Pi *extension* (not MCP); its CRs (scaffold/verbs, wake-spike, packaging) spin from that PRD.
Phase 4 (Pi) — monorepo TS subfolder `integrations/pi/` (**bun** + TypeScript, `bun test`), driven by the
**`bun-*` agents** via **`bun-crucible.py`**. CR-SAN-013 (verbs) → CR-SAN-014 (native wake, design W1) → CR-SAN-015 (packaging).
Phase 5 (schema evolution) — design contract: **[PRD-db-migration](../research/PRD-db-migration.md)**
(yoyo runner + `[migrate]` extra + derived JSON snapshot; CLI/installer only, no MCP/Pi).
CR-SAN-012 (core `message.status` retirement) is **no longer a standalone CR — folded into CR-SAN-017**
as the engine's first real migration / proving case (the `0002` 12-step rebuild). CR-SAN-007 superseded.

## Canonical statuses
`PENDING` / `IN_PROGRESS` / `COMPLETED` / `SUPERSEDED` / `DEFERRED`
