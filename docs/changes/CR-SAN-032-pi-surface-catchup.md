# CR-SAN-032 — Pi surface catch-up: 12-tool parity, version gate, docs

**Status:** PENDING
**Priority:** High (the wave's main body — Pi reaches the post-Wave-7 surface)
**Depends on:** CR-SAN-031 (wake hardening lands first; this CR's tests build on the loop)
**Labels:** wave-8, pi, parity, bun
**Wave:** Wave 8 (Pi extension catch-up)
**Design reference:** docs/research/PRD-pi-extension.md (PE6, PE7, PE8, PE9, PE10)
**Stack:** TypeScript/bun — `integrations/pi/` (`bun test`)

## Context

The Pi extension registers the Wave-4 9-tool surface over the installed `sandesh` CLI.
Sandesh-core is now at the post-Wave-7 surface (MCP: 12 tools, filters, search). Being a
CLI shim, the Wave-6 semantics already ride the CLI; this CR catches the TOOL SURFACE up
to full parity and gates on the CLI version that carries those semantics.

## Scope

### §S1 — three new tools (PE6): 9 → 12
- `sandesh_archive` — params `project_id` (required), `by` (required), `dry_run`
  (optional bool), `force` (optional bool) → `sandesh archive --project <id> --by <addr>
  [--dry-run] [--force]`. `sandesh_unarchive` — params `project_id`, `by`, `dry_run`
  ONLY (the CLI has no unarchive `--force`; eviction is archive-side) →
  `sandesh unarchive --project <id> --by <addr> [--dry-run]`. Boolean flags emitted only
  when the param is true (house omit-at-default style). Descriptions: Mainline-tier
  reversible lifecycle pair; archived = can't send/receive, reads intact, watchers
  evicted.
- `sandesh_search` — params `recipient` (required), `query` (required), `limit`
  (optional int), `offset` (optional int), `sender_project` (optional), mapping to
  `sandesh search <query> --to <recipient> [--limit N] [--offset N] [--from-project P]`;
  each optional flag emitted ONLY when its param is provided (omit-at-default — the CLI
  defaults, limit 20 / offset 0, then apply).
  Description mirrors the MCP tool: FTS5 syntax, bm25+snippets, own-mailbox boundary,
  pagination, never marks read, lazy-reindex notice passthrough (the CLI prints it).

### §S2 — filter params on inbox/fetch (PE6)
- `sandesh_inbox` and `sandesh_fetch` gain optional `sender`, `sender_project`, `kind`,
  `since`, `until`, `subject_like` → CLI flags
  `--from --from-project --kind --since --until --subject`. `sender_project` described as
  the cross-project proxy-stream filter.

### §S3 — CLI version gate ≥ 0.2.0 (PE7)
- The session-start probe parses `sandesh --version` stdout against
  `^sandesh (\d+)\.(\d+)\.(\d+)` (the CLI emits `sandesh {__version__}`); a version
  below 0.2.0 takes the missing-CLI path: one-time `ctx.ui.notify` warning naming the
  required minimum + upgrade hint, wake loop NOT armed. Unparseable output counts as
  too-old. Tool registration stays static/unblocked.

### §S4 — Wave-6 error-string pins (PE8)
- Tests (mocked `pi.exec` stderr) pin that the existing error contract surfaces the
  tracker/grant refusals verbatim: `project '<id>' is archived`, `project '<id>' is
  tombstoned`, `unknown project '<id>'`, and `cross-project sending not approved for
  project '<id>' — ask the Sandesh admin`. No shim-side handling added (PE9 boundary:
  no tombstone/grant/revoke/admin/reindex tools).

### §S4b — wake-test assertion strengthening (031 VERIFY register item)
- `src/wake.test.ts` AC2 "no infinite spin" test: `notifyCalls.length` assertion tightens
  from `toBeGreaterThanOrEqual(2)` to `toBe(2)` (the comment's stated intent; the
  3-item exec sequence makes >2 impossible).

### §S5 — docs + version (PE10)
- promptSnippets/descriptions gain the proxy-stream + search/pagination story (reuse the
  MCP/usage-scenarios wording). `package.json` version → 0.2.0. (npm publish itself rides
  the core v0.2.0 release — not this CR's gate.)

## Acceptance criteria

- [ ] **AC1 — tool inventory.** The extension registers exactly 12 tools; the new names
      are `sandesh_archive`, `sandesh_unarchive`, `sandesh_search`; no registered tool
      name contains `tombstone`, `grant`, `revoke`, `admin`, or `reindex`.
- [ ] **AC2 — arg mapping.** Each new tool/param builds exactly the CLI argv pinned in
      §S1/§S2 (mock `pi.exec`, assert argv): archive with/without `dry_run`/`force`;
      unarchive with/without `dry_run` (and NO force param in its schema); search with
      no optionals (argv carries no --limit/--offset/--from-project) and with all
      params; inbox/fetch with each filter alone and combined.
- [ ] **AC3 — version gate.** Probe `sandesh 0.1.0` → warning notice naming 0.2.0, wake
      loop not armed; `sandesh 0.2.0` (and `0.3.1`) → armed as today; garbage output →
      treated as too-old; missing CLI path unchanged.
- [ ] **AC4 — error passthrough.** For each §S4 refusal string, a mocked non-zero exec
      makes the tool throw an Error whose message contains the string verbatim.
- [ ] **AC5 — docs markers.** promptSnippets/descriptions contain the proxy-stream and
      search/pagination markers (grep); `package.json` version is `0.2.0`.
- [ ] **AC6 — regression.** Full `bun test` suite green in `integrations/pi/`.
- [ ] **AC7 — wake assertion tightened.** The §S4b test asserts `toBe(2)`; the wake suite
      stays green.

## Estimated size
Medium — three tools + six params + gate + docs, all shim-thin; the AC2 argv matrix is
the bulk of the test work.

## Risks / open questions
- (none open — flag sets, omit-at-default emission, and the version-probe regex were
  pinned at gap-analysis against `cli.py`; the §S4 error strings verified verbatim in
  `sandesh_db.py`.)

## Non-goals
- npm publish (rides the v0.2.0 release); any Sandesh-core change; wake-loop changes
  beyond CR-SAN-031; admin/maintenance tool exposure (never).
