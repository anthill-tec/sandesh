# Releasing Sandesh

Sandesh is published to **PyPI** as **`sandesh-relay`** (the import package + the `sandesh` /
`sandesh-mcp` console scripts keep the name `sandesh`). Publishing is automated by
`.github/workflows/publish-pypi.yml` using **OIDC trusted publishing** — there is **no API token
anywhere**.

The version is **git-tag-driven** (`hatch-vcs`): the git tag **`vX.Y.Z`** is the single source of
truth, and the published PyPI version is exactly **`X.Y.Z`** (hatch-vcs strips the `v`). You never
hand-edit a version — **you bump by tagging.**

---

## One-time maintainer prerequisites

These are owner actions on PyPI / TestPyPI / GitHub. They are configured **before** the first
publish (PyPI supports *pending* publishers — the project need not exist yet; the first successful
publish creates it).

### 1. PyPI — pending Trusted Publisher
PyPI → *Your projects* → *Publishing* → **Add a new pending publisher** (GitHub):

| Field | Value |
|---|---|
| PyPI Project Name | `sandesh-relay` |
| Owner | `anthill-tec` |
| Repository name | `sandesh` |
| Workflow name | `publish-pypi.yml` |
| Environment name | `pypi` |

### 2. TestPyPI — pending Trusted Publisher (for the dry-run)
Same flow on **test.pypi.org**:

| Field | Value |
|---|---|
| PyPI Project Name | `sandesh-relay` |
| Owner | `anthill-tec` |
| Repository name | `sandesh` |
| Workflow name | `publish-pypi.yml` |
| Environment name | `testpypi` |

### 3. GitHub Environments
Repo → *Settings → Environments* → create:
- **`pypi`** — **add a required reviewer** (yourself). The `publish-pypi` job then **pauses for
  manual approval** before it uploads to PyPI — a human gate on every release.
