# CR-SAN-008 — Packaging: `pyproject.toml`, `sandesh/` package, console scripts, `[mcp]` extra

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-008)
**Priority:** High
**Depends on:** CR-SAN-001, CR-SAN-002, CR-SAN-003, CR-SAN-004, CR-SAN-005 (9-tool surface), CR-SAN-006 (`sandesh://usage` resource — its doc must be bundled here, §S7)
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
- `[project]`: **`name = "sandesh-relay"`** — the PyPI *distribution* name (`sandesh` is already
  taken on PyPI). NOTE: distribution name ≠ import name — the **import package stays `sandesh`**
  and the **console scripts stay `sandesh` / `sandesh-mcp`**; only the `pip install` name is
  `sandesh-relay`. Plus `requires-python = ">=3.10"` (matches the `mcp` extra),
  description, readme, **`license = "GPL-3.0-only"`** (SPDX; matches the repo `LICENSE` — or
  `GPL-3.0-or-later` if the "or later" upgrade clause is wanted) + the GPLv3 trove classifier.
- **Version = git-tag-driven via `hatch-vcs`** (gap-analysis decision). `[project]` declares
  `dynamic = ["version"]` (NOT a static `version =`); `[tool.hatch.version] source = "vcs"`; and the
  build backend requires `["hatchling", "hatch-vcs"]`. Single source of truth = the **git tag**:
  semver `major.minor.patch`, tags formatted **`vX.Y.Z`** (e.g. `v0.1.0`); hatch-vcs strips the `v`
  so the PEP 440 / PyPI version is `0.1.0`. Between tags, builds version as `0.1.1.devN+g<sha>`.
  The first release tag (`v0.1.0`) is cut on the eventual git-flow release to `main` (CR-SAN-010
  publishes the exact tagged version); on `develop` the dev-suffixed version is expected/fine.
  Expose `sandesh.__version__` (and a `sandesh --version`) via `importlib.metadata.version`.
- `[project.scripts]`: `sandesh = "sandesh.cli:main"`, `sandesh-mcp = "sandesh.mcp_server:main"`.
- `[project.optional-dependencies]`: `mcp = ["mcp>=1.27,<2"]`.
- **Build backend: `hatchling`** (gap-analysis decision). `[build-system] requires = ["hatchling"]`,
  `build-backend = "hatchling.build"`. Core CLI has **no** runtime deps. Configure hatchling to
  include the package and the bundled usage doc (§S7) as package data.

### §S3 — Install paths (installer-agnostic; **`uv` primary**, per PRD-distribution D2)
The package is installer-agnostic; the entry points are what each installer puts on `$PATH`.
> **Distribution name = `sandesh-relay`.** Every `sandesh[mcp]` shorthand below means
> **`sandesh-relay[mcp]`** (the PyPI install name); the invoked console scripts remain
> `sandesh` / `sandesh-mcp`. E.g. `uvx --from 'sandesh-relay[mcp]' sandesh-mcp`,
> `pipx install 'sandesh-relay[mcp]'`.
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
The current suite is **7 files**, all using `sys.path.insert(..., "app")` + bare
`import sandesh_db`/`import mcp_server`/`import cli`. Convert every one to package imports:
- **stdlib (mcp-free):** `tests/test_sandesh.py` → `from sandesh import sandesh_db`.
- **MCP suites:** `tests/test_mcp_server.py`, `tests/test_mcp_read_tools.py`,
  `tests/test_mcp_mutating_tools.py`, `tests/test_mcp_surface.py` → `from sandesh import mcp_server`
  / `from sandesh import sandesh_db` (drop the `sys.path` hack).
- **e2e:** `tests/test_mcp_e2e.py` → package imports; **T3 spawns via `python -m sandesh.mcp_server`**
  (gap-analysis decision — robust without requiring the console script on PATH), not the old
  `app/mcp_server.py` path.
- **`tests/test_install.py` — REWRITE** (gap-analysis decision): it currently asserts the *old*
  `install.sh` venv+wrapper + `app/mcp_server.py` (CR-SAN-001 AC1–AC3), which §S1/§S5 dismantle.
  Replace it with **package-install integration tests**: in a fresh venv, `pip install .` →
  `sandesh`/`sandesh-mcp` on PATH + stdlib-only CLI works with no `mcp` (AC3); `pip install '.[mcp]'`
  → `import mcp` works + server launches (AC4); base install `sandesh-mcp` prints the friendly error
  and exits non-zero (AC8). (May be renamed `tests/test_package_install.py`.)
- The whole suite stays green under the new layout.

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

### §S7 — Bundle the usage doc so `sandesh://usage` serves real content when installed
CR-SAN-006 added the `sandesh://usage` MCP resource, whose `_read_usage_doc()` resolves
`docs/usage-scenarios.md` by walking up from the module file. That works from a source checkout,
but a pip/uv/pipx install ships **only the `sandesh/` package — not the repo `docs/`** — so the
resource silently degrades to its stub. CR-SAN-006 deferred bundling to this CR; do it now:
- **Include `usage-scenarios.md` as package data** inside the `sandesh` package (e.g.
  `sandesh/data/usage-scenarios.md`, kept in sync with / sourced from `docs/usage-scenarios.md`),
  declared to hatchling so it ships in the wheel.
