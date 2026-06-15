# CR-SAN-034 — branch-gated release pipeline (push-to-main → Release → PyPI) + `scripts/release.sh`

**Status:** PENDING
**Priority:** High (codifies the publish policy; closes the "publish from anywhere" hole before the first real PyPI release)
**Depends on:** —
**Labels:** release, ci, packaging, tooling
**Wave:** post-v0.2.1 release-engineering
**Design reference:** RELEASING.md (the publish contract — rewritten by this CR); the EntityStore
`ci.yaml` branch-gating pattern (`github.ref == 'refs/heads/master' && github.event_name == 'push'`,
`ci.yaml:919-923`) as the model for branch-scoped CI steps.

## Context

Publishing today is gated only by `release: published` and is never verified against the branch
the tag lives on, so a GitHub Release created from *any* tag would publish to PyPI. The owner's
policy is: **the real PyPI publish happens only as a consequence of a git-flow `release`/`hotfix`
finish landing a `vX.Y.Z` tag on `main`.** Finishing a hotfix/release and pushing `main` is the
"change trigger"; CI on `main` then drives the publish. A TestPyPI checkpoint must be runnable
from the (untagged) `hotfix/*` or `release/*` branch as a pre-finish gate.

Two verified facts shape the design (gap-analysis, 2026-06-15):
1. The untagged version is `0.2.2.dev5+geb197a4db` — the PEP 440 **local** segment (`+g…`) that
   PyPI/TestPyPI **reject on upload**. `[tool.hatch.version]` has `source = "vcs"` with **no**
   `local_scheme` override (default scheme emits the local segment).
2. The `publish-pypi`/`publish-testpypi` jobs do **not** check out a working tree; the `build` job
   checks out `fetch-depth: 0`.

**Chosen trigger model (owner, 2026-06-15): Option 3** — a `push: main` carrying a new `v*` tag
auto-creates a GitHub Release, whose `release: published` event runs the existing publish job.
This keeps the first-class Release object, the clean tag-derived version, and the `pypi`
environment approval gate, all automatic from `finish` + push.

> **Prerequisite (maintainer, one-time) — required by Option 3:** a Release created with the
> default `GITHUB_TOKEN` does **not** re-trigger an `on: release` workflow (GitHub anti-recursion).
> The create-release job must use a non-default token: a **fine-grained PAT** (or GitHub App
> token) with **Contents: write** on `anthill-tec/sandesh`, stored as repo secret
> **`RELEASE_PAT`**. Without it, the Release is created but nothing publishes.

## Scope

### §S1 — `pyproject.toml`: drop the PEP 440 local segment
- `[tool.hatch.version]` gains `raw-options = { local_scheme = "no-local-version" }` (forwarded to
  setuptools_scm). Untagged builds then derive `X.Y.Z.devN` (no `+local`); an exact `vX.Y.Z` tag
  still derives the clean `X.Y.Z`. This makes a TestPyPI checkpoint from an untagged
  `hotfix/*`/`release/*` branch upload-valid with **no rc tag required**.

### §S2 — `publish-pypi.yml`: push-to-main create-release + publish guard
- **Triggers** gain `push: branches: [main]` (keep `develop` for the build check, `pull_request`,
  `release`, `workflow_dispatch`).
- **New `create-release` job** — `needs: build`, `if: github.event_name == 'push' &&
  github.ref == 'refs/heads/main'`:
  - checkout `fetch-depth: 0`; read the version tag at HEAD
    (`git tag --points-at HEAD | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$'`).
  - **No version tag at HEAD ⇒ exit 0 without creating a release** (ordinary main commits are no-ops).
  - **Idempotent:** if a Release for that tag already exists (`gh release view`), exit 0 (no
    duplicate); else `gh release create vX.Y.Z --generate-notes` using
    `env: GH_TOKEN: ${{ secrets.RELEASE_PAT }}` (so the `release: published` event fires).
