# CR-SAN-019 — Pi: tombstone-aware unregister + real-binary smoke test

**Status:** PENDING
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
shells to the actual installed `sandesh` CLI:
- **Resolve the binary**: if `sandesh` is not on PATH, the test **skips** with a clear message
  (`test.skipIf`/equivalent) — so CI without the Python CLI installed stays green and the mocked suite
  is unaffected.
- **When present**: assert `sandesh --version` exits 0 and prints a parseable version string (fast-fail
  on a missing/broken CLI). Then a **round-trip** against an isolated temp `$XDG_DATA_HOME`:
  `setup` → `register` → `send` → `fetch` and assert the sent subject appears in the fetched output
  (proves the CLI↔shim argv contract end-to-end through the real DB).
- Keep it lightweight and hermetic (temp data home, cleaned up); no network, no install step.
- (A hard **minimum CLI version** gate is deferred — the shim has no version-specific dependency today;
  decide at gap-analysis whether `--version` parsing should enforce a floor or just smoke-check.)

## Acceptance criteria

- [ ] **AC1** — `unregister` with `r.code === 3` returns a **success** result (NOT thrown) whose
      `content[0].text` contains the tombstone message (asserted via a mocked `pi.exec` returning
      `{ code: 3, stdout: "<tombstone msg>" }`; the execute resolves, does not reject).
- [ ] **AC2** — `unregister` with `r.code === 1` (or any non-0/3) **still throws** an `Error` whose
      message carries the verb + exit code + stderr (asserted via `rejects.toThrow`).
- [ ] **AC3** — the exit-3 special-case is **scoped to `unregister`**: another verb (e.g. `send`)
      returning `r.code === 3` **still throws** (asserted).
- [ ] **AC4** — `unregister` with `r.code === 0` returns the normal success result (unchanged; asserted).
- [ ] **AC5** — a real-binary smoke test exists that, **when `sandesh` is on PATH**, asserts
      `sandesh --version` succeeds and a `setup`→`register`→`send`→`fetch` round-trip surfaces the sent
      message; **when absent**, the test **skips** (does not fail) — asserted by the test running green
      in both conditions (skip path verified by temporarily masking PATH or via the skip predicate).
- [ ] **AC6** — full `integrations/pi` suite green; `tsc --noEmit` clean; **Sandesh-core untouched**
      (`git diff develop..HEAD -- sandesh/` empty) and **no change** to `package.json`
      `peerDependencies`/`dependencies` (#2 rejected), `tsconfig.json` (#3 rejected), or
      `mcp_server.py` (#4 already correct).

## Gap-analysis findings
_To be completed by `/gap-analysis CR-SAN-019` before the feature branch — confirm `cli.py` prints the
tombstone message to **stdout** (so `r.stdout` carries it), confirm no other verb legitimately returns
exit 3, and decide the `--version` floor question (smoke-only vs enforced minimum)._

## Estimated size
Small: one scoped change in `runSandesh`/`unregister` `execute` + its tests, plus one new real-binary
smoke test file. All under `integrations/pi/`; no Sandesh-core changes.

## Risks / open questions
- **Message source** — verify the tombstone text is on `r.stdout` (cli.py uses `print`); fall back to
  `r.stderr` defensively.
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