- **Switch `_read_usage_doc()` to `importlib.resources`** (e.g.
  `importlib.resources.files("sandesh").joinpath("data/usage-scenarios.md").read_text()`) to read
  the bundled copy — robust in any install layout — keeping the non-empty stub as the final
  fallback. (Keep a single source of truth: either move the canonical doc under the package and
  have the repo `docs/` reference it, or copy it in at build time — CR decides the sync mechanism;
  simplest is to relocate the canonical file under `sandesh/data/` and update the few doc links.)

## Acceptance criteria

- [x] **AC1** — a `sandesh/` package exists (`__init__.py` + `cli.py`, `sandesh_db.py`,
      `notify.py`, `mcp_server.py`); `grep -rn "sys.path.insert" sandesh/` returns nothing
      (no path hacks in production modules).
- [x] **AC2** — `pyproject.toml` declares `[project.scripts]` `sandesh = "sandesh.cli:main"`
      and `sandesh-mcp = "sandesh.mcp_server:main"`, `[project.optional-dependencies]
      mcp = ["mcp>=1.27,<2"]`, and `requires-python >= 3.10`.
- [x] **AC2b** — versioning is git-tag-driven: `[build-system].requires` includes `hatch-vcs`,
      `[project].dynamic` includes `"version"` (no static `version =`), and
      `[tool.hatch.version].source == "vcs"`. A build from a `vX.Y.Z`-tagged checkout produces the
      `X.Y.Z` PEP 440 version (asserted by config presence; build-from-tag check optional), and
      `sandesh.__version__` / `sandesh --version` resolves via `importlib.metadata`.
