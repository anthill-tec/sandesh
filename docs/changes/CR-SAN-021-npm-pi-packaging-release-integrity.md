# CR-SAN-021 — npm/Pi packaging hardening + release-version integrity

**Status:** COMPLETED (2026-06-09)
**Priority:** Medium (the version-sync gate + npm CI are real release-integrity value; the rest is polish)
**Depends on:** CR-SAN-015 (the npm/Pi package being hardened), CR-SAN-011 (server.json — a version-sync target)
**Labels:** wave-4, packaging, pi, npm, ci
**Wave:** Wave 4 (Pi integration)
**Design reference:** docs/research/PRD-pi-extension.md PE5 (distribute via Pi packages); Pi `docs/packages.md` (deps rules); packaging audit (2026-06-07)

## Context

A packaging-config audit flagged the npm/Pi extension distribution as "under-hardened" — no publish CI,
no manual-publish safety gate, and a real **version-drift risk** across the three hand-maintained version
strings. The accepted items are fixed here; two audit items are **rejected** (recorded under Non-goals
with citations — the "move deps to `dependencies`" item is its **third** re-raise and would break Pi
compliance).

**The version-drift surface (the most valuable fix):** three versions must agree at every release —
- the **git tag** `vX.Y.Z` (drives the Python `sandesh-relay` version via hatch-vcs),
- `integrations/pi/package.json` `version` (hand-bumped, CR-SAN-015 §S1),
- `server.json` `version` **and** `packages[0].version` (hand-set, CR-SAN-011) — **two** fields.
Nothing currently enforces they match; all are `0.1.0` today by hand.

## Scope

### §S1 — npm publish CI workflow (`.github/workflows/publish-npm.yml`)
Mirror `publish-pypi.yml`'s shape, scoped to the `integrations/pi/` package:
- **on `release: published`** → build-check (`tsc --noEmit`, `bun test`, `npm pack --dry-run`) then
  `npm publish --access public` from `integrations/pi/` (scoped `@anthill-tec/sandesh-pi`).
- **on `workflow_dispatch`** → a dry-run (`npm publish --dry-run` / `npm pack`) for manual verification.
- **on `pull_request` / `push: develop`** → build-check only (no publish), matching the PyPI workflow.
- **Auth mechanism — RESOLVED at gap-analysis (2026-06-08): npm OIDC trusted publishing.** npm trusted
  publishing with OIDC is **generally available** (GitHub Changelog 2025-07-31), so we mirror the PyPI
  side — **passwordless, no `NPM_TOKEN`**. Requirements baked into the publish job:
  - `permissions: id-token: write` on the publish job + a GitHub **`npm` environment**;
  - **npm CLI ≥ 11.5.1** is required for OIDC — node ≥22.19.0 (our §S3 floor) ships npm 10.x, so the
    publish job **must upgrade npm** (`npm install -g npm@latest`) before `npm publish`;
  - provenance is **auto-generated** under trusted publishing (the `--provenance` flag is no longer
    needed); `publishConfig.access` + `repository` are already present in `package.json` (prereqs met);
  - **one-time maintainer setup** (out of CI, documented in RELEASING.md): configure the Trusted
    Publisher on npmjs.com for `@anthill-tec/sandesh-pi` — org/user `anthill-tec`, repo `sandesh`,
    workflow file `publish-npm.yml`, environment `npm`. (Mirrors the PyPI trusted-publisher prereq.)
  - **Caveat:** trusted publishing supports only cloud-hosted runners (we use `ubuntu-latest` — fine).
- Keep the existing **manual** `npm publish` documented in RELEASING.md as the fallback (CR-SAN-015 chose
  manual-first; this adds the automation it explicitly left as "a documented CI option").
- **CI runtime:** the workflow needs BOTH **bun** (`oven-sh/setup-bun`, for `bun test`/`prepublishOnly`)
  and **node+npm** (`actions/setup-node` with `registry-url`, for `npm pack`/`npm publish`/the npm-upgrade)
  in the relevant jobs.

