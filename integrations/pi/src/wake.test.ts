/**
 * CR-SAN-048 §S4/§S2/§4.7 — RED: sandesh_notify_start/status/stop tools, the
 * /sandesh-watcher command, the wake path end-to-end through the tool, and the
 * sandesh_status home view's appended `watcher: running|stopped` line
 * (PRD-axi-toon.md §4.6 "Home view" P8, §4.7 "Tools"; AC4, AC7, AC3b).
 *
 * REWRITE (§S5/AC9): this file previously tested the OLD module-level
 * `wakeLoop` in index.ts (probe-gated auto-arm, seams `__setWakeSleepFn` /
 * env-gated session_start). Cycle 216 replaces `wakeLoop` with the already-
 * shipped `WakeSupervisor` (src/wake.ts, GREEN'd in C3 — see
 * wake_supervisor.test.ts for its own state-machine unit tests) wired into
 * index.ts behind three new tools + a slash command. This file drives that
 * wiring exclusively through `registerExtension(fakePi)`'s captured
 * tools/commands — it never imports `./wake` directly (that seam is already
 * covered).
 *
 * RED reason: none of sandesh_notify_start/status/stop or sandesh_status are
 * registered yet (registerTool is never called with those names today), so
 * every `getTool(...)` lookup in this file throws "Tool ... not registered" —
 * a not-yet-existing-SUT-symbol RED (Mode 1).
 */

import { test, expect, describe, mock } from "bun:test";
import { encode } from "@toon-format/toon";
import type {
  ExecResult,
  ExtensionAPI,
  ExtensionContext,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ─── Envelope fixtures ──────────────────────────────────────────────────────

/** Mirrors notify.py's `_finish` — PRD §4.5 (same shape used in wake_supervisor.test.ts). */
function notifyEnvelope(opts: {
  exit: number;
  address: string;
  project: string;
  unread?: number[];
  error?: string;
  ok?: boolean;
}): string {
  const ok = opts.ok ?? (opts.exit === 0 || opts.exit === 2 || opts.exit === 5);
  const axi: Record<string, unknown> = {
    verb: "notify",
    ok,
    exit: opts.exit,
    address: opts.address,
    project: opts.project,
    unread: opts.unread ?? [],
  };
  if (opts.error !== undefined) axi.error = opts.error;
  axi.context = { project: opts.project, address: opts.address };
  axi.warnings = [];
  return encode({ axi });
}

/** A `sandesh --format toon status` fixture — the CLI's home view (§4.6 P8). */
function statusEnvelope(opts: { address?: string; listening?: boolean; unread?: number } = {}): string {
  const axi: Record<string, unknown> = {
    verb: "status",
    ok: true,
    bin: "sandesh",
    address: opts.address ?? "Mainline - Demo",
    listening: opts.listening ?? true,
    unread: opts.unread ?? 0,
    context: { project: "Demo" },
    warnings: [],
  };
  return encode({ axi });
}

// ─── Deferred helper (mirrors wake_supervisor.test.ts) ─────────────────────

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function makeDeferred<T>(): Deferred<T> {
  let resolveFn!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolveFn = res;
  });
  return { promise, resolve: resolveFn };
}

async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

// ─── Fake exec — routes by argv content. --version / init --check / status
// calls resolve immediately (status is scriptable via setStatusResult);
// "notify" calls are deferred so the test can drive exit codes/relaunch and
// observe the AbortSignal. ────────────────────────────────────────────────

interface ExecCall {
  cmd: string;
  args: string[];
  signal?: AbortSignal;
}

interface NotifyDeferred {
  args: string[];
  signal?: AbortSignal;
  resolve: (r: ExecResult) => void;
}

function makeFakeExec() {
  const calls: ExecCall[] = [];
  const notifyDeferreds: NotifyDeferred[] = [];
  let statusResult: ExecResult = { stdout: statusEnvelope(), stderr: "", code: 0, killed: false };

  const exec = mock((cmd: string, args: string[], opts?: { signal?: AbortSignal }): Promise<ExecResult> => {
    calls.push({ cmd, args, signal: opts?.signal });
    if (args.includes("--version")) {
      return Promise.resolve({ stdout: "sandesh 0.4.0", stderr: "", code: 0, killed: false });
    }
    if (args.includes("init")) {
      return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
    }
    if (args.includes("notify")) {
      const d = makeDeferred<ExecResult>();
      notifyDeferreds.push({ args, signal: opts?.signal, resolve: d.resolve });
      return d.promise;
    }
    if (args.includes("status")) {
      return Promise.resolve(statusResult);
    }
    return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
  });

  return {
    exec,
    calls,
    notifyDeferreds,
    setStatusResult: (r: ExecResult) => {
      statusResult = r;
    },
  };
}

