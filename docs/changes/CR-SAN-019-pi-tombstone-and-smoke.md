# CR-SAN-019 — Pi: tombstone-aware unregister + real-binary smoke test

**Status:** COMPLETED (2026-06-08) — shipped on `feature/CR-SAN-019-pi-tombstone-smoke`
(`e536dae` RED → `4b227e4` GREEN §S1 → `2d006ec` tsc-narrow → `94f1c3d` §S2 smoke). 153 tests
green (4 new tombstone + 4 smoke), `tsc --noEmit` clean, coverage 99.7% lines / 95.2% funcs.
Sandesh-core untouched. VERIFY: APPROVE (0 blocking, all 6 ACs PASS). Awaiting merge to `develop`.
**Priority:** Medium (AC1 is a correctness defect — a cooperative-eviction intermediate state is reported to the agent as a failure)
**Depends on:** CR-SAN-013 (the verbs extension), CR-SAN-016 (the throw-on-failure contract being refined here)
**Labels:** phase-4, pi, typescript, fix, test
**Phase:** Phase 4 (Pi integration)
**Design reference:** CLAUDE.md §8 (cooperative tombstone eviction, exit 3) + "notify exit codes"; Pi `docs/extensions.md` (throw → isError); the integration audit (2026-06-07).

## Context

An integration-point audit between the Sandesh backend and the Pi extension surfaced seven findings.
**Five were rejected on independent verification** (recorded under Non-goals with citations — do not
re-raise). **Two survive and are fixed here:**

- **#1 (VALID — defect):** `sandesh unregister` against an address with a **live** notifier returns
  **exit 3** and prints the tombstone message (`cli.py:89-92`) — a *cooperative-eviction intermediate
  state* (CLAUDE.md §8: the caller is meant to retry once the watcher is offline). But CR-SAN-016 made
  `runSandesh` throw on **any** non-zero exit (`index.ts:140-141`), so the agent sees `isError: true`
  for what is actually a successful tombstone-set + deferred soft-delete. **Fix (Option A): treat
  `unregister` exit 3 as a success result carrying the tombstone message; every other non-zero exit
  still throws.**
- **#7 (VALID — coverage gap):** every TS test mocks `pi.exec`; nothing exercises the **real** `sandesh`
  binary, so a CLI↔shim version/argv skew (e.g. the CLI gains a new required flag) would go uncaught.
  **Fix: add a real-binary smoke test** (resolve + `sandesh --version`, and a `send`→`fetch` round-trip
  through a temp store), skipped cleanly when the CLI is absent so the mocked suite still runs in CI.

## Scope

### §S1 — tombstone-aware `unregister` (#1, Option A)
In `integrations/pi/src/index.ts`, scope the success/throw decision so that the **`unregister`** verb's
**exit 3** is NOT an error:
- In `runSandesh` (or the `unregister` tool's `execute` path), when **`verb === "unregister"` &&
  `r.code === 3`**, return a **success** `AgentToolResult`:
  `{ content: [{ type: "text", text: <the tombstone message> }], details: undefined }`. The message
  text is the CLI's output for that case (`cli.py:90` prints it to **stdout** → use `r.stdout`; fall
  back to `r.stderr` if stdout is empty). The text MUST still convey "tombstone set … re-run once
  `addressbook` shows it offline" so the agent knows to retry.
- **All other non-zero exits remain throws** (the CR-SAN-016 contract) — including `unregister` with any
  code other than 0 or 3, and code 3 from **any other verb** (the special-case is scoped to
  `unregister`).
- **Exit 0 unchanged** (success, as today).
- The **wake loop (CR-SAN-014) is untouched** — it branches on `r.code` directly (not via `runSandesh`);
  its exit-code handling (`index.ts:514/533`) stays as-is.

### §S2 — real-binary smoke test (#7)
Add a new test file (e.g. `integrations/pi/src/smoke.test.ts`) that does **not** mock `pi.exec` but
exercises the actual `sandesh` CLI:
- **Resolve the binary**: if `sandesh` is not on PATH, the test **skips** (`test.skipIf`/equivalent) —
  so CI without the Python CLI installed stays green and the mocked suite is unaffected.
