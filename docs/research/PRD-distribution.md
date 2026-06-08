# PRD — Sandesh Distribution & Packaging

**Status:** DRAFT
**Owner:** Mainline - Sandesh
**Wave:** Wave 3 (Distribution)
**Related:** `PRD-mcp-server.md` (supersedes its D2 venv+wrapper *install mechanism* and §7a
PATH handling), `DN-windows-notifier.md` (runtime portability caveat)

Design contract (WHY + WHAT) for how Sandesh is **installed and distributed cross-platform**.
CRs derived from it cite it via `**Design reference:**`.

---

## 1. Why

Sandesh today installs via a bespoke `install.sh` that copies `app/` + `bin/` into the XDG
data dir, symlinks launchers into `~/.local/bin`, and (for the MCP server) builds a venv +
a `sandesh-mcp` wrapper. That works on Linux but:
- is **not cross-platform** (bash, XDG paths, symlinks, shell-profile PATH edits);
- re-implements, by hand, what Python packaging tools already do (isolated venv, deps,
  `$PATH` entry points);
- makes the `$PATH` problem (launchers callable by bare name) a manual shell-profile edit
  (the CR-SAN-007 concern).

A standard **Python package** solves install, dependency isolation, and `$PATH` together,
on Linux/macOS/Windows.

## 2. What we ship (the decision)

We ship **one artifact — a standard Python package** (`pyproject.toml` + console scripts +
`[mcp]` extra). It is **installer-agnostic**: any of the tools below consume it. The package is
primary; the installer is the user's choice. **`uv` is the recommended primary** (it's the
MCP ecosystem's de-facto tool, fast, and manages its own Python so it sidesteps PEP 668).

| Channel | Role | Mechanism |
|---|---|---|
| **`uv` — `uvx` / `uv tool install`** | **PRIMARY (recommended)** | `uvx --from 'sandesh[mcp]' sandesh-mcp` (ephemeral, zero-install) **or** `uv tool install 'sandesh[mcp]'` (persistent; both console scripts on PATH) |
| **`pipx` / `pipxu`** | **ALTERNATIVE** (equivalent isolation) | `pipx install 'sandesh[mcp]'` + `pipx ensurepath`; `pipxu` = pipx UX on a uv backend (Arch) |
| **PKGBUILD (AUR)** | **SECONDARY** (Arch-native) | `yay -S sandesh`; pacman resolves prerequisites (no uv/pipx bootstrap needed) |
| **`install.sh`** | **FALLBACK** (no uv/pipx, no network, dev) | builds its own venv with plain `python3`+`pip`; PEP-668-safe |

## 3. Decisions

**D1 — A real Python package (`pyproject.toml`).** Restructure the `app/` modules (which today
rely on a `sys.path.insert` hack) into an importable **`sandesh/` package** (`sandesh.cli`,
`sandesh.sandesh_db`, `sandesh.notify`, `sandesh.mcp_server`). Build backend: setuptools or
hatchling (CR decides). This is the prerequisite for every other channel.

**D2 — Installer-agnostic isolation + PATH; `uv` is the recommended primary.** The package's
`[project.scripts]` entry points — `sandesh = "sandesh.cli:main"` and
`sandesh-mcp = "sandesh.mcp_server:main"` — are what every installer drops on `$PATH`, each in
its own isolated venv. This **subsumes** PRD-mcp-server D2 (venv+wrapper) and **CR-SAN-007**
(PATH hardening): no bespoke wrapper, no per-shell PATH logic.
- **`uv` (primary).** `uv tool install 'sandesh[mcp]'` → isolated, both scripts on PATH
  (`uv tool update-shell` once for PATH); or **`uvx --from 'sandesh[mcp]' sandesh-mcp`** to run
  the server **ephemerally** (no install; deps cached on first run). `uv run --with mcp …` is the
  same pattern the MCP SDK's own `mcp install` emits. uv manages its own Python → **no PEP-668
  issue**.
- **`pipx`/`pipxu` (alternative, equivalent).** `pipx install 'sandesh[mcp]'` + `pipx ensurepath`
  (one-time profile edit; shell restart). User-space by default; `sudo pipx install --global`
  (pipx ≥ 1.5) for all users. `pipxu` = the same UX on a uv backend (Arch).

**Dual-channel caveat (wake vs verbs).** Sandesh needs BOTH `sandesh-mcp` (the server the client
spawns) AND `sandesh` (the wake — the agent backgrounds `sandesh notify` *every cycle*). A
**persistent** install (`uv tool install` / `pipx install`) puts *both* on PATH — best for the
frequently-relaunched wake. Pure **ephemeral `uvx`** is ideal for the server/registration and
try-before-install, but the wake would then also run via `uvx --from sandesh sandesh notify …`
(a cold-ish resolve per relaunch). **Recommendation: persistent install for steady use; `uvx`
for zero-install/trial and as the MCP-registration command.**

**D2a — the installer (uv/pipx) may be absent; raw `pip` into system Python is blocked.**
Neither `uv` nor `pipx` is guaranteed present. **Bootstrap one:** `uv` via `sudo pacman -S uv`
/ the Astral install script / `pip install --user uv`; or `pipx` via `pip install --user pipx &&
pipx ensurepath` / `sudo pacman -S python-pipx`. **Do NOT** fall back to a plain
`pip install sandesh` into the system interpreter — on externally-managed Pythons (**PEP 668** —
Arch, Debian, Fedora, recent macOS) that is **blocked** by design. So *something* must create a
venv: `uv`, `pipx`, the `install.sh` fallback (D6), or a hand-rolled venv. (On Arch the PKGBUILD,
D5, dodges this entirely — pacman installs the prerequisites.) Docs must cover the "no uv/pipx"
case, not assume it.

