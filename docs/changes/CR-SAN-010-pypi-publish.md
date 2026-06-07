# CR-SAN-010 — PyPI release via OIDC trusted publishing (`sandesh-relay`)

**Status:** PENDING
**Priority:** High
**Depends on:** CR-SAN-008 (the package + `pyproject.toml` + hatch-vcs versioning)
**Labels:** phase-3, distribution, ci, pypi
**Phase:** Phase 3
**Design reference:** docs/research/PRD-distribution.md §3 D7, §5 (CR-SAN-010 row)

## Context

Publishing `sandesh-relay` to **PyPI** is what makes `uv tool install` / `uvx --from` /
`pipx install` / `pipxu install` able to fetch Sandesh (D7 — PyPI is the global index those
clients pull from; there is no separate uv registry). This CR adds a GitHub Actions workflow that
builds the sdist+wheel and publishes them to PyPI on a GitHub **Release**, using **OIDC trusted
publishing** (no API token stored anywhere).

Versioning is already git-tag-driven (CR-SAN-008, hatch-vcs): the release is cut on a `vX.Y.Z`
tag, CI builds **from that tagged ref**, so the published artifact's PEP 440 version is exactly
`X.Y.Z`. The first publish is `v0.1.0`, cut at the eventual git-flow **release to `main`**.

Verified against current PyPI/PyPA guidance (2026-06): use **pending trusted publishers** (the
publisher is configured *before* the project exists; the first publish creates `sandesh-relay`),
`pypa/gh-action-pypi-publish@release/v1`, the `id-token: write` permission, and a GitHub
**environment** (`pypi`) referenced by the publisher. PEP 740 **attestations are generated and
uploaded by default** (action ≥ v1.11) for trusted-publishing flows — no extra config.

Out of scope: AUR PKGBUILD (CR-SAN-009), the MCP Registry listing (CR-SAN-011), any code/tool
change.

## Scope

### §S1 — The publish workflow (`.github/workflows/publish-pypi.yml`)
- **Trigger:** `on: release: types: [published]` (a published GitHub Release, which points at the
  `vX.Y.Z` tag). Plus `workflow_dispatch` for a manual/dry path (see §S2 decision).
- **`build` job** (runs for the build path; see §S2): checks out the repo **with full history +
  tags** (`fetch-depth: 0` — hatch-vcs needs the tag to compute the version), sets up Python,
  installs the build frontend, runs `python -m build` to produce `dist/*.whl` + `dist/*.tar.gz`,
  runs **`twine check dist/*`**, and uploads `dist/` as a workflow artifact.
- **`publish` job** (release path only): `needs: build`; `permissions: id-token: write`;
  `environment: pypi`; downloads the `dist/` artifact; runs
  **`pypa/gh-action-pypi-publish@release/v1`** (no `password`/token — OIDC). Attestations upload by
  default. Guard so it runs **only** on the `release: published` event (e.g. job-level `if`).
- No PyPI API token, no `password:` input, no secret — trusted publishing only.

### §S2 — Build is CI-verified without publishing
The **build + `twine check`** path must run on pull requests / pushes to `develop` (and the
release), so packaging breakage is caught in CI **without** publishing. Only the `publish` job is
gated to the `release: published` event. (Mechanism: one workflow with a build job on
`pull_request`/`push` + `release`, and a publish job `if: github.event_name == 'release'`; or a
small `build-check` job in the existing/another workflow — CR decides, keep it one file if clean.)

