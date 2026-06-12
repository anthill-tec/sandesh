/**
 * CR-SAN-032 C2 — RED: version gate + error pins + docs markers (AC3, AC4, AC5)
 *
 * AC3 — version gate (§S3):
 *   The session-start probe parses `sandesh --version` stdout against
 *   `^sandesh (\d+)\.(\d+)\.(\d+)`. A version below 0.2.0 takes the
 *   missing-CLI path: one-time ctx.ui.notify warning naming the required
 *   minimum (0.2.0) + upgrade hint; wake loop NOT armed. Unparseable output
 *   counts as too-old. `sandesh 0.2.0` and `sandesh 0.3.1` arm normally.
 *   Missing-CLI path (non-zero / throw) is unchanged from today.
 *
 * AC4 — error passthrough pins (§S4):
 *   For each of the four Wave-6 refusal strings, a mocked non-zero exec
 *   makes the relevant tool throw an Error whose message CONTAINS the string
 *   verbatim (existing runSandesh shape: `sandesh <verb> failed (exit 1): <stderr>`).
 *   These may already pass (pure passthrough) — if so they are coverage pins.
 *
 * AC5 — docs markers (§S5 / PE10):
 *   1. package.json version === "0.2.0"  (RED: currently "0.1.0")
 *   2. inbox description / promptSnippet contains sender_project / proxy-stream wording
 *   3. fetch description / promptSnippet contains sender_project / proxy-stream wording
 *   4. search description / promptSnippet contains pagination / offset wording
 *
 * Harness: mirrors wake.test.ts (makeFakePi with execSequence / makeFakeCtx).
 * Note: existing tests feed `sandesh 1.0.0` as the probe stdout (≥0.2.0),
 * so those tests stay green under the version gate implementation.
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
import pkgJson from "../package.json";

// ─── Types ───────────────────────────────────────────────────────────────────

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type CapturedTool = ToolDefinition<any, any, any>;

// ─── Exec-sequence harness (mirrors wake.test.ts) ────────────────────────────

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

  const sendUserMessageMock = mock(
    (_content: string | unknown[], _opts?: unknown): void => {},
  );

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
    sendUserMessage: sendUserMessageMock,
  } as unknown as ExtensionAPI;

  return {
    fakePi,
    capturedTools,
    execMock,
    sendUserMessageMock,
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

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Run a session_start handler with env set and drain microtasks. */
async function fireSessionStart(
  handler: SessionStartHandler,
  fakeCtx: ExtensionContext,
) {
  await handler(fakeSessionStartEvent, fakeCtx);
  await drainMicrotasks();
}

function countNotifyCalls(
  execMock: ReturnType<typeof mock>,
): number {
  return (execMock.mock.calls as Array<[string, string[], unknown?]>).filter(
    ([, args]) => Array.isArray(args) && args.includes("notify"),
  ).length;
}

// ═══════════════════════════════════════════════════════════════════════════════
// AC3 — version gate
// ═══════════════════════════════════════════════════════════════════════════════

