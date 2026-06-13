# CR-SAN-033 — hotfix: `consolidate` skips non-store DB files

**Status:** PENDING
**Priority:** Critical (v0.2.0 installer crashes on real-world debris)
**Depends on:** —
**Labels:** hotfix-0.2.1, consolidation, installer
**Wave:** hotfix 0.2.1
**Design reference:** docs/research/PRD-global-store.md D10 (consolidation is glue over
*legacy stores* — a non-store file is not one)

## Context

The v0.2.0 install crashed live: `projects/ci/sandesh.db` on the user's machine holds only
empty yoyo bookkeeping tables (debris from a Wave-5-era `migrate` test run — no `address`
table, no data). `consolidate()` (`sandesh_db.py`) treats every `projects/*/sandesh.db` as
a legacy store; `_consolidate_store` then dies with `sqlite3.OperationalError: no such
table: address`, aborting the installer (`set -e`) before `reindex` and the admin
assignment. Any v0.1.x user with similar debris hits the same wall.

## Scope

- `consolidate()` probes each candidate file BEFORE `_consolidate_store`: open read-only;
  a file whose `sqlite_master` has no `address` table, or that raises
  `sqlite3.DatabaseError` on open/probe (corrupt / not a SQLite file), is **skipped** —
  the file is left untouched (no rename), and the summary list gains
  `{'project_id': <id>, 'skipped': True, 'reason': <short text>}`.
- Real legacy stores consolidate exactly as today (summary dict shape unchanged for them);
  a skip never aborts the scan — later stores still consolidate; return code stays 0.
- `cmd_consolidate` (CLI) prints for skipped entries:
  `skipped <project_id>: not a legacy store (<reason>) — file left untouched`.
- Idempotent: a re-run with the debris still present re-skips identically (no rename, no
  duplicate imports).
- No `install.sh` change (the verb now exits 0 over debris).

## Acceptance criteria

- [ ] **AC1 — yoyo-stub skip.** A `projects/x/sandesh.db` containing only yoyo tables
      (the live-debris shape): `consolidate()` returns a summary entry with
      `skipped=True` and a non-empty `reason`, the file is byte-identical afterwards
      (no rename, same content), and a REAL legacy store in a sibling project dir in the
      same run is consolidated normally.
- [ ] **AC2 — corrupt/non-SQLite skip.** A `sandesh.db` that is (a) zero-byte and
      (b) plain text garbage: both skipped with a reason, no exception escapes.
- [ ] **AC3 — regression.** All existing consolidation tests pass unchanged (real-store
      import, id remap, `.pre-global` rename, idempotency).
- [ ] **AC4 — CLI notice.** With a stub present, `sandesh consolidate` exits 0 and stdout
      contains `skipped x: not a legacy store` and `file left untouched`.
- [ ] **AC5 — idempotent skip.** Two consecutive `consolidate()` runs over the same stub
      yield the same skip entry; no rows are imported from it either time.

## Estimated size
Tiny — one probe + skip branch, CLI line, focused tests.

## Risks / open questions
- (none — the probe is read-only; the live debris is the verification fixture.)

## Non-goals
- Cleaning up / renaming debris files (left for the user); any change to real-store
  consolidation; migrating yoyo bookkeeping out of legacy locations.
