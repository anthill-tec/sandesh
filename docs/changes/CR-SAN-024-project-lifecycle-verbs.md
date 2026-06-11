# CR-SAN-024 — Project lifecycle verbs (archive / unarchive / tombstone) + super-admin

**Status:** COMPLETED (2026-06-11)
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
- `tombstone_project(con, project_id, by, *, force=False, wait_secs=None)` (no `store_root` param —
  gap-analysis DRIFT-6: the body folder comes from `store_dir(project_id)`, XDG-honouring): requires
  state **`archived`** (tombstoning an `active` project errors: `archive it first`) and `by` == the
  **super-admin**; purges **project-internal** rows; deletes the whole `projects/<id>/` folder (bodies —
  T1 content-dies-with-origin); sets `state='tombstoned', tombstoned_at=now`.
- **Purge predicate + ORDERING (gap-analysis DRIFT-2):** internal message = sender's project == `<id>`
  AND no recipient row resolves to another project. Recipient projects resolve via the `address` table,
  so the purge MUST (1) compute the internal message-id set while the project's address rows still
  exist, (2) delete those message + message_recipient rows, (3) THEN delete the project's `address` +
  `notifier` rows. Cross-project message rows AND their recipient rows (including this project's
  recipient rows on surviving messages) SURVIVE — PRD D6 "audit + thread anchoring".
- **DEC-E (user-decided 2026-06-11):** `grant_xproj`/`revoke_xproj` gain `_require_active_project`
  (refuse archived/tombstoned with the §S3 state errors); lifecycle transitions NEVER touch the
  `xproj_granted_*` columns (archive↔unarchive leave a grant in place — sends are state-blocked anyway;
  tombstone leaves the columns as part of the permanent marker).
- **`poll_interval()` helper moves INTO `sandesh_db`** (gap-analysis DRIFT-5: `notify._interval` would
  be a reversed import; `notify` now imports it) — `wait_secs` default = 2 × `poll_interval()`.
- State-machine errors are exact: `project '<id>' is not active` / `not archived` / `already tombstoned`.

### §S2 — read rules (D5/D6)
- `inbox`/`fetch`: messages **to or from a tombstoned project's addresses are hidden** (filtered out, not
  placeholder-rendered; unread mail from a now-tombstoned project never surfaces). **Archived projects'
  traffic is NOT affected** — displays fully.
- `thread`: where a visible chain passes through hidden/purged nodes, render the exact warning line
  `incomplete chain — message(s) removed (project tombstoned)` instead of silently skipping or failing.
- **Mechanism (gap-analysis DRIFT-3):** the tombstoned project's `address` rows are purged, so the
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
- **Authz shape (gap-analysis DRIFT-4):** archive/unarchive `by` must `validate_address` to
  (`Mainline`, `<project_id>`) — format-based, honor-system, the `unregister` house pattern; tombstone
  `by` must equal `admin_name(con)`.

### §S4 — super-admin persona (O3) — **CONSUMES the row shipped by CR-SAN-023**
- _Moved by 023's gap-analysis (DEC-C/DEC-D, user-decided 2026-06-11):_ the dedicated single-row
  `admin` table, `assign_admin`/`admin_name`, and the `install.sh` `$SANDESH_ADMIN` assignment (with
  the no-silent-reassign refusal) all SHIP IN CR-SAN-023 (§S2b there). This CR only **reads** it:
  `tombstone --by` must equal `admin_name(con)`; empty admin table → clear `no admin assigned` error.

### §S5 — docs
- `CLAUDE.md` locked semantics: replace the teardown-less lifecycle (#5 keep-history gets the
  archive/tombstone exception note); README lifecycle section; `--dry-run`/admin documented.
- **PRD D7 terminology disambiguation** (gap-analysis DRIFT-6): docs must distinguish the `notifier`
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
- [ ] **AC11 — DEC-E grant×state.** `grant_xproj`/`revoke_xproj` on an archived project raise
      `project '<id>' is archived`; on a tombstoned one `project '<id>' is tombstoned`; a grant set
      while active SURVIVES archive→unarchive (cross-project send works again immediately after
      unarchive); after tombstone the `xproj_granted_*` columns are still populated on the marker row
      (asserted raw).

## Estimated size
Large — three core ops with guard matrix, the read-rule filters touching `inbox`/`fetch`/`thread`, the
installer admin hook, and the widest AC set of the wave.

## Risks / open questions
- ~~grant×state interaction~~ — **DEC-E resolved (user, 2026-06-11)**: grant/revoke require ACTIVE;
  transitions never touch the grant columns (§S1, AC11).
- ~~Purge predicate~~ — **pinned at gap-analysis** (DRIFT-2: compute-internal-set-first ordering, §S1).
- ~~Read-filter shape~~ — **pinned** (DRIFT-3: `_address_project` suffix fallback + tombstoned-set,
  python-side, §S2).
- ~~Admin row placement / dependency direction~~ — **resolved in CR-SAN-023** (DEC-C/DEC-D; §S4 here is
  consume-only).

## Non-goals
- Any MCP exposure (CR-SAN-025 adds archive/unarchive tools; tombstone NEVER gets one — D9).
- Message-level retention/purge tools; un-tombstoning; per-address grants.

