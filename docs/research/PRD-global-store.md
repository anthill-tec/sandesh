# PRD — Global store, cross-project communication & project lifecycle

**Status:** AGREED (design contract — owner resolved ALL open questions 2026-06-11 incl. O1/O3; CR-SAN-022..025 spin from this)
**Owner:** Mainline - Sandesh
**Wave:** Wave 6 (global store)
**Supersedes:** `PRD-project-lifecycle.md` (its eviction machinery, guards and authz carry over; its hard-delete
teardown D4 is replaced by the archive/tombstone lifecycle below)
**Related:** `CLAUDE.md` locked semantics #2 (per-project stores — **re-opened with reason here**), #5 (keep
history), #7/#8 (crash-safe liveness / cooperative tombstone eviction), #9 (removal authorization);
PRD-db-migration (the engine that carries the consolidation); PRD-mcp-server (verb catalog).

Design contract (WHY + WHAT) for moving Sandesh from N per-project SQLite stores to **one global database**,
enabling **cross-project messaging**, a **global project tracker**, and a two-state **archive → tombstone**
project lifecycle. CRs derived from this cite it via `**Design reference:**`.

---

## 1. Why

Sandesh currently assumes communication happens only between participants of one project: every table —
**including the addressbook** — lives inside `projects/<id>/sandesh.db`, and `send` validates that the
recipient's `<Project>` equals the store's `project_id`. But cross-project communication is essential in some
cases: an orchestrator (typically a Mainline) needs to talk to the orchestrator of *another* project. With
per-project stores that is structurally impossible — there is no global addressbook to discover the peer, and
no store both watchers poll.