- [x] **AC3** — in a clean venv, `pip install .` then `sandesh --help` exits 0 with **no**
      `mcp` installed (stdlib-only CLI), and `which sandesh` / `which sandesh-mcp` resolve on
      `$PATH` (the venv's scripts dir).
- [x] **AC4** — `pip install '.[mcp]'` makes `python -c "import mcp"` succeed in that env and
      `sandesh-mcp` launches the stdio server; `grep -rln "import mcp" sandesh/` lists only
      `mcp_server.py`.
- [x] **AC5** — `cli.main` and `mcp_server.main` are importable callables wired to the entry
      points (the console scripts invoke them).
- [x] **AC6** — the full test suite passes against the new layout: the stdlib tests
      (`sandesh_db`/CLI) run with system `python3` and **no** `mcp`; the MCP + e2e tests run
      under the `[mcp]` env; T3 spawns via the entry point / `python -m sandesh.mcp_server`.
- [x] **AC7** — README install section **leads with `uv`** (`uv tool install 'sandesh[mcp]'`
      persistent, and `uvx --from 'sandesh[mcp]' sandesh-mcp` ephemeral), notes the dual-channel
      recommendation (persistent install so the wake's `sandesh` is on PATH), lists **`pipx`**
      (`pipx install … && pipx ensurepath`, +`--global`) as the alternative, and documents
      `install.sh` as the offline/from-source fallback (still functional).
- [x] **AC8** — in a base install (no `[mcp]`), running `sandesh-mcp` prints a clear message
      naming the fix (`pipx install 'sandesh[mcp]'`) and exits non-zero — **not** a raw
      `ImportError`/traceback. (Test: invoke the entry point in an env without `mcp`; assert the
      message + non-zero exit.) The `sandesh` CLI still works in that same env.
- [x] **AC9** — README handles **no installer present (uv/pipx absent)**: a bootstrap line for
      `uv` (`pacman -S uv` / Astral script / `pip install --user uv`) or `pipx`
      (`pip install --user pipx`) **and** the `install.sh` fallback, plus a PEP-668 warning that
      plain `pip install` into system Python is blocked (use a venv / uv / pipx).
- [x] **AC10** — the usage doc is bundled as package data and `sandesh://usage` serves the **real**
      content from an installed wheel: after `pip install '.[mcp]'` in a clean venv,
      `read_resource("sandesh://usage")` returns the full `usage-scenarios.md` (NOT the stub) —
      asserted by a substring unique to the doc (e.g. `"Model-B"` / a §-heading); and
      `grep -rn "sys.path.insert" sandesh/` is empty (the resource uses `importlib.resources`, not a
      path walk). (Build mechanics: `python -m build`/`hatchling` wheel contains `usage-scenarios.md`.)
- [x] **AC11** — all **7** test modules pass under the package layout with no `sys.path` hacks in
      tests: stdlib `tests/test_sandesh.py` under system `python3` (no `mcp`); the MCP suites
      (`test_mcp_server`, `test_mcp_read_tools`, `test_mcp_mutating_tools`, `test_mcp_surface`,
      `test_mcp_e2e`) under the `[mcp]` env; and the rewritten `test_install.py`/`test_package_install.py`
      exercising the package install (AC3/AC4/AC8). T3 spawns via `python -m sandesh.mcp_server`.

## Gap-analysis findings (2026-06-07) — verdict SPEC_UPDATE applied; now READY

Analyzed against `develop` after CR-SAN-005/006 merged (this CR predated them). Findings folded
into scope above:
- **DRIFT-1 (Dim 3 → §S7/AC10):** CR-SAN-006's `sandesh://usage` resolves `docs/usage-scenarios.md`
  by walking up from the module; an installed wheel ships no `docs/`, so it degrades to the stub.
  Resolution: bundle the doc as package data + `importlib.resources` (§S7), asserted by AC10.
- **DRIFT-2 (Dim 2 → §S4/AC11):** real test inventory is **7 files**, all with `sys.path.insert`
  hacks; **`test_install.py` tests the old `install.sh` venv+wrapper + `app/mcp_server.py`** that
  §S1/§S5 dismantle. Resolution: enumerate all 7 in §S4, rewrite `test_install.py` for the package
  install, AC11.
- **DRIFT-3 (Dim 1, minor):** Depends-on updated to include CR-SAN-005/006.
- **Decisions (gap-analysis):** build backend = **hatchling**; **versioning = git-tag-driven via
  `hatch-vcs`** (`dynamic=["version"]`, `source="vcs"`; semver `vX.Y.Z` tags → PEP 440 `X.Y.Z`);
  usage doc = **bundle + importlib.resources**; `test_install.py` = **rewrite for package install**;
  T3 = **`python -m sandesh.mcp_server`**; `install.sh` = **kept as the PEP-668-safe own-venv
  fallback** (PRD D6), rewritten to the new layout.
- **No blocking code drift** — the move is structural; `mcp` stays isolated to `sandesh/mcp_server.py`.

## Estimated size
Medium–large: a structural refactor (module moves + import rewrites across code & tests) +
`pyproject.toml` + installer/README edits + doc bundling. Mechanical but broad.

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

## Implementation Notes (2026-06-07)

Five RED→GREEN cycles + a README docs step + VERIFY → FIX → pre-merge, agent-dispatched.

- **C0** — restructure (`5d83e47` RED / `23b0856` GREEN): `git mv app/* → sandesh/` + `__init__.py`;
  dropped every `sys.path.insert` hack (production + all 7 test files); T3 spawns `python -m
  sandesh.mcp_server`. `app/` removed.
- **C1** — `pyproject.toml` (`dee7345` / `5e46ece`): hatchling + **hatch-vcs tag-driven version**
  (`dynamic=["version"]`, `source="vcs"`), console scripts `sandesh`/`sandesh-mcp`, `[mcp]` extra,
  `requires-python>=3.10`, GPL-3.0-only; `sandesh.__version__` + `sandesh --version` via
  `importlib.metadata`. Wheel built clean (`sandesh_relay-0.1.devN+g<sha>`).
- **C2** — friendly `[mcp]`-absent error (`17ff862` / `361e09d`): guarded the mcp imports; `main()`
  prints the `[mcp]`-extra hint + exits non-zero (no traceback). 9-tool surface intact when present.
- **C3** — doc bundling (`aae381f` / `a3f8596`): `git mv docs/usage-scenarios.md →
  sandesh/data/usage-scenarios.md`; `_read_usage_doc()` now uses `importlib.resources`; wheel
  **proven** to contain the doc. `sandesh://usage` serves real content in source AND installed.
- **C4** — package-install integration (`5259974` / `616f769`): replaced `test_install.py` with
  clean-venv `pip install .` / `.[mcp]` tests (AC3/AC4/AC8); rewrote `install.sh` as the
  PEP-668-safe own-venv fallback (pip-installs the package, symlinks the console scripts).
- **README** (docs): install via uv (uvx / `uv tool install`) first, dual-channel note, pipx
  alternative, install.sh fallback, PEP-668 + no-installer bootstrap (AC7/AC9); fixed
  layout/MCP-path/tool-count (10→9) staleness.
- **VERIFY** (`CR-SAN-008-VERIFY`): 184 tests green, all AC1–AC11 PASS, 0 blocking; 1 SHOULD-FIX +
  2 cosmetic. **FIX** (`7c6d4f8`): removed the last `sys.path` hack (test_usage_resource_packaging,
  AC11) + refreshed the `mcp_server.py` docstring for the package layout.
- **Pre-merge gate**: py_compile clean; stdlib baseline 24/24 (system python3, no `mcp`);
  in-process suites 123/123; install integration 16/16. Coverage `mcp_server.py` 90% /
  `sandesh_db.py` 81% / total 55% (cli/notify exercised via subprocess installs, uncaptured).
- **Contract preserved**: 9 MCP tools, `sandesh_reply` signature, `mcp` isolated to
  `sandesh/mcp_server.py`. **CR-SAN-007 (PATH hardening) stays SUPERSEDED** by this CR.
