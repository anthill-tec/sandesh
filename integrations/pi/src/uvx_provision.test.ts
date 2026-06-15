/**
 * CR-SAN-038 C2 — RED: Pi uvx-on-demand CLI resolution + provision nudge
 *
 * §S1 — uvx-on-demand:
 *   When the `sandesh --version` probe fails (non-zero exit OR exec rejection),
 *   the extension resolves the CLI via `uvx` with prefix args
 *   ["--from", "sandesh-relay[migrate]", "sandesh", ...].
 *   When the probe succeeds, the local `sandesh` binary is used directly.
 *   This shared resolution is applied at ALL three exec sites:
 *     1. the `--version` probe itself
 *     2. runSandesh verb calls
 *     3. the `notify` wake-loop call
 *
 * §S2 — provision nudge:
 *   After a successful ≥0.2.0 version check, the session_start handler runs
 *   `sandesh init --check`. On non-zero exit, it emits a one-line
 *   ctx.ui.notify naming `sandesh init`. On exit 0, no nudge is emitted.
 *
 * Harness: mirrors version_gate.test.ts — execSequence-based makeFakePi,
 * makeFakeCtx with notifyCalls capture, fireSessionStart + drainMicrotasks.
 *
 * AC1 — uvx invocation (bun)
 * AC2 — provision nudge (bun)
 * AC3 — version gate preserved (regression — existing gate must still fire)
 * AC4 — no init/admin/migrate tool registered (tool count stays 12)
 * AC5 — error passthrough (verbatim stderr, no self-install spawned)
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
import registerExtension, { __resetWakeState } from "./index";

type SessionStartHandler = ExtensionHandler<SessionStartEvent>;
type CapturedTool = ToolDefinition<any, any, any>;

// ── Sequence-based exec factory ───────────────────────────────────────────────

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

// ── Fake-Pi factory (captures ALL exec calls for argv inspection) ─────────────

interface FakePiOptions {
  execSequence: Array<ExecResult | "reject">;
}

function makeFakePi(opts: FakePiOptions) {
  const capturedTools = new Map<string, CapturedTool>();
  let sessionStartHandler: SessionStartHandler | undefined;

  // execMock records every call so tests can assert cmd + argv
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
    exec: execMock,
    sendUserMessage: sendUserMessageMock,
    on: onMock,
  } as unknown as ExtensionAPI;

  return {
    fakePi,
    capturedTools,
    execMock,
    sendUserMessageMock,
    getSessionStartHandler: () => sessionStartHandler,
  };
}

// ── Fake context factory ──────────────────────────────────────────────────────

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

// ── Helpers ───────────────────────────────────────────────────────────────────

async function drainMicrotasks(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

async function fireSessionStart(
  handler: SessionStartHandler,
  fakeCtx: ExtensionContext,
): Promise<void> {
  await handler(fakeSessionStartEvent, fakeCtx);
  await drainMicrotasks();
}

/** Extract the session_start handler captured by pi.on */
function getHandler(fakePi: ExtensionAPI): SessionStartHandler {
  const onMock = fakePi.on as ReturnType<typeof mock>;
  const entry = (onMock.mock.calls as Array<[string, unknown]>).find(
    ([e]) => e === "session_start",
  );
  expect(entry).toBeDefined();
  return entry![1] as SessionStartHandler;
}

/**
 * Return all exec calls whose first positional arg is the command string.
 * Bun mock stores calls as [cmd, args, opts?].
 */
function allExecCalls(
  execMock: ReturnType<typeof mock>,
): Array<[string, string[], unknown?]> {
  return execMock.mock.calls as Array<[string, string[], unknown?]>;
}

// ── Env save/restore ──────────────────────────────────────────────────────────

const SAVED_ENV: Partial<Record<string, string>> = {};

