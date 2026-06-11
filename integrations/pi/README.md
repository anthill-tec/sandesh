# @anthill-tec/sandesh-pi

A [Pi](https://pi.dev) extension that exposes [Sandesh](https://github.com/anthill-tec/sandesh)
— a tiny, standalone, multi-project messaging system for cooperating agent/orchestrator
sessions — as native Pi tools, plus a background wake loop that re-enters the Pi session
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

## Prerequisite — the `sandesh` CLI on PATH

The extension calls the `sandesh` binary; it must be installed and on your `PATH`
(CR-SAN-008). Install it via any of:

```bash
uv tool install sandesh-relay     # uv
pipx install sandesh-relay        # pipx
./install.sh                      # from a repo checkout
# or the AUR package on Arch
```

Verify: `sandesh --help`.

## Environment

| Variable           | Purpose                                                              |
| ------------------ | ------------------------------------------------------------------- |
| `$SANDESH_PROJECT` | the project store the tools route to (the per-project mailbox).      |
| `$SANDESH_ADDRESS` | this session's own Sandesh address (the wake loop watches this).     |

Both are required for the native wake loop (it needs to know which project to poll and
which address to wait on). Individual tools accept an explicit `project_id` that falls
back to `$SANDESH_PROJECT`.

## Verbs (the tools)

| Tool                  | What it does                                                       |
| --------------------- | ----------------------------------------------------------------- |
| `sandesh_setup`       | provision a project store (idempotent).                           |
| `sandesh_register`    | add an address to the project's addressbook.                      |
| `sandesh_unregister`  | remove (soft-delete) an address; tombstones a live watcher first. |
| `sandesh_addressbook` | list the project's registered addresses.                          |
| `sandesh_send`        | send a message (subject, optional body, `to`/`cc`, kind).         |
| `sandesh_reply`       | reply to a message (threads on the parent, optional `--resolves`).|
| `sandesh_inbox`       | list messages addressed to an address.                            |
| `sandesh_fetch`       | fetch + mark-read messages for an address.                        |
| `sandesh_thread`      | walk the reply chain of a message.                                |

## Native wake

A Pi extension cannot, by itself, re-enter a sleeping session. This extension launches the
blocking `sandesh notify` watcher as a **background process**; when mail addressed to
`$SANDESH_ADDRESS` arrives the watcher exits, and the extension injects a turn into the Pi
session via `sendUserMessage` (then triggers the turn). The agent then fetches and acts,
and the wake loop is relaunched.

The wake loop needs both `$SANDESH_PROJECT` and `$SANDESH_ADDRESS` set.

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
   pi   # run the session; the extension launches the wake loop on start
   ```

3. From another terminal, send mail to the agent's address:

   ```bash
   sandesh --project Demo send --from "Mainline - Demo" --to "Track 1 - Demo" \
     --subject "ping"
   ```

4. Confirm the Pi session **wakes** (a new turn is injected) and the agent **fetches** the
   message (`sandesh_fetch` shows the `ping`). The wake loop then relaunches and waits for
   the next message.
