# CR-SAN-046 — Hotfix 0.3.6: `npm pack --json` shape guard (unblock the inaugural npm publish)

**Status:** PENDING
**Priority:** High (release blocker — the first `@anthill-tec/sandesh-pi` publish fails at `prepublishOnly`)
**Depends on:** —
**Labels:** hotfix, pi, npm, release-engineering, test-only
**Wave:** hotfix **0.3.6** (off `main`, git-flow hotfix; precedent CR-SAN-033 / 0.3.2)
**Design reference:** owner decision 2026-09-26 — the npm account/org (`@anthill-tec`) and the
`NPM_TOKEN` repo secret now exist, so `publish-npm.yml` runs the real publish on the next
`release: published` event. The 2026-09-26 re-fire of the `v0.3.5` release (run 36236542037)
proved the token + provenance path and failed ONLY in npm's own `prepublishOnly` gate.

## Context
`publish-npm.yml` upgrades the runner's npm before publishing (`npm install -g npm@latest`, needed
for OIDC ≥ 11.5.1) and then runs `npm publish --provenance --access public`. npm executes the
package's `prepublishOnly` script (`tsc --noEmit && bun test`) under that upgraded npm — today
**npm 12**. `integrations/pi/src/package.test.ts` (CR-SAN-021 AC3) shells `npm pack --dry-run
--json` and parses the output as an **array** (`parsed[0].files`), which is the npm ≤ 11 shape.
npm 12 emits an **object keyed by package name**
(`{"@anthill-tec/sandesh-pi": {id, name, version, files[…]}}`), so `parsed[0]` is `undefined` and
the whole AC3 group errors: `TypeError: undefined is not an object (evaluating
'JSON.parse(result.stdout)[0].files')`. `prepublishOnly` exits 1, `npm publish` aborts, and the
package stays 404 on the registry.

The `build-check` job passes the same suite because it uses node 22's **bundled npm 10** (array
shape). The failure is therefore invisible until the publish step, and it reproduces locally on any
machine with npm ≥ 12 (this workstation: npm 12.1.0).

Since the release event checks out the **tag**, no fix on `develop` can reach the `v0.3.5`
publish — the fix must ship in a new tag, hence a hotfix. Sandesh core is untouched; the PyPI
0.3.6 wheel is identical code to 0.3.5 (hatch-vcs derives the version from the tag).

## Scope
- **§S1 — shape normalizer.** Add `integrations/pi/src/npm_pack.ts` exporting
  `packedFilePaths(stdout: string): string[]`: parse the JSON; if the root is an **array**, take
  element 0 (npm ≤ 11); if the root is a **plain object**, take its first value (npm ≥ 12, keyed by
  package name); return `entry.files.map(f => f.path)`. Any other root, a missing/non-array `files`,
  or an entry lacking `files` throws an `Error` whose message starts with
  `unrecognised npm pack --json shape:` and includes the first 200 characters of the input. Test-support
  code only — NOT in `package.json` `files` (the whitelist ships `src/index.ts`, `README.md`,
  `LICENSE`), so the tarball is unchanged.
- **§S2 — AC3 uses it.** `src/package.test.ts` `beforeAll` replaces the inline
  `JSON.parse(result.stdout) as NpmPackResult[]` + `parsed[0].files.map(...)` with
  `packedFilePaths(result.stdout)`. The nine AC3 assertions (includes `src/index.ts`/`README.md`/
  `LICENSE`, excludes every `*.test.ts`) are unchanged. The now-unused `NpmPackResult` interface is
  removed (`NpmPackFile` may move to `npm_pack.ts`).
- **§S3 — manifests + docs.** `release.sh set-version 0.3.6` bumps `integrations/pi/package.json`
  and `server.json` (×2) on the hotfix branch before `finish` (CR-SAN-042 guard). Queue row in
  `docs/changes/README.md`; no README/USER_GUIDE change.

## Acceptance criteria
- **AC1** — `src/npm_pack.test.ts`: `packedFilePaths` returns `["LICENSE","README.md","package.json",
  "src/index.ts"]` for BOTH fixtures — (a) the npm 10 array form `[{"id":…,"files":[…]}]` and (b) the
  npm 12 keyed form `{"@anthill-tec/sandesh-pi":{"id":…,"files":[…]}}` — with identical `files` entries.
- **AC2** — `packedFilePaths` throws `Error` with message prefix `unrecognised npm pack --json shape:`
  for: `"[]"`, `"{}"`, `"null"`, `"42"`, and an entry without `files` (`[{"id":"x"}]`).
- **AC3** — `bun test src/package.test.ts` is green on this workstation (npm 12.1.0) AND the CI
  `build-check` job (npm 10) — the AC3 group reports 9 pass, 0 fail in both.
- **AC4** — `npm pack --dry-run` file list is unchanged: exactly `LICENSE`, `README.md`,
  `package.json`, `src/index.ts` (`npm_pack.ts` and all `*.test.ts` absent).
- **AC5** — `integrations/pi/package.json` `version`, `server.json` `.version` and
  `.packages[0].version` all equal `0.3.6` on the hotfix branch before finish (`version_sync.test.ts`
  AC4 + `version_gate.test.ts` AC5 green).
- **AC6** — On the `v0.3.6` release event, the `publish-npm` job's *Publish to npm* step exits 0 and
  `https://registry.npmjs.org/@anthill-tec/sandesh-pi` returns 200 with `dist-tags.latest == "0.3.6"`.

## Estimated size
Tiny — one ~20-line helper, a two-line change in the existing test's `beforeAll`, one new test file
with two fixtures, and the manual manifest bump.

## Non-goals
- No change to the wake loop, the tool surface, or Sandesh core.
- No npm-version pinning in `publish-npm.yml` (`npm@latest` stays — OIDC needs ≥ 11.5.1 and the test
  must simply tolerate both shapes).
- Trusted-publishing migration (retire `NPM_TOKEN`, OIDC-only) — the 0.4.0 release CR.
