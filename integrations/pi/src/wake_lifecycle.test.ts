/**
 * CR-SAN-014 C1 — RED: lifecycle + env-gating (AC4/AC5)
 *
 * These tests MUST FAIL before GREEN implements:
 *   1. A `session_shutdown` handler registered via `pi.on("session_shutdown", ...)`.
 *   2. An AbortController held by the wake loop; its signal threaded into `pi.exec notify`
 *      as the 3rd arg `{ signal: <AbortSignal> }`.
 *   3. `abort()` called on shutdown; stopped flag checked so no re-arm occurs after shutdown.
 *   4. A module-level single-loop guard preventing a second concurrent loop on double
 *      `session_start`.
 *   5. A one-time `ctx.ui.notify(...)` notice when probe succeeds but
 *      `$SANDESH_ADDRESS` / `$SANDESH_PROJECT` are unset (AC4).
 *
 * Seams required of GREEN (in addition to the existing __setWakeSleepFn):
 *
 *   export function __resetWakeState(): void
 *
 *   This resets the module-level single-loop guard between tests so the guard does
 *   not bleed across test cases. Mirrors the __setWakeSleepFn pattern.
 *
 *   The `pi.exec` call for `sandesh notify` MUST pass a 3rd options arg carrying
 *   an AbortSignal:
 *     pi.exec("sandesh", [..., "notify", "--to", self], { signal: <AbortSignal> })
 *
 *   The `session_shutdown` handler must:
 *     1. Call abort() on the AbortController.
 *     2. Set the stopped flag so no further re-arm occurs.
 *
 * ACs tested:
 *   AC4 — missing-env notice: when probe succeeds but $SANDESH_ADDRESS or
 *         $SANDESH_PROJECT is unset, ctx.ui.notify is called with a message naming
 *         those env vars; the wake loop does NOT start.
 *   AC5 — session_shutdown stops the loop (no re-arm after abort); only one loop
 *         runs per session (double session_start does not spawn two concurrent loops);
 *         pi.exec notify receives a { signal } options arg.
 */

import { test, expect, describe, mock, beforeEach, afterEach } from "bun:test";
import type {
  ExtensionAPI,
  ExtensionContext,
  ExecResult,
  SessionStartEvent,
  SessionShutdownEvent,
  ExtensionHandler,
} from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { __setWakeSleepFn } from "./index";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type SessionShutdownHandler = ExtensionHandler<SessionShutdownEvent>;

// ---------------------------------------------------------------------------
// Helpers — exec sequence, fake ExecResults
// ---------------------------------------------------------------------------

/** A scriptable sequence of exec results. Each call to exec pops the next entry. */
function makeExecSequence(
  sequence: Array<ExecResult | "reject" | "hang">,
): (cmd: string, args: string[], opts?: unknown) => Promise<ExecResult> {
  let index = 0;
  return async (_cmd, _args, _opts) => {
    const entry = sequence[index];
    if (index < sequence.length - 1) index++;
    if (entry === "reject") {
      throw new Error("sandesh: command not found");
    }
    if (entry === "hang") {
      // Simulate a long-blocking notify — resolves only after abort signal fires
      // or after a generous timeout. Tests that use "hang" should fire shutdown.
      return new Promise<ExecResult>((resolve) => {
        const opts = _opts as { signal?: AbortSignal } | undefined;
        if (opts?.signal) {
          opts.signal.addEventListener("abort", () => {
            resolve({ stdout: "", stderr: "aborted", code: 1, killed: true });
          });
        } else {
          // No signal provided — resolve as error after microtask drain
          setTimeout(() => resolve({ stdout: "", stderr: "no signal", code: 1, killed: false }), 0);
        }
      });
    }
    return entry as ExecResult;
  };
}

/** Minimal ok ExecResult */
function ok(stdout = "", stderr = "", code = 0): ExecResult {
  return { stdout, stderr, code, killed: false };
}

/** ExecResult with a specific exit code */
function exit(code: number): ExecResult {
  return { stdout: "", stderr: `exit ${code}`, code, killed: false };
}

// ---------------------------------------------------------------------------
// Fake Pi harness (captures both session_start and session_shutdown handlers)
// ---------------------------------------------------------------------------

interface FakePiOptions {
  execSequence: Array<ExecResult | "reject" | "hang">;
}

