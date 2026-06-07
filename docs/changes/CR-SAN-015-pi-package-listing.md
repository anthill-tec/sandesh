# CR-SAN-015 — Pi extension packaging & listing (npm + pi.dev/packages)

**Status:** PENDING
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
- Version: decide whether the TS package version tracks the Sandesh release or versions
  independently (it depends on the `sandesh` CLI at runtime, not the Python version) — see decisions.

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

- [ ] **AC1** — `integrations/pi/package.json` is npm-publish-ready: `keywords` includes
      `"pi-package"`; a `files` allowlist (or `.npmignore`) ships `src/**/*.ts` + README + LICENSE and
      **excludes** `*.test.ts`; `license`, `repository` (with `directory: "integrations/pi"`),
      `description`, `publishConfig.access: "public"` present (asserted by parsing package.json).
- [ ] **AC2** — the Pi-bundled packages stay `peerDependencies: "*"` (not bundled); the `pi` manifest
      `extensions` entry is intact (asserted by parsing package.json).
- [ ] **AC3** — `npm pack --dry-run` (from `integrations/pi/`) produces a tarball that **includes**
      `src/index.ts` + README + LICENSE and **excludes** `*.test.ts` (asserted by a test running
      `npm pack --dry-run --json` and checking the file list).
- [ ] **AC4** — `integrations/pi/README.md` documents install (`pi install npm:@anthill-tec/sandesh-pi`
      + dev local-path), the `sandesh`-CLI prerequisite, env vars, verbs, the native wake, and the
      manual smoke test.
- [ ] **AC5** — repo `README.md`/`RELEASING.md` document the Pi extension + the npm-publish maintainer
      step (and the pi.dev/packages gallery via the `pi-package` keyword).
- [ ] **AC6** — Sandesh-core untouched; all existing `integrations/pi` suites stay green; tsc clean.
      (The actual `npm publish` + gallery listing are maintainer actions, documented not executed.)

## Estimated size
Small: package.json publish metadata + a `npm pack` test + an extension README + repo doc notes. The
real effort is the maintainer prerequisites (npm `@anthill-tec` org + publish) — out of CI scope.

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
- Any change to Sandesh-core, the CLI, the MCP surface, or the extension's behaviour (verbs/wake).