- **`testpypi`** — no protection needed (it's a rehearsal target).

PEP 740 **attestations** are generated and uploaded automatically by
`pypa/gh-action-pypi-publish` (≥ v1.11) for trusted-publishing flows — nothing to configure.

### 4. GitHub repo secret — `RELEASE_PAT` (required by the auto-release chain)
The push-to-`main` step that auto-creates the GitHub Release uses a **non-default token**: a Release
created with the default `GITHUB_TOKEN` does **not** re-trigger an `on: release` workflow (GitHub's
anti-recursion rule), so the publish would never fire. Create a fine-grained **PAT** (or GitHub App
token) with **Contents: write** on `anthill-tec/sandesh` and store it as the repo secret
**`RELEASE_PAT`**. Without it, `create-release` still makes the Release, but nothing publishes.
(The job is gated to `push: main` only — never PRs — so the secret is never exposed to forked-PR code.)

---

## The workflow (`.github/workflows/publish-pypi.yml`)

One `build` job feeds the event-gated jobs below:

| Job | Runs on | Target |
|---|---|---|
| `build` | `pull_request`, `push` (`develop`, `main`), `release`, `workflow_dispatch` | builds sdist+wheel, `twine check`, uploads `dist/` artifact — **CI-verifies packaging without publishing** |
| `create-release` | **`push` to `main`** carrying a new `vX.Y.Z` tag | runs `gh release create` (using `RELEASE_PAT`) → fires `release: published`. Idempotent (skips if the Release exists); a no-op on ordinary `main` commits with no version tag |
| `publish-pypi` | **`release: published`** only | **PyPI** (env `pypi`, OIDC, paused for required-reviewer approval) — **guarded** (see below) |
| `publish-testpypi` | **`workflow_dispatch`** only | **TestPyPI** (env `testpypi`, OIDC) |

`build` checks out with `fetch-depth: 0` so hatch-vcs sees the tag → the artifact version is the
tag's `X.Y.Z`.

**Publish branch guard.** Before any upload, `publish-pypi` (and `publish-npm`) assert the ref is a
`^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$` tag **and** that its commit is reachable from `origin/main`
(`git merge-base --is-ancestor "$GITHUB_SHA" origin/main`). A Release cut from any other ref fails
the guard before publishing — the publish can only happen for a real release tag on `main`.

---

## TestPyPI checkpoint (rehearse from the hotfix/release branch, before finishing)

A TestPyPI upload is a **`workflow_dispatch`** that builds from whatever ref you dispatch. The
helper makes it one command from the branch you're working on:

```bash
scripts/release.sh checkpoint            # on a hotfix/* or release/* branch
# → gh workflow run publish-pypi.yml --ref <that branch>
```

1. Ensure the TestPyPI pending publisher (prereq #2) + the `testpypi` environment (#3) exist.
2. Run the checkpoint above (or, manually: GitHub → *Actions* → **Publish to PyPI** → **Run
   workflow** on the branch). It builds and publishes to **TestPyPI**.
3. Verify: `uv tool install --index-url https://test.pypi.org/simple/ 'sandesh-relay[mcp]'`
   (deps from real PyPI may need `--extra-index-url https://pypi.org/simple/`).

> **Versioning on an untagged branch.** `pyproject.toml` sets
> `[tool.hatch.version] raw-options = { local_scheme = "no-local-version" }`, so an untagged
> `hotfix/*`/`release/*` build derives a clean **`X.Y.Z.devN`** (PEP 440 dev release) instead of a
> `…devN+g<sha>` *local* version. PyPI/TestPyPI **reject** local versions, so this is what makes a
> pre-finish checkpoint uploadable — **no rc tag required.** (Each checkpoint at a new commit gets a
> higher `devN`, keeping the version unique.)

---

## Cutting a real release (PyPI) — push-`main` → Release → publish

Publishing is a **consequence of landing a `vX.Y.Z` tag on `main`**; you never create the GitHub
Release by hand. The chain:

> **push to `main` (new `vX.Y.Z` tag) → `create-release` runs `gh release create` (auth via
> `RELEASE_PAT`) → `release: published` → guarded `publish-pypi` (+`publish-npm`) upload.**

git-flow puts that tag on `main` on a `release`/`hotfix` *finish*; pushing `main` is the trigger.
Use the helper:

```bash
# from the hotfix/* or release/* branch you've been working on:
scripts/release.sh finish X.Y.Z
#   → git flow <hotfix|release> finish X.Y.Z   (merges to main + develop, tags vX.Y.Z)
#   → git push origin main develop --tags      (the main push starts the chain above)
```

Equivalently by hand:

```bash
git flow release start X.Y.Z          # off develop  (or: git flow hotfix start X.Y.Z, off main)
git flow release finish X.Y.Z         # merges to main + develop, creates tag vX.Y.Z
git push origin main develop --tags   # the main push fires create-release → release → publish
```

Then:
1. CI on `main` runs `create-release`, which `gh release create vX.Y.Z --generate-notes` (idempotent).
2. That `release: published` event runs **`publish-pypi`**, which **passes the branch guard**
   (`vX.Y.Z` tag reachable from `origin/main`), then **pauses for your approval** (the `pypi`
   environment reviewer), then uploads `sandesh-relay X.Y.Z` to PyPI with attestations.
3. Verify: `uv tool install 'sandesh-relay[mcp]'` (or `pipx install 'sandesh-relay[mcp]'`).

You can still publish the **manual** way (GitHub → *Releases* → *Draft a new release* → pick the
`vX.Y.Z` tag → *Publish release*); the same guarded `publish-pypi` runs. The auto path just removes
the manual step.

The **first** release converts the *pending* publishers (prereqs #1/#2) to active and creates the
PyPI project; ensure **`RELEASE_PAT`** (prereq #4) exists first, or the auto chain stops at the
created Release.

---

## Schema-migration release steps (before tagging)

Sandesh's schema is versioned by the migration subsystem (`sandesh migrate` + the `migrations/`
directory), with a committed snapshot of the fully-migrated schema at
**`sandesh/schema/current-schema.json`**. Before you tag a release, ensure that snapshot and the
`migrations/` directory are **in sync**:

1. If this release added a migration, regenerate the snapshot and commit it:
   ```bash
   pip install -e '.[migrate]'                                  # the migrate extra (yoyo + jsonschema)
   sandesh setup --project rel && sandesh migrate --all --project rel
   sandesh migrate --dump-schema --project rel > sandesh/schema/current-schema.json
   git add sandesh/schema/current-schema.json && git commit -m "chore: refresh schema snapshot"
   ```
2. The **snapshot-sync** gate in `.github/workflows/publish-pypi.yml` enforces this in CI: it seeds a
   temp store, runs `migrate --all`, and asserts `migrate --dump-schema` **equals** the committed
   `current-schema.json` — a mismatch fails the job, so a migration added without refreshing the
   snapshot blocks the release. Keep the two in sync and the gate stays green.

Note that the installer **migrates** existing stores on update (`install.sh` runs
`sandesh migrate --all`), so a released update brings users' stores to the latest schema
automatically.

---

## Listing on the official MCP Registry (after the PyPI publish)

Sandesh ships a [`server.json`](server.json) (`io.github.anthill-tec/sandesh`) for the official MCP
Registry. The registry **verifies ownership against the live PyPI package** — it fetches
`https://pypi.org/pypi/sandesh-relay/json` and checks the README (the long-description) for the
marker `mcp-name: io.github.anthill-tec/sandesh` (present as an HTML comment in `README.md`, so it
ships via `readme = "README.md"`). So publish to the registry **only after** the package (with that
README) is live on PyPI.

One-time + per-listing (maintainer, as an `anthill-tec` GitHub member):

```bash
# install the publisher CLI (see modelcontextprotocol/registry releases), then:
mcp-publisher login github
mcp-publisher publish --dry-run     # validate server.json
mcp-publisher publish               # list io.github.anthill-tec/sandesh
```

- The `io.github.*` namespace authenticates via GitHub (the repo owner/org).
- `server.json`'s `version` should track the published PyPI version; bump it when re-listing a new
  release.
- CI validates `server.json` structurally (the `tests/test_server_json.py` suite); the authoritative
  validation is `mcp-publisher publish --dry-run`.

---

## Publishing the AUR package (Arch, after the PyPI release)

Sandesh ships an AUR `PKGBUILD` at [`packaging/aur/`](packaging/aur/) (`pkgname=sandesh-relay`,
source = the PyPI sdist). The published checksum can only be computed **after** the PyPI sdist for
`X.Y.Z` exists, so publish the AUR package **after** the PyPI release.

One-time + per-release (maintainer, with an AUR account + SSH key registered):

```bash
# 1. point pkgver at the release and fill the real checksum from the published sdist
cd packaging/aur
#   edit PKGBUILD: pkgver=X.Y.Z, pkgrel=1
updpkgsums                      # replaces sha256sums=('SKIP') with the real hash from PyPI
makepkg --printsrcinfo > .SRCINFO
makepkg -f                      # sanity: builds the package locally
namcap PKGBUILD                 # lint (no E:)

# 2. push to the AUR
git clone ssh://aur@aur.archlinux.org/sandesh-relay.git aur-sandesh-relay   # first time
cp PKGBUILD .SRCINFO aur-sandesh-relay/
cd aur-sandesh-relay && git commit -am "X.Y.Z-1" && git push
```

- Bump `pkgver` (and reset `pkgrel=1`) + regenerate `.SRCINFO` every release; bump only `pkgrel`
  for packaging-only fixes.
- `python-mcp` is an **AUR** dependency (optdepends) — yay/paru resolve it for users who want the
  server. The repo keeps `sha256sums=('SKIP')` as a pre-publish placeholder; `updpkgsums` fills the
  real hash at release.

---

## Publishing the Pi extension (npm, after the release)

The Sandesh **Pi extension** lives at [`integrations/pi/`](integrations/pi/) and is distributed via
**npm** as **`@anthill-tec/sandesh-pi`** (Pi's `git:` install can't target a repo subdir, so npm is
the mechanism). Pi users then `pi install npm:@anthill-tec/sandesh-pi`; it appears on the
[pi.dev/packages](https://pi.dev/packages) gallery via the `pi-package` keyword.

One-time prerequisite: the **`@anthill-tec` npm org** must exist and you a member.

Per release (maintainer):

```bash
cd integrations/pi
# 1. version tracks the Sandesh release (X.Y.Z == the vX.Y.Z tag)
#    edit package.json "version" to X.Y.Z
bun test && bun x tsc --noEmit          # green + typecheck
npm pack --dry-run                       # sanity: ships src/index.ts + README.md + LICENSE, no *.test.ts
npm publish --access public              # scoped public publish
```

- The extension depends on the **`sandesh` CLI** on PATH (the PyPI/AUR install) — it shells to it
  via `pi.exec`; it does not bundle Sandesh-core.
- `@earendil-works/*` + `typebox` stay `peerDependencies` (Pi bundles them) — not shipped in the tarball.
