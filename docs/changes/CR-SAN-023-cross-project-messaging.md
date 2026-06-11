# CR-SAN-023 — Cross-project messaging + access control

**Status:** PENDING
**Priority:** High (the wave's raison d'être — Mainline-to-Mainline across projects)
**Depends on:** CR-SAN-022 (global DB + tracker)
**Labels:** wave-6, global-store, messaging, access-control
**Wave:** Wave 6 (global store)
**Design reference:** docs/research/PRD-global-store.md (D3, D11, D2 state checks; §6 verification)

## Context

With the global DB in place (CR-SAN-022), delivery across projects is just rows — but the validation layer
still enforces "recipient project == sender project", and PRD D11 says cross-project sending must be
**admin-gated**: a one-time, **project-level** grant, **inherited by every participant** of the approved
project, revocable project-wide. This CR relaxes the same-project rule, adds the grant machinery and the
tracker-state checks, and proves To-wakes/Cc-silent across projects.

## Scope

### §S1 — relax the same-project rule (D3)
- `sandesh_db.send`/`reply`: a recipient is valid iff it is a **registered, active** address in **any
  `active` project**. The explicit same-project recipient check **added by CR-SAN-022** (its gap-analysis
  DRIFT-1: the rule was previously *emergent from store isolation*, made explicit code in 022) is
  **replaced** by the grant-gated rule of §S2. `register` keeps its existing per-address validation
  (address's `<Project>` part must equal the project being registered into) — unchanged.

### §S2 — cross-project access control (D11)
- Grant metadata on the **`project` tracker row**: columns `xproj_granted_at` (TEXT, NULL = not granted)
  and `xproj_granted_by` (TEXT) — added by a yoyo step `0004-xproj-grant.sql` (+ rollback); snapshot
  regenerated. The same step creates the **`admin` table (gap-analysis DEC-C: dedicated table — the
  admin is NOT an address, it must never be messageable/registrable/listable)**:
  ```sql
  CREATE TABLE IF NOT EXISTS admin (
    id          INTEGER PRIMARY KEY CHECK (id = 1),   -- single row, enforced
    name        TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
  ```
  `_SCHEMA` gains both (fresh-DB parity, harmless-rerun like 0003).
- Enforcement in `send`/`reply`: if **any** recipient's project differs from the sender's project AND the
  sender's project has `xproj_granted_at IS NULL` → reject with the exact error
  `cross-project sending not approved for project '<id>' — ask the Sandesh admin`. In-project sends are
  never affected by the grant.
- The grant is **inherited**: no per-address state; every participant of a granted project may send
  cross-project, immediately and without re-approval (one-time).
- Admin CLI verbs (CLI-only — D8/D9/D11 boundary; never MCP): `sandesh grant --cross-project --project
  <id> --by <admin>` sets the grant (idempotent); `sandesh revoke --cross-project --project <id> --by
  <admin>` clears it **project-wide** (all participants lose access at once). `by` must equal the stored
  admin name (`admin` table) — wrong `by` → `PermissionError`-style exit `only the Sandesh admin may
  grant/revoke cross-project access`; empty admin table → `no admin assigned — re-run install.sh with
  $SANDESH_ADMIN`. **Parser shape (gap-analysis DRIFT-3):** both subparsers are built WITHOUT
  `parents=[common]` (their `--project` is the TARGET argument, not routing context — the
  migrate/consolidate pattern; avoids the dual-position SUPPRESS trap).
- Visibility (DRIFT-5): `sandesh projects` output gains columns — `PROJECT  STATE  CROSS-PROJECT`
  (state from the tracker; `✓`/`-` for the grant). `list_projects` itself is unchanged; the CLI does a
  richer query.

### §S2b — admin assignment at install (gap-analysis DEC-D: pulled forward from CR-SAN-024 §S4)
- `sandesh_db.assign_admin(con, name)`: empty table → INSERT; same name → no-op; **different name →
  `ValueError` (`admin already assigned — refusing to silently re-assign`)**. Reader
  `sandesh_db.admin_name(con)` → str or None.
- **NOT a CLI verb** (PRD O3: no agent-reachable Sandesh surface may create/change the admin).
  `install.sh` assigns via an inline interpreter call —
  `"$VENV/bin/python" -c "from sandesh import sandesh_db as s; ..."` — when `$SANDESH_ADMIN` is set
  (non-interactive default: skip with a notice when unset; the re-assign refusal is caught and
  surfaced as a notice, NOT an install abort).

### §S3 — tracker-state checks on mutating verbs (D2/D5/D6)
- `send`/`reply`/`register` consult `project_state()` for every project involved and fail with **distinct
  errors**: `project '<id>' is archived`, `project '<id>' is tombstoned`, `unknown project '<id>'`.
  Both directions: a send *from* an archived/tombstoned project's address and a send addressed *to* one
  are rejected at the sender (no silent drops, no queueing).
- **Sender side included (gap-analysis DRIFT-6):** the SENDER's project state is checked too (an address
  whose project has no tracker row → `unknown project`). `register` gains the enrollment requirement —
  registering into a project with no tracker row fails `unknown project '<id>'` (today it silently
  succeeds; `setup` first is the documented flow). All checks live inside `send`'s existing
  `if validate:` block (`validate=False` remains an internal/test hook — verified no production caller,
  DRIFT-4).

