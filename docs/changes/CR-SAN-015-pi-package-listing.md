# CR-SAN-015 — Pi extension packaging & listing (npm + pi.dev/packages)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-015)
**Priority:** Medium
**Depends on:** CR-SAN-013 (the extension), CR-SAN-014 (the wake) — the thing being published
**Labels:** phase-4, pi, distribution, npm
**Phase:** Phase 4 (Pi integration)
**Design reference:** docs/research/PRD-pi-extension.md PE5 (distribute via Pi packages), Pi `docs/packages.md`

## Context

Make the Sandesh Pi extension (`integrations/pi/`) **installable + discoverable** so Pi users can
`pi install …` it and find it on the **pi.dev/packages** gallery. The verbs (CR-SAN-013) and native
wake (CR-SAN-014) are done; this is the distribution wrapper (the Pi analogue of CR-SAN-010 PyPI +
CR-SAN-011 registry for the MCP side).

**Verified against Pi `docs/packages.md` (2026-06):** Pi accepts three package sources — `npm:`,
`git:` (clones the **repo root**, runs `npm install` if a root `package.json` exists), and local
paths. **`git:` has NO subdir/subpath syntax** — it cannot target `integrations/pi/` inside the
monorepo. Therefore **npm is the distribution mechanism** for our subdir package (publish the
`integrations/pi/` package to npm; users `pi install npm:@anthill-tec/sandesh-pi`). `git:` install of
the whole repo would look for a root `package.json` (there is none / it's the wrong package), so it
is **not** offered; local-path (`pi install ./integrations/pi`) remains the dev/from-source path.

## Scope

### §S1 — npm publish-readiness of `integrations/pi/package.json`
- Add **`keywords: ["pi-package", …]`** (required for the pi.dev/packages gallery).
- Ensure the published tarball includes the TS the extension loads: a **`files`** allowlist covering
  `src/**/*.ts` (Pi loads TypeScript directly; do NOT strip to JS unless we add a build — keep
  shipping `.ts` per the Pi convention), plus README + LICENSE. Exclude tests (`*.test.ts`) from the
  package (`files` allowlist or `.npmignore`).
- Keep the `pi` manifest (`"pi": { "extensions": ["./src/index.ts"] }`) and the **peerDependencies
  `"*"`** for the Pi-bundled packages (`@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`,
  `typebox`) — Pi provides them; do NOT bundle (CR-SAN-013 §S1).
- `license` (GPL-3.0-only, matching the repo), `repository` (the GitHub repo + `directory:
  "integrations/pi"`), `description`, `publishConfig: { access: "public" }` (scoped `@anthill-tec`).
- **Version = track the Sandesh release** (gap-analysis decision): the extension's
  `package.json` version is hand-bumped to match the `vX.Y.Z` release tag (the Python
  `sandesh-relay` version) at each release — "same version everywhere." (Documented as a
  RELEASING.md step, alongside the git tag; the Python side stays hatch-vcs tag-driven.)

### §S2 — gallery metadata (pi.dev/packages)
- `keywords: ["pi-package"]` makes it appear in the gallery; optionally add `pi.image` / `pi.video`
  (a screenshot / demo) for the preview card. Image/video are optional polish.

### §S3 — publish flow (decision: manual vs CI)
- `npm publish` from `integrations/pi/` (the subdir is the package root). The `@anthill-tec` npm
  **org/scope must exist** and the publisher must be a member (maintainer prerequisite).
- **Decision:** manual `npm publish` vs a CI workflow (`.github/workflows/` publishing on a tag/release,
  via npm **trusted publishing / OIDC** or an `NPM_TOKEN`). Default: **manual** for the first publish
  (low frequency; mirrors CR-SAN-011's manual registry publish), with a documented CI option.
- Gated on CR-SAN-013/014 being merged (done) — publish is a maintainer action.

### §S4 — docs
- **`integrations/pi/README.md`**: what the extension is, **install** (`pi install
  npm:@anthill-tec/sandesh-pi`; dev: `pi install ./integrations/pi`), the **prerequisite** (`sandesh`
  CLI on PATH — CR-SAN-008), env (`$SANDESH_PROJECT`/`$SANDESH_ADDRESS`), the verbs, the native wake,
  and the **manual end-to-end smoke test** (a real Pi session + two Sandesh addresses — deferred from
  CR-SAN-014's risks).
- Repo `README.md` + `RELEASING.md`: a short "Pi extension" pointer + the npm-publish maintainer step.

## Acceptance criteria

- [x] **AC1** — `integrations/pi/package.json` is npm-publish-ready: `keywords` includes
      `"pi-package"`; a `files` allowlist (or `.npmignore`) ships `src/**/*.ts` + README + LICENSE and
      **excludes** `*.test.ts`; `license`, `repository` (with `directory: "integrations/pi"`),
      `description`, `publishConfig.access: "public"` present (asserted by parsing package.json).
- [x] **AC2** — the Pi-bundled packages stay `peerDependencies: "*"` (not bundled); the `pi` manifest
      `extensions` entry is intact (asserted by parsing package.json).
- [x] **AC3** — `npm pack --dry-run` (from `integrations/pi/`) produces a tarball that **includes**
      `src/index.ts` + README + LICENSE and **excludes** `*.test.ts` (asserted by a test running
      `npm pack --dry-run --json` and checking the file list).
- [x] **AC4** — `integrations/pi/README.md` documents install (`pi install npm:@anthill-tec/sandesh-pi`
      + dev local-path), the `sandesh`-CLI prerequisite, env vars, verbs, the native wake, and the
      manual smoke test.
- [x] **AC5** — repo `README.md`/`RELEASING.md` document the Pi extension + the npm-publish maintainer
      step (and the pi.dev/packages gallery via the `pi-package` keyword).
- [x] **AC6** — Sandesh-core untouched; all existing `integrations/pi` suites stay green; tsc clean.
      (The actual `npm publish` + gallery listing are maintainer actions, documented not executed.)

## Gap-analysis findings (2026-06-07) — verdict READY

Verified against Pi `docs/packages.md` + the current `integrations/pi/package.json` + `npm pack`:
- **npm is the mechanism** (Dim 1): Pi `git:` clones the repo root and has **no subdir syntax** — it
  cannot install `integrations/pi/`; npm publish is the clean path for a subdir package.
- **Implementation deltas** (Dim 2): package.json currently lacks `keywords`/`files`/`license`/
  `repository`/`publishConfig`; `npm pack --dry-run` today ships `*.test.ts` + bun.lock and **no
  README/LICENSE**. §S1/AC1/AC3 add the metadata + a `files` allowlist; **AC4's README + a LICENSE
  must exist for the pack test (AC3) to pass** — so the extension README + LICENSE are part of the
  same cycle as the package.json work, not a later docs-only step.
- **Decisions:** npm name **`@anthill-tec/sandesh-pi`** (scoped; needs the npm `anthill-tec` org —
  maintainer prereq); publish = **manual `npm publish`** (documented, like CR-SAN-011); version =
  **tracks the Sandesh release** (`X.Y.Z`, hand-bumped at release).
- **Dim 3:** shim thin, Sandesh-core untouched. No blocking drift.

## Estimated size
Small: package.json publish metadata + extension README + LICENSE + a `npm pack` test + repo doc
notes. The real effort is the maintainer prerequisites (npm `@anthill-tec` org + publish) — out of CI scope.

## Risks / open questions
- **`git:` can't target the subdir** → npm is the only clean distribution (decided above). If npm is
  undesirable, the alternative is a dedicated repo or a repo-root package (rejected — breaks the
  monorepo model the user chose).
- **Decisions for gap-analysis:** (1) npm scope/name — `@anthill-tec/sandesh-pi` (needs the org) vs
  unscoped `sandesh-pi`; (2) manual `npm publish` vs a CI workflow (OIDC/token); (3) TS-package
  version — track the Sandesh release vs independent semver.
- **Shipping `.ts`** (no build step): Pi loads TS directly, so the npm package ships `src/*.ts`. If a
  build/JS dist is ever wanted, that's a separate concern.

## Non-goals
- Running the actual `npm publish` / creating the gallery listing (maintainer actions, documented).
- A JS build pipeline (Pi loads `.ts`).

## Implementation Notes (2026-06-07)

One cycle (C0) + a repo-docs step, agent-dispatched (bun-* agents). All under `integrations/pi/` +
repo docs; Sandesh-core untouched.

- **C0** — publish-readiness (`2ab9d82` RED / `6a08a3b` GREEN): `package.json` publish metadata
  (`keywords` incl `pi-package`, `license` GPL-3.0-only, `repository.directory`, `publishConfig.access`
  public, `files` allowlist) + new `integrations/pi/README.md` (install/prereq/env/verbs/wake/smoke)
  + `integrations/pi/LICENSE` (GPL-3.0). 23 tests; `npm pack --dry-run` ships exactly
  `src/index.ts` + `README.md` + `LICENSE` (no `*.test.ts`).
  - **`files` allowlist is the precise `["src/index.ts","README.md","LICENSE"]`** (not the spec's
    `src/**/*.ts` glob) — the glob would re-include `*.test.ts` (npm `files` negation is unreliable);
    the precise list guarantees test exclusion. Update it if a future CR adds non-test source files.
- **Docs** — repo `README.md` Pi-extension roadmap entry + `RELEASING.md` "Publishing the Pi
  extension (npm)" step (version tracks the release; `npm publish --access public`; `@anthill-tec`
  org; pi.dev/packages via the `pi-package` keyword). Also refreshed the roadmap (009/011 marked done).
- **VERIFY** (`CR-SAN-015-VERIFY`): 128/128, tsc clean, all AC1–AC6 PASS, 0 blocking.
- **Pre-merge gate**: tsc clean; **128/128 bun tests; 99.6% line / 95.2% function coverage**;
  Sandesh-core untouched.
- **Distribution decision:** npm (`@anthill-tec/sandesh-pi`) — Pi `git:` can't target the
  `integrations/pi/` subdir; manual `npm publish`; version tracks the Sandesh release.
- **Remaining (maintainer):** claim the `@anthill-tec` npm org + `npm publish` at the first release;
  list on pi.dev/packages (automatic via the `pi-package` keyword once published).
- Any change to Sandesh-core, the CLI, the MCP surface, or the extension's behaviour (verbs/wake).
