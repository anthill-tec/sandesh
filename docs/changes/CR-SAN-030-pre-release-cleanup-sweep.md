# CR-SAN-030 — pre-release cleanup sweep (VERIFY-nit backlog, Waves 6–7)

**Status:** COMPLETED (shipped 2026-06-12 on develop)
**Priority:** Low (hygiene before v0.2.0)
**Depends on:** —
**Labels:** pre-0.2.0, chore, tests, installer
**Wave:** pre-v0.2.0 housekeeping

## Context

VERIFY passes across CR-SAN-024..028 accumulated small non-blocking findings —
redundant imports, two imprecise docstrings, two MCP-layer coverage gaps, and two
installer/test-hygiene items. None affects behavior; this CR clears them in one
sweep so v0.2.0 ships with an empty nit backlog.

## Scope

### §S1 — code nits (`sandesh/sandesh_db.py`)
- Remove the redundant in-function `import sqlite3` in `connect()` (~line 135) and
  `_consolidate_store()` (~line 1062) — the module-level import (line 42) covers both.
- `_tombstone_guards` docstring: state the check ORDER explicitly — state guards run
  BEFORE the super-admin authz check, so unknown/active/already-tombstoned errors
  surface identically for admin and non-admin callers.
- `search()` docstring: name the snippet highlight markers — matched terms are wrapped
  in `[`…`]` and elisions rendered as `…` (the `snippet(message_fts, -1, '[', ']',
  '…', 8)` projection).

### §S2 — MCP-layer coverage (tests only, `tests/test_mcp_search_surface.py`)
- `reindexed`-flag passthrough: seed a store with message rows + body files but an
  EMPTY `message_fts` (raw inserts, the pre-FTS-history fixture shape from
  `test_fts_index.py`), call `sandesh_search` via the FastMCP instance, assert the
  result dict carries `reindexed: True` and the hits are found.
- Direct MCP exercise of the three currently-untested filter params: `sandesh_inbox`
  with `sender=` returns only that sender's rows; with `since=`/`until=` brackets a
  seeded timestamp spread (including a date-only `until` matching a same-day message —
  the end-of-day normalization observed through the MCP layer).
- Strengthen the AC1 state-column assertions in `tests/test_projects_listing_cli.py`:
  anchor each state assertion to the line containing its project id (e.g. the `active`
  cell asserted on the `ActiveProj` row), not bare output membership.

### §S3 — installer + test hygiene
- `install.sh`: rewrite the `$SANDESH_ADMIN` admin-assignment `$'...'`-escaped
  inline-python (line ~101) as a readable heredoc (`"$VENV/bin/python" - <<'PY' ... PY`)
  — byte-identical behavior: env-read inside python, ValueError surfaced as a notice
  with install continuing, unset → skip notice. The §S2b comment block stays.
- `tests/test_install.py`: close the unclosed `TextIOWrapper` handles (subprocess
  pipes) behind the long-standing ResourceWarning so the suite runs warning-clean.
  NOTE: the warning fires in GC context, where it CANNOT be promoted to an exception —
  `-W error::ResourceWarning` passes even with the bug present; the gate must inspect
  the run's output.

## Acceptance criteria

- [ ] **AC1 — imports.** `grep -n "^    import sqlite3" sandesh/sandesh_db.py` finds
      nothing; the module-level import remains; full suite green.
- [ ] **AC2 — docstrings.** `_tombstone_guards.__doc__` mentions that state checks
      precede the admin check; `search.__doc__` names the `[`/`]` markers and `…`
      elision.
- [ ] **AC3 — reindexed passthrough.** The §S2 fixture: `sandesh_search` via MCP
      returns `reindexed` True with correct hits/total; a second call returns the same
      hits WITHOUT the `reindexed` key (index now populated).
- [ ] **AC4 — filter params via MCP.** `sender`, `since`, `until` each narrow
      `sandesh_inbox` results as specified, including the date-only `until`
      end-of-day case.
- [ ] **AC5 — installer heredoc.** `install.sh` contains no `$'import os\n...'`
      escaped python; a fresh install with `$SANDESH_ADMIN` set assigns the admin
      (`admin_name()` returns it), a re-run with a DIFFERENT name prints the keeping-
      notice and exits 0, unset skips with the notice (existing install tests stay
      green; extend if uncovered).
- [ ] **AC6 — warning-clean.** The combined output of `PYTHONPATH=. .venv/bin/python
      tests/test_install.py` (stdout+stderr) contains ZERO lines matching
      `ResourceWarning` (currently 3+); the suite stays 27/27 green.
- [ ] **AC7 — line-anchored state assertions.** Each AC1 state assertion in
      `tests/test_projects_listing_cli.py` asserts the state cell on the line containing
      its own project id; the suite stays 20/20 green.

## Estimated size
Small — mechanical edits + a handful of tests; the installer rewrite is the only piece
needing care (verified by the existing install suite + AC5).

## Risks / open questions
- The heredoc rewrite touches the installer right before the v0.2.0 reinstall — AC5's
  three-path check (assign / different-name re-run / unset) is the guard.

## Non-goals
- `sender_project` SQL-join optimization (register note — revisit at volume); any
  production behavior change; the `projects --all` flag (CR-SAN-029).