Fixing this *within* the per-project layout (a global addressbook DB beside per-project message DBs) solves
discovery but not **delivery**: a cross-project `send` would have to write into another project's DB, splitting
recipients across stores, doubling the schema lineages the migration engine maintains, and complicating the
connection code. The decisive simplification is the opposite move: **one global DB**. The address format
`'<Orchestrator> - <Project>'` already makes every address globally unique — the schema was accidentally ready.
One DB also collapses migration (`migrate --all`'s per-store loop disappears — one yoyo target), simplifies
`connect()`, and makes the wake path cross-project for free (every watcher polls the same DB).

A global DB then forces the lifecycle question the per-project layout dodged: "delete the project folder" no
longer cleanly maps to "delete the project" (cross-project threads interleave projects' histories — tearing one
down can amputate another's message chains). Hence the two-state lifecycle: **archive** (soft close, read-only,
nothing deleted, reversible) and **tombstone** (terminal, payload destroyed, marker survives).

## 2. What it is (and is not)

**Is:**
1. **One global DB** at `<data_home>/sandesh/sandesh.db` holding all tables for all projects. Per-project
   folders remain **only for message bodies** (`projects/<id>/messages/msg-<n>.md`, owned by the *sender's*
   project).
2. **A global `project` tracker table** — a row per enrolled project: `project_id` (PK), `state`
   (`active` | `archived` | `tombstoned`), and lifecycle timestamps. `setup` enrolls (creates the row);
   `list_projects` reads the table (not the filesystem).
3. **Cross-project messaging** — any registered address in an **active** project may message any other;
   the send-time "recipient project must equal store project" check is dropped (register-time address-format
   validation stays). `all-tracks` stays **scoped to the sender's project**.
4. **The lifecycle pair** — `archive` (reversible soft close) and `tombstone` (terminal hard end, only from
   `archived`), with the cooperative watcher-eviction, authorization and CLI guards inherited from the
   superseded lifecycle PRD.
5. **A one-time consolidation** migrating existing per-project stores into the global DB (id remap), carried
   by the installer alongside the existing migration engine.

**Is not:** cross-**host** messaging (still one machine, one data dir); a retention/message-purge tool (no
message-level deletion; the only data-destroying op is project tombstone); a change to the address format or
the wake mechanism (`notify` stays a background process; it just polls the one DB); a rewrite of `sandesh_db`'s
layering (stateless fns over a `con` stay; there is just one DB behind the `con` now); multi-user auth (same
honor-system, locked semantics #9).

## 3. Decisions

**D1 — One global DB; bodies stay per-project-foldered.** All five tables (`project`, `address`, `message`,
`message_recipient`, `notifier`) live in `<data_home>/sandesh/sandesh.db`. Body files keep living under
`projects/<sender-project>/messages/` — that folder layout is what makes tombstone's "delete this project's
payload" one operation. `connect()` opens the one DB (WAL mode); `store_dir(project_id)` survives only as the
body-folder path helper.

**D2 — Global `project` tracker.** `project(project_id PK, state TEXT NOT NULL CHECK(state IN
('active','archived','tombstoned')), created_at, archived_at, tombstoned_at)`. `setup` enrolls (INSERT, or
no-op if active; **un-tombstoning is impossible and a tombstoned id is retired forever** — `setup` of one
refuses: "project id retired (tombstoned) — choose a new id"; O1). Every mutating verb checks the tracker state of the projects involved and
fails with a precise error (`project archived` / `project tombstoned` / `unknown project`).

**D3 — Cross-project send semantics.** `send`/`reply` accept any recipient registered in **any active**
project — **provided the sender's project holds the admin's cross-project grant (D11)**. Register keeps the existing per-address validation (`<Project>` part must equal the project being
registered into). `all-tracks` expands within the **sender's** project only (a global broadcast would storm
every project; cross-project mail is always explicitly addressed). To-wakes/Cc-silent semantics unchanged and
now work across projects natively (one DB, one poll target).

**D4 — Lifecycle state machine: `active → archived ⇄` and `archived → tombstoned` (terminal).**
Two-step destruction is **mandatory**: tombstoning an `active` project errors ("archive it first").
Unarchive (`archived → active`) is supported — archive deletes nothing, so reactivation is a state flip
(participants re-launch watchers).

**D5 — `archived` = read-only, nothing deleted, watchers evicted.**
- Its participants can **neither send nor receive**: a send *from* an archived-project address is rejected, and
  a send addressed *to* one is rejected at the sender (distinct error: "project archived" — no silent drops, no
  queueing for an unarchive). `register`/mutations blocked.
- `inbox`/`fetch`/`thread` work **fully** — bodies intact, chains complete. Cross-project peers (P1 reading a
  thread involving archived P2) see complete history. This is the state that protects thread integrity.
- Live watchers of its addresses are cooperatively evicted (D7 machinery) — no new mail can arrive, polling is
  pointless.

**D6 — `tombstoned` = payload destroyed, marker survives, hidden from standard reads.** Only from `archived`.
On tombstone:
- **Bodies:** delete the project's whole `messages/` folder — **content dies with its origin** (owner ruling
  T1): bodies of cross-project messages this project *sent* are gone too.