### §S3 — Maintainer prerequisites (manual; cannot be automated by this CR)
Document precisely (in §S4's RELEASING.md) — these are one-time owner actions on PyPI/GitHub:
1. **Configure a *pending* Trusted Publisher on PyPI** (PyPI → *Publishing* → add a GitHub
   publisher for a project that doesn't exist yet): **PyPI project name `sandesh-relay`**, owner
   **`anthill-tec`**, repository **`sandesh`**, workflow filename **`publish-pypi.yml`**,
   environment name **`pypi`**. The first successful publish converts the pending publisher to
   active and creates the project — **no manual project registration needed**.
2. **Create the GitHub Environment `pypi`** in repo *Settings → Environments* (optionally add
   protection: required reviewer / restrict to tags) — the publish job references it.
3. (Optional safety) configure the same as a **TestPyPI** pending publisher if the TestPyPI
   dry-run path (§S2 decision) is adopted.

### §S4 — Docs
- **`RELEASING.md`** (repo root or `docs/`): the end-to-end release→publish flow — git-flow
  release to `main`, bump is automatic (tag-driven), `git tag vX.Y.Z`, push, create a GitHub
  **Release** for that tag → the workflow builds + publishes → verify on PyPI. Plus the §S3
  maintainer prerequisites and a note that the version comes from the tag (hatch-vcs).
- **README:** make the PyPI install lines live ("once published" → published): `uv tool install
  'sandesh-relay[mcp]'`, `pipx install 'sandesh-relay[mcp]'`, `uvx --from 'sandesh-relay[mcp]'
  sandesh-mcp`. Add a short "Releasing" pointer to RELEASING.md.

### §S5 — Version provenance (tie to hatch-vcs)
The workflow must build from the tagged commit with tags available (`fetch-depth: 0`) so the
artifact version == the release tag's `X.Y.Z`. (A build from an untagged ref would yield a
`devN+g<sha>` version — unacceptable for a real release.)

## Acceptance criteria

- [ ] **AC1** — `.github/workflows/publish-pypi.yml` exists, is valid YAML, and triggers on
      `release` with `types: [published]` (asserted by parsing the workflow).
- [ ] **AC2** — the publish job declares `permissions: id-token: write`, `environment: pypi`, uses
      `pypa/gh-action-pypi-publish@release/v1`, and contains **no** API-token/`password` input and
      no PyPI secret reference (OIDC-only) — asserted by parsing the workflow.
- [ ] **AC3** — a build step runs `python -m build` and `twine check`; locally, building the repo
      produces a `*.whl` + `*.tar.gz` that `twine check` reports as **PASSED** (asserted by a build
      validation test).
- [ ] **AC4** — the **build** path runs on `pull_request`/`push` (not only release) while the
      **publish** job is gated to the `release: published` event (asserted by parsing the workflow:
      build triggers include non-release events; publish job has the release `if`/separation).
- [ ] **AC5** — the build job checks out with `fetch-depth: 0` (tags present) so the artifact
      version derives from the git tag (hatch-vcs); a build from a `vX.Y.Z` ref yields bare
      `X.Y.Z` (asserted by parsing the workflow for `fetch-depth: 0`; version-from-tag verified by
      the build test where feasible).
- [ ] **AC6** — `RELEASING.md` documents (a) the maintainer prerequisites — the *pending* trusted
      publisher fields (project `sandesh-relay`, owner `anthill-tec`, repo `sandesh`, workflow
      `publish-pypi.yml`, env `pypi`) and creating the GitHub `pypi` environment — and (b) the
      release→publish flow incl. tag-driven version; README's PyPI install lines are live + point
      to RELEASING.md.
- [ ] **AC7** — the workflow passes a lint/dry check: `actionlint` (or `yamllint`) clean, or
      `act -n` dry-run parses it without error (whichever is available; record which).

## Estimated size
Small–medium: one workflow file + a workflow-contract test + a build/twine-check validation test +
`RELEASING.md` + README edits. The real gating effort is the **maintainer prereqs** (manual) and
not being able to do a real publish from a feature branch.

## Risks / open questions
- **No real publish in CI from a feature branch** — we validate the build path + the workflow
  structure; the *actual* publish is exercised only at the first real release. Mitigate with the
  build+twine-check job and (optionally) a TestPyPI dry-run (§S2 decision).
- **Decisions to settle in gap-analysis:** (1) publish tool — `pypa/gh-action-pypi-publish`
  (recommended; PRD-first; attestations default) vs `uv build`+`uv publish`; (2) include a
  **TestPyPI** dry-run path (`workflow_dispatch`/pre-release) or PyPI-only; (3) `pypi` environment
  protection (required reviewer) yes/no.
- **First-publish ordering** — the pending publisher + the first `v0.1.0` GitHub Release must both
  exist; the release is cut on `main` via git-flow (a separate, later action — this CR ships the
  workflow + docs, not the release itself).

## Non-goals
- Cutting the actual `v0.1.0` release / running the first publish (a maintainer action, done at
  the git-flow release to `main`).
- AUR PKGBUILD (CR-SAN-009), MCP Registry (CR-SAN-011).
- Any change to the package code, MCP tools, or messaging semantics.
