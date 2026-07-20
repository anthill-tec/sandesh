# CR-SAN-045 — Reject un-addressable `project_id`s at creation + super-admin archive of zero-address projects (unwedge the zombies)

**Status:** PROPOSED (gap-analysis complete; S2 mechanism = **Option B**, owner-decided 2026-07-20 —
keep the two-step interlock, relax the *archive* authz; carries a folded PRD-global-store **D8** amendment)
**Priority:** High (a whole *class* of projects — any id the address grammar cannot express — becomes a
permanently unmanageable zombie: it cannot be archived and therefore cannot be tombstoned. Operability /
data-lifecycle bug.)
**Depends on:** — (self-contained; builds on CR-SAN-023 enrollment + CR-SAN-024 archive→tombstone
lifecycle; touches no schema, no migration)
**Labels:** bug, lifecycle, project-id, archive-authz, hotfix-class
**Wave:** post-0.3.4 → patch release **0.3.5**
**Design reference:** BUG + FIX REQUEST — *"Sandesh: space-named projects become unmanageable zombies"* —
raised by Vidushi-modelB (`Mainline - ModelB`), 2026-07-20. Reproduced live the same day against the
installed 0.3.4 store. **S2 design fork resolved by owner (2026-07-20): Option B** — preserve the AGREED
"two-step mandatory / tombstone only from archived" invariant (PRD-global-store **D4/D6**) fully, and
instead carve the **archive authorization** (**D8**) so the super-admin may archive a Mainline-less
(zero-address) project. The requester's original ask (direct tombstone of an active project) was **not**
taken, because it would break the more safety-critical two-step interlock.

## Context

`sandesh_db.setup(project_id)` (`sandesh_db.py:240`) accepts **any** non-empty string and provisions a
store + `'active'` tracker row for it. The address grammar, however, only admits
`'<Orchestrator> - <Project>'` where `<Project>` matches the `ADDRESS_RE` sub-pattern
`[A-Za-z][A-Za-z0-9_]*` (`sandesh_db.py:58` — a letter, then letters/digits/underscores; **no spaces,
hyphens, or punctuation**), and `<Project>` must equal the `project_id` (`validate_address`
`sandesh_db.py:367`).

**The wedge.** A project whose id the grammar cannot express (`"Model B"`, `"Crucible v2"`) can never
register *any* valid address — so it can never have a `Mainline - <id>`. That makes the archive
predicate unsatisfiable:

- `archive()` → `_archive_guards()` → `_require_project_mainline(project_id, by)` (`sandesh_db.py:957`)
  requires `by` to (a) parse as a valid address **and** (b) equal `Mainline - <project_id>`. For a
  space-named id, (a) and (b) cannot both hold.
- `tombstone_project()` → `_tombstone_guards()` (`sandesh_db.py:1027`) refuses a non-archived project
  (`"archive it first"`) — by design (PRD-global-store D4/D6: two-step mandatory).

⇒ archive impossible ⇒ tombstone (reachable only from `archived`) impossible ⇒ **permanent zombie**,
visible in `projects` listings forever, un-evictable.

**Live reproduction (2026-07-20, 0.3.4):** `setup("Model B")` → ACTIVE; `register("Mainline - Model B")`
→ grammar reject; `tombstone --project "Model B"` → `"active — archive it first"`;
`archive --by "Mainline - Model B"` → grammar reject; `archive --by "Mainline - ModelB"` → `"only the
project's own Mainline may archive"`. The two archive predicates are jointly unsatisfiable.

Two independent defects, fixed together:
- **Prevention (S1):** creation does not validate the id against the grammar that gates every address —
  so an un-addressable project can be born at all.
- **No admin route to retirement (S2):** even with S1 shipped, the *already-created* zombies (and any
  zero-participant project) have no path to `archived` — the only archiver is a Mainline that cannot
  exist. **Fix: the super-admin may archive a zero-address project.** The normal two-step then applies:
  the admin archives it, then tombstones it (both super-admin, `archived → tombstoned` unchanged).

No schema change; version is tag-derived (hatch-vcs). Ships as patch **0.3.5**.

## Scope

Confined to `sandesh_db.py` (model + ops), `cli.py` (one small idempotency branch), the folded PRD-D8
amendment, and tests. `sandesh_db.py` stays pure (locked convention). **`_tombstone_guards` is NOT
touched** (the two-step precondition is preserved verbatim).

### S1 — Prevention: validate `project_id` at creation against the address `<Project>` grammar

1. Factor the `<Project>` sub-pattern out of `ADDRESS_RE` into a shared constant so the two provably use
   **one** grammar source (sole `ADDRESS_RE` consumer is `validate_address:369`; the compiled regex value
   is unchanged — refactor is behaviour-identical):
   ```python
   _PROJECT = r"[A-Za-z][A-Za-z0-9_]*"
   PROJECT_RE = re.compile(rf"^{_PROJECT}$")
   ADDRESS_RE = re.compile(rf"^(?P<orch>Mainline|Track \d+) - (?P<proj>{_PROJECT})$")
   ```
