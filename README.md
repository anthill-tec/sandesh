# Sandesh

**संदेश** (Sanskrit/Hindi: *message · dispatch*) — a tiny, standalone, multi-project
**messaging system for cooperating agent/orchestrator sessions**. A SQLite-backed
maildir + a mailbox watcher, pure Python stdlib (no third-party deps).

Built for the "Model-B" parallel-orchestration pattern (a Mainline coordinator +
worker *Track* sessions that can't talk to each other directly), but project-agnostic.

## Why

Sessions can't message each other directly, and re-invoking a *sleeping* agent is
only possible via its host's background-task mechanism. Sandesh provides the relay:
a durable, queryable mailbox each session reads from, plus a blocking **notify**
watcher a session runs in the background so it *wakes* when mail addressed to it
arrives.

## Model

| table | holds |
|---|---|
| `address` | the addressbook — durable identities, `'<Orchestrator> - <Project>'` |
| `message` | the envelope — subject (required), kind, status, `in_reply_to`, `body_path` |
| `message_recipient` | per-message addressees — `role` (`to`/`cc`) + per-recipient `read_at` |
| `notifier` | per-session watcher liveness — pid, token, heartbeat, tombstone |

Semantics: **To wakes / Cc silent** · `all-tracks` **broadcast** (minus sender) ·
**per-recipient read** · **subject-only** ⟷ **file-body** (full absolute paths) ·
**keep history + `actioned`** · **reply threading** · **crash-safe liveness**
(dead-pid / stale-heartbeat reap) · **cooperative tombstone eviction** ·
**validated address format**.

## Layout

```
$XDG_DATA_HOME/sandesh/          (default ~/.local/share/sandesh/)
├── app/                         the code (sandesh_db.py, cli.py, notify.py)
├── bin/sandesh                  launcher (symlinked onto ~/.local/bin)
└── projects/<project_id>/
    ├── sandesh.db
    └── messages/msg-<id>.md
```

## Install

```bash
./install.sh           # → ~/.local/share/sandesh/ + ~/.local/bin/sandesh
```

## Use

```bash
sandesh setup --project Nai
sandesh --project Nai register --address "Mainline - Nai" --kind mainline
sandesh --project Nai register --address "Track 2 - Nai"  --kind track

# send (subject-only ⇢ no file; --body/--body-file ⇢ md body)
sandesh --project Nai send --from "Track 2 - Nai" --to "Mainline - Nai" \
        --subject "CR-308 started — chain unaffected"

# the watcher (run in the background; exits 0 with ids when 'to' mail lands)
sandesh --project Nai notify --to "Mainline - Nai"

# read (consolidates unread to+cc, marks read)
sandesh --project Nai fetch --to "Mainline - Nai"

# reply (threads; --resolves closes the parent request)
sandesh --project Nai reply --to-msg 1 --from "Mainline - Nai" --resolves --body "ack"

sandesh --project Nai addressbook
```

`$SANDESH_PROJECT` and `$SANDESH_ADDRESS` default `--project` and the caller's own
address. `$SANDESH_POLL_SECONDS` sets the watcher cadence (default 10, floor 3).

## Test

```bash
python3 -m unittest -v          # from the repo root
```

## Roadmap

- **MCP server** — expose the verbs as MCP tools (each taking `project_id`); the
  `notify` watcher stays the wake path (an MCP server can't re-invoke a sleeping agent).
