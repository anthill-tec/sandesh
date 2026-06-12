# CR-SAN-029 — `sandesh projects --all`: tombstoned rows in the listing

**Status:** IN_PROGRESS
**Priority:** Low (lifecycle-story completeness)
**Depends on:** —
**Labels:** pre-0.2.0, cli, lifecycle
**Wave:** pre-v0.2.0 housekeeping
**Design reference:** docs/research/PRD-global-store.md (lifecycle states; the tracker row is the permanent marker)

## Context

`sandesh projects` renders the 3-column listing (PROJECT / STATE / CROSS-PROJECT) but
hardcodes `WHERE state != 'tombstoned'` (`cli.py cmd_projects`), so a tombstoned
project's permanent marker row is invisible from the CLI — there is currently no way
to see *that* a project was retired, or when. The lib already supports inclusion
(`list_projects(include_tombstoned=True)`); only the CLI view lacks the switch.

## Scope

- `sandesh projects --all` (store_true, default off) includes tombstoned rows in the
  same 3-column view, `state` column rendering `tombstoned`; row order stays
  `ORDER BY project_id`. The default (no flag) output is byte-identical to today.
- Column values render verbatim — in particular the CROSS-PROJECT cell still reflects
  `xproj_granted_at` (lifecycle transitions never clear the grant columns), so a
  tombstoned row MAY show `✓`; the `tombstoned` state cell is the signal that the
  grant is moot.
- Flag help text: `include tombstoned projects (permanent markers)`.

## Acceptance criteria

- [ ] **AC1 — default unchanged.** With one project per state (active/archived/
      tombstoned): `sandesh projects` exits 0 and its output contains the active and
      archived project ids and does NOT contain the tombstoned id.
- [ ] **AC2 — `--all` includes.** `sandesh projects --all` output contains all three
      ids; the tombstoned project's row contains the word `tombstoned` in the STATE
      column.
- [ ] **AC3 — verbatim grant cell.** A project granted cross-project then archived
      then tombstoned renders `✓` in CROSS-PROJECT under `--all` (grant columns
      untouched by transitions).
- [ ] **AC4 — empty store.** `sandesh projects --all` on a store with no tracker rows
      prints `(no projects set up)` and exits 0.

## Estimated size
Tiny — one flag, one WHERE-clause branch, CLI tests.

## Risks / open questions
- (none)

## Non-goals
- Any MCP exposure of the projects listing; lib `list_projects` signature changes;
  unretiring a tombstoned id (terminal by design).