2. Add `validate_project_id(project_id)` (raises `ValueError` — empty, or non-matching id — naming the
   grammar rule and giving a `'ModelB' not 'Model B'` hint).
3. Call it as the **first statement** of `setup()` (before `store_dir()`/`makedirs`), so no store dir and
   no tracker row are created for an invalid id. Both public creation surfaces route through `setup()` —
   CLI `cmd_setup` (`cli.py:58`) and MCP `sandesh_setup` (`mcp_server.py:139`, `ValueError → ToolError`)
   — single choke point.

**Deliberately NOT validated at `store_dir()`** — that is on the read/purge path for *already-existing*
(possibly invalid-id) projects; guarding it would make the existing zombies unpurgeable. Validation lives
**only** at creation. **Also NOT** gated on `consolidate()`'s legacy import (`sandesh_db.py:1264`) — it
imports pre-existing stores as-is (Non-goals).

### S2 — Escape hatch: super-admin may archive a **zero-address** project (Option B)

1. Add helper `_has_any_address(con, project_id) -> bool` — **any** row in `address` for the project,
   **active OR soft-deleted** (`SELECT COUNT(*) … WHERE project=?`, no `active` filter). **Must not**
   reuse `active_addresses()` (`:426`, which filters `active=TRUE`) — a project whose only addresses are
   soft-deleted (unregistered) still *has* a reconstructable Mainline and must stay on the normal path.
   (Gap-analysis DRIFT-2.)
2. Relax `_archive_guards()` (`sandesh_db.py:1006`) — **additive**, no regression:
   ```python
   def _archive_guards(con, project_id, by):
       state = project_state(con, project_id)
       if state is None:      raise ValueError(f"unknown project '{project_id}'")
       if state != "active":  raise ValueError(f"project '{project_id}' is not active")
       # Escape hatch (CR-SAN-045): a zero-address project has no Mainline that could
       # ever satisfy the check; the super-admin may archive it so it can enter the
       # mandatory two-step. A grammar-valid 'Mainline - <id>' still works too.
       stored_admin = admin_name(con)
       if not _has_any_address(con, project_id) and stored_admin is not None and by == stored_admin:
           return
       _require_project_mainline(project_id, by)
   ```
   - zero-address + `by == admin` → allowed (the unwedge).
   - zero-address + `by == "Mainline - <valid-id>"` → falls through to `_require_project_mainline` →
     **still allowed** (additive; no regression for empty grammar-valid projects).
   - zero-address + grammar-invalid `by` (`"Model B"` zombie, any non-admin) → `_require_project_mainline`
     → `PermissionError`. So for the zombie the admin is the *only* working archiver — exactly the fix.
   - **≥1 address row** → straight to `_require_project_mainline` — **unchanged** (admin can NOT archive a
     populated project; only its own Mainline can).
3. `_tombstone_guards()`, `tombstone_project()`, `archive()`, `_unarchive_guards()`, `unarchive()` are
   **UNCHANGED**. Once the admin archives the empty project, the existing `archived → tombstoned`
   super-admin path retires it (its no-op deletes tolerate zero rows; the body-folder delete is already
   `rmtree(…, ignore_errors=True)` `:1146`, so a missing store dir is fine).
4. CLI idempotency (`cmd_tombstone`, `cli.py:313`): a second tombstone of a now-tombstoned project must
   report it as already gone and exit **0** (catch the `"already tombstoned"` `ValueError`, print an
   already-gone line, return 0). All other `ValueError`/`PermissionError` still exit 1. `--dry-run`,
   `--yes`/confirm gate untouched. (This is CLI-only; the library still raises — the existing
   `test_lifecycle_tombstone` idempotence test stays green.)

### PRD amendment (folded into this CR)

Amend `docs/research/PRD-global-store.md` **D8** to add the zero-address archive carve-out (super-admin
may archive a Mainline-less project; the two-step D4/D6 and the tombstone super-admin rule are unchanged),
and note it in the CR-SAN-045 row of the decisions/scope table.

## Acceptance criteria

**S1 — prevention (library + MCP):**
- [ ] **AC1** `setup("Model B")` raises `ValueError` (message contains `Model B` + `[A-Za-z][A-Za-z0-9_]*`);
      afterwards `project_state(con,"Model B") is None` **and** `store_dir("Model B")` does not exist.
- [ ] **AC2** `setup("ModelB")` still succeeds — returns the store dir, dir exists,
      `project_state(con,"ModelB") == "active"`. (Regression.)
- [ ] **AC3** Rejected each raise from `setup`: `"Model B"`, `"model-b"`, `"2fast"`, `"a.b"`,
      `"Crucible v2"`, `""`. Accepted each succeed: `"ModelB"`, `"Nai"`, `"P2"`, `"a_b"`, `"x1"`.
- [ ] **AC4** Single-grammar-source pin: for a mixed id table, `validate_project_id(pid)` succeeds **iff**
      `ADDRESS_RE.match(f"Mainline - {pid}")` is not `None`.
- [ ] **AC5** MCP: `sandesh_setup` tool with `project_id="Model B"` raises FastMCP `ToolError` and creates
      no store dir / tracker row; with `"ModelB"` returns the store path.

