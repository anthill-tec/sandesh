# CR-SAN-039 — docs restructure: slim README + per-route/surface install guide + uninstall matrix

**Status:** PENDING
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
- **§S2 — install guide** (`docs/INSTALL.md` or `docs/install/`): per **route × surface**
  (Claude / Pi / both), each with **install → `sandesh init` → manage (auto-migrate, admin) →
  uninstall**. `[mcp]` only on the Claude path; Pi path uses uvx-on-demand (no mcp).
- **§S3 — remove AUR from README** (the PKGBUILD + RELEASING steps stay for when it ships).
- **§S4 — uninstall matrix:** per route (`uv tool uninstall` / `pipx uninstall` / `pip uninstall`
  +orphans caveat / `install.sh --uninstall [--purge]` / Pi extension removal) + the two manual
  steps every route shares (remove the **data store**; `claude mcp remove sandesh`).

## Acceptance criteria
- [ ] **AC1 — README slimmed.** README no longer contains the multi-route install command blocks
      (only a pointer/link to the install guide); retains what/why/model.
- [ ] **AC2 — install guide exists** with per-route × per-surface sections (Claude/Pi/both) each
      covering install→init→manage→uninstall.
- [ ] **AC3 — AUR removed from README** (no `yay`/`pacman`/AUR tokens in README; `packaging/aur/` +
      RELEASING.md unchanged).
- [ ] **AC4 — uninstall matrix present** (all routes + Pi + the data-store + `claude mcp remove`
      manual steps).
- [ ] **AC5 — boundary test green.** `test_migration_docs_boundary.py` still passes (migration
      documented in RELEASING/CLAUDE + the new install guide; engine boundary intact).

## Estimated size
Small-medium — docs only (README split, new install guide, uninstall matrix); doc-marker tests.

## Non-goals
- Behaviour changes (all in CR-SAN-035..038); re-adding AUR to the README before it's published.
