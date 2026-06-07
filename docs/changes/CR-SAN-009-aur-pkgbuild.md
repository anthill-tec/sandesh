# CR-SAN-009 — AUR PKGBUILD (Arch packaging, secondary)

**Status:** PENDING
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
- `pkgname` — **decision** (gap-analysis): `sandesh-relay` (matches the PyPI dist) vs `sandesh` vs
  `python-sandesh-relay`. Default recommendation: **`sandesh-relay`**.
- `pkgver` / `pkgrel` — `pkgver` tracks the release `X.Y.Z` (the git tag `vX.Y.Z` minus `v`).
- `arch=('any')` (pure Python), `license=('GPL-3.0-only')`, `url` = the GitHub repo.
- `depends=('python')` — the stdlib CLI + `notify` need only CPython.
- **`optdepends=('python-mcp: the MCP server (sandesh-mcp); from AUR')`** — the `[mcp]` extra is
  optional; base install = stdlib CLI. (Decision: optdepends vs a split `sandesh-relay-mcp`
  package; default **optdepends**.)
- `makedepends=('python-build' 'python-installer' 'python-hatchling' 'python-hatch-vcs')` (all
  official).
- `source` + `sha256sums` — **decision**: the **PyPI sdist**
  (`https://files.pythonhosted.org/.../sandesh_relay-$pkgver.tar.gz`, version baked in PKG-INFO —
  canonical, but gated on the CR-SAN-010 first publish) **vs** a **GitHub release tarball / git
  tag** (works before PyPI; but a plain tarball has no `.git`, so hatch-vcs needs
  `HATCH_VCS_PRETEND_VERSION=$pkgver` at build, or use a `git+…#tag=v$pkgver` source). Default
  recommendation: **PyPI sdist** (version baked, no hatch-vcs-without-git problem; same
  post-`v0.1.0` gating as the registry listing).
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

- [ ] **AC1** — `packaging/aur/PKGBUILD` exists with `pkgname` (the agreed name), `pkgver`,
      `arch=('any')`, `license=('GPL-3.0-only')`, `depends=('python')`, the `optdepends` for
      `python-mcp`, and `makedepends` of `python-build`/`python-installer`/`python-hatchling`/`python-hatch-vcs`
      (asserted by parsing the PKGBUILD).
- [ ] **AC2** — `build()` uses `python -m build` (hatchling) and `package()` installs via
      `python -m installer … dist/*.whl` and installs `LICENSE` under
      `usr/share/licenses/$pkgname/` (asserted by parsing the PKGBUILD).
- [ ] **AC3** — `packaging/aur/.SRCINFO` exists and equals `makepkg --printsrcinfo` output (the
      committed `.SRCINFO` is in sync — asserted by regenerating and diffing).
- [ ] **AC4** — `namcap packaging/aur/PKGBUILD` reports no errors (warnings triaged/justified), and
      `shellcheck` (with makepkg's known globals) is clean — recorded.
- [ ] **AC5** — the source line resolves to the agreed source (PyPI sdist `sandesh_relay-$pkgver`
      *or* the GitHub tag) and `sha256sums` are present (`SKIP` allowed only for a VCS source);
      version provenance: the built package version == `pkgver` == release `X.Y.Z`.
- [ ] **AC6** — README documents `yay -S <pkg>` (Arch) + the `python-mcp` (AUR) note; RELEASING.md
      documents the AUR push step (maintainer, post-PyPI-release).

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
