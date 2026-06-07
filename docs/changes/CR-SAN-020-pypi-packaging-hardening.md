# CR-SAN-020 — PyPI packaging metadata hardening

**Status:** PENDING
**Priority:** Low (metadata polish on an already-shipped, functional packaging config — no correctness defect)
**Depends on:** CR-SAN-008 (the `pyproject.toml` it hardens)
**Labels:** phase-4, packaging, pypi, python
**Phase:** Phase 4 (filed at the end of the Phase-4 queue per direction; the work is Python/PyPI metadata)
**Design reference:** docs/research/PRD-distribution.md (D-build: hatchling + hatch-vcs); packaging audit (2026-06-07)

## Context

A packaging-config audit flagged minor PyPI metadata gaps in `pyproject.toml`. These are **polish, not
defects** — the package builds and publishes correctly today (CR-SAN-008/010). Three accepted items are
fixed here; two audit items are rejected (recorded under Non-goals with citations).

## Scope

### §S1 — explicit `license-files`
`[project]` declares the SPDX `license = "GPL-3.0-only"` but no explicit **`license-files`**. Add
`license-files = ["LICENSE"]` (the repo-root `LICENSE` exists) so the license file is unambiguously
included in the wheel/sdist METADATA under PEP 639 (rather than relying on the backend's default glob).

### §S2 — pin build-system requirements (lower bounds)
`requires = ["hatchling", "hatch-vcs"]` is unpinned. Add **lower-bound version constraints** (e.g.
`hatchling>=X`, `hatch-vcs>=Y`) for build reproducibility. Exact floors decided at gap-analysis against
the currently-resolved versions (don't assume — read what's installed / current).

### §S3 — granular Python version classifiers
`classifiers` has only `Programming Language :: Python :: 3`. With `requires-python = ">=3.10"`, add the
granular trove classifiers for each supported minor (`3.10`, `3.11`, `3.12`, `3.13`) so PyPI's
version filtering reflects the real support matrix. (Keep them consistent with `requires-python`.)

## Acceptance criteria

- [ ] **AC1** — `pyproject.toml` `[project]` declares `license-files = ["LICENSE"]` and the referenced
      `LICENSE` exists at the repo root (asserted by parsing `pyproject.toml` + file existence).
- [ ] **AC2** — every entry in `[build-system] requires` carries a version constraint (no bare
      `"hatchling"`/`"hatch-vcs"`); the constraints are valid PEP 440 specifiers (asserted by parsing).
- [ ] **AC3** — `classifiers` includes `Programming Language :: Python :: 3.10`, `…3.11`, `…3.12`,
      `…3.13`, consistent with `requires-python = ">=3.10"` (asserted by parsing).
- [ ] **AC4** — the build still succeeds and `twine check` passes on the produced sdist+wheel; the
      `sandesh` import package and CLI/MCP entry points are unchanged (no functional change).
- [ ] **AC5** — **no `py.typed`** marker is added (audit P3 rejected — see Non-goals); the existing test
      suites stay green.

## Gap-analysis findings
_To be completed by `/gap-analysis CR-SAN-020` before the feature branch — confirm the current
hatchling/hatch-vcs versions to choose sensible lower bounds; confirm `tests/test_pyproject.py` is the
right place to assert the new metadata; confirm hatchling honours `license-files` at the `[project]`
level for the installed version (PEP 639 support)._

## Estimated size
Very small: three `pyproject.toml` metadata additions + extending the existing `tests/test_pyproject.py`
assertions. No source changes.

## Risks / open questions
- **hatchling PEP 639 support** — verify the resolved hatchling version reads `license-files` at
  `[project]` (vs `[tool.hatch.build]`); adjust placement if needed.
- **classifier/`requires-python` drift** — if `requires-python` changes later, the granular classifiers
  must track it (the test enforces consistency).

## Non-goals (rejected audit items — verified)
- **`py.typed` marker (audit P3).** REJECTED: `sandesh` is a CLI + MCP **tool**, not a library consumed
  by downstream code for type information (PEP 561). The marker would be misleading and the package is
  not fully annotated. Not added.
- Any change to npm/Pi packaging (that is **CR-SAN-021**), the build backend choice, versioning
  mechanism (stays hatch-vcs tag-driven), or the `[mcp]` extra.
