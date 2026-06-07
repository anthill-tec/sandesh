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
| [CR-SAN-008](CR-SAN-008-packaging.md) | Packaging: `pyproject.toml`, `sandesh/` package, console scripts, `[mcp]` extra | Phase 3 | PENDING | CR-SAN-001..004 | — |
| CR-SAN-009 | AUR PKGBUILD (secondary, Arch) — derives from the package | Phase 3 | PENDING | CR-SAN-008 | — |
| CR-SAN-010 | PyPI release (OIDC trusted publishing) — publishes `sandesh-relay`; enables uv/pipx/pipxu install | Phase 3 | PENDING | CR-SAN-008 | — |
| [CR-SAN-011](CR-SAN-011-mcp-registry.md) | Official MCP Registry listing (`server.json`, `io.github.anthill-tec/sandesh`) — discoverable by MCP clients/directories | Phase 3 | PENDING | CR-SAN-010 | — |

Design contracts: [PRD-mcp-server](../research/PRD-mcp-server.md) · [PRD-distribution](../research/PRD-distribution.md) · [PRD-pi-extension](../research/PRD-pi-extension.md)
Design notes: [DN-windows-notifier](../research/DN-windows-notifier.md)
PyPI distribution name: **`sandesh-relay`** (`sandesh` is taken; import package + CLI stay `sandesh`).
Pi integration: see **[PRD-pi-extension](../research/PRD-pi-extension.md)** — a native Pi *extension* (not MCP); its CRs (scaffold/verbs, wake-spike, packaging) spin from that PRD.
(CR-SAN-009/010 specs to be authored when scheduled; defined in the PRDs.)

## Canonical statuses
`PENDING` / `IN_PROGRESS` / `COMPLETED` / `SUPERSEDED` / `DEFERRED`
