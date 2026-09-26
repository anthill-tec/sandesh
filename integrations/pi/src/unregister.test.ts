/**
 * CR-SAN-019 C1 — tombstone-aware unregister (§S1, AC1–AC4), re-based on
 * CR-SAN-048 §S2 (errors-as-results over the `--format toon` envelope).
 *
 * AC1 — unregister with r.code === 3 whose stdout is an `ok:true` envelope
 *        carrying `result: tombstoned` RESOLVES with that envelope text
 *        byte-for-byte (a ≥0.4.0 CLI always emits an envelope). An EMPTY
 *        stdout at exit 3 is undecodable and therefore THROWS
 *        `sandesh unregister failed (exit 3)` (CR-SAN-048 AC3).
 * AC2 — unregister with r.code === 1 (any non-0/3) and no envelope on stdout
 *        still throws an Error whose message carries verb + exit code + stderr.
 * AC3 — a non-envelope exit 3 on another verb (send/register) still throws:
 *        the resolve path is keyed on a decodable envelope, never on the verb.
 * AC4 — unregister with r.code === 0 returns the normal success result.
 *
 * History: at CR-SAN-019 RED, runSandesh threw on ALL non-zero exits and the
 * fixtures were plain text; CR-SAN-048 replaced the unregister/exit-3
 * special case with the generic "non-zero + decodable envelope → result"
 * rule, so the AC1 fixtures are now real TOON envelopes.
 *
 * NOTE: AC5 (real-binary smoke test) is §S2 — a separate Cycle 2 dispatch.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import { encode } from "@toon-format/toon";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

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
// AC1 — unregister exit 3 → success result carrying the tombstoned envelope
// ---------------------------------------------------------------------------

describe("AC1 — sandesh_unregister: exit 3 returns success result (tombstone)", () => {
  // CR-SAN-048 §S2: the CLI's `--format toon` envelope for the tombstoned
  // disposition (PRD-axi-toon.md §4.1) — a real, decodable TOON fixture.
  const tombstoneMsg = encode({
    axi: {
      verb: "unregister",
      ok: true,
      address: "Track 1 - Demo",
      project: "Demo",
      result: "tombstoned",
      context: { project: "Demo", address: "Track 1 - Demo" },
      help: ["sandesh addressbook"],
      warnings: [],
    },
  });

  test("execute resolves (does not reject) when pi.exec returns code 3", async () => {
    const { getTool } = setup({
      stdout: tombstoneMsg,
      stderr: "",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    // Must resolve, not reject — the envelope on stdout is the result.
    const result = await callExecute(tool, {
      address: "Track 1 - Demo",
      project_id: "Demo",
    });

    expect(result).toBeDefined();
  });

  test("result content[0].text is the tombstoned envelope when code is 3", async () => {
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
    const env = decodeEnvelope((result.content[0] as { type: "text"; text: string }).text);
    expect(env.verb).toBe("unregister");
    expect(env.ok).toBe(true);
    expect(env.fields.result).toBe("tombstoned");
  });

  test("result content[0].text carries the full envelope from stdout byte-for-byte", async () => {
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

    expect((result.content[0] as { type: "text"; text: string }).text).toBe(tombstoneMsg);
  });

  test("empty stdout at exit 3 throws sandesh unregister failed (exit 3) — no stderr fallback (CR-SAN-048 AC3)", async () => {
    const { getTool } = setup({
      stdout: "",
      stderr: "tombstone set on Track 1 - Demo (notifier pid 12345). It stops within one poll; re-run once `addressbook` shows it offline.",
      code: 3,
      killed: false,
    });
    const tool = getTool("sandesh_unregister");

    // An undecodable (empty) stdout is the only thing that throws now; a
    // ≥0.4.0 CLI always emits an envelope, so there is no stderr fallback.
    await expect(
      callExecute(tool, {
        address: "Track 1 - Demo",
        project_id: "Demo",
      }),
    ).rejects.toThrow(/sandesh unregister failed \(exit 3\)/);
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
    expect((result.content[0] as { type: "text"; text: string }).text).toContain("address unregistered");
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