function makeFakePi() {
  const capturedTools = new Map<string, ToolDefinition<any, any, any>>();
  const capturedCommands = new Map<string, { description?: string; handler: (args: string, ctx: ExtensionContext) => Promise<void> }>();
  const handlers = new Map<string, (event: unknown, ctx: ExtensionContext) => unknown>();
  const { exec, calls: execCalls, notifyDeferreds, setStatusResult } = makeFakeExec();
  const sendUserMessageMock = mock((_text: string, _opts?: { deliverAs: string }): void => {});
  const sendMessageMock = mock((_msg: unknown, _opts?: unknown): void => {});

  const fakePi = {
    registerTool: mock((tool: ToolDefinition<any, any, any>) => {
      capturedTools.set(tool.name, tool);
    }),
    registerCommand: mock((name: string, opts: any) => {
      capturedCommands.set(name, opts);
    }),
    on: mock((event: string, handler: unknown) => {
      handlers.set(event, handler as (event: unknown, ctx: ExtensionContext) => unknown);
    }),
    exec,
    sendUserMessage: sendUserMessageMock,
    sendMessage: sendMessageMock,
  } as unknown as ExtensionAPI;

  return {
    fakePi,
    capturedTools,
    capturedCommands,
    handlers,
    execCalls,
    notifyDeferreds,
    setStatusResult,
    sendUserMessageMock,
    sendMessageMock,
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

function getTool(tools: Map<string, ToolDefinition<any, any, any>>, name: string): ToolDefinition<any, any, any> {
  const t = tools.get(name);
  if (!t) throw new Error(`Tool "${name}" not registered`);
  return t;
}

async function callExecute(tool: ToolDefinition<any, any, any>, params: Record<string, unknown>, ctx: ExtensionContext) {
  return tool.execute("test-call-id", params, undefined, undefined, ctx);
}

function text(result: { content: unknown[] }): string {
  return (result.content[0] as { type: "text"; text: string }).text;
}

// ─── Env save/restore ───────────────────────────────────────────────────────

const SAVED_ENV: Partial<Record<string, string>> = {};

function saveEnv(): void {
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;
}

function restoreEnv(): void {
  if (SAVED_ENV.SANDESH_ADDRESS === undefined) delete process.env.SANDESH_ADDRESS;
  else process.env.SANDESH_ADDRESS = SAVED_ENV.SANDESH_ADDRESS;
  if (SAVED_ENV.SANDESH_PROJECT === undefined) delete process.env.SANDESH_PROJECT;
  else process.env.SANDESH_PROJECT = SAVED_ENV.SANDESH_PROJECT;
}

// ============================================================================
// A — sandesh_notify_start / notify_status / notify_stop tools + command (§S4, AC7)
// ============================================================================

describe("A — sandesh_notify_start/status/stop tools + /sandesh-watcher command", () => {
  test("registerExtension registers 16 tools total (12 verbs + sandesh_status + 3 notify tools)", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    expect(capturedTools.size).toBe(16);
    expect(capturedTools.has("sandesh_notify_start")).toBe(true);
    expect(capturedTools.has("sandesh_notify_status")).toBe(true);
    expect(capturedTools.has("sandesh_notify_stop")).toBe(true);
    expect(capturedTools.has("sandesh_status")).toBe(true);
  });

  test("sandesh_notify_start spawns notify with the exact argv and returns an ok envelope with already:false", async () => {
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const tool = getTool(capturedTools, "sandesh_notify_start");

    const result = await callExecute(tool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    const env = decodeEnvelope(text(result));

    expect(env.verb).toBe("notify_start");
    expect(env.ok).toBe(true);
    expect(env.fields.already).toBe(false);
    expect(notifyDeferreds.length).toBe(1);
    expect(notifyDeferreds[0].args).toEqual([
      "--project",
      "Demo",
      "--format",
      "toon",
      "notify",
      "--to",
      "Mainline - Demo",
    ]);
  });

  test("a second sandesh_notify_start for the same address reports already:true and spawns no second child", async () => {
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const tool = getTool(capturedTools, "sandesh_notify_start");

    await callExecute(tool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    expect(notifyDeferreds.length).toBe(1);

    const second = await callExecute(tool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    const env = decodeEnvelope(text(second));
    expect(env.fields.already).toBe(true);
    expect(notifyDeferreds.length).toBe(1); // no second spawn
  });

  test("sandesh_notify_start with no params falls back to $SANDESH_ADDRESS / $SANDESH_PROJECT", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Track 1 - TestProj";
    process.env.SANDESH_PROJECT = "TestProj";
    try {
      const { fakePi, capturedTools, notifyDeferreds } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const tool = getTool(capturedTools, "sandesh_notify_start");

      const result = await callExecute(tool, {}, fakeCtx);
      const env = decodeEnvelope(text(result));
      expect(env.ok).toBe(true);
      expect(notifyDeferreds[0].args).toEqual([
        "--project",
        "TestProj",
        "--format",
        "toon",
        "notify",
        "--to",
        "Track 1 - TestProj",
      ]);
    } finally {
      restoreEnv();
    }
  });

  test("sandesh_notify_start with no params and no env vars returns ok:false naming both SANDESH_ADDRESS and SANDESH_PROJECT", async () => {
    saveEnv();
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    try {
      const { fakePi, capturedTools, notifyDeferreds } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const tool = getTool(capturedTools, "sandesh_notify_start");

      const result = await callExecute(tool, {}, fakeCtx);
      const env = decodeEnvelope(text(result));
      expect(env.ok).toBe(false);
      expect(env.error).toBeDefined();
      expect(env.error as string).toContain("SANDESH_ADDRESS");
      expect(env.error as string).toContain("SANDESH_PROJECT");
      expect(notifyDeferreds.length).toBe(0); // nothing spawned — an error result, not a throw
    } finally {
      restoreEnv();
    }
  });

  test("sandesh_notify_status reports one watcher entry {address, running, lastExit} after a start", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const statusTool = getTool(capturedTools, "sandesh_notify_status");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    const result = await callExecute(statusTool, {}, fakeCtx);
    const env = decodeEnvelope(text(result));

    expect(env.verb).toBe("notify_status");
    const watchers = env.fields.watchers as Array<{ address: string; running: boolean; lastExit: number | null }>;
    expect(Array.isArray(watchers)).toBe(true);
    expect(watchers.length).toBe(1);
    expect(watchers[0].address).toBe("Mainline - Demo");
    expect(watchers[0].running).toBe(true);
  });

  test("sandesh_notify_stop({address}) aborts the running child's signal and reports stopped:1", async () => {
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const stopTool = getTool(capturedTools, "sandesh_notify_stop");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    expect(notifyDeferreds[0].signal?.aborted).toBe(false);

    const result = await callExecute(stopTool, { address: "Mainline - Demo" }, fakeCtx);
    const env = decodeEnvelope(text(result));
    expect(env.verb).toBe("notify_stop");
    expect(env.fields.stopped).toBe(1);
    expect(notifyDeferreds[0].signal?.aborted).toBe(true);
  });

  test("sandesh_notify_stop() with nothing running reports ok:true stopped:0", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const stopTool = getTool(capturedTools, "sandesh_notify_stop");

    const result = await callExecute(stopTool, {}, fakeCtx);
    const env = decodeEnvelope(text(result));
    expect(env.ok).toBe(true);
    expect(env.fields.stopped).toBe(0);
  });

  test("registers a /sandesh-watcher command whose status/stop subcommands both call ctx.ui.notify", async () => {
    const { fakePi, capturedCommands } = makeFakePi();
    registerExtension(fakePi);
    expect(capturedCommands.has("sandesh-watcher")).toBe(true);

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    const cmd = capturedCommands.get("sandesh-watcher")!;

    await cmd.handler("status", fakeCtx);
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);

    const beforeStop = notifyCalls.length;
    await cmd.handler("stop", fakeCtx);
    expect(notifyCalls.length).toBeGreaterThan(beforeStop);
  });
});