### §S2 — `prepublishOnly` safety gate
Add `scripts.prepublishOnly` to `integrations/pi/package.json` running **`tsc --noEmit && bun test`** so a
manual or CI `npm publish` aborts on a type error or failing test.
- **Gap-analysis prerequisite (DRIFT-1): add `typescript` to `devDependencies`.** Verified at
  gap-analysis: `typescript` is **not** a declared dev dependency and `tsc` is **not** resolvable in
  `node_modules/.bin` — so `tsc --noEmit` (this gate *and* the existing `scripts.typecheck`) would fail
  under a clean `npm ci`. This CR must add `typescript` (e.g. `"typescript": "^5"`) to
  `integrations/pi/package.json` `devDependencies` so the gate (and AC6's `tsc --noEmit`) actually run.

### §S3 — `engines` floor
Add an `engines` field to `integrations/pi/package.json`. **Floor resolved at gap-analysis:**
`"engines": { "node": ">=22.19.0" }` — matching **Pi's own** declared floor (verified: every Pi package —
`pi-coding-agent`, `pi-ai`, `pi-agent-core`, `pi-tui`, and the monorepo root — declares
`"node": ">=22.19.0"`). Advisory metadata; low-risk. (No separate `bun` floor — Pi declares only node.)

### §S4 — version-sync gate (the release-integrity fix) — **two arms** (resolved at gap-analysis)
The audit framed this as one 4-way comparison, but **there are zero git tags today** (`git describe
--tags` errors) and the three JSON fields are all `0.1.0` with **no matching tag** — an always-on tag
comparison would fail on `develop`. So the gate splits into two arms:

- **Arm A — JSON internal consistency (always-on, a `bun test`).** Assert
  `integrations/pi/package.json.version` **==** `server.json.version` **==**
  `server.json.packages[0].version`. No git needed → deterministic in the suite; passes today (all
  `0.1.0`); **fails** if any of the three drifts. Placement: a `bun test` in `integrations/pi/`
  (new `src/version_sync.test.ts` or extend `src/package.test.ts`) reading `server.json` via the
  relative path `../../server.json`.
- **Arm B — tag agreement (release-only, a CI step in `publish-npm.yml`).** On a `release` event,
  assert all three JSON versions **==** the release tag (`vX.Y.Z` → `X.Y.Z`, the value hatch-vcs derives
  for Python). Resolve the expected version from the release ref (`github.ref_name`), **not**
  `git describe` (which is empty pre-tag). Skipped on non-release events (no reachable tag).

This split is the guard against the drift the audit verdict flags for both npm and the MCP registry,
without breaking the pre-first-tag `develop` state.

## Acceptance criteria

- [x] **AC1** — `.github/workflows/publish-npm.yml` exists and: publishes `integrations/pi/` to npm
      (scoped, public) on `release: published`; runs a dry-run on `workflow_dispatch`; runs build-check
      only (no publish) on `pull_request`/`push: develop`; the publish job uses a GitHub `environment`
      (`npm`) with `permissions: id-token: write` and **OIDC trusted publishing** (no `NPM_TOKEN`),
      including an `npm install -g npm@latest` upgrade step (npm ≥ 11.5.1 for OIDC) before publish; sets
      up **both** bun and node; runs from `integrations/pi/` (asserted by parsing the workflow YAML:
      triggers, `working-directory: integrations/pi`, `id-token: write`, the npm-upgrade + publish steps,
      the build-check steps).
- [x] **AC2** — `integrations/pi/package.json` has `scripts.prepublishOnly` running
      `tsc --noEmit && bun test`, **and** declares `typescript` in `devDependencies` so `tsc` resolves
      under `npm ci` (asserted by parsing package.json).
- [x] **AC3** — `integrations/pi/package.json` declares `engines.node` `">=22.19.0"` (matching Pi's own
      floor) (asserted by parsing package.json).
- [x] **AC4** — **Arm A (always-on):** a `bun test` asserts `package.json.version` ==
      `server.json.version` == `server.json.packages[0].version` and **fails** when any one of the three
      is tampered, while the in-sync state passes. **Arm B (release-only):** the workflow has a CI step
      that, on `release`, asserts the three JSON versions equal the release tag (`vX.Y.Z`→`X.Y.Z`,
      resolved from the release ref) (Arm A asserted by the test; Arm B asserted by parsing the workflow).
- [x] **AC5** — **rejects intact:** the bundled-core packages stay in `peerDependencies` with `"*"` and
      are **not** added to `dependencies` (audit P1 rejected); **no `exports` field** is added (audit P2
      rejected) (asserted by parsing package.json).
