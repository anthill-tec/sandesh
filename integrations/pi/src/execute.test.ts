/**
 * CR-SAN-013 C1 — RED: execute() CLI argv + AgentToolResult mapping
 *
 * Asserts AC4 + AC5 (+ AC6 semantics that are not duplicated in index.test.ts):
 *   AC4: each tool's execute() builds the exact `sandesh` CLI argv per the
 *        mapping table (mocked pi.exec, inspect call args).
 *   AC5: zero pi.exec code → AgentToolResult with stdout in content[0].text;
 *        non-zero code → error result surfacing stderr.
 *   AC6: sandesh_send description mentions To-wakes/Cc-silent;
 *        sandesh_reply description conveys parent_id = original message id.
 *
 * These tests FAIL now because index.ts uses stubExecute (never calls pi.exec,
 * returns empty text). That is the valid RED — the stub doesn't build argv.
 *
 * Mapping table (spec source of truth):
 *   sandesh_send    send --from F --to a,b [--cc c,d] --subject S [--kind K] [--body B]
 *   sandesh_reply   reply --to-msg N [--from F] [--subject S] [--body B]
 *   sandesh_inbox   inbox --to R [--all]          (unread_only:false → --all)
 *   sandesh_fetch   fetch --to R [--peek]          (mark:false → --peek)
 *   sandesh_thread  thread --id N                  (msg_id → --id)
 *   sandesh_register  register --address A [--kind K] [--name N]
 *   sandesh_unregister unregister --address A [--as R]
 *   sandesh_addressbook addressbook
 *   sandesh_setup   setup
 *
 * All verbs are prefixed with ["--project", P] when project_id is supplied,
 * or when $SANDESH_PROJECT is set and project_id is omitted.
 */

import { test, expect, describe, mock, beforeEach, afterEach } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

/** Build a fake ExtensionAPI that captures registerTool calls and lets us
 *  script pi.exec return values per test. */
function makeFakePi(execResult: ExecResult = { stdout: "ok-output", stderr: "", code: 0, killed: false }) {
  const capturedTools = new Map<string, CapturedTool>();

  const execMock = mock(async (_cmd: string, _args: string[], _opts?: unknown): Promise<ExecResult> => execResult);

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
// AC4 — exact CLI argv
// ---------------------------------------------------------------------------

describe("AC4 — sandesh_send argv", () => {
  test("builds full argv with to/cc comma-joined", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X", "Track 2 - X"],
      cc: ["A - X"],
      subject: "hi",
      body: "b",
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual([
      "--project", "X",
      "send",
      "--from", "Track 1 - X",
      "--to", "Mainline - X,Track 2 - X",
      "--cc", "A - X",
      "--subject", "hi",
      "--body", "b",
    ]);
  });

  test("omits --cc when cc not provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "ping",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--cc");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - X");
  });

  test("omits --body when body not provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "subject-only",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--body");
  });

  test("includes --kind when provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "directive",
      kind: "directive",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--kind");
    expect(args).toContain("directive");
  });

  test("does NOT include --to-msg, --peek, --all, or --resolves", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "x",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--to-msg");
    expect(args).not.toContain("--peek");
    expect(args).not.toContain("--all");
    expect(args).not.toContain("--resolves");
  });
});

describe("AC4 — sandesh_reply argv", () => {
  test("maps parent_id → --to-msg", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_reply");

    await callExecute(tool, {
      parent_id: 42,
      from: "Mainline - X",
      subject: "re",
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual([
      "--project", "X",
      "reply",
      "--to-msg", "42",
      "--from", "Mainline - X",
      "--subject", "re",
    ]);
  });

  test("does NOT include --resolves or --all flags", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_reply");

    await callExecute(tool, {
      parent_id: 42,
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--resolves");
    expect(args).not.toContain("--all");
  });

  test("omits optional flags when not provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_reply");

    await callExecute(tool, {
      parent_id: 7,
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--to-msg");
    expect(args).toContain("7");
    expect(args).not.toContain("--from");
    expect(args).not.toContain("--subject");
    expect(args).not.toContain("--body");
  });

  test("includes --body when provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_reply");

    await callExecute(tool, {
      parent_id: 3,
      body: "reply body",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--body");
    expect(args).toContain("reply body");
  });
});

describe("AC4 — sandesh_inbox argv", () => {
  test("unread_only:false → includes --all", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - X",
      unread_only: false,
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("inbox");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - X");
    expect(args).toContain("--all");
  });

  test("unread_only:true → does NOT include --all", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - X",
      unread_only: true,
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--all");
  });

  test("unread_only omitted (defaults to unread) → does NOT include --all", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--all");
  });

  test("uses --to (not --from or --address) for recipient", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Track 1 - X",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--to");
    expect(args).toContain("Track 1 - X");
    expect(args).not.toContain("--from");
    expect(args).not.toContain("--address");
  });
});

