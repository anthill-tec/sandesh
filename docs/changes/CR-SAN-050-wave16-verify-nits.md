# CR-SAN-050 — Wave 16 VERIFY nits (047/048/049 follow-through before 0.4.0)

**Status:** PENDING
**Priority:** Medium (chore — closes findings that should have been fixed inside their CRs)
**Depends on:** CR-SAN-047, CR-SAN-048, CR-SAN-049
**Labels:** chore, tests, axi, python, bun
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** the VERIFY reports of CR-SAN-047 (cycle 209), CR-SAN-049 (cycle 212) and CR-SAN-048
(cycle 218) — items the orchestrator wrongly routed to the deferred register instead of a FIX cycle.

## Context
Each VERIFY approved with SHOULD-FIX/SUGGESTION items that are in-scope and small. They are fixed here, as
one chore, before the 0.4.0 release boundary so the release ships without known nits.

## Scope
- **§S1 (047 SHOULD-FIX 1 — AC2 breadth, python).** `tests/test_axi_verbs.py`: a table-driven test that, for
  EVERY verb `cli.py` routes through `AXI_FN`, runs the verb once in `toon` and once in `json` on the fixture
  store and asserts `_toon.decode(toon_out) == json.loads(json_out)` (the literal AC2 oracle, per verb).
- **§S2 (047 SHOULD-FIX 2, python).** `cli.py`: replace the repeated `con = sdb.connect(); try … finally
  con.close()` blocks in `axi_search`/`axi_projects`/`_axi_xproj` with the `_ctx()`-tracked connection seam
  already used elsewhere (no behaviour change).
- **§S3 (047 SUGGESTION, python).** `axi_archive`/`axi_unarchive` `--dry-run` envelopes carry the preview
  fields (`project`, `state`, `evicted[N]`, `dry_run: true`, and for archive the counts the human preview
  prints) instead of `{}`.
- **§S4 (049 SUGGESTION 2, python).** `tests/test_store_guard.py`: remove the dead `if False` branch in the
  AC4b subprocess snippet; (049 SUGGESTION 1) the CR-SAN-049 spec's stale "`tests/__init__.py`" phrase is
  corrected in place.
- **§S5 (048 SUGGESTION 1, bun).** `sandesh_notify_status`/`sandesh_notify_start` gain the `fields` knob
  (P2 widening) mapping to the full `WatcherStatus` columns `address,project,running,pid,startedAt,lastExit,
  lastIds,timeoutExits`; default stays `{address,running,lastExit}`. (048 SUGGESTION 3) rename
  `__resetWakeState` → `resetExtensionState` (still exported; the two test importers updated).

## Acceptance criteria
- **AC1** — `test_axi_verbs.py::AC2 round-trip` covers every `AXI_FN` verb (the test derives the list from
  `cli.AXI_FN` so a new verb cannot be missed) and passes.
- **AC2** — `grep -c "con.close()" sandesh/cli.py` decreases by 3 and `git diff` shows no behaviour change
  (all `test_axi_*` + `test_lifecycle_cli` green).
- **AC3** — `archive --dry-run --format toon` decodes with `dry_run: true`, `state: archived`, `evicted[N]`
  (and the purge counts for `tombstone --dry-run`); nothing is written (project state unchanged after).
- **AC4** — no `if False` in `tests/test_store_guard.py`; 8/8 green; the 049 spec no longer says `tests/__init__.py`.
- **AC5** — `sandesh_notify_status({fields:["address","project","running","pid","startedAt","lastExit",
  "lastIds","timeoutExits"]})` returns those eight columns; default returns three; `resetExtensionState` is
  the exported name and `grep -c __resetWakeState src/` is 0; full `bun test` green.

## Estimated size
Small — two test additions, three mechanical refactors, one knob.

## Non-goals
- No new behaviour beyond the listed items; no release-engineering change.