beforeEach(() => {
  __resetWakeState();
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

// ══════════════════════════════════════════════════════════════════════════════
// AC1 — uvx-on-demand CLI resolution
// ══════════════════════════════════════════════════════════════════════════════

describe("AC1 — uvx-on-demand: probe site — version check uses uvx when sandesh absent", () => {
  /**
   * When the first probe (`--version` check via direct `sandesh`) fails with
   * non-zero exit, the extension must RETRY using `uvx --from sandesh-relay[migrate] sandesh`.
   * The second exec call must have cmd "uvx" and args starting with
   * ["--from", "sandesh-relay[migrate]", "sandesh", "--version"].
   */
  test("AC1a — probe fails (non-zero) → next exec uses 'uvx' as the command", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Sequence: direct sandesh probe fails, uvx probe succeeds with 0.2.0,
    // then init --check succeeds (0), then notify exits 3 (terminal).
    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127, "sandesh: command not found"),
        ok("sandesh 0.2.0"),
        ok(""),         // init --check → 0 (provisioned)
        exit(3),        // notify → terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    // Must have made at least 2 calls
    expect(calls.length).toBeGreaterThanOrEqual(2);
    // The second call (index 1) must use "uvx" as the command
    const [cmd2, args2] = calls[1];
    expect(cmd2).toBe("uvx");
    // and must include "--from" + "sandesh-relay[migrate]" prefix
    expect(args2[0]).toBe("--from");
    expect(args2[1]).toBe("sandesh-relay[migrate]");
  });

  test("AC1b — probe fails (exec throws) → next exec uses 'uvx' as the command", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        "reject",               // direct sandesh --version throws
        ok("sandesh 0.2.0"),   // uvx sandesh --version succeeds
        ok(""),                 // init --check
        exit(3),                // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    expect(calls.length).toBeGreaterThanOrEqual(2);
    const [cmd2, args2] = calls[1];
    expect(cmd2).toBe("uvx");
    expect(args2[0]).toBe("--from");
    expect(args2[1]).toBe("sandesh-relay[migrate]");
  });

  test("AC1c — probe via uvx: --version arg is forwarded after the uvx prefix", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127),
        ok("sandesh 0.2.0"),
        ok(""),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    expect(calls.length).toBeGreaterThanOrEqual(2);
    const [cmd2, args2] = calls[1];
    expect(cmd2).toBe("uvx");
    // Full argv: ["--from", "sandesh-relay[migrate]", "sandesh", "--version"]
    expect(args2).toEqual(["--from", "sandesh-relay[migrate]", "sandesh", "--version"]);
  });

  test("AC1d — probe extra is 'sandesh-relay[migrate]' (NOT mcp, NOT sandesh-relay[mcp])", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127),
        ok("sandesh 0.2.0"),
        ok(""),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    const uvxCall = calls.find(([cmd]) => cmd === "uvx");
    expect(uvxCall).toBeDefined();
    const [, args] = uvxCall!;
    const fromIdx = args.indexOf("--from");
    expect(fromIdx).toBeGreaterThanOrEqual(0);
    const extraValue = args[fromIdx + 1];
    // Must contain "migrate", must NOT contain "mcp"
    expect(extraValue).toContain("migrate");
    expect(extraValue).not.toContain("mcp");
    expect(extraValue).toBe("sandesh-relay[migrate]");
  });
});

describe("AC1 — uvx-on-demand: local sandesh present → uses 'sandesh' directly", () => {
  test("AC1e — probe succeeds (code 0) → --version probe used cmd 'sandesh'", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),  // direct sandesh --version succeeds
        ok(""),               // init --check
        exit(3),              // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    // First call is the --version probe — must use "sandesh"
    const [cmd1, args1] = calls[0];
    expect(cmd1).toBe("sandesh");
    expect(args1).toContain("--version");

    // No uvx calls when sandesh is present
    const uvxCalls = calls.filter(([cmd]) => cmd === "uvx");
    expect(uvxCalls.length).toBe(0);
  });

  test("AC1f — local sandesh: verb calls still use 'sandesh' (not uvx)", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const execMock = mock(
      async (_cmd: string, _args: string[]): Promise<ExecResult> =>
        ok("done"),
    );
    const fakePi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: execMock,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);

    const tool = capturedTools.get("sandesh_setup")!;
    expect(tool).toBeDefined();
    await tool.execute("call-id", {}, undefined, undefined, {} as any);

    const calls = allExecCalls(execMock);
    const verbCall = calls[calls.length - 1];
    expect(verbCall[0]).toBe("sandesh");
  });
});

