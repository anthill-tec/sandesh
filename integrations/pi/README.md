# @anthill-tec/sandesh-pi

A [Pi](https://pi.dev) extension that exposes [Sandesh](https://github.com/anthill-tec/sandesh)
— a tiny, standalone, multi-project messaging system for cooperating agent/orchestrator
sessions — as native Pi tools, plus a supervised wake watcher that re-enters the Pi session
when mail arrives.

The extension is a **thin shim**: each tool shells out to the `sandesh` CLI via `pi.exec`.
No messaging logic lives here — Sandesh-core stays Python.

## Install

```bash
# from the npm registry (gallery package)
pi install npm:@anthill-tec/sandesh-pi

# local development (from a checkout of this repo)
pi install ./integrations/pi
```

## Prerequisite — the `sandesh` CLI (≥ 0.4.0)

The extension calls the `sandesh` binary — **CLI ≥ 0.4.0 is required** (the tools speak the
`--format toon` AXI envelope; an older CLI is refused at session start with a too-old
notice). It is resolved from `PATH`, else run on demand via
`uvx --from sandesh-relay[migrate] sandesh`. Install it via any of:

```bash
uv tool install sandesh-relay     # uv
pipx install sandesh-relay        # pipx
./install.sh                      # from a repo checkout
# or the AUR package on Arch
```

Verify: `sandesh --version`.

## Environment

| Variable             | Purpose                                                                                   |
| -------------------- | ----------------------------------------------------------------------------------------- |
| `$SANDESH_PROJECT`   | the project the tools route to (the default `project_id`).                                |
| `$SANDESH_ADDRESS`   | this session's own Sandesh address (the default watcher address).                         |
| `$SANDESH_AUTOSTART` | `1` → arm the wake watcher at session start (the pre-0.4.0 auto-arm; off by default).     |
| `$SANDESH_BIN`       | dev/test override: run exactly this binary (no PATH lookup, no `uvx` fallback).           |

Individual tools accept an explicit `project_id` that falls back to `$SANDESH_PROJECT`.
The extension reads these from the process environment only and never reads `.env` — load
the project's `.env` at the shell (e.g. direnv: an `.envrc` containing `dotenv`, then
`direnv allow`). If `./.env` assigns the identity but it is not exported, session start warns.
With both identity vars set, session start injects a ≤ 12-line **ambient status block** (your
address, listening state, unread count) so the agent knows its mailbox state before the
first turn.

## The tools (16)

| Tool                    | What it does                                                                     |
| ----------------------- | -------------------------------------------------------------------------------- |
| `sandesh_setup`         | provision a project (idempotent).                                                |
| `sandesh_register`      | add an address to the project's addressbook.                                     |
| `sandesh_unregister`    | remove (soft-delete) an address; tombstones a live watcher first.                |
| `sandesh_addressbook`   | list the project's registered addresses.                                         |
| `sandesh_send`          | send a message (subject, optional body, `to`/`cc`, kind).                        |
| `sandesh_reply`         | reply to a message (threads on the parent; subject defaults to `Re: …`).         |
| `sandesh_inbox`         | list messages addressed to an address (filters, `limit`).                        |
| `sandesh_fetch`         | fetch + mark-read messages for an address (`full` for bodies).                   |
| `sandesh_thread`        | walk the reply chain of a message (`full` for bodies).                           |
| `sandesh_archive`       | archive a project (read-only, reversible; `dry_run`).                            |
| `sandesh_unarchive`     | restore an archived project to active (`dry_run`).                               |
| `sandesh_search`        | FTS5 full-text search over subjects/bodies (`limit`).                            |
| `sandesh_status`        | home view: address, listening, unread — plus `watcher: running\|stopped`.        |
| `sandesh_notify_start`  | start the supervised wake watcher for an address (idempotent, one per address); returns the watcher table. |
| `sandesh_notify_status` | list in-session watchers: address, running, last exit by default; `project` scopes the list, and is required when watchers span projects; `fields` selects watcher columns. |
| `sandesh_notify_stop`   | stop one watcher by address or an aggregate scoped by `project`; unscoped multi-project stops return an error. |

