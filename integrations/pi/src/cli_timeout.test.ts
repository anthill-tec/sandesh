/**
 * CR-SAN-055 §S3 / AC6 — RED: every sandesh-pi CLI call gets `timeout:
 * CLI_TIMEOUT_MS` (240_000), except the wake watcher's `notify` exec; a
 * `killed:true` result is told apart by the caller's abort signal — aborted
 * → "aborted", not aborted → "timed out" (Pi integration, production
 * `registerExtension`, fake `pi`).
 *
 * RED reasons (today's index.ts):
 *   - `runSandeshText` (l.387-399) calls `pi.exec(cmd, resolvedArgs, { signal })`
 *     — no `timeout` key at all, so every verb call's `opts.timeout` is
 *     `undefined`, never `240000`.
 *   - `probeVersion` (l.224-243) only checks `r.code === 0`, ignoring
 *     `killed` — a killed `--version` probe with `code:0` is wrongly
 *     reported `reachable:true` with empty stdout, which then fails the
 *     version-string parse and fires the OUTDATED_CLI_NOTICE ("too old"),
 *     not the missing/unreachable-CLI notice.
 *   - the `init --check` nudge (l.1363) only checks `check.code !== 0` — a
 *     killed `init --check` with `code:0` is silently treated as
 *     provisioned (no nudge).
 *   - `runSandeshText`'s only throw condition is
 *     `r.code !== 0 && !isEnvelope(r.stdout)`; a `{killed:true, code:0,
 *     stdout:""}` result has `code === 0`, so it short-circuits past the
 *     throw and returns the empty stdout as a SUCCESSFUL result — no verb
 *     tool ever surfaces "timed out" or "aborted" today.
 *
 * Harness: mirrors uvx_provision.test.ts / unregister_stops_watcher.test.ts
 * (execSequence-based makeFakePi, makeFakeCtx with notifyCalls capture,
 * fireSessionStart + drainMicrotasks, __setStartSettleMs(0) for notify-start
 * calls).
 */