describe("AC1 — uvx-on-demand: verb exec site uses uvx when sandesh is absent", () => {
  /**
   * When the local sandesh probe failed (falling back to uvx), subsequent verb
   * calls (runSandesh) must ALSO use uvx, not the local sandesh binary.
   * This verifies the resolution is shared, not just applied to the probe.
   */
  test("AC1g — verb call after uvx-fallback uses 'uvx' command", async () => {
    // We exercise this by calling a tool's execute() after the session_start
    // has established that the local sandesh is absent.
    // The extension must store the resolved binary choice and apply it to
    // subsequent exec calls (the verb path), not re-probe each time.
    const capturedTools = new Map<string, CapturedTool>();
    const execCalls: Array<[string, string[]]> = [];

    let callIdx = 0;
    // Sequence: --version probe fails, uvx --version succeeds, then verb call
    const execSequence: Array<ExecResult | "reject"> = [
      exit(127, "sandesh: command not found"),  // direct probe fails
      ok("sandesh 0.2.0"),                      // uvx probe succeeds
      ok(""),                                   // init --check (provisioned)
      ok("setup done"),                         // setup verb call
      exit(3),                                  // notify terminal
    ];

    const execMock = mock(
      async (cmd: string, args: string[]): Promise<ExecResult> => {
        execCalls.push([cmd, args]);
        const entry = execSequence[callIdx];
        if (callIdx < execSequence.length - 1) callIdx++;
        if (entry === "reject") throw new Error("sandesh: command not found");
        return entry;
      },
    );

    let sessionStartHandler: SessionStartHandler | undefined;
    const fakePi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock((event: string, handler: unknown) => {
        if (event === "session_start") {
          sessionStartHandler = handler as SessionStartHandler;
        }
      }),
      exec: execMock,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;

    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    registerExtension(fakePi);
    expect(sessionStartHandler).toBeDefined();

    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(sessionStartHandler!, fakeCtx);

    // Now call a verb tool
    const setupTool = capturedTools.get("sandesh_setup")!;
    expect(setupTool).toBeDefined();
    await setupTool.execute("call-id", { project_id: "Demo" }, undefined, undefined, {} as any);

    // The verb call must have used "uvx" (sandesh was absent)
    const verbCall = execCalls.find(([cmd, args]) =>
      cmd === "uvx" && args.some((a) => a === "setup"),
    );
    expect(verbCall).toBeDefined();
    // Verify the full uvx prefix
    const [, verbArgs] = verbCall!;
    expect(verbArgs[0]).toBe("--from");
    expect(verbArgs[1]).toBe("sandesh-relay[migrate]");
    expect(verbArgs[2]).toBe("sandesh");
  });

  test("AC1h — notify wake call uses 'uvx' when sandesh is absent", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Sequence: direct probe fails, uvx probe succeeds, init --check ok,
    // then notify call (exit 2 = timeout, then exit 3 = terminal to stop)
    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127),              // direct sandesh --version fails
        ok("sandesh 0.2.0"),   // uvx sandesh --version succeeds
        ok(""),                 // init --check (provisioned)
        exit(2),                // notify → timeout (re-arm)
        exit(3),                // notify → terminal (stop)
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);

    // Find a call that includes "notify" in its args — it must use "uvx"
    const notifyCall = calls.find(([, args]) => args.some((a) => a === "notify"));
    expect(notifyCall).toBeDefined();
    const [notifyCmd, notifyArgs] = notifyCall!;
    expect(notifyCmd).toBe("uvx");
    expect(notifyArgs[0]).toBe("--from");
    expect(notifyArgs[1]).toBe("sandesh-relay[migrate]");
    expect(notifyArgs[2]).toBe("sandesh");
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// AC2 — provision nudge
// ══════════════════════════════════════════════════════════════════════════════

describe("AC2 — provision nudge: init --check is called after version check", () => {
  test("AC2a — session_start calls 'sandesh init --check' after a passing version probe", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),  // --version probe passes
        ok(""),               // init --check → 0 (provisioned)
        exit(3),              // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    // Must have called init --check: either via "sandesh" or "uvx" with "init" + "--check" in args
    const initCheckCall = calls.find(([, args]) =>
      args.includes("init") && args.includes("--check"),
    );
    expect(initCheckCall).toBeDefined();
  });

  test("AC2b — init --check argv includes 'init' and '--check' (exact subcommand)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        ok(""),               // init --check
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    const initCall = calls.find(([, args]) =>
      args.includes("init") && args.includes("--check"),
    );
    expect(initCall).toBeDefined();
    const [, initArgs] = initCall!;
    expect(initArgs).toContain("init");
    expect(initArgs).toContain("--check");
  });

  test("AC2c — init --check is NOT called when version probe fails (version gate blocks it)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Non-zero exit from --version → missing-CLI path, no init --check
    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127),  // --version probe fails → missing-CLI path
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    const initCheckCall = calls.find(([, args]) =>
      args.includes("init") && args.includes("--check"),
    );
    expect(initCheckCall).toBeUndefined();
  });

  test("AC2d — init --check is NOT called when version is too old (0.1.0)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 0.1.0"),  // version gate fires → too-old path
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    const initCheckCall = calls.find(([, args]) =>
      args.includes("init") && args.includes("--check"),
    );
    expect(initCheckCall).toBeUndefined();
  });
});