function makeFakePi(opts: FakePiOptions) {
  const capturedTools = new Map<string, unknown>();
  let sessionStartHandler: SessionStartHandler | undefined;
  let sessionShutdownHandler: SessionShutdownHandler | undefined;

  const execMock = mock(makeExecSequence(opts.execSequence));

  const sendUserMessageMock = mock(
    (_content: string | unknown[], _opts?: unknown): void => {
      // no-op
    },
  );

  const onMock = mock((event: string, handler: unknown) => {
    if (event === "session_start") {
      sessionStartHandler = handler as SessionStartHandler;
    } else if (event === "session_shutdown") {
      sessionShutdownHandler = handler as SessionShutdownHandler;
    }
  });

  const fakePi = {
    registerTool: mock((tool: { name: string }) => {
      capturedTools.set(tool.name, tool);
    }),
    on: onMock,
    exec: execMock,
    sendUserMessage: sendUserMessageMock,
  } as unknown as ExtensionAPI;

  return {
    fakePi,
    capturedTools,
    execMock,
    sendUserMessageMock,
    onMock,
    getSessionStartHandler: () => sessionStartHandler,
    getSessionShutdownHandler: () => sessionShutdownHandler,
    fireShutdown: async (ctx: ExtensionContext) => {
      if (sessionShutdownHandler) {
        const fakeShutdownEvent: SessionShutdownEvent = {
          type: "session_shutdown",
        } as SessionShutdownEvent;
        await sessionShutdownHandler(fakeShutdownEvent, ctx);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Fake ctx harness
// ---------------------------------------------------------------------------

function makeFakeCtx() {
  const notifyCalls: Array<{ msg: string; type?: string }> = [];
  const fakeCtx = {
    ui: {
      notify: mock((msg: string, type?: "info" | "warning" | "error") => {
        notifyCalls.push({ msg, type });
      }),
    },
  } as unknown as ExtensionContext;
  return { fakeCtx, notifyCalls };
}

/** Minimal fake session_start event */
const fakeSessionStartEvent: SessionStartEvent = {
  type: "session_start",
} as SessionStartEvent;

// ---------------------------------------------------------------------------
// Drain microtasks
// ---------------------------------------------------------------------------

async function drainMicrotasks(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

// ---------------------------------------------------------------------------
// Env setup / teardown
// ---------------------------------------------------------------------------

const SAVED_ENV: Partial<Record<string, string>> = {};

beforeEach(() => {
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;

  // Inject no-op sleep so backoff paths don't use real timers
  __setWakeSleepFn(async () => {});

  // Reset the module-level single-loop guard between tests.
  // GREEN must export __resetWakeState() — until then this is a no-op
  // (imported dynamically so a missing export doesn't fail the import).
  const indexModule = require("./index") as Record<string, unknown>;
  if (typeof indexModule.__resetWakeState === "function") {
    (indexModule.__resetWakeState as () => void)();
  }
});

afterEach(() => {
  if (SAVED_ENV.SANDESH_ADDRESS === undefined) {
    delete process.env.SANDESH_ADDRESS;
  } else {
    process.env.SANDESH_ADDRESS = SAVED_ENV.SANDESH_ADDRESS;
  }
  if (SAVED_ENV.SANDESH_PROJECT === undefined) {
    delete process.env.SANDESH_PROJECT;
  } else {
    process.env.SANDESH_PROJECT = SAVED_ENV.SANDESH_PROJECT;
  }
});

// ---------------------------------------------------------------------------
// AC4 — missing-env notice
// ---------------------------------------------------------------------------

describe("AC4 — missing-env notice when probe succeeds but env vars unset", () => {
  test("AC4a — SANDESH_ADDRESS unset → ctx.ui.notify called with a message naming $SANDESH_ADDRESS", async () => {
    delete process.env.SANDESH_ADDRESS;
    process.env.SANDESH_PROJECT = "TestProj";

    // Probe would succeed, but address env is missing
    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    expect(startCall).toBeDefined();
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // A notice MUST have been surfaced via ctx.ui.notify
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);

    // The notice must name $SANDESH_ADDRESS so the user knows what to set
    const noticeText = notifyCalls.map((n) => n.msg).join(" ");
    expect(noticeText).toContain("SANDESH_ADDRESS");
  });

  test("AC4b — SANDESH_PROJECT unset → ctx.ui.notify called with a message naming $SANDESH_PROJECT", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    delete process.env.SANDESH_PROJECT;

    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);

    const noticeText = notifyCalls.map((n) => n.msg).join(" ");
    expect(noticeText).toContain("SANDESH_PROJECT");
  });

  test("AC4c — both env vars unset → notice surfaced (names both $SANDESH_ADDRESS and $SANDESH_PROJECT)", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;

    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    // Combined notice text must name both env vars
    const noticeText = notifyCalls.map((n) => n.msg).join(" ");
    expect(noticeText).toContain("SANDESH_ADDRESS");
    expect(noticeText).toContain("SANDESH_PROJECT");
  });

  test("AC4d — missing-env notice is distinct from the missing-CLI notice (probe succeeded)", async () => {
    delete process.env.SANDESH_ADDRESS;
    process.env.SANDESH_PROJECT = "TestProj";

    // Probe succeeds (code:0) — so the missing-CLI notice must NOT appear
    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // The notice must NOT be the install-CLI notice (which would mention installation)
    // It should be the env-vars notice
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    const noticeText = notifyCalls.map((n) => n.msg).join(" ");
    // Must name the env vars (not just "install sandesh")
    expect(noticeText).toContain("SANDESH_ADDRESS");
  });

  test("AC4e — missing env → no notify exec, notice surfaced naming SANDESH_ADDRESS, verbs still registered (12 tools)", async () => {
    delete process.env.SANDESH_ADDRESS;
    process.env.SANDESH_PROJECT = "TestProj";

    const { fakePi, execMock, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // No notify exec (C0 already ensured this; kept as regression guard)
    const wakeNotifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(wakeNotifyCalls.length).toBe(0);

    // Verbs still registered (12 tools after CR-SAN-032)
    expect(capturedTools.size).toBe(12);

    // C1 NEW: a notice MUST have been surfaced via ctx.ui.notify naming $SANDESH_ADDRESS
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    const noticeText = notifyCalls.map((n) => n.msg).join(" ");
    expect(noticeText).toContain("SANDESH_ADDRESS");
  });
});

// ---------------------------------------------------------------------------
// AC5 — session_shutdown stops the loop
// ---------------------------------------------------------------------------

describe("AC5 — session_shutdown: registered handler, stops loop, no re-arm after abort", () => {
  test("AC5a — registerExtension registers a session_shutdown handler via pi.on", () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, onMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(3)],
    });
    registerExtension(fakePi);

    // pi.on must have been called with "session_shutdown"
    const onCalls = (onMock as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const shutdownCall = onCalls.find(([e]) => e === "session_shutdown");
    expect(shutdownCall).toBeDefined();
    expect(typeof shutdownCall![1]).toBe("function");
  });

  test("AC5b — shutdown handler invoked after loop starts → loop stops re-arming (notify call count frozen)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Sequence: probe → notify(2, timeout/re-arm) × many — loop keeps running
    // until we fire shutdown. Use "hang" for the second notify so it blocks
    // waiting for the abort signal, then resolves as error (code:1) — but the
    // stopped flag should prevent re-arm after shutdown.
    const { fakePi, execMock, getSessionShutdownHandler } = makeFakePi({
      execSequence: [
        ok("sandesh 1.0.0"), // probe
        ok(""),              // init --check (provisioned)
        exit(2),             // first notify: timeout → re-arm
        "hang",              // second notify: blocks until aborted
        exit(3),             // fallback (should not be reached)
      ],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);

    // Let the loop advance past the first notify (code:2 → re-arm → hits "hang")
    await drainMicrotasks(10);

    // Capture notify call count at the moment of shutdown
    const countAtShutdown = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    ).length;

    // Fire shutdown
    await (getSessionShutdownHandler() as SessionShutdownHandler)(
      { type: "session_shutdown" } as SessionShutdownEvent,
      fakeCtx,
    );

    // Drain further — loop must NOT re-arm after shutdown
    await drainMicrotasks(20);

    const countAfterShutdown = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    ).length;

    // Count must not have grown beyond countAtShutdown + 1 (the aborted "hang" call itself)
    expect(countAfterShutdown).toBeLessThanOrEqual(countAtShutdown + 1);
  });

  test("AC5c — pi.exec notify call receives a { signal } options arg (AbortSignal threaded)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → init --check(0) → notify(3, stop immediately so test is deterministic)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), ok(""), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // Find the notify exec call and assert it received a 3rd options arg with a signal
    const calls = execMock.mock.calls as Array<[string, string[], unknown]>;
    const notifyCall = calls.find(([, args]) => Array.isArray(args) && args.includes("notify"));
    expect(notifyCall).toBeDefined();

    // The 3rd argument MUST be present and carry a signal property
    const opts = notifyCall![2] as { signal?: unknown } | undefined;
    expect(opts).toBeDefined();
    expect(opts).not.toBeNull();
    expect(opts!.signal).toBeDefined();
    // The signal must be an AbortSignal instance
    expect(opts!.signal).toBeInstanceOf(AbortSignal);
  });

  test("AC5d — AbortSignal is aborted after session_shutdown fires", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // We need to capture the AbortSignal passed into pi.exec notify, then
    // verify it is aborted after shutdown.
    let capturedSignal: AbortSignal | undefined;

    // Custom exec mock that captures the signal from the notify call
    const execMock = mock(async (cmd: string, args: string[], opts?: { signal?: AbortSignal }) => {
      if (cmd === "sandesh" && Array.isArray(args) && args.includes("notify")) {
        capturedSignal = opts?.signal;
        // Hang until aborted
        return new Promise<ExecResult>((resolve) => {
          if (opts?.signal) {
            opts.signal.addEventListener("abort", () => {
              resolve({ stdout: "", stderr: "aborted", code: 1, killed: true });
            });
          } else {
            setTimeout(() => resolve({ stdout: "", stderr: "no signal", code: 1, killed: false }), 0);
          }
        });
      }
      // Version probe
      return { stdout: "sandesh 1.0.0", stderr: "", code: 0, killed: false };
    });

    let sessionStartHandler: SessionStartHandler | undefined;
    let sessionShutdownHandler: SessionShutdownHandler | undefined;

    const fakePi = {
      registerTool: mock((_tool: unknown) => {}),
      on: mock((event: string, handler: unknown) => {
        if (event === "session_start") sessionStartHandler = handler as SessionStartHandler;
        if (event === "session_shutdown") sessionShutdownHandler = handler as SessionShutdownHandler;
      }),
      exec: execMock,
      sendUserMessage: mock((_content: unknown) => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);

    const { fakeCtx } = makeFakeCtx();
    await sessionStartHandler!(fakeSessionStartEvent, fakeCtx);

    // Let the loop reach the notify call
    await drainMicrotasks(5);

    // Signal not yet aborted
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);

    // Fire shutdown
    await sessionShutdownHandler!(
      { type: "session_shutdown" } as SessionShutdownEvent,
      fakeCtx,
    );

    // Now the signal must be aborted
    expect(capturedSignal!.aborted).toBe(true);
  });

  test("AC5e — after shutdown, no further pi.exec notify calls occur (stopped flag prevents re-arm)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Use custom exec to track calls and resolve notify immediately (code:2) until
    // shutdown fires, then stop counting.
    let notifyCallCount = 0;
    let shutdownFired = false;

    const execMock = mock(async (cmd: string, args: string[]) => {
      if (cmd === "sandesh" && Array.isArray(args) && args.includes("--version")) {
        return { stdout: "sandesh 1.0.0", stderr: "", code: 0, killed: false };
      }
      if (cmd === "sandesh" && Array.isArray(args) && args.includes("notify")) {
        notifyCallCount++;
        if (shutdownFired) {
          // This call should never happen — fail loudly if it does
          throw new Error("notify called after shutdown — single-loop guard failed");
        }
        // Return timeout (code:2 → re-arm) for the first call, then hang
        if (notifyCallCount === 1) {
          return { stdout: "", stderr: "", code: 2, killed: false };
        }
        // Second call: hang, waiting for abort
        return new Promise<ExecResult>((resolve) => {
          setTimeout(() => {
            // If shutdown not fired by now, resolve as terminal to prevent infinite hang
            resolve({ stdout: "", stderr: "timeout", code: 3, killed: false });
          }, 50);
        });
      }
      return { stdout: "", stderr: "", code: 0, killed: false };
    });

    let sessionStartHandler: SessionStartHandler | undefined;
    let sessionShutdownHandler: SessionShutdownHandler | undefined;

    const fakePi = {
      registerTool: mock((_tool: unknown) => {}),
      on: mock((event: string, handler: unknown) => {
        if (event === "session_start") sessionStartHandler = handler as SessionStartHandler;
        if (event === "session_shutdown") sessionShutdownHandler = handler as SessionShutdownHandler;
      }),
      exec: execMock,
      sendUserMessage: mock((_content: unknown) => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);

    const { fakeCtx } = makeFakeCtx();
    await sessionStartHandler!(fakeSessionStartEvent, fakeCtx);

    // Let the loop advance at least one iteration (code:2 → re-arm)
    await drainMicrotasks(10);

    const countBeforeShutdown = notifyCallCount;
    expect(countBeforeShutdown).toBeGreaterThanOrEqual(1);

    // Mark shutdown as fired before invoking the handler
    shutdownFired = true;
    await sessionShutdownHandler!(
      { type: "session_shutdown" } as SessionShutdownEvent,
      fakeCtx,
    );

    // Drain and verify no new calls beyond what was already in-flight
    await drainMicrotasks(20);
    // notifyCallCount may have gone to 2 (the in-flight "hang" arm), but not 3+
    expect(notifyCallCount).toBeLessThanOrEqual(countBeforeShutdown + 1);
  });
});

