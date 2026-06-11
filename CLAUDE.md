# CLAUDE.md — Sandesh Project Context

Read this before changing anything. It captures the architecture, the **locked
design decisions** (don't re-litigate them without reason), the gotchas, and the
Wave-2 roadmap. The code is small and stdlib-only — pair this doc with the four
`.py` files and you have the whole picture.

---

## What Sandesh Is

**Sandesh** (संदेश — Sanskrit/Hindi for *message / dispatch*) is a tiny, **standalone,
multi-project messaging system for cooperating agent/orchestrator sessions**. It is a
SQLite-backed maildir + a blocking mailbox watcher. **Pure Python stdlib — no
third-party dependencies.**

### Origin & use case (why it exists)
It was extracted from a "Model-B" parallel-orchestration workflow: a **Mainline**
coordinator session plus worker **Track** sessions running in parallel. Those sessions
**cannot message each other directly**, and — critically — **re-invoking a *sleeping*
agent turn is only possible via the host's background-task mechanism** (e.g. Claude
Code's `run_in_background` tool, or Cron). A hook / MCP server / plain daemon *cannot*
push a turn into an idle session. Sandesh is the relay that works within that limit:

- a durable, queryable **mailbox** each session reads on demand, and
- a blocking **`notify` watcher** a session launches *in the background* so that when
  mail addressed to it arrives, the watcher exits → the host wakes the agent → it
  fetches.

Addresses **represent orchestrators** (`'Mainline - <Project>'`, `'Track <N> - <Project>'`),
but nothing is Claude-specific anymore — it's a general agent-messaging primitive.

---

## Project Classification: Standalone Python CLI tool (+ planned MCP server)

- Pure stdlib (`sqlite3`, `argparse`, `os`, `signal`, `uuid`, `socket`). No venv needed.
- **Source of truth = this repo.** It is *installed* (copied) to the XDG data dir; edits
  here require a re-`./install.sh` to take effect on the installed binary.
- Provenance note: an earlier, pre-standalone copy lived in the user's Claude dotfiles
  at `~/.claude/scripts/sandesh/` (it imported a Claude-specific `schedule_db`). That
  copy is **superseded** by this repo and should be removed from the dotfiles to avoid
  divergence.

---

## Status & Roadmap

- **Waves 1–5 — DONE** (through **v0.1.0**): the standalone CLI + `notify` watcher
  (Wave 1), the MCP server `sandesh-mcp` (Wave 2 — the verbs as MCP tools; the watcher
  stays the wake path), packaging/PyPI workflow, and the schema-migration subsystem +
  installer auto-migrate (Wave 5, CR-SAN-017/018).
- **Wave 6 — IN PROGRESS: the global store.** **CR-SAN-022 done (pending merge)** — one
  global `sandesh.db` (WAL) for all projects, the `project` tracker table +
  `address.project`, global-target `migrate`, explicit project scoping, legacy-store
  `consolidate`. Next: CR-SAN-023 (cross-project grant), CR-SAN-024 (lifecycle verbs),
  CR-SAN-025 (MCP surface). Design contract: `docs/research/PRD-global-store.md`
  (AGREED 2026-06-11).
- **Not yet adopted** by the originating orchestration workflow — that's a separate,
  deliberate step (seed an addressbook, sessions run `notify`, migrate off the old
  file-note relay).

---

## File Layout

```
sandesh/                         (this repo — source of truth)
├── app/
│   ├── sandesh_db.py   the library: schema + all operations (no CLI, no I/O loop)
│   ├── cli.py          argparse CLI over the library (one binary, all subcommands)
│   └── notify.py       the blocking mailbox watcher (run() + a thin main)
├── bin/sandesh         bash launcher → resolves its real path → runs app/cli.py
├── install.sh          copies app/ + bin/ to the XDG data dir, symlinks the launcher
├── tests/test_sandesh.py   24 unit tests (run against a temp store; no install needed)
├── README.md
└── CLAUDE.md           (this file)

Installed (by install.sh) + runtime data:
~/.local/share/sandesh/          ($XDG_DATA_HOME/sandesh)
├── app/  bin/sandesh            the installed code + launcher
├── sandesh.db                   the ONE global DB (WAL) — all projects; five tables:
│                                address, message, message_recipient, notifier, project
└── projects/<project_id>/
    ├── messages/msg-<id>.md     message bodies (full absolute paths stored in the DB)
    └── sandesh.db.pre-global    legacy per-project DB, kept as backup after consolidation
~/.local/bin/sandesh             symlink → ~/.local/share/sandesh/bin/sandesh (PATH entry)
```

---

## Architecture

### The store — XDG, one global DB, projectid-scoped
Every operation carries a **`project_id`**, but all projects share **ONE global
database** at `<data_home>/sandesh/sandesh.db` (WAL mode; `data_home` =
`$XDG_DATA_HOME` or `~/.local/share`). The `project` tracker table enrolls each
project; per-project body files live under
`<data_home>/sandesh/projects/<project_id>/messages/`. There is **no git/CWD
inference** — projectid is explicit (an MCP daemon has no CWD). The CLI accepts
`--project` (before *or* after the subcommand) or `$SANDESH_PROJECT`.
`sandesh_db.db_path()`/`connect()` open the global DB;
`sandesh_db.store_dir(project_id)` builds the body-folder path;
`sandesh_db.setup(project_id)` enrolls + provisions (idempotent; refuses a
tombstoned id).

### Five tables (`sandesh_db._SCHEMA`)
| table | holds |
|---|---|
| `address` | the **addressbook** — durable identities, PK `address` (rejects dupes), `active` soft-delete, `project` (the address's `<Project>` part — the exact-match scoping key) |
| `message` | the **envelope** — `subject` (NOT NULL), `kind`, `in_reply_to`, `body_path` (NULL = subject-only; else a FULL absolute path) |
| `message_recipient` | **per-message addressees** — (`message_id`, `recipient`, `role` to/cc, `read_at`); PK `(message_id, recipient)` |
| `notifier` | **per-session watcher liveness** — PK `recipient`, `pid`, `token` (uuid/launch), `heartbeat_at`, `tombstone` |
| `project` | the **project tracker** — PK `project_id`, `state` (CHECK `active`\|`archived`\|`tombstoned`), `created_at`/`archived_at`/`tombstoned_at` |

### Modules
- **`sandesh_db.py`** — the entire model + operations. Stateless functions taking a
  `con` (sqlite connection) and, where bodies are involved, a `store` dir. No printing,
  no loops. This is what the CLI and (future) MCP server both call.
- **`cli.py`** — argparse front end. `_ctx(args)` → `(project, store, con)`. Address
  defaults: `--from`/`--to` → `$SANDESH_ADDRESS` → `$WF_TRACK`.
- **`notify.py`** — `run(project_id, address, timeout)` is the blocking poll loop;
  `main()` is the `sandesh notify` entry. Interval from `$SANDESH_POLL_SECONDS`
  (default 10, floor 3).

---

## Locked semantics (the design — change only with reason)

1. **To wakes / Cc silent.** Both `to` and `cc` get `message_recipient` rows and are
   read by `fetch`; the difference is the **wake**: `notify` polls
   `unread_to()` = `role='to' AND read_at IS NULL`. Cc is delivered + readable but
   never wakes — it's swept up on the recipient's next `fetch`. Conserves agent turns.
2. **`all-tracks` broadcast — sender-project-scoped.** A reserved recipient keyword;
   `send` expands it to all **active** addresses **in the sender's project**, minus the
   sender. Per-recipient rows mean every recipient's watcher fires on its own row (the
   sender gets none). *Re-opened by design (CR-SAN-022):* the original wording relied on
   per-project stores for isolation; `docs/research/PRD-global-store.md` (AGREED
   2026-06-11) replaced those with the single global DB, so the scoping is now explicit
   via `address.project` — and cross-project messaging is EXPLICITLY BLOCKED until
   CR-SAN-023's grant: `send` to a foreign project fails with
   `cross-project sending is not enabled (CR-SAN-023)`.
3. **Per-recipient read.** `read_at` lives on `message_recipient`, not `message` — a
   broadcast/cc stays unread for the others after one reads it.
4. **Subject-only ⟷ file-body.** `subject` is mandatory (the minimal content). No
   `--body`/`--body-file` ⇒ `body_path` NULL, **no file written**. With a body, it's an
   md file under `messages/`, and the DB stores its **full absolute path**.
5. **Keep history; read=seen is the only signal.** Nothing is deleted. `read_at`
   (per recipient, on `message_recipient`) is the sole "seen" signal — there is no
   `message.status` disposition machine (no open/actioned/closed). A request stays
   visible in history; whether it has been *acted on* is conveyed by replies, not a
   status column. (CR-SAN-017 0002 dropped `message.status`; new≡migrated stores have
   no status column.)
6. **Reply threading.** `message.in_reply_to` links a reply to its parent; `reply`
   defaults `to`=parent's sender and subject=`Re: …` (no `Re: Re:`). `thread` walks the
   chain. `fetch` shows `↳ re #N "<parent subject>"`. (`reply` has no `--resolves`
   flag — see #5.)
7. **Crash-safe liveness.** `notifier_live()` treats a row as dead if its `pid` is gone
   OR `heartbeat_at` is older than `HEARTBEAT_STALE_SECS` (60). A clean exit removes the
   row (token-guarded); a SIGKILL leaves a stale row the next `notifier_acquire()` reaps.
   This is the self-heal that compensates for unreliable shutdown hooks.
8. **Cooperative tombstone eviction.** You can't cross-session-kill another's watcher.
   `notifier_tombstone(recipient)` sets a flag the watcher sees on its next poll →
   it self-terminates (exit 3). `unregister` of a *live* address tombstones first,
   returns `('tombstoned', pid)`; the caller retries once it's offline, then soft-deletes.
9. **Removal authorization.** `Mainline` may unregister anyone; any address may
   unregister itself. (Honor-system; all-local cooperative orchestrators.)
10. **Validated address format** — `'<Orchestrator> - <Project>'`, regex
    `^(Mainline|Track \d+) - [A-Za-z][A-Za-z0-9_]*$`, and the `<Project>` part must equal
    the `project_id`. Caught at `register` and `send`. (Extend the orchestrator
    alternation in `ADDRESS_RE` if new roles appear.)

---

## The wake mechanism (the most important constraint)

Re-invoking a **sleeping** agent turn is **exclusive to the host's background-task
mechanism** (Claude Code's `run_in_background` tool; or Cron/scheduled wakeups). Verified
against the Claude Code hooks docs:
- `SessionStart`/`SessionEnd` hooks exist, but a hook-spawned process **cannot wake the
  agent**, and `SessionEnd` is unreliable on crash/SIGKILL.
- An MCP server **cannot** push a turn into an idle session either.

**Therefore:** the `notify` watcher must be launched by the agent via its background-task
tool (which is what re-invokes it on exit). MCP (Wave 2) replaces the *verbs* (send/
fetch/…), **not** the wake. Keep `notify` as a standalone process. This is *why* the
liveness table is crash-safe rather than relying on a shutdown hook.

---

## How to run

```bash
# tests (no install needed — runs against a temp store)
python3 tests/test_sandesh.py            # or: python3 -m unittest -v (from repo root)

# install / re-install after edits (copies app/+bin/ to ~/.local/share/sandesh/)
./install.sh

# use (installed launcher; ~/.local/bin must be on PATH, else call by full path)
sandesh setup --project Demo
sandesh --project Demo register --address "Mainline - Demo" --kind mainline
sandesh --project Demo send --from "Track 1 - Demo" --to "Mainline - Demo" --subject "ping"
sandesh --project Demo notify --to "Mainline - Demo"     # blocks; run in background
sandesh --project Demo fetch  --to "Mainline - Demo"
```
Env: `$SANDESH_PROJECT`, `$SANDESH_ADDRESS` (caller's own address),
`$SANDESH_POLL_SECONDS`. `notify` exit codes: `0` mail / `2` timeout / `3` tombstoned /
`4` evicted / `5` dedup / `1` error.

### Model-B usage pattern (how an orchestrator uses it)
Each session, at start: ensure its address is registered, then launch
`sandesh --project <P> notify --to "<self>"` **via the host's run_in_background tool**.
On wake (exit 0) → `sandesh fetch --to "<self>"` → act → relaunch `notify`. Send with
`send`/`reply`. On exit-3 (tombstoned) do **not** relaunch.

---

## Gotchas (learned while building — don't rediscover them)

- **argparse `--project` in both positions:** the shared `--project` (on the parent and
  every subparser via `parents=[common]`) uses `default=argparse.SUPPRESS` so an absent
  one doesn't clobber the value given in the other position. Removing SUPPRESS breaks
  `sandesh --project X <cmd>`.
- **SQLite has no real BOOLEAN.** `active`/`tombstone` are declared `BOOLEAN` (readable)
  but stored as integers `0`/`1`; `sqlite3` returns ints, so compare truthily
  (`if row["tombstone"]:`), never `is True`. (`TRUE`/`FALSE` literals work since 3.23.)
- **Body paths are absolute.** `send` stores the full path; `fetch` opens it directly
  (with a legacy relative-path fallback under `store`).
- **`store` vs `con`.** Functions that touch body files take a `store` dir; pure-DB
  functions take only `con`. Keep that split.
- **`sqlite_sequence`** appears once any AUTOINCREMENT table exists — it's sqlite-internal,
  harmless.
- **The launcher** resolves its own real path via `readlink -f`, so it works through the
  `~/.local/bin/sandesh` symlink. Don't hardcode the install path.
- **Schema changes ship via the migration subsystem.** Don't hand-evolve a store's schema —
  add a migration. The `sandesh migrate` CLI command (gated behind the optional **`[migrate]`**
  extra — `yoyo` + `jsonschema`) applies pending migrations; `--status`/`--rollback`/`--check`
  inspect and reverse them. On update the installer **auto-migrates** the global DB
  (`install.sh` runs `sandesh migrate --all`), and the committed
  `sandesh/schema/current-schema.json` snapshot must stay in sync with `migrations/` — the CI
  gate in `publish-pypi.yml` asserts `migrate --dump-schema` equals that snapshot.
- **`migrate` no longer accepts `--project`** (CR-SAN-022): the global DB is the single
  migration target, so the subcommand has no per-project routing — `sandesh migrate
  --project X` is a CLI error.
- **`setup` of a tombstoned project refuses** (PRD O1): the tracker row is terminal; the
  raised error message contains `retired (tombstoned)` and the row is left unchanged.
- **Legacy per-project stores are auto-consolidated by the installer.** `install.sh` runs
  `sandesh consolidate` (stdlib-only, idempotent) after the migrate block: it imports each
  `projects/<id>/sandesh.db` into the global DB (ids remapped, reply chains relinked,
  body files unmoved), enrolls the project, and keeps the legacy file as
  `sandesh.db.pre-global`.

---

## Wave 2 — MCP server (plan)

1. **Verify the MCP Python SDK first** (read its actual API; it's the one real new
   dependency — decide stdio vs HTTP transport).
2. `app/mcp_server.py` exposing tools: `sandesh_setup`, `sandesh_register`,
   `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`, `sandesh_reply`,
   `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread` — **each takes
   `project_id`**. They call `sandesh_db.*` directly (the server is a thin adapter).
3. **Do NOT** put the wake in MCP. `notify` stays a background process.
4. Add the MCP dep to `install.sh` (or document a venv); keep the CLI working unchanged.
5. Tests for the adapter layer.

---

## Conventions

- **Git:** the user works git-flow style (feature branches off `develop`). This repo is
  fresh (initial commit on the default branch) — establish `develop` + a remote if asked.
  **Commit/push only when asked; branch before committing on the default branch.**
- **Commit messages: NEVER add Claude attribution** ("Generated with Claude",
  "Co-Authored-By: Claude"). Clean, technical messages only.
- **New dependency?** This project's whole virtue is stdlib-only — adding a dep (only the
  MCP SDK is anticipated) is a deliberate decision; read the real upstream API first.
- Keep `sandesh_db.py` pure (model + ops, no printing/looping); presentation in `cli.py`,
  the loop in `notify.py`, the future protocol in `mcp_server.py`.
```
