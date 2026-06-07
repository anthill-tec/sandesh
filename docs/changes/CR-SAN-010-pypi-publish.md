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
- **`publish-pypi` job** (release path only): `needs: build`; `permissions: id-token: write`;
  `environment: pypi`; downloads the `dist/` artifact; runs
  **`pypa/gh-action-pypi-publish@release/v1`** (no `password`/token — OIDC, default PyPI). Attestations
  upload by default. Guard so it runs **only** on the `release: published` event (job-level `if`).
- **`publish-testpypi` job** (dry-run path only): `needs: build`; `permissions: id-token: write`;
  `environment: testpypi`; same artifact; `pypa/gh-action-pypi-publish@release/v1` with
  **`repository-url: https://test.pypi.org/legacy/`**. Guard to run **only** on `workflow_dispatch`
  (`if: github.event_name == 'workflow_dispatch'`) — a rehearsal of the full publish before the real
  `v0.1.0`. (Gap-analysis decision: TestPyPI dry-run included.)
- No PyPI API token, no `password:` input, no secret anywhere — trusted publishing only.

### §S2 — Build is CI-verified without publishing (+ TestPyPI dry-run)
The **build + `twine check`** path must run on pull requests / pushes to `develop` (and on
`release` + `workflow_dispatch`), so packaging breakage is caught in CI **without** publishing. The
publish jobs are each gated by event: `publish-pypi` only on `release: published`,
`publish-testpypi` only on `workflow_dispatch`. One workflow file, one `build` job feeding both
publish jobs (decision: keep it a single `publish-pypi.yml`). The **TestPyPI** path lets a
maintainer rehearse the entire OIDC publish end-to-end before the first real release.

### §S3 — Maintainer prerequisites (manual; cannot be automated by this CR)
Document precisely (in §S4's RELEASING.md) — these are one-time owner actions on PyPI/GitHub:
1. **Configure a *pending* Trusted Publisher on PyPI** (PyPI → *Publishing* → add a GitHub
   publisher for a project that doesn't exist yet): **PyPI project name `sandesh-relay`**, owner
   **`anthill-tec`**, repository **`sandesh`**, workflow filename **`publish-pypi.yml`**,
   environment name **`pypi`**. The first successful publish converts the pending publisher to
   active and creates the project — **no manual project registration needed**.
2. **Configure a *pending* Trusted Publisher on TestPyPI** (test.pypi.org, same flow): project
   `sandesh-relay`, owner `anthill-tec`, repo `sandesh`, workflow `publish-pypi.yml`, environment
   **`testpypi`** — for the `workflow_dispatch` dry-run (gap-analysis decision).
3. **Create the GitHub Environments** in repo *Settings → Environments*:
   - **`pypi`** — **protected with a required reviewer** (the owner approves each release publish;
     the `publish-pypi` job pauses for approval before upload). (Gap-analysis decision.)
   - **`testpypi`** — unprotected (it's a rehearsal target).

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
- [ ] **AC2** — the `publish-pypi` job declares `permissions: id-token: write`, `environment: pypi`,
      uses `pypa/gh-action-pypi-publish@release/v1`, and contains **no** API-token/`password` input
      and no PyPI secret reference (OIDC-only) — asserted by parsing the workflow.
- [ ] **AC2b** — a `publish-testpypi` job exists, gated to `workflow_dispatch`, with
      `environment: testpypi`, `permissions: id-token: write`, and the pypa action configured with
      `repository-url: https://test.pypi.org/legacy/` — asserted by parsing the workflow.
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
      publishers for **both** PyPI (env `pypi`) and TestPyPI (env `testpypi`) with the exact fields
      (project `sandesh-relay`, owner `anthill-tec`, repo `sandesh`, workflow `publish-pypi.yml`),
      creating both GitHub environments, and **protecting `pypi` with a required reviewer** — and
      (b) the release→publish flow incl. tag-driven version and the TestPyPI dry-run via
      `workflow_dispatch`; README's PyPI install lines are live + point to RELEASING.md.
- [ ] **AC7** — the workflow passes a lint/dry check: `actionlint` (or `yamllint`) clean, or
      `act -n` dry-run parses it without error (whichever is available; record which).

## Gap-analysis findings (2026-06-07) — verdict READY

- **Dim 1 (Spec vs PRD):** spec covers PRD §5/D7 in full (publish `sandesh-relay` on
  `release: published` via OIDC; pending publisher; `.github/workflows/`). No gap.
- **Dim 2 (Spec vs Code):** `pyproject.toml` = hatchling + hatch-vcs (`source="vcs"`); verified
  `python -m build` yields a valid **sdist + wheel**, both carrying the tag-derived version
  (`0.1.devN+g<sha>` pre-tag) and **both containing `sandesh/data/usage-scenarios.md`** (the sdist
  inclusion was unverified by CR-008 — now confirmed; de-risks CR-009 which builds from the sdist).
  `fetch-depth: 0` requirement is correct. No `.github/` exists (clean slate). No drift.
- **Dim 3 (Code vs PRD):** PyPI version == git tag `X.Y.Z` == release semver, by construction
  (hatch-vcs). No conflict.
- **Decisions (gap-analysis):** publish tool = **`pypa/gh-action-pypi-publish@release/v1`** +
  `python -m build`; **TestPyPI dry-run** via `workflow_dispatch` (env `testpypi`); **`pypi`
  environment protected with a required reviewer**.
- **Verified current (web, 2026-06):** pending publishers (no pre-registration), `id-token: write`,
  PEP 740 **attestations on by default** (action ≥ v1.11) for trusted-publishing flows.

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
