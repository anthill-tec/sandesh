# CR-SAN-011 — Official MCP Registry listing (`server.json`)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-011)
**Priority:** Medium
**Depends on:** CR-SAN-008 (package), CR-SAN-010 (PyPI publish — the registry verifies the package)
**Labels:** wave-3, distribution, mcp-registry
**Wave:** Wave 3
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
- **`$schema`: pin the current registry schema** `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
  (verified current 2026-06; re-confirm/upgrade the date at impl time).
- **`name`: `io.github.anthill-tec/sandesh`** — the `io.github.*` namespace authenticates via
  GitHub (the repo owner/org), which we have.
- Description conveys what Sandesh is + the **wake caveat** (the MCP server exposes the *verbs*;
  the `notify` wake is a separate background process, not a tool — PRD-mcp-server §6).
- Package block (field names verified against the pinned schema): `registryType: "pypi"`,
  `registryBaseUrl: "https://pypi.org"`, `identifier: "sandesh-relay"`, `version`,
  `runtimeHint: "uvx"`, `transport: { "type": "stdio" }`, and **`runtimeArguments`** to express the
  extra + script — i.e. the client invocation `uvx --from 'sandesh-relay[mcp]' sandesh-mcp`
  (e.g. `runtimeArguments: [{type: named, name: "--from", value: "sandesh-relay[mcp]"}, {type: positional, value: "sandesh-mcp"}]` — confirm the exact argument-object shape against the schema in GREEN).

### §S2 — Package-ownership verification (marker in **README**, not pyproject)
**Verified mechanism (2026-06):** the registry fetches `https://pypi.org/pypi/sandesh-relay/json`
and checks the package's **README (the PyPI long-description)** for a line
**`mcp-name: io.github.anthill-tec/sandesh`** that matches `server.json`'s `name`. (For npm it's an
`mcpName` field in `package.json`; **for PyPI it is the README marker** — there is *no* pyproject
field for this.)
- Add `mcp-name: io.github.anthill-tec/sandesh` to **`README.md`** — as an HTML comment
  (`<!-- mcp-name: io.github.anthill-tec/sandesh -->`) so it doesn't render. Because
  `pyproject.toml` sets `readme = "README.md"`, the marker ships in the PyPI long-description.
- **Corrects DRIFT-1:** the original spec said the marker lives in `pyproject.toml` — it does not.

