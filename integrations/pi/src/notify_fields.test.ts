/**
 * CR-SAN-050 §S5, AC5 — watcher field selection and public reset API.
 * The harness drives registered tools through `registerExtension(fakePi)` and
 * checks the returned watcher rows and module exports.
 */

import { test, expect, describe, mock, beforeAll } from "bun:test";
import type {
  ExecResult,
  ExtensionAPI,
  ExtensionContext,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import registerExtension, { __setStartSettleMs } from "./index";
import { decodeEnvelope } from "./toon";

// CR-SAN-053 \u00a7S3: force the settle window to 0 for this file \u2014 the fields
// tests start a watcher with a pending (never-resolved) notify child, and the
// production `sandesh_notify_start` path now awaits
// `settle(address, START_SETTLE_MS)` after a successful start.
beforeAll(() => {
  __setStartSettleMs(0);
});

// ─── Fake pi harness (mirrors wake.test.ts's makeFakeExec/makeFakePi/makeFakeCtx) ──

interface NotifyDeferred {
  args: string[];
  resolve: (r: ExecResult) => void;
}

function makeDeferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolveFn!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolveFn = res;
  });
  return { promise, resolve: resolveFn };
}

function makeFakeExec() {
  const notifyDeferreds: NotifyDeferred[] = [];
  const exec = mock((_cmd: string, args: string[], opts?: { signal?: AbortSignal }): Promise<ExecResult> => {
    if (args.includes("--version")) {
      return Promise.resolve({ stdout: "sandesh 0.4.0", stderr: "", code: 0, killed: false });
    }
    if (args.includes("notify")) {
      const d = makeDeferred<ExecResult>();
      notifyDeferreds.push({ args, resolve: d.resolve });
      return d.promise;
    }
    return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
  });
  return { exec, notifyDeferreds };
}

function makeFakePi() {
  const capturedTools = new Map<string, ToolDefinition<any, any, any>>();
  const { exec, notifyDeferreds } = makeFakeExec();
  const fakePi = {
    registerTool: mock((tool: ToolDefinition<any, any, any>) => {
      capturedTools.set(tool.name, tool);
    }),
    registerCommand: mock((_name: string, _opts: unknown) => {}),
    on: mock((_event: string, _handler: unknown) => {}),
    exec,
    sendUserMessage: mock((_text: string, _opts?: { deliverAs: string }) => {}),
    sendMessage: mock((_msg: unknown, _opts?: unknown) => {}),
  } as unknown as ExtensionAPI;
  return { fakePi, capturedTools, notifyDeferreds };
}