- **⚠️ INSTALL PREREQUISITE (gap-analysis DRIFT-1).** The on-PATH `sandesh` must be **current** — the
  `--version` flag was added to the top-level parser (`cli.py:221`) in the CR-008 era, and a **stale
  installed copy lacks it** (`sandesh --version` then errors with **exit 2**, not 0). Before running /
  verifying this test, **re-run `./install.sh`** so the installed binary matches the repo (the repo
  source is correct: `python3 -m sandesh.cli --version` → `sandesh 0+unknown`, exit 0). Alternatively
  resolve the CLI via the repo (`python3 -m sandesh.cli`) for the smoke test. The GREEN/VERIFY agents
  MUST reinstall first or AC5 fails spuriously on a stale binary.
- **Version assertion (DRIFT-2).** Assert a **loose, parseable** version string (e.g. matches
  `/^sandesh \S+/`) — **NOT** a strict semver: pre-release/pre-tag the hatch-vcs fallback is
  `sandesh 0+unknown`, which is valid.
- **Round-trip — drive the real shim (DRIFT-3).** Prefer wiring a **real spawning `pi.exec`** (e.g. via
  Bun's subprocess / `spawnSync`, the pattern `package.test.ts` already uses for `npm pack`) and calling
  the **captured tools' `execute`** (`setup`→`register`→`send`→`fetch`) so the test exercises the shim's
  own argv-building (this is what catches CLI↔shim skew). A hand-built argv round-trip is weaker.
- **Valid fixtures (DRIFT-4).** Use a temp `$XDG_DATA_HOME` and **valid addresses** — `'<Orchestrator>
  - <Project>'` with the `<Project>` part equal to the `project_id` (locked-semantics #10; `send`/
  `register` validate the format), e.g. project `Smoke` with `Mainline - Smoke` / `Track 1 - Smoke`.
  Assert the sent subject appears in the fetched output. Hermetic, cleaned up; no network.
- (A hard **minimum CLI version** gate is **out of scope** — the shim has no version-specific dependency
  today; `--version` is a presence/smoke check only, not a floor.)

## Acceptance criteria

- [ ] **AC1** — `unregister` with `r.code === 3` returns a **success** result (NOT thrown) whose
      `content[0].text` contains the tombstone message (asserted via a mocked `pi.exec` returning
      `{ code: 3, stdout: "<tombstone msg>" }`; the execute resolves, does not reject).
- [ ] **AC2** — `unregister` with `r.code === 1` (or any non-0/3) **still throws** an `Error` whose
      message carries the verb + exit code + stderr (asserted via `rejects.toThrow`).
- [ ] **AC3** — the exit-3 special-case is **scoped to `unregister`**: another verb (e.g. `send`)
      returning `r.code === 3` **still throws** (asserted).
- [ ] **AC4** — `unregister` with `r.code === 0` returns the normal success result (unchanged; asserted).
- [ ] **AC5** — a real-binary smoke test exists, **`skipIf`-guarded** on the `sandesh` binary being
      resolvable (so it skips, not fails, when absent). When it runs, it asserts `sandesh --version`
      exits 0 with a **loose** parseable version (`/^sandesh \S+/`, NOT semver) and a `setup`→`register`
      →`send`→`fetch` round-trip (driving the shim's `execute` via a real spawning `pi.exec`, valid
      `'<Orch> - <Project>'` addresses, temp `$XDG_DATA_HOME`) surfaces the sent subject. (The
      reinstall prerequisite in §S2 must be honored or `--version` fails on a stale binary.)
- [ ] **AC6** — full `integrations/pi` suite green; `tsc --noEmit` clean; **Sandesh-core untouched**
      (`git diff develop..HEAD -- sandesh/` empty) and **no change** to `package.json`
      `peerDependencies`/`dependencies` (#2 rejected), `tsconfig.json` (#3 rejected), or
      `mcp_server.py` (#4 already correct).

## Gap-analysis findings (2026-06-07) — verdict READY (spec updated)

Verified against the actual code. **§S1 (the defect fix) is accurate, no drift:**
- `runSandesh` (`index.ts:139-143`) matches the spec; returns `AgentToolResult<undefined>`/`details:
  undefined` — the exit-3 success return slots in cleanly.
- `cli.py:90-92` **prints the tombstone message to stdout** then `return 3` ⇒ §S1's "use `r.stdout`" is
  correct (risk resolved).
- **Exit 3 is unique to `unregister`** (`grep`: only `cli.py:92`; all other verbs return 0 or
  `sys.exit(<str>)`→1) ⇒ scoping the special-case to `verb==="unregister"` is safe.
- The wake loop uses `pi.exec` **directly** (`index.ts:526`) and reads `r.code` (`:533`) — NOT via
  `runSandesh` ⇒ unaffected.
- AC6 invariants hold now: `package.json` has no `dependencies` (peerDeps `"*"`); tsconfig standalone;
  `mcp_server` uses `description=` (0× `desc=`).

**§S2 (smoke test) — 5 non-blocking findings, folded into the spec above:**
- **DRIFT-1 (env, important):** the on-PATH `sandesh` is **STALE** and lacks `--version` (`sandesh
  --version` → **exit 2**). Repo source is correct (`python3 -m sandesh.cli --version` → `sandesh
  0+unknown`, exit 0; top-level parser `cli.py:221`); the installed `~/.local/share/sandesh/app/cli.py`
  predates it. ⇒ **GREEN/VERIFY MUST re-run `./install.sh`** before exercising AC5. (Same root cause
  would make the shipped wake-loop prereq probe `index.ts:464` mis-detect the CLI as missing against a
  stale install — operational, not a repo defect.)
- **DRIFT-2:** version is `sandesh 0+unknown` pre-tag ⇒ assert loose `/^sandesh \S+/`, not semver.
- **DRIFT-3:** drive the captured tools' `execute` with a real spawning `pi.exec` (precedent:
  `package.test.ts` `spawnSync`) to actually catch argv skew.
- **DRIFT-4:** round-trip needs valid `'<Orch> - <Project>'` addresses (project part == project_id) +
  temp `$XDG_DATA_HOME`.
- **DRIFT-5:** AC5 softened to "`skipIf`-guarded" (don't mechanically assert the skip branch).

No downstream CRs depend on CR-SAN-019. **Proceed to the feature branch.**

## Estimated size
Small: one scoped change in `runSandesh`/`unregister` `execute` + its tests, plus one new real-binary
smoke test file. All under `integrations/pi/`; no Sandesh-core changes.

## Risks / open questions
- **Message source** — confirmed: the tombstone text is on `r.stdout` (`cli.py:90` `print`); fall back
  to `r.stderr` defensively.
- **Stale install (DRIFT-1)** — `sandesh --version` exits 2 on a stale on-PATH binary; re-run
  `./install.sh` before running the smoke test or AC5 fails spuriously. The repo source is correct.
- **Version format (DRIFT-2)** — pre-tag the version is `0+unknown` (hatch-vcs fallback); assert loosely.
- **Smoke-test hermeticity / CI** — must skip cleanly without the CLI and must not pollute the real data
  home (temp `$XDG_DATA_HOME`); confirm the bun test runner can spawn the real process in CI.
- **Other exit-3 producers** — `notify`'s exit codes (2/3/4/5) are NOT routed through `runSandesh`
  (the wake loop reads `r.code` directly), so scoping the special-case to the `unregister` verb is safe;
  re-confirm at gap-analysis.

## Non-goals (rejected audit findings — verified, do NOT re-raise)
- **#2 — move bundled-core deps to `dependencies`.** REJECTED. Pi `packages.md:171` (v0.78.1): the
  bundled-core packages (`@earendil-works/pi-ai`, `@earendil-works/pi-coding-agent`, `typebox` — exactly
  our imports) MUST be in `peerDependencies` with `"*"` and **must not be bundled**. Our `package.json`
  is already correct. (Same as CR-SAN-016 #3.)
- **#3 — extend `@earendil-works/pi-tsconfig`.** REJECTED. No such package exists in the Pi monorepo
  (`find`/`grep` empty) and `packages.md` never prescribes extending a base tsconfig. (Same as
  CR-SAN-016 #4.)
- **#4 — `Field(desc=)` → `description=`.** REJECTED (already correct): `mcp_server.py` has **0×**
  `desc=`, **30×** `description=`.
- **#5 — empty `--body ""` → write an empty body file.** REJECTED: subject-only on empty/None body is
  the **documented, locked** contract (`sandesh_db.py:220` docstring "None/'' ⇒ subject-only";
  CLAUDE.md locked-semantics #4). Changing it would require a CLAUDE.md §4 amendment (not in scope).
- **#6 — parse read-tool output into structured JSON.** Out of scope by design: the Pi extension is a
  **thin CLI shim** (CR-SAN-013); structured data is the MCP server's role (CR-SAN-001..003).
- Any change to the CLI argv mapping (CR-SAN-013), the wake loop (CR-SAN-014), or Sandesh-core.
