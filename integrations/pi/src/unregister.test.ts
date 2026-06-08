/**
 * CR-SAN-019 C1 — RED: tombstone-aware unregister (§S1, AC1–AC4)
 *
 * AC1 — unregister with r.code === 3 returns a SUCCESS result (not thrown)
 *        whose content[0].text contains the tombstone message.
 * AC2 — unregister with r.code === 1 (any non-0/3) still throws an Error
 *        whose message carries verb + exit code + stderr.
 * AC3 — the exit-3 special-case is scoped to unregister: another verb (send)
 *        returning r.code === 3 still throws.
 * AC4 — unregister with r.code === 0 returns the normal success result.
 *
 * These tests FAIL at RED because runSandesh currently throws on ALL non-zero
 * exits (index.ts:140-141), so AC1 fails (it throws instead of returning a
 * success result for code 3), and AC3 requires the code-3 throw to remain
 * scoped — no change yet means AC3 would also fail on the wrong reason once
 * the AC1 path is present.  The RED state is: AC1 rejects.toThrow (not
 * resolves), and the AC3 verify-throw still holds today only because the
 * scoping hasn't been written yet.
 *
 * NOTE: AC5 (real-binary smoke test) is §S2 — a separate Cycle 2 dispatch.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Helpers (mirrors execute.test.ts pattern exactly)
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

function makeFakePi(
  execResult: ExecResult = { stdout: "ok-output", stderr: "", code: 0, killed: false },
) {
  const capturedTools = new Map<string, CapturedTool>();

  const execMock = mock(
    async (_cmd: string, _args: string[], _opts?: unknown): Promise<ExecResult> => execResult,
  );

  const fakePi = {
    registerTool: mock((tool: CapturedTool) => {
      capturedTools.set(tool.name, tool);
    }),
    exec: execMock,
    on: mock(() => {}),
  } as unknown as ExtensionAPI;

  return { fakePi, capturedTools, execMock };
}

/** Call a tool's execute() with null signal/onUpdate/ctx — the SUT only uses
 *  params and pi.exec, so we pass minimal stubs for the rest. */
async function callExecute(tool: CapturedTool, params: Record<string, unknown>) {
  return tool.execute("test-call-id", params, undefined, undefined, {} as any);
}

/** Register all tools and return a helper to get one by name. */
function setup(execResult?: ExecResult) {
  const harness = makeFakePi(execResult);
  registerExtension(harness.fakePi);
  function getTool(name: string): CapturedTool {
    const t = harness.capturedTools.get(name);
    if (!t) throw new Error(`Tool "${name}" not registered`);
    return t;
  }
  return { ...harness, getTool };
}

// ---------------------------------------------------------------------------
// AC1 — unregister exit 3 → success result carrying tombstone message
// ---------------------------------------------------------------------------

