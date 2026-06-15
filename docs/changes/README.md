# CR Queue — Sandesh

Single source of truth for change requests. Pick the next `PENDING` CR by phase + dependencies.

| CR | Title | Phase | Status | Depends on | Shipped |
|---|---|---|---|---|---|
| [CR-SAN-001](CR-SAN-001-mcp-server.md) | MCP server foundation (venv+wrapper) & dependency isolation | Wave 2 | COMPLETED | — | 2026-06-06 |
| [CR-SAN-002](CR-SAN-002-read-tools.md) | MCP read/query tools | Wave 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-003](CR-SAN-003-mutating-tools.md) | MCP mutating tools & error mapping | Wave 2 | COMPLETED | CR-SAN-001 | 2026-06-06 |
| [CR-SAN-004](CR-SAN-004-e2e-smoke.md) | MCP E2E: protocol + real-subprocess stdio smoke tests | Wave 2 | COMPLETED | CR-SAN-001..003 | 2026-06-06 |
| [CR-SAN-005](CR-SAN-005-retire-status.md) | Retire `status`/disposition tool: remove `sandesh_actioned` (10→9); lock no `resolves`/`reply_all` (PRD §10/D7) | Wave 2 | COMPLETED | CR-SAN-001..004 | 2026-06-07 |
| [CR-SAN-006](CR-SAN-006-docstrings-instructions.md) | Docstring & usability enrichment + server `instructions` + `sandesh://usage` resource (PRD §10) | Wave 2 | COMPLETED | CR-SAN-005 | 2026-06-07 |
| CR-SAN-007 | Install `$PATH` hardening | Wave 2 | SUPERSEDED by CR-SAN-008 | CR-SAN-001 | — |
| [CR-SAN-008](CR-SAN-008-packaging.md) | Packaging: `pyproject.toml` (hatchling + hatch-vcs), `sandesh/` package, console scripts, `[mcp]` extra, bundled usage doc | Wave 3 | COMPLETED | CR-SAN-001..006 | 2026-06-07 |
| [CR-SAN-009](CR-SAN-009-aur-pkgbuild.md) | AUR PKGBUILD (secondary, Arch) — derives from the package | Wave 3 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-010](CR-SAN-010-pypi-publish.md) | PyPI release (OIDC trusted publishing) — publishes `sandesh-relay`; enables uv/pipx/pipxu install | Wave 3 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-011](CR-SAN-011-mcp-registry.md) | Official MCP Registry listing (`server.json`, `io.github.anthill-tec/sandesh`) — discoverable by MCP clients/directories | Wave 3 | COMPLETED | CR-SAN-010 | 2026-06-07 |
| [CR-SAN-013](CR-SAN-013-pi-verbs-extension.md) | Pi extension: scaffold + verb tools (`integrations/pi/`, TS, `registerTool` over the `sandesh` CLI) | Wave 4 | COMPLETED | CR-SAN-008 | 2026-06-07 |
| [CR-SAN-014](CR-SAN-014-pi-native-wake.md) | Pi native wake (background watcher → `sendUserMessage`; design W1 from DN-pi-wake) | Wave 4 | COMPLETED | CR-SAN-013 | 2026-06-07 |
| [CR-SAN-015](CR-SAN-015-pi-package-listing.md) | Pi extension packaging/listing — **npm** publish (`npm:@anthill-tec/sandesh-pi`) + pi.dev/packages gallery (git: can't target a subdir) | Wave 4 | COMPLETED | CR-SAN-013, CR-SAN-014 | 2026-06-07 |
| [CR-SAN-016](CR-SAN-016-pi-error-handling-promptsnippet.md) | Pi extension review fixes: tool `execute` **throws** on CLI failure (sets `isError`) + `promptSnippet`/`promptGuidelines` on all 9 tools (review #1/#2/#5; #3/#4 rejected) | Wave 4 | COMPLETED | CR-SAN-013 | 2026-06-07 |
| [CR-SAN-017](CR-SAN-017-migration-engine.md) | DB schema **migration engine** + first real migration: `[migrate]` extra (yoyo + jsonschema), `sandesh migrate` CLI (apply/check/status/rollback/dump-schema/diff), baseline `0001` + adoption glue, derived `current-schema.json`; **proven by** the `message.status` drop (`0002` 12-step rebuild — folds deferred CR-SAN-012). CLI/installer only, no MCP/Pi | Wave 5 | COMPLETED | CR-SAN-008 | 2026-06-10 |
| [CR-SAN-018](CR-SAN-018-migration-installer-integration.md) | Migration **installer & CI integration**: `install.sh` runs `migrate --all` (tolerates missing extra), `--check` release/CI gate, README/RELEASING/CLAUDE docs | Wave 5 | COMPLETED | CR-SAN-017 | 2026-06-10 |
| [CR-SAN-019](CR-SAN-019-pi-tombstone-and-smoke.md) | Pi audit fixes: **tombstone-aware `unregister`** (exit 3 → success result, not thrown; Option A) + **real-binary smoke test** (`--version` + send→fetch round-trip). Audit #1/#7 valid; #2/#3/#4/#5/#6 rejected w/ citations | Wave 4 | COMPLETED | CR-SAN-013, CR-SAN-016 | 2026-06-08 |
| [CR-SAN-020](CR-SAN-020-pypi-packaging-hardening.md) | PyPI metadata hardening: `license-files`, pin hatchling/hatch-vcs lower bounds, granular Python classifiers. Packaging audit P2/P3; **py.typed rejected** | Wave 4 | COMPLETED | CR-SAN-008 | 2026-06-09 |
| [CR-SAN-021](CR-SAN-021-npm-pi-packaging-release-integrity.md) | npm/Pi hardening + release integrity: `publish-npm.yml` CI, `prepublishOnly`, `engines`, **version-sync gate** (package.json ↔ server.json ×2 ↔ git tag). Packaging audit P1/P2/P3; **deps-move + exports rejected** w/ citations | Wave 4 | COMPLETED | CR-SAN-015, CR-SAN-011 | 2026-06-09 |
| [CR-SAN-022](CR-SAN-022-global-db-tracker-consolidation.md) | **Global DB + project tracker + consolidation**: single `sandesh.db` (WAL), `project` table (`active\|archived\|tombstoned`), `address.project`, `setup` enrolls (tombstoned ids retired — O1), explicit project scoping, legacy stores consolidated (id remap, `.pre-global`), `migrate` global single-target (`--project` removed, breaking) | Wave 6 | COMPLETED | CR-SAN-017, CR-SAN-018 | 2026-06-11 |
| [CR-SAN-023](CR-SAN-023-cross-project-messaging.md) | **Cross-project messaging + access control**: grant-gated sends (D11 — **admin grant per project, inherited, revoked project-wide**; CLI-only `grant`/`revoke`); **super-admin: dedicated single-row `admin` table + install-time `$SANDESH_ADMIN` assignment**; tracker-state errors on send/register; `projects` listing w/ grant flag; cross-project To-wakes/Cc-silent | Wave 6 | COMPLETED | CR-SAN-022 | 2026-06-11 |
| [CR-SAN-024](CR-SAN-024-project-lifecycle-verbs.md) | **Lifecycle verbs**: `archive` (read-only, nothing deleted, watchers evicted, reversible) / `unarchive` / `tombstone` (archived-only; selective internal purge + body-folder delete; hidden from reads incl. wake, `thread` warns); confirm/`--yes`/`--dry-run` previews/`--force`; super-admin (from 023) sole tombstoner; grant/revoke require active, transitions never touch grant columns | Wave 6 | COMPLETED | CR-SAN-023 | 2026-06-11 |
| [CR-SAN-025](CR-SAN-025-mcp-surface-update.md) | **MCP surface**: `sandesh_archive`+`sandesh_unarchive` (9→11 tools; tombstone/grant/revoke NEVER exposed), `project_id` optional where derivable, docstrings/`instructions`/usage + stdio E2E cross-project scenario | Wave 6 | COMPLETED | CR-SAN-024 | 2026-06-12 |
| [CR-SAN-026](CR-SAN-026-inbox-filters.md) | **Inbox/fetch filters (lib+CLI)**: composable server-side filters — `sender_project` headline (the cross-project proxy stream) + sender/kind/since/until/subject; filtered fetch marks only the subset; wake path untouched | Wave 7 | COMPLETED | — | 2026-06-12 |
| [CR-SAN-027](CR-SAN-027-fts-search-engine.md) | **FTS5 search engine**: `0005-message-fts` (+dump excludes the FTS family), send-time indexing, `search()` (bm25+snippets, own-mailbox, paginated limit/offset/total) + `reindex` (explicit CLI + installer + lazy empty-index path) | Wave 7 | COMPLETED | CR-SAN-026 | 2026-06-12 |
| [CR-SAN-028](CR-SAN-028-mcp-search-surface.md) | **MCP search surface**: `sandesh_inbox`/`sandesh_fetch` filter params + `sandesh_search` (11→12 tools, readOnlyHint, paginated; NO reindex tool), instructions/usage, stdio E2E | Wave 7 | COMPLETED | CR-SAN-027 | 2026-06-12 |
| [CR-SAN-029](CR-SAN-029-projects-listing-tombstoned.md) | **`projects --all`**: include tombstoned rows (permanent markers) in the 3-column listing; default output unchanged | pre-v0.2.0 | COMPLETED | — | 2026-06-12 |
| [CR-SAN-030](CR-SAN-030-pre-release-cleanup-sweep.md) | **Cleanup sweep**: redundant in-function imports, `_tombstone_guards`/`search` docstring precision, MCP `reindexed`-flag + `sender`/`since`/`until` coverage, install.sh admin-block heredoc, test_install ResourceWarning | pre-v0.2.0 | COMPLETED | — | 2026-06-12 |
| [CR-SAN-031](CR-SAN-031-pi-wake-followup.md) | **Pi wake hardening**: the wake call gains `deliverAs: "followUp"` (mid-turn mail was silently lost + hot-spin; PE11 source-verified) + throw-survival guard | Wave 8 | COMPLETED | — | 2026-06-13 |
| [CR-SAN-032](CR-SAN-032-pi-surface-catchup.md) | **Pi surface catch-up**: 9→12 tools (archive/unarchive/search), 6 inbox/fetch filter params, `sandesh` ≥0.2.0 session gate, Wave-6 error-string pins, promptSnippet/docs refresh, pkg 0.2.0 | Wave 8 | COMPLETED | CR-SAN-031 | 2026-06-13 |
| [CR-SAN-033](CR-SAN-033-consolidate-skip-non-stores.md) | **Hotfix 0.2.1**: `consolidate` skips non-store `sandesh.db` files (yoyo-stub/corrupt debris crashed the v0.2.0 installer live) with a CLI notice; real stores unaffected | hotfix 0.2.1 | COMPLETED | — | 2026-06-13 |
| [CR-SAN-034](CR-SAN-034-release-pipeline-branch-gated.md) | **Branch-gated release pipeline**: push-to-`main` (new `v*` tag) auto-creates a GitHub Release (PAT) → `publish-pypi`; `publish-pypi`/`publish-npm` guard on `v*`-tag-reachable-from-`main`; `local_scheme=no-local-version` (untagged ⇒ upload-valid `X.Y.Z.devN` for TestPyPI checkpoints); `scripts/release.sh` (checkpoint/finish/status, branch-gated); RELEASING.md rewrite | hotfix 0.2.2 | COMPLETED | — | 2026-06-15 |
| [CR-SAN-035](CR-SAN-035-install-uninstall-mode.md) | **`install.sh --uninstall [--purge]`**: installer self-removal — arg dispatch (default install unchanged), remove symlinks + venv; `--purge` also removes the data store; `-h/--help` usage; prints `claude mcp remove` reminder; idempotent + scoping-safe (teardown half of the provisioning-lifecycle PRD; uninstall matrix → CR-039) | provisioning-lifecycle (0.3.0) | COMPLETED | — | 2026-06-15 |
| [CR-SAN-036](CR-SAN-036-provisioning-core.md) | **Provisioning core**: `sandesh init` CLI (migrate→consolidate→reindex→admin; idempotent; `--admin`/`--yes`; CLI-only, never MCP) + **lazy auto-migrate on store open** (apply if `[migrate]`, else install-method-aware actionable error, no self-pip) | provisioning-lifecycle (0.3.0) | PENDING | — | — |
| [CR-SAN-037](CR-SAN-037-install-surface-choice.md) | **install.sh surface choice**: interactive Claude/Pi/both/none prompt (Pi ⇒ no `mcp`; non-interactive override) + delegate provisioning to `sandesh init` + **mandatory migrate when an existing `sandesh.db` is detected** (fail loudly, no silent skip) | provisioning-lifecycle (0.3.0) | PENDING | CR-SAN-036 | — |
| [CR-SAN-038](CR-SAN-038-pi-uvx-and-provision-nudge.md) | **Pi surface**: CLI via `uvx --from 'sandesh-relay[migrate]'` (no `mcp`) when local CLI absent + session-gate **nudge to `sandesh init`** on an unprovisioned store; no init/admin/migrate tool; `[migrate]` error passthrough (bun) | provisioning-lifecycle (0.3.0) | PENDING | CR-SAN-036 | — |
| [CR-SAN-039](CR-SAN-039-docs-restructure.md) | **Docs restructure**: slim README + per-route×surface install guide (`docs/INSTALL.md`/`docs/install/`, install→init→manage→uninstall) + **AUR removed from README** + uninstall matrix | provisioning-lifecycle (0.3.0) | PENDING | CR-SAN-036..038, CR-SAN-035 | — |

Design contracts: [PRD-mcp-server](../research/PRD-mcp-server.md) · [PRD-distribution](../research/PRD-distribution.md) · [PRD-pi-extension](../research/PRD-pi-extension.md) · [PRD-db-migration](../research/PRD-db-migration.md) · [PRD-global-store](../research/PRD-global-store.md)
Design notes: [DN-windows-notifier](../research/DN-windows-notifier.md) · [DN-pi-wake](../research/DN-pi-wake.md) (Pi wake spike — RESOLVED: native injection)
PyPI distribution name: **`sandesh-relay`** (`sandesh` is taken; import package + CLI stay `sandesh`).
Pi integration: see **[PRD-pi-extension](../research/PRD-pi-extension.md)** — a native Pi *extension* (not MCP); its CRs (scaffold/verbs, wake-spike, packaging) spin from that PRD.
Wave 4 (Pi) — monorepo TS subfolder `integrations/pi/` (**bun** + TypeScript, `bun test`), driven by the
**`bun-*` agents** via **`bun-crucible.py`**. CR-SAN-013 (verbs) → CR-SAN-014 (native wake, design W1) → CR-SAN-015 (packaging).
Wave 5 (schema evolution) — design contract: **[PRD-db-migration](../research/PRD-db-migration.md)**
(yoyo runner + `[migrate]` extra + derived JSON snapshot; CLI/installer only, no MCP/Pi).
CR-SAN-012 (core `message.status` retirement) is **no longer a standalone CR — folded into CR-SAN-017**
as the engine's first real migration / proving case (the `0002` 12-step rebuild). CR-SAN-007 superseded.
Wave 6 (global store) — design contract: **[PRD-global-store](../research/PRD-global-store.md)** (AGREED
2026-06-11; supersedes PRD-project-lifecycle): single global DB + project tracker, **cross-project messaging
behind a per-project admin grant**, `archive → tombstone` lifecycle, **super-admin assigned at install**.
Strict order CR-SAN-022 → 023 → 024 → 025 — **Wave 6 COMPLETE (2026-06-12)**. Scheduling notes: the
super-admin storage + installer assignment moved from CR-SAN-024 into CR-SAN-023 at 023's gap-analysis
(023 is the first reader; 024 consumes the row); `migrate --project` removal (022) was a user-approved
breaking change.
Wave 7 (inbox search) — design contract: **[PRD-inbox-search](../research/PRD-inbox-search.md)**
(AGREED 2026-06-12): sender-project proxy stream + composable filters, FTS5 keyword search (own-mailbox,
paginated), explicit+lazy reindex; semantic search assessed/deferred. Strict order CR-SAN-026 → 027 →
028 (breakdown user-approved 2026-06-12) — **Wave 7 COMPLETE (2026-06-12)**.
Pre-v0.2.0 housekeeping (filed at the Wave-7-close SCRUM, 2026-06-12): CR-SAN-029 + CR-SAN-030 (the
Waves-6/7 VERIFY-nit backlog; install nits routed into the sweep, `sender_project` SQL-join optimization
deferred-at-volume). Then: **Wave 8 — Pi extension catch-up** → the
**v0.2.0 release + local reinstall**.
Wave 8 (Pi catch-up) — design contract: **[PRD-pi-extension](../research/PRD-pi-extension.md) §8**
(AGREED 2026-06-13, PE6–PE11; amended in place per owner choice — full 12-tool parity, hard
≥0.2.0 CLI gate, wake `followUp` hardening from the opensrc source re-verification). Strict order
CR-SAN-031 → 032 (breakdown user-approved 2026-06-13). bun stack (`integrations/pi/`, `bun-*`
agents).
Release engineering: CR-SAN-034 (branch-gated release pipeline) shipped in **0.2.2** (first real
PyPI publish, 2026-06-15).
**Provisioning lifecycle (0.3.0)** — design contract: **[PRD-provisioning-lifecycle](../research/PRD-provisioning-lifecycle.md)**
(APPROVED 2026-06-15). Decomposition (owner-approved 2026-06-15): CR-SAN-035 (teardown) → 036
(provisioning core: `sandesh init` + lazy auto-migrate) → 037 (install.sh surface-choice + delegate
+ mandatory-migrate) → 038 (Pi uvx + provision nudge, bun) → 039 (docs restructure). One CLI core,
two interchangeable surfaces (`[mcp]`=Claude, Pi extension=Pi); install prompts for surface(s).

## Canonical statuses
`PENDING` / `IN_PROGRESS` / `COMPLETED` / `SUPERSEDED` / `DEFERRED`