### §S3 — Publish flow (manual; requires the package live on PyPI)
- The registry validates ownership against the **live PyPI package**, so `mcp-publisher publish`
  can only succeed **after** the real `v0.1.0` PyPI publish (CR-SAN-010's maintainer step) — the
  README marker must be in the *published* long-description.
- Flow (maintainer, post-`v0.1.0`): `mcp-publisher login github` → `mcp-publisher publish`
  (`--dry-run` first to validate). **Manual** (decision): it needs the package live + GitHub auth
  as an `anthill-tec` member; not worth CI automation for a low-frequency action.
- **In CI / this CR we validate `server.json` structurally** against the pinned JSON-schema (a
  test), independent of the `mcp-publisher` binary (which needs auth and isn't assumable locally).

### §S4 — Docs
- README/`instructions`: a short "Discover via the MCP Registry" note + that the listing is the
  server only (wake is separate).

## Acceptance criteria

- [x] **AC1** — `server.json` exists at the repo root, is valid JSON, declares the pinned
      `$schema` (2025-12-11), and `name = "io.github.anthill-tec/sandesh"` (asserted by a test
      parsing `server.json`).
- [x] **AC2** — `server.json` declares the **PyPI** package (`registryType: pypi`,
      `identifier: sandesh-relay`) with stdio transport and the run matching the `sandesh-mcp`
      console script / `uvx --from 'sandesh-relay[mcp]' sandesh-mcp` (asserted by parsing
      `server.json`).
- [x] **AC3** — **`README.md` contains the ownership marker** `mcp-name: io.github.anthill-tec/sandesh`
      (an HTML comment is fine) and it **matches** `server.json`'s `name` (asserted by a test
      reading both). The marker reaches PyPI via `readme = "README.md"`. (NOT a pyproject field.)
- [x] **AC4** — `server.json` validates against the pinned JSON-schema (a CI test;
      `jsonschema`-validate the file, or a structural check if the lib is unavailable — note which).
      The actual `mcp-publisher publish` is documented in RELEASING.md as a manual maintainer step
      gated on the live PyPI package (it can't run in this CR's CI).
- [x] **AC5** — the `server.json` description states the wake (`notify`) is NOT an MCP tool
      (separate background process) — asserted by a substring on the description.
- [x] **AC6** — README documents discovery via the registry; RELEASING.md documents the
      `mcp-publisher` publish step (post-`v0.1.0`, manual).

## Gap-analysis findings (2026-06-07) — verdict SPEC_UPDATE applied; now READY

Verified against the live registry docs (web, 2026-06):
- **DRIFT-1 (Dim 1/3 → §S2/AC3, FIXED):** the PyPI ownership marker is **not** a pyproject field —
  the registry reads `mcp-name: <name>` from the **README (PyPI long-description)**. Marker moved to
  `README.md` (HTML comment), reaching PyPI via `readme = "README.md"`.
- **DRIFT-2 (§S1/AC1, FIXED):** pin `$schema` = `…/schemas/2025-12-11/server.schema.json`.
- **DRIFT-3 (§S3/AC4, FIXED):** registry publish validates against the **live** PyPI package, so
  `mcp-publisher publish` is a **manual maintainer step after the `v0.1.0` PyPI publish**; CI here
  validates `server.json` against the JSON-schema only (the `mcp-publisher` binary needs GitHub auth
  and isn't assumable locally).
- **Dim 2 (Spec vs Code):** `pyproject.toml` `readme = "README.md"` ✓ (marker reaches PyPI); the
  run command matches the shipped `sandesh-mcp` console script ✓.
- **Decision:** registry publish = **manual** (low-frequency, needs auth + live package).
- **Sequencing:** this CR ships `server.json` + README marker + docs + a schema-validation test;
  the live registry publish happens once `v0.1.0` is on PyPI (a maintainer action).

## Estimated size
Small: one `server.json` + a README marker + a schema-validation test + README/RELEASING notes.
Most effort was confirming the current registry schema + PyPI verification mechanism (done).

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
- Running the actual `mcp-publisher publish` (a manual maintainer action after the `v0.1.0` PyPI
  publish; this CR ships `server.json` + the README marker + the schema-validation test + docs).

## Implementation Notes (2026-06-07)

One cycle (C0) + a docs step, agent-dispatched, then VERIFY → pre-merge. **No `sandesh/` code
changed** (`git diff develop..HEAD -- sandesh/` empty).

- **C0** — RED (`2576576`): `tests/test_server_json.py` — contract assertions for `server.json`
  (schema/name/packages/description) + README marker + jsonschema validation. GREEN (`fc5e3d8`):
  `server.json` (`io.github.anthill-tec/sandesh`, `$schema` 2025-12-11, PyPI `sandesh-relay`,
  `runtimeHint: uvx`, stdio, `runtimeArguments` for `--from 'sandesh-relay[mcp]' sandesh-mcp`) +
  `<!-- mcp-name: io.github.anthill-tec/sandesh -->` in README. Included an **orchestrator-approved
  test-only fix**: the RED marker-match regex `[^\s\-\->]+` excluded hyphens (truncated the name);
  corrected to `[\w./-]+` (stricter full-name equality, not weaker).
- **Docs**: README "Discover via the MCP Registry" note (server only; wake separate) + RELEASING.md
  `mcp-publisher login/publish` step (post-`v0.1.0`, ownership via the README marker).
- **VERIFY** (`CR-SAN-011-VERIFY`): all AC1–AC6 PASS, 0 blocking; **independently validated
  `server.json` against the real fetched 2025-12-11 schema (jsonschema.validate → PASS)**; no prod
  code changed. 1 suggestion (vendor the schema for self-contained CI — future).
- **Pre-merge gate**: no prod Python changed; stdlib baseline green; **full venv suite 204/204
  green** (incl. 20 server.json tests).
- **Remaining maintainer action:** the live registry publish (`mcp-publisher publish`) after the
  `v0.1.0` PyPI release — documented in RELEASING.md, gated on the live package.
