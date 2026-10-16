/**
 * CR-SAN-055 §S2 / AC5 — RED: `sandesh_unregister` stops THIS session's own
 * watcher before calling the CLI's `unregister` (Pi integration, production
 * `registerExtension`, fake `pi`).
 *
 * RED reason: today's `sandesh_unregister` (index.ts l.770-792) builds the
 * CLI argv and calls `runSandesh` directly — it never looks at the
 * supervisor (`stopWatchers` is wired only into `sandesh_notify_stop` and the
 * `/sandesh-watcher stop` command, index.ts l.1272/l.1303). A watcher this
 * session started for the unregistered address is therefore left running:
 * its AbortSignal is never aborted (the call-order assertions below never
 * see "watcher-aborted"), `sandesh_notify_status` still reports it
 * `running:true`, and the only other-address scoping tests pass today
 * incidentally because nothing is ever stopped — see the note on the last
 * describe block.
 */

import { test, expect, describe, beforeAll } from "bun:test";
import type { ExecResult, ExtensionAPI, ExtensionContext, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { encode } from "@toon-format/toon";
import registerExtension, { __setStartSettleMs } from "./index";
import { decodeEnvelope } from "./toon";

// CR-SAN-053 §S3: several scenarios here start a watcher with a pending
// (never-resolved) notify child; the production `sandesh_notify_start` path
// awaits `settle(address, START_SETTLE_MS)` after a successful start, so the
// window is forced to 0 for this whole file (mirrors wake.test.ts).
beforeAll(() => {
  __setStartSettleMs(0);
});

// ─── Deferred helper (mirrors wake.test.ts / wake_lifecycle.test.ts) ──────

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

// ─── Fixture: the CLI's own `unregister` TOON envelope ─────────────────────

function unregisterEnvelope(address: string): string {
  return encode({
    axi: {
      verb: "unregister",
      ok: true,
      address,
      project: "Demo",
      result: "unregistered",
      context: { project: "Demo", address },
      help: ["sandesh addressbook"],
      warnings: [],
    },
  });
}

// ─── Fake exec — routes by argv content, and records the ordering between a
// watcher's AbortSignal firing and the CLI's `unregister` exec call. ───────

interface NotifyDeferred {
  address: string;
  signal?: AbortSignal;
  resolve: (r: ExecResult) => void;
}

function makeFakeExec(order: string[]) {
  const notifyDeferreds: NotifyDeferred[] = [];
  const unregisterCalls: string[][] = [];

  const exec = async (_cmd: string, args: string[], opts?: { signal?: AbortSignal }): Promise<ExecResult> => {
    if (args.includes("--version")) {
      return { stdout: "sandesh 0.4.2", stderr: "", code: 0, killed: false };
    }
    if (args.includes("init")) {
      return { stdout: "", stderr: "", code: 0, killed: false };
    }
    if (args.includes("notify")) {
      const addressIndex = args.indexOf("--to");
      const address = addressIndex !== -1 ? args[addressIndex + 1] : "";
      const d = makeDeferred<ExecResult>();
      opts?.signal?.addEventListener("abort", () => order.push(`watcher-aborted:${address}`));
      notifyDeferreds.push({ address, signal: opts?.signal, resolve: d.resolve });
      return d.promise;
    }
    if (args.includes("unregister")) {
      const addressIndex = args.indexOf("--address");
      const address = addressIndex !== -1 ? args[addressIndex + 1] : "";
      order.push(`unregister-exec-called:${address}`);
      unregisterCalls.push(args);
      return { stdout: unregisterEnvelope(address), stderr: "", code: 0, killed: false };
    }
    return { stdout: "", stderr: "", code: 0, killed: false };
  };

  return { exec, notifyDeferreds, unregisterCalls };
}

function makeFakePi(order: string[]) {
  const capturedTools = new Map<string, ToolDefinition<any, any, any>>();
  const { exec, notifyDeferreds, unregisterCalls } = makeFakeExec(order);
  const fakePi = {
    registerTool: (tool: ToolDefinition<any, any, any>) => {
      capturedTools.set(tool.name, tool);
    },
    registerCommand: () => {},
    on: () => {},
    exec,
    sendUserMessage: () => {},
  } as unknown as ExtensionAPI;
  return { fakePi, capturedTools, notifyDeferreds, unregisterCalls };
}

function makeFakeCtx() {
  const notifyCalls: Array<{ msg: string; type?: string }> = [];
  const fakeCtx = {
    ui: {
      notify: (msg: string, type?: "info" | "warning" | "error") => {
        notifyCalls.push({ msg, type });
      },
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

async function watcherRunning(
  capturedTools: Map<string, ToolDefinition<any, any, any>>,
  fakeCtx: ExtensionContext,
  address: string,
): Promise<boolean> {
  const statusResult = await callExecute(getTool(capturedTools, "sandesh_notify_status"), {}, fakeCtx);
  const watchers = decodeEnvelope(text(statusResult)).fields.watchers as Array<{
    address: string;
    running: boolean;
  }>;
  const entry = watchers.find((w) => w.address === address);
  if (entry === undefined) throw new Error(`no watcher status row for ${address}`);
  return entry.running;
}

// ============================================================================
// AC5 — sandesh_unregister stops this session's own watcher first (§S2)
// ============================================================================

describe("AC5 — sandesh_unregister stops this session's own watcher before the CLI call", () => {
  test("aborts the watcher's AbortSignal before the CLI unregister exec is called, and still returns the CLI's envelope", async () => {
    const order: string[] = [];
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi(order);
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();

    const startTool = getTool(capturedTools, "sandesh_notify_start");
    await callExecute(startTool, { address: "Mainline - Demo", project: "Demo" }, fakeCtx);
    expect(notifyDeferreds.length).toBe(1);
    expect(notifyDeferreds[0].signal?.aborted).toBe(false);

    const unregisterTool = getTool(capturedTools, "sandesh_unregister");
    const result = await callExecute(
      unregisterTool,
      { address: "Mainline - Demo", requester: "Mainline - Demo" },
      fakeCtx,
    );

    // The real RED signal: today the watcher is never stopped, so
    // "watcher-aborted:Mainline - Demo" never lands in `order` — it stays
    // ["unregister-exec-called:Mainline - Demo"] and this fails.
    expect(order).toEqual(["watcher-aborted:Mainline - Demo", "unregister-exec-called:Mainline - Demo"]);

    // The CLI's own envelope must still flow through unchanged.
    const env = decodeEnvelope(text(result));
    expect(env.verb).toBe("unregister");
    expect(env.ok).toBe(true);
    expect(env.fields.result).toBe("unregistered");
  });

  test("leaves the watcher running:false and posts no UI notify after unregistering", async () => {
    const order: string[] = [];
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi(order);
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();

    await callExecute(
      getTool(capturedTools, "sandesh_notify_start"),
      { address: "Mainline - Demo", project: "Demo" },
      fakeCtx,
    );
    expect(notifyDeferreds.length).toBe(1);

    await callExecute(
      getTool(capturedTools, "sandesh_unregister"),
      { address: "Mainline - Demo", requester: "Mainline - Demo" },
      fakeCtx,
    );

    // Real RED signal: the watcher is still running:true today (never
    // stopped), so this fails before the quiet check below is even reached.
    expect(await watcherRunning(capturedTools, fakeCtx, "Mainline - Demo")).toBe(false);

    // A requested stop is quiet (WakeSupervisor.stop's `{requested:true}`
    // path never calls `deps.notify`) — no UI notify for the stop itself.
    expect(notifyCalls.length).toBe(0);
  });

  test("does not touch a different address's running watcher in the same session", async () => {
    const order: string[] = [];
    const { fakePi, capturedTools, notifyDeferreds } = makeFakePi(order);
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();

    await callExecute(
      getTool(capturedTools, "sandesh_notify_start"),
      { address: "Mainline - Demo", project: "Demo" },
      fakeCtx,
    );
    await callExecute(
      getTool(capturedTools, "sandesh_notify_start"),
      { address: "Track 1 - Demo", project: "Demo" },
      fakeCtx,
    );
    expect(notifyDeferreds.length).toBe(2);
    const trackDeferred = notifyDeferreds.find((d) => d.address === "Track 1 - Demo")!;
    expect(trackDeferred.signal?.aborted).toBe(false);

    await callExecute(
      getTool(capturedTools, "sandesh_unregister"),
      { address: "Mainline - Demo", requester: "Mainline - Demo" },
      fakeCtx,
    );

    // Real RED signal: Mainline's own watcher must be stopped — fails today.
    expect(order).toContain("watcher-aborted:Mainline - Demo");
    expect(await watcherRunning(capturedTools, fakeCtx, "Mainline - Demo")).toBe(false);

    // The untouched address: signal never fires, stays running.
    expect(trackDeferred.signal?.aborted).toBe(false);
    expect(order).not.toContain("watcher-aborted:Track 1 - Demo");
    expect(await watcherRunning(capturedTools, fakeCtx, "Track 1 - Demo")).toBe(true);
  });

  // NOTE on this describe block — a documented coincidental pass (same
  // precedent as wake_lifecycle.test.ts's AUTOSTART-vars-unset case): when
  // the unregistered address has NO watcher in this session at all, nothing
  // is touched either today (sandesh_unregister never looks at the
  // supervisor) or under a correct §S2 implementation (sup.stop() finds no
  // matching entry). There is no behavioural difference to RED on for this
  // specific input; it stands as a regression guard against a GREEN that
  // stops the wrong/every watcher instead of scoping to `params.address`.
  test("an address this session isn't watching is passed straight to the CLI, untouched (regression guard — see note above)", async () => {
    const order: string[] = [];
    const { fakePi, capturedTools, notifyDeferreds, unregisterCalls } = makeFakePi(order);
    registerExtension(fakePi);
    const { fakeCtx } = makeFakeCtx();

    await callExecute(
      getTool(capturedTools, "sandesh_notify_start"),
      { address: "Track 1 - Demo", project: "Demo" },
      fakeCtx,
    );
    expect(notifyDeferreds.length).toBe(1);
    const trackDeferred = notifyDeferreds[0];

    const result = await callExecute(
      getTool(capturedTools, "sandesh_unregister"),
      { address: "Mainline - Demo", requester: "Mainline - Demo" },
      fakeCtx,
    );

    expect(order).toEqual(["unregister-exec-called:Mainline - Demo"]);
    expect(trackDeferred.signal?.aborted).toBe(false);
    expect(await watcherRunning(capturedTools, fakeCtx, "Track 1 - Demo")).toBe(true);
    expect(unregisterCalls.length).toBe(1);
    expect(unregisterCalls[0]).toContain("Mainline - Demo");

    const env = decodeEnvelope(text(result));
    expect(env.fields.result).toBe("unregistered");
  });
});