// ---------------------------------------------------------------------------
// AC5 — single-loop guard
// ---------------------------------------------------------------------------

describe("AC5 — single-loop guard: double session_start does not spawn two concurrent loops", () => {
  test("AC5f — session_start fired twice → only one loop runs (notify call count matches single-loop pattern)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Both session_start fires share the same exec sequence.
    // A single loop: probe(ok) → notify(5, stop). Total notify calls: 1.
    // Two loops: probe(ok) × 2 → notify(5) × 2. Total notify calls: 2+.
    //
    // We script the sequence for a SINGLE pass. If a second loop starts, the
    // exec mock will run past the scripted entries and clamp to the last (exit(5)),
    // but the notify count will exceed 1 — revealing the double-start bug.
    const { fakePi, execMock } = makeFakePi({
      // Enough entries for ONE probe + init --check + ONE notify (terminal). If two
      // loops run concurrently, the second loop's notify call reveals it.
      execSequence: [
        ok("sandesh 1.0.0"), // probe for first session_start
        ok(""),              // init --check for first session_start (provisioned)
        exit(5),             // notify for first loop → stops
        ok("sandesh 1.0.0"), // probe for second session_start (guard must prevent loop)
        ok(""),              // init --check for second session_start
      ],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();

    // First session_start — starts the loop
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks(10);

    // Second session_start — guard must prevent a second loop
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks(10);

    // Count notify calls: a single loop produces exactly 1 (stopped by exit(5))
    // Two concurrent loops would produce 2.
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBe(1);
  });

  test("AC5g — single-loop guard: __resetWakeState() exported and resets the guard between tests", () => {
    // This test documents the required seam.
    // GREEN must export __resetWakeState from index.ts.
    // If the export is absent, this test will fail — surfacing the missing seam.
    const indexModule = require("./index") as Record<string, unknown>;
    expect(typeof indexModule.__resetWakeState).toBe("function");
  });

  test("AC5h — __resetWakeState must be exported AND functional: after reset, single-loop guard allows a new loop", async () => {
    // This test FAILS RED because:
    //   (a) __resetWakeState is not yet exported (AC5g covers that), AND
    //   (b) without a guard, the second session_start in AC5f already launches a second loop
    //       CONCURRENTLY — meaning the guard logic itself is absent.
    //
    // GREEN must:
    //   1. Export __resetWakeState().
    //   2. Implement a module-level "loop running" flag that __resetWakeState() clears.
    //   3. The session_start handler checks the flag — if set, skips starting a new loop.
    //
    // The assertion here verifies the COMPLETE contract: guard exists, reset works,
    // and a post-reset second session_start starts exactly one more loop (not two concurrent).
    //
    // Strategy: use a guard-check assertion that REQUIRES __resetWakeState to exist.
    // Without it, the require() check fails the test.
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const indexModule = require("./index") as Record<string, unknown>;

    // AC5h requires the seam to exist — fail immediately if missing
    expect(typeof indexModule.__resetWakeState).toBe("function");
    const resetWakeState = indexModule.__resetWakeState as () => void;

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 1.0.0"), // probe for first session_start
        ok(""),              // init --check for first session_start (provisioned)
        exit(5),             // notify → stop (first loop ends naturally; guard becomes "stopped")
        ok("sandesh 1.0.0"), // probe for second session_start (after guard reset)
        ok(""),              // init --check for second session_start
        exit(5),             // notify → stop (second loop)
      ],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();

    // First session_start — loop runs and stops (exit:5)
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks(10);

    // Verify exactly 1 notify call consumed so far
    const firstPassNotifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(firstPassNotifyCalls.length).toBe(1);

    // Reset the guard so a new loop can start
    resetWakeState();

    // Second session_start — should be allowed to start a fresh loop
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks(10);

    // Both loops should have run sequentially: 2 notify calls total (one per loop)
    const allNotifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(allNotifyCalls.length).toBe(2);
  });
});
