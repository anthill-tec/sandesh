# PRD — AXI agent surface (TOON envelope + AXI principles) + Pi wake supervision

**Version:** 1.0
**Date:** 2026-09-26
**Status:** APPROVED (owner delegated design to Mainline - Sandesh, 2026-09-26)
**Owner:** Mainline - Sandesh
**Release:** 0.4.0 · **Wave:** 16
**Requested by:** Mainline - ModelB (Sandesh thread #1394 → #1396, 2026-09-26)

Design contract (WHY + WHAT) for two features Model B asked for so that `@anthill-tec/sandesh-pi`
can replace their own Pi extension outright: a **machine envelope on the CLI** (TOON, the
fleet's AXI shape) and a **supervised wake loop** in the Pi extension with an explicit
start/status/stop surface. CRs derived from this PRD cite it via `**Design reference:**`.

## Change Control

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-09-26 | Mainline - Sandesh | Initial contract from the Model B thread; six design decisions D1–D6 fixed by the owner's delegation. |
| 1.1 | 2026-09-26 | Mainline - Sandesh | Owner ruling: the extension is the orchestrator's interface — adopt the ten AXI principles (axi.md) as the standard for the whole agent surface, shaped once in the CLI (§4.0); minimal schemas, truncation, aggregates, empty states, idempotent no-ops, structured errors as results, ambient context, home view, `help[]`. |
| 1.2 | 2026-09-29 | Mainline - Sandesh | Owner ruling: the identity environment is loaded at the shell boundary (direnv, owned by Model B's `modelb-axi init`); the extension never parses `.env`. P7 gains the unexported-identity nudge (§4.6). |

---

## 1. Problem

Model B (Pi-only, no MCP) drives every Mainline↔Track exchange through Sandesh. Today:

1. Their skills **scrape human text** — the `addressbook` table for `listening`, the
   `[notify] … ✉ N unread 'to' message(s): [ids]` banner for wake ids. Neither is a contract; any
   wording change breaks them silently.
2. **Two wake loops exist** — their `sandesh-watcher` extension and our 0.3.x `session_start`
   auto-arm. They must not both run. Their supervision contract (ids in the wake, de-dup,
   timeout cap, terminal codes, explicit start/status/stop, one watcher per address) is stricter
   than ours.
3. Our 12 verb tools return the CLI's human text to the agent — structured for a human, not for
   a skill that needs a field.

Model B's own words fix the target (#1394 Q2/Q3, #1396 3a/3b) and are the wire contract below.

## 2. Hard constraints

- **Core stays stdlib-only.** No runtime dependency for the TOON encoder (PyPI `toon-format`
  is a stub). `sandesh_db.py` stays pure (no printing); presentation lives in `cli.py`.
- **Exit codes are unchanged** everywhere; the envelope never replaces them (`notify`
  0/2/3/4/5/1 + `128+signal`).
- **Default output is byte-identical to 0.3.x.** Machine mode is opt-in.
- **The wake stays out-of-band.** MCP cannot wake; the Pi extension's loop wakes via
  `pi.sendUserMessage(…, {deliverAs: "followUp"})` (PE11; Model B confirmed the same).
- **AXI is the standard** (https://axi.md — the ten principles; the fleet-wide rule Crucible's clients already
  follow). Every agent-facing output of the CLI in machine mode and every tool result of the Pi extension is
  measured against it: the calling orchestrator's context budget is a first-class constraint.
- **Exactly one watcher per address**; a second `notify` exits 5 and is treated as
  "already running", never surfaced as failure, never relaunched.

## 3. Goals

- **G1** A stable machine envelope for every CLI verb, byte-compatible with the fleet's TOON
  decoders (`@toon-format/toon`; Crucible's `toon.py`).
- **G2** `notify` reports its outcome as an envelope, so no consumer parses the banner.
- **G3** The Pi extension is the complete Model B surface: verb tools return the envelope;
  the wake loop implements Model B's supervision contract; arming is explicit.
- **G4** Model B retires its watcher and points its skills at our tools with no other change.
- **G5** The orchestrator spends the fewest tokens per Sandesh interaction that still lets it act: one call answers
  "is anyone listening / is there mail / what is it" — no follow-up calls, no exception traces, no scraping.

## 4. Design

### 4.0 AXI principles → design rules (shaped ONCE in the CLI; the extension passes through)

| # | Principle | Sandesh rule |
|---|---|---|
| 1 | Token-efficient output | TOON at the output boundary (CLI machine mode); internal logic on dicts. The extension never re-renders. |
| 2 | Minimal default schemas | Lists default to 3–4 columns: `inbox` → `id,from,subject,unread`; `addressbook` → `address,listening`; `search` → `id,from,subject`; `thread` → `id,from,subject`. `--fields a,b,c` (CLI) / `fields` (tools) widens to the full column set of §4.4. Default limits cover the common case in one call (`inbox` 50, `search` 20). |
| 3 | Content truncation | `fetch`/`thread` bodies: first 500 chars + `(truncated, N chars total)`; `--full` / `full:true` returns everything. Never omit a body; always state its size. |
| 4 | Pre-computed aggregates | `inbox` → `unread: <n> of <total>`; `addressbook` → `listening: <n>/<m>`; `send`/`reply` → `delivered: <n>`; `fetch` → `marked_read`; `search` → `total`. The home view (§4.6) answers listening + unread in one call. |
| 5 | Definitive empty states | An empty result is a sentence field, never a bare `key: []` alone: `messages: 0 unread for <addr>`, `participants: 0 registered in <project>`, `hits: 0 for "<q>"`. `ok: true` — absence is the answer. |
| 6 | Structured errors, idempotent no-ops, exit codes | `register` of an active address → `ok:true result:already`; `unregister` of an absent one → `ok:true result:absent`; `archive` of an archived project → `ok:true result:already`; `notify_start` on a running loop → `ok:true already:true`. Errors go on **stdout** as `ok:false error help[]` (exit 1 unchanged; usage errors exit 2 and name the valid flags). Unknown flags fail loud. No prompts in machine mode (`tombstone` requires `--yes`). Progress only on stderr. |
| 7 | Ambient context | The Pi extension injects the home envelope at `session_start` **minus the `bin` and `description` lines** (self-identification is noise inside a session that already holds the tools) — ≤ 12 lines: verb/ok, project, address, listening, unread, context, `help[2]`, warnings. Opt-in by the identity env being set; nothing when unset (the §4.6 unexported-identity nudge is a warning, not this block). (Amended 2026-09-27 at CR-SAN-048 VERIFY: the original "≤ 6 lines" predates the P8/P10 home-view fields.) |
| 8 | Content first | `sandesh` with no arguments (machine mode) and the `sandesh_status` tool print the same dashboard: `bin`, one-line description, address/listening/unread, `help[]`. |
| 9 | Contextual disclosure | List and mutation results carry 1–3 `help[]` templates with `<id>`/`"<subject>"` placeholders (after `inbox` → fetch; after `send` → `thread <id>`; after an empty addressbook → register). Detail views and confirmations carry none. Truncated lists say how to see all. |
| 10 | Consistent help | Every verb's `--help` is the concise per-verb reference (flags + defaults + 2 examples); tool descriptions are that reference. `--version`/`-v`/`-V` print the bare version fast (already true). |

### 4.1 Envelope shape (wire contract — Model B #1396 3a, verbatim semantics)

```text
axi:
  verb: <the CLI verb>
  ok: true|false
  <verb-specific result fields, flat>
  context:
    project: <id>          # always
    address: <addr>        # where the verb acts for one
  help[N]: <next-step hints>          # optional
  warnings[N]: <strings>              # ALWAYS present; `warnings: []` when clean
```

- One envelope per invocation on **stdout**; human text on **stderr**; exit code unchanged.
- Failure: `ok: false` plus flat `error: "<message>"` (the same text the human mode prints).
- Wire rules = the official TOON spec (toonformat.dev): nested objects `key:` + 2-space
  children; primitive arrays inline `key[N]: a,b,c`; uniform-object arrays tabular
  `key[N]{col,…}:` + one comma-joined row each; empty array `key: []` (spec v4.1 §9.1 — the legacy `key[0]:` header MUST NOT be emitted; decoders accept both, which is why Model B's #1396 wording `warnings[0]:` still parses); bare strings only
  when safe (non-empty, not `true/false/null`, not numeric-looking, no leading/trailing
  space, none of `: " \ [ ] { } ,`, no control chars, not starting with `-` or `#`),
  otherwise JSON-quoted.
- **Conformance oracle:** every emitted envelope must `decode()` via the vendored codec
  (§4.2) and via `@toon-format/toon` (bun dev dependency in `integrations/pi`) to the
  same dict the CLI built. Tests assert this round-trip, not string equality.

### 4.2 Encoder — D1: vendor the fleet codec

`sandesh/_toon.py` = a verbatim copy of Crucible's `clients/toon.py` (stdlib-only: `math`,
`re`, `typing`; full official grammar; already cross-validated against the reference), with a
provenance header (source path, source commit, date) and a `SANDESH_TOON_SOURCE_SHA` constant a
test compares against the copied file's hash. Sandesh only calls `encode()`; `decode()` is kept
for the round-trip tests. No hand-written encoder (D1 rejects the "minimal subset" option —
the quoting rules are where the subset would drift).

### 4.3 CLI selection — D2

- Global option `--format {human,toon,json}` accepted **before or after** the subcommand
  (same `parents=[common]` + `default=argparse.SUPPRESS` idiom as `--project`; see the
  CLAUDE.md gotcha). Env fallback `$SANDESH_FORMAT`. Default `human`.
- `json` is the identical envelope dict serialised with `json.dumps` — no separate field
  contract. Provided because it is free once the envelope is a dict.
- `sandesh/axi.py` (new, presentation layer): `Envelope` builder (`verb`, `ok`, fields,
  `context`, `help`, `warnings`), the three serialisers, and `emit(envelope, fmt)` — the single
  seam `cli.py` calls. In `human` mode `emit` is a no-op and the existing printers run
  unchanged; in machine modes the printers are redirected to **stderr** and the envelope is
  the only stdout.
- `ValueError`/`PermissionError` from the library map to `ok:false` + `error` with the
  existing exit code (1). `sandesh_db` is untouched.

### 4.4 Per-verb result fields (Model B #1396 3b = the FULL column set; §4.0 P2 fixes the DEFAULT subset)

| Verb | Result fields |
|---|---|
| `addressbook` | default `participants[N]{address,listening}` + `listening: n/m`; full (`--fields`) `address,kind,status,listening,registered` — `status` = `active`\|`inactive`; `listening` boolean; `registered` ISO timestamp |
| `inbox` | default `messages[N]{id,from,subject,unread}` + `unread: n of total`; full `id,from,to,cc,kind,subject,created,re,unread` — `to`/`cc` joined with `;` inside the cell (quoted when needed); `re` = parent id or `null`; no bodies |
| `fetch` | `messages[N]{id,from,to,cc,kind,subject,created,re}` + `bodies` (object keyed by id → body text, truncated per P3 unless `--full`; absent key = subject-only) + `marked_read: <n>` |
| `send`, `reply` | `id`, `to`, `cc`, `kind`, `subject`, `delivered: <n>`; `reply` adds `re`; `help[]`: `thread --id <id>` |
| `register`, `unregister` | `address`, `project`, `kind`, `result: registered\|already\|unregistered\|absent\|tombstoned` (P6 no-ops are `ok:true`) |
| `notify` (final envelope) | `exit: <code>`, `address`, `project`, `unread[N]: <ids>` (`unread: []` unless exit 0) |
| every other verb (`setup`, `thread`, `search`, `projects`, `archive`, `unarchive`, `init`, `migrate`, …) | the generic envelope (`verb`, `ok`, `context`, `warnings`) plus that verb's natural result as flat fields; exact fields are fixed in the implementing CR's spec, never invented at emit time |

`context.project` on all; `context.address` on the recipient/sender-keyed verbs (`register`,
`unregister`, `inbox`, `fetch`, `notify`, `send`, `reply`).

### 4.5 `notify` in machine mode — G2

- Progress lines (`no 'to' mail — next check in 10s`, `DB busy … staying up`) go to **stderr**.
- On **every** exit path (0 mail, 2 timeout, 3 tombstoned, 4 evicted, 5 dedup, 1 error,
  signal) exactly one final envelope is written to stdout before the process ends; the
  `atexit`/signal handlers already present in `notify.py` are the seam. `ok` is `true` for
  0/2/5 (normal outcomes), `false` for 1/3/4 with `error` naming the reason.
- `unread[N]` lists the message ids that triggered the wake, in id order — this is the
  de-duplication key the extension uses (§4.7).

### 4.6 Pi extension — the AXI surface the orchestrator reads (D5)

- **Pass-through.** Every verb tool shells `sandesh --format toon …` and returns the CLI's stdout — the AXI
  envelope — as the tool result text, byte-for-byte. Tool parameters mirror the AXI flags: `fields`, `full`,
  `limit`. The extension re-renders nothing (P1) and adds no prose.
- **Errors are results, not exceptions (P6).** A non-zero exit whose stdout is an `ok:false` envelope is returned
  as the tool result (the agent reads `error` + `help[]`); only a missing/undecodable envelope (CLI absent,
  crash) throws, with verb + code + stderr as today. Exit 5 on `notify` and every `result: already|absent`
  are `ok:true`.
- **Ambient context (P7).** On `session_start`, when `$SANDESH_ADDRESS` + `$SANDESH_PROJECT` are set, the
  extension runs the home view once and injects it as compact context (`pi.sendUserMessage` is NOT used —
  the harness's system-context seam is; ≤6 lines: address, listening, `unread: n`, `help[2]`). Nothing is
  injected when the vars are unset. This replaces the 0.3.x "wake disabled" warning.
- **Unexported-identity nudge (P7, v1.2).** Every tool reads its identity from the process environment
  only; loading a project's `.env` into the environment is the shell's job (direnv, emitted by
  `modelb-axi init`), never the extension's. When `$SANDESH_ADDRESS` or `$SANDESH_PROJECT` is unset at
  `session_start` but `<cwd>/.env` assigns it, the extension emits ONE warning naming the unexported
  key(s) and how to load them, and does nothing else: it does not read the values, does not export
  them, does not walk up from `<cwd>`. Silent when both vars are set, when `.env` is absent/unreadable,
  or when it assigns neither key. Turns a silent "no ambient view, wake not armed" into a one-line cause.
- **Home view (P8).** Tool `sandesh_status()` (no args) = the same dashboard on demand: `bin`, description,
  `address`, `listening`, `unread`, `watcher: running|stopped`, `help[]`.
- **Decoder.** The extension decodes only where it needs a field (the wake loop's `unread`/`exit`,
  `sandesh_unregister`'s `result`, the home view's counts). `@toon-format/toon` (reference implementation) as
  the extension's first runtime dependency, **conditional on** an AC proving `pi install npm:` installs it;
  otherwise a ≤60-line in-repo parser for the flat-scalar + inline-array subset, and pass-through is unchanged.

### 4.7 Wake supervision — D4 (Model B #1394 Q2, adopted verbatim)

State machine per address, owned by the extension:

| Event | Action |
|---|---|
| `start(address, project)` | refuse if a loop for that address exists (report it); else spawn `sandesh --project P --format toon notify --to A`; record start time |
| exit **0** (`unread[N]`) | if the id set equals the previous wake's set → **no wake**, relaunch after **30 s**; else `sendUserMessage("Unread Sandesh mail for <A>: ids …", {deliverAs:"followUp"})`, remember the set, relaunch **immediately** |
| exit **2** (timeout) | silent relaunch; if this is the **third** exit-2 within 60 s → surface (`ctx.ui.notify` warning) and keep relaunching |
| exit **5** (dedup) | "already running" — stop this loop silently, do not surface, do not relaunch |
| exit **1 / 3 / 4** or a signal | stop the loop; surface the code + reason (from the envelope's `error`) |
| `stop(address?)` | abort the child (SIGTERM, then SIGKILL after a grace), clear state; no address = all |
| `status()` | per address: running, pid, started, last exit, last wake ids, exit-2 count |

- **Tools:** `sandesh_notify_start(address?, project?)`, `sandesh_notify_status()`,
  `sandesh_notify_stop(address?)`; `address`/`project` default to `$SANDESH_ADDRESS` /
  `$SANDESH_PROJECT`. Results are AXI envelopes with the P2 minimal default `watchers[N]{address,running,lastExit}`
  (`context.project` always present); a `fields` knob widening to the full status set is deferred. `start` on a running address → `ok:true already:true` (P6); `stop` on nothing → `ok:true stopped: 0`.
  A `/sandesh-watcher status|stop` slash command mirrors status/stop.
- **Arming (D4):** `session_start` **no longer arms by default**. Setting
  `SANDESH_AUTOSTART=1` (with both identity vars) restores the 0.3.x auto-arm for users who
  want it — documented in `USER_GUIDE.md` §Pi as the one behaviour change of 0.4.0.
  `session_shutdown` still stops every loop.
- `__resetWakeState`/`__setWakeSleepFn` test seams are kept and extended (injectable clock
  for the 30 s / 60 s rules).

### 4.8 Release — D6

Ships as **0.4.0** (additive CLI flag + tools; the arming default is the single behaviour
change). Publishing: PyPI via the existing chain; npm via `publish-npm.yml`. Once the owner
configures npm Trusted Publishing for `anthill-tec/sandesh` · `publish-npm.yml`, the workflow's
`NODE_AUTH_TOKEN` bootstrap and the `NPM_TOKEN` secret are retired (OIDC-only, like PyPI).
The `workflow_dispatch` `npm publish --dry-run` job is the mandatory rehearsal before
`finish`, alongside the TestPyPI checkpoint.

## 5. Non-goals

- No change to the MCP server (Claude surface); `sandesh_*` MCP tools stay JSON-native.
- No AXI shaping in the extension beyond what only a harness can do (P7 ambient, P8 home tool, P6 errors-as-results); all field/schema/truncation/aggregate rules live in the CLI so direct callers get the same surface.
- No TOON on **input** (no `--format` for reading stdin); no negotiation via env other than
  `$SANDESH_FORMAT`.
- No semantic change to any verb; no new verbs on the CLI.
- No wake for `cc` (locked semantics #1); no change to `notify` polling or liveness.
- Model B's skill rewrite is theirs (they said so on the thread); we ship tools + envelope.

## 6. Open questions

None. Model B's questions (#1394 Q1–Q4) are answered on the thread (#1395/#1397/#1398); D1–D6
were delegated to the owner's Mainline on 2026-09-26.

## 7. Verification themes

- **AXI conformance per verb** — default column count ≤4, aggregates present, empty-state sentence present, `help[]` present on lists/mutations and absent on details, no-op mutations `ok:true`, errors on stdout with `help[]`; one table-driven test over the verbs.
- **Round-trip oracle** for every verb's envelope (python: vendored `decode()`; bun:
  `@toon-format/toon`) — identical dict both ways.
- **Byte-identical human mode** — golden comparison against 0.3.6 output for every verb.
- **Notify exit matrix** — one envelope per exit code incl. SIGTERM, ids present only on 0.
- **Supervision matrix** — same-ids/no-wake + 30 s; third exit-2 in 60 s surfaces; 5 silent;
  1/3/4/signal stop + surface; one loop per address; start/stop/status idempotence.
- **Install proof** — `pi install npm:@anthill-tec/sandesh-pi` resolves the runtime dependency
  (or the fallback parser is in play), the wake fires end-to-end via a real `sandesh notify`
  (smoke test, as CR-SAN-019 does for verbs).
