/**
 * CR-SAN-048 §S1b — RED: CLI version gate raised to MIN_CLI_VERSION [0,4,0] (AC1b).
 *
 * Every tool now passes `--format toon`, which a CLI below 0.4.0 rejects
 * with exit 2 — so the session_start version probe must reject anything
 * below 0.4.0 (not 0.2.0) and name "0.4.0" in the too-old notice.
 *
 * Harness mirrors `version_gate.test.ts`'s exec-sequence harness (makeFakePi
 * / makeFakeCtx / fireSessionStart), just with different probe stdout
 * fixtures. `MIN_CLI_VERSION` is not exported from `src/index.ts` today, so
 * the minimum is asserted via the notice text only, per spec.
 *
 * RED reason: today's gate constant is `[0, 2, 0]` and the notice names
 * "0.2.0" — so "sandesh 0.3.6" (below the NEW 0.4.0 minimum, above the OLD
 * 0.2.0 minimum) wrongly arms the wake loop today instead of taking the
 * too-old path.
 */

import { test, expect, describe, mock, beforeEach, afterEach } from "bun:test";
import type {
  ExtensionAPI,
  ExtensionContext,
  ExecResult,
  SessionStartEvent,
  ExtensionHandler,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ─── Types ───────────────────────────────────────────────────────────────────

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type CapturedTool = ToolDefinition<any, any, any>;

// ─── Exec-sequence harness (mirrors version_gate.test.ts / wake.test.ts) ────

function makeExecSequence(
  sequence: Array<ExecResult | "reject">,
): (cmd: string, args: string[], opts?: unknown) => Promise<ExecResult> {
  let index = 0;
  return async (_cmd, _args, _opts) => {
    const entry = sequence[index];
    if (index < sequence.length - 1) index++;
    if (entry === "reject") {
      throw new Error("sandesh: command not found");
    }
    return entry;
  };
}

function ok(stdout = "", stderr = "", code = 0): ExecResult {
  return { stdout, stderr, code, killed: false };
}

function exit(code: number, stderr = ""): ExecResult {
  return { stdout: "", stderr: stderr || `exit ${code}`, code, killed: false };
}

interface FakePiOptions {
  execSequence: Array<ExecResult | "reject">;
}

function makeFakePi(opts: FakePiOptions) {
  const capturedTools = new Map<string, CapturedTool>();
  let sessionStartHandler: SessionStartHandler | undefined;

  const execMock = mock(makeExecSequence(opts.execSequence));

  const onMock = mock((event: string, handler: unknown) => {
    if (event === "session_start") {
      sessionStartHandler = handler as SessionStartHandler;
    }
  });

  const fakePi = {
    registerTool: mock((tool: CapturedTool) => {
      capturedTools.set(tool.name, tool);
    }),
    on: onMock,
    exec: execMock,
    sendUserMessage: mock((_content: string | unknown[], _opts?: unknown): void => {}),
  } as unknown as ExtensionAPI;

  return {
    fakePi,
    capturedTools,
    execMock,
    getSessionStartHandler: () => sessionStartHandler,
  };
}

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

const fakeSessionStartEvent: SessionStartEvent = {
  type: "session_start",
} as SessionStartEvent;

async function drainMicrotasks(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

async function fireSessionStart(
  handler: SessionStartHandler,
  fakeCtx: ExtensionContext,
) {
  await handler(fakeSessionStartEvent, fakeCtx);
  await drainMicrotasks();
}

function countNotifyExecCalls(execMock: ReturnType<typeof mock>): number {
  return (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
    ([, args]) => Array.isArray(args) && args.includes("notify"),
  ).length;
}

// ─── Env save/restore ────────────────────────────────────────────────────────

const SAVED_ENV: Partial<Record<string, string>> = {};

beforeEach(() => {
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;
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

// ═══════════════════════════════════════════════════════════════════════════
// AC1b — version gate raised to 0.4.0
// ═══════════════════════════════════════════════════════════════════════════

describe("AC1b — version gate: MIN_CLI_VERSION is [0,4,0]", () => {
  test("probe stdout 'sandesh 0.3.6' (below the NEW 0.4.0 minimum) → too-old notice naming '0.4.0'", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, getSessionStartHandler } = makeFakePi({
      execSequence: [ok("sandesh 0.3.6"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler()!;
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    expect(notifyCalls.length).toBe(1);
    expect(notifyCalls[0].msg).toContain("0.4.0");
    expect(["warning", "error"]).toContain(notifyCalls[0].type ?? "");
    // Too old: the wake loop must not have armed (no `notify` exec call).
    expect(countNotifyExecCalls(execMock)).toBe(0);
  });

  test("probe stdout 'sandesh 0.4.0' (exact new minimum) → armed, no too-old notice", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, getSessionStartHandler } = makeFakePi({
      execSequence: [ok("sandesh 0.4.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler()!;
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const gateNotice = notifyCalls.find((c) => c.msg.includes("too old"));
    expect(gateNotice).toBeUndefined();
    expect(countNotifyExecCalls(execMock)).toBeGreaterThanOrEqual(1);
  });

  test("probe stdout 'sandesh 0.4.1' (above the new minimum) → armed, no too-old notice", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock, getSessionStartHandler } = makeFakePi({
      execSequence: [ok("sandesh 0.4.1"), exit(5)],
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler()!;
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const gateNotice = notifyCalls.find((c) => c.msg.includes("too old"));
    expect(gateNotice).toBeUndefined();
    expect(countNotifyExecCalls(execMock)).toBeGreaterThanOrEqual(1);
  });
});
