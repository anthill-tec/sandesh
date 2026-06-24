# PRD — Sandesh CR Registry (per-project CR metadata + scheduling state)

**Version:** 0.1
**Date:** 2026-06-18
**Status:** DRAFT
**Owner:** Mainline - Sandesh
**Wave:** Wave 3 (after the MCP server, Wave 2)
**Authors:** AJ + Vidushi (vidushi)

This is the design contract (WHY + WHAT) for a **second coordination plane** in Sandesh:
alongside *messages between orchestrators*, a registry of *the work those orchestrators do*
— Change Requests (CRs), their dependencies, state, and dynamic track assignment. CRs
derived from this PRD cite it via `**Design reference:**` and implement the HOW.

### Change Control

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1 | 2026-06-18 | AJ + VD | Initial draft — problem, key functionality, data model (`cr`/`cr_dep`/`cr_event`), verb surface, authority model, escalation flow, worktree-flow migration path (Phase 0/1/2). |

---

## 1. Why

The Model-B workflow (one **Mainline** coordinator + parallel **Track** workers per project)
already runs on Sandesh for *messaging*. But the **scheduling state** — which CRs exist, in
what dependency order, who is working on what, and what state each is in — lives in two other
places, both of which hurt:

1. **The README queue (git-versioned markdown).** A track that edits the queue table to mark a
   CR `IN_PROGRESS`/`COMPLETED` does so **inside its git worktree** → merge conflicts on the
   shared table, and the integration tree **cannot see the status until the branch merges**
   (the "filesystem doesn't reflect live state" problem). Status updates also churn git history
   and burn tokens editing a large table.
