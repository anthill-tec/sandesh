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

### §S1 — The wake loop (design W1)
In the extension, on **`pi.on("session_start", …)`**, start a **detached async loop** (fire-and-forget,
not awaited by any turn) that arms the doorbell and translates its exit into a Pi turn:
- `const r = await pi.exec("sandesh", ["--project", P, "notify", "--to", self])` — blocks until the
  address has unread `to` mail (or timeout). Reuses Sandesh's liveness-aware watcher + exit-code
  contract unchanged.
- Branch on `r.code`:
  - **`0` (mail)** → `pi.sendUserMessage("You have unread Sandesh mail — call sandesh_fetch for
    \"<self>\", then act on it.")` (always triggers a turn), then re-arm.
  - **`2` (timeout)** → re-arm silently.
  - **`3` (tombstoned) / `4` (evicted)** → stop the loop (the address is being removed).
  - **`5` (dedup — a notifier already live)** → stop (another loop owns the doorbell).
  - **other (error)** → backoff + re-arm.

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
- Stop the loop on **`pi.on("session_shutdown", …)`** (abort the in-flight `pi.exec` via its
  `signal`; set a stopped flag so a pending re-arm doesn't restart).
- One loop per session: rely on Sandesh's own notifier dedup (exit `5`) for cross-process safety,
  plus an in-extension guard so `session_start` can't start two loops.

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

- [ ] **AC1** — on `session_start`, the extension starts a wake loop that calls
      `pi.exec("sandesh", [..., "notify", "--to", <self>])` with `self`/project from
      `$SANDESH_ADDRESS`/`$SANDESH_PROJECT` (asserted with mocked exec).
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

## Estimated size
Small–medium: one wake-loop module + lifecycle wiring + `bun test` unit tests over a mocked exec. The
design is fully specified by DN-pi-wake (W1); no research left.

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