- **`publish-pypi` job gains a guard step** (before download/publish) — adds `actions/checkout`
  `fetch-depth: 0`, then asserts both: (a) `github.ref` matches `^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$`
  (no rc on the real index); (b) `git fetch --no-tags origin main` then
  `git merge-base --is-ancestor "$GITHUB_SHA" origin/main`. Either failing ⇒ the job fails before
  any upload.
- **`publish-testpypi` job** stays `workflow_dispatch`-only; it gains an early **version-sanity
  step** that fails with a clear message if the derived version still contains a `+` local segment
  (defense-in-depth for §S1).

### §S3 — `publish-npm.yml`: same trigger model + guard (shared Release)
- The auto-created Release fires this workflow's `release` event too. `publish-npm` gains the same
  `v*`-tag-on-`main` guard step (its `build-check`/`publish-npm` already check out `fetch-depth: 0`).
- **Note:** the create-release job lives only in `publish-pypi.yml` (one Release serves both
  workflows; `needs` cannot cross workflow files). The pre-existing `npm pack --dry-run contents
  (AC3)` test failure is **out of scope** here (tracked separately).

### §S4 — `scripts/release.sh` (new; stdlib bash, `set -euo pipefail`, branch-gated)
Adopts the EntityStore companion-script conventions (arg parse, `--dry-run`/`--verbose`, repo
auto-detect, `info`/`error`/`success` helpers, usage block). **Subcommands:**
- `checkpoint` — from a `hotfix/*` or `release/*` branch only (else refuse, exit 2): dispatch the
  TestPyPI workflow on the current branch (`gh workflow run publish-pypi.yml --ref "$BRANCH"`),
  report the run URL. `--dry-run` prints the command without dispatching.
- `finish <X.Y.Z>` — detect `hotfix/*` vs `release/*`, run the matching
  `GIT_MERGE_AUTOEDIT=no git flow {hotfix|release} finish X.Y.Z`, then
  `git push origin main develop --tags`. The main push hands off to CI (create-release → publish).
  Refuse (exit 2) if not on a `hotfix/*`/`release/*` branch or if `<X.Y.Z>` ≠ the branch's version.
- `status` — print current branch, `git describe`, the derived package version, and whether a
  `vX.Y.Z` tag is already on `origin/main` / already on PyPI.
- `-h|--help` / unknown subcommand ⇒ usage to stderr, exit 2.
- The script **never** uploads to a package index itself — it only dispatches CI / runs git-flow;
  publishing stays a CI concern (the branch guard is the single enforcement point, mirrored locally
  by the subcommand branch checks).

### §S5 — RELEASING.md rewrite
- Replace the false "untagged dev version is fine for a TestPyPI rehearsal" note with the §S1
  reality (no-local-version ⇒ `X.Y.Z.devN` is upload-valid).
- Document the Option-3 chain (push `main` → create-release (PAT) → `release: published` →
  `publish-pypi`), the `RELEASE_PAT` prerequisite, the branch guard, and the hotfix/release flow
  via `scripts/release.sh` (`checkpoint` on the branch → `finish` → CI publishes).

## Acceptance criteria

- [ ] **AC1 — no local segment.** With `raw-options = { local_scheme = "no-local-version" }`, the
      version derived on an untagged commit matches `^\d+\.\d+\.\d+\.dev\d+$` (no `+`); on an exact
      `vX.Y.Z` checkout it is exactly `X.Y.Z`. (Test derives both via the version machinery.)
- [ ] **AC2 — create-release gating.** `publish-pypi.yml` parses to: a `create-release` job whose
      `if` is exactly `github.event_name == 'push' && github.ref == 'refs/heads/main'`, that
      checks out `fetch-depth: 0`, only acts when a `^v\d+\.\d+\.\d+$` tag points at HEAD, is
      idempotent on an existing Release, and invokes `gh release create` with
      `GH_TOKEN: ${{ secrets.RELEASE_PAT }}`. Triggers include `push: branches:` containing `main`.
