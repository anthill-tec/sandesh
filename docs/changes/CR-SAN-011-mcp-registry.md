# CR-SAN-011 — Official MCP Registry listing (`server.json`)

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-008 (package), CR-SAN-010 (PyPI publish — the registry verifies the package)
**Labels:** phase-3, distribution, mcp-registry
**Phase:** Phase 3
**Design reference:** docs/research/PRD-distribution.md §5, D7

## Context

List Sandesh's MCP server on the **official MCP Registry** (`registry.modelcontextprotocol.io`)
so MCP-aware clients/aggregators (Claude Code, Cursor, Glama, PulseMCP, mcp.so, …) can discover
it. The registry is the canonical catalog most directories index from; the listing points at the
already-published PyPI distribution **`sandesh-relay`** and the stdio server entry (`sandesh-mcp`).

Out of scope: Smithery (hosted/HTTP — wrong fit for a local per-user server, PRD §6) and the Pi
integration (its own design — `docs/research/PRD-pi-extension.md`).

## Scope

### §S1 — `server.json`
- Generate with `mcp-publisher init`, then edit. Commit it at the repo root.
- **`name`: `io.github.anthill-tec/sandesh`** — the `io.github.*` namespace authenticates via
  GitHub (the repo owner/org), which we have.
- Description conveys what Sandesh is + the **wake caveat** (the MCP server exposes the *verbs*;
  the `notify` wake is a separate background process, not a tool — PRD-mcp-server §6).
- Package block: the **PyPI** distribution `sandesh-relay` and the stdio run via the
  `sandesh-mcp` console script (the launch a client uses, e.g. `uvx --from 'sandesh-relay[mcp]'
  sandesh-mcp`). Confirm the exact `server.json` package/transport schema against the registry's
  current `server-json` reference at implementation time.

### §S2 — Package-ownership verification
- The registry verifies the underlying package matches the server metadata. For npm this is an
  `mcpName` field in `package.json`; **for PyPI, add the registry's required ownership marker**
  (the PyPI equivalent — confirm the exact mechanism in the registry's "official-registry
  requirements" / PyPI section at impl time, e.g. a `mcp-name` marker in project metadata or a
  PyPI attestation). This couples back to CR-SAN-008's `pyproject.toml` (the marker lives there).

### §S3 — Publish flow
- `mcp-publisher login github` → `mcp-publisher publish --dry-run` (validate) → `mcp-publisher
  publish`. Decide manual vs CI (a CI step after the PyPI publish on `release: published` is an
  option, but registry auth/credentials must be handled — keep manual unless trivially automatable).

### §S4 — Docs
- README/`instructions`: a short "Discover via the MCP Registry" note + that the listing is the
  server only (wake is separate).

## Acceptance criteria

- [ ] **AC1** — `server.json` exists at the repo root, `name = "io.github.anthill-tec/sandesh"`,
      and `mcp-publisher publish --dry-run` validates it.
- [ ] **AC2** — `server.json` declares the **PyPI** package `sandesh-relay` and the stdio run via
      the `sandesh-mcp` entry (matching how a client launches it).
- [ ] **AC3** — the package carries the registry's PyPI ownership marker (in `pyproject.toml`)
      so the registry verifies the package belongs to this server.
- [ ] **AC4** — `mcp-publisher publish` succeeds and the server is listed (or, if gated on
      credentials, the dry-run validates and the publish step is documented for the maintainer).
- [ ] **AC5** — the listing description states the wake (`notify`) is NOT an MCP tool (separate
      background process).
- [ ] **AC6** — README documents discovery/install via the registry.

## Estimated size
Small: one `server.json` + a pyproject marker + a publish step + a README note. Most effort is
confirming the current registry schema + PyPI verification mechanism.

## Risks / open questions
- **Registry schema churn** — `server.json` format + the PyPI ownership-verification mechanism
  are evolving; verify against the live registry docs at implementation time (don't assume).
- Publishing requires GitHub auth as an `anthill-tec` member; CI automation needs a credential path.
- The `server.json` run command must exactly match a client's launch (uvx/console script) — test
  with at least one client (Claude Code) after listing.

## Non-goals
- Smithery / hosted marketplaces (PRD §6 — local-server mismatch).
- The Pi integration (see `docs/research/PRD-pi-extension.md`).
- HTTP/SSE transport.