### §S4 — `all-tracks` stays in-project (D3)
- The `all-tracks` expansion enumerates active addresses of the **sender's project only** (minus the
  sender) — never other projects, regardless of grant.

### §S5 — wake across projects
- No `notify` code change expected (it polls the one DB after CR-SAN-022); prove by test that a
  cross-project `to` wakes the recipient's watcher row (its `unread_to` filter matches) and `cc` stays
  silent across projects.

## Acceptance criteria

- [ ] **AC1 — cross-project round-trip (granted).** With projects P1, P2 active and P2 granted:
      `send(from='Mainline - P2', to=['Mainline - P1'], …)` succeeds; the message appears in
      `inbox('Mainline - P1')` and `fetch` returns it with the correct `from`.
- [ ] **AC2 — deny without grant.** Same send with P2's `xproj_granted_at` NULL raises/exits with the
      exact message `cross-project sending not approved for project 'P2' — ask the Sandesh admin`; no
      message row is written. An **in-project** send from ungranted P2 still succeeds.
- [ ] **AC3 — grant is project-inherited + one-time.** After `grant --cross-project --project P2`, BOTH
      `Mainline - P2` and `Track 1 - P2` can send cross-project with no further approval; the grant
      columns hold the admin identity + timestamp; re-granting is a no-op.
- [ ] **AC4 — revocation is project-wide.** After `revoke --cross-project --project P2`, every P2 address
      is denied (AC2's error) on the next cross-project send; in-project sends unaffected.
- [ ] **AC5 — admin-only + CLI-only.** `grant`/`revoke` with `by` ≠ the stored admin name are rejected
      (`only the Sandesh admin may grant/revoke cross-project access`); with an EMPTY admin table the
      error is `no admin assigned — re-run install.sh with $SANDESH_ADMIN`; no `grant`/`revoke`/`xproj`/
      `admin` symbol is exposed by `mcp_server.py` (boundary grep).
- [ ] **AC6 — tracker-state errors.** Sends from/to an `archived` project fail with `project '<id>' is
      archived`; from/to a `tombstoned` one with `project '<id>' is tombstoned`; to an unenrolled project
      with `unknown project '<id>'` — each as a distinct, exact message; no rows written.
- [ ] **AC7 — `all-tracks` scope.** With P1+P2 active, both granted, `send(from='Mainline - P1',
      to=['all-tracks'])` creates recipient rows ONLY for P1's active addresses (minus sender) — zero P2
      rows.
- [ ] **AC8 — cross-project wake semantics.** A cross-project `to` recipient's id appears in its
      `unread_to()` (the notify/wake filter); a cross-project `cc` recipient's does not (delivered,
      readable, silent).
- [ ] **AC9 — schema gate.** `0004-xproj-grant` applies + rolls back cleanly (xproj columns AND the
      `admin` table both ways); `migrate --dump-schema` equals the regenerated committed snapshot
      including `admin` (CI snapshot-sync gate green); fresh-`_SCHEMA` parity + harmless re-run hold.
- [ ] **AC10 — admin assignment (DEC-C/DEC-D).** `assign_admin`: empty table → row created (`id=1`,
      name, `assigned_at`); same-name re-assign → no-op; different-name → `ValueError` containing
      `refusing to silently re-assign`, row unchanged. A second INSERT attempt violates the `CHECK
      (id = 1)`/PK (single row enforced at the schema level). `install.sh`: with `$SANDESH_ADMIN=ops`
      the row is assigned; re-running with `$SANDESH_ADMIN=other` leaves `ops` in place and the install
      COMPLETES with a notice; unset `$SANDESH_ADMIN` → skipped with a notice; assignment is via the
      venv python inline call, and NO `admin` CLI subcommand exists (`sandesh admin` → argparse error).
