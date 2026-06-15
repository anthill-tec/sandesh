# CR-SAN-042 — `release.sh set-version` + `finish` manifest-version guard (Option A)

**Status:** PENDING
**Priority:** Medium (prevents the manual-manifest version drift that reddened 0.3.1's npm
build-check and forced the 0.3.2 hotfix)
**Depends on:** — (builds on CR-SAN-034 `scripts/release.sh`)
**Labels:** release-tooling, dx, automation
**Wave:** post-0.3.2
**Design reference:** `scripts/release.sh` (CR-SAN-034 — `checkpoint`/`finish`/`status`, branch-gated,
`require_release_branch`, `--dry-run`); the manual manifests `integrations/pi/package.json` +
repo-root `server.json`; the gates that enforce parity — `integrations/pi/src/version_gate.test.ts`
(AC5 release-aware) + `integrations/pi/src/version_sync.test.ts` (AC4 package.json ↔ server.json);
the existing script test harness `tests/test_release_script.py::ReleaseScriptHarness`.

## Context
PyPI/Python versions are derived from the git tag by `hatch-vcs` — no file edit. But two **manual**
manifests carry hardcoded version strings that the release-aware gates require to equal the release
tag: `integrations/pi/package.json` (`version`) and repo-root `server.json` (`version` +
`packages[*].version`). Forgetting to bump them is invisible until CI: in 0.3.1 the real PyPI publish
succeeded while the npm `build-check` went RED, forcing the 0.3.2 version-sync hotfix. Automate the
bump (single command) and make a forgotten bump impossible to *finish* (a preflight guard), so the
failure can no longer reach a cut tag.

## Scope
- **§S1 — `release.sh set-version <X.Y.Z>` subcommand.** Branch-gated (hotfix/* or release/* only,
  via `require_release_branch`). Validates `<X.Y.Z>` against `^[0-9]+\.[0-9]+\.[0-9]+$`. Rewrites the
  **manual manifests' version strings to `X.Y.Z`**, preserving file formatting (only the version
  *values* change — no whole-file re-serialization that would reorder/reindent keys):
  - `integrations/pi/package.json` — the top-level `"version"` field.
  - `server.json` — the top-level `"version"` field **and** every `packages[*].version` field.
  Implementation may shell out to `python3` (stdlib `json`/`re`; guaranteed present in the release
  context) for a targeted, format-preserving substitution of the `"version":` key lines. Manifest
  paths MUST be anchored to the repo root (`git rev-parse --show-toplevel`), not the cwd, so the
  script is correct when invoked by absolute path. After
  writing, `git add` the changed manifests and commit them with a clear message (e.g.
  `chore(release): set manual manifests to X.Y.Z`). `--dry-run`: print the planned per-file changes
  to stdout, write/stage/commit **nothing**, exit 0.
- **§S2 — `finish <X.Y.Z>` manifest-version guard (preflight).** Before the git-flow finish (and
  *before* the `--dry-run` early-return, so dry-run is a true preflight), assert that for **each
  manual manifest that EXISTS**, its version equals `<X.Y.Z>`. A manifest that is ABSENT is skipped
  (NOT an error) — this preserves the existing manifest-less harness tests. On any mismatch: exit 1,
  print to stderr the offending file + found-vs-expected, and a remediation hint
  (`run: scripts/release.sh set-version <X.Y.Z>`); git-flow finish and `git push` are NOT run. When
  all existing manifests match (or none exist), `finish` proceeds exactly as today.
- **§S3 — help + docs.** Add `set-version <X.Y.Z>` to `usage()`. Document the new step + the guard in
  `RELEASING.md` (run `set-version` on the release/hotfix branch before `finish`; the guard refuses a
  forgotten bump). PyPI/Python version stays tag-derived (state this — set-version does NOT touch
  `pyproject.toml`).

## Acceptance criteria
- [ ] **AC1 — set-version writes package.json.** `set-version 0.5.7` on `hotfix/0.5.7` (or
      `release/0.5.7`) sets `integrations/pi/package.json` top-level `"version"` to `"0.5.7"`
      (file remains valid JSON; other keys unchanged).
- [ ] **AC2 — set-version writes server.json (both/all fields).** Same invocation sets repo-root
      `server.json` top-level `"version"` AND `packages[0].version` to `"0.5.7"` (valid JSON;
      non-version keys unchanged).
- [ ] **AC3 — set-version commits; --dry-run does not.** Live `set-version 0.5.7` leaves a clean
      working tree with a new HEAD commit that modifies the manifests. `set-version 0.5.7 --dry-run`
      exits 0, prints the planned change, and leaves the manifests **and** git state unchanged (no
      commit, no staged changes).
- [ ] **AC4 — set-version gating + validation.** `set-version 0.5.7` on `develop`/`main`/`feature/x`
      exits 2 with a branch error and leaves manifests untouched; a malformed version
      (`set-version 1.2`, `set-version v1.2.3`, `set-version 1.2.3.4`) exits 2 and writes nothing.
- [ ] **AC5 — finish guard rejects a mismatch.** With `integrations/pi/package.json` at `0.0.1` on
      `hotfix/0.5.7`, `finish 0.5.7` exits 1, stderr names `package.json` with found `0.0.1` vs
      expected `0.5.7`; and even `finish 0.5.7 --dry-run` exits 1 — neither git-flow finish nor
      `git push` runs (branch/main unchanged, `gh` stub not invoked).
- [ ] **AC6 — finish guard is non-breaking.** `finish 0.5.7 --dry-run` in a repo whose manifests
      **match** `0.5.7` exits 0 and prints the git-flow + push commands; and the existing
      manifest-LESS harness behaviour is preserved (absent manifests are skipped) — all current
      `tests/test_release_script.py` cases stay green.
- [ ] **AC7 — help + docs + guards.** `release.sh --help` stdout lists `set-version`; `RELEASING.md`
      documents `set-version` and the finish guard; `tests/test_release_script.py` (extended) and
      `tests/test_releasing_doc.py` are green; no schema/code outside `scripts/release.sh` +
      `RELEASING.md` is touched.

## Estimated size
Small-medium — one new bash subcommand + a preflight guard in `cmd_finish`, a `usage()` line, a
RELEASING.md section, and new cases on the existing `ReleaseScriptHarness` (plus manifest fixtures).

## Risks / open questions
- **Format-preserving JSON edit in bash.** Resolved: shell out to `python3` for a targeted
  `"version":`-key substitution (no `json.dump` reserialize). GREEN must keep the manifests
  byte-identical except the version values.
- **Blast radius on the existing 40 release-script tests.** The harness builds a repo with NO
  manifests; the guard MUST skip absent files (baked into §S2/AC6).

## Non-goals
- Touching `pyproject.toml` / the Python version (hatch-vcs owns it via the tag).
- Auto-running the bun gate suites inside `set-version` (couples release.sh to bun; the §S2 guard +
  CI gates already enforce parity). 
- Changing `checkpoint`/`status`, the publish workflows, or any product code.