- **Rows:** purge **project-internal** rows (messages whose sender AND all recipients are in the project, plus
  their recipient rows, plus the project's `address` and `notifier` rows). **Cross-project rows survive in the
  DB** (audit + thread anchoring) — but see the read rule below.
- **Read rule (owner ruling 2026-06-11): standard reads hide tombstoned-project traffic entirely.**
  `inbox`/`fetch` do **not** show messages sent **to or from** a tombstoned project's addresses — they are
  filtered out, not rendered as placeholders (so unread cross-project mail from a now-tombstoned project simply
  never surfaces). **Archived projects are NOT affected** — their messages display fully (D5). `thread` is the
  one view that acknowledges the holes: where a visible message's chain passes through hidden/purged nodes it
  renders an explicit *"incomplete chain — message(s) removed (project tombstoned)"* warning rather than
  silently skipping or failing.
- The tracker row flips to `tombstoned` and **remains forever** — the permanent marker that the project existed
  and was ended.

**D7 — Cooperative watcher eviction (inherited).** Archive (and hence every path to tombstone) evicts live
notifiers via the existing seam: `notifier_tombstone(recipient)` per live watcher → bounded wait (≈ 2 poll
cycles, from `$SANDESH_POLL_SECONDS`) for the self-exit (code 3) → `notifier_reap_if_stale` sweeps residue.
Refuse-by-default if a watcher stays live; `--force` reaps and proceeds. **Terminology note:** the `notifier`
table's `tombstone` column (a per-watcher cooperative-shutdown flag, locked semantics #8) and the project
lifecycle state `tombstoned` are **distinct concepts that share a word** — code keeps both names; docs must
disambiguate ("watcher tombstone" vs "project tombstone").

**D8 — Authorization: two tiers — project Mainline for archive/unarchive, a Sandesh ADMIN super-user for
tombstone.** Only the project's own **Mainline** may archive/unarchive it (CLI and MCP carry a **`by`**
requester for the check + audit). **Tombstone is stricter: only the Sandesh backend admin — a single,
identified SUPER-ADMIN persona that exists OUTSIDE any project — may tombstone a project.** This is an
**application-level Sandesh identity, NOT a unix/OS user** — no euid/root checks; it is a persona registered
with Sandesh itself, like the orchestrator addresses are (but global: it is not a
`'<Orchestrator> - <Project>'` address). Not even a project's Mainline can tombstone (it can only take its
project as far as `archived`; destruction is the admin's decision). Checked by `tombstone --by <admin>`; the
admin is **assigned during install** (`install.sh`, human-run — never from an agentic session, so no
agent-reachable surface can create or change it); stored-form details in O3 (§7). Guards: CLI interactive confirm bypassable with
`--yes`; `--dry-run` reports what would happen (watchers to evict; on tombstone: counts of internal rows to
purge, bodies to delete, and **cross-project messages whose bodies would be lost / threads that would hole**)
and writes nothing. Honor-system, as ever — local cooperative orchestrators.

**D9 — Surface: 3 lifecycle verbs on the CLI; only the reversible pair on MCP.** CLI subcommands
`sandesh archive|unarchive|tombstone --project <id> --by <addr>` (+ `--yes`/`--dry-run`/`--force` where
applicable). **MCP exposes `sandesh_archive` + `sandesh_unarchive` only** (an orchestrator may soft-close /
resume a project at workflow boundaries) — each with a **required** `project_id` (no `$SANDESH_PROJECT`
fallback on lifecycle ops). **`tombstone` is deliberately NOT an MCP tool** — it is a backend/admin operation
(destructive, irreversible), CLI-only, mirroring `migrate`'s D8 precedent of keeping destructive/maintenance
ops off the agent surface. The MCP verb surface grows 9 → 11. On the *existing* verbs, `project_id` becomes
optional where derivable from addresses (send/reply/fetch/inbox/thread) but is retained as an explicit arg for
compatibility and for `setup`/`addressbook`.

**D11 — Cross-project access control: one-time admin approval, GRANTED PER PROJECT (owner, 2026-06-11).**
Cross-project sending is **not** open by default — rudimentary access control: the super-admin approves a
**project**, and the grant is **global to that project and inherited by every participant** — each address of
an approved project may send cross-project by inheritance; there are no per-address grants. The approval is **one-time** (granted once, persists; no expiry). **Revocation is project-wide
too**: revoking strips cross-project access from **all participants of that project** at once. Enforced at
`send`/`reply` time: if any recipient lies outside the sender's project and the **sender's project** holds no
grant, the send is rejected (exact error: `cross-project sending not approved for project '<id>' — ask the
Sandesh admin`). Receiving needs no approval (an approved sender's mail is deliverable to any active-project
address). Grant/revoke are **admin ops, CLI-only** (like tombstone — D8/D9 boundary):
`sandesh grant|revoke --cross-project --project <id> --by <admin>`; stored on the **`project` tracker row**
(`xproj_granted_at`, `xproj_granted_by` — exact shape at gap-analysis); `addressbook`/`projects` listings
expose the flag so participants can see which projects may reach across.