2. **A per-project local SQLite mirror** (`worktree-flow`'s `.nai-schedule.db` + `schedule_db.py`).
   This already proves the model works (one row per CR; tracks advance `IN_PROGRESS→COMPLETED`;
   Mainline reads it) — but it is **local, single-project, gitignored, and duplicates the
   project + track facts Sandesh already owns**.

Meanwhile Sandesh **already holds** everything this needs: a multi-project store (`project`
table + project-column scoping), the `address` model (`Mainline`/`Track N` = the actors and
assignees), `notifier`-based wake, and a clean library/CLI/MCP three-front architecture. The CR
registry is the natural **"what work exists and who's on it"** plane sitting beside the existing
**"what they said to each other"** plane — and unifying them means **one MCP, one store, one
scoping model** for all of Model-B coordination.

It also unlocks the scheduling change this is really for: **dynamic track assignment**. Today a
CR is pre-bolted to a track lane. With a registry as the source of truth, a CR is sequenced
**by dependency only**, and Mainline assigns the next *ready* CR to whichever track is *free* at
dispatch time — dispatched over the existing Sandesh `directive` message.

## 2. What it is (and is not)

**Is:** a per-project **CR registry** — new tables in the existing global `sandesh.db` (project-
scoped exactly like `address`/`message`), plus the verbs to register / assign / advance / query
CRs, exposed on **both** front ends (CLI + `@mcp.tool()`), the same thin-adapter role `cli.py`
and `mcp_server.py` already play over `sandesh_db.py`.

**Is not:**
- **Not a store of CR content.** The CR **spec files stay on disk in each project's own repo**
  (`docs/changes/CR-….md`). The registry holds **metadata + a reference** (`spec_path`), the
  same split Sandesh already uses for messages (metadata in the DB, bodies as per-project files).
- **Not the README.** The README (or any human view) becomes a **coarse, generated or
  hand-kept summary**; the **DB is the live source of truth** for status + assignment.
- **Not a replacement for messaging.** Dispatch, escalation, and unblock still flow as Sandesh
  `directive`/`request` messages. The registry is the *state*; messaging is the *signaling*.
- **Not a dependency/build engine.** It records `depends_on` edges and computes *readiness*; it
  does not run anything.

## 3. Key functionality (the contract)

> The eight capabilities this feature MUST provide. Each maps to a verb in §5.

1. **CR registration (planning-time).** During upstream planning, Mainline registers each CR:
   `cr_id`, `title`, `wave`, `depends_on[]`, `spec_path`, `heading`. Idempotent upsert.
2. **Dependency chains preserved.** `depends_on` edges are first-class and **never dropped** —
   code-level dependencies are real. A CR is **READY** only when every `depends_on` CR is
   `COMPLETED`.
3. **Dynamic (un-fixed) track assignment.** A CR carries **no track until dispatch**. Mainline
   assigns a READY CR to a *free* track at dispatch time (`cr_assign`) — lanes are not
   pre-planned.
4. **State lifecycle.** `PENDING → ASSIGNED → IN_PROGRESS → COMPLETED` (+ `ABORTED`). A track
   advances **only the CRs assigned to it** (`cr_start`, `cr_complete`); Mainline owns the rest.
5. **Authority model (mirrors addresses).** Mainline registers/assigns/re-sequences/aborts; a
   Track may only `start`/`complete` a CR whose `assignee` is itself. Reads are open within the
   project.
6. **Escalation handling without git churn.** When a track discovers a new dependency or a scope
   change during gap-analysis, it **raises a Sandesh `request`** (it never self-reschedules);
   Mainline **updates the CR row** (`cr_update`: add a dep, re-sequence, re-scope, split) — a
   single DB write, **zero README/git conflict** — and re-dispatches.
7. **Query surface.** `cr_next` (the next READY+unassigned CR for Mainline to dispatch, or a
   given track's current), `cr_list` (the board, filterable by state/assignee/wave), `cr_status`
   (one CR + its deps + computed readiness).
8. **Project scoping.** Every verb is routed by `project_id`, identical to messaging — each
   project's Mainline sees **only its own** CRs in the shared store.

## 4. Data model

New tables in the existing global `sandesh.db` (added via a yoyo migration — next is
`0006-cr-registry.sql` + its `.rollback.sql`). Scoping column `project` matches `address.project`
and the `project` table's `project_id`.

```sql
CREATE TABLE IF NOT EXISTS cr (
    project     TEXT NOT NULL,                       -- scoping key (FK-style → project.project_id)
    cr_id       TEXT NOT NULL,                       -- e.g. 'CR-NAI-329'
    title       TEXT NOT NULL,
    heading     TEXT,                                -- short slug source (branch naming)
    wave        TEXT,                                -- coarse grouping label (free text)
    state       TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ('PENDING','ASSIGNED','IN_PROGRESS','COMPLETED','ABORTED')),
    assignee    TEXT,                                -- the assigned address ('Track N - <Project>'); NULL until dispatch
    spec_path   TEXT,                                -- path to the CR spec FILE in the project repo (reference, not stored)
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project, cr_id)
);

CREATE TABLE IF NOT EXISTS cr_dep (
    project    TEXT NOT NULL,
    cr_id      TEXT NOT NULL,                        -- the dependent CR
    depends_on TEXT NOT NULL,                        -- the prerequisite CR (same project)
    PRIMARY KEY (project, cr_id, depends_on)
    -- READY(cr) ⟺ every depends_on row for (project, cr) has state = 'COMPLETED'
);

-- Append-only audit of every transition + re-sequence/re-scope (escalation history).
CREATE TABLE IF NOT EXISTS cr_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    cr_id      TEXT NOT NULL,
    actor      TEXT NOT NULL,                        -- the address that made the change
    event      TEXT NOT NULL,                        -- 'register'|'assign'|'start'|'complete'|'abort'|'update'
    detail     TEXT,                                 -- JSON: what changed (old→new state, dep added, …)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS cr_event_lookup ON cr_event(project, cr_id, id);
```

**Readiness** is computed, not stored: `READY(cr)` ⟺ `cr.state = 'PENDING'` AND no `cr_dep` row
for it points at a non-`COMPLETED` CR. (Equivalent to the current `merged:CR-X` trigger.) A
"tree-quiet" predicate — no CR `IN_PROGRESS` in the project — is the current `;quiet` clause,
used to serialize a cross-cutting CR.

## 5. Verb surface (CLI + MCP)

Mirrors the existing pattern: a `sandesh_db.py` function (the model), a `cmd_*` + `add_parser`
in `cli.py`, and an `@mcp.tool()` in `mcp_server.py` with the right `ToolAnnotations`.

| Verb | Annotation | Caller | Purpose |
|---|---|---|---|
| `cr_register(project_id, cr_id, title, wave?, depends_on?[], spec_path?, heading?)` | idempotent | Mainline | Add/upsert a CR at planning time. |
| `cr_assign(project_id, cr_id, assignee)` | — | Mainline | Assign a READY CR to a free track → `ASSIGNED`. |
| `cr_update(project_id, cr_id, {title?, wave?, depends_on?, spec_path?, note?})` | — | Mainline | Re-sequence / re-scope / add-dep (escalation). |
| `cr_abort(project_id, cr_id)` | destructive | Mainline | Cancel/supersede → `ABORTED`. |
| `cr_start(project_id, cr_id)` | — | assigned Track | `ASSIGNED → IN_PROGRESS` (own CR only). |
| `cr_complete(project_id, cr_id)` | — | assigned Track | `IN_PROGRESS → COMPLETED` (own CR only). |
| `cr_next(project_id, assignee?)` | readOnly | any | No `assignee` → the next READY+unassigned CR for Mainline to dispatch; with `assignee` → that track's current/next + readiness. |
| `cr_list(project_id, state?, assignee?, wave?)` | readOnly | any | The board (the `worktree-flow status` lane view). |
| `cr_status(project_id, cr_id)` | readOnly | any | One CR + its deps + computed readiness. |

All verbs route by `project_id` (falling back to `$SANDESH_PROJECT`, as the messaging verbs do).

## 6. Authority model

Reuses Sandesh's existing address-authority shape (Mainline-privileged vs self-only):

- **Mainline-only:** `cr_register`, `cr_assign`, `cr_update`, `cr_abort`. (Same privilege tier as
  `grant`/`unregister-anyone`.)
- **Assigned-track-only:** `cr_start`, `cr_complete` — rejected unless `cr.assignee` equals the
  caller's address. (Same shape as address self-ops.) **This is the same guarantee as today:** a
  track only advances CRs Mainline explicitly handed it.
- **Open reads (in-project):** `cr_next`, `cr_list`, `cr_status`.

## 7. The dynamic-assignment flow

```
PLAN  : Mainline  cr_register(CR, depends_on=[…])           → PENDING, assignee=NULL
READY : when all depends_on COMPLETED                        → READY (computed)
DISPATCH: a track frees up → Mainline cr_next() → cr_assign(CR, "Track k") + sandesh_send(directive)
RUN   : Track  cr_start(CR)                                  → IN_PROGRESS
DONE  : Track  cr_complete(CR)                               → COMPLETED  (unblocks dependents)
```

Track lanes are **emergent**, not pre-planned: the same CR could run on any free track. The only
fixed constraint a project layers on top (e.g. NAI's *file-disjointness* / single-track-`nai_ast`)
is enforced by **Mainline at `cr_assign` time**, not by the registry.

## 8. Escalation handling (the motivating case)

A track in gap-analysis discovers CR-B actually needs CR-A first (a dep not in the plan):

1. Track → `sandesh_send(kind="request", to=["Mainline - <P>"], …)` — *raises*, never reschedules.
2. Mainline → `cr_update(CR-B, depends_on += CR-A)` (and `cr_register(CR-A, …)` if new) — a DB
   write; `cr_event` records it. CR-B's readiness flips to blocked automatically.
3. Mainline → `sandesh_reply` (disposition) and, when CR-A completes, `cr_assign(CR-B, …)` +
   `directive`.

No README edit, no worktree git conflict, no token-heavy table rewrite — the exact failure mode
§1 calls out.

## 9. Relationship to worktree-flow + the migration path

`worktree-flow`'s ChangeSet commands map 1:1 onto these verbs — it becomes a **thin client** of
the registry instead of owning `schedule_db.py`:

| worktree-flow today | registry verb |
|---|---|
| `cs --cr … --heading … --trigger …` | `cr_register` (+ `cr_dep`) |
| `cs --cr … --track … --seq …` | `cr_assign` |
| `start --cr …` | `cr_start` |
| `finish --cr …` (DB half) | `cr_complete` |
| `next --track …` | `cr_next(assignee=…)` |
| `status` (lane board) | `cr_list` |

**Rollout (decided with AJ 2026-06-18):**
- **Phase 0 — NOW, no Sandesh change (interim through the demo).** NAI keeps its **own**
  `.nai-schedule.db` + `worktree-flow`, but adopts the two behavioural changes this enables:
  *dynamic assignment* (drop the pre-fixed `track`; assign at dispatch) and *DB-as-status-truth*
  (status lives in the DB, not the README queue). No dependency on Sandesh shipping.
- **Phase 1 — this PRD.** Sandesh ships the `cr`/`cr_dep`/`cr_event` tables + the verbs
  (migration `0006`).
- **Phase 2 — cut over.** `worktree-flow` calls the Sandesh registry (CLI or MCP); `schedule_db.py`
  is retired. NAI migrates its rows in; other projects onboard by registering their CRs.

## 10. Verified platform facts (Sandesh internals)

Confirmed against the live source (not memory):
- **Store:** one global `sandesh.db` at `<XDG_DATA_HOME>/sandesh/sandesh.db`; project scoping via
  a `project` column + the `project` table (`active`/`archived`/`tombstoned`). The CR tables join
  this model unchanged.
- **Migrations:** yoyo, numbered SQL + rollback in `sandesh/migrations/` (`0001`…`0005`); next is
  `0006-cr-registry.sql`. `connect()` lazy-auto-heals when the `[migrate]` extra is present.
- **Fronts:** model in `sandesh_db.py`; CLI `cmd_*` + `add_parser` in `cli.py`; MCP `@mcp.tool()`
  (FastMCP) in `mcp_server.py` with `ToolAnnotations(readOnlyHint/destructiveHint/idempotentHint)`.
- **Actors:** `address` rows (`Mainline`/`Track N` per project) are the registrants + assignees —
  the `assignee` column references the same address strings the messaging verbs already validate.
- **Spec files are NOT ingested:** like message bodies (per-project files referenced by
  `body_path`), CR spec files stay in the project repo; the registry stores only `spec_path`.

## 11. Open questions

- **Readiness predicate location** — compute in `sandesh_db.py` (a `cr_ready(project)` query) vs
  a generated column / view. Lean: a query, like the current trigger evaluation.
- **`quiet`/serialize semantics** — keep the project-wide "no `IN_PROGRESS`" gate as an optional
  `cr_assign` guard, or push that policy entirely to Mainline? Lean: expose a `tree_quiet`
  read; let Mainline decide.
- **Cross-project CReqs** — a consumer project filing a refinement request into a provider
  project's backlog (e.g. NAI → EntityStore). Natural fit for a future `cr` row with a cross-
  project origin, gated behind the existing `xproj` grant. Out of scope for v0.1.
- **History vs FTS** — should `cr`/`cr_event` be searchable via the existing `message_fts` pattern?
  Deferred.