describe("AC4 — sandesh_fetch argv", () => {
  test("mark:false → includes --peek", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, {
      recipient: "Mainline - X",
      mark: false,
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("fetch");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - X");
    expect(args).toContain("--peek");
  });

  test("mark:true → does NOT include --peek", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, {
      recipient: "Mainline - X",
      mark: true,
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--peek");
  });

  test("mark omitted (defaults to mark=true) → does NOT include --peek", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--peek");
  });
});

describe("AC4 — sandesh_thread argv", () => {
  test("maps msg_id → --id", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_thread");

    await callExecute(tool, {
      msg_id: 7,
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual([
      "--project", "X",
      "thread",
      "--id", "7",
    ]);
  });

  test("does NOT use --msg-id or --message-id", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_thread");

    await callExecute(tool, {
      msg_id: 3,
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--msg-id");
    expect(args).not.toContain("--message-id");
  });
});

describe("AC4 — sandesh_register argv", () => {
  test("builds register argv with address and kind", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_register");

    await callExecute(tool, {
      address: "Track 4 - X",
      kind: "track",
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual([
      "--project", "X",
      "register",
      "--address", "Track 4 - X",
      "--kind", "track",
    ]);
  });

  test("omits --kind when not provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_register");

    await callExecute(tool, {
      address: "Mainline - X",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--kind");
  });

  test("includes --name when provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_register");

    await callExecute(tool, {
      address: "Mainline - X",
      name: "Main coordinator",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--name");
    expect(args).toContain("Main coordinator");
  });
});

describe("AC4 — sandesh_unregister argv", () => {
  test("builds unregister argv with address", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unregister");

    await callExecute(tool, {
      address: "Track 1 - X",
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("unregister");
    expect(args).toContain("--address");
    expect(args).toContain("Track 1 - X");
  });

  test("maps requester → --as when provided", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unregister");

    await callExecute(tool, {
      address: "Track 1 - X",
      requester: "Mainline - X",
      project_id: "X",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--as");
    expect(args).toContain("Mainline - X");
  });
});

describe("AC4 — sandesh_addressbook argv", () => {
  test("builds addressbook argv with project", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_addressbook");

    await callExecute(tool, {
      project_id: "X",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual(["--project", "X", "addressbook"]);
  });
});

describe("AC4 — sandesh_setup argv", () => {
  test("builds setup argv with project", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_setup");

    await callExecute(tool, {
      project_id: "MyProj",
    });

    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual(["--project", "MyProj", "setup"]);
  });
});

describe("AC4 — project_id env fallback", () => {
  let origEnv: string | undefined;

  beforeEach(() => {
    origEnv = process.env.SANDESH_PROJECT;
    process.env.SANDESH_PROJECT = "EnvProject";
  });

  afterEach(() => {
    if (origEnv === undefined) {
      delete process.env.SANDESH_PROJECT;
    } else {
      process.env.SANDESH_PROJECT = origEnv;
    }
  });

  test("sandesh_setup uses $SANDESH_PROJECT when project_id omitted", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_setup");

    await callExecute(tool, {});

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--project");
    expect(args).toContain("EnvProject");
  });

  test("sandesh_send uses $SANDESH_PROJECT when project_id omitted", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - EnvProject",
      to: ["Mainline - EnvProject"],
      subject: "env test",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--project");
    expect(args).toContain("EnvProject");
  });

  test("sandesh_inbox uses $SANDESH_PROJECT when project_id omitted", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - EnvProject",
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--project");
    expect(args).toContain("EnvProject");
  });

  test("explicit project_id takes precedence over $SANDESH_PROJECT", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_setup");

    await callExecute(tool, { project_id: "ExplicitProject" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    const projectIdx = args.indexOf("--project");
    expect(projectIdx).toBeGreaterThanOrEqual(0);
    expect(args[projectIdx + 1]).toBe("ExplicitProject");
  });
});

// ---------------------------------------------------------------------------
// AC5 — result mapping
// ---------------------------------------------------------------------------

describe("AC5 — result mapping: zero code → success AgentToolResult", () => {
  test("sandesh_send: zero code → content[0].text contains stdout", async () => {
    const { getTool } = setup({ stdout: "Message sent: #42", stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_send");

    const result = await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "hi",
      project_id: "X",
    });

    expect(result.content).toBeDefined();
    expect(result.content.length).toBeGreaterThan(0);
    expect(result.content[0].type).toBe("text");
    expect((result.content[0] as { type: "text"; text: string }).text).toContain("Message sent: #42");
  });

  test("sandesh_fetch: zero code → success result with stdout", async () => {
    const { getTool } = setup({ stdout: "--- msg #7 ---\nSubject: hello", stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_fetch");

    const result = await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    const text = (result.content[0] as { type: "text"; text: string }).text;
    expect(text).toContain("msg #7");
  });

  test("sandesh_inbox: zero code → success result with stdout", async () => {
    const { getTool } = setup({ stdout: "2 unread messages", stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_inbox");

    const result = await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    const text = (result.content[0] as { type: "text"; text: string }).text;
    expect(text).toContain("2 unread");
  });
});

describe("AC5 — result mapping: non-zero code → error result surfacing stderr", () => {
  test("sandesh_send: non-zero code → result surfaces stderr text", async () => {
    const { getTool } = setup({ stdout: "", stderr: "boom: address not registered", code: 1, killed: false });
    const tool = getTool("sandesh_send");

    const result = await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "x",
      project_id: "X",
    });

    expect(result.content.length).toBeGreaterThan(0);
    const text = (result.content[0] as { type: "text"; text: string }).text;
    expect(text).toContain("boom");
  });

  test("sandesh_reply: non-zero code → result surfaces stderr text", async () => {
    const { getTool } = setup({ stdout: "", stderr: "error: parent message not found", code: 1, killed: false });
    const tool = getTool("sandesh_reply");

    const result = await callExecute(tool, {
      parent_id: 999,
      project_id: "X",
    });

    const text = (result.content[0] as { type: "text"; text: string }).text;
    expect(text).toContain("error");
    expect(text).toContain("parent message not found");
  });

  test("sandesh_fetch: non-zero code → result surfaces stderr text", async () => {
    const { getTool } = setup({ stdout: "", stderr: "no such recipient", code: 2, killed: false });
    const tool = getTool("sandesh_fetch");

    const result = await callExecute(tool, {
      recipient: "Ghost - X",
      project_id: "X",
    });

    const text = (result.content[0] as { type: "text"; text: string }).text;
    expect(text).toContain("no such recipient");
  });

  test("non-zero code result is distinct from success (stub returns empty → no stderr)", async () => {
    // The stub returns empty text — the real impl must return the error content.
    // This test distinguishes: stub returns {content:[{type:"text",text:""}]}
    // whereas spec requires the error surface. We assert text is non-empty on error.
    const { getTool } = setup({ stdout: "", stderr: "fatal error", code: 1, killed: false });
    const tool = getTool("sandesh_setup");

    const result = await callExecute(tool, { project_id: "X" });

    const text = (result.content[0] as { type: "text"; text: string }).text;
    // Stub returns "" — real impl must include the error text.
    expect(text.length).toBeGreaterThan(0);
    expect(text).toContain("fatal error");
  });
});

describe("AC5 — pi.exec is actually called (stub never calls it)", () => {
  test("sandesh_send: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_send");

    await callExecute(tool, {
      from: "Track 1 - X",
      to: ["Mainline - X"],
      subject: "x",
      project_id: "X",
    });

    // Stub never calls pi.exec — real impl must call it exactly once per execute
    expect(execMock.mock.calls.length).toBe(1);
  });

  test("sandesh_reply: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_reply");

    await callExecute(tool, {
      parent_id: 1,
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
  });

  test("sandesh_inbox: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
  });

  test("sandesh_fetch: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, {
      recipient: "Mainline - X",
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
  });

  test("sandesh_thread: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_thread");

    await callExecute(tool, {
      msg_id: 1,
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
  });

  test("sandesh_register: execMock is called exactly once", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_register");

    await callExecute(tool, {
      address: "Track 1 - X",
      project_id: "X",
    });

    expect(execMock.mock.calls.length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// AC6 — description semantics (not duplicated from C0 index.test.ts)
// ---------------------------------------------------------------------------

describe("AC6 — tool description semantics", () => {
  test("sandesh_send description mentions To-wakes or 'wake'", () => {
    const { capturedTools } = setup();
    const send = capturedTools.get("sandesh_send");
    expect(send).toBeDefined();
    // The description must convey that To-role wakes / Cc is silent
    const desc = send!.description.toLowerCase();
    expect(desc).toMatch(/wake|to.*wake|notify/);
  });

  test("sandesh_send description mentions Cc-silent semantics", () => {
    const { capturedTools } = setup();
    const send = capturedTools.get("sandesh_send");
    expect(send).toBeDefined();
    const desc = send!.description.toLowerCase();
    expect(desc).toMatch(/cc|silent|never wake/);
  });

  test("sandesh_reply description conveys parent_id is the original message id", () => {
    const { capturedTools } = setup();
    const reply = capturedTools.get("sandesh_reply");
    expect(reply).toBeDefined();
    const desc = reply!.description.toLowerCase();
    // Must express that parent_id references the original message
    expect(desc).toMatch(/parent_id|original|id of the/);
  });
});
