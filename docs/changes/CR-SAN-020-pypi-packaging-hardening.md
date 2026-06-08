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
`license-files = ["LICENSE"]` **at the `[project]` table level** (PEP 639 — confirmed at gap-analysis:
hatchling docs `docs/config/metadata.md:119` place `license-files` under `[project]`, *not*
`[tool.hatch.build]`; the glob-array form is honoured since hatchling 1.26.0). The repo-root `LICENSE`
exists (verified). This makes the license file unambiguously included in the wheel/sdist METADATA
rather than relying on the backend's default glob.

### §S2 — pin build-system requirements (lower bounds)
`requires = ["hatchling", "hatch-vcs"]` is unpinned. Add **lower-bound version constraints** for build
reproducibility. **Floors resolved at gap-analysis (2026-06-08):** `hatchling>=1.27` and
`hatch-vcs>=0.4`.
- `hatchling>=1.27` is not arbitrary — it is the **PEP 639 floor**: `license-files` as a glob array
  landed in hatchling 1.26.0 and core-metadata 2.4 (which actually writes `License-Expression` +
  `License-File`) became the default in **1.27.0**. This is the version that makes *both* the existing
  SPDX `license = "GPL-3.0-only"` string and the new `§S1 license-files` emit correct METADATA. (Also
  mirrors the project's existing `mcp>=1.27,<2` lower-bound style.)
- `hatch-vcs>=0.4` is a conservative floor (current = 0.5.0; 0.4.0 is the modern Python-3.12-era line).

### §S3 — granular Python version classifiers
`classifiers` has only `Programming Language :: Python :: 3`. With `requires-python = ">=3.10"`, add the
granular trove classifiers for each supported minor (`3.10`, `3.11`, `3.12`, `3.13`) so PyPI's
version filtering reflects the real support matrix. (Keep them consistent with `requires-python`.)

## Acceptance criteria

- [ ] **AC1** — `pyproject.toml` `[project]` declares `license-files = ["LICENSE"]` and the referenced
      `LICENSE` exists at the repo root (asserted by parsing `pyproject.toml` + file existence).
- [ ] **AC2** — every entry in `[build-system] requires` carries a version constraint (no bare
      `"hatchling"`/`"hatch-vcs"`); the constraints are valid PEP 440 specifiers, and the lower bounds
      are at least `hatchling>=1.27` and `hatch-vcs>=0.4` (the PEP 639 floor — see §S2) (asserted by
      parsing). The existing `test_pyproject.py` hatchling/hatch-vcs assertions use substring matching,
      so they stay green under the pinned form — the new floor checks are additive.
- [ ] **AC3** — `classifiers` includes `Programming Language :: Python :: 3.10`, `…3.11`, `…3.12`,
      `…3.13`, consistent with `requires-python = ">=3.10"` (asserted by parsing).
- [ ] **AC4** — the build still succeeds and `twine check` passes on the produced sdist+wheel; the
      `sandesh` import package and CLI/MCP entry points are unchanged (no functional change).
- [ ] **AC5** — **no `py.typed`** marker is added (audit P3 rejected — see Non-goals); the existing test
      suites stay green.

## Gap-analysis findings
_Completed 2026-06-08 (orchestrator). Verdict: **READY** — no drift in any of the three dimensions; the
only changes were to record the decisions this CR explicitly deferred (the §S2 floors + §S1 PEP 639
placement, now folded into Scope/ACs above)._

- **Dimension 1 (Spec vs PRD):** consistent. PRD-distribution names hatchling + hatch-vcs as the build
  backend (D1, §5 CR-SAN-008 row); these are pure metadata-polish additions on top, none contradicting
  the PRD. The PRD does not require `py.typed` — its rejection (Non-goals) is correct.
- **Dimension 2 (Spec vs Code):** all three current-state claims verified against `pyproject.toml` —
  `license = "GPL-3.0-only"` present with **no** `license-files` (line 11); `requires = ["hatchling",
  "hatch-vcs"]` unpinned (line 2); `classifiers` has only `Programming Language :: Python :: 3` (line 16)
  with `requires-python = ">=3.10"` (line 10). The named test file `tests/test_pyproject.py` exists and
  is the right place — its hatchling/hatch-vcs checks use **substring** matching, so pinning keeps them
  green (the new floor/license-files/classifier checks are additive).
- **Dimension 3 (Code vs PRD):** no architectural concern (metadata only). hatch-vcs versioning is
  tag-driven; **no git tags exist yet** → version resolves to `0+unknown` (consistent with the
  established versioning model). The build still succeeds at `0+unknown`, and `twine check` passes
  because it is a valid PEP 440 local version. AC4's build + `twine check` will need `build`/`twine`
  available in the crucible venv (implementation note, not a spec defect).
- **Resolved decisions (now in the spec):** §S2 floors = `hatchling>=1.27` (PEP 639 / core-metadata-2.4
  floor) + `hatch-vcs>=0.4`; §S1 placement = `[project].license-files` (verified via hatchling docs).
- **Environment verified:** installed = hatchling 1.30.1 / hatch-vcs 0.5.0; both ≥ the chosen floors,
  so the dev/crucible build resolves cleanly.

## Estimated size
Very small: three `pyproject.toml` metadata additions + extending the existing `tests/test_pyproject.py`
assertions. No source changes.

## Risks / open questions
- ~~**hatchling PEP 639 support**~~ — **RESOLVED at gap-analysis:** `license-files` belongs at the
  `[project]` level; honoured since hatchling 1.26.0 (glob array), default core-metadata 2.4 since 1.27.0
  → floor `hatchling>=1.27` guarantees it. No placement ambiguity remains.
- **classifier/`requires-python` drift** — if `requires-python` changes later, the granular classifiers
  must track it (the test enforces consistency).

## Non-goals (rejected audit items — verified)
- **`py.typed` marker (audit P3).** REJECTED: `sandesh` is a CLI + MCP **tool**, not a library consumed
  by downstream code for type information (PEP 561). The marker would be misleading and the package is
  not fully annotated. Not added.
- Any change to npm/Pi packaging (that is **CR-SAN-021**), the build backend choice, versioning
  mechanism (stays hatch-vcs tag-driven), or the `[mcp]` extra.