describe("AC2 — provision nudge: non-zero init --check → nudge emitted", () => {
  test("AC2e — init --check exits non-zero → ctx.ui.notify is called", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),                              // version ok
        exit(1, "store not provisioned: DB absent"),      // init --check fails
        exit(3),                                          // notify terminal (not armed)
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Must have emitted a nudge
    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("AC2f — init --check exits non-zero → nudge names 'sandesh init'", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        exit(1, "store not provisioned: DB absent"),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Nudge must mention "sandesh init"
    const nudge = notifyCalls.find((c) =>
      c.msg.toLowerCase().includes("sandesh init"),
    );
    expect(nudge).toBeDefined();
  });

  test("AC2g — init --check exits non-zero → nudge surfaces the CLI's own message", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const storeAbsentMsg = "store not provisioned: DB absent";
    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        exit(1, storeAbsentMsg),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // The nudge should carry the probe's stderr so the user sees the reason
    const nudge = notifyCalls.find((c) => c.msg.includes(storeAbsentMsg));
    expect(nudge).toBeDefined();
  });

  test("AC2h — init --check exits non-zero (admin unset msg) → nudge surfaces admin-unset reason", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const adminUnsetMsg = "store present but admin not assigned";
    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        exit(1, adminUnsetMsg),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const nudge = notifyCalls.find((c) => c.msg.includes(adminUnsetMsg));
    expect(nudge).toBeDefined();
  });

  test("AC2i — init --check non-zero: nudge does NOT throw (handler is non-throwing)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        exit(1, "store not provisioned"),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await expect(fireSessionStart(handler, fakeCtx)).resolves.toBeUndefined();
  });
});

