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
  regenerated.
- Enforcement in `send`/`reply`: if **any** recipient's project differs from the sender's project AND the
  sender's project has `xproj_granted_at IS NULL` → reject with the exact error
  `cross-project sending not approved for project '<id>' — ask the Sandesh admin`. In-project sends are
  never affected by the grant.
- The grant is **inherited**: no per-address state; every participant of a granted project may send
  cross-project, immediately and without re-approval (one-time).
- Admin CLI verbs (CLI-only, like tombstone — D8/D9 boundary): `sandesh grant --cross-project --project
  <id> --by <admin>` sets the grant (idempotent); `sandesh revoke --cross-project --project <id> --by
  <admin>` clears it **project-wide** (all participants lose access at once). Non-admin `by` rejected.
- Visibility: `projects` listing (and `addressbook` per-project headers if present) expose the grant flag.

### §S3 — tracker-state checks on mutating verbs (D2/D5/D6)
- `send`/`reply`/`register` consult `project_state()` for every project involved and fail with **distinct
  errors**: `project '<id>' is archived`, `project '<id>' is tombstoned`, `unknown project '<id>'`.
  Both directions: a send *from* an archived/tombstoned project's address and a send addressed *to* one
  are rejected at the sender (no silent drops, no queueing).

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
- [ ] **AC5 — admin-only + CLI-only.** `grant`/`revoke` with a non-admin `by` are rejected; no
      `grant`/`revoke`/`xproj` symbol is exposed by `mcp_server.py` (boundary grep).
- [ ] **AC6 — tracker-state errors.** Sends from/to an `archived` project fail with `project '<id>' is
      archived`; from/to a `tombstoned` one with `project '<id>' is tombstoned`; to an unenrolled project
      with `unknown project '<id>'` — each as a distinct, exact message; no rows written.
- [ ] **AC7 — `all-tracks` scope.** With P1+P2 active, both granted, `send(from='Mainline - P1',
      to=['all-tracks'])` creates recipient rows ONLY for P1's active addresses (minus sender) — zero P2
      rows.
- [ ] **AC8 — cross-project wake semantics.** A cross-project `to` recipient's id appears in its
      `unread_to()` (the notify/wake filter); a cross-project `cc` recipient's does not (delivered,
      readable, silent).
- [ ] **AC9 — schema gate.** `0004-xproj-grant` applies + rolls back cleanly; `migrate --dump-schema`
      equals the regenerated committed snapshot (CI snapshot-sync gate green).

## Estimated size
Medium — one small migration, focused validation changes in `send`/`reply`, two admin CLI verbs, and a
broad but mechanical test set (the AC matrix above).

## Risks / open questions
- Multi-recipient mixed sends (in-project + cross-project recipients in one `to` list): the grant check
  applies if ANY recipient is cross-project — confirm wording at gap-analysis.
- The admin identity check reuses CR-SAN-024's stored-admin row — sequencing: this CR lands first, so it
  needs the admin row's reader (or this CR's `grant` lands behind 024; resolve the dependency direction
  at gap-analysis — the PRD's CR order is 022→023→024, but the admin is assigned at install in 024's
  scope; the gap-analysis decides whether the admin-assignment installer bit moves here).

## Non-goals
- The lifecycle verbs and their read rules (CR-SAN-024).
- Any MCP surface change (CR-SAN-025) — grant/revoke never get MCP tools at all (D11).
- Per-address grants, expiring grants, or approval workflows (one-time project-level only).
