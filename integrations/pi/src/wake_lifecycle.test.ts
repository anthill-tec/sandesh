/**
 * CR-SAN-048 §S5/§4.7 — RED: arming (D4) + session_shutdown + AC9 pins
 * (PRD-axi-toon.md §4.7 "Arming (D4)"; AC8, AC9).
 *
 * REWRITE (§S5/AC9): this file previously tested the OLD module-level
 * `wakeLoop` lifecycle (`__resetWakeState`, AbortController threading,
 * `MISSING_ENV_NOTICE` on any missing env var, single-loop guard). Cycle 216
 * replaces all of that: `session_start` no longer auto-arms by default —
 * arming now requires `SANDESH_AUTOSTART=1` (with both identity vars) and
 * calls the same `WakeSupervisor.start()` the `sandesh_notify_start` tool
 * uses (see wake.test.ts). `session_shutdown` stops every running watcher via
 * the supervisor. AC9 also requires the old `wakeLoop` symbol gone and the
 * npm `files` whitelist extended to ship `src/wake.ts`.
 *
 * RED reason: today's `session_start` handler unconditionally starts the OLD
 * `wakeLoop` whenever both identity vars are set (no `SANDESH_AUTOSTART` gate
 * exists at all) and emits `MISSING_ENV_NOTICE` — a "warning" — instead of
 * the new "info" tool-started notice; `function wakeLoop` still exists in
 * index.ts; `package.json` `files` does not list `src/wake.ts`.
 *
 * Coincidental pass (documented per the axi_passthrough.test.ts precedent —
 * "a coincidental pass, not a real signal"): the "SANDESH_AUTOSTART=1 with
 * both vars unset" case already passes against TODAY's code, because old
 * `MISSING_ENV_NOTICE` already fires unconditionally (independent of any
 * autostart concept) whenever either identity var is missing, and already
 * names both vars while spawning nothing — the exact same observable outcome
 * AC8 requires for this input. There is no AUTOSTART-specific behavior left
 * to differentiate for THIS combination of inputs (vars unset); the gate's
 * real RED signal lives in the other three C tests (vars-set-no-autostart,
 * autostart-spawns, shutdown-aborts), which all fail as expected.
 */

import { test, expect, describe, mock } from "bun:test";
import { readFileSync } from "fs";
import { resolve } from "path";
import { spawnSync } from "child_process";
import type { ExecResult, ExtensionAPI, ExtensionContext, ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";
import { packedFilePaths } from "./npm_pack";

// ─── Deferred helper ────────────────────────────────────────────────────────

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

// ─── Fake exec — same routing as wake.test.ts: --version/init resolve
// immediately; "notify" calls are deferred (tracked, never auto-resolved —
// these tests only assert on the spawn/abort, not on exit-code reactions);
// anything else (the ambient "status" probe) resolves immediately with an
// empty/undecodable envelope so it never breaks session_start. ────────────

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
    return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
  });

  return { exec, calls, notifyDeferreds };
}

