# Installing & uninstalling Sandesh

This guide covers installing Sandesh by **route** (uv / pipx / pip / `install.sh`) for each
**surface** (Claude / Pi / both), provisioning with `sandesh init`, managing the install
(auto-migrate on update, admin), and the full **uninstall matrix**.

**Distribution name:** `sandesh-relay`. The console scripts keep the name `sandesh`
(`sandesh`, `sandesh-mcp`); the bare PyPI name `sandesh` was already taken.

## The two extras

Sandesh is the stdlib-only CLI + `notify` watcher, with two optional extras:

| extra | adds | who needs it |
|---|---|---|
| `[mcp]` | the MCP server (`sandesh-mcp`, the `mcp` SDK) | the **Claude** surface |
| `[migrate]` | the migration engine (`yoyo` + `jsonschema`) | every persistent install (auto-migrate on update) |

- **Claude surface** → install with `[mcp]` (and `[migrate]`): `sandesh-relay[mcp,migrate]`.
- **Pi surface** → **no** `[mcp]`. Pi shells out to the CLI on demand with
  `uvx --from 'sandesh-relay[migrate]' sandesh` — uvx fetches/caches the package per invocation,
  so there is nothing persistent to install for the verbs themselves.

The lifecycle is the same for every route and surface:

> **install → `sandesh init` (provision; `--check` to verify) → manage (auto-migrate on update, admin) → uninstall**

`sandesh init` is the single provisioning path shared by the installer and operators. It runs
migrate → consolidate → reindex → admin-assign idempotently:

```bash
sandesh init                 # provision the global store (prompts for admin if a tty)
sandesh init --admin <name>  # assign the Sandesh super-admin non-interactively
sandesh init --yes           # non-interactive (no admin prompt)
sandesh init --check         # read-only probe: verify provisioning, change nothing
```

> **Mandatory migrate on an existing DB.** If the global store already exists and its schema is
> behind, `sandesh init` (and `install.sh`) **fails loudly** unless the `[migrate]` extra is present.
> Always install `[migrate]` on a persistent install.

---

## Surface: Claude (MCP)

The Claude surface uses the **`[mcp]`** extra so Claude Code can spawn `sandesh-mcp`. The agent
also backgrounds `sandesh notify` (the wake), so `sandesh` must be on `PATH` — use a **persistent**
install.

### Route: uv (recommended)

```bash
# install — both scripts on PATH (run `uv tool update-shell` once for PATH)
uv tool install 'sandesh-relay[mcp,migrate]'                  # from PyPI (once published)
uv tool install 'git+https://github.com/anthill-tec/sandesh'  # from git, today
uv tool install '.[mcp,migrate]'                              # from a local checkout, today

# provision
sandesh init                 # --check to verify

# register the MCP server with Claude Code (bakes a default project)
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
```

uv manages its own Python, so it sidesteps PEP 668.

### Route: pipx / pipxu

```bash
pipx install 'sandesh-relay[mcp,migrate]' && pipx ensurepath   # user-space; restart shell once
sudo pipx install --global 'sandesh-relay[mcp,migrate]'        # all users (pipx ≥ 1.5)
sandesh init
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
```

(`pipxu` gives the same UX on a uv backend — common on Arch.)

### Route: pip (last resort)

```bash
pip install --user 'sandesh-relay[mcp,migrate]'    # PEP-668-safe only into a venv / --user
sandesh init
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
```

> **PEP 668:** a plain `pip install` into the **system** Python is *blocked* on externally-managed
> distros (Arch, Debian, Fedora, recent macOS). Prefer uv, pipx, or `install.sh` (each isolates into
> a venv).

### Route: install.sh (offline / from-source fallback)

`install.sh` builds its **own venv** and pip-installs the package into it — it needs only `python3`
(with `venv`) + `pip`, and is **PEP-668-safe**. The `--surface` flag picks the extras and provisions
via `sandesh init`:

```bash
./install.sh --surface claude          # claude ⇒ [mcp,migrate], registers via `sandesh init`
SANDESH_ADMIN=<name> ./install.sh --surface claude   # also assign the super-admin
```

`--surface claude|both` ⇒ `[mcp,migrate]`; `--surface pi|none` ⇒ `[migrate]`. With no flag the
installer falls through to `$SANDESH_SURFACE` / a prompt / the default. It then runs
`sandesh init --yes` to provision.

---

## Surface: Pi

The Pi extension (`@anthill-tec/sandesh-pi`) registers the Sandesh verbs as Pi tools and wakes the
agent natively. It does **not** use `[mcp]` — it invokes the CLI **on demand** with uvx:

```bash
uvx --from 'sandesh-relay[migrate]' sandesh   # Pi runs the CLI per invocation (cached); [migrate], NO [mcp]
```

So a Pi-only setup needs **no persistent Python install** for the verbs — just `uv` on PATH and the
Pi extension. To provision the store once:

```bash
uvx --from 'sandesh-relay[migrate]' sandesh init          # provision (--check to verify)
```

If you prefer a persistent CLI on PATH alongside Pi, install it without the MCP extra:

```bash
uv tool install 'sandesh-relay[migrate]'     # uv  — no [mcp]
pipx install 'sandesh-relay[migrate]'        # pipx — no [mcp]
pip install --user 'sandesh-relay[migrate]'  # pip  — no [mcp], venv/--user only
./install.sh --surface pi                    # install.sh — [migrate], no MCP register
sandesh init
```

> Pi requires a ≥ 0.2.0 CLI session; the extension gates on it. See
> [`integrations/pi/README.md`](../integrations/pi/README.md) for installing the extension itself.

---

## Surface: both

Install the **`[mcp,migrate]`** extras (Claude path) so the MCP server is present, and add the Pi
extension separately. Pi still invokes `uvx --from 'sandesh-relay[migrate]' sandesh` on demand — the
persistent install just guarantees `sandesh`/`sandesh-mcp` on PATH for the wake + MCP server.

```bash
uv tool install 'sandesh-relay[mcp,migrate]'           # uv route, both surfaces
# or:
./install.sh --surface both                            # install.sh route, both surfaces
sandesh init
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<id> -- sandesh-mcp
# + install the Pi extension (@anthill-tec/sandesh-pi)
```

---

## Bootstrapping uv / pipx (no installer present)

Neither uv nor pipx is guaranteed. Bootstrap one (these `pacman` hints are the **non-AUR**
prerequisite bootstrap for uv/pipx themselves), **or** use `install.sh` (needs only `python3` + pip):

```bash
# uv:   sudo pacman -S uv          |  curl -LsSf https://astral.sh/uv/install.sh | sh  |  pip install --user uv
# pipx: sudo pacman -S python-pipx |  pip install --user pipx && pipx ensurepath
```

---

## Managing the install

- **Update:** re-run the route's install (`uv tool install …`, `pipx install …`, `./install.sh …`).
  On an existing store the **migration runs automatically** (`install.sh` / `sandesh init` apply
  pending migrations) — this is why `[migrate]` is required on a persistent install. A schema-behind
  store with no `[migrate]` extra aborts loudly rather than running on a stale schema.
- **Verify:** `sandesh init --check` — a read-only probe that confirms the store is provisioned and
  reports the admin, changing nothing.
- **Admin:** the Sandesh super-admin is assigned **only at provisioning** (`sandesh init --admin <name>`
  or `$SANDESH_ADMIN` via `install.sh`). There is deliberately no CLI/MCP surface to change it; a
  different-name re-assign is refused.

---

## Uninstall matrix

Removing the package by route, plus the **shared manual steps** every route needs (the package
managers do not touch the data store or the Claude MCP registration).

| route | uninstall command |
|---|---|
| `install.sh` | `install.sh --uninstall` (KEEPS the data store) · `install.sh --uninstall --purge` (also deletes the data home) |
| uv | `uv tool uninstall sandesh-relay` |
| pipx | `pipx uninstall sandesh-relay` |
| pip | `pip uninstall sandesh-relay` *(orphaned dependencies — `mcp`, `yoyo`, `jsonschema` — are NOT removed; clean them up manually if desired)* |
| Pi extension | remove the `@anthill-tec/sandesh-pi` extension from Pi (it is uvx-on-demand, so there is no persistent CLI to uninstall unless you installed one) |

**Shared manual steps (all routes):**

```bash
# 1. remove the MCP server registration from Claude Code (if you ran `claude mcp add sandesh`)
claude mcp remove sandesh

# 2. delete the data store — the ONE global DB + all per-project bodies
#    (skipped by `install.sh --uninstall` unless --purge; package managers never touch it)
rm -rf ~/.local/share/sandesh        # or "$XDG_DATA_HOME/sandesh" if XDG_DATA_HOME is set
```

> The data store at `~/.local/share/sandesh` (or `$XDG_DATA_HOME/sandesh`) holds `sandesh.db` and
> every project's message bodies. None of the package-manager uninstalls remove it — delete it by
> hand (or use `install.sh --uninstall --purge`).