**D3 — `mcp` is an optional extra (`sandesh[mcp]`), preserving stdlib-only CLI.**
`[project.optional-dependencies] mcp = ["mcp>=1.27,<2"]`. `pipx install sandesh` → the
stdlib-only CLI + `notify` (no `mcp`); `pipx install 'sandesh[mcp]'` → adds the MCP server to
the *same* isolated venv. `sandesh.mcp_server` is the only module that imports `mcp`, so the
CLI path stays import-clean either way (the D2-from-the-MCP-PRD *intent* is preserved; only the
*mechanism* changes from a hand-built venv to pipx + an extra).

**D4 — One distribution serves both front ends.** The package exposes both console scripts.
The agent calls `sandesh` (e.g. backgrounded `sandesh notify`, the wake) and the MCP client
spawns `sandesh-mcp` — both by bare name, both on `$PATH` via the entry points.

**D5 — PKGBUILD is secondary, derives from the package, and resolves prerequisites natively.**
An AUR `PKGBUILD` builds from the `pyproject.toml` / source tarball. Its advantage over the pip
path on Arch: **pacman/AUR resolves declared `depends` automatically**, so the prerequisite
problem from D2a disappears — the PKGBUILD declares its deps (`python`, and `python-mcp` for the
server if it's in the repos/AUR; else vendor `mcp` at build time) and a `yay -S sandesh` pulls
them in. No manual pipx bootstrap, no PEP-668 dance — pacman owns the install. (Still Arch-only,
so not *the* cross-platform answer, but it's the smoothest path **for Arch users**.) Add it after
the pip package exists.

**D6 — `install.sh` is the no-pipx, PEP-668-safe fallback (kept, demoted in docs).** It builds
its **own venv** (`python -m venv`) and pip-installs into it, so it needs only `python3` (with
`venv`) + `pip` — **no pipx**, and it's PEP-668-safe because the venv is not the system
environment. This makes it the answer for "only pip is available" / no-network / from-checkout
/ dev. README points to `pipx` first; `install.sh` is the documented fallback. (Revisit removing
it only if pipx becomes universally assumable — which D2a says it is not.)

**D7 — The "global repo" for uv / pipx / pipxu IS PyPI; there is no separate uv registry.**
`uv`, `pipx`, and `pipxu` are *installers/clients* — they all pull from **PyPI** (or any
PEP-503 index you configure). There is no uv-specific package registry. So **publishing to
PyPI is exactly what lets `uv tool install` / `uvx --from` / `pipx install` / `pipxu install`
fetch Sandesh** — one publish target serves all three. **Distribution name: `sandesh-relay`**
(`sandesh` is taken on PyPI); the import package + `sandesh`/`sandesh-mcp` console scripts are
unchanged. Building/publishing can use either `python -m build` + `pypa/gh-action-pypi-publish`
(the GitHub trusted-publishing template) **or** uv-native `uv build` + `uv publish` (uv also
supports OIDC trusted publishing) — both end at PyPI. See CR-SAN-010.

## 4. Cross-platform scope

- **Linux / macOS:** install + runtime fully supported via pip/pipx.
- **Windows:** **install** works (pip/pipx; Scripts dir on `$PATH`). **Runtime is NOT yet
  guaranteed** — the `notify` watcher + liveness use POSIX-isms (`os.kill(pid, 0)`, `signal`).
  Packaging ≠ runtime portability. The runtime gap is analyzed in
  **`DN-windows-notifier.md`** and is out of scope for the packaging CR.

## 5. CR breakdown

| CR | Scope | Depends on |
|---|---|---|
| **CR-SAN-008** | **Packaging:** restructure `app/` → `sandesh/` package, `pyproject.toml` with `[project.scripts]` (`sandesh`, `sandesh-mcp`) + `[mcp]` extra; installer-agnostic (uv/pipx/pip); fix tests for the new layout; demote `install.sh` to fallback; README install-via-**uv** (uvx / `uv tool install`) first, pipx alternative. | CR-SAN-001..004 |
| **CR-SAN-009** | **PKGBUILD (AUR):** build from the package/source; resolve `mcp`; install both console scripts. | CR-SAN-008 |
| **CR-SAN-010** | **PyPI release (trusted publishing) → enables uv/pipx/pipxu (D7).** Publish `sandesh-relay` to PyPI on GitHub `release: published`, via OIDC trusted publishing (`pypa/gh-action-pypi-publish` **or** `uv build`+`uv publish`). Prereqs (user/maintainer): register the PyPI project + configure a **Trusted Publisher** (repo `anthill-tec/sandesh`, workflow file, env `pypi`); create the **`pypi` GitHub environment**. Add `.github/workflows/`. | CR-SAN-008 |

(CR-SAN-007 "install PATH hardening" is **SUPERSEDED** by CR-SAN-008 — pipx/entry points
solve `$PATH`.)

## 6. Non-goals / out of scope

- ~~Publishing to the public PyPI index~~ — **now PLANNED as CR-SAN-010** (the global index that
  uv/pipx/pipxu pull from, per D7); distribution name `sandesh-relay`, via OIDC trusted publishing.
- Windows **runtime** support for the watcher (see `DN-windows-notifier.md`).
- Homebrew / .deb / .rpm / Nix (revisit by demand once pip + AUR exist).
- Any change to the MCP tool surface or messaging semantics (covered by the MCP PRD).