function makeFakePi() {
  const capturedTools = new Map<string, ToolDefinition<any, any, any>>();
  const handlers = new Map<string, (event: unknown, ctx: ExtensionContext) => unknown>();
  const { exec, calls: execCalls, notifyDeferreds } = makeFakeExec();
  const sendUserMessageMock = mock((_text: string, _opts?: { deliverAs: string }): void => {});
  const sendMessageMock = mock((_msg: unknown, _opts?: unknown): void => {});

  const fakePi = {
    registerTool: mock((tool: ToolDefinition<any, any, any>) => {
      capturedTools.set(tool.name, tool);
    }),
    registerCommand: mock(() => {}),
    on: mock((event: string, handler: unknown) => {
      handlers.set(event, handler as (event: unknown, ctx: ExtensionContext) => unknown);
    }),
    exec,
    sendUserMessage: sendUserMessageMock,
    sendMessage: sendMessageMock,
  } as unknown as ExtensionAPI;

  return { fakePi, capturedTools, handlers, execCalls, notifyDeferreds };
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

// ─── Env save/restore (address, project, autostart) ───────────────────────

const SAVED_ENV: Partial<Record<string, string>> = {};

function saveEnv(): void {
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;
  SAVED_ENV.SANDESH_AUTOSTART = process.env.SANDESH_AUTOSTART;
}

function restoreEnv(): void {
  if (SAVED_ENV.SANDESH_ADDRESS === undefined) delete process.env.SANDESH_ADDRESS;
  else process.env.SANDESH_ADDRESS = SAVED_ENV.SANDESH_ADDRESS;
  if (SAVED_ENV.SANDESH_PROJECT === undefined) delete process.env.SANDESH_PROJECT;
  else process.env.SANDESH_PROJECT = SAVED_ENV.SANDESH_PROJECT;
  if (SAVED_ENV.SANDESH_AUTOSTART === undefined) delete process.env.SANDESH_AUTOSTART;
  else process.env.SANDESH_AUTOSTART = SAVED_ENV.SANDESH_AUTOSTART;
}

const fakeSessionStartEvent = { type: "session_start", reason: "startup" } as const;
const fakeSessionShutdownEvent = { type: "session_shutdown", reason: "quit" } as const;

// ============================================================================
// C — arming (D4) + session_shutdown (AC8)
// ============================================================================

describe("C — arming: SANDESH_AUTOSTART gate (§S5, AC8)", () => {
  test("both identity vars set, no SANDESH_AUTOSTART → no notify spawned, one info notice naming sandesh_notify_start", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    delete process.env.SANDESH_AUTOSTART;
    try {
      const { fakePi, handlers, notifyDeferreds } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx, notifyCalls } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(notifyDeferreds.length).toBe(0);

      const infoNotices = notifyCalls.filter((n) => n.type === "info");
      expect(infoNotices.length).toBe(1);
      expect(infoNotices[0].msg).toContain("sandesh_notify_start");
    } finally {
      restoreEnv();
    }
  });

  test("SANDESH_AUTOSTART=1 with both identity vars set spawns exactly one notify with the tool's argv", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    process.env.SANDESH_AUTOSTART = "1";
    try {
      const { fakePi, handlers, notifyDeferreds } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

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
    } finally {
      restoreEnv();
    }
  });

  test("SANDESH_AUTOSTART=1 with both vars unset emits an error/warning naming both vars and spawns nothing", async () => {
    saveEnv();
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    process.env.SANDESH_AUTOSTART = "1";
    try {
      const { fakePi, handlers, notifyDeferreds } = makeFakePi();
      registerExtension(fakePi);
      const { fakeCtx, notifyCalls } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(notifyDeferreds.length).toBe(0);

      const severe = notifyCalls.filter((n) => n.type === "warning" || n.type === "error");
      expect(severe.length).toBeGreaterThanOrEqual(1);
      const joined = severe.map((n) => n.msg).join(" ");
      expect(joined).toContain("SANDESH_ADDRESS");
      expect(joined).toContain("SANDESH_PROJECT");
    } finally {
      restoreEnv();
    }
  });

  test("session_shutdown aborts every running child; sandesh_notify_status then reports running:false for both", async () => {
    const { fakePi, capturedTools, handlers, notifyDeferreds } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const statusTool = getTool(capturedTools, "sandesh_notify_status");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    await callExecute(startTool, { address: "Track 1 - Demo", project: "Demo" }, fakeCtx);
    expect(notifyDeferreds.length).toBe(2);
    expect(notifyDeferreds[0].signal?.aborted).toBe(false);
    expect(notifyDeferreds[1].signal?.aborted).toBe(false);

    const shutdownHandler = handlers.get("session_shutdown")!;
    await shutdownHandler(fakeSessionShutdownEvent, fakeCtx);
    await flush();

    expect(notifyDeferreds[0].signal?.aborted).toBe(true);
    expect(notifyDeferreds[1].signal?.aborted).toBe(true);

    const result = await callExecute(statusTool, {}, fakeCtx);
    const env = decodeEnvelope(text(result));
    const watchers = env.fields.watchers as Array<{ address: string; running: boolean }>;
    expect(watchers.length).toBe(2);
    for (const w of watchers) expect(w.running).toBe(false);
  });
});

// ============================================================================
// F — AC9 pins: wakeLoop deleted; files whitelist + npm pack ship src/wake.ts
// ============================================================================

describe("F — AC9 pins", () => {
  test("src/index.ts source no longer defines a wakeLoop function", () => {
    const src = readFileSync(resolve(import.meta.dir, "index.ts"), "utf-8");
    expect(src).not.toContain("function wakeLoop");
  });

  test("package.json files includes src/wake.ts and src/toon.ts", () => {
    const pkg = JSON.parse(readFileSync(resolve(import.meta.dir, "..", "package.json"), "utf-8")) as {
      files?: string[];
    };
    expect(pkg.files).toContain("src/wake.ts");
    expect(pkg.files).toContain("src/toon.ts");
  });

  test("npm pack --dry-run --json lists src/wake.ts in the tarball", () => {
    const result = spawnSync("npm", ["pack", "--dry-run", "--json"], {
      cwd: resolve(import.meta.dir, ".."),
      encoding: "utf-8",
      shell: true,
    });
    if (result.status !== 0) {
      throw new Error(`npm pack failed (exit ${result.status ?? "null"}): ${result.stderr}`);
    }
    const files = packedFilePaths(result.stdout);
    expect(files).toContain("src/wake.ts");
  });
});
