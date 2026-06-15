# PRD — Install / Provisioning / Teardown Lifecycle

**Status:** APPROVED (2026-06-15)
**Author:** Vidushi (orchestrator), with owner
**Supersedes/absorbs:** CR-SAN-035 (install.sh `--uninstall`) becomes the *teardown* half of this lifecycle.

---

## 1. Problem

Provisioning is **asymmetric across install routes**, which produces unusable installs:

- Only **`install.sh`** provisions a store — it runs `migrate --all` → `consolidate` → `reindex` →
  super-admin assignment (`$SANDESH_ADMIN`). Those steps live *only* in the shell script.
- **`uv tool` / `pipx` / `pip` / AUR** install the wheel via PEP 517 and run **no post-install
  script** (pip removed that capability by design). So a package-manager user gets working
  binaries but an **unprovisioned store**: no super-admin (so `grant`/`tombstone` are unusable), no
  consolidation of legacy stores, no FTS reindex, and — critically — **no automatic migration when
  upgrading over an existing store**, risking a schema-behind DB in use.
- The super-admin can be assigned **only** at `install.sh` time; there is deliberately **no
  CLI/MCP surface** for it — so a package-only install can *never* get an admin.

A tool that, depending on how it was installed, silently yields a non-functional configuration is
broken. **Every install route must yield a usable, migrated, admin-provisioned store, and updates
must migrate automatically — without exposing initialization over MCP.**

## 2. Hard constraints (the design must live within these)

1. **Wheels run no post-install hooks.** `pip`/`uv`/`pipx`/AUR cannot auto-execute a script or
   introspect the data dir at install time. Provisioning therefore cannot be an *installer* feature
   for those routes — it must be a **capability of the package** (a CLI command + runtime logic).
2. **The core stays stdlib-only** (CLAUDE.md locked value). The migration engine deps
   (`yoyo`+`jsonschema`) remain the optional **`[migrate]`** extra — not a core dependency.
3. **No MCP initialization surface** (owner-locked). `init`/admin/migrate are CLI / install-time
   only; no MCP tool may create/configure a store or set the admin.
4. **No self-mutation of managed environments.** The tool must not `pip install` its own deps into a
   uv/pipx-managed venv at runtime (unsupported, can corrupt the env). It *instructs* instead.

## 3. Goals

- G1 — **Every install route can fully provision** a store (migrate + consolidate + reindex + admin).
- G2 — **Updates auto-migrate** on every route, with no user action in the common case.
- G3 — **Single source of truth** for provisioning (no logic duplicated between `install.sh` and the
  package).
- G4 — **Symmetric teardown** (`install.sh --uninstall`) and a documented uninstall path per route.
- G5 — Constraints §2 fully honored.

## 4. Design

### 4.0 Two interchangeable agent surfaces (the model everything else serves)
Sandesh has one **CLI core** (stdlib-only: verbs + store) and **two alternative agent surfaces**
over it:
- **MCP server** (`[mcp]` extra → `sandesh-mcp`) — the surface for **Claude / MCP clients**.
- **Pi extension** (npm `@anthill-tec/sandesh-pi`) — the surface for the **Pi harness**; it is a
  **replacement for** the MCP server (it has its own native wake), **not** a consumer of it. A Pi
  user needs **neither `[mcp]` nor `sandesh-mcp`** — that is *why* `[mcp]` is a separate extra.

A machine may run **Claude, Pi, or both** (both is a real setup — they share the same CLI + the same
global store). So the install must let the user **choose the surface(s)** and pull only what each
needs; nothing is assumed/defaulted-on. `[mcp]` is for the Claude path only; the Pi extension for the
Pi path only; `[migrate]` is recommended on every path.

### 4.1 `sandesh init` — the single provisioning entry point (CLI only)
A new idempotent CLI command, shipped *in the package* so every route has it:
`sandesh init [--admin <name>] [--yes]` → runs, in order: **migrate** (if `[migrate]` present, else
the §4.2 actionable notice) → **consolidate** (stdlib) → **reindex** (stdlib) → **admin assignment**
(from `--admin`, else interactive prompt, else `$SANDESH_ADMIN`, else skip-with-notice; the existing
"refuse different-name re-assign" rule holds). Idempotent: safe to re-run. **Never an MCP tool**
(§2.3). `install.sh` **delegates to `sandesh init`** instead of carrying its own provisioning block
(G3).

### 4.2 Lazy auto-migrate on store open (self-healing) — G2
The library, when it opens a store whose schema is **behind** (cheap version-sentinel check in
`connect()`/`setup()`), **applies pending migrations automatically** if `[migrate]` is present.
If `[migrate]` is absent, it raises an **actionable error** that detects the install method and
prints the exact remediation (`uv tool install --with …` / `pipx inject …` / `pip install
'sandesh-relay[migrate]'` / pacman), never a self-`pip` (§2.4). A fresh/empty store is a no-op.