**D10 — Consolidation of existing stores (one-time, installer-driven glue).** On update, the installer
detects legacy `projects/<id>/sandesh.db` files and consolidates them into the global DB: re-insert messages
with **remapped ids** (fixing `in_reply_to`, `message_recipient.message_id`; `body_path` is stored absolute and
the files don't move, so paths stay valid even though filenames embed old ids), merge `address` rows (PK
collisions impossible — addresses embed the project), enroll each project `active` in the tracker, then rename
each legacy DB to `sandesh.db.pre-global` (kept as backup, ignored thereafter). This is **glue, not a yoyo
step** (it is cross-DB), exactly like the baseline-adoption glue of CR-SAN-017; the global DB itself is
created/evolved by the normal yoyo chain. `migrate --all` collapses to the single target (flag kept as alias).

## 4. Architecture / layout

```
<data_home>/sandesh/
  sandesh.db                     # THE database (WAL): project, address, message, message_recipient, notifier
  projects/<id>/messages/        # body files only, owned by the sender's project (msg-<n>.md)
  projects/<id>/sandesh.db.pre-global   # post-consolidation backup of a legacy store (inert)

sandesh/
  sandesh_db.py    connect() → the one DB; + project_state()/enroll(); + archive()/unarchive()/tombstone_project()
                   (eviction via the existing notifier seam); send/reply relax the cross-project check (D3);
                   fetch/thread gain the degraded-render warnings (D6)
  cli.py           + archive|unarchive|tombstone subcommands (D8/D9 guards); --project optional where derivable
  notify.py        polls the one DB (simpler); exit codes unchanged
  mcp_server.py    + sandesh_archive/sandesh_unarchive ONLY (D9 — tombstone is CLI-only); docstring updates
  migrations/      000N-global-… yoyo steps (project table; schema now global); consolidation glue in installer
```

## 5. CR breakdown (spin from this — numbers provisional until queued)

| CR | Scope | Depends on |
|---|---|---|
| **CR-SAN-022** | **Global DB + tracker + consolidation**: `project` table migration; `connect()`/`setup`/`list_projects` rework to the single DB (WAL); body-folder helper retained; the one-time consolidation glue (id remap, `.pre-global` backups) + installer hook; `migrate --all` → single target. | CR-SAN-017/018 (engine) |
| **CR-SAN-023** | **Cross-project messaging**: drop the send-time project-equality check; tracker-state checks on every mutating verb (distinct errors); `all-tracks` scoped to sender's project; notify against the global DB; To-wakes/Cc-silent across projects proven by tests. | CR-SAN-022 |
| **CR-SAN-024** | **Lifecycle verbs**: `archive`/`unarchive`/`tombstone_project` core ops (D4–D7: state machine, read-only enforcement, eviction reuse, T1 folder delete + internal-row purge, hidden-from-reads rule + thread warnings); CLI subcommands with confirm/`--yes`/`--dry-run`/`--force`; two-tier authz (D8: Mainline for archive/unarchive, super-admin for tombstone) + **installer admin assignment** (O3). | CR-SAN-023 |
| **CR-SAN-025** | **MCP surface update**: `sandesh_archive` + `sandesh_unarchive` tools (D9 — tombstone deliberately absent from MCP), `project_id` optionality on existing verbs, docstrings/`instructions`/usage-resource updates, stdio E2E extended to a cross-project + archive scenario. | CR-SAN-024 |

## 6. Verification (test pyramid — informs the CRs' ACs)