- [x] **AC6** — Sandesh-core untouched; the full `integrations/pi` suite stays green; `tsc --noEmit`
      clean; `npm pack --dry-run` still ships exactly `src/index.ts` + README + LICENSE (CR-SAN-015 AC3
      invariant preserved).

## Close-out
_Completed 2026-06-09 (orchestrator: vidushi-sandesh)._
- **Cycle A** `f87a8db` RED / `729314e` GREEN — package.json: `prepublishOnly`, `typescript` devDep,
  `engines.node>=22.19.0`; `bun install` added `typescript@5.9.3` so `tsc` resolves.
- **Cycle B** `c6f16c3` — Arm A guard `src/version_sync.test.ts` (3 asserts, `import.meta.dir`-anchored,
  tamper-proven non-vacuous).
- **Cycle C** `d4ce87b` RED / `454453b` GREEN / `15b932a` FIX — `.github/workflows/publish-npm.yml`
  (OIDC trusted publishing, `npm` env + `id-token:write`, `npm@latest` upgrade, both bun+node,
  `working-directory: integrations/pi`, build-check on PR/push, dry-run on dispatch) + Arm B release-tag
  check + `src/workflow.test.ts` (22 asserts). FIX: Arm B was reading nonexistent
  `./integrations/pi/server.json` → corrected to repo-root `./server.json`; the too-weak substring
  assertion was strengthened to pin the path (RED 21/1 → GREEN 22/0).
- **VERIFY** (bun-verify-agent) — PASS, all 6 ACs; zero Sandesh-core diff; tests non-vacuous.
- **Independent verification (orchestrator):** `tsc --noEmit` clean; full `integrations/pi` suite
  **188/188**; `npm pack --dry-run` ships exactly `src/index.ts` + `README.md` + `LICENSE` (+ npm's
  always-included `package.json`).
- **Pre-merge gate:** `bun-crucible.py pre-merge-gate` → 188 passed / 0 failed, `tsc` exit 0,
  coverage **99.7% lines / 95.2% funcs**, ingest ok.
- **Accepted-as-is (orchestrator ruling):** the `dry-run` job omits `npm install -g npm@latest`. AC1
  requires the upgrade "before publish" (the real `publish-npm` job has it); `npm publish --dry-run`
  doesn't touch the registry or exercise OIDC, so the upgrade adds no correctness there — not added
  (avoids scope; trivial follow-up if release-rehearsal fidelity is later wanted).
- **One-time maintainer prereq (out of CI, for the first real release):** configure the npm Trusted
  Publisher for `@anthill-tec/sandesh-pi` (org `anthill-tec`, repo `sandesh`, workflow `publish-npm.yml`,
  environment `npm`) — to be documented in RELEASING.md.

## Gap-analysis findings
_Completed 2026-06-08 (orchestrator). Verdict: **READY** (spec updated). Two real gaps found and folded
into Scope/ACs (DRIFT-1 typescript devDep; DRIFT-2 version-sync vs no-tags); all deferred decisions
(auth, engines floor, gate placement) resolved against actual sources._

| # | Dim | Finding | Fix scope | Blocking? |
|---|-----|---------|-----------|-----------|
| DRIFT-1 | 2 | `tsc` not resolvable — no `typescript` devDependency (`node_modules/.bin/tsc` absent); `prepublishOnly`/AC6 `tsc --noEmit` would fail under clean `npm ci` | SPEC_UPDATE (add `typescript` to devDependencies) | Yes (for §S2) |
| DRIFT-2 | 3 | §S4 framed as one always-on 4-way compare, but **zero git tags exist** + JSONs are `0.1.0` with no matching tag → would fail on `develop` | SPEC_UPDATE (split into Arm A always-on JSON + Arm B release-only tag) | Yes (for §S4) |

- **Dimension 1 (Spec vs PRD):** consistent with PRD-pi-extension PE5 (distribute via Pi packages /
  npm). The rejected items (deps-move, exports) remain correctly rejected — re-verified against Pi
  `packages.md:171` (bundled-core → `peerDependencies "*"`, not bundled) and the `pi.extensions` manifest
  load mechanism (not Node `exports`). `package.json` already carries `peerDependencies` (3 × `"*"`),
  `publishConfig.access: public`, `repository{url,directory}`, `files` allowlist — AC5/AC6 invariants hold.