describe("AC3 — version gate: sandesh below 0.2.0 takes missing-CLI path", () => {
  /**
   * §S3: version below 0.2.0 → one-time ctx.ui.notify warning naming the
   * required minimum (0.2.0) + upgrade hint; wake loop NOT armed.
   * RED reason: today the probe only checks exit code 0; it does NOT parse
   * the version string, so 0.1.0 wrongly arms the wake loop.
   */

  test("AC3a — probe stdout 'sandesh 0.1.0' → warning notice fired", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    // Probe returns version 0.1.0 (below minimum); terminal exit-3 is spare
    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 0.1.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // A warning notice must have been fired (the version-too-old path)
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("AC3b — probe stdout 'sandesh 0.1.0' → notice names required minimum '0.2.0'", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 0.1.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Notice must name the required minimum version
    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    expect(notice.msg).toContain("0.2.0");
  });

  test("AC3c — probe stdout 'sandesh 0.1.0' → notice is warning severity", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 0.1.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    expect(["warning", "error"]).toContain(notice.type);
  });

  test("AC3d — probe stdout 'sandesh 0.1.0' → wake loop NOT armed (no notify exec)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";

    // Version too old — wake loop must NOT start regardless of terminal code presence
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 0.1.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // No notify exec must have been called
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3e — probe stdout 'sandesh 0.1.99' (still below 0.2.0) → too-old path", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 0.1.99"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Too-old path: notice fired, no notify exec
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3f — garbage stdout ('flooble') → treated as too-old (unparseable = too-old)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Unparseable output: does NOT match ^sandesh (\d+)\.(\d+)\.(\d+)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("flooble"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Too-old path: notice fired, no notify exec
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3g — garbage stdout ('flooble') → notice mentions 0.2.0", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [ok("flooble"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    expect(notice.msg).toContain("0.2.0");
  });

  test("AC3h — empty stdout → treated as too-old", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok(""), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3i — probe stdout 'sandesh 0.2.0' (exact minimum) → armed (notify exec called)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // 0.2.0 meets minimum — loop should arm; exit-3 terminates immediately
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // No version-gate notice (not the too-old path)
    const versionGateNotice = notifyCalls.find((c) => c.msg.includes("0.2.0"));
    expect(versionGateNotice).toBeUndefined();

    // Loop armed: notify exec was called
    expect(countNotifyCalls(execMock)).toBeGreaterThanOrEqual(1);
  });

  test("AC3j — probe stdout 'sandesh 0.3.1' (above minimum) → armed (notify exec called)", async () => {
    process.env.SANDESH_ADDRESS = "Track 1 - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 0.3.1"), exit(5)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const versionGateNotice = notifyCalls.find((c) => c.msg.includes("0.2.0"));
    expect(versionGateNotice).toBeUndefined();

    expect(countNotifyCalls(execMock)).toBeGreaterThanOrEqual(1);
  });

  test("AC3k — existing probe: 'sandesh 1.0.0' still arms the loop (no regression)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // All pre-existing tests use "sandesh 1.0.0" — must still arm
    const { fakePi, execMock } = makeFakePi({
      execSequence: [ok("sandesh 1.0.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const versionGateNotice = notifyCalls.find((c) => c.msg.includes("0.2.0"));
    expect(versionGateNotice).toBeUndefined();
    expect(countNotifyCalls(execMock)).toBeGreaterThanOrEqual(1);
  });

  test("AC3l — missing CLI (non-zero exit code) → install notice unchanged (not version gate)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Non-zero probe: missing-CLI path (same as before, no change)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [exit(127)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Missing-CLI notice fired, no notify exec
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3m — missing CLI (exec throws) → install notice unchanged (not version gate)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: ["reject"],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    expect(countNotifyCalls(execMock)).toBe(0);
  });

  test("AC3n — version 0.1.0: notice contains upgrade hint (names sandesh or upgrade command)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [ok("sandesh 0.1.0"), exit(3)],
    });
    registerExtension(fakePi);

    const handler = fakePi.on.mock.calls.find(
      ([e]: [string, unknown]) => e === "session_start",
    )![1] as SessionStartHandler;

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    // Must name sandesh and contain some upgrade signal
    const msg = notice.msg.toLowerCase();
    const hasUpgradeHint =
      msg.includes("upgrade") ||
      msg.includes("update") ||
      msg.includes("uv tool install") ||
      msg.includes("pipx") ||
      msg.includes("install.sh");
    expect(hasUpgradeHint).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AC4 — error passthrough pins (§S4 Wave-6 refusal strings)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * For each §S4 string the existing runSandesh contract:
 *   non-zero exit → throws Error(`sandesh <verb> failed (exit <N>): <stderr>`)
 * which means every error string is already surfaced verbatim IF production
 * code passes it straight through. These tests pin that contract so a future
 * shim-side rewrite of the error message doesn't silently drop it.
 *
 * They may already PASS (pure passthrough) — that is fine; they are coverage
 * pins. If any fails it reveals a production gap.
 */

describe("AC4 — error passthrough: project archived", () => {
  test("archive tool: stderr 'project \\'Demo\\' is archived' → Error contains string verbatim", async () => {
    const stderrMsg = "project 'Demo' is archived";
    const { fakePi } = makeFakePi({
      execSequence: [ok(stderrMsg, "", 1)],
    });
    registerExtension(fakePi);

    // Use a fresh harness with per-call exec result for the tool execute path
    // We need a separate static exec that always returns the error
    const capturedTools = new Map<string, CapturedTool>();
    const staticExec = mock(
      async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false }),
    );
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => {
        capturedTools.set(tool.name, tool);
      }),
      on: mock(() => {}),
      exec: staticExec,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_archive")!;
    expect(tool).toBeDefined();

    await expect(
      tool.execute("call-id", { project_id: "Demo", by: "Mainline - Demo" }, undefined, undefined, {} as any),
    ).rejects.toThrow(stderrMsg);
  });

  test("archive tool: thrown Error message contains the verbatim string", async () => {
    const stderrMsg = "project 'Demo' is archived";
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_archive")!;
    let caughtMessage = "";
    try {
      await tool.execute("call-id", { project_id: "Demo", by: "Mainline - Demo" }, undefined, undefined, {} as any);
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(stderrMsg);
  });
});