- **Tier 1 — deterministic integration (the bulk, no subprocess).** Liveness seeded via
  `notifier_acquire(con, addr, os.getpid(), tok, host)` exactly as the existing suite does. Cover: cross-project
  send/reply round-trips (+ To/Cc roles); `all-tracks` stays in-project; tracker-state errors (archived/
  tombstoned/unknown, both directions); archive → reads fully intact + watcher evicted + unarchive resumes;
  two-step enforcement (tombstone-from-active errors); tombstone → internal rows purged, cross-project rows
  survive in the DB but `inbox`/`fetch` **hide** to/from-tombstoned traffic (while archived-project traffic
  still displays), bodies gone, `thread` renders the incomplete-chain warning; `--dry-run` writes nothing and
  reports the would-lose counts; non-Mainline `by` rejected; **consolidation**: fixture legacy stores (incl. colliding message ids +
  threads) → consolidated DB with correct remapping, `.pre-global` backups left.
- **Tier 2 — exactly ONE real-subprocess E2E (the capstone).** Two projects in a temp `XDG_DATA_HOME`;
  `Popen(["sandesh","notify",…])` for a recipient in P1; **poll-with-timeout** until acquired; cross-project
  send P2→P1 → watcher exits 0 (mail); relaunch; `archive` P1 → watcher exits 3 (evicted). Never
  sleep-and-hope; kill-on-timeout. Mirrors CR-SAN-004/019 precedents.

## 7. Open questions

- **O1 — re-enrolling a tombstoned project id: RESOLVED (owner, 2026-06-11) — DON'T reuse.** `setup` of a
  tombstoned id **refuses** with a precise error ("project id retired (tombstoned) — choose a new id"). A
  tombstoned id is retired forever; the marker stays unambiguous.
- **O2 — `sqlite_sequence` / id behavior post-consolidation** and WAL-mode rollout on existing-file upgrades —
  mechanical, verify at CR-SAN-022 gap-analysis (read real SQLite behavior, don't assume).
- **O3 — super-admin enrollment: RESOLVED (owner, 2026-06-11) — assigned during INSTALL.** The admin is an
  application-level Sandesh persona (NOT a unix user), and it is **assigned by `install.sh`** — which a human
  operator runs, **never from within an agentic session**. That placement is itself the security boundary: no
  agent-reachable surface (MCP, or even the post-install CLI) can create or change the admin. **Stored form
  APPROVED (owner, 2026-06-11):** one reserved global row (`kind='admin'`, fixed project-less name), value
  supplied at install via `$SANDESH_ADMIN` or an interactive prompt; **re-running install must NOT silently
  re-assign it**; `tombstone --by` must match it. **Placement RESOLVED (CR-SAN-023 gap-analysis DEC-C,
  user-decided 2026-06-11): a dedicated single-row `admin` table** (the admin is not an address — never
  messageable/registrable/listable); storage + installer assignment ship in CR-SAN-023 (DEC-D pulled the
  assignment forward), CR-SAN-024 consumes it.

**Resolved by owner review (2026-06-11):** single global DB over hybrid (easier migration, simpler connection
code); global tracker with tombstoned state; **archive vs tombstone split** (archive = blocks sends, full reads,
nothing deleted; tombstone = disk cleanup + data removal with incomplete-chain warnings — the owner's framing);
T1 content-dies-with-origin for cross-project bodies; purge project-internal rows on tombstone; **two-step
mandatory** (active→archived→tombstoned); **unarchive supported**; Mainline-only + `by`; confirm/`--yes` +
`--dry-run`; cross-project comms accepted as the reason to re-open locked semantics #2; **archived participants
can neither send nor receive** (rejected at the sender, no queueing); **standard reads hide to/from-tombstoned
traffic; archived projects' traffic displays fully; `thread` warns on incomplete chains**; **archive/unarchive
on MCP, tombstone CLI-only ("truly backend admin")**; **tombstone requires the Sandesh super-admin** — an
application-level persona (not a unix user), **assigned during install** (human-run, outside agentic sessions);
a project's Mainline can take it only as far as `archived`; **cross-project access control (D11)**: one-time
admin approval **granted per project** (all participants covered), revocation likewise project-wide, grant/
revoke CLI-only admin ops, enforced at send.