// ============================================================================
// B — wake path end-to-end through the tool (AC4)
// ============================================================================

describe("B — wake path end-to-end through sandesh_notify_start (AC4)", () => {
  test("exit 0 with ids [12,13] sends a followUp message naming them and relaunches", async () => {
    const { fakePi, capturedTools, notifyDeferreds, sendUserMessageMock } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    expect(notifyDeferreds.length).toBe(1);

    notifyDeferreds[0].resolve({
      code: 0,
      stdout: notifyEnvelope({ exit: 0, address: "Mainline - Demo", project: "Demo", unread: [12, 13] }),
      stderr: "",
      killed: false,
    });
    await flush();

    expect(sendUserMessageMock.mock.calls.length).toBe(1);
    const [msgText, opts] = sendUserMessageMock.mock.calls[0] as [string, { deliverAs: string }];
    expect(msgText).toContain("12, 13");
    expect(opts).toEqual({ deliverAs: "followUp" });

    expect(notifyDeferreds.length).toBe(2); // relaunch registered a second notify call
  });
});

// ============================================================================
// E — sandesh_status home tool: appends watcher: running|stopped (§S2/P8, AC3b)
// ============================================================================

describe("E — sandesh_status appends watcher: running|stopped without breaking the TOON envelope", () => {
  test("before any start, sandesh_status() decodes with fields.watcher === 'stopped'", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    try {
      const { fakePi, capturedTools } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const statusTool = getTool(capturedTools, "sandesh_status");

      const result = await callExecute(statusTool, {}, fakeCtx);
      const env = decodeEnvelope(text(result));
      expect(env.verb).toBe("status");
      expect(env.fields.watcher).toBe("stopped");
    } finally {
      restoreEnv();
    }
  });

  test("after sandesh_notify_start for the session's own address, sandesh_status() decodes with fields.watcher === 'running'", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    try {
      const { fakePi, capturedTools } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startTool = getTool(capturedTools, "sandesh_notify_start");
      const statusTool = getTool(capturedTools, "sandesh_status");

      // No params — resolves from $SANDESH_ADDRESS/$SANDESH_PROJECT, so this is
      // unambiguously "my own watcher" under either a self-address or
      // any-running-watcher interpretation of the appended field.
      await callExecute(startTool, {}, fakeCtx);
      const result = await callExecute(statusTool, {}, fakeCtx);
      const env = decodeEnvelope(text(result));
      expect(env.verb).toBe("status");
      expect(env.fields.watcher).toBe("running");
    } finally {
      restoreEnv();
    }
  });
});
