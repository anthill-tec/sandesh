# CR-SAN-039 — docs restructure: slim README + per-route/surface install guide + uninstall matrix

**Status:** COMPLETED (implemented on feature/CR-SAN-039; ships in 0.3.0)
**Priority:** Medium
**Depends on:** CR-SAN-036, CR-SAN-037, CR-SAN-038, CR-SAN-035 (documents their behaviours)
**Labels:** docs, lifecycle
**Wave:** provisioning-lifecycle (0.3.0)
**Design reference:** PRD-provisioning-lifecycle §4.4, §4.5.

## Context
The README serves too many audiences at once. The PRD splits install/management docs by route and
surface, removes AUR (unpublished), and adds an uninstall matrix. This CR is documentation-only and
runs last, once the behaviours it describes are final.

## Scope
- **§S1 — slim README.** Keep what/why/model + a quick-start pointer; remove the multi-route install
  blocks (link out to the install guide). Frame the product as CLI + a chosen surface (MCP or Pi).
  **MUST PRESERVE** the `<!-- mcp-name: io.github.anthill-tec/sandesh -->` ownership marker
  (gap-analysis NOTE-1: `test_server_json.py::ReadmeMcpOwnershipMarkerTest` pins it; the MCP
  registry verifies it against the PyPI long-description).
- **§S2 — install guide** (`docs/INSTALL.md` or `docs/install/`): per **route × surface**
  (Claude / Pi / both), each with **install → `sandesh init` → manage (auto-migrate, admin) →
  uninstall**. `[mcp]` only on the Claude path; Pi path uses uvx-on-demand (no mcp).
- **§S3 — remove the AUR install route from README** (the `### Arch Linux (AUR)` section + the
  AUR PEP-668 asides + the Roadmap AUR line). The legit **non-AUR** `pacman`-bootstrap-for-uv/pipx
  hints (`sudo pacman -S uv` / `python-pipx`) are **relocated into the install guide** (§S2), not
  left orphaned in README (gap-analysis DRIFT-3). `packaging/aur/PKGBUILD` + RELEASING AUR steps
  stay untouched (for when AUR ships).
- **§S4 — uninstall matrix:** per route (`uv tool uninstall` / `pipx uninstall` / `pip uninstall`
  +orphans caveat / `install.sh --uninstall [--purge]` / Pi extension removal) + the two manual
  steps every route shares (remove the **data store**; `claude mcp remove sandesh`).

## Acceptance criteria
- [x] **AC1 — README slimmed.** README no longer contains the multi-route install command blocks
      (only a pointer/link to the install guide); retains what/why/model.
- [x] **AC2 — install guide exists** with per-route × per-surface sections (Claude/Pi/both) each
      covering install→init→manage→uninstall.
- [x] **AC3 — AUR install route removed from README** (no `yay`/`paru`/`AUR` tokens, and no AUR
      install section, in README; any surviving `pacman` mention is only a non-AUR uv/pipx-bootstrap
      hint IF kept — preferred: relocated to the install guide). `packaging/aur/PKGBUILD` +
      RELEASING.md unchanged; `test_pkgbuild.py` stays green.
- [x] **AC6 — mcp-name marker preserved.** README still contains
      `mcp-name: io.github.anthill-tec/sandesh` (`test_server_json.py` stays green).
- [x] **AC4 — uninstall matrix present** (all routes + Pi + the data-store + `claude mcp remove`
      manual steps).
- [x] **AC5 — boundary test green.** `test_migration_docs_boundary.py` still passes (migration
      documented in RELEASING/CLAUDE + the new install guide; engine boundary intact).

## Estimated size
Small-medium — docs only (README split, new install guide, uninstall matrix); doc-marker tests.

## Non-goals
- Behaviour changes (all in CR-SAN-035..038); re-adding AUR to the README before it's published.