**S2 — super-admin archive of zero-address project (library + CLI):**
- [ ] **AC6** `archive(con, "Empty", <admin>)` on an **active, zero-address** project succeeds:
      `project_state` → `"archived"`, `archived_at` non-NULL.
- [ ] **AC7** AC6 also succeeds when `store_dir("Empty")` is **absent** (archive touches no store dir) —
      models the out-of-band-deleted `"Model B"`.
- [ ] **AC8** Additive (no regression): `archive(con, "Empty", "Mainline - Empty")` on the active
      zero-address project **also** succeeds (grammar-valid Mainline still archives).
- [ ] **AC9** Authz still holds on the escape hatch: `archive(con, "Empty", "random")` and
      `archive(con, "Empty", "Track 1 - Empty")` raise `PermissionError`, state stays `"active"`.
- [ ] **AC10** Tight carve-out (regression): for an **active project WITH ≥1 address row** (`"Full"`),
      `archive(con, "Full", <admin>)` raises `PermissionError` (admin may not archive a populated
      project) while `archive(con, "Full", "Mainline - Full")` succeeds.
- [ ] **AC11** Soft-deleted ≠ zero (DRIFT-2 pin): a project with its only address `register`ed then
      `unregister`ed (→ 1 soft-deleted row) is **not** a zero-address project — `archive(con, X, <admin>)`
      raises `PermissionError` (admin path not taken; `_has_any_address` counts soft-deleted rows).
- [ ] **AC12** Two-step preserved (regression — the crux of Option B): `tombstone_project(con, "Empty",
      <admin>)` while `"Empty"` is **active** (even though zero-address) still raises `ValueError`
      containing `"archive it first"`; state unchanged. (`_tombstone_guards` untouched.)
- [ ] **AC13** Full unwedge E2E: for a zero-address active `"Zombie"` with its store dir **deleted**:
      `archive(con,"Zombie",<admin>)` then `tombstone_project(con,"Zombie",<admin>)` succeed in sequence;
      final `project_state == "tombstoned"`, `"Zombie"` absent from `list_projects()`, no exception.
- [ ] **AC14** `archive_preview(con, "Empty", <admin>)` (dry-run) on the active zero-address project
      returns `[]` (no live watchers) and writes nothing — state still `"active"`.
- [ ] **AC15** CLI idempotency: `cmd_tombstone` (`args.yes=True`) run on an already-tombstoned project
      returns **0** and prints an *already gone / already tombstoned* line (not exit 1). The project
      appears in no `projects` listing.

## Estimated size

Small. `sandesh_db.py`: constant split + `validate_project_id` (~10 lines) + one `setup` call +
`_has_any_address` (~4 lines) + ~4-line additive branch in `_archive_guards`. `cli.py`: ~4-line
idempotency branch in `cmd_tombstone`. PRD: one D8 paragraph + a table note. Tests: one new S1 file + one
new S2 file (zero-address / populated / soft-deleted fixtures + the full-unwedge E2E). No schema, no
migration, no MCP tool-surface change.

## Risks / open questions

- **DECIDED (owner, Option B):** carve archive authz (D8), not the two-step (D4/D6). `_tombstone_guards`
  untouched, so the safety-critical `active→archived→tombstoned` interlock is fully preserved.
- **Unarchive of an admin-archived empty project stays Mainline-only (accepted asymmetry — Non-goal).**
  `_unarchive_guards` is NOT carved. An admin who archives a zero-address project completes retirement via
  `tombstone` (the intended flow); it cannot be `unarchive`d back (no Mainline). Low stakes (the project
  is empty). Flagged for visibility; add a symmetric carve later only if a real need appears.
- **Additive semantics chosen** (super-admin *in addition to* a grammar-valid Mainline for zero-address),
  matching the owner's "super-admin allowed IFF 0 address rows" framing — so empty grammar-valid projects
  keep their existing Mainline-archive behaviour unchanged.

## Non-goals

- **No change to the address grammar.** Space-named *addresses* stay invalid; this CR makes `project_id`
  conform to the existing grammar, not the reverse.
- **No auto-migration / renaming of existing zombies** — they are *purged* via the S2 archive+tombstone
  path, not rescued.
- **No change to the two-step or the tombstone precondition** — `tombstone` remains reachable only from
  `archived` (PRD D4/D6 intact).
- **`consolidate()` legacy import is not gated by S1** (`sandesh_db.py:1264`) — historical stores import
  as-is.
- **No new MCP tool** — archive is already an MCP tool but its authz is honor-system `by`; tombstone stays
  CLI-admin-only. No MCP surface change.

## Post-merge operational cleanup (AFTER the 0.3.5 release + re-install)

Purge the two known zombies with the shipped two-step (super-admin `--by`):
```
sandesh archive   --project "Model B"    --by <admin>           # store dir already gone → tolerated
sandesh tombstone --project "Model B"    --by <admin> --yes
sandesh archive   --project "Crucible v2" --by <admin>
sandesh tombstone --project "Crucible v2" --by <admin> --yes
```
`--dry-run` first to preview. Idempotent — a re-run of `tombstone` reports "already gone".
