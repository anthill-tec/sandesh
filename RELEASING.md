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

---

## The workflow (`.github/workflows/publish-pypi.yml`)

One `build` job feeds two event-gated publish jobs:

| Job | Runs on | Target |
|---|---|---|
| `build` | `pull_request`, `push` (develop), `release`, `workflow_dispatch` | builds sdist+wheel, `twine check`, uploads `dist/` artifact — **CI-verifies packaging without publishing** |
| `publish-pypi` | **`release: published`** only | **PyPI** (env `pypi`, OIDC, paused for required-reviewer approval) |
| `publish-testpypi` | **`workflow_dispatch`** only | **TestPyPI** (env `testpypi`, OIDC) |

`build` checks out with `fetch-depth: 0` so hatch-vcs sees the tag → the artifact version is the
tag's `X.Y.Z`.

---

## TestPyPI dry-run (rehearse before the first real release)

1. Ensure the TestPyPI pending publisher (prereq #2) + the `testpypi` environment (#3) exist.
2. GitHub → *Actions* → **Publish to PyPI** → **Run workflow** (`workflow_dispatch`).
3. It builds and publishes to **TestPyPI**. Verify the project page on test.pypi.org and a trial
   install: `uv tool install --index-url https://test.pypi.org/simple/ 'sandesh-relay[mcp]'`
   (deps from real PyPI may need `--extra-index-url https://pypi.org/simple/`).

> A `workflow_dispatch` run builds from the current ref. If that ref is **untagged**, the version
> is a `devN+g<sha>` string — fine for a TestPyPI rehearsal; the real release publishes a clean
> `X.Y.Z` (below).

---

## Cutting a real release (PyPI)

Releases are built from `main` via git-flow; the version comes from the tag.

1. **git-flow release** off `develop`:
   ```bash
   git flow release start X.Y.Z        # e.g. 0.1.0
   # (no version file to edit — hatch-vcs derives it from the tag)
   git flow release finish X.Y.Z       # merges to main + develop, creates tag vX.Y.Z
   git push origin main develop --tags
   ```
   git-flow tags as `vX.Y.Z` — exactly what hatch-vcs expects.
2. **Create a GitHub Release** for the `vX.Y.Z` tag (GitHub → *Releases* → *Draft a new release* →
   pick the tag → *Publish release*). This fires `release: published`.
3. The **`publish-pypi`** job runs, **pauses for your approval** (the `pypi` environment reviewer),
   then uploads `sandesh-relay X.Y.Z` to PyPI with attestations.
4. Verify: `uv tool install 'sandesh-relay[mcp]'` (or `pipx install 'sandesh-relay[mcp]'`).

The **first** release is `v0.1.0` — it also converts the *pending* publishers (prereqs #1/#2) to
active and creates the PyPI project.

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
