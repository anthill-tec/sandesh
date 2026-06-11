/**
 * CR-SAN-013 C2 — RED: missing-CLI prerequisite probe (AC7)
 *
 * §S4 contract:
 *   On load, the extension calls pi.on("session_start", handler).
 *   The handler probes pi.exec("sandesh", ["--version"]).
 *   If the probe fails (non-zero code OR rejection), the handler calls
 *   ctx.ui.notify(<install-options message>, "warning") and does NOT throw.
 *   If the probe succeeds (code 0), no install notice is surfaced.
 *   In ALL cases the 9 tools are still registered (probe never blocks registration).
 *
 * Mechanism assumption (documented):
 *   §S4 says "on load, probe ... surface a clear one-time notice (ctx.ui.notify)".
 *   At extension load time there is no ctx — the only place ctx is available is
 *   an event handler. The API confirms pi.on("session_start", (event, ctx) => ...)
 *   is the canonical pattern for load-time setup that needs ctx.ui. This test
 *   therefore drives the session_start handler captured from pi.on() to verify
 *   the probe and notice behaviour. If the implementation places the probe
 *   elsewhere (e.g. directly in registerExtension via a top-level exec), the
 *   exec-called and no-throw assertions (AC7a/AC7d) still cover it, but the
 *   ctx.ui.notify path (AC7b) must use session_start.
 *
 * Asserts:
 *   AC7a — probe argv: pi.exec("sandesh", ["--version"], ...) is called
 *   AC7b — missing CLI → notice, no throw: ctx.ui.notify is called with a
 *           message that names sandesh + at least one install option
 *           (uv tool install / pipx / install.sh / PATH)
 *   AC7c — present CLI → no notice
 *   AC7d — verbs unaffected: registerTool called exactly 9 times regardless
 */

import { test, expect, describe, mock, beforeEach } from "bun:test";
import type {
  ExtensionAPI,
  ExtensionContext,
  ExecResult,
  SessionStartEvent,
  ExtensionHandler,
} from "@earendil-works/pi-coding-agent";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

/** Handler type captured from pi.on("session_start", ...) */
type SessionStartHandler = ExtensionHandler<SessionStartEvent>;

/**
 * Build a fake ExtensionAPI that:
 *  - captures registerTool calls (count + names)
 *  - captures pi.on("session_start", handler) so we can invoke it manually
 *  - has a scriptable exec mock
 */
function makeFakePi(probeResult: ExecResult | "reject") {
  const capturedTools = new Map<string, CapturedTool>();
  let sessionStartHandler: SessionStartHandler | undefined;

  const execMock = mock(
    async (_cmd: string, _args: string[], _opts?: unknown): Promise<ExecResult> => {
      if (probeResult === "reject") {
        throw new Error("sandesh: command not found");
      }
      return probeResult;
    },
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
  } as unknown as ExtensionAPI;

  return { fakePi, capturedTools, execMock, onMock, getSessionStartHandler: () => sessionStartHandler };
}

/**
 * Build a minimal fake ExtensionContext with a scriptable ui.notify.
 */
