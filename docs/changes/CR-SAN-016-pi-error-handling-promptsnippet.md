# CR-SAN-016 — Pi extension: throw on CLI failure + tool promptSnippet (review corrections)

**Status:** COMPLETED (shipped 2026-06-07 on feature/CR-SAN-016)
**Priority:** High (AC1 is a correctness defect — failed CLI calls look like successes to the LLM)
**Depends on:** CR-SAN-013 (the verbs extension being corrected), CR-SAN-014 (shares `src/index.ts`)
**Labels:** phase-4, pi, typescript, fix
**Phase:** Phase 4 (Pi integration)
**Design reference:** Pi `docs/extensions.md` (tool error signaling), `docs/packages.md` (deps), PRD-pi-extension.md PE4

## Context

An independent review of the Pi extension (`integrations/pi/src/index.ts`, shipped in CR-SAN-013)
surfaced five findings; **three are valid** and corrected here, **two are rejected** (they conflict
with Pi's own docs). Verdicts verified against `earendil-works/pi@main`:

- **#1 (VALID — defect):** `runSandesh` *returns* an error-text `AgentToolResult` on non-zero exit.
  Pi `docs/extensions.md`: *"To mark a tool execution as failed (sets `isError: true` on the result
  and reports it to the LLM), **throw an error from `execute`**. Returning a value never sets the
  error flag."* → a failed `sandesh` call currently looks like a **success** to the agent. **Fix:
  throw on failure.**
- **#2 (VALID — should-fix):** the 9 tools have no `promptSnippet`. Pi `types.ts`: *"promptSnippet …
  Custom tools are **omitted from** the [Available tools] section when this is not provided."* → the
  tools aren't listed in the system-prompt's Available-tools guidance. **Fix: add `promptSnippet`
  (+ optional `promptGuidelines`) to every tool.**
- **#5 (VALID — follows #1):** `src/execute.test.ts` asserts the (wrong) returned-error-text
  contract. **Fix: assert the thrown error instead.**
- **#3 (REJECTED):** "runtime deps must be in `dependencies`." Pi `docs/packages.md` makes a specific
  exception — its **bundled-core** packages (`@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`,
  `typebox` — exactly the three we import) go in **`peerDependencies "*"` and must NOT be bundled**.
  Our `dependencies` is correctly absent. **No change.**
- **#4 (REJECTED):** "extend `@earendil-works/pi-tsconfig`." That package **does not exist** (not in
  the Pi monorepo; npm 404). Our standalone tsconfig is tsc-clean. **No change.**

## Scope

### §S1 — Throw on CLI failure (#1)
In `src/index.ts`, change `runSandesh` so the tool `execute` **throws** when the `sandesh` CLI fails,
instead of returning an error-text result:
- **non-zero `r.code`** → `throw new Error(\`sandesh ${verb} failed (exit ${r.code}): ${r.stderr}\`)`
  (Pi catches it, sets `isError: true`, reports to the LLM). Preserve the same message content (verb +
  exit code + stderr) — now as the thrown `Error.message`.
- **`pi.exec` rejection** (the spawn itself throws — e.g. `sandesh` not on PATH) → let it propagate
  (do not swallow); if wrapping for context, rethrow an `Error` (still a throw).
- **`r.code === 0`** → unchanged: `return { content: [{ type: "text", text: r.stdout }], details: undefined }`.
- The **wake loop (CR-SAN-014) is NOT affected** — it branches on `r.code` directly (not via a tool
  `execute`); leave its exit-code handling as-is.