## Close-out
_Completed 2026-06-11 (orchestrator: vidushi-sandesh). 5 cycles + VERIFY + 1 FIX._
- **C1** `1360051`/`cf2b5bd` — `archive`/`unarchive` (state machine, Mainline authz, cooperative
  eviction with bounded wait + `--force`), `poll_interval()` moved into `sandesh_db` (DRIFT-5), DEC-E
  guards on grant/revoke. 63/63. AC2/AC3/AC11 (+AC1/AC6 halves).
- **C2** `3e11a77`/`ff61280` — `tombstone_project` (two-step `archive it first`, admin-only via
  `_require_admin(action=…)`, shared `_evict_project_notifiers`, **DRIFT-2-ordered purge** in one
  transaction, body-folder rmtree, marker row keeps grant columns). 56/56. AC1/AC4/AC6/AC8/AC9.
- **C3** `533b8f9`/`9db393a` — read rules: `inbox`/`fetch`/`unread_to` hide tombstoned-project traffic
  (DRIFT-3 suffix-fallback + per-call set; hidden mail never marked read; wake suppressed); `thread`
  warning entry `incomplete chain — message(s) removed (project tombstoned)` (consecutive holes
  collapse; root case returns the warning); archived contrast intact. 19/19. AC5.
- **C4** `4bfd36b`/`bf5424c` — CLI `archive|unarchive|tombstone` (confirm/`--yes` non-TTY refusal,
  `--dry-run` previews via print-free `*_preview` fns sharing the guard code — byte-identical errors);
  CLAUDE.md locked-semantics #5 exception + D7 terminology disambiguation; README lifecycle. 67/67. AC7.
- **C5** `355960e` — the ONE real-subprocess E2E (watcher wake exit 0 → relaunch → archive eviction
  exit 3; poll-with-timeout throughout; 3× stable). AC10.
- **FIX** `7a373bd` — VERIFY should-fix: `finally: con.close()` in the 5 grant/revoke/lifecycle CLI
  handlers (backfilled CR-023's pattern).
- **VERIFY** (python-verify-agent) — **PASS on all 11 ACs**; boundaries clean (`mcp_server.py` diff vs
  develop EMPTY; notify layer-clean; eviction helper + guard fns shared, no drift); the one SHOULD-FIX
  fixed above; one NIT noted (state gate precedes authz in tombstone guards — intentional).
- **Independent verification (orchestrator):** live probes per cycle — wedged-watcher refusal/force,
  purge selectivity (internal gone, cross+audit rows survive, folder gone, grant marker kept), hidden
  reads with `read_at` untouched + thread warning, dry-run accurate counts with zero writes; E2E run
  myself; final clean gate after clearing concurrent-report artifacts.
- **Pre-merge gate:** **973 passed / 0 failed**, `ok=True`, coverage **81.5% lines / 87.7% funcs**
  (up from 77.0/83.7 at CR-023). 31/31 test files.

## Gap-analysis findings
_Completed 2026-06-11 (orchestrator: vidushi-sandesh; gap-analysis skill). Verdict: **SPEC_UPDATE_NEEDED
→ applied above**. DEC-E escalated → **user-decided 2026-06-11**._

### Dimension 1 (Spec vs PRD)
D4–D8 map onto §S1–§S5 cleanly after the updates; PRD D7's terminology note added to §S5. The §S4
assignment scope had already moved to 023 (DEC-D). No PRD requirement unmet; nothing unsupported.

### Dimension 2 (Spec vs Code)
- **DRIFT-2** — purge predicate needs the address rows to resolve recipient projects → ordering pinned
  (compute internal set → delete messages/recipients → delete address/notifier rows); surviving
  cross-project recipient rows kept (PRD D6 audit).
- **DRIFT-3** — at read time the purged address rows force suffix resolution: `_address_project`
  (`sandesh_db.py:332-339`, fallback already present) + per-call tombstoned-set; python-side filter.
- **DRIFT-4** — archive/unarchive authz pinned to `validate_address(by) == ('Mainline', <id>)` (the
  `unregister` pattern); tombstone `by == admin_name(con)`.
- **DRIFT-5** — `wait_secs` needs the poll interval; `notify._interval` would invert the layer →
  `poll_interval()` moves into `sandesh_db`, `notify` imports it.
- **DRIFT-6** — stale spec bits fixed: `store_root` param dropped (use `store_dir`); AC10's spurious
  `--yes` on archive removed; resolved risk bullets cleaned.

### Dimension 3 (Code vs PRD)
- **DRIFT-1 / DEC-E (user-decided)** — grant×lifecycle was undefined (023 VERIFY): grant/revoke gain
  `_require_active_project`; transitions never touch `xproj_granted_*` (tombstoned marker keeps them).
- **Verified:** eviction seam ready (address rows alive at archive time → project→watchers join works);
  `setup` already refuses tombstoned ids (AC9 pins it); 023's state checks already yield AC2's
  send/register errors for archived projects.

### Summary table
| # | Dim | Finding | Fix scope | Blocking? |
|---|-----|---------|-----------|-----------|
| DRIFT-1 | 3 | grant×lifecycle undefined | DEC-E (user-decided) | Yes |
| DRIFT-2 | 2 | purge predicate/ordering | SPEC_UPDATE (§S1) | No |
| DRIFT-3 | 2 | read-filter mechanism | SPEC_UPDATE (§S2) | No |
| DRIFT-4 | 2 | authz shape | SPEC_UPDATE (§S3) | No |
| DRIFT-5 | 2 | poll-interval layering | SPEC_UPDATE (§S1) | No |
| DRIFT-6 | 2 | store_root param, AC10 --yes, stale risks | SPEC_UPDATE | No |