describe("AC4 — error passthrough: project tombstoned", () => {
  test("send tool: stderr 'project \\'Demo\\' is tombstoned' → Error contains string verbatim", async () => {
    const stderrMsg = "project 'Demo' is tombstoned";
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_send")!;
    expect(tool).toBeDefined();

    let caughtMessage = "";
    try {
      await tool.execute(
        "call-id",
        { from: "Mainline - Demo", to: ["Track 1 - Demo"], subject: "ping" },
        undefined,
        undefined,
        {} as any,
      );
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(stderrMsg);
  });

  test("inbox tool: stderr 'project \\'Demo\\' is tombstoned' → Error contains string verbatim", async () => {
    const stderrMsg = "project 'Demo' is tombstoned";
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_inbox")!;
    let caughtMessage = "";
    try {
      await tool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(stderrMsg);
  });
});

describe("AC4 — error passthrough: unknown project", () => {
  test("register tool: stderr 'unknown project \\'Demo\\'' → Error contains string verbatim", async () => {
    const stderrMsg = "unknown project 'Demo'";
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_register")!;
    expect(tool).toBeDefined();

    let caughtMessage = "";
    try {
      await tool.execute(
        "call-id",
        { address: "Mainline - Demo" },
        undefined,
        undefined,
        {} as any,
      );
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(stderrMsg);
  });

  test("fetch tool: stderr 'unknown project \\'Demo\\'' → Error contains string verbatim", async () => {
    const stderrMsg = "unknown project 'Demo'";
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: stderrMsg, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_fetch")!;
    let caughtMessage = "";
    try {
      await tool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(stderrMsg);
  });
});

describe("AC4 — error passthrough: cross-project grant error", () => {
  const GRANT_ERROR = "cross-project sending not approved for project 'Demo' — ask the Sandesh admin";

  test("send tool: grant refusal stderr → Error contains verbatim grant string", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: GRANT_ERROR, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_send")!;
    expect(tool).toBeDefined();

    let caughtMessage = "";
    try {
      await tool.execute(
        "call-id",
        { from: "Mainline - Demo", to: ["Mainline - OtherProj"], subject: "cross-project msg" },
        undefined,
        undefined,
        {} as any,
      );
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(GRANT_ERROR);
  });

  test("send tool: grant refusal stderr contains full admin hint substring", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: GRANT_ERROR, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_send")!;

    let caughtMessage = "";
    try {
      await tool.execute(
        "call-id",
        { from: "Mainline - Demo", to: ["Track 1 - OtherProj"], subject: "ping" },
        undefined,
        undefined,
        {} as any,
      );
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    // Must carry the full "ask the Sandesh admin" tail verbatim
    expect(caughtMessage).toContain("ask the Sandesh admin");
  });

  test("reply tool: grant refusal stderr → Error contains verbatim grant string", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const staticPi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: GRANT_ERROR, code: 1, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(staticPi);

    const tool = capturedTools.get("sandesh_reply")!;
    let caughtMessage = "";
    try {
      await tool.execute("call-id", { parent_id: 42 }, undefined, undefined, {} as any);
    } catch (e) {
      caughtMessage = (e as Error).message;
    }
    expect(caughtMessage).toContain(GRANT_ERROR);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AC5 — docs markers
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * §S5 / PE10: promptSnippets/descriptions gain the proxy-stream + search/pagination
 * markers. package.json version → 0.2.0.
 *
 * RED items:
 *   - package.json version (currently "0.1.0" → RED until GREEN bumps it)
 *
 * May already PASS post-C1 (check + report):
 *   - sender_project / proxy-stream wording in inbox + fetch
 *   - pagination / offset wording in search description
 */

describe("AC5 — package.json version is 0.2.0", () => {
  test("package.json version field is '0.2.0'", () => {
    // RED: currently "0.1.0" — this fails until GREEN bumps package.json
    expect(pkgJson.version).toBe("0.2.0");
  });
});

describe("AC5 — docs markers: inbox and fetch carry sender_project / proxy-stream wording", () => {
  function getRegisteredTools(): Map<string, CapturedTool> {
    const capturedTools = new Map<string, CapturedTool>();
    const pi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: mock(async (): Promise<ExecResult> => ({ stdout: "", stderr: "", code: 0, killed: false })),
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;
    registerExtension(pi);
    return capturedTools;
  }

  test("sandesh_inbox description or promptSnippet contains 'sender_project' or 'proxy' wording", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_inbox")!;
    expect(tool).toBeDefined();
    const combined = ((tool as any).desc ?? "") + " " + ((tool as any).promptSnippet ?? "");
    const hasMark =
      combined.toLowerCase().includes("sender_project") ||
      combined.toLowerCase().includes("proxy") ||
      combined.toLowerCase().includes("cross-project");
    // If the desc/promptSnippet already has the wording (post-C1), this passes.
    // If not, this is the RED signal for GREEN to add it.
    expect(hasMark).toBe(true);
  });

  test("sandesh_fetch description or promptSnippet contains 'sender_project' or 'proxy' wording", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_fetch")!;
    expect(tool).toBeDefined();
    const combined = ((tool as any).desc ?? "") + " " + ((tool as any).promptSnippet ?? "");
    const hasMark =
      combined.toLowerCase().includes("sender_project") ||
      combined.toLowerCase().includes("proxy") ||
      combined.toLowerCase().includes("cross-project");
    expect(hasMark).toBe(true);
  });

  test("sandesh_search description or promptSnippet contains pagination / offset wording", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_search")!;
    expect(tool).toBeDefined();
    const combined = ((tool as any).desc ?? "") + " " + ((tool as any).promptSnippet ?? "");
    const hasPagination =
      combined.toLowerCase().includes("paginate") ||
      combined.toLowerCase().includes("pagination") ||
      combined.toLowerCase().includes("offset");
    expect(hasPagination).toBe(true);
  });

  test("sandesh_inbox sender_project param description mentions 'proxy' or 'cross-project'", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_inbox")!;
    expect(tool).toBeDefined();
    const props = ((tool as any).parameters as Record<string, unknown>).properties as Record<string, any>;
    const senderProjectProp = props?.sender_project;
    expect(senderProjectProp).toBeDefined();
    const desc: string =
      senderProjectProp?.description ?? senderProjectProp?.desc ?? "";
    const hasMark =
      desc.toLowerCase().includes("proxy") ||
      desc.toLowerCase().includes("cross-project") ||
      desc.toLowerCase().includes("proxy-stream");
    expect(hasMark).toBe(true);
  });

  test("sandesh_fetch sender_project param description mentions 'proxy' or 'cross-project'", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_fetch")!;
    expect(tool).toBeDefined();
    const props = ((tool as any).parameters as Record<string, unknown>).properties as Record<string, any>;
    const senderProjectProp = props?.sender_project;
    expect(senderProjectProp).toBeDefined();
    const desc: string =
      senderProjectProp?.description ?? senderProjectProp?.desc ?? "";
    const hasMark =
      desc.toLowerCase().includes("proxy") ||
      desc.toLowerCase().includes("cross-project") ||
      desc.toLowerCase().includes("proxy-stream");
    expect(hasMark).toBe(true);
  });

  test("sandesh_search sender_project param description mentions 'proxy' or 'cross-project'", () => {
    const tools = getRegisteredTools();
    const tool = tools.get("sandesh_search")!;
    expect(tool).toBeDefined();
    const props = ((tool as any).parameters as Record<string, unknown>).properties as Record<string, any>;
    const senderProjectProp = props?.sender_project;
    expect(senderProjectProp).toBeDefined();
    const desc: string =
      senderProjectProp?.description ?? senderProjectProp?.desc ?? "";
    const hasMark =
      desc.toLowerCase().includes("proxy") ||
      desc.toLowerCase().includes("cross-project") ||
      desc.toLowerCase().includes("proxy-stream");
    expect(hasMark).toBe(true);
  });
});