describe("AC1 — sandesh_unregister: exit 3 returns success result (tombstone)", () => {
  const tombstoneMsg =
    "tombstone set on Track 1 - Demo (notifier pid 12345). It stops within one poll; re-run once `addressbook` shows it offline.";

  test("execute resolves (does not reject) when pi.exec returns code 3", async () => {
    const { getTool } = setup({
      stdout: tombstoneMsg,
      stderr: "",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    // Must resolve, not reject — if the current throw-on-any-nonzero is in
    // place this expectation fails, which is the valid RED.
    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result).toBeDefined();
  });

  test("result content[0].text contains the tombstone message when code is 3", async () => {
    const { getTool } = setup({
      stdout: tombstoneMsg,
      stderr: "",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result.content[0].type).toBe("text");
    expect(result.content[0].text).toContain("tombstone set");
  });

  test("result content[0].text carries the full tombstone message from stdout", async () => {
    const { getTool } = setup({
      stdout: tombstoneMsg,
      stderr: "",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result.content[0].text).toContain("re-run once");
    expect(result.content[0].text).toContain("addressbook");
  });

  test("falls back to stderr when stdout is empty on code 3", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "tombstone set on Track 1 - Demo (notifier pid 12345). It stops within one poll; re-run once `addressbook` shows it offline.",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    // Result must still be a success containing some tombstone text (from stderr fallback)
    expect(result.content[0].text).toContain("tombstone set");
  });
});

// ---------------------------------------------------------------------------
// AC2 — unregister exit 1 (any non-0/3) still throws
// ---------------------------------------------------------------------------

describe("AC2 — sandesh_unregister: exit 1 still throws with verb + code + stderr", () => {
  test("execute rejects when pi.exec returns code 1", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "error: address not registered",
      code: 1,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    await expect(
      callExecute(tool, {
        address: "Ghost - Demo",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh unregister failed \(exit 1\)/);
  });

  test("thrown error message contains the exit code and stderr for code 1", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "error: address not registered",
      code: 1,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    let thrown: unknown;
    try {
      await callExecute(tool, {
        address: "Ghost - Demo",
        project_id: "Demo",
      });
    } catch (e) {
      thrown = e;
    }

    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain("unregister");
    expect((thrown as Error).message).toContain("exit 1");
    expect((thrown as Error).message).toContain("address not registered");
  });

  test("execute rejects when pi.exec returns code 2 (not 0 or 3)", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "unexpected error",
      code: 2,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    await expect(
      callExecute(tool, {
        address: "Track 1 - Demo",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh unregister failed \(exit 2\)/);
  });

  test("execute rejects when pi.exec returns code 5 (not 0 or 3)", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "dedup: watcher already running",
      code: 5,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    await expect(
      callExecute(tool, {
        address: "Track 1 - Demo",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh unregister failed \(exit 5\)/);
  });
});

// ---------------------------------------------------------------------------
// AC3 — exit-3 success path is scoped to unregister; send code 3 still throws
// ---------------------------------------------------------------------------

describe("AC3 — exit-3 special-case is scoped to unregister only (send code 3 still throws)", () => {
  test("sandesh_send: pi.exec returning code 3 still throws (not a success result)", async () => {
    const { getTool } = setup({
      stdout: "some output",
      stderr: "unexpected tombstone from send",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_send");

    await expect(
      callExecute(tool, {
        from: "Track 1 - Demo",
        to: ["Mainline - Demo"],
        subject: "ping",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh send failed \(exit 3\)/);
  });

  test("sandesh_send: thrown error for code 3 contains verb + exit code + stderr", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "unexpected tombstone from send",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_send");

    let thrown: unknown;
    try {
      await callExecute(tool, {
        from: "Track 1 - Demo",
        to: ["Mainline - Demo"],
        subject: "ping",
        project_id: "Demo",
      });
    } catch (e) {
      thrown = e;
    }

    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain("send");
    expect((thrown as Error).message).toContain("exit 3");
    expect((thrown as Error).message).toContain("unexpected tombstone from send");
  });

  test("sandesh_register: pi.exec returning code 3 still throws", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "error from register",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_register");

    await expect(
      callExecute(tool, {
        address: "Track 1 - Demo",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh register failed \(exit 3\)/);
  });
});

// ---------------------------------------------------------------------------
// AC4 — unregister exit 0 returns normal success result (unchanged)
// ---------------------------------------------------------------------------

describe("AC4 — sandesh_unregister: exit 0 returns normal success result", () => {
  test("execute resolves with success result when pi.exec returns code 0", async () => {
    const { getTool } = setup({
      stdout: "address unregistered",
      stderr: "",
      code: 0,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result).toBeDefined();
    expect(result.content[0].type).toBe("text");
    expect(result.content[0].text).toContain("address unregistered");
  });

  test("success result details is undefined for exit 0", async () => {
    const { getTool } = setup({
      stdout: "address unregistered",
      stderr: "",
      code: 0,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result.details).toBeUndefined();
  });

  test("pi.exec is called with correct unregister argv on exit 0", async () => {
    const { getTool, execMock } = setup({
      stdout: "unregistered",
      stderr: "",
      code: 0,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("unregister");
    expect(args).toContain("--address");
    expect(args).toContain("Track 1 - Demo");
  });
});
