# CR-SAN-031 — Pi wake delivery hardening: `deliverAs: "followUp"`

**Status:** COMPLETED (shipped 2026-06-13 on develop)
**Priority:** High (shipped defect — wake messages lost while the agent is mid-turn)
**Depends on:** —
**Labels:** wave-8, pi, wake, bun
**Wave:** Wave 8 (Pi extension catch-up)
**Design reference:** docs/research/PRD-pi-extension.md (PE11; §5/W1)
**Stack:** TypeScript/bun — `integrations/pi/` (`bun test`)

## Context

Source verification against `earendil-works/pi@main` (PE11, 2026-06-13) found that the
Wave-4 wake loop's `pi.sendUserMessage(msg)` call carries no options. Pi's
`AgentSession.prompt()` THROWS when the agent is streaming and no `streamingBehavior` is
given; the throw is swallowed at Pi's wiring site (`runner.emitError`), so from the
extension's side the call silently does nothing — the wake message is lost, the loop
re-arms `notify` (which exits 0 instantly while mail stays unread) and hot-spins until the
agent goes idle. `{deliverAs: "followUp"}` is ignored on the idle path (immediate turn) and
queues a follow-up when busy — correct in both states.

## Scope

- The wake loop's exit-0 branch (`integrations/pi/src/index.ts`, `wakeLoop`) calls
  `pi.sendUserMessage(<unchanged message text>, { deliverAs: "followUp" })`.
- Defensive: a synchronously-throwing `sendUserMessage` must not kill the loop — the
  exit-0 branch survives and re-arms (today the real Pi wrapper is void/catching; the
  guard is against host variations).
- No other wake-loop behavior changes: exit 0/2 re-arm, 3/4/5 terminal, default backoff —
  all byte-identical.

## Acceptance criteria

- [ ] **AC1 — options pin.** On a notify exit-0, the `sendUserMessage` mock is called
      exactly once with the existing message text AND an options argument whose
      `deliverAs` is `"followUp"` (asserted on the mock's call args).
- [ ] **AC2 — loop survives a throwing send.** With a `sendUserMessage` mock that throws
      synchronously, the loop does not crash: the next `notify` is still armed (the exec
      mock sees the subsequent invocation) and a later exit-3 still terminates cleanly.
- [ ] **AC3 — regression.** The full `integrations/pi` suite (`bun test`) is green; no
      existing wake-lifecycle assertion changes except those updated for AC1's new
      argument.

## Estimated size
Tiny — one call-site change + targeted tests.

## Risks / open questions
- (none — the options shape is pinned from Pi's `types.ts` `sendUserMessage` signature.)

## Non-goals
- The surface catch-up (tools/params/gate/docs — CR-SAN-032); any `deliverAs: "steer"`
  path (PE11: mail must not hijack an in-flight turn); changes to notify exit-code
  semantics.
