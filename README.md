# Sandesh

<!-- mcp-name: io.github.anthill-tec/sandesh -->

**संदेश** (Sanskrit/Hindi: *message · dispatch*) — a tiny, standalone, multi-project
**messaging system for cooperating agent/orchestrator sessions**. A SQLite-backed
maildir + a mailbox watcher, pure Python stdlib (no third-party deps).

**Latest release** ([see all releases](https://github.com/anthill-tec/sandesh/releases)) —
the global store (one DB for all projects, cross-project messaging behind an admin grant,
archive→tombstone lifecycle), inbox filters + FTS5 search (CLI/MCP/Pi), 12 MCP tools, and
the Pi extension at full parity.

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
| `message` | the envelope — subject (required), kind, `in_reply_to`, `body_path` |
| `message_recipient` | per-message addressees — `role` (`to`/`cc`) + per-recipient `read_at` |
| `notifier` | per-session watcher liveness — pid, token, heartbeat, tombstone |

Semantics: **To wakes / Cc silent** · `all-tracks` **broadcast** (minus sender) ·
**per-recipient read** (read = being acted on; **reply = done**) · **subject-only** ⟷
**file-body** (full absolute paths) · **keep history** · **reply threading** ·
**crash-safe liveness** (dead-pid / stale-heartbeat reap) · **cooperative tombstone eviction** ·
**validated address format**.

## Layout

Sandesh is a standard Python package (`sandesh/`) installed via `pyproject.toml`; the two
console scripts (`sandesh`, `sandesh-mcp`) land on `$PATH`. Runtime data lives under
the XDG data dir — one global DB for all projects, plus a body folder per project:

```
$XDG_DATA_HOME/sandesh/                           (default ~/.local/share/sandesh/…)
├── sandesh.db                                    (the ONE global DB, WAL — all projects)
└── projects/<project_id>/
    └── messages/msg-<id>.md
```

## Install

**Distribution name:** `sandesh-relay` (the import package + the `sandesh` / `sandesh-mcp`
commands keep the name `sandesh`; `sandesh` was taken on PyPI). The bare install is the
stdlib-only CLI + `notify`; the `[mcp]` extra adds the MCP server.

Sandesh is **CLI + a chosen agent surface** (MCP for Claude Code, or the Pi extension). The
full per-route × per-surface walkthrough — install → `sandesh init` (provision) → manage
(auto-migrate on update, admin) → uninstall, plus the uninstall matrix — lives in the
install guide:

➡ **[Install & uninstall guide → docs/INSTALL.md](docs/INSTALL.md)**

In short: pick a route (uv / pipx / pip / `install.sh`), install the `sandesh-relay`
distribution with the extras for your surface (`[mcp,migrate]` for Claude, `[migrate]` for Pi),
then provision with `sandesh init` (`--check` to verify). PyPI publishing is automated via OIDC
trusted publishing — see **[RELEASING.md](RELEASING.md)**.

## Use

```bash
sandesh setup --project Atlas
sandesh --project Atlas register --address "Mainline - Atlas" --kind mainline
sandesh --project Atlas register --address "Track 2 - Atlas"  --kind track

# send (subject-only ⇢ no file; --body/--body-file ⇢ md body)
sandesh --project Atlas send --from "Track 2 - Atlas" --to "Mainline - Atlas" \
        --subject "CR-308 started — chain unaffected"

# the watcher (run in the background; exits 0 with ids when 'to' mail lands)
sandesh --project Atlas notify --to "Mainline - Atlas"

# read (consolidates unread to+cc, marks read)
sandesh --project Atlas fetch --to "Mainline - Atlas"

# reply (threads under the parent; the reply IS the completion signal)
sandesh --project Atlas reply --to-msg 1 --from "Mainline - Atlas" --body "ack"

sandesh --project Atlas addressbook
```

`$SANDESH_PROJECT` and `$SANDESH_ADDRESS` default `--project` and the caller's own
address. `$SANDESH_POLL_SECONDS` sets the watcher cadence (default 10, floor 3).

### Cross-project messaging

All projects share the one global DB, but sending **across** projects is **admin-gated**:
the sender's project needs a one-time grant — `sandesh grant --cross-project --project <id>
--by <admin>` (revoke with `sandesh revoke --cross-project …`; project-wide, inherited by
every participant of the granted project). Without it, the send fails with
`cross-project sending not approved for project '<id>' — ask the Sandesh admin`. The admin
is assigned **only at install** via `$SANDESH_ADMIN` (`install.sh`) — there is no CLI or MCP
surface to set it. `all-tracks` broadcasts never cross projects, grant or not.
`sandesh projects` shows each project's state and grant (`PROJECT  STATE  CROSS-PROJECT`).

### Project lifecycle

A project moves `active → archived → tombstoned` — strictly two-step, with two-tier
authorization:

```bash
# read-only freeze — reversible, deletes NOTHING (project's own Mainline only)
sandesh archive   --project Atlas --by "Mainline - Atlas"
sandesh unarchive --project Atlas --by "Mainline - Atlas"

# permanent retirement — ARCHIVED projects only (the install-assigned super-admin only)
sandesh tombstone --project Atlas --by <admin>          # prompts y/N; --yes to skip
```

- **`archive`** evicts the project's live `notify` watchers (cooperatively; `--force`
  reaps stragglers) and freezes it: sends from/to it and new registrations are refused,
  but every message, body, and thread stays fully readable. **`unarchive`** reverses it.
- **`tombstone`** is destructive and irreversible: it purges the project's *internal*
  messages (sender and all recipients inside the project) and deletes its
  `projects/<id>/` body folder. What survives: cross-project envelopes (rows stay for
  audit + thread anchoring — their bodies are lost), and the tracker row itself as a
  permanent `tombstoned` marker (the project id is retired; `setup` refuses to reuse it).
  Afterwards `inbox`/`fetch` hide the tombstoned project's traffic, and `thread` marks
  holes with `incomplete chain — message(s) removed (project tombstoned)`.
- **Who may do what:** `archive`/`unarchive` take the project's **own Mainline** as
  `--by`; `tombstone` takes only the **super-admin** assigned at install via
  `$SANDESH_ADMIN`. Without `--yes`, `tombstone` asks for interactive confirmation
  (and refuses when stdin is not a terminal).
- **`--dry-run`** (all three verbs) reports what would happen — watchers to evict,
  the would-be state, and for `tombstone` the purge counts (`internal messages`,
  `body files`, `cross-project messages` whose bodies would be lost) — and writes
  nothing. Guards still apply: a dry-run on the wrong state or with the wrong `--by`
  errors exactly like the real command.

## MCP server

The verbs are also exposed as an **MCP server** (stdio) so an agent can call them as tools
instead of shelling out. `mcp` is the only third-party dependency — it ships behind the optional
**`[mcp]` extra** and is imported only by `sandesh.mcp_server`, so the CLI above stays pure-stdlib
either way. Installing the `[mcp]` extra (see the [install guide](docs/INSTALL.md)) provides the
`sandesh-mcp` command; a base
install without the extra still ships `sandesh-mcp` but it prints a one-line "install the `[mcp]`
extra" hint and exits non-zero rather than crashing.

**Register with Claude Code** (stdio; bake a default project via env so tools can omit
`project_id`):

```bash
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
```

That writes an `mcpServers` entry (`~/.claude.json` for user scope, or a committed
`.mcp.json` for `--scope project`). Manage with `claude mcp list` / `claude mcp get sandesh`
/ `claude mcp remove sandesh`; the in-session `/mcp` panel shows status + tools.

**Nine tools**, each taking `project_id` (falls back to `$SANDESH_PROJECT`): `sandesh_setup`,
`sandesh_register`, `sandesh_unregister`, `sandesh_addressbook`, `sandesh_send`,
`sandesh_reply`, `sandesh_inbox`, `sandesh_fetch`, `sandesh_thread`. The server also returns
usage `instructions` on `initialize` and serves the full scenarios doc as the `sandesh://usage`
resource. (Lifecycle: **read = being acted on, reply = done** — there is no separate status tool.)

**Manual smoke** — the MCP Inspector (browser UI):

```bash
mcp dev -m sandesh.mcp_server     # needs the [mcp] extra; explore tools by hand
```

The **wake path stays the `notify` watcher** — MCP exposes the *verbs*, not the wake (an MCP
server can't re-invoke a sleeping agent). No `notify`/watch tool is exposed over MCP.

### Discover via the MCP Registry

Sandesh is listed on the **official MCP Registry** as **`io.github.anthill-tec/sandesh`** (see
[`server.json`](server.json)), so MCP-aware clients/aggregators (Claude Code, Cursor, Glama,
PulseMCP, mcp.so, …) can discover it. The listing points at the PyPI distribution `sandesh-relay`
and the stdio launch `uvx --from 'sandesh-relay[mcp]' sandesh-mcp`. **The listing is the *server*
(the verbs) only** — the `notify` wake is a separate background process, not a tool. Publishing the
listing is a maintainer step (after the PyPI release) — see [RELEASING.md](RELEASING.md).

## Test

```bash
python3 -m unittest -v          # from the repo root (stdlib-only: CLI + library)
.venv/bin/python -m unittest -v # includes the MCP server + E2E tests (needs the venv)
```

## Roadmap

- **MCP server — DONE** (Phase 2): 9 tools over stdio + `instructions` + `sandesh://usage`
  resource; `[mcp]`-extra isolation; in-memory + real-subprocess E2E tests. The `notify` watcher
  remains the wake path.
- **Packaging — DONE** (Phase 3): `pyproject.toml` (hatchling + tag-driven `hatch-vcs` version),
  `sandesh`/`sandesh-mcp` console scripts, `[mcp]` extra; uv/pipx/`install.sh` install.
- **PyPI publish — DONE** (Phase 3, CR-SAN-010): `.github/workflows/publish-pypi.yml` publishes
  `sandesh-relay` to PyPI on a GitHub Release via OIDC trusted publishing (TestPyPI dry-run on
  manual dispatch); version is git-tag-driven. See **[RELEASING.md](RELEASING.md)**.
- **Discovery/distribution — DONE** (Phase 3): official **MCP Registry** listing
  (CR-SAN-011, [`server.json`](server.json) `io.github.anthill-tec/sandesh`).
- **Pi extension — DONE** (Phase 4): a native Pi extension at [`integrations/pi/`](integrations/pi/)
  — registers the Sandesh verbs as Pi tools (CR-SAN-013) and a **native wake** (CR-SAN-014: the
  extension wakes the idle agent itself via `sendUserMessage`, no host background task) — published to
  npm as `@anthill-tec/sandesh-pi` (CR-SAN-015). See [`integrations/pi/README.md`](integrations/pi/README.md).
- The registry publishes (PyPI / MCP-registry / npm) are maintainer actions — see RELEASING.md.

## License

Copyright © 2026 anthill-tec. Licensed under the **GNU General Public License v3.0** — see
[`LICENSE`](LICENSE). You may use, study, modify, and redistribute Sandesh under the GPLv3;
distributed derivatives must remain GPL-licensed with source available.
