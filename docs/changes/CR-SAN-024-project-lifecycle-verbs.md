# CR-SAN-024 — Project lifecycle verbs (archive / unarchive / tombstone) + super-admin

**Status:** PENDING
**Priority:** Medium-High (delivers the lifecycle the wave was designed around)
**Depends on:** CR-SAN-023 (tracker-state checks + admin identity interplay)
**Labels:** wave-6, global-store, lifecycle, admin, installer
**Wave:** Wave 6 (global store)
**Design reference:** docs/research/PRD-global-store.md (D4–D8, O1, O3; §6 verification)

## Context

The tracker (CR-SAN-022) and the state checks (CR-SAN-023) exist, but nothing yet *moves* a project
through `active → archived → tombstoned`. This CR adds the three core lifecycle ops, the read rules for
tombstoned traffic, the CLI verbs with their guards, and the **super-admin persona assigned at install**
(O3) that alone may tombstone.

## Scope

### §S1 — core lifecycle ops (`sandesh_db`)
- `archive(con, project_id, by, *, force=False, wait_secs=None)`: requires state `active` and `by` ==
  the project's registered Mainline; cooperatively evicts every live notifier of the project's addresses
  (existing seam: `notifier_tombstone` → bounded wait ≈ 2 poll cycles from `$SANDESH_POLL_SECONDS` →
  `notifier_reap_if_stale`); refuses (state unchanged) if a watcher stays live unless `force`; then sets
  `state='archived', archived_at=now`. Deletes **nothing**.
- `unarchive(con, project_id, by)`: `archived → active` (Mainline-only), clears `archived_at`.
- `tombstone_project(con, store_root, project_id, by, *, force=False, wait_secs=None)`: requires state
  **`archived`** (tombstoning an `active` project errors: `archive it first`) and `by` == the
  **super-admin**; purges **project-internal** rows (messages whose sender AND all recipients are in the
  project + their recipient rows + the project's `address` and `notifier` rows); deletes the whole
  `projects/<id>/` folder (bodies — T1 content-dies-with-origin); sets `state='tombstoned',
  tombstoned_at=now`. Cross-project message rows survive in the DB.
- State-machine errors are exact: `project '<id>' is not active` / `not archived` / `already tombstoned`.

### §S2 — read rules (D5/D6)
- `inbox`/`fetch`: messages **to or from a tombstoned project's addresses are hidden** (filtered out, not
  placeholder-rendered; unread mail from a now-tombstoned project never surfaces). **Archived projects'
  traffic is NOT affected** — displays fully.
- `thread`: where a visible chain passes through hidden/purged nodes, render the exact warning line
  `incomplete chain — message(s) removed (project tombstoned)` instead of silently skipping or failing.

### §S3 — CLI verbs + guards (D8/D9)
- `sandesh archive|unarchive|tombstone --project <id> --by <addr>`; `archive`/`tombstone` take `--force`;
  destructive `tombstone` takes the interactive confirm bypassable with `--yes`, and all three accept
  `--dry-run` (reports watchers to evict; for tombstone additionally: counts of internal rows to purge,
  body files to delete, and cross-project messages whose bodies would be lost / threads that would hole —
  **writes nothing**).

### §S4 — super-admin persona (O3) — **CONSUMES the row shipped by CR-SAN-023**
- _Moved by 023's gap-analysis (DEC-C/DEC-D, user-decided 2026-06-11):_ the dedicated single-row
  `admin` table, `assign_admin`/`admin_name`, and the `install.sh` `$SANDESH_ADMIN` assignment (with
  the no-silent-reassign refusal) all SHIP IN CR-SAN-023 (§S2b there). This CR only **reads** it:
  `tombstone --by` must equal `admin_name(con)`; empty admin table → clear `no admin assigned` error.

### §S5 — docs
- `CLAUDE.md` locked semantics: replace the teardown-less lifecycle (#5 keep-history gets the
  archive/tombstone exception note); README lifecycle section; `--dry-run`/admin documented.