function makeFakeCtx() {
  const notifyCalls: Array<{ message: string; type?: string }> = [];
  const fakeCtx = {
    ui: {
      notify: mock((message: string, type?: "info" | "warning" | "error") => {
        notifyCalls.push({ message, type });
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
// AC7d — verbs always registered regardless of probe outcome (must pass count check)
// ---------------------------------------------------------------------------

describe("AC7d — 9 tools registered regardless of probe outcome", () => {
  test("all 9 tools registered when sandesh is present (code 0)", () => {
    const { fakePi, capturedTools } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);
    expect(capturedTools.size).toBe(9);
  });

  test("all 9 tools registered when sandesh is missing (non-zero code)", () => {
    const { fakePi, capturedTools } = makeFakePi({
      stdout: "",
      stderr: "sandesh: command not found",
      code: 127,
      killed: false,
    });
    registerExtension(fakePi);
    expect(capturedTools.size).toBe(9);
  });

  test("all 9 tools registered when probe rejects (exec throws)", () => {
    const { fakePi, capturedTools } = makeFakePi("reject");
    registerExtension(fakePi);
    expect(capturedTools.size).toBe(9);
  });

  test("registered tool names include all 9 expected names", () => {
    const { fakePi, capturedTools } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);
    const expectedNames = [
      "sandesh_setup",
      "sandesh_register",
      "sandesh_unregister",
      "sandesh_addressbook",
      "sandesh_send",
      "sandesh_reply",
      "sandesh_inbox",
      "sandesh_fetch",
      "sandesh_thread",
    ];
    for (const name of expectedNames) {
      expect(capturedTools.has(name)).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// AC7a — probe runs: pi.exec("sandesh", ["--version"]) is called
// ---------------------------------------------------------------------------

describe("AC7a — probe runs: pi.exec called with sandesh --version", () => {
  test("session_start handler calls pi.exec('sandesh', ['--version'])", async () => {
    const { fakePi, execMock, getSessionStartHandler } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    expect(handler).toBeDefined(); // session_start must be registered

    const { fakeCtx } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    // The probe must have been called
    expect(execMock.mock.calls.length).toBeGreaterThanOrEqual(1);

    // Find the --version probe call (not a verb call)
    const versionCall = execMock.mock.calls.find(
      (call) => {
        const [cmd, args] = call as [string, string[]];
        return cmd === "sandesh" && Array.isArray(args) && args[0] === "--version";
      },
    );
    expect(versionCall).toBeDefined();
  });

  test("pi.on('session_start', ...) is registered at load time (before any session)", () => {
    const { fakePi, onMock } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);

    // pi.on must have been called with "session_start"
    const sessionStartRegistration = onMock.mock.calls.find(
      (call) => (call as [string, unknown])[0] === "session_start",
    );
    expect(sessionStartRegistration).toBeDefined();
  });

  test("probe argv is exactly ['--version'] (not a subcommand)", async () => {
    const { fakePi, execMock, getSessionStartHandler } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    const versionCall = execMock.mock.calls.find(
      (call) => {
        const [cmd, args] = call as [string, string[]];
        return cmd === "sandesh" && Array.isArray(args) && args[0] === "--version";
      },
    ) as [string, string[]] | undefined;

    expect(versionCall).toBeDefined();
    expect(versionCall![0]).toBe("sandesh");
    expect(versionCall![1]).toEqual(["--version"]);
  });
});

// ---------------------------------------------------------------------------
// AC7b — missing CLI → notice, no throw
// ---------------------------------------------------------------------------

describe("AC7b — missing CLI (non-zero code) → notice surfaced, no throw", () => {
  test("non-zero exit code → ctx.ui.notify is called", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "",
      stderr: "command not found: sandesh",
      code: 127,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    expect(handler).toBeDefined();

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    // Must NOT throw
    await expect(handler!(fakeSessionStartEvent, fakeCtx)).resolves.toBeUndefined();

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("non-zero exit code → notice message contains 'sandesh'", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "",
      stderr: "command not found: sandesh",
      code: 127,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    expect(notice.message.toLowerCase()).toContain("sandesh");
  });

  test("non-zero exit code → notice message names at least one install option", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "",
      stderr: "command not found: sandesh",
      code: 127,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    const notice = notifyCalls[0];
    const msg = notice.message;
    // The notice must name at least one install option per §S4 + AC7
    const mentionsInstallOption =
      msg.includes("uv tool install") ||
      msg.includes("pipx") ||
      msg.includes("install.sh") ||
      msg.includes("PATH");
    expect(mentionsInstallOption).toBe(true);
  });

  test("non-zero exit code → notice is warning or error severity (not silent info)", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "",
      stderr: "command not found: sandesh",
      code: 127,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    // Must be surfaced as warning or error, not silently as info
    const noticeSeverity: string = notice!.type ?? "";
    expect(["warning", "error"]).toContain(noticeSeverity);
  });

  test("exec rejection (CLI not on PATH) → ctx.ui.notify is called, no throw", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi("reject");
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    expect(handler).toBeDefined();

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    // Must NOT throw even when exec rejects
    await expect(handler!(fakeSessionStartEvent, fakeCtx)).resolves.toBeUndefined();

    expect(notifyCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("exec rejection → notice names install options", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi("reject");
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    const notice = notifyCalls[0];
    expect(notice).toBeDefined();
    const msg = notice.message;
    const mentionsInstallOption =
      msg.includes("uv tool install") ||
      msg.includes("pipx") ||
      msg.includes("install.sh") ||
      msg.includes("PATH");
    expect(mentionsInstallOption).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AC7c — present CLI → NO notice
// ---------------------------------------------------------------------------

describe("AC7c — present CLI (code 0) → no install notice surfaced", () => {
  test("probe succeeds (code 0) → ctx.ui.notify not called", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "sandesh 1.2.3",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    expect(handler).toBeDefined();

    const { fakeCtx, notifyCalls } = makeFakeCtx();
    await handler!(fakeSessionStartEvent, fakeCtx);

    // CLI present must NOT trigger the missing-CLI / install notice.
    // (A missing-env notice may still surface — that's CR-SAN-014 AC4 — so we
    // assert specifically that none of the notices carry install wording.)
    const hasInstallNotice = notifyCalls.some((c) => {
      const msg = c.message;
      return (
        msg.includes("uv tool install") ||
        msg.includes("pipx") ||
        msg.includes("install.sh") ||
        msg.includes("PATH")
      );
    });
    expect(hasInstallNotice).toBe(false);
  });

  test("probe succeeds → handler does not throw", async () => {
    const { fakePi, getSessionStartHandler } = makeFakePi({
      stdout: "sandesh 1.0.0",
      stderr: "",
      code: 0,
      killed: false,
    });
    registerExtension(fakePi);

    const handler = getSessionStartHandler();
    const { fakeCtx } = makeFakeCtx();

    await expect(handler!(fakeSessionStartEvent, fakeCtx)).resolves.toBeUndefined();
  });
});