### Results are AXI envelopes (`--format toon`)

Every tool returns the CLI's machine-readable **AXI envelope** in TOON encoding — the same
text the CLI prints with `--format toon` (`verb`, `ok`, per-verb fields, `help[]`,
`warnings[]`). Shape knobs: `fields: ["a","b"]` selects columns (`--fields a,b`), `full: true`
includes complete message bodies on `fetch`/`thread` (`--full`), and `limit: N` caps
`inbox`/`search` (`--limit N`). Omitted knobs add no flag. **Errors are results, not
throws:** a CLI failure that still emits an envelope comes back as `ok: false` with `error`
and `help[]`, so the agent can read and recover; only an undecodable failure (no envelope on
stdout) throws with the verb, exit code and stderr.

## Wake

A Pi extension cannot, by itself, re-enter a sleeping session. The extension runs the
blocking `sandesh notify` watcher as a supervised child; when mail addressed to the watched
address arrives, the watcher exits with the unread ids and the extension injects a follow-up
turn (`sendUserMessage(…, { deliverAs: "followUp" })`) naming them. The agent fetches and
acts; the supervisor relaunches the watcher.

**The wake is tool-started (0.4.0):** call `sandesh_notify_start` (defaults to
`$SANDESH_ADDRESS` / `$SANDESH_PROJECT`), or set `SANDESH_AUTOSTART=1` to keep the
pre-0.4.0 auto-arm at session start (both identity vars must then be set). Supervision rules:

- **Exit 0 (mail)** → one follow-up turn naming the ids, then an immediate relaunch.
- **Same ids again** (nothing fetched yet) → no repeat message; relaunch delayed 30 s.
- **Exit 2 (timeout)** → silent relaunch; 3 timeouts within 60 s surface one warning.
- **Exit 5 (already running elsewhere)** → one quiet retry after 30 s (the other
  owner may be a previous watcher still winding down; the entry stays `running`); a
  second exit 5 in a row stops the loop quietly.
- **Exit 1 / 3 (tombstoned) / 4 (evicted) / killed by signal** → the loop stops and a
  notice carries the code and the envelope's `error`.
- **One watcher per address:** a second `start` for the same address returns
  `already: true` and spawns nothing; different addresses run concurrently. After a
  `stop`, a `start` for the same address is accepted at once but spawns its child only
  when the stopped one has exited, so it never loses the address to it.
- `session_shutdown` stops every watcher.

Inspect or stop from the prompt with `/sandesh-watcher status [--project <id>]` and
`/sandesh-watcher stop [address] [--project <id>]`; mixed-project aggregates require the
project filter. `sandesh_notify_status` / `sandesh_notify_stop` are the tool equivalents.

## Manual end-to-end smoke test

1. Pick a project, e.g. `Demo`, and provision it plus two addresses:

   ```bash
   sandesh setup --project Demo
   sandesh --project Demo register --address "Mainline - Demo" --kind mainline
   sandesh --project Demo register --address "Track 1 - Demo" --kind track
   ```

2. Start a Pi session with the extension installed and the env vars set for one address:

   ```bash
   export SANDESH_PROJECT=Demo
   export SANDESH_ADDRESS="Track 1 - Demo"
   export SANDESH_AUTOSTART=1   # or call sandesh_notify_start from inside the session
   pi
   ```

3. From another terminal, send mail to the agent's address:

   ```bash
   sandesh --project Demo send --from "Mainline - Demo" --to "Track 1 - Demo" \
     --subject "ping"
   ```

4. Confirm the Pi session **wakes** (a follow-up turn naming the message id is injected) and
   the agent **fetches** the message (`sandesh_fetch` shows the `ping`). The watcher then
   relaunches and waits for the next message; `/sandesh-watcher status` shows it running.

The automated counterpart is `src/smoke.test.ts`: it resolves the binary from `$SANDESH_BIN`
→ `<repo>/.venv/bin/sandesh` → PATH, runs every spawn under a temp `XDG_DATA_HOME`, and skips
when the binary lacks `--format`.