## Acceptance criteria

- [ ] **AC1 — state machine + two-step.** `archive` flips `active→archived` (+`archived_at`);
      `unarchive` flips back; `tombstone_project` on an `active` project errors with a message containing
      `archive it first` and changes nothing; on an `archived` project it flips to `tombstoned`
      (+`tombstoned_at`). Each invalid transition raises its exact error.
- [ ] **AC2 — archived = read-only, intact.** After `archive(P2)`: sends from AND to P2 addresses are
      rejected (`project 'P2' is archived`); `register` into P2 rejected; P2's `inbox`/`fetch`/`thread`
      return complete data (bodies readable); cross-project viewers still see P2 threads fully.
- [ ] **AC3 — eviction.** With a live notifier seeded via `notifier_acquire(con, addr, os.getpid(), tok,
      host)`: `archive` sets its `tombstone` flag and, while it stays live past the bounded wait, refuses
      with state unchanged; with `force=True` it reaps and proceeds. A dead-pid/stale-heartbeat row does
      not block.
- [ ] **AC4 — tombstone purge selectivity.** Fixture: P2-internal messages + P1↔P2 cross-project messages.
      After tombstone: internal message + recipient rows GONE; cross-project rows PRESENT; P2's `address`
      + `notifier` rows gone; `projects/P2/` (incl. all bodies) gone from disk; tracker row remains
      `tombstoned`.
- [ ] **AC5 — read rules.** Post-tombstone of P2: P1's `inbox`/`fetch` show NO messages to/from P2
      addresses (including previously-unread ones); messages involving an *archived* P3 still display;
      `thread` on a P1 chain that passed through P2 renders `incomplete chain — message(s) removed
      (project tombstoned)`.
- [ ] **AC6 — authz tiers.** `archive`/`unarchive` with `by` ≠ the project's Mainline are rejected;
      `tombstone` with `by` == the project's Mainline is rejected; only `by` == the stored admin succeeds.
- [ ] **AC7 — dry-run writes nothing.** `--dry-run` tombstone reports the would-be counts (internal rows,
      body files, cross-project bodies lost) and afterwards: state unchanged, all rows present, folder
      present, notifier rows untouched.
- [ ] **AC8 — admin consumption.** (Assignment itself ships in CR-SAN-023 — its AC10.) `tombstone` with
      `by` ≠ `admin_name(con)` is rejected; with an EMPTY admin table it fails with a clear
      `no admin assigned` error; with the stored admin it proceeds (state permitting).
- [ ] **AC9 — O1 retired ids.** After tombstone of P2, `setup('P2')` raises with message containing
      `retired (tombstoned)`.
- [ ] **AC10 — E2E capstone (the ONE real-subprocess test).** Temp `XDG_DATA_HOME`, real
      `Popen(["sandesh","notify",…])` for a P1 recipient, poll-with-timeout until acquired; cross-project
      send P2→P1 → watcher exits 0; relaunch; `archive P1 --yes --force` → watcher exits 3; kill-on-timeout
      guards, no bare sleeps.

## Estimated size
Large — three core ops with guard matrix, the read-rule filters touching `inbox`/`fetch`/`thread`, the
installer admin hook, and the widest AC set of the wave.

## Risks / open questions
- The purge's "internal message" predicate (sender AND all recipients in-project) needs a careful SQL
  shape — verify against multi-recipient mixed messages at gap-analysis.
- Hidden-traffic filters touch the hottest read queries — keep them index-friendly.
- Admin row placement (address table vs dedicated) — mechanical, settle at gap-analysis (O3).
- Dependency direction with CR-SAN-023's `grant --by <admin>` (see 023's risks) — if 023 lands first it
  needs the admin reader earlier; gap-analysis may move §S4 forward.

## Non-goals
- Any MCP exposure (CR-SAN-025 adds archive/unarchive tools; tombstone NEVER gets one — D9).
- Message-level retention/purge tools; un-tombstoning; per-address grants.
