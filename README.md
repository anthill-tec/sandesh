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
console scripts (`sandesh`, `sandesh-mcp`) land on `$PATH`. Per-project runtime data lives under
the XDG data dir:

```
$XDG_DATA_HOME/sandesh/projects/<project_id>/     (default ~/.local/share/sandesh/…)
├── sandesh.db
└── messages/msg-<id>.md
```

## Install

**Distribution name:** `sandesh-relay` (the import package + the `sandesh` / `sandesh-mcp`
commands keep the name `sandesh`; `sandesh` was taken on PyPI). The `[mcp]` extra adds the MCP
server; the bare install is the stdlib-only CLI + `notify`.

> **Two things need `sandesh` on PATH:** the MCP client spawns `sandesh-mcp`, **and** the agent
> backgrounds `sandesh notify` (the wake) every cycle. A **persistent** install puts both on PATH
> and is the steady-use recommendation; ephemeral `uvx` is ideal for trial / the registration command.

### uv (recommended)

```bash
# persistent — both scripts on PATH (run `uv tool update-shell` once for PATH)
uv tool install 'sandesh-relay[mcp]'           # once published to PyPI (CR-SAN-010)
uv tool install '.[mcp]'                        # from a local checkout, today

# ephemeral — run the server with no install (deps cached on first run)
uvx --from 'sandesh-relay[mcp]' sandesh-mcp
```

uv manages its own Python, so it sidesteps PEP 668 (see below).

### pipx / pipxu (alternative)

```bash
pipx install 'sandesh-relay[mcp]' && pipx ensurepath   # user-space; restart shell once
sudo pipx install --global 'sandesh-relay[mcp]'        # all users (pipx ≥ 1.5)
```

(`pipxu` gives the same UX on a uv backend — common on Arch.)

### install.sh (offline / from-source fallback)

No uv or pipx? `install.sh` builds its **own venv** and pip-installs the package into it — it
needs only `python3` (with `venv`) + `pip`, and is **PEP-668-safe** (the venv is not the system
environment):

```bash
./install.sh           # → venv at ~/.local/share/sandesh/.venv + symlinks on ~/.local/bin
```

### No installer present?

Neither uv nor pipx is guaranteed. Bootstrap one, **or** use `install.sh`:

```bash
# uv:   sudo pacman -S uv   |   curl -LsSf https://astral.sh/uv/install.sh | sh   |   pip install --user uv
# pipx: pip install --user pipx && pipx ensurepath   |   sudo pacman -S python-pipx
```

> **PEP 668:** a plain `pip install sandesh-relay` into the **system** Python is *blocked* on
> externally-managed distros (Arch, Debian, Fedora, recent macOS) by design. Use uv, pipx, or
> `install.sh` (each isolates into a venv). On Arch, the AUR `PKGBUILD` (CR-SAN-009) sidesteps this
> entirely — pacman resolves the prerequisites.

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

# reply (threads under the parent; the reply IS the completion signal)
sandesh --project Nai reply --to-msg 1 --from "Mainline - Nai" --body "ack"

sandesh --project Nai addressbook
```

`$SANDESH_PROJECT` and `$SANDESH_ADDRESS` default `--project` and the caller's own
address. `$SANDESH_POLL_SECONDS` sets the watcher cadence (default 10, floor 3).

## MCP server

The verbs are also exposed as an **MCP server** (stdio) so an agent can call them as tools
instead of shelling out. `mcp` is the only third-party dependency — it ships behind the optional
**`[mcp]` extra** and is imported only by `sandesh.mcp_server`, so the CLI above stays pure-stdlib
either way. Installing `sandesh-relay[mcp]` (above) provides the `sandesh-mcp` command; a base
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
- **Next** (Phase 3): PyPI publish (`sandesh-relay`, CR-SAN-010), AUR `PKGBUILD` (CR-SAN-009),
  official MCP Registry listing (CR-SAN-011).

## License

Copyright © 2026 anthill-tec. Licensed under the **GNU General Public License v3.0** — see
[`LICENSE`](LICENSE). You may use, study, modify, and redistribute Sandesh under the GPLv3;
distributed derivatives must remain GPL-licensed with source available.