### §S2 — `promptSnippet` (+ `promptGuidelines`) on all 9 tools (#2)
Add to each `pi.registerTool({...})`:
- **`promptSnippet`** — a one-line "what it's for / when to call it" drawn from
  `sandesh/data/usage-scenarios.md` (e.g. `sandesh_send`: *"Send a message to another orchestrator
  (To wakes the recipient; Cc is silent)."*). Required so the tool appears in the Available-tools
  section.
- **`promptGuidelines`** (optional bullets, where it adds value) — the key gotchas: e.g. `sandesh_send`
  → To-wakes/Cc-silent + `all-tracks` minus sender; `sandesh_reply` → `parent_id` is the original
  message's id; `sandesh_fetch` vs `sandesh_inbox` (fetch marks read). Keep concise.

### §S3 — Tests (#5 + #2)
- `src/execute.test.ts`: replace the non-zero-exit assertions (currently "result.content[0].text
  contains stderr") with **`expect(execute(...)).rejects.toThrow(/sandesh … failed \(exit N\)/)`**
  (or the bun-test rejects idiom), asserting the thrown message contains the verb + exit code +
  stderr. Keep the zero-exit success assertions. Add a case for **`pi.exec` rejection → execute
  rejects/throws**.
- Add an assertion (in `index.test.ts` or `package.test.ts`) that **every** registered tool has a
  non-empty `promptSnippet` (and, if added, `sandesh_send`/`sandesh_reply` carry `promptGuidelines`).

## Acceptance criteria

- [x] **AC1** — `execute` **throws** on non-zero `pi.exec` `code`; the thrown `Error.message` contains
      the verb, the exit code, and `stderr` (asserted via `rejects.toThrow`/equivalent for ≥2 tools).
- [x] **AC2** — `r.code === 0` still returns `{ content: [{ type: "text", text: stdout }], details: undefined }`
      (success path unchanged; asserted).
- [x] **AC3** — a `pi.exec` **rejection** (spawn throws) propagates out of `execute` as a thrown error
      (not swallowed into a returned result) — asserted with a mocked rejecting `pi.exec`.
- [x] **AC4** — all **9** tools register a non-empty `promptSnippet`; `sandesh_send` + `sandesh_reply`
      carry `promptGuidelines` conveying To-wakes/Cc-silent and `parent_id`=original-message-id
      respectively (asserted via the captured tool defs).
- [x] **AC5** — `src/execute.test.ts` no longer asserts the returned-error-text contract (all
      non-zero-exit cases assert the throw); full `integrations/pi` suite green; `tsc --noEmit` clean.
- [x] **AC6** — **no change** to `peerDependencies` (#3 stays — bundled-core `"*"`) or `tsconfig.json`
      (#4 stays); the wake loop's exit-code handling is unchanged; Sandesh-core untouched
      (`git diff develop..HEAD -- sandesh/` empty).

## Gap-analysis findings (2026-06-07) — verdict READY

Verified against the current `integrations/pi/`:
- `src/index.ts:138-150` — `runSandesh` returns success on `code===0` and a **returned error result**
  on non-zero (the exact change point → throw). The 9 `pi.registerTool` calls (e.g. `sandesh_send`
  L305) have `name`/`label`/`description`/`parameters` but **no `promptSnippet`**.
- `src/execute.test.ts:620+` — a `describe("AC5 — … non-zero code → error result surfacing stderr")`
  block (sandesh_send/reply/fetch) asserts `result.content[0].text` contains stderr → **flip to
  assert the throw**. The zero-exit success assertions (L576+) stay.
- Findings verified at source: extensions.md:1782 (throw → isError), types.ts:441 (promptSnippet →
  Available-tools), packages.md:171 (peerDeps "*" for bundled-core → #3 rejected), npm 404 for
  pi-tsconfig (→ #4 rejected). No drift; the spec matches the code.

## Estimated size
Small: one `runSandesh` change (return→throw) + `promptSnippet`/`promptGuidelines` on 9 tools +
updating the execute tests' error assertions + a promptSnippet test. All in `integrations/pi/`.

## Risks / open questions
- **Error message format** — keep `"sandesh <verb> failed (exit <code>): <stderr>"` as the thrown
  message (the tests assert verb + code + stderr substrings; don't over-constrain exact punctuation).
- **promptGuidelines scope** — bullets are "active when the tool is active"; keep them short to avoid
  bloating the system prompt. Required: `promptSnippet` on all 9; `promptGuidelines` on the two
  gotcha-heavy tools (send/reply) at minimum.

## Non-goals
- Findings **#3** (deps) and **#4** (pi-tsconfig) — rejected with citations above; explicitly NOT changed.
- Any change to the wake loop behaviour (CR-SAN-014), the CLI argv mapping (CR-SAN-013), Sandesh-core,
  or the MCP surface.
- npm publish / gallery (CR-SAN-015, already done).

## Implementation Notes (2026-06-07)

Corrective CR from an independent code review. Two cycles + a FIX, agent-dispatched (bun-* agents,
all Crucible lifecycle via `bun-crucible.py`). Only `integrations/pi/src/{index,execute,index.test,execute.test}.ts`.

- **C0** — throw on CLI failure (`aaf54ce` RED / `b502a44` GREEN): `runSandesh` now `throw`s
  `Error("sandesh <verb> failed (exit N): <stderr>")` on non-zero exit (Pi sets `isError` + reports
  to the LLM) — a returned value never sets the flag (#1); `pi.exec` spawn rejections propagate (#3
  test); success path unchanged. `execute.test.ts` AC5 block flipped to `rejects.toThrow` + a
  rejection-propagation case (#5).
- **C1** — `promptSnippet` on all 9 tools + `promptGuidelines` on send/reply/fetch (`72648ca` RED /
  `282ac06` GREEN), **wording lifted from `sandesh/data/usage-scenarios.md`** (§5 table → snippets;
  §4/§6 → guideline bullets) — same single source as the MCP docstrings (CR-006) (#2).
- **VERIFY** (`CR-SAN-016-VERIFY`): 135/135, tsc clean, all AC1–AC6 PASS, 0 blocking; 1 should-fix.
- **FIX** (`9ac78b1`): cleaned a garbled `act-this` autocomplete artifact in `sandesh_send`'s first
  guideline bullet (would have shipped verbatim into the LLM system prompt).
- **Pre-merge gate**: tsc clean; **135/135 bun tests; 99.7% line / 95.2% function coverage**; AC6
  invariants confirmed (`peerDependencies`/`tsconfig`/`sandesh/` all untouched).
- **Rejected findings stand:** #3 (deps) — Pi mandates `peerDependencies "*"` for bundled-core; #4
  (`@earendil-works/pi-tsconfig`) — does not exist (npm 404).
