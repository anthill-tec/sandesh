/**
 * CR-SAN-014 C0 — RED: wake loop core (AC1/AC2/AC3)
 *
 * Testable seam assumed for the detached async loop:
 *   The GREEN implementation must export a module-level function (or the
 *   session_start handler must accept it via a module-level setter):
 *
 *     export function __setWakeSleepFn(fn: (ms: number) => Promise<void>): void
 *
 *   This allows tests to inject a no-op sleep so the backoff path (error code →
 *   backoff + re-arm) does not use real timers and the test terminates quickly.
 *
 *   The loop is fire-and-forget from the session_start handler. Tests drive it
 *   to a terminal state by scripting pi.exec to eventually return a terminal
 *   exit code (3 or 5), then drain the microtask queue (setImmediate/setTimeout 0)
 *   before asserting. This is sufficient because pi.exec is synchronously
 *   resolved by the mock — no real I/O happens.
 *
 * ACs tested:
 *   AC1 — probe-gated start: wake loop arms notify ONLY after a successful
 *         sandesh --version probe; failed probe → no notify exec.
 *   AC2 — code:0 → sendUserMessage (once, with fetch-prompting msg) then re-arm.
 *   AC3 — code:2 → re-arm, no sendUserMessage; code:3/5 → stop; code:1 → backoff+re-arm.
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type SessionShutdownHandler = ExtensionHandler<SessionShutdownEvent>;

/** A scriptable sequence of exec results. Each call to exec pops the next entry. */
function makeExecSequence(
  sequence: Array<ExecResult | "reject">,
): (cmd: string, args: string[], opts?: unknown) => Promise<ExecResult> {
  let index = 0;
  return async (_cmd, _args, _opts) => {
    const entry = sequence[index];
    // Clamp to last entry if we run past the scripted sequence (shouldn't happen in well-formed tests)
    if (index < sequence.length - 1) index++;
    if (entry === "reject") {
      throw new Error("sandesh: command not found");
    }
    return entry;
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
// Fake Pi harness
// ---------------------------------------------------------------------------

interface FakePiOptions {
  /** Scripted exec results in order. First call gets index 0, etc. */
  execSequence: Array<ExecResult | "reject">;
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
    fireShutdown: async () => {
      if (sessionShutdownHandler) {
        const fakeShutdownEvent: SessionShutdownEvent = {
          type: "session_shutdown",
        } as SessionShutdownEvent;
        await sessionShutdownHandler(fakeShutdownEvent, makeFakeCtx().fakeCtx);
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
// Drain microtasks — give the detached loop a chance to execute.
// We use multiple rounds to handle async chains of arbitrary depth
// (probe → loop iteration → sendUserMessage → re-arm → terminal code → stop).
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
});

afterEach(() => {
  // Restore env
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
// AC1 — probe-gated start
// ---------------------------------------------------------------------------

describe("AC1 — probe-gated start: wake loop arms after successful probe only", () => {
  test("AC1a — successful probe + env set → notify exec is called with correct argv", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    // Sequence: [0] version probe succeeds; [1] notify exits terminal (3 → stop)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    expect(startCall).toBeDefined();
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // The notify call should have been made
    const allCalls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = allCalls.find(
      ([cmd, args]) => cmd === "sandesh" && args.includes("notify"),
    );
    expect(notifyCall).toBeDefined();
    expect(notifyCall![0]).toBe("sandesh");
    expect(notifyCall![1]).toContain("--project");
    expect(notifyCall![1]).toContain("TestProj");
    expect(notifyCall![1]).toContain("notify");
    expect(notifyCall![1]).toContain("--to");
    expect(notifyCall![1]).toContain("Mainline - TestProj");
  });

  test("AC1b — notify argv is exactly ['--project', P, 'notify', '--to', self]", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - MyProject";
    process.env.SANDESH_PROJECT = "MyProject";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(5)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    const calls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = calls.find(([, args]) => args.includes("notify"));
    expect(notifyCall).toBeDefined();
    // Exact argv: ["--project", "MyProject", "notify", "--to", "Track 1 - MyProject"]
    expect(notifyCall![1]).toEqual(["--project", "MyProject", "notify", "--to", "Track 1 - MyProject"]);
  });

  test("AC1c — failed probe (non-zero) → no notify exec called, only install notice", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    // Probe fails — only the version call should appear, no notify
    const { fakePi, execMock } = makeFakePi({
      execSequence: [exit(127)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // exec must NOT have been called with notify
    const calls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = calls.find(([, args]) => args.includes("notify"));
    expect(notifyCall).toBeUndefined();
    // The install notice must have been surfaced
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("AC1d — failed probe (exec throws) → no notify exec called, only install notice", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    const { fakePi, execMock } = makeFakePi({
      execSequence: ["reject"],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    const calls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = calls.find(([, args]) => Array.isArray(args) && args.includes("notify"));
    expect(notifyCall).toBeUndefined();
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("AC1e — env unset (no SANDESH_ADDRESS) → loop does not start (no notify exec)", async () => {
    delete process.env.SANDESH_ADDRESS;
    process.env.SANDESH_PROJECT = "TestProj";

    // Probe would succeed, but env is missing
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    const calls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = calls.find(([, args]) => Array.isArray(args) && args.includes("notify"));
    expect(notifyCall).toBeUndefined();
  });

  test("AC1f — env unset (no SANDESH_PROJECT) → loop does not start (no notify exec)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    delete process.env.SANDESH_PROJECT;

    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0")],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    const calls = execMock.mock.calls as Array<[string, string[], unknown?]>;
    const notifyCall = calls.find(([, args]) => Array.isArray(args) && args.includes("notify"));
    expect(notifyCall).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// AC2 — mail (code:0) → sendUserMessage + re-arm
// ---------------------------------------------------------------------------

describe("AC2 — notify code:0 → sendUserMessage once + re-arm", () => {
  test("AC2a — code:0 then terminal(3) → sendUserMessage called exactly once", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // sequence: probe ok → notify code:0 (mail) → re-arm → notify code:3 (stop)
    const { fakePi, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(0), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startCall = onCalls.find(([e]) => e === "session_start");
    const startHandler = startCall![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(1);
  });

  test("AC2b — sendUserMessage content references 'sandesh_fetch'", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(0), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBeGreaterThanOrEqual(1);
    const [content] = sendUserMessageMock.mock.calls[0] as [string, unknown?];
    expect(typeof content).toBe("string");
    expect((content as string).toLowerCase()).toContain("sandesh_fetch");
  });

  test("AC2c — sendUserMessage content mentions self address", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(0), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    const [content] = sendUserMessageMock.mock.calls[0] as [string, unknown?];
    // The message should mention the self address so the agent knows who to fetch for
    expect(content as string).toContain("Mainline - Demo");
  });

  test("AC2d — after code:0, loop re-arms (a 2nd notify exec is called)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(0, mail) → re-arm notify(3, stop)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(0), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // Count notify calls (not the version probe)
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    // Must have been called at least twice (first arm + re-arm)
    expect(notifyCalls.length).toBeGreaterThanOrEqual(2);
  });

  test("AC2e — two code:0 results → sendUserMessage called twice (each mail triggers once)", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(0) → notify(0) → notify(5, stop)
    const { fakePi, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(0), exit(0), exit(5)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// AC3 — exit-code branches
// ---------------------------------------------------------------------------

describe("AC3 — exit-code branches", () => {
  test("AC3a — code:2 (timeout) → re-arms silently, NO sendUserMessage", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(2, timeout) → re-arm → notify(3, stop)
    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(2), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // No sendUserMessage on timeout
    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    // Loop re-armed after code:2 (≥2 notify calls total)
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBeGreaterThanOrEqual(2);
  });

  test("AC3b — code:3 (tombstoned) → loop stops immediately (no further notify exec)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(3, stop)
    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    // Exactly 1 notify call (no re-arm)
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBe(1);
  });

  test("AC3c — code:4 (evicted) → loop stops (no further notify exec)", async () => {
    process.env.SANDESH_ADDRESS = "Track 2 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(4)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBe(1);
  });

  test("AC3d — code:5 (dedup) → loop stops (no further notify exec)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(5)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBe(1);
  });

  test("AC3e — code:1 (error) → backoff then re-arm (loop does NOT stop on error)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(1, error) → [backoff] → re-arm → notify(3, stop)
    // GREEN must inject/use a no-op sleep via __setWakeSleepFn or equivalent
    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(1), exit(3)],
    });

    // Inject no-op sleep BEFORE registering — GREEN must export __setWakeSleepFn
    // Import path: the default export is registerExtension; sleep injection seam:
    //   import { __setWakeSleepFn } from "./index";
    //   __setWakeSleepFn(async () => {});
    // Since the function doesn't exist yet (RED), this test will fail.
    // The GREEN agent MUST implement this seam.
    const indexModule = await import("./index");
    type WithSleepSeam = { __setWakeSleepFn?: (fn: () => Promise<void>) => void };
    const setWakeSleep = (indexModule as unknown as WithSleepSeam).__setWakeSleepFn;
    if (typeof setWakeSleep === "function") {
      setWakeSleep(async () => {});
    }
    // Even if __setWakeSleepFn is absent, the test asserts the re-arm behaviour:
    // with the real sleep the backoff adds real delay, but the test might still
    // pass if bun awaits long enough — however for CI reliability the seam is required.

    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    // Need more rounds to drain backoff + re-arm
    await drainMicrotasks(50);

    // No sendUserMessage (error doesn't deliver mail)
    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    // Loop re-armed after code:1 (at least 2 notify calls)
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBeGreaterThanOrEqual(2);
  });

  test("AC3f — code:99 (unknown error) → backoff then re-arm (loop does NOT stop)", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(99), exit(5)],
    });

    const indexModule = await import("./index");
    type WithSleepSeam = { __setWakeSleepFn?: (fn: () => Promise<void>) => void };
    const setWakeSleep2 = (indexModule as unknown as WithSleepSeam).__setWakeSleepFn;
    if (typeof setWakeSleep2 === "function") {
      setWakeSleep2(async () => {});
    }

    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks(50);

    expect(sendUserMessageMock.mock.calls.length).toBe(0);

    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    // Re-armed after unknown error
    expect(notifyCalls.length).toBeGreaterThanOrEqual(2);
  });

  test("AC3g — mixed sequence: code:2, code:0, code:2, code:3 → 1 sendUserMessage, loop stops", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(2) → notify(0, mail) → notify(2) → notify(3, stop)
    const { fakePi, execMock, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(2), exit(0), exit(2), exit(3)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    // Exactly 1 sendUserMessage (only for code:0)
    expect(sendUserMessageMock.mock.calls.length).toBe(1);

    // Loop stopped after code:3 (4 notify calls: arm, re-arm×3)
    const notifyCalls = (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
      ([, args]) => Array.isArray(args) && args.includes("notify"),
    );
    expect(notifyCalls.length).toBe(4);
  });

  test("AC3h — code:2 re-arm does not call sendUserMessage", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // probe → notify(2) → notify(5, stop)
    const { fakePi, sendUserMessageMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(2), exit(5)],
    });
    registerExtension(fakePi);

    const onCalls = (fakePi.on as ReturnType<typeof mock>).mock.calls as Array<[string, unknown]>;
    const startHandler = (onCalls.find(([e]) => e === "session_start")![1]) as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await drainMicrotasks();

    expect(sendUserMessageMock.mock.calls.length).toBe(0);
  });
});