describe("AC2 — provision nudge: exit 0 from init --check → NO nudge", () => {
  test("AC2j — provisioned store (exit 0) → no provision nudge emitted", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),  // version ok
        ok(""),               // init --check → 0 (provisioned)
        exit(3),              // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // No nudge that mentions "sandesh init" must have been emitted
    const nudge = notifyCalls.find((c) =>
      c.msg.toLowerCase().includes("sandesh init"),
    );
    expect(nudge).toBeUndefined();
  });

  test("AC2k — provisioned store: no extra notify calls injected by init --check", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // No env warnings (both env vars set) + provisioned → expect 0 notifyCalls total
    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        ok(""),
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // With both env vars set + version ok + provisioned, no notify should fire
    expect(notifyCalls.length).toBe(0);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// AC3 — version gate preserved (regression)
// ══════════════════════════════════════════════════════════════════════════════

describe("AC3 — version gate preserved: out-of-date CLI still fires warning", () => {
  test("AC3-regression-a — sandesh 0.1.0 still triggers OUTDATED notice (not provision nudge)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.1.0"),  // too old
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // Exactly one notice: the outdated-CLI warning
    expect(notifyCalls.length).toBe(1);
    const notice = notifyCalls[0];
    expect(notice.msg).toContain("0.2.0");
    // It must NOT be the provision nudge
    expect(notice.msg.toLowerCase()).not.toContain("sandesh init");
  });

  test("AC3-regression-b — missing CLI (non-zero probe) still fires install notice, not provision nudge", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi } = makeFakePi({
      execSequence: [exit(127)],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
    // No init nudge on the missing-CLI path
    const initNudge = notifyCalls.find((c) =>
      c.msg.toLowerCase().includes("sandesh init"),
    );
    expect(initNudge).toBeUndefined();
  });

  test("AC3-regression-c — sandesh 1.0.0 (well above min) still arms the wake loop", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        ok("sandesh 1.0.0"),
        ok(""),     // init --check
        exit(3),    // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // No version-gate notice
    const versionGateNotice = notifyCalls.find((c) => c.msg.includes("0.2.0"));
    expect(versionGateNotice).toBeUndefined();

    // Wake loop armed: notify was called
    const calls = allExecCalls(execMock);
    const notifyCall = calls.find(([, args]) => args.some((a) => a === "notify"));
    expect(notifyCall).toBeDefined();
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// AC4 — no init/admin/migrate tool registered
// ══════════════════════════════════════════════════════════════════════════════

describe("AC4 — no new provisioning tools (tool count still 12, no init/check/admin/migrate)", () => {
  test("AC4a — registerTool is still called exactly 12 times", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    expect(capturedTools.size).toBe(12);
  });

  test("AC4b — no registered tool name contains 'init'", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    for (const name of capturedTools.keys()) {
      expect(name).not.toContain("init");
    }
  });

  test("AC4c — no registered tool name contains 'check'", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    for (const name of capturedTools.keys()) {
      expect(name).not.toContain("check");
    }
  });

  test("AC4d — no registered tool name contains 'admin'", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    for (const name of capturedTools.keys()) {
      expect(name).not.toContain("admin");
    }
  });

  test("AC4e — no registered tool name contains 'migrate'", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    for (const name of capturedTools.keys()) {
      expect(name).not.toContain("migrate");
    }
  });

  test("AC4f — the 12 exact tool names are unchanged", () => {
    const { fakePi, capturedTools } = makeFakePi({
      execSequence: [ok("sandesh 0.2.0")],
    });
    registerExtension(fakePi);
    const names = Array.from(capturedTools.keys()).sort();
    expect(names).toEqual([
      "sandesh_addressbook",
      "sandesh_archive",
      "sandesh_fetch",
      "sandesh_inbox",
      "sandesh_register",
      "sandesh_reply",
      "sandesh_search",
      "sandesh_send",
      "sandesh_setup",
      "sandesh_thread",
      "sandesh_unarchive",
      "sandesh_unregister",
    ]);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// AC5 — error passthrough (verbatim stderr, no self-install)
// ══════════════════════════════════════════════════════════════════════════════

describe("AC5 — error passthrough: CLI errors surface verbatim, no self-install spawned", () => {
  test("AC5a — non-zero verb exit → thrown error contains verbatim stderr", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const verbStderr = "cross-project sending not approved for project 'Demo' — ask the Sandesh admin";
    const execMock = mock(
      async (): Promise<ExecResult> => ({
        stdout: "",
        stderr: verbStderr,
        code: 1,
        killed: false,
      }),
    );
    const fakePi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: execMock,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);
    const tool = capturedTools.get("sandesh_send")!;
    expect(tool).toBeDefined();

    let caught: Error | undefined;
    try {
      await tool.execute(
        "call-id",
        { from: "Mainline - Demo", to: ["Mainline - Other"], subject: "ping" },
        undefined,
        undefined,
        {} as any,
      );
    } catch (e) {
      caught = e as Error;
    }
    expect(caught).toBeDefined();
    expect(caught!.message).toContain(verbStderr);
  });

  test("AC5b — no 'pip install' or 'uv install' exec call emitted by the extension", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    // Even when the direct probe fails (uvx path), the extension must NOT
    // spawn a `pip install` or `uv install` to self-provision.
    const { fakePi, execMock } = makeFakePi({
      execSequence: [
        exit(127),              // direct probe fails
        ok("sandesh 0.2.0"),   // uvx probe succeeds
        ok(""),                 // init --check
        exit(3),                // notify terminal
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    const calls = allExecCalls(execMock);
    // No call with "pip" or "uv install" as command or arg
    const installCall = calls.find(([cmd, args]) =>
      cmd === "pip" ||
      cmd === "pip3" ||
      (cmd === "uv" && args.includes("install")) ||
      args.includes("pip") ||
      args.includes("pip3"),
    );
    expect(installCall).toBeUndefined();
  });

  test("AC5c — [migrate]-absent error surfaces verbatim (no shim message injected)", async () => {
    const capturedTools = new Map<string, CapturedTool>();
    const migrateAbsentStderr =
      "No module named yoyo; install sandesh[migrate] to use migration commands";
    const execMock = mock(
      async (): Promise<ExecResult> => ({
        stdout: "",
        stderr: migrateAbsentStderr,
        code: 1,
        killed: false,
      }),
    );
    const fakePi = {
      registerTool: mock((tool: CapturedTool) => capturedTools.set(tool.name, tool)),
      on: mock(() => {}),
      exec: execMock,
      sendUserMessage: mock(() => {}),
    } as unknown as ExtensionAPI;

    registerExtension(fakePi);
    const tool = capturedTools.get("sandesh_setup")!;

    let caught: Error | undefined;
    try {
      await tool.execute("call-id", {}, undefined, undefined, {} as any);
    } catch (e) {
      caught = e as Error;
    }
    expect(caught).toBeDefined();
    // stderr must appear verbatim in the thrown error
    expect(caught!.message).toContain(migrateAbsentStderr);
  });

  test("AC5d — provision error from init --check propagates verbatim in the nudge (not swallowed)", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";

    const provisionStderr = "store not provisioned: DB absent at /path/to/sandesh.db";
    const { fakePi } = makeFakePi({
      execSequence: [
        ok("sandesh 0.2.0"),
        exit(1, provisionStderr),   // init --check: non-zero with message
        exit(3),
      ],
    });

    registerExtension(fakePi);
    const handler = getHandler(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await fireSessionStart(handler, fakeCtx);

    // The nudge must carry the CLI's stderr verbatim
    const nudge = notifyCalls.find((c) => c.msg.includes(provisionStderr));
    expect(nudge).toBeDefined();
  });
});
