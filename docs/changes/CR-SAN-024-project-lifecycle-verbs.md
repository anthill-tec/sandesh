# CR-SAN-024 — Project lifecycle verbs (archive / unarchive / tombstone) + super-admin

**Status:** COMPLETED (shipped 2026-06-11 on develop)
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
- `tombstone_project(con, project_id, by, *, force=False, wait_secs=None)` (the body folder comes
  from `store_dir(project_id)`, XDG-honouring): requires
  state **`archived`** (tombstoning an `active` project errors: `archive it first`) and `by` == the
  **super-admin**; purges **project-internal** rows; deletes the whole `projects/<id>/` folder (bodies —
  T1 content-dies-with-origin); sets `state='tombstoned', tombstoned_at=now`.
- **Purge predicate + ordering:** internal message = sender's project == `<id>`
  AND no recipient row resolves to another project. Recipient projects resolve via the `address` table,
  so the purge MUST (1) compute the internal message-id set while the project's address rows still
  exist, (2) delete those message + message_recipient rows, (3) THEN delete the project's `address` +
  `notifier` rows. Cross-project message rows AND their recipient rows (including this project's
  recipient rows on surviving messages) SURVIVE — PRD D6 "audit + thread anchoring".
- `grant_xproj`/`revoke_xproj` gain `_require_active_project`
  (refuse archived/tombstoned with the §S3 state errors); lifecycle transitions NEVER touch the
  `xproj_granted_*` columns (archive↔unarchive leave a grant in place — sends are state-blocked anyway;
  tombstone leaves the columns as part of the permanent marker).
- **`poll_interval()` helper lives in `sandesh_db`** (`notify` imports it — keeps the layering
  one-directional) — `wait_secs` default = 2 × `poll_interval()`.
- State-machine errors are exact: `project '<id>' is not active` / `not archived` / `already tombstoned`.

### §S2 — read rules (D5/D6)
- `inbox`/`fetch`: messages **to or from a tombstoned project's addresses are hidden** (filtered out, not
  placeholder-rendered; unread mail from a now-tombstoned project never surfaces). **Archived projects'
  traffic is NOT affected** — displays fully.
- `thread`: where a visible chain passes through hidden/purged nodes, render the exact warning line
  `incomplete chain — message(s) removed (project tombstoned)` instead of silently skipping or failing.
- **Mechanism:** the tombstoned project's `address` rows are purged, so the
  filter resolves each `from_addr` (and thread nodes' addresses) via the existing `_address_project`
  suffix-fallback against a once-per-call tombstoned-project set (`SELECT project_id FROM project WHERE
  state='tombstoned'`). Python-side filtering over the fetched rows — agent-scale volumes; no fragile
  SQL suffix joins.

### §S3 — CLI verbs + guards (D8/D9)
- `sandesh archive|unarchive|tombstone --project <id> --by <addr>` — subparsers WITHOUT
  `parents=[common]` (target `--project`, the grant/revoke pattern); `archive`/`tombstone` take
  `--force`; destructive `tombstone` takes the interactive confirm bypassable with `--yes`, and all
  three accept `--dry-run` (reports watchers to evict; for tombstone additionally: counts of internal
  rows to purge, body files to delete, and cross-project messages whose bodies would be lost / threads
  that would hole — **writes nothing**).
- **Authz shape:** archive/unarchive `by` must `validate_address` to
  (`Mainline`, `<project_id>`) — format-based, honor-system, the `unregister` house pattern; tombstone
  `by` must equal `admin_name(con)`.

### §S4 — super-admin persona (O3)
- The admin storage and assignment ship in CR-SAN-023 (its §S2b: single-row `admin` table,
  `assign_admin`/`admin_name`, `install.sh` `$SANDESH_ADMIN`). This CR only **reads** it:
  `tombstone --by` must equal `admin_name(con)`; empty admin table → clear `no admin assigned` error.

### §S5 — docs
- `CLAUDE.md` locked semantics: replace the teardown-less lifecycle (#5 keep-history gets the
  archive/tombstone exception note); README lifecycle section; `--dry-run`/admin documented.
- **PRD D7 terminology disambiguation:** docs must distinguish the `notifier`
  table's per-watcher `tombstone` flag (cooperative shutdown, locked semantics #8) from the project
  lifecycle state `tombstoned` — two concepts sharing a word.

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
      send P2→P1 → watcher exits 0; relaunch; `archive --project P1 --by 'Mainline - P1' --force` →
      watcher exits 3 (archive takes no `--yes` — it is not destructive); kill-on-timeout guards, no bare
      sleeps.
- [ ] **AC11 — grant×state.** `grant_xproj`/`revoke_xproj` on an archived project raise
      `project '<id>' is archived`; on a tombstoned one `project '<id>' is tombstoned`; a grant set
      while active SURVIVES archive→unarchive (cross-project send works again immediately after
      unarchive); after tombstone the `xproj_granted_*` columns are still populated on the marker row
      (asserted raw).

## Estimated size
Large — three core ops with guard matrix, the read-rule filters touching `inbox`/`fetch`/`thread`, the
installer admin hook, and the widest AC set of the wave.

## Risks / open questions
- Hidden-traffic read filters sit on the hottest queries — keep the empty-tombstoned-set fast path.

## Non-goals
- Any MCP exposure (CR-SAN-025 adds archive/unarchive tools; tombstone NEVER gets one — D9).
- Message-level retention/purge tools; un-tombstoning; per-address grants.