### 4.3 Per-route provisioning
| Route | Provision | Migrate-on-update |
|---|---|---|
| **install.sh** | runs `sandesh init` at the end | **detects an existing `sandesh.db` → `[migrate]` is MANDATORY**: pull it and migrate; if it can't (offline), **fail loudly** (no silent skip on a real store). Fresh install → `[migrate]` best-effort. |
| **uv / pipx / pip** | user runs `sandesh init` once (first-run nudge when an unprovisioned store is detected) | recommended install command includes `[migrate]` → lazy auto-migrate just works; else §4.2 notice |
| **AUR** | pacman **post-install message** points at `sandesh init` | migrate deps shipped as a dependency (not optdepend) so lazy auto-migrate works |

### 4.4 Surface choice at install + docs restructure (README slimmed, per-route guides split out)
**Install-time surface choice.** The installer asks which agent surface(s) the user wants and pulls
only those:
- **Claude (MCP)** → CLI + `[mcp]` (+ `[migrate]`); register `sandesh-mcp`.
- **Pi** → CLI (+ `[migrate]`), **no `[mcp]`**; the Pi surface is the npm extension (§4.6).
- **Both** → CLI + `[mcp]` + `[migrate]` + the Pi extension.
- **CLI-only / none** → base.
`install.sh` does this **interactively** (with non-interactive overrides — `SANDESH_INSTALL_EXTRAS`
/ a flag — for CI). Package-manager routes can't prompt (no hook), so they're served by **per-surface
documented commands** (the right `uv`/`pipx` extra set per surface).

**Docs restructure.** The README is **slimmed** (what/why/model + a quick-start pointer) and the
per-route install detail is **split into separate guides** (`docs/INSTALL.md` or `docs/install/`),
each organized **by route AND surface**, carrying its own install → `sandesh init` → manage
(auto-migrate, admin) → uninstall flow. The super-admin is documented as init/`$SANDESH_ADMIN` only
(never a CLI verb, never MCP). **AUR is removed from the README** (unpublished / not meaningful yet;
the PKGBUILD + RELEASING steps remain for when it ships).

### 4.5 Teardown (absorbs CR-SAN-035)
`install.sh --uninstall [--purge]` removes the symlinks + venv (and `--purge` the data store), prints
the `claude mcp remove sandesh` reminder. Docs gain an **"Uninstalling" matrix** per route
(`uv tool uninstall` / `pipx uninstall` / `pip uninstall` (+orphans caveat) / pacman / install.sh)
plus the two manual steps every route shares: remove the **data store** and the **MCP registration**.

### 4.6 Pi extension (npm) — the Pi-path agent surface (alternative to MCP)
The Pi extension (`@anthill-tec/sandesh-pi`) is the **Pi-harness surface** — the counterpart to the
MCP server, **not** a consumer of it. It shells to the `sandesh` CLI via `pi.exec` for the verbs and
uses its own native wake; it needs **no `[mcp]` and no `sandesh-mcp`**.
- A Pi user installs the **CLI (without `[mcp]`, with `[migrate]` recommended)** + the Pi extension.
  To kill the two-step friction, the Pi extension acquires the CLI **on-demand via uvx** —
  `uvx --from 'sandesh-relay[migrate]' sandesh …` (**no `mcp`**) — so there is no separate CLI install
  step; the store persists in XDG and `sandesh init` is run once (via uvx) for admin.
- Pi needs **no `[migrate]` of its own**: lazy auto-migrate (§4.2) runs inside the CLI it invokes;
  Pi's error-passthrough surfaces the actionable `[migrate]`/provision message **verbatim** — no
  shim, no self-install.
- The existing **≥0.2.0 CLI session gate is extended** to also detect an **unprovisioned store**
  (admin unset / store absent) and emit a one-line nudge to run `sandesh init`. It still exposes
  **no init/admin/migrate tool** (Pi mirrors the MCP surface, which excludes them — §2.3).
- **Coexistence:** when a machine runs **both** Claude and Pi, both surfaces operate over the same
  CLI + same global store; installing the Pi extension never requires/affects `[mcp]` and vice-versa.
- **Teardown:** removing the Pi extension (Pi's own uninstall) removes only the extension, never the
  `sandesh` CLI or its data — a distinct row in the §4.5 uninstall matrix.

## 5. Non-goals
- Auto-running provisioning *at wheel-install time* for package managers (impossible — §2.1).
- Runtime self-installation of deps into managed envs (§2.4).
- Any MCP surface for init/admin/migrate (§2.3).
- Making `[migrate]` a core dependency (§2.2).
- Treating npm/Pi as a Sandesh-core install or provisioning route (it is a consumer layer — §4.6).

## 6. Open questions
- (resolved by owner 2026-06-15) `[migrate]` stays optional + actionable error; lazy-migrate
  on-open; PRD-first.
- Admin prompt UX in `sandesh init` — interactive prompt vs flag/env only when stdin is non-tty
  (to be settled at CR time).

## 7. Verification themes
Per-route install→use→update→uninstall integration tests (isolated `HOME`/`XDG_DATA_HOME`);
lazy-migrate self-heal test (behind store + `[migrate]` present → auto-applied; absent → actionable
error, no self-pip); `sandesh init` idempotency; `install.sh` mandatory-migrate-on-existing-DB;
Pi session gate nudges `sandesh init` on an unprovisioned store + passes the `[migrate]` message
through; README lifecycle markers (install/provision/manage/uninstall per route).
