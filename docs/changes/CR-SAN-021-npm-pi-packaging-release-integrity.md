# CR-SAN-021 — npm/Pi packaging hardening + release-version integrity

**Status:** PENDING
**Priority:** Medium (the version-sync gate + npm CI are real release-integrity value; the rest is polish)
**Depends on:** CR-SAN-015 (the npm/Pi package being hardened), CR-SAN-011 (server.json — a version-sync target)
**Labels:** phase-4, packaging, pi, npm, ci
**Phase:** Phase 4 (Pi integration)
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
- **Auth mechanism — VERIFY at gap-analysis, do not assume:** prefer **npm OIDC trusted publishing**
  *if* npm supports it for this flow at implementation time (passwordless, like the PyPI side); otherwise
  an `NPM_TOKEN` repo secret + `--provenance`. Use a `pi`/`npm` GitHub environment for the publish job.
- Keep the existing **manual** `npm publish` documented in RELEASING.md as the fallback (CR-SAN-015 chose
  manual-first; this adds the automation it explicitly left as "a documented CI option").

### §S2 — `prepublishOnly` safety gate
Add `scripts.prepublishOnly` to `integrations/pi/package.json` running **`tsc --noEmit && bun test`** so a
manual or CI `npm publish` aborts on a type error or failing test.

### §S3 — `engines` floor
Add an `engines` field to `integrations/pi/package.json` declaring the supported runtime floor (node
and/or bun). Exact versions decided at gap-analysis (align with what Pi itself requires / supports —
read Pi's own engines). Advisory metadata; low-risk.

### §S4 — version-sync gate (the release-integrity fix)
Add an automated check that the three version strings agree:
`integrations/pi/package.json.version` **==** `server.json.version` **==** `server.json.packages[0].version`
**==** the latest git tag (`vX.Y.Z` → `X.Y.Z`, the value hatch-vcs derives for Python). On a `release`
build, all must equal the release tag.
- Implementation (decide placement at gap-analysis): a small test (e.g. a `bun test` in
  `integrations/pi/` reading the two JSONs via relative path + `git describe --tags`/the release ref) and
  /or a CI job step. It MUST fail when any of the four disagree.
- This is the guard against the drift the audit verdict flags for both npm and the MCP registry.

## Acceptance criteria

- [ ] **AC1** — `.github/workflows/publish-npm.yml` exists and: publishes `integrations/pi/` to npm
      (scoped, public) on `release: published`; runs a dry-run on `workflow_dispatch`; runs build-check
      only (no publish) on `pull_request`/`push: develop`; the publish job uses a GitHub `environment`
      and the gap-analysis-chosen auth (OIDC or `NPM_TOKEN`) (asserted by parsing the workflow YAML:
      triggers, `working-directory: integrations/pi`, the publish step, the build-check steps).
- [ ] **AC2** — `integrations/pi/package.json` has `scripts.prepublishOnly` running
      `tsc --noEmit && bun test` (asserted by parsing package.json).
- [ ] **AC3** — `integrations/pi/package.json` declares an `engines` field with a node and/or bun floor
      (asserted by parsing package.json).
- [ ] **AC4** — a version-sync check exists and **fails** when `package.json.version`,
      `server.json.version`, `server.json.packages[0].version`, and the git tag disagree (asserted by a
      test that tampering any one of the four makes the check fail, and the in-sync state passes).
- [ ] **AC5** — **rejects intact:** the bundled-core packages stay in `peerDependencies` with `"*"` and
      are **not** added to `dependencies` (audit P1 rejected); **no `exports` field** is added (audit P2
      rejected) (asserted by parsing package.json).
- [ ] **AC6** — Sandesh-core untouched; the full `integrations/pi` suite stays green; `tsc --noEmit`
      clean; `npm pack --dry-run` still ships exactly `src/index.ts` + README + LICENSE (CR-SAN-015 AC3
      invariant preserved).

## Gap-analysis findings
_To be completed by `/gap-analysis CR-SAN-021` before the feature branch — **VERIFY whether npm supports
OIDC trusted publishing** for this flow (else `NPM_TOKEN` + provenance); confirm the `engines` floor from
Pi's own package metadata; decide the version-sync gate's placement (bun test in `integrations/pi/` vs a
CI step) and how it resolves the git tag on a release build; confirm `npm pack` still excludes the new
`scripts`/`engines` from affecting the tarball file list (it won't — `files` allowlist is explicit)._

## Estimated size
Small–medium: one new CI workflow + two `package.json` additions (`prepublishOnly`, `engines`) + a
version-sync check/test. The substance is the workflow auth decision and the sync gate. All under
`integrations/pi/`, `.github/workflows/`, plus reading `server.json`; no Sandesh-core changes.

## Risks / open questions
- **npm OIDC support** — must be verified; the PyPI side gets passwordless trusted publishing, npm may
  still need a token. Don't assume parity.
- **Version-sync on release** — resolving the "expected" version on a `release` event vs a normal push
  (tag may not be checked out the same way); the gate must handle both the in-repo tag and the release ref.
- **`engines` over-tightening** — too high a floor could reject valid Pi hosts; align with Pi's own.

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
