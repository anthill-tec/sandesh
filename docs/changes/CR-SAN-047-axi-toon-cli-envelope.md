# CR-SAN-047 — AXI agent surface on the CLI: TOON envelope + the ten AXI principles (`--format toon|json`)

**Status:** PENDING
**Priority:** High (Model B's #3 request; prerequisite of CR-SAN-048)
**Depends on:** —
**Labels:** cli, axi, toon, presentation, python
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** [PRD-axi-toon](../research/PRD-axi-toon.md) §4.0 (AXI principles → rules), §4.1–§4.5
(envelope shape, D1 vendored codec, D2 `--format`, per-verb default/full fields, notify final envelope)

## Context
Model B's skills scrape human output (`addressbook` table, the `notify` banner). PRD §4.1 fixes the
machine envelope (TOON per the official spec, the fleet's AXI shape); §4.4 fixes the fields for the eight
verbs Model B named; §4.5 makes `notify` report its outcome as an envelope on every exit path. PRD §4.0 adopts the ten AXI
principles (axi.md) as the standard for the machine mode: the orchestrator reading these results has a
context budget, so schemas are minimal by default, bodies truncated with size hints, aggregates and empty
states pre-computed, no-op mutations succeed, errors are structured on stdout, and every list/mutation
carries `help[]`. All of that is shaped HERE, once, so the Pi extension (CR-SAN-048) passes it through. The
default `human` output must stay byte-identical to 0.3.6. Core stays stdlib-only (D1: the codec is a
vendored copy of the fleet's `toon.py`). `sandesh_db.py` is untouched; this CR is presentation only
(`cli.py`, `notify.py`, two new modules).

## Scope
- **§S1 — vendored codec.** Add `sandesh/_toon.py` = verbatim copy of `~/.crucible/clients/toon.py`
  (stdlib-only, `encode()`/`decode()`), prefixed with a provenance header (source path, source SHA-256,
  copy date). Add `sandesh/_toon_provenance.py` holding `TOON_SOURCE_SHA256` for the test in AC1.
- **§S2 — envelope builder + serialisers.** New `sandesh/axi.py`: `Envelope(verb, ok, fields: dict,
  context: dict, help: list[str] = [], warnings: list[str] = [])` → `.to_dict()` producing
  `{"axi": {"verb", "ok", …fields, "context", "help"?, "warnings"}}` (`help` omitted when empty;
  `warnings` always present); `render(env, fmt)` → `toon` via `_toon.encode`, `json` via
  `json.dumps(…, ensure_ascii=False)`; `emit(env, fmt, out=sys.stdout)`; `error_envelope(verb, exc,
  context)` → `ok:false` + `error:str(exc)`.
- **§S3 — `--format` plumbing.** In `cli.py`: `--format {human,toon,json}` on the shared `common`
  parent (both positions, `default=argparse.SUPPRESS`, same idiom as `--project`), resolved as
  `args.format` → `$SANDESH_FORMAT` → `human`; invalid env value → CLI error naming the three values.
  `_ctx()` returns the resolved format. In machine modes every existing human printer writes to
  **stderr** (a module-level `_out` stream swapped by `_ctx`), and each verb handler returns an
  `Envelope` that the dispatcher emits to stdout. `ValueError`/`PermissionError` → `error_envelope`
  + the existing exit code.
- **§S4 — per-verb fields (PRD §4.4) with AXI defaults (PRD §4.0).** Lists emit the DEFAULT columns unless
  `--fields <csv>` names more (any subset of the FULL set; an unknown name → exit 2 naming the valid set):
  `addressbook` default `participants[N]{address,listening}` + `listening: n/m`; `inbox` default
  `messages[N]{id,from,subject,unread}` + `unread: n of total` (`--limit`, default 50); `search` default
  `hits[N]{id,from,subject}` + `total`; `thread` default `chain[N]{id,from,subject}` + `incomplete:bool`;
  `projects` → `projects[N]{project,state,cross_project}`. FULL sets per the PRD table. `fetch` →
  `messages[N]{id,from,to,cc,kind,subject,created,re}` + `bodies{<id>: text}` + `marked_read: n`; each body is
  the first 500 chars + `(truncated, N chars total)` unless `--full`, and `help[]` names `fetch … --full` only
  when something was truncated. `send`/`reply` → `id,to,cc,kind,subject,delivered:n` (+`re`) with
  `help[1]: thread --id <id>`. `register`/`unregister` → `address,project,kind,result` where `result` ∈
  `registered|already|unregistered|absent|tombstoned` and `already`/`absent` are `ok:true` (idempotent, P6).
  `archive`/`unarchive` of a project already in that state → `ok:true result:already`. Empty lists state the
  zero as a field (`messages: 0 unread for <addr>`, `participants: 0 registered in <project>`, `hits: 0 for
  "<q>"`) — P5. `help[]` (1–3 command templates with `<id>` placeholders) on lists and mutations only; none on
  `thread`/`fetch` of a single message or on confirmations — P9. Remaining verbs (`setup`, `tombstone`,
  `grant`, `revoke`, `init`, `migrate`, `consolidate`, `reindex`): generic envelope + natural flat fields
  (`setup`→`project,store`; lifecycle→`project,state,evicted[N]`(+`purged` counts); grant/revoke→
  `project,cross_project:bool`; provisioning→`steps[N]{step,result}`). `context.project` on all;
  `context.address` on register/unregister/inbox/fetch/notify/send/reply.
- **§S4b — home view (P8/P10).** `sandesh` with no subcommand in machine mode (and `sandesh status`, new
  read-only verb) prints the dashboard: `bin` (`~`-collapsed path), `description`, `project`, `address`
  (from `$SANDESH_ADDRESS`, else absent), `listening:bool`, `unread: n`, `help[2]`. In human mode the
  no-subcommand path keeps printing usage (unchanged). Usage errors (unknown flag/subcommand, bad
  `--fields`) → exit 2 with `ok:false error help[]` on stdout in machine mode. `tombstone` without `--yes`
  in machine mode → exit 2 (no prompt).
- **§S5 — `notify` final envelope (PRD §4.5).** In `notify.py`, when format is `toon`/`json`: progress
  lines → stderr; a single envelope `{verb:"notify", ok, exit, address, project, unread[N]}` is written to
  stdout on **every** exit (return paths 0/2/3/4/5/1 and the SIGTERM/SIGINT handlers), exactly once
  (guarded flag). `ok` true for 0/2/5, false for 1/3/4 with `error`. `unread` = the triggering ids in
  ascending order on exit 0, empty otherwise.
- **§S6 — docs.** `docs/USER_GUIDE.md` §CLI: a "Machine output (`--format toon|json`)" section with one
  addressbook example and the notify exit-envelope table; `sandesh --help` epilog names the flag +
  env. CLAUDE.md gotcha: `--format` shares the `SUPPRESS` idiom with `--project`.

## Acceptance criteria
- **AC1** — `tests/test_toon_vendor.py`: SHA-256 of `sandesh/_toon.py` **minus its provenance header**
  equals `TOON_SOURCE_SHA256`; `_toon` imports only stdlib modules (AST scan of its imports); `encode`
  and `decode` are importable.
- **AC2** — Round-trip oracle: for every verb in §S4 + §S5, `decode(render(env,"toon")) ==
  env.to_dict()` and `json.loads(render(env,"json")) == env.to_dict()`; one test per verb over a
  fixture store.
- **AC3** — Byte-identical human mode: for each of the 8 named verbs, stdout with no `--format`
  equals stdout with `--format human` equals a golden captured from 0.3.6 for the same fixture store
  (goldens committed under `tests/golden/`).
- **AC4** — Placement: `sandesh --format toon addressbook --project P` and `sandesh addressbook
  --project P --format toon` produce identical stdout; `SANDESH_FORMAT=toon` with no flag likewise;
  `SANDESH_FORMAT=xml` → exit 2 with a message naming `human, toon, json`.
- **AC5** — `addressbook` envelope: `participants[N]{address,kind,status,listening,registered}` with
  `listening` decoding to a Python bool and `status` ∈ {`active`,`inactive`}; a live notifier flips
  `listening` to `true` (uses the existing notifier fixtures).
- **AC6** — `fetch` envelope: `messages[N]` columns exactly `id,from,to,cc,kind,subject,created,re`;
  multi-recipient `to` renders `;`-joined and decodes back verbatim; `bodies` has a key only for
  messages with a body; `marked_read` equals the number of rows marked; `re` is `null` for non-replies.
- **AC7** — Failure envelope: a `send` to an unregistered recipient in `toon` mode prints `ok: false`
  + `error: <the same message the human mode prints>` on stdout, nothing on stdout besides the
  envelope, and exits with the same code as human mode.
- **AC8** — `notify` exit matrix (subprocess tests, `--format toon`): exit 0 → `unread[N]` lists the
  triggering ids and `ok: true`; exit 2 (short `--timeout`) → `unread[0]:`, `ok: true`; exit 5 (dedup) →
  `ok: true`; exit 3 (tombstoned) and 4 (evicted) → `ok: false` + `error`; SIGTERM → exactly one
  envelope with `exit: 143`. All progress text is on stderr; stdout decodes as one envelope.
- **AC9** — In `human` mode `notify` output is byte-identical to 0.3.6 (golden).
- **AC11** — AXI conformance, table-driven over `addressbook, inbox, search, thread, projects, send, reply,
  register, unregister, archive`: default list column count ≤ 4; the named aggregate field present; the
  empty-state sentence present on an empty fixture; `help[]` present on lists/mutations and absent on
  `fetch --id`/`thread` single-message output; `--fields id,from,to,cc,kind,subject,created,re,unread` on
  `inbox` yields all nine columns; `--fields bogus` → exit 2, stdout `ok:false` + `error` naming the valid set.
- **AC12** — Idempotent no-ops: `register` twice → second `ok:true result:already` exit 0; `unregister` of an
  absent address → `ok:true result:absent` exit 0; `archive` twice → `ok:true result:already` exit 0. Human mode
  exit codes for these calls are unchanged from 0.3.6 (golden).
- **AC13** — Truncation: a 2,000-char body → `fetch` shows 500 chars + `(truncated, 2000 chars total)` and
  `help[]` names `--full`; with `--full` the body is complete and `help[]` is absent; a 100-char body is never
  marked truncated.
- **AC14** — Home view: `SANDESH_FORMAT=toon sandesh` (no subcommand) with `$SANDESH_PROJECT`/`$SANDESH_ADDRESS`
  set prints `bin`, `description`, `listening`, `unread`, `help[2]` and exits 0; a live notifier flips
  `listening`; without the env it prints `ok:false` + `error` naming both vars, exit 2. `sandesh --format toon
  --bogus` → exit 2, `ok:false`, `help[]` lists the valid global flags.
- **AC10** — `sandesh_db.py` is untouched (`git diff --stat` on the file is empty at VERIFY); the full
  Python suite is green; no new runtime dependency in `pyproject.toml`.

## Estimated size
Medium-large — one vendored module, one new ~250-line presentation module (envelope, fields/limit/full
handling, truncation, help templates, home view), a sweep of the `cli.py` verb handlers (each returns an
`Envelope`), the `notify.py` exit seam, goldens + oracle + AXI-conformance tests.

## Risks / open questions
- Golden capture must come from a real 0.3.6 install (`uv tool` 0.3.6 is on this machine) — record
  the command in the test file's docstring.
- The `notify` signal path must not double-emit (atexit + handler): the guarded flag is the AC8
  SIGTERM case.

## Non-goals
- No MCP change; no TOON input; no verb semantics change; no `sandesh_db` change.
