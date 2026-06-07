# CR-SAN-008 — Packaging: `pyproject.toml`, `sandesh/` package, console scripts, `[mcp]` extra

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-001, CR-SAN-002, CR-SAN-003, CR-SAN-004
**Labels:** phase-3, distribution, packaging, refactor
**Phase:** Phase 3
**Design reference:** docs/research/PRD-distribution.md §3 (D1–D4, D6), §5

## Context

Make Sandesh a standard, cross-platform **pip package** (the primary install per
PRD-distribution). This replaces the bespoke `install.sh` venv+wrapper + manual `$PATH` edit
with `pyproject.toml` + console-script entry points (placed on `$PATH` by pip/pipx) + an
optional `mcp` extra. It **subsumes CR-SAN-007** (PATH hardening) and changes the *mechanism*
of PRD-mcp-server D2 (the CLI stays import-clean of `mcp`; isolation now via pipx + an extra).

Out of scope here: the AUR PKGBUILD (CR-SAN-009) and Windows **runtime** (DN-windows-notifier).

## Scope

### §S1 — Restructure `app/` → an importable `sandesh/` package
- Move `app/{cli,sandesh_db,notify,mcp_server}.py` → `sandesh/{cli,sandesh_db,notify,mcp_server}.py`
  with a `sandesh/__init__.py`.
- Replace the `sys.path.insert(...)` hacks (in `mcp_server.py` and the tests) with proper
  package imports (e.g. `from sandesh import sandesh_db` or intra-package relative imports).
- `cli.py` and `mcp_server.py` each expose a `main()` callable for the entry points.

### §S2 — `pyproject.toml`
- `[project]`: `name = "sandesh"`, a version, `requires-python = ">=3.10"` (matches the `mcp`
  extra), description, readme, **`license = "GPL-3.0-only"`** (SPDX; matches the repo `LICENSE`
  — or `GPL-3.0-or-later` if the "or later" upgrade clause is wanted) + the GPLv3 trove
  classifier.
- `[project.scripts]`: `sandesh = "sandesh.cli:main"`, `sandesh-mcp = "sandesh.mcp_server:main"`.
- `[project.optional-dependencies]`: `mcp = ["mcp>=1.27,<2"]`.
- A build backend (setuptools or hatchling — CR decides); core CLI has **no** runtime deps.

### §S3 — Install paths (installer-agnostic; **`uv` primary**, per PRD-distribution D2)
The package is installer-agnostic; the entry points are what each installer puts on `$PATH`.
- `pip install .` (clean venv) → `sandesh` and `sandesh-mcp` on `$PATH`; CLI works with **no**
  third-party package present. `pip install '.[mcp]'` → `mcp` importable; `mcp` imported ONLY by
  `sandesh.mcp_server`. (This is the build-mechanics contract every installer relies on.)
- **`uv` (primary).** `uv tool install 'sandesh[mcp]'` (persistent — both scripts on PATH;
  `uv tool update-shell`), or `uvx --from 'sandesh[mcp]' sandesh-mcp` (ephemeral, zero-install).
  uv manages its own Python → PEP-668-safe. **Dual-channel note:** the wake (`sandesh notify`,
  relaunched each cycle) also needs `sandesh` on PATH, so **persistent `uv tool install` is the
  steady-use recommendation**; `uvx` suits trial + the MCP-registration command.
- **`pipx`/`pipxu` (alternative).** `pipx install 'sandesh[mcp]'` + `pipx ensurepath` (one-time
  profile edit; shell restart). User-space default; `sudo pipx install --global` (≥1.5) all-users.
