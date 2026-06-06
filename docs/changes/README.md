# CR Queue — Sandesh

Single source of truth for change requests. Pick the next `PENDING` CR by phase + dependencies.

| CR | Title | Phase | Status | Depends on | Shipped |
|---|---|---|---|---|---|
| [CR-SAN-001](CR-SAN-001-mcp-server.md) | MCP server foundation (venv+wrapper) & dependency isolation | Phase 2 | COMPLETED | — | 2026-06-06 |
| [CR-SAN-002](CR-SAN-002-read-tools.md) | MCP read/query tools | Phase 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-003](CR-SAN-003-mutating-tools.md) | MCP mutating tools & error mapping | Phase 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-004](CR-SAN-004-e2e-smoke.md) | MCP E2E: protocol + real-subprocess stdio smoke tests | Phase 2 | COMPLETED | CR-SAN-001..003 | 2026-06-06 |

Design contract: [PRD-mcp-server](../research/PRD-mcp-server.md)

## Canonical statuses
`PENDING` / `IN_PROGRESS` / `COMPLETED` / `SUPERSEDED` / `DEFERRED`