- **Dimension 2 (Spec vs Code):** version fields verified — `package.json.version` `0.1.0`;
  `server.json.version` `0.1.0` (line 10) **and** `server.json.packages[0].version` `0.1.0` (the two
  fields §S4 targets). Existing `src/package.test.ts` is the natural home for Arm A (already loads
  `package.json`; reads `server.json` via `../../server.json`). DRIFT-1 above is the one code-vs-spec gap.
- **Dimension 3 (Code vs PRD):** DRIFT-2 above (gate vs the pre-first-tag reality). Otherwise no
  boundary/semantic concerns — this CR touches only packaging/CI, never the verbs/wake/CLI argv.
- **Resolved deferred decisions:** **auth = npm OIDC trusted publishing** (GA 2025-07-31; `id-token:
  write` + `npm` environment + `npm@latest` upgrade for npm ≥ 11.5.1; provenance auto; one-time
  trusted-publisher setup on npmjs.com — documented in RELEASING.md); **engines floor = `node >=22.19.0`**
  (Pi's own, verified across all Pi packages); **gate placement = Arm A bun test + Arm B CI step**;
  `npm pack` file list is unaffected by new `scripts`/`engines`/`devDependencies` (the `files` allowlist
  is explicit — AC6 invariant) — confirmed by the existing CR-SAN-015 AC3 pack test.
- **Note on the freshness of this analysis:** per the orchestrator preference (re-validate immediately
  before implementation), if CR-SAN-019 (which edits `integrations/pi/src/index.ts`) ships before this
  CR, re-confirm DRIFT-1/the test placement against the changed `src/` before the RED phase.
- **Re-validation 2026-06-09 (post-CR-019 + CR-020 merged): verdict HOLDS.** Re-checked against current
  `develop`: `typescript` still absent from devDependencies, `tsc` still not in `node_modules/.bin`,
  `prepublishOnly`/`engines` still absent (DRIFT-1 stands). All three version fields still `0.1.0` and
  in sync (Arm A target). Still zero git tags — `git describe --tags` errors (DRIFT-2 / Arm split stands).
  peerDependencies (3×`"*"`), `publishConfig.access:public`, and the `files` allowlist intact (AC5/AC6).
  CR-019 added only behavior tests (`smoke.test.ts`, `unregister.test.ts`) — no packaging overlap, so the
  `src/package.test.ts` home for Arm A is unaffected. No spec change required.

## Estimated size
Small–medium: one new CI workflow + two `package.json` additions (`prepublishOnly`, `engines`) + a
version-sync check/test. The substance is the workflow auth decision and the sync gate. All under
`integrations/pi/`, `.github/workflows/`, plus reading `server.json`; no Sandesh-core changes.

## Risks / open questions
- ~~**npm OIDC support**~~ — **RESOLVED:** GA since 2025-07-31; using it (passwordless, parity with PyPI).
  Residual: needs npm ≥ 11.5.1 (job upgrades npm) + a one-time trusted-publisher config on npmjs.com.
- ~~**Version-sync on release**~~ — **RESOLVED:** two-arm design — Arm A (JSON-only) always-on; Arm B
  resolves the expected version from the **release ref** (`github.ref_name`), not `git describe`.
- ~~**`engines` over-tightening**~~ — **RESOLVED:** floor = Pi's own `node >=22.19.0` (no tighter).

## Non-goals (rejected audit items — verified, do NOT re-raise)
- **Move bundled-core deps to `dependencies` (audit P1).** REJECTED — **3rd re-raise**. Pi
  `packages.md:171` (v0.78.1): the bundled-core packages (`@earendil-works/pi-ai`,
  `@earendil-works/pi-coding-agent`, `typebox` — exactly our imports) MUST be in `peerDependencies` with
  `"*"` and **must not be bundled**. They are Pi-provided, not third-party runtime deps (line 169).
  Moving them breaks Pi compliance. (= prior-audit #2, = CR-SAN-016 #3.)
- **Add `exports` to package.json (audit P2).** REJECTED — not applicable: Pi loads the extension via the
  `pi.extensions` manifest (`./src/index.ts`), **not** Node `exports`/`main` resolution; the package is
  not consumed as an importable library. Generic npm-library advice misapplied to a Pi extension.
- Any change to the extension's behaviour (verbs/wake), the CLI argv mapping, or Sandesh-core.