- README leads with `uv` (uvx / `uv tool install`), then pipx, then `install.sh`.
- **No-installer handling (don't assume uv/pipx).** README must cover neither being present:
  bootstrap `uv` (`sudo pacman -S uv` / Astral script / `pip install --user uv`) or `pipx`
  (`pip install --user pipx`), **or** use the `install.sh` fallback (§S5 — own venv; `python3`+
  `pip` only). Warn that a plain `pip install sandesh`
  into the system Python is blocked on externally-managed distros (**PEP 668**) — use a venv or
  pipx. On Arch, the AUR PKGBUILD (CR-SAN-009) sidesteps this entirely (pacman resolves deps).

### §S4 — Tests adapt to the package layout
- Update test imports to the package (drop `sys.path` hacks); the whole suite stays green.
- T3 (e2e subprocess) spawns the server via the installed `sandesh-mcp` entry point **or**
  `python -m sandesh.mcp_server` (CR decides), not the old `app/mcp_server.py` path.

### §S5 — `install.sh` demoted to fallback
- Keep a working offline/dev install (either a thin wrapper that runs `pip install .` into a
  venv, or the copy-based install updated to the new layout). README presents `uv` first and
  `install.sh` as the no-network/from-source fallback.

### §S6 — Friendly error when the `[mcp]` extra is absent
Console-script entry points are unconditional, so a base (`no-[mcp]`) install still ships
`sandesh-mcp`, and invoking it would raise a raw `ImportError` (`sandesh.mcp_server` imports
`mcp` at module load). **Guard the import** so a missing `mcp` produces a clear, actionable
message and a non-zero exit — e.g.:
> `sandesh-mcp requires the MCP extra. Install it with:  pipx install 'sandesh[mcp]'  (or  pip install 'sandesh[mcp]')`

— instead of a traceback. The `sandesh` CLI is unaffected (it never imports `mcp`).

## Acceptance criteria

- [ ] **AC1** — a `sandesh/` package exists (`__init__.py` + `cli.py`, `sandesh_db.py`,
      `notify.py`, `mcp_server.py`); `grep -rn "sys.path.insert" sandesh/` returns nothing
      (no path hacks in production modules).
- [ ] **AC2** — `pyproject.toml` declares `[project.scripts]` `sandesh = "sandesh.cli:main"`
      and `sandesh-mcp = "sandesh.mcp_server:main"`, `[project.optional-dependencies]
      mcp = ["mcp>=1.27,<2"]`, and `requires-python >= 3.10`.
- [ ] **AC3** — in a clean venv, `pip install .` then `sandesh --help` exits 0 with **no**
      `mcp` installed (stdlib-only CLI), and `which sandesh` / `which sandesh-mcp` resolve on
      `$PATH` (the venv's scripts dir).
- [ ] **AC4** — `pip install '.[mcp]'` makes `python -c "import mcp"` succeed in that env and
      `sandesh-mcp` launches the stdio server; `grep -rln "import mcp" sandesh/` lists only
      `mcp_server.py`.
- [ ] **AC5** — `cli.main` and `mcp_server.main` are importable callables wired to the entry
      points (the console scripts invoke them).
- [ ] **AC6** — the full test suite passes against the new layout: the stdlib tests
      (`sandesh_db`/CLI) run with system `python3` and **no** `mcp`; the MCP + e2e tests run
      under the `[mcp]` env; T3 spawns via the entry point / `python -m sandesh.mcp_server`.
- [ ] **AC7** — README install section **leads with `uv`** (`uv tool install 'sandesh[mcp]'`
      persistent, and `uvx --from 'sandesh[mcp]' sandesh-mcp` ephemeral), notes the dual-channel
      recommendation (persistent install so the wake's `sandesh` is on PATH), lists **`pipx`**
      (`pipx install … && pipx ensurepath`, +`--global`) as the alternative, and documents
      `install.sh` as the offline/from-source fallback (still functional).
- [ ] **AC8** — in a base install (no `[mcp]`), running `sandesh-mcp` prints a clear message
      naming the fix (`pipx install 'sandesh[mcp]'`) and exits non-zero — **not** a raw
      `ImportError`/traceback. (Test: invoke the entry point in an env without `mcp`; assert the
      message + non-zero exit.) The `sandesh` CLI still works in that same env.
- [ ] **AC9** — README handles **no installer present (uv/pipx absent)**: a bootstrap line for
      `uv` (`pacman -S uv` / Astral script / `pip install --user uv`) or `pipx`
      (`pip install --user pipx`) **and** the `install.sh` fallback, plus a PEP-668 warning that
      plain `pip install` into system Python is blocked (use a venv / uv / pipx).

## Estimated size
Medium–large: a structural refactor (module moves + import rewrites across code & tests) +
`pyproject.toml` + installer/README edits. Mechanical but broad.

## Risks / open questions
- The `sys.path.insert` test bootstrap is removed — every test's imports change; do it
  mechanically and run the full suite.
- Build backend choice (setuptools vs hatchling) — pick one; keep deps minimal.
- `install.sh`'s venv+wrapper logic is largely retired; decide keep-as-fallback vs replace
  with `pipx install`.
- Decide whether T3 uses the `sandesh-mcp` console script (truest) or `python -m sandesh.mcp_server`.

## Non-goals
- AUR PKGBUILD (CR-SAN-009).
- Windows **runtime** support (DN-windows-notifier).
- Publishing to public PyPI (a separate release decision; package is installable from source/Git).
- Any change to the MCP tool surface or messaging semantics.