import { test, expect, describe, mock, beforeEach, afterEach, beforeAll } from "bun:test";
import type {
  ExecResult,
  ExtensionAPI,
  ExtensionContext,
  SessionStartEvent,
  ExtensionHandler,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import registerExtension, {
  resetExtensionState,
  __setStartSettleMs,
  __setCliTimeoutMs,
} from "./index";

/** Owner ruling (2026-10-16, CR-SAN-055 §S3): the CLI's own worst case tops
 * out at ~200s (6 locked-write retries x 30s busy_timeout, plus the §S1
 * wait); 240_000 sits above it. Hardcoded here (not imported) — AC6 asserts
 * the VALUE pi.exec is called with, not an internal symbol name. */
const CLI_TIMEOUT_MS = 240_000;

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type CapturedTool = ToolDefinition<any, any, any>;
type ExecOpts = { signal?: AbortSignal; timeout?: number };
type ExecCall = [string, string[], ExecOpts | undefined];

// ─── exec-sequence harness (mirrors uvx_provision.test.ts) ───────────────────

function ok(stdout = "", stderr = "", code = 0): ExecResult {
  return { stdout, stderr, code, killed: false };
}

function exit(code: number, stderr = ""): ExecResult {
  return { stdout: "", stderr: stderr || `exit ${code}`, code, killed: false };
}

/** A result Pi reports for a child it killed (signal-killed == code 0, per the CR context). */
function killed(code = 0): ExecResult {
  return { stdout: "", stderr: "", code, killed: true };
}

function makeExecSequence(
  sequence: Array<ExecResult | "reject">,
): (cmd: string, args: string[], opts?: ExecOpts) => Promise<ExecResult> {
  let index = 0;
  return async (_cmd, _args, _opts) => {
    const entry = sequence[index];
    if (index < sequence.length - 1) index++;
    if (entry === "reject") throw new Error("sandesh: command not found");
    return entry;
  };
}

function makeFakePi(execSequence: Array<ExecResult | "reject">) {
  const capturedTools = new Map<string, CapturedTool>();
  let sessionStartHandler: SessionStartHandler | undefined;
  const execMock = mock(makeExecSequence(execSequence));
  const fakePi = {
    registerCommand: mock(() => {}), // the extension registers /sandesh-watcher
    registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
    on: mock((event: string, handler: unknown) => {
      if (event === "session_start") sessionStartHandler = handler as SessionStartHandler;
    }),
    exec: execMock,
    sendUserMessage: mock(() => {}),
  } as unknown as ExtensionAPI;
  return {
    fakePi,
    capturedTools,
    execMock,
    getSessionStartHandler: (): SessionStartHandler => {
      if (!sessionStartHandler) throw new Error("session_start handler was never registered");
      return sessionStartHandler;
    },
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

const fakeSessionStartEvent: SessionStartEvent = { type: "session_start" } as SessionStartEvent;

async function drainMicrotasks(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

async function fireSessionStart(handler: SessionStartHandler, fakeCtx: ExtensionContext): Promise<void> {
  await handler(fakeSessionStartEvent, fakeCtx);
  await drainMicrotasks();
}

function execCalls(execMock: ReturnType<typeof mock>): ExecCall[] {
  return execMock.mock.calls as ExecCall[];
}

function getTool(tools: Map<string, CapturedTool>, name: string): CapturedTool {
  const t = tools.get(name);
  if (!t) throw new Error(`Tool "${name}" not registered`);
  return t;
}

// ─── env save/restore (session_start tests read SANDESH_ADDRESS/PROJECT) ────

const SAVED_ENV: Partial<Record<string, string>> = {};

beforeEach(() => {
  resetExtensionState();
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;
});

afterEach(() => {
  if (SAVED_ENV.SANDESH_ADDRESS === undefined) delete process.env.SANDESH_ADDRESS;
  else process.env.SANDESH_ADDRESS = SAVED_ENV.SANDESH_ADDRESS;
  if (SAVED_ENV.SANDESH_PROJECT === undefined) delete process.env.SANDESH_PROJECT;
  else process.env.SANDESH_PROJECT = SAVED_ENV.SANDESH_PROJECT;
});

// Notify-start tests await `sup.settle()`; force the settle window to 0 so
// they don't actually wait 2s (mirrors unregister_stops_watcher.test.ts).
beforeAll(() => {
  __setStartSettleMs(0);
});

// ═══════════════════════════════════════════════════════════════════════════
// AC6 — a verb tool's pi.exec call carries timeout === CLI_TIMEOUT_MS
// ═══════════════════════════════════════════════════════════════════════════

describe("AC6 — verb calls pass timeout === CLI_TIMEOUT_MS to pi.exec", () => {
  test("sandesh_inbox's pi.exec call carries timeout === CLI_TIMEOUT_MS (240000)", async () => {
    const { fakePi, capturedTools, execMock } = makeFakePi([ok("")]);
    registerExtension(fakePi);

    const inboxTool = getTool(capturedTools, "sandesh_inbox");
    await inboxTool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);

    const calls = execCalls(execMock);
    expect(calls.length).toBe(1);
    const [, args, opts] = calls[0];
    expect(args).toContain("inbox");
    // RED today: opts.timeout is undefined — runSandeshText never passes `timeout`.
    expect(opts?.timeout).toBe(CLI_TIMEOUT_MS);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AC6 — the session-start --version probe and init --check carry the same timeout
// ═══════════════════════════════════════════════════════════════════════════

describe("AC6 — session-start probes pass timeout === CLI_TIMEOUT_MS", () => {
  test("the --version probe's pi.exec call carries timeout === CLI_TIMEOUT_MS (240000)", async () => {
    // --version probe, init --check (provisioned) — no SANDESH_ADDRESS/PROJECT
    // set, so the identified-only home-view probe and arming are both skipped.
    const { fakePi, execMock, getSessionStartHandler } = makeFakePi([
      ok("sandesh 0.4.2"),
      ok(""),
    ]);
    registerExtension(fakePi);
    const handler = getSessionStartHandler();
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = execCalls(execMock);
    const probeCall = calls.find(([, args]) => args.includes("--version"));
    expect(probeCall).toBeDefined();
    // RED today: probeVersion's pi.exec call never receives a `timeout` option.
    expect(probeCall![2]?.timeout).toBe(CLI_TIMEOUT_MS);
  });

  test("the init --check pi.exec call carries timeout === CLI_TIMEOUT_MS (240000)", async () => {
    const { fakePi, execMock, getSessionStartHandler } = makeFakePi([
      ok("sandesh 0.4.2"),
      ok(""),
    ]);
    registerExtension(fakePi);
    const handler = getSessionStartHandler();
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = execCalls(execMock);
    const checkCall = calls.find(([, args]) => args.includes("init") && args.includes("--check"));
    expect(checkCall).toBeDefined();
    // RED today: the init --check pi.exec call never receives a `timeout` option.
    expect(checkCall![2]?.timeout).toBe(CLI_TIMEOUT_MS);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AC6 — the wake watcher's notify exec gets no timeout, unlike verb calls
// ═══════════════════════════════════════════════════════════════════════════

describe("AC6 — the wake watcher's notify exec has no timeout", () => {
  test("a verb call's exec carries CLI_TIMEOUT_MS; the same session's notify-watcher exec carries none", async () => {
    const calls: ExecCall[] = [];
    let notifyResolve: ((r: ExecResult) => void) | undefined;
    const notifyPromise = new Promise<ExecResult>((res) => {
      notifyResolve = res;
    });

    const execMock = mock(async (cmd: string, args: string[], opts?: ExecOpts): Promise<ExecResult> => {
      calls.push([cmd, args, opts]);
      if (args.includes("notify")) return notifyPromise; // long-running; never resolves in this test
      return ok("");
    });

    const capturedTools = new Map<string, CapturedTool>();
    const fakePi = {
      registerCommand: mock(() => {}),
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: execMock,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();

    // Start this session's own wake watcher (not the session_start arming path).
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    await startTool.execute(
      "call-id",
      { address: "Mainline - Demo", project: "Demo" },
      undefined,
      undefined,
      fakeCtx,
    );

    const inboxTool = getTool(capturedTools, "sandesh_inbox");
    await inboxTool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);

    const notifyCall = calls.find(([, args]) => args.includes("notify"));
    const inboxCall = calls.find(([, args]) => args.includes("inbox"));
    expect(notifyCall).toBeDefined();
    expect(inboxCall).toBeDefined();

    // RED today: the inbox call's opts.timeout is undefined, not CLI_TIMEOUT_MS.
    expect(inboxCall![2]?.timeout).toBe(CLI_TIMEOUT_MS);
    // Regression guard (passes today and must keep passing under GREEN): the
    // long-running wake watcher must never be given a timeout.
    expect(notifyCall![2]?.timeout).toBeUndefined();

    // Avoid leaking a pending microtask into later tests.
    notifyResolve?.(exit(3));
    await drainMicrotasks();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AC6 — a killed:true verb result is told apart by the caller's abort signal
// ═══════════════════════════════════════════════════════════════════════════

describe("AC6 — killed:true verb result: aborted vs timed out", () => {
  test("killed verb result + signal NOT aborted → Error names the verb and says 'timed out', never 'aborted'", async () => {
    const { fakePi, capturedTools } = makeFakePi([killed(0)]);
    registerExtension(fakePi);

    const inboxTool = getTool(capturedTools, "sandesh_inbox");
    const controller = new AbortController(); // never aborted

    let caught: unknown;
    try {
      await inboxTool.execute(
        "call-id",
        { recipient: "Mainline - Demo" },
        controller.signal,
        undefined,
        {} as any,
      );
    } catch (err) {
      caught = err;
    }

    // RED today: runSandeshText's only throw path is `code !== 0`; a
    // killed, code:0 result short-circuits past it and resolves normally —
    // no Error is ever thrown.
    expect(caught).toBeInstanceOf(Error);
    const msg = (caught as Error).message;
    expect(msg).toContain("inbox");
    expect(msg).toContain("timed out");
    expect(msg).not.toContain("aborted");
  });

  test("killed verb result + signal ALREADY aborted → Error names the verb and says 'aborted', never 'timed out'", async () => {
    const { fakePi, capturedTools } = makeFakePi([killed(0)]);
    registerExtension(fakePi);

    const inboxTool = getTool(capturedTools, "sandesh_inbox");
    const controller = new AbortController();
    controller.abort();

    let caught: unknown;
    try {
      await inboxTool.execute(
        "call-id",
        { recipient: "Mainline - Demo" },
        controller.signal,
        undefined,
        {} as any,
      );
    } catch (err) {
      caught = err;
    }

    // RED today: no Error is ever thrown for a killed, code:0 result,
    // regardless of the caller's abort signal.
    expect(caught).toBeInstanceOf(Error);
    const msg = (caught as Error).message;
    expect(msg).toContain("inbox");
    expect(msg).toContain("aborted");
    expect(msg).not.toContain("timed out");
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AC8 — __setCliTimeoutMs test seam drives every CLI call's timeout and its
// message; resetExtensionState() restores the default
// ═══════════════════════════════════════════════════════════════════════════

describe("AC8 — __setCliTimeoutMs test seam drives every CLI call's timeout and its message", () => {
  test("after __setCliTimeoutMs(50): a verb call, the --version probe and init --check each carry timeout === 50, and a killed verb call (signal not aborted) fails with 'timed out after 0.05s'", async () => {
    __setCliTimeoutMs(50);

    // a verb tool's pi.exec call carries timeout === 50
    const { fakePi: verbPi, capturedTools: verbTools, execMock: verbExec } = makeFakePi([ok("")]);
    registerExtension(verbPi);
    const inboxTool = getTool(verbTools, "sandesh_inbox");
    await inboxTool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);
    const verbCalls = execCalls(verbExec);
    expect(verbCalls[0][2]?.timeout).toBe(50);

    // the --version probe and init --check calls carry timeout === 50
    const { fakePi: probePi, execMock: probeExec, getSessionStartHandler } = makeFakePi([
      ok("sandesh 0.4.2"),
      ok(""),
    ]);
    registerExtension(probePi);
    const handler = getSessionStartHandler();
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);
    const probeCalls = execCalls(probeExec);
    const versionProbeCall = probeCalls.find(([, args]) => args.includes("--version"));
    const initCheckCall = probeCalls.find(([, args]) => args.includes("init") && args.includes("--check"));
    expect(versionProbeCall).toBeDefined();
    expect(initCheckCall).toBeDefined();
    expect(versionProbeCall![2]?.timeout).toBe(50);
    expect(initCheckCall![2]?.timeout).toBe(50);

    // a killed verb call under the 50ms timeout, signal NOT aborted, fails
    // with "timed out after 0.05s" (50 / 1000)
    const { fakePi: killedPi, capturedTools: killedTools } = makeFakePi([killed(0)]);
    registerExtension(killedPi);
    const killedInboxTool = getTool(killedTools, "sandesh_inbox");
    const controller = new AbortController(); // never aborted

    let caught: unknown;
    try {
      await killedInboxTool.execute(
        "call-id",
        { recipient: "Mainline - Demo" },
        controller.signal,
        undefined,
        {} as any,
      );
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(Error);
    const msg = (caught as Error).message;
    expect(msg).toContain("inbox");
    expect(msg).toContain("timed out after 0.05s");

    resetExtensionState();
  });

  test("resetExtensionState() restores timeout === CLI_TIMEOUT_MS (240000) after __setCliTimeoutMs(50)", async () => {
    __setCliTimeoutMs(50);
    resetExtensionState();

    const { fakePi, capturedTools, execMock } = makeFakePi([ok("")]);
    registerExtension(fakePi);
    const inboxTool = getTool(capturedTools, "sandesh_inbox");
    await inboxTool.execute("call-id", { recipient: "Mainline - Demo" }, undefined, undefined, {} as any);

    const calls = execCalls(execMock);
    expect(calls[0][2]?.timeout).toBe(CLI_TIMEOUT_MS);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// AC6 — a killed --version probe / init --check are told apart from today's
// code==0-only checks
// ═══════════════════════════════════════════════════════════════════════════

describe("AC6 — a killed --version probe takes the CLI-unreachable path", () => {
  test("killed --version probe (killed:true, code:0) fires the missing/unreachable-CLI notice, not the outdated-version notice", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi([killed(0)]);
    registerExtension(fakePi);
    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    expect(notifyCalls.length).toBe(1);
    // RED today: probeVersion only checks `r.code === 0` (true here), so it
    // reports reachable:true with empty stdout; the empty stdout then fails
    // the version parse and fires the OUTDATED_CLI_NOTICE ("too old"), not
    // the missing/unreachable-CLI notice.
    expect(notifyCalls[0].msg).toContain("not found on PATH");
    expect(notifyCalls[0].msg).not.toContain("too old");
  });
});

describe("AC6 — a killed init --check fires the provision nudge, not the provisioned path", () => {
  test("killed init --check (killed:true, code:0) after a passing version probe → the provision nudge fires", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi([
      ok("sandesh 0.4.2"), // version probe passes
      killed(0), // init --check killed
    ]);
    registerExtension(fakePi);
    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // RED today: `check.code !== 0` is false for a killed, code:0 result, so
    // the nudge branch never runs — a killed check is silently treated as
    // "provisioned" (no notice at all).
    const nudge = notifyCalls.find((c) => c.msg.toLowerCase().includes("sandesh init"));
    expect(nudge).toBeDefined();
  });
});
