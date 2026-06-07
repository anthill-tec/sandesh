# CR-SAN-014 — Pi native wake (background watcher → turn injection)

**Status:** PENDING
**Priority:** Medium
**Depends on:** CR-SAN-013 (the Pi extension scaffold + verbs), CR-SAN-008 (the `sandesh` CLI / `notify`)
**Labels:** phase-4, pi, typescript, wake
**Phase:** Phase 4 (Pi integration)
**Design reference:** docs/research/DN-pi-wake.md (verdict **a** = native injection; design **W1**), PRD-pi-extension.md §5

## Context

On Pi the wake is **native** — `DN-pi-wake.md` (spike) proved a Pi extension can re-invoke an *idle*
agent from a background watcher via `pi.sendUserMessage()` ("always triggers a turn") /
`pi.sendMessage(…, {triggerTurn:true})` (cf. the canonical `file-trigger.ts`). So unlike Claude
Code (where the wake must be an out-of-band `run_in_background`), the Sandesh Pi extension **owns the
wake loop itself**. This CR adds that loop to the extension from CR-SAN-013, reusing the proven
`sandesh notify` watcher as the doorbell (no change to Sandesh-core).

## Scope

### §S1 — The wake loop (design W1) — INTEGRATE with the existing `session_start` handler
**Gap-analysis DRIFT-1:** CR-SAN-013 C2 **already registered a `session_start` handler** in
`integrations/pi/src/index.ts` (the missing-CLI prereq probe: `pi.exec("sandesh",["--version"])` →
`ctx.ui.notify` on failure). Do NOT add a competing/duplicate handler — **extend that handler**:
- The handler already probes the CLI. **Start the wake loop only after a SUCCESSFUL probe**
  (`code === 0`, i.e. `sandesh` is present). If the probe fails (CLI missing) the existing notice
  fires and the loop **does not start** — otherwise arming `sandesh notify` would make `pi.exec`
  reject and the error branch would spin/retry forever against a missing CLI.
- Then start a **detached async loop** (fire-and-forget, not awaited by the handler) that arms the
  doorbell and translates its exit into a Pi turn:
- `const r = await pi.exec("sandesh", ["--project", P, "notify", "--to", self], { signal })` — blocks
  until the address has unread `to` mail (or timeout). Reuses Sandesh's liveness-aware watcher +
  exit-code contract unchanged. (Verified codes: `0` mail / `1` error / `2` timeout / `3` tombstoned /
  `4` evicted / `5` dedup; `notify` default `--timeout` is **14400s = 4h** — see DRIFT-2 note.)
- Branch on `r.code`:
  - **`0` (mail)** → `pi.sendUserMessage("You have unread Sandesh mail — call sandesh_fetch for
    \"<self>\", then act on it.")` (always triggers a turn), then re-arm.
  - **`2` (timeout)** → re-arm silently.
  - **`3` (tombstoned) / `4` (evicted)** → stop the loop (the address is being removed).
  - **`5` (dedup — a notifier already live)** → stop (another loop owns the doorbell).
  - **`1`/other (error)** → backoff + re-arm (do NOT busy-spin; cap the backoff).

> **DRIFT-2 note (`notify --timeout`):** a single `pi.exec notify` blocks up to the 4h default held in
> a detached promise — acceptable (exit-2 re-arms; `session_shutdown` aborts via the signal). Passing
> a shorter `--timeout` to bound each arm is optional; the 4h hold is by-design, not a leak.

### §S2 — Address / project resolution
- `self` = `$SANDESH_ADDRESS`, project = `$SANDESH_PROJECT` (same env contract as verbs). If unset,
  the loop does not start and the extension surfaces a one-time notice (the verbs still work). A
  `pi.registerCommand` / setting to set them at runtime is optional (nice-to-have).

### §S3 — Wake payload (decision: prompt the agent to fetch)
- Use `pi.sendUserMessage(...)` instructing the agent to call the `sandesh_fetch` **tool**
  (from CR-SAN-013) — keeps the agent in control and the action auditable (DN decision i).
- If the user is mid-turn when mail lands, pass `deliverAs: "followUp"` so it's delivered when idle
  rather than interrupting.

### §S4 — Lifecycle / single-loop guard
- Stop the loop on **`pi.on("session_shutdown", …)`** (verified Pi event) — hold an `AbortController`,
  pass its `signal` to the `pi.exec` notify call, and `abort()` it on shutdown; set a stopped flag so
  a pending re-arm doesn't restart.
- One loop per session: rely on Sandesh's own notifier dedup (exit `5`) for cross-process safety,
  plus an in-extension guard (a module-level "loop started" flag) so the `session_start` handler
  can't start two loops if fired twice.