function makeFakeCtx(): ExtensionContext {
  return {
    ui: { notify: mock((_msg: string, _type?: "info" | "warning" | "error") => {}) },
  } as unknown as ExtensionContext;
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

/** The full `WatcherStatus` column set, in the exact order AC5 lists them. */
const ALL_FIELDS = ["address", "project", "running", "pid", "startedAt", "lastExit", "lastIds", "timeoutExits"];

// ============================================================================
// A — `fields` knob on sandesh_notify_status / sandesh_notify_start (AC5)
// ============================================================================

describe("sandesh_notify_status/start — fields knob (CR-SAN-050 §S5, AC5)", () => {
  test("sandesh_notify_status({fields: <all 8>}) after one notify_start returns exactly those 8 keys, in order", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const ctx = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const statusTool = getTool(capturedTools, "sandesh_notify_status");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, ctx);
    const result = await callExecute(statusTool, { fields: ALL_FIELDS }, ctx);
    const env = decodeEnvelope(text(result));

    expect(env.ok).toBe(true);
    const watchers = env.fields.watchers as Array<Record<string, unknown>>;
    expect(Array.isArray(watchers)).toBe(true);
    expect(watchers.length).toBe(1);
    const row = watchers[0];
    expect(Object.keys(row)).toEqual(ALL_FIELDS);
    expect(row.address).toBe("Mainline - Demo");
    expect(row.project).toBe("Demo");
    expect(row.running).toBe(true);
    expect(Array.isArray(row.lastIds)).toBe(true);
    expect(Array.isArray(row.timeoutExits)).toBe(true);
    expect(typeof row.startedAt).toBe("number");
    expect(row.pid).toBeNull();
  });

  test("sandesh_notify_status default (no fields) returns exactly address,running,lastExit", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const ctx = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const statusTool = getTool(capturedTools, "sandesh_notify_status");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, ctx);
    const result = await callExecute(statusTool, {}, ctx);
    const env = decodeEnvelope(text(result));
    const watchers = env.fields.watchers as Array<Record<string, unknown>>;
    expect(watchers.length).toBe(1);
    expect(Object.keys(watchers[0])).toEqual(["address", "running", "lastExit"]);
    expect(watchers[0].address).toBe("Mainline - Demo");
    expect(watchers[0].running).toBe(true);
    expect(watchers[0].lastExit).toBeNull();
  });

  test("sandesh_notify_start({fields: <all 8>}) honours the knob on watchers[1] (second watcher started)", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const ctx = makeFakeCtx();
    const startTool = getTool(capturedTools, "sandesh_notify_start");

    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, ctx);
    const second = await callExecute(
      startTool,
      { address: "Track 1 - Demo", project: "Demo", fields: ALL_FIELDS },
      ctx,
    );
    const env = decodeEnvelope(text(second));
    expect(env.ok).toBe(true);
    const watchers = env.fields.watchers as Array<Record<string, unknown>>;
    expect(watchers.length).toBe(2);
    const row = watchers[1];
    expect(Object.keys(row)).toEqual(ALL_FIELDS);
    expect(row.address).toBe("Track 1 - Demo");
    expect(row.project).toBe("Demo");
    expect(row.running).toBe(true);
    expect(row.pid).toBeNull();
  });

  test("an unknown field name returns ok:false with an error naming it and the valid set (not a throw)", async () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const ctx = makeFakeCtx();
    const statusTool = getTool(capturedTools, "sandesh_notify_status");

    const result = await callExecute(statusTool, { fields: ["address", "bogus"] }, ctx);
    const env = decodeEnvelope(text(result));

    expect(env.ok).toBe(false);
    expect(env.error).toBeDefined();
    expect(env.error as string).toContain("bogus");
    for (const f of ALL_FIELDS) {
      expect(env.error as string).toContain(f);
      expect(env.help?.join(" ")).toContain(f);
    }
  });

  test("notify status/stop project filters and status fields are declared by TypeBox", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const statusTool = getTool(capturedTools, "sandesh_notify_status");
    const startTool = getTool(capturedTools, "sandesh_notify_start");
    const stopTool = getTool(capturedTools, "sandesh_notify_stop");

    const statusProps = (statusTool.parameters as { properties?: Record<string, unknown> }).properties ?? {};
    const startProps = (startTool.parameters as { properties?: Record<string, unknown> }).properties ?? {};
    const stopProps = (stopTool.parameters as { properties?: Record<string, unknown> }).properties ?? {};
    expect(Object.prototype.hasOwnProperty.call(statusProps, "fields")).toBe(true);
    expect(Object.prototype.hasOwnProperty.call(startProps, "fields")).toBe(true);
    expect(Object.prototype.hasOwnProperty.call(statusProps, "project")).toBe(true);
    expect(Object.prototype.hasOwnProperty.call(stopProps, "project")).toBe(true);
  });
});

// ============================================================================
// B — `__resetWakeState` → `resetExtensionState` rename (048 SUGGESTION 3, AC5)
// ============================================================================

describe("__resetWakeState → resetExtensionState rename (CR-SAN-050 §S5, AC5)", () => {
  test("resetExtensionState is exported from ./index as a function", async () => {
    const mod: Record<string, unknown> = await import("./index");
    expect(typeof mod.resetExtensionState).toBe("function");
  });

});