- [ ] **AC11 — `projects` listing visibility.** `sandesh projects` shows `PROJECT  STATE
      CROSS-PROJECT` columns; a granted project shows the flag, an ungranted one does not (in-process
      CLI capture).

## Estimated size
Medium — one small migration, focused validation changes in `send`/`reply`, two admin CLI verbs, and a
broad but mechanical test set (the AC matrix above).

## Risks / open questions
- ~~Multi-recipient mixed sends~~ — **confirmed at gap-analysis**: the grant check applies if ANY
  recipient is cross-project; with the grant held, every cross-project recipient's project must
  additionally be `active` (§S3). All checks complete before any insert (atomicity, the 022 pattern).
- ~~Admin sequencing~~ — **resolved (DEC-C/DEC-D, user-decided 2026-06-11)**: dedicated single-row
  `admin` table shipped in 0004 here; installer assignment pulled forward from CR-SAN-024 §S4 into
  §S2b. CR-SAN-024 only CONSUMES the row (tombstone authz).

## Non-goals
- The lifecycle verbs and their read rules (CR-SAN-024 — it now consumes the admin row shipped here).
- Any MCP surface change (CR-SAN-025) — grant/revoke/admin never get MCP tools at all (D11/O3).
- Per-address grants, expiring grants, or approval workflows (one-time project-level only).

## Gap-analysis findings
_Completed 2026-06-11 (orchestrator: vidushi-sandesh; gap-analysis skill). Verdict: **SPEC_UPDATE_NEEDED
→ applied above**. DEC-C/DEC-D escalated → **user-decided 2026-06-11**._

### Dimension 1 (Spec vs PRD)
- **DRIFT-2 / DEC-D (user-decided)** — admin ASSIGNMENT was CR-024 §S4 scope; landing 023 first would
  ship an unusable grant path (deny-by-default until 024). → installer assignment **pulled forward**
  into §S2b; 024 trimmed to consume-only.
- PRD O3 boundary honored: assignment is NOT a CLI verb (no agent-reachable Sandesh surface) —
  install.sh uses an inline venv-python call.

### Dimension 2 (Spec vs Code)
- **DRIFT-3** — grant/revoke `--project` is a TARGET arg: subparsers built without `parents=[common]`
  (migrate/consolidate pattern; pinned in §S2).
- **DRIFT-4 (verified, note only)** — `send(validate=False)` bypasses all checks; no production caller
  (CLI/MCP always validate; one test fixture uses it). Grant+state checks live inside `if validate:`.
- **DRIFT-5** — `cmd_projects` prints bare names; §S2 visibility pinned to a 3-column listing (AC11).

### Dimension 3 (Code vs PRD)
- **DRIFT-1 / DEC-C (user-decided)** — no admin storage exists anywhere and 023 is its first READER
  (AC5) though placement was parked at 024's gap-analysis. → **dedicated single-row `admin` table**
  (id=1 CHECK; the admin is not an address — never messageable/registrable/listable), shipped in 0004.
- **DRIFT-6** — `register` today succeeds for an UNENROLLED project (no tracker-row check,
  `sandesh_db.py:191-209`); and `send`'s sender-side project state is unchecked. §S3 extended: register
  requires enrollment; sender-side `unknown project` covered.
- **Verified non-drifts:** `all-tracks` already project-filtered by 022 (`_expand_recipients:261`);
  cross-project BODY files land in the sender's folder automatically (CLI passes
  `store_dir(<context>)` — PRD D1 sender-owned holds with zero new code); the 022 refusal site
  (`send:294-297`, pre-insert) is exactly where the grant-gated rule replaces it, preserving atomicity.

### Summary table
| # | Dim | Finding | Fix scope | Blocking? |
|---|-----|---------|-----------|-----------|
| DRIFT-1 | 3/1 | admin stored form needed by 023 | DEC-C: dedicated `admin` table (user) | Yes |
| DRIFT-2 | 1 | assignment timing | DEC-D: pulled into 023 §S2b (user) | Yes |
| DRIFT-3 | 2 | grant/revoke parser shape | SPEC_UPDATE (§S2) | No |
| DRIFT-4 | 2 | `validate=False` hook | verified, note | No |
| DRIFT-5 | 2 | projects listing format | SPEC_UPDATE (AC11) | No |
| DRIFT-6 | 3 | register/sender enrollment checks missing | SPEC_UPDATE (§S3) | No |
