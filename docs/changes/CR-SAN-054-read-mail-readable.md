# CR-SAN-054 — read mail stays readable: search snippets, caller-aware thread bodies, `inbox --with-body`, unknown projects refused

**Type:** bugfix + small feature
**Priority:** High (a recipient cannot re-read a request it has already read; reported by Crucible, #1422)
**Depends on:** CR-SAN-027, CR-SAN-028, CR-SAN-047, CR-SAN-048
**Labels:** cli, mcp, pi, axi, search
**Wave:** 18 · **Release:** 0.4.2
**Design reference:** [PRD-inbox-search](../research/PRD-inbox-search.md) (search contract),
[PRD-axi-toon](../research/PRD-axi-toon.md) §4.0 (AXI shaping)

**Author:** Antony John
**Co-author:** Vidushi-Sandesh (Mainline — Sandesh)

## Context

Crucible's Mainline tried to re-read a request it had already read (#1412) and found no read verb that
returns the body of read mail:

- **Search drops its snippet.** `sandesh_db.search()` returns every hit with an FTS5 `snippet`, and the
  MCP `sandesh_search` and human-mode `search` show it. The CLI machine mode (`--format toon|json`, also
  what the Pi `sandesh_search` tool uses) maps each hit through the fixed `SEARCH_DEFAULT`
  (`id, from, subject`), and `search` has no `--fields`, so the snippet never reaches the agent.
- **Thread bodies depend on an identity the caller cannot pass.** `axi_thread` returns a body only when
  `$SANDESH_ADDRESS` (or `$WF_TRACK`) is set, validates for the `--project`, and is the sender or a
  recipient of that message. Otherwise `bodies` is empty, with no hint why. The MCP `sandesh_thread` has no
  caller identity at all and never returns a body.
- **`fetch --full`** returns bodies of unread mail only, and marks them read. Nothing re-reads in batch.
- **A wrong project id answers empty.** `addressbook --project crucible` (the id is `Crucible`) returns
  `0 registered in crucible`, which reads as "nobody is registered", not "no such project".

The owner's decisions (2026-10-07): a patch release 0.4.2 off `develop`; `thread` takes an explicit
caller and says loudly when it withholds bodies (no new verb); include the snippet fix, the
unknown-project refusal and `inbox --with-body`.

## Scope

### §S1 — search returns its snippet in machine mode (CLI + Pi)

- `cli.py` gains `SEARCH_FIELDS = ("id", "from", "subject", "kind", "created", "role", "snippet")`;
  `SEARCH_DEFAULT` becomes `("id", "from", "subject", "snippet")`.
- `search` accepts `--fields CSV` (validated by `_fields_arg(SEARCH_FIELDS)`, like `thread`/`inbox`);
  `axi_search` maps hits through `args.fields or SEARCH_DEFAULT`. Each hit's `snippet` is the library's
  snippet text, unchanged (`[`…`]` highlight, `…` elisions). `created` maps from `created_at`.
- The Pi `sandesh_search` tool gains an optional `fields: string[]` parameter, passed as `--fields`.
- Human mode and the MCP `sandesh_search` are unchanged (both already show the snippet).

**Surfaces (verified 2026-10-07):** `sandesh/cli.py` `SEARCH_DEFAULT` (l.905), `axi_search`
(l.1147–1155), `search` parser (l.1515–); `sandesh/sandesh_db.py` `search()` (l.817–);
`integrations/pi/src/index.ts` `sandesh_search` (l.1046–).

### §S2 — `thread --as`, and withheld bodies are announced (CLI + Pi)

- `thread` gains `--as ADDRESS` (the caller's own address; default `$SANDESH_ADDRESS`, then `$WF_TRACK`).
  The rule for showing a body is unchanged: the caller is valid for `--project` and is the message's sender
  or one of its recipients (to or cc, read or unread).
- When at least one message in the chain has a body that is not shown, the payload gains
  `withheld: <n>` (the count), and `help[]` gains the template
  `sandesh --project <P> thread --id <id> --as '<your address>'`. With nothing withheld there is no
  `withheld` key (the payload shape is unchanged).
- An explicit `--as` that is not a valid address for `--project` fails the verb (`ok:false`, the
  `validate_address` error). A caller taken from the environment that is not valid for `--project` is
  treated as absent, as today: metadata is returned with `rc 0`, and its bodies count as withheld
  (the 0.4.0 body-authorization rule, pinned by `tests/test_axi_disclosure.py` l.287–306, is kept).
- The Pi `sandesh_thread` tool gains an optional `requester: string` parameter, passed as `--as` (the
  same name `sandesh_unregister` already uses for its `--as`).

**Surfaces (verified 2026-10-07):** `cli.py` `axi_thread` (l.1158–1196), `thread` parser (l.1446–);
`index.ts` `sandesh_thread` (l.965–).

### §S3 — the MCP `sandesh_thread` returns bodies to a named caller (MCP)

- `sandesh_thread` gains an optional `requester: str | None = None` parameter (owner ruling 2026-10-07:
  the name `sandesh_unregister` already uses for the caller's own address; `as` is a Python keyword).
- With `requester` set and valid, each returned chain dict for a message whose sender or recipient is
  `requester` and which has a body gains `body` (the full text). Subject-only messages get no `body` key.
  A well-formed requester that is not a party gets no `body` and no error. Without `requester` the
  result is unchanged.
- Validation mirrors the CLI's `--as` + `--project` (owner ruling 2026-10-07): the existing, until now
  unused `project_id` parameter becomes the project check. With `project_id`, `requester` must pass
  `validate_address(requester, project_id)`; without it, only the address format is checked. A failure
  raises `ToolError` with the `validate_address` message. The project is never derived from the thread,
  so a cross-project recipient keeps reading its own mail.
- The docstring states that bodies need `requester`, and the `project_id` description states its new
  role.

**Surfaces (verified 2026-10-07):** `sandesh/mcp_server.py` `sandesh_thread` (l.318–345).

### §S4 — `inbox --with-body` re-reads in batch, never marking (CLI + Pi + MCP)

- CLI `inbox` gains `--with-body` and `--full`. With `--with-body`, the payload gains
  `bodies: {"<id>": text}` for the listed rows that have a body (after `--limit`); each body is cut to
  `BODY_LIMIT` characters unless `--full`, and a cut adds the `inbox … --with-body --full` template to
  `help[]`. Works with or without `--all`. Nothing is marked read.
- The Pi `sandesh_inbox` tool gains optional `with_body: boolean` and `full: boolean`, passed as
  `--with-body` / `--full`.
- The MCP `sandesh_inbox` gains `with_body: bool = False`; when true, each returned row with a body gains
  `body` (full text). Nothing is marked read.

**Surfaces (verified 2026-10-07):** `cli.py` `axi_inbox` (l.968–991), `inbox` parser (l.1407–);
`index.ts` `sandesh_inbox` (l.892–); `mcp_server.py` `sandesh_inbox`.

### §S5 — an unknown project id is refused, naming the known ones (CLI + MCP)

- `sandesh_db` gains `require_known_project(con, project_id)`: pure, raises `ValueError` when
  `project_state()` is `None`, returns otherwise. It reuses the existing `unknown project '<id>'` wording
  (as `register`/`send`/`grant` raise it) and accepts archived projects (unlike
  `_require_active_project`, which rejects them).
- The read verbs `addressbook`, `inbox`, `fetch`, `thread` and `status` call it for their `--project` /
  `$SANDESH_PROJECT` id, with the error
  `unknown project '<id>' — known projects: <A, B, …>` (active and archived ids, sorted, from
  `list_projects()`). When exactly one known id matches case-insensitively, the error adds
  `— did you mean '<Id>'?`. Machine mode returns `ok:false` with `help[]` naming `projects`. Human mode
  keeps the house `[sandesh] ERROR: …` exit. `status` checks the project before `validate_address`, so
  the unknown-project error wins.
- An archived project is known (reads stay allowed); a tombstoned one keeps its current behaviour.
- The MCP `sandesh_addressbook` raises `ToolError` with the same message for an unknown `project_id`.
- `setup` (which enrolls) is unaffected.

**Surfaces (verified 2026-10-07):** `cli.py` `_project` (l.33–37), `_ctx` (l.52–58), `axi_addressbook`
(l.949–965), `axi_status`; `sandesh_db.py` `list_projects()` (l.301–312), `project_state()` (l.315–).

### §S6 — contracts, docs and the Pi version gate

- PRD-inbox-search gains a Change Control line: machine-mode hits carry `snippet` by default, with
  `--fields`.
- PRD-axi-toon gains Change Control 1.6 and updates: the P2 row (`search` default
  `id,from,subject,snippet`), the P3 row (the caller is `--as`, else `$SANDESH_ADDRESS`; withheld bodies
  are counted as `withheld` with an `--as` hint), the §4.4 `inbox` row (no bodies unless `--with-body`),
  and new §4.4 rows for `search` (full set `id,from,subject,kind,created,role,snippet`) and `thread`
  (`chain` + `bodies` + `withheld` + `incomplete`).
- `docs/USER_GUIDE.md`, `sandesh/data/usage-scenarios.md` (the `sandesh://usage` resource) and
  `integrations/pi/README.md` (the three tool rows) document re-reading read mail: `thread --as`,
  `inbox --with-body` (pair it with `--limit`: 50 rows × 500 chars by default), the search snippet.
- The Pi `MIN_CLI_VERSION` becomes `[0, 4, 2]` (owner ruling 2026-10-07: the extension now passes flags
  0.4.1 does not know). The version fixtures that use `sandesh 0.4.0` / `sandesh 0.4.1` as a passing
  probe move to `sandesh 0.4.2`: `src/version_gate.test.ts`, `src/version_gate_040.test.ts`,
  `src/uvx_provision.test.ts` (and any other probe fixture the RED run finds).

**Surfaces (verified 2026-10-07):** `integrations/pi/src/index.ts` `MIN_CLI_VERSION` (l.141).

## Acceptance criteria

All CLI criteria run the real `sandesh` CLI in a subprocess against a temp store (`XDG_DATA_HOME` in the
subprocess env), seeded with project `Demo`, `Mainline - Demo`, `Track 1 - Demo`, and a message `#m`
from `Track 1 - Demo` to `Mainline - Demo` with a body containing `gateway timeout`, already fetched (read).

- [ ] **AC1** — `search "gateway" --to "Mainline - Demo" --format json` → each hit has keys exactly
  `id, from, subject, snippet`, and the snippet contains `[gateway]`. `--fields id,snippet` → keys exactly
  `id, snippet`. `--fields bogus` → `ok:false`, exit 2. (Supersedes the old-default assertion in
  `tests/test_axi_disclosure.py` l.509, which RED updates to the new default.)
- [ ] **AC2** — Pi `sandesh_search({recipient, query, fields:["id","snippet"]})` passes
  `--fields id,snippet` to the CLI (fake `exec` argv assertion), and the result decodes with `snippet`.
- [ ] **AC3** — `thread --id <m> --as "Mainline - Demo" --format json` with no `SANDESH_ADDRESS` in the
  env → `bodies` has `<m>` containing `gateway timeout`, and there is no `withheld` key.
- [ ] **AC4** — the same with no `--as` and no `SANDESH_ADDRESS`/`WF_TRACK` → `rc 0`, `bodies` is
  empty, `withheld: 1`, and `help[]` contains `--as`. With `--as "Track 2 - Demo"` (registered, not a
  party) → `withheld: 1`. With `--as "Mainline - Other"` → `ok:false`, error naming the address. With
  no `--as` and `SANDESH_ADDRESS="Mainline - Other"` in the env → `rc 0`, `withheld: 1` (env caller
  treated as absent). A chain of subject-only messages has no `withheld` key.
- [ ] **AC5** — Pi `sandesh_thread({msg_id, requester:"Mainline - Demo"})` passes
  `--as Mainline - Demo` (one argv element) to the CLI.
- [ ] **AC6** — MCP (in-process FastMCP client): `sandesh_thread(msg_id=m, requester="Mainline - Demo")`
  → the chain dict for `m` has `body` containing `gateway timeout`; without `requester` no dict has
  `body`; `requester="Track 2 - Demo"` (not a party) → no `body`, no error; `requester="not an
  address"` → `ToolError`; `requester="Mainline - Other", project_id="Demo"` → `ToolError` naming the
  project mismatch; `requester="Mainline - Other"` with no `project_id` → no error, no `body`.
- [ ] **AC7** — seed a second message `#u` to `Mainline - Demo`, left unread.
  `inbox --to "Mainline - Demo" --all --with-body --format json` → `bodies["<m>"]` contains
  `gateway timeout` and `bodies["<u>"]` is present. Afterwards `inbox --to "Mainline - Demo"` (unread
  only) still lists `#u`: nothing was marked read. A body longer than `BODY_LIMIT` is cut without
  `--full`, with the `--full` template in `help[]`, and whole with `--full`.
- [ ] **AC8** — Pi `sandesh_inbox({recipient, unread_only:false, with_body:true, full:true})` passes
  `--all --with-body --full`. MCP `sandesh_inbox(recipient, unread_only=False, with_body=True)` → the row
  for `m` has `body`; read state unchanged.
- [ ] **AC9** — `addressbook --project demo --format json` → `ok:false`, error contains
  `unknown project 'demo'`, `known projects:` and `did you mean 'Demo'?`, `help[]` names `projects`;
  `--project Nope` → no `did you mean`. Each of `inbox`, `fetch`, `thread`, `status` with
  `--project demo` → `ok:false` with `unknown project` (for `status`, with
  `SANDESH_ADDRESS="Mainline - Demo"`, the unknown-project error, not the address error). An archived
  project still answers `addressbook`. MCP `sandesh_addressbook(project_id="demo")` → `ToolError` with
  the same text. Unit: `sandesh_db.require_known_project` raises for `demo`, returns for `Demo` active
  and archived.
- [ ] **AC10** — docs pins: PRD-inbox-search Change Control mentions `snippet` and `--fields`;
  PRD-axi-toon has a 1.6 Change Control row and its P2 row contains `id,from,subject,snippet`;
  USER_GUIDE and `usage-scenarios.md` contain `thread --id` with `--as` and `--with-body`; the Pi README
  rows for `sandesh_thread` / `sandesh_inbox` / `sandesh_search` name `requester`, `with_body` and
  `fields`.
- [ ] **AC11** — Pi version gate: a `sandesh 0.4.1` probe takes the too-old path and its notice names
  `0.4.2`; a `sandesh 0.4.2` probe arms normally. Full Python gate and full `bun test` green.

**VERIFY audit (not a test):** grep that `axi_search` reads `args.fields`, that `axi_thread` reads
`args.as_`, that the Pi tools push `--fields` / `--as` / `--with-body`, that MCP `sandesh_thread`
and `sandesh_inbox` read `requester` / `with_body`, and that each of the five read verbs and the MCP
`sandesh_addressbook` calls `require_known_project`.

## Gap analysis (2026-10-07) — READY after this update

Baseline measured on `develop` @ `aa3268d`: Python 1786/1786 (83.9% lines), Bun 477/477. Findings folded
in above: PRD-axi-toon P2/P3/§4.4 rows (DRIFT-1); the env-caller rule kept, only explicit `--as` fails
(DRIFT-2); `test_axi_disclosure.py` l.509 superseded (DRIFT-3); `require_known_project` reuses the
existing wording and allows archived (DRIFT-4); `MIN_CLI_VERSION` 0.4.2 with its fixture moves (DRIFT-5,
owner ruling); `requester` for Pi + MCP (DRIFT-6, owner ruling); doc items pinned in AC10 (DRIFT-7).

## Estimated size

Medium — ~120 lines across `cli.py`, `mcp_server.py`, `index.ts`; ~25 tests; doc sections in four files.

## Risk

- `SEARCH_DEFAULT` gains a column: an agent that parsed exactly three columns sees four. Snippets are
  short (8 tokens of context) and were promised by every search description, so this is the documented
  contract arriving, not a break.
- The unknown-project refusal turns a silent empty answer into an error. Callers relying on an empty
  addressbook for a not-yet-set-up project now get an error naming `setup`/`projects`, which is the
  intent.
- Listing known project ids in an error reveals other projects' names on the machine. The store is local
  and `projects` already lists them.

## Non-goals

- A `show --id` verb (owner chose `thread --as`). Marking or unmarking read state. Searching mail the
  caller only SENT. Changing the body-visibility rule (sender or recipient). Write verbs' handling of an
  unknown project (they already refuse).
