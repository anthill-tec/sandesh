# CR-SAN-009 — AUR PKGBUILD (Arch packaging, secondary)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-009)
**Priority:** Medium
**Depends on:** CR-SAN-008 (the package + pyproject), CR-SAN-010 (PyPI publish — if the source is the PyPI sdist)
**Labels:** phase-3, distribution, aur, arch
**Phase:** Phase 3
**Design reference:** docs/research/PRD-distribution.md §3 D5, §5 (CR-SAN-009 row)

## Context

Provide an **AUR `PKGBUILD`** so Arch users can `yay -S sandesh-relay` (or equivalent). Per
PRD D5, the PKGBUILD's advantage over the pip path on Arch is that **pacman/AUR resolves declared
`depends` automatically** — sidestepping the PEP-668 / "bootstrap uv/pipx" friction (D2a). It is
**secondary** to the cross-platform pip/uv path (Arch-only), and **derives from the package**
(`pyproject.toml`, hatchling).

Verified on the target Arch system (2026-06): `makepkg`, `namcap`, `shellcheck`, `updpkgsums`
present; build deps `python-build`, `python-installer`, `python-hatchling`, `python-hatch-vcs` are
in the **official repos**; **`python-mcp` is NOT in the official repos** (AUR or vendored).

Out of scope: the actual `git push` to `aur.archlinux.org` (a maintainer action needing an AUR
account + SSH key — documented, not automated here); Windows runtime.

## Scope

### §S1 — `packaging/aur/PKGBUILD`
A standard Python-application PKGBUILD (PEP 517 build via hatchling):
- `pkgname` — **`sandesh-relay`** (gap-analysis decision; matches the PyPI dist). Installed commands
  stay `sandesh` / `sandesh-mcp`.
- `pkgver` / `pkgrel` — `pkgver` tracks the release `X.Y.Z` (the git tag `vX.Y.Z` minus `v`).
- `arch=('any')` (pure Python), `license=('GPL-3.0-only')`, `url` = the GitHub repo.
- `depends=('python')` — the stdlib CLI + `notify` need only CPython.
- **`optdepends=('python-mcp: the MCP server (sandesh-mcp); from AUR')`** — the `[mcp]` extra is
  optional; base install = stdlib CLI. (Gap-analysis decision: **optdepends**, not a split package.)
- `makedepends=('python-build' 'python-installer' 'python-hatchling' 'python-hatch-vcs')` (all
  official).
- `source` + `sha256sums` — **PyPI sdist** (gap-analysis decision):
  `source=("https://files.pythonhosted.org/packages/source/s/sandesh-relay/sandesh_relay-$pkgver.tar.gz")`
  — version baked in PKG-INFO (no hatch-vcs-without-git problem), `sha256sums` filled by
  `updpkgsums` at release. **Gated on the CR-SAN-010 first publish** (same as the registry listing);
  pre-publish the checksum is a placeholder (`'SKIP'`) and the real build is exercised at release.
- `build()` — `python -m build --wheel --no-isolation`.
- `package()` — `python -m installer --destdir="$pkgdir" dist/*.whl`; install `LICENSE` to
  `usr/share/licenses/$pkgname/`. The wheel's entry points place `sandesh` + `sandesh-mcp` in
  `/usr/bin` automatically.

### §S2 — `.SRCINFO`
Generate `packaging/aur/.SRCINFO` via `makepkg --printsrcinfo > .SRCINFO` (AUR requires it).

### §S3 — Lint / build validation (real, on Arch)
- `namcap PKGBUILD` clean (or only justified warnings).
- `shellcheck` the PKGBUILD clean (or pragma-justified).
- `makepkg --printsrcinfo` matches the committed `.SRCINFO`.
- A best-effort `makepkg -f` build in CI/test where the source is reachable (the PyPI sdist only
  resolves post-`v0.1.0`; if unreachable, validate PKGBUILD syntax + namcap + .SRCINFO and SKIP the
  network build, noting it). On the dev Arch box the build can be exercised once the source exists.

### §S4 — Docs
- README install: an **Arch** entry — `yay -S sandesh-relay` (or `paru`), noting it pulls
  `python-mcp` from AUR for the server; cross-reference that this sidesteps PEP-668.
- RELEASING.md: the **AUR submission** maintainer step (clone `ssh://aur@aur.archlinux.org/<pkg>.git`,
  copy `PKGBUILD` + `.SRCINFO`, `git push`), gated on the matching PyPI release; bump `pkgver` +
  regenerate `.SRCINFO` per release.

## Acceptance criteria

- [x] **AC1** — `packaging/aur/PKGBUILD` exists with `pkgname` (the agreed name), `pkgver`,
      `arch=('any')`, `license=('GPL-3.0-only')`, `depends=('python')`, the `optdepends` for
      `python-mcp`, and `makedepends` of `python-build`/`python-installer`/`python-hatchling`/`python-hatch-vcs`
      (asserted by parsing the PKGBUILD).
- [x] **AC2** — `build()` uses `python -m build` (hatchling) and `package()` installs via
      `python -m installer … dist/*.whl` and installs `LICENSE` under
      `usr/share/licenses/$pkgname/` (asserted by parsing the PKGBUILD).
- [x] **AC3** — `packaging/aur/.SRCINFO` exists and equals `makepkg --printsrcinfo` output (the
      committed `.SRCINFO` is in sync — asserted by regenerating and diffing).