- [ ] **AC3 — publish guard.** The `publish-pypi` job contains a guard step, ordered before the
      `pypa/gh-action-pypi-publish` step, that (a) checks out `fetch-depth: 0`, (b) asserts
      `github.ref` matches `^refs/tags/v\d+\.\d+\.\d+$`, and (c) runs
      `git merge-base --is-ancestor` against `origin/main`. (YAML-structure test + `act` dry-run.)
- [ ] **AC4 — npm guard parity.** `publish-npm.yml`'s `publish-npm` job has the same `v*`-tag guard
      step before `npm publish`; no `create-release` job is duplicated there.
- [ ] **AC5 — testpypi sanity.** `publish-testpypi` stays `workflow_dispatch`-only and has a
      version-sanity step that fails if the version contains `+`.
- [ ] **AC6 — `scripts/release.sh` branch gating.** `checkpoint` and `finish` exit 2 with a
      branch-error message when run on `develop`/`main`/`feature/*`; `--help` exits 2-or-0 with a
      usage block; `--dry-run checkpoint` on a `hotfix/x`/`release/x` branch prints the
      `gh workflow run … --ref <branch>` command and does **not** dispatch. (Tested in temp git
      repos with `gh` stubbed.)
- [ ] **AC7 — `scripts/release.sh status`.** On a temp repo at tag `v9.9.9`, `status` prints
      `9.9.9` as the derived version and the current branch; exit 0.
- [ ] **AC8 — docs.** RELEASING.md no longer contains the string "fine for a TestPyPI rehearsal"
      tied to an untagged dev version, and DOES contain the `RELEASE_PAT` prerequisite, the
      push-main→Release→publish chain, and the `scripts/release.sh` flow (grep markers).
- [ ] **AC9 — `act` validation.** `act push -n` (main ref) reaches the `create-release` job, and
      `act release -n` reaches the guarded `publish-pypi` job, with no YAML/structural error.

## Assets / tooling required (per cycle)

| Asset | Used by | Status / how |
|---|---|---|
| `.venv/bin/python` (+ hatch-vcs/setuptools_scm) | C1 version-derivation test | present (dev venv) |
| `act` ≥ 0.2.89 + Docker | C2/C3 YAML structure, C6 dry-run gate | installed; Docker daemon up |
| `gh` ≥ 2.9 | `scripts/release.sh` (`gh workflow run`, `gh release`) | present — **PATH-stubbed in C4 tests** (no live calls) |
| git-flow (`git flow` config) | `scripts/release.sh finish`; C4 tests | configured (`master=main`, tag `v`) — **finish path tested via `--dry-run` only**; never runs a real finish in tests |
| Crucible (`localhost:3849`, project key) | every cycle's test ingest | `~/.claude/scripts/python-crucible.py` |
| temp git repos | C4/C7 script tests, C1 tag-checkout test | created in-test |
| **`RELEASE_PAT`** repo secret | runtime publish only (Option 3 chain) | **maintainer prereq — NOT needed to implement/test**; tests assert the YAML *references* the secret |

## Estimated size
Medium — one pyproject line, two workflow files (one new job + guard steps), a ~150-line bash
script with focused tests, a RELEASING.md rewrite.

## Risks / open questions
- **`RELEASE_PAT` is a maintainer prerequisite** (can't be automated here). Until the secret
  exists, `create-release` creates the Release but the publish chain won't fire — documented in §S5.
- PAT with Contents:write on a public repo: the create-release job is gated to `push: main`
  (never PRs), so forked-PR access to the secret is not possible; exposure is limited to the
  main-push context. Acceptable; noted in RELEASING.md.
- Idempotency relies on `gh release view` — a partially-created Release (created, but publish
  failed) is re-runnable: re-pushing main re-detects the existing Release, the operator re-runs the
  failed `publish-pypi` from the Actions UI.

## Non-goals
- Fixing the npm `npm pack --dry-run contents (AC3)` test failure (separate).
- A test/coverage badge updater (the EntityStore `update-test-badge.sh` is a *pattern* reference
  only; sandesh has no CI test badge).
- Migrating off the `release: published` manual path — it remains available alongside the auto path.
- Publishing 0.2.x itself (a release act, done after this CR via `scripts/release.sh`).
