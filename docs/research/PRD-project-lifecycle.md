# PRD — Sandesh project lifecycle (setup ↔ teardown)

**Status:** SUPERSEDED by [PRD-global-store](PRD-global-store.md) (2026-06-11 — the cross-project/global-DB
redesign replaced the hard-delete teardown with the archive→tombstone lifecycle; the eviction machinery (D2/D3),
guards (D5) and authz (D7) carried over into that PRD; no CR was ever cut from this document)
**Owner:** Mainline - Sandesh
**Wave:** Wave 6 (project lifecycle)
**Related:** `CLAUDE.md` locked semantics #7 (crash-safe liveness) + #8 (cooperative tombstone eviction) + #9
(removal authorization); `sandesh_db` notifier seam (`notifier_live`/`notifier_tombstone`/`notifier_reap_if_stale`),
`unregister` (the address-level cooperative-eviction precedent); PRD-mcp-server §verb-catalog (where `sandesh_setup`
appears as a tool-mapping row).

Design contract (WHY + WHAT) for the **create ↔ destroy** lifecycle of a per-project store — making the
inverse of `setup` a **tooled, watcher-safe operation** instead of a manual `rm -rf`. CRs derived from this
cite it via `**Design reference:**`.

---

## 1. Why

`setup(project_id)` provisions a project's store (`projects/<id>/` + `messages/` + `sandesh.db`); `setup` has
**no inverse**. Today, removing a project means manually deleting its folder — and that risks leaving Sandesh in
an **indeterminate state**:

- A **live `notify` watcher** may still be polling that store's `sandesh.db`. Deleting the file out from under it
  orphans the process: it keeps polling a vanished DB (errors), and never performs the clean, token-guarded
  `notifier_release` exit — defeating the crash-safe-liveness design (locked semantics #7).
- The `notifier` table holds **per-session liveness rows + tokens**; a project teardown must go through the same
  **cooperative tombstone → evict** path that `unregister` already uses for a single address (locked semantics #8),
  not bypass it.
- It is asymmetric and undiscoverable: `setup` is a verb (CLI + MCP), its inverse is a shell `rm`.

So a project **teardown** must be a first-class, tooled operation that (a) cooperatively stops any live watcher
**first**, then (b) removes the store atomically and idempotently. This PRD also **formalizes `setup`** (the
create side) which was only ever documented as an MCP tool-mapping row, never as a core lifecycle contract.

## 2. What it is (and is not)

**Is:** a symmetric project-lifecycle pair on the existing layered stack —
1. **Create = `setup(project_id)`** (already implemented; formalized here): idempotent provision of
   `projects/<id>/` + `messages/` + the migrated `sandesh.db`. Lazily also created by any op that opens the DB
   (`connect()` does `makedirs`), but `setup` is the explicit entry point.
2. **Destroy = `teardown(project_id, …)`** (NEW): the inverse. A **core `sandesh_db` op** that
   **cooperatively evicts** every live notifier for the project's addresses (reusing `notifier_tombstone` +
   the bounded wait-for-offline + `notifier_reap_if_stale`), then removes `projects/<id>/` (DB + `messages/`).
   Idempotent (no-op if absent). Removing the dir auto-drops it from `list_projects()` (which enumerates
   `projects/<id>/sandesh.db` on disk — **no separate registry to update**).
3. **Invocation:** a `sandesh teardown --project <id>` CLI verb (with the destructive-op confirm/`--yes` +
   `--dry-run` guards) and a symmetric **`sandesh_teardown`** MCP tool (`destructiveHint=True`, required
   `project_id`).

**Is not:** a soft-delete (that's address-level `deactivate`/`unregister`; teardown is the whole store gone); a
message-purge/retention tool (it removes the *project*, not "old messages" — Sandesh keeps history, locked
semantics #5); a way to kill **another machine's** watcher (cross-host force-kill is impossible — the cooperative
tombstone is the only lever, locked semantics #8); a migration concern (orthogonal to PRD-db-migration).

## 3. Decisions

**D1 — Teardown is a CORE `sandesh_db` op; CLI + (proposed) MCP are thin wrappers.** The actual eviction +
removal logic lives in `sandesh_db.teardown(...)` (stdlib only, like `setup`), so every front end shares one
correct implementation. `cli.py` adds the `teardown` subcommand; the MCP/Pi exposure is a wrapper decision (D6/§7).

**D2 — Cooperative watcher eviction FIRST, reusing the address-level machinery.** Pre-flight: for the project's
addresses, find those with a **live** notifier (`notifier_live` — fresh heartbeat + live pid). For each, call
`notifier_tombstone(recipient)` and **wait, bounded**, for the watcher to observe it on its next poll, self-exit
(exit 3), and `notifier_release` its row (or go stale). Then `notifier_reap_if_stale` sweeps any residue. This is
locked-semantics #8 applied at project scope — teardown never force-deletes a DB under a running poller.

**D3 — Refuse-by-default if a live watcher won't release in time; `--force` overrides.**
If, after the bounded wait (**≈ 2 poll cycles**, derived from `$SANDESH_POLL_SECONDS`), a notifier is still live
(watcher wedged / not polling), `teardown` **refuses** (non-zero exit, store untouched) unless `--force` is given,
in which case it tombstones-again, reaps the rows, and removes the store regardless.

**D4 — What is removed: a HARD DELETE; history is irreversibly destroyed.** On success teardown removes the
**entire `projects/<id>/`** subtree — the per-project `sandesh.db` (all four tables: addressbook, envelopes,
recipients, notifier state) **and every `messages/msg-*.md` body**. The **addressbook is per-project** (the
`address` table lives inside that same DB; there is no global addressbook), so the registered identities are
destroyed with it — a later re-`setup` of the same id is a rebirth: empty addressbook, everyone re-registers,
message ids restart. This is a **hard delete**: no soft-delete, no archive, no undo — **all of the project's
message history AND its addressbook are irreversibly lost.** It is the one deliberate
exception to locked-semantics #5 ("keep history"): that rule governs a *live* store; teardown ends the store
itself. Order: evict (D2) → confirm no live notifier → `shutil.rmtree(projects/<id>)`. Idempotent: absent
project → success no-op. (No partial state: either it refused early (D3) or the tree is gone.)

**D5 — Destructive-op guards on the CLI: confirm/`--yes` + `--dry-run`.** `sandesh teardown --project <id>` is
**destructive and irreversible** (D4). It requires an explicit confirmation — interactive prompt, bypassable with
**`--yes`** for scripts/CI. A **`--dry-run`** flag reports what *would* happen — which addresses have live
notifiers (would be evicted / would block), and the store path that would be removed — without writing anything.
`--force` (D3) is separate (it concerns *live watchers*, not the confirmation).

**D6 — MCP exposure: ship the symmetric `sandesh_teardown` tool.** `sandesh_setup` is an MCP tool; its inverse is
too (an orchestrator tearing a project down at end-of-workflow), with `ToolAnnotations(destructiveHint=True)` and a
**required** `project_id` — **no `$SANDESH_PROJECT` fallback** for a destructive whole-store op (an implicit-env
teardown is exactly the accident this PRD exists to prevent). The 9-verb surface grows to 10.

**D7 — Authorization: Mainline-only, with a `by` requester.** Mirror removal authorization (locked semantics #9):
only **`Mainline`** may tear down a project (any-address-may-remove-itself doesn't map to a whole-store op). The
CLI and the MCP tool both carry a **`by`** requester (as `unregister` does) for the authorization check + audit.
Honor-system, local cooperative orchestrators.

## 4. Architecture / layout

```
sandesh/
  sandesh_db.py    + teardown(project_id, *, force=False, wait_secs=…)  # core: evict notifiers (D2) → rmtree (D4)
                     (reuses notifier_live / notifier_tombstone / notifier_reap_if_stale; no new table)
  cli.py           + `teardown` subcommand (D5: confirm / --yes / --dry-run; D3: --force; D7: --by)
  mcp_server.py    + sandesh_teardown(project_id, by, …) @mcp.tool(destructiveHint=True)   # D6 — 10th verb
```
- No schema change, no new table — teardown is pure orchestration over the **existing** notifier seam + the
  filesystem. Core stays stdlib (`shutil`, `os`).
- `list_projects()` needs no change: it derives from `projects/<id>/sandesh.db` existing, so removal is reflected
  automatically.

## 5. CR breakdown (spin from this)

| CR | Scope | Depends on |
|---|---|---|
| **CR-SAN-022** | **Project teardown**: `sandesh_db.teardown(project_id, by, *, force, wait_secs)` — Mainline-only authz (D7), cooperative live-notifier eviction (D2) → hard-delete `rmtree` of `projects/<id>/` (D4), idempotent; refuse-by-default + `--force` on a wedged watcher (D3, wait ≈ 2 poll cycles); `sandesh teardown --project <id> --by …` CLI verb with confirm/`--yes` + `--dry-run` (D5); the `sandesh_teardown` MCP tool, `destructiveHint=True`, required `project_id`, 10th verb (D6). Tests per §6. | (none new — builds on the notifier seam) |

(Number per the user's request; confirmed against queue HEAD when scheduled, after this PRD is agreed.)

## 6. Verification (test pyramid — informs the CR's ACs)

The live-watcher path is tested **without** a real blocking process for the bulk of coverage, because liveness is
decided entirely by the `notifier` row (`notifier_live` = fresh `heartbeat_at` + `_pid_alive(pid)`):

- **Tier 1 — deterministic integration (no subprocess; the bulk).** Seed a "live watcher" exactly as the existing
  suite does (`test_unregister_tombstones_live_notifier_first`): `notifier_acquire(con, addr, os.getpid(), tok, host)`
  → the row is live (this process's pid + fresh heartbeat). Assert teardown: (a) tombstones it / refuses per D3
  (store intact, `tombstone=TRUE`); (b) **dead/stale** watcher (bogus pid or stale heartbeat, à la
  `test_notifier_stale_dead_pid_reaped`) → `notifier_live` is `None` → teardown proceeds, store gone; (c) `--force`
  on a still-live row → reap + remove; (d) idempotent on an absent project; (e) `setup` round-trip (create →
  teardown → `list_projects()` no longer lists it); (f) `--dry-run` reports the would-be evictions/removal and
  writes nothing (store + notifier rows untouched); (g) non-Mainline `by` is rejected (D7), store untouched.
- **Tier 2 — exactly ONE real-subprocess E2E (the capstone).** `Popen(["sandesh","notify",…])` against a temp
  `XDG_DATA_HOME`; **poll-with-timeout** until its notifier row appears (acquired); run `sandesh teardown
  --project <id> --yes`; assert the watcher **exits code 3** (tombstoned) **and** the store dir is gone. Mirrors the
  CR-SAN-004 MCP stdio E2E and CR-SAN-019 real-binary smoke. Poll-with-timeout + kill-on-timeout (never
  sleep-and-hope) keeps it non-flaky. One test only — the slow/fragile tier.

## 7. Open questions — RESOLVED by owner review (2026-06-11)

1. **MCP exposure (D6)** — **Ship `sandesh_teardown`** (`destructiveHint=True`, required `project_id`, no env
   fallback). The verb surface grows 9 → 10.
2. **Refuse-vs-force + wait window (D3)** — **Refuse-by-default + `--force`; bounded wait ≈ 2 poll cycles**
   (derived from `$SANDESH_POLL_SECONDS`).
3. **Authorization (D7)** — **Mainline-only; CLI + MCP carry a `by` requester** for the check + audit.
4. **Confirmation UX (D5)** — **Interactive confirm + `--yes`, plus `--dry-run`.**
5. **Scope of removal (D4)** — **Confirmed: whole `projects/<id>/`, a HARD delete of the per-project
   `sandesh.db` + all message bodies — history is irreversibly lost** (the owner explicitly confirmed this
   destructive semantic). No message-only purge here (would contradict locked semantics #5); teardown is
   project-granularity only.