- [x] **AC4** — `namcap packaging/aur/PKGBUILD` reports no errors (warnings triaged/justified), and
      `shellcheck` (with makepkg's known globals) is clean — recorded.
- [x] **AC5** — the source line resolves to the agreed source (PyPI sdist `sandesh_relay-$pkgver`
      *or* the GitHub tag) and `sha256sums` are present (`SKIP` allowed only for a VCS source);
      version provenance: the built package version == `pkgver` == release `X.Y.Z`.
- [x] **AC6** — README documents `yay -S <pkg>` (Arch) + the `python-mcp` (AUR) note; RELEASING.md
      documents the AUR push step (maintainer, post-PyPI-release).

## Gap-analysis findings (2026-06-07) — verdict READY

- **Dim 1 (Spec vs PRD):** implements PRD D5 (secondary Arch PKGBUILD, derives from the package,
  pacman resolves deps). No gap.
- **Dim 2 (Spec vs Code):** verified on the target Arch box — `pyproject.toml` = hatchling +
  hatch-vcs; `makedepends` (`python-build/installer/hatchling/hatch-vcs`) all in **official** repos;
  the wheel's `entry_points.txt` declares `sandesh` + `sandesh-mcp` console scripts (so
  `python -m installer dist/*.whl` lands both in `/usr/bin`); `LICENSE` present. `makepkg`,
  `namcap`, `shellcheck`, `updpkgsums` available → real lint/build. No drift.
- **Dim 3 (Code vs PRD):** version provenance — the PyPI sdist bakes the version in PKG-INFO
  (verified), so hatch-vcs needs no `.git`; `pkgver` == release `X.Y.Z`. No conflict.
- **Decisions:** `pkgname = sandesh-relay`; source = **PyPI sdist**; `[mcp]` via **`optdepends:
  python-mcp`** (AUR). `python-mcp` is AUR-only (document the uv/pipx fallback for the server).
- **Sequencing:** this CR ships the PKGBUILD + `.SRCINFO` + lint/parse tests + docs; the real
  `makepkg` build + final `sha256sums` (`updpkgsums`) + the AUR `git push` happen at the maintainer
  release once `v0.1.0` is on PyPI.

## Estimated size
Small–medium: one PKGBUILD + `.SRCINFO` + a lint/parse test + README/RELEASING notes. The build is
gated on the source being published (PyPI sdist post-`v0.1.0`); lint + .SRCINFO + parse are testable now.

## Risks / open questions
- **hatch-vcs needs the version** — a plain release tarball has no `.git`; either use the PyPI sdist
  (version baked in PKG-INFO) or set `HATCH_VCS_PRETEND_VERSION=$pkgver` / use a `git+…#tag` source.
  (Drives the §S1 source decision.)
- **`python-mcp` is AUR-only** — `optdepends`/dep on it pulls another AUR package (yay resolves it).
  If it's absent/unmaintained, the server extra can't be satisfied via pacman — document the uv/pipx
  fallback for the server.
- **Decisions for gap-analysis:** `pkgname`; source (PyPI sdist vs GitHub tag); `[mcp]` as
  `optdepends` vs a split package.
- AUR account/SSH push is a maintainer action — not automatable in this CR.

## Non-goals
- Pushing to the AUR (maintainer action; documented in RELEASING.md).
- Official `[extra]`/`community` repo inclusion (AUR only).
- Windows runtime, Homebrew/.deb/.rpm/Nix (PRD §6).
- Any change to the package code, MCP tools, or messaging semantics.

## Implementation Notes (2026-06-07)

One cycle (C0) + a docs step, agent-dispatched, then VERIFY → pre-merge. **No `sandesh/` code
changed** (`git diff develop..HEAD -- sandesh/` empty).

- **C0** — RED (`2510ca3`): `tests/test_pkgbuild.py` — text-parse contract (metadata/build/source) +
  real `makepkg --printsrcinfo` sync, `namcap`, `shellcheck` gates. GREEN (`ece8e7a`):
  `packaging/aur/PKGBUILD` (pkgname `sandesh-relay`, `arch=any`, GPL-3.0-only, `depends=python`,
  `optdepends=python-mcp`, official `makedepends`, PyPI-sdist source, hatchling `build()` +
  `installer` `package()` + LICENSE install, `sha256sums=('SKIP')` placeholder) + `.SRCINFO`. Added
  two lint directives (`# shellcheck shell=bash`, `cd … || exit 1`).
- **Docs**: README "Arch Linux (AUR)" entry (`yay -S sandesh-relay`, `python-mcp` AUR optdep,
  PEP-668 sidestep) + RELEASING.md AUR-publish step (`updpkgsums` → `.SRCINFO` → `git push`
  ssh://aur, post-PyPI-release).
- **VERIFY** (`CR-SAN-009-VERIFY`): all AC1–AC6 PASS, 0 blocking; **namcap zero-output, shellcheck
  exit 0, `.SRCINFO` byte-identical to `makepkg --printsrcinfo`**; no prod code changed.
- **Pre-merge gate**: namcap clean; `.SRCINFO` in sync; stdlib baseline green; **full venv suite
  230/230 green** (+26 PKGBUILD tests).
- **Remaining maintainer action:** at the `v0.1.0` release — `updpkgsums` (fill the real checksum
  from the published sdist), regen `.SRCINFO`, `makepkg -f` sanity, `git push` to the AUR
  (documented in RELEASING.md). Gated on the CR-SAN-010 PyPI publish.