### §S5 — Tests (bun test)
- Drive the loop with a **mocked `pi.exec`** returning a scripted sequence of `{code}` and assert:
  - `code 0` → `pi.sendUserMessage` called (once) with a fetch-prompting message, then re-arm;
  - `code 2` → re-arm, no `sendUserMessage`;
  - `code 3` and `code 5` → loop stops (no further `pi.exec`);
  - error code → backoff then re-arm.
- Assert the loop does NOT start when `$SANDESH_ADDRESS`/`$SANDESH_PROJECT` are unset (notice shown).
- Assert `session_shutdown` stops the loop (no re-arm after).
- Make the loop deterministic/testable (inject the exec + a no-op sleep; avoid real timers/processes).

## Acceptance criteria

- [ ] **AC1** — the **existing** `session_start` handler (the CR-013 prereq probe) is extended so
      that, **after a successful `sandesh --version` probe**, it starts a wake loop calling
      `pi.exec("sandesh", [..., "notify", "--to", <self>], {signal})` with `self`/project from
      `$SANDESH_ADDRESS`/`$SANDESH_PROJECT` (asserted with mocked exec). If the probe FAILS
      (CLI missing), the loop does NOT start (only the existing install notice fires).
- [ ] **AC2** — on `notify` exit **0**, the loop calls `pi.sendUserMessage(...)` exactly once with a
      message directing the agent to fetch (`sandesh_fetch` / "unread mail"), then re-arms (asserted).
- [ ] **AC3** — exit **2** re-arms silently (no `sendUserMessage`); exit **3** and exit **5** stop
      the loop (no further `pi.exec`); an error code backs off then re-arms (asserted via scripted
      exec sequences).
- [ ] **AC4** — when `$SANDESH_ADDRESS` or `$SANDESH_PROJECT` is unset, the loop does **not** start
      and a one-time notice is shown; the verbs (CR-SAN-013) are unaffected (asserted).
- [ ] **AC5** — `session_shutdown` stops the loop (aborts the in-flight exec; no re-arm afterward)
      and only one loop runs per session (asserted).
- [ ] **AC6** — Sandesh-core untouched (no `sandesh/` Python diff); the wake is entirely in
      `integrations/pi/`; the `notify` exit-code contract is consumed, not modified.

## Gap-analysis findings (2026-06-07, `/gap-analysis CR-SAN-014`) — verdict SPEC_UPDATE applied; now READY

Verified against `sandesh/notify.py` (exit codes), the merged `integrations/pi/src/index.ts`, and the
Pi API (`earendil-works/pi@main`):
- **DRIFT-1 (Dim 2/3 → §S1/§S4/AC1, FIXED):** CR-013 C2 already registered a `session_start` handler
  (the prereq probe). The wake loop must **extend that handler** and **start only after a successful
  `sandesh --version` probe** — arming `notify` when the CLI is missing would spin the error branch.
- **DRIFT-2 (Dim 2 → §S1 note):** `notify` default `--timeout` is **14400s (4h)**; a detached
  `pi.exec` holds that long (acceptable; exit-2 re-arms, shutdown aborts via signal).
- **Verified contracts:** notify exit codes `0/1/2/3/4/5` match the W1 branch table; Pi
  `session_shutdown` event + `sendUserMessage(content,{deliverAs})` + `pi.exec(...,{signal})` all real.
- **Dim 3:** native wake (sendUserMessage triggers a turn, DN outcome a); shim thin; Sandesh-core
  untouched (the wake only `pi.exec`s the `sandesh` CLI).
- **Verdict: READY** — DRIFT-1/2 folded into §S1/§S4/AC1.

## Estimated size
Small–medium: extend the `session_start` handler with a wake-loop module + lifecycle wiring +
`bun test` unit tests over a mocked exec. The design is fully specified by DN-pi-wake (W1); no research left.

## Risks / open questions
- **Long-blocking `pi.exec`** held in a detached promise — DN-pi-wake flags confirming a
  full-timeout `notify` is fine in the event loop (it's a child process; the `notify` timeout floor
  bounds it). Validate against a real Pi session during GREEN/VERIFY.
- **`sendUserMessage` vs `sendMessage(triggerTurn)`** — `sendUserMessage` always triggers and reads
  as a user turn (chosen); revisit if a custom message type renders better.
- Manual end-to-end (a real Pi session + two Sandesh addresses) is a maintainer smoke test — the
  `bun test` suite covers the loop logic with mocks; document the manual smoke in the extension README.

## Non-goals
- The verbs (CR-SAN-013).
- Packaging/listing (CR-SAN-015).
- Any change to Sandesh-core, the CLI, the `notify` watcher, or the MCP surface.
