# PRD — Sandesh Distribution & Packaging

**Status:** DRAFT
**Owner:** Mainline - Sandesh
**Phase:** Phase 3 (Distribution)
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

| Channel | Role | Mechanism |
|---|---|---|
| **pip package + `pipx`** | **PRIMARY** (cross-platform) | `pyproject.toml`; `pipx install sandesh` (CLI) / `pipx install 'sandesh[mcp]'` (CLI + MCP server) |
| **PKGBUILD (AUR)** | **SECONDARY** (Arch convenience) | wraps the pip package / source tarball; `yay -S sandesh` |
| **`install.sh`** | **FALLBACK** (no-network / dev / vendored) | kept, but no longer the recommended path |

## 3. Decisions

**D1 — A real Python package (`pyproject.toml`).** Restructure the `app/` modules (which today
rely on a `sys.path.insert` hack) into an importable **`sandesh/` package** (`sandesh.cli`,
`sandesh.sandesh_db`, `sandesh.notify`, `sandesh.mcp_server`). Build backend: setuptools or
hatchling (CR decides). This is the prerequisite for every other channel.

**D2 — `pipx` is the isolation + PATH mechanism (replaces the bespoke venv+wrapper).**
`[project.scripts]` entry points — `sandesh = "sandesh.cli:main"` and
`sandesh-mcp = "sandesh.mcp_server:main"` — are emitted into pipx's bin dir (`~/.local/bin`);
**`pipx ensurepath`** puts that dir on `$PATH` (a one-time, idempotent profile edit; needs a
shell restart). `pipx` gives each tool its own isolated venv. Default is **user-space** (no
root); `sudo pipx install --global` (pipx ≥ 1.5 → `/usr/local/bin`) is the all-users option.
This **subsumes** PRD-mcp-server D2 (venv+wrapper) and **CR-SAN-007** (PATH hardening): the
wrapper is gone, and the PATH edit becomes the single standard `pipx ensurepath` instead of
bespoke per-shell logic.

**D2a — pipx is a prerequisite that may be absent; raw `pip` into system Python is blocked.**
`pipx` is a separate tool, not guaranteed present. If missing, **bootstrap it**:
`python -m pip install --user pipx && python -m pipx ensurepath`, or via the OS package
(`sudo pacman -S python-pipx`, `apt install pipx`, `brew install pipx`). **Do NOT** fall back to
a plain `pip install sandesh` into the system interpreter: on externally-managed Pythons
(**PEP 668** — Arch, Debian, Fedora, recent macOS) that is **blocked** by design. So *something*
must create a venv — either pipx, or the `install.sh` fallback (D6), or a hand-rolled venv. The
docs must cover the "no pipx" case, not assume it.

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

## 4. Cross-platform scope

- **Linux / macOS:** install + runtime fully supported via pip/pipx.
- **Windows:** **install** works (pip/pipx; Scripts dir on `$PATH`). **Runtime is NOT yet
  guaranteed** — the `notify` watcher + liveness use POSIX-isms (`os.kill(pid, 0)`, `signal`).
  Packaging ≠ runtime portability. The runtime gap is analyzed in
  **`DN-windows-notifier.md`** and is out of scope for the packaging CR.

## 5. CR breakdown

| CR | Scope | Depends on |
|---|---|---|
| **CR-SAN-008** | **Packaging:** restructure `app/` → `sandesh/` package, `pyproject.toml` with `[project.scripts]` (`sandesh`, `sandesh-mcp`) + `[mcp]` extra, pip/pipx-installable; fix tests for the new layout; demote `install.sh` to fallback; README install-via-pipx. | CR-SAN-001..004 |
| **CR-SAN-009** | **PKGBUILD (AUR):** build from the package/source; resolve `mcp`; install both console scripts. | CR-SAN-008 |

(CR-SAN-007 "install PATH hardening" is **SUPERSEDED** by CR-SAN-008 — pipx/entry points
solve `$PATH`.)

## 6. Non-goals / out of scope

- Publishing to the public PyPI index (a release step; the package is pip-installable from
  source/Git regardless — decide PyPI separately).
- Windows **runtime** support for the watcher (see `DN-windows-notifier.md`).
- Homebrew / .deb / .rpm / Nix (revisit by demand once pip + AUR exist).
- Any change to the MCP tool surface or messaging semantics (covered by the MCP PRD).
