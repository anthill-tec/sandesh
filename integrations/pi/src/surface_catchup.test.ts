/**
 * CR-SAN-032 C1 — RED: argv matrix for new tools + filter params (AC2)
 *
 * Asserts:
 *   AC2a: sandesh_archive argv — with/without dry_run/force
 *   AC2b: sandesh_unarchive argv — with/without dry_run; NO force param in its schema
 *   AC2c: sandesh_search argv — no optionals (no --limit/--offset/--from-project);
 *         with all params; NO global --project prefix (search is parentless)
 *   AC2d: sandesh_inbox filter params — each of the six filters alone + combined
 *   AC2e: sandesh_fetch filter params — each of the six filters alone + combined
 *
 * Pattern: reuses the makeFakePi / callExecute / setup harness from execute.test.ts.
 * These tests FAIL until GREEN implements the three new tools + filter params.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Test harness (mirrors execute.test.ts)
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

function makeFakePi(
  execResult: ExecResult = { stdout: "ok", stderr: "", code: 0, killed: false },
) {
  const capturedTools = new Map<string, CapturedTool>();
  const execMock = mock(
    async (_cmd: string, _args: string[], _opts?: unknown): Promise<ExecResult> =>
      execResult,
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

async function callExecute(tool: CapturedTool, params: Record<string, unknown>) {
  return tool.execute("test-call-id", params, undefined, undefined, {} as any);
}

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
// AC2a — sandesh_archive argv
// ---------------------------------------------------------------------------

describe("AC2a — sandesh_archive argv", () => {
  test("minimal: project_id + by → archive --project <id> --by <addr>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual(["archive", "--project", "Demo", "--by", "Mainline - Demo"]);
  });

  test("dry_run:true → emits --dry-run flag", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", dry_run: true });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--dry-run");
  });

  test("dry_run:false → does NOT emit --dry-run", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", dry_run: false });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--dry-run");
  });

  test("dry_run omitted → does NOT emit --dry-run", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--dry-run");
  });

  test("force:true → emits --force flag", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", force: true });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--force");
  });

  test("force:false → does NOT emit --force", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", force: false });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--force");
  });

  test("force omitted → does NOT emit --force", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--force");
  });

  test("all flags: dry_run+force → exact full argv", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, {
      project_id: "Demo",
      by: "Mainline - Demo",
      dry_run: true,
      force: true,
    });
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("archive");
    expect(args).toContain("--project");
    expect(args).toContain("Demo");
    expect(args).toContain("--by");
    expect(args).toContain("Mainline - Demo");
    expect(args).toContain("--dry-run");
    expect(args).toContain("--force");
  });

  test("archive argv does NOT include a duplicate --project prefix before the verb", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_archive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    // First element must be the verb "archive", not "--project"
    expect(args[0]).toBe("archive");
    // "--project" appears exactly once (as a verb-level flag, not global prefix)
    const projectCount = args.filter((a) => a === "--project").length;
    expect(projectCount).toBe(1);
  });

  test("sandesh_archive schema has project_id, by, dry_run, force properties", () => {
    const { capturedTools } = setup();
    const tool = capturedTools.get("sandesh_archive");
    expect(tool).toBeDefined();
    const props = (tool!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("project_id");
    expect(props).toHaveProperty("by");
    expect(props).toHaveProperty("dry_run");
    expect(props).toHaveProperty("force");
  });

  test("non-zero exit → execute throws with archive verb in message", async () => {
    const { getTool } = setup({ stdout: "", stderr: "not a Mainline address", code: 1, killed: false });
    const tool = getTool("sandesh_archive");
    await expect(
      callExecute(tool, { project_id: "Demo", by: "Track 1 - Demo" }),
    ).rejects.toThrow(/sandesh archive failed \(exit 1\)/);
  });
});

// ---------------------------------------------------------------------------
// AC2b — sandesh_unarchive argv
// ---------------------------------------------------------------------------

describe("AC2b — sandesh_unarchive argv", () => {
  test("minimal: project_id + by → unarchive --project <id> --by <addr>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual(["unarchive", "--project", "Demo", "--by", "Mainline - Demo"]);
  });

  test("dry_run:true → emits --dry-run flag", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", dry_run: true });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--dry-run");
  });

  test("dry_run:false → does NOT emit --dry-run", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", dry_run: false });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--dry-run");
  });

  test("dry_run omitted → does NOT emit --dry-run", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--dry-run");
  });

  test("unarchive argv does NOT include --force (no force param)", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo", dry_run: true });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--force");
  });

  test("sandesh_unarchive schema has NO force property", () => {
    const { capturedTools } = setup();
    const tool = capturedTools.get("sandesh_unarchive");
    expect(tool).toBeDefined();
    const props = (tool!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("project_id");
    expect(props).toHaveProperty("by");
    expect(props).toHaveProperty("dry_run");
    // force must NOT be in the schema
    expect(props).not.toHaveProperty("force");
  });

  test("unarchive argv does NOT include a duplicate --project prefix before the verb", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_unarchive");
    await callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args[0]).toBe("unarchive");
    const projectCount = args.filter((a) => a === "--project").length;
    expect(projectCount).toBe(1);
  });

  test("non-zero exit → execute throws with unarchive verb in message", async () => {
    const { getTool } = setup({ stdout: "", stderr: "project is not archived", code: 1, killed: false });
    const tool = getTool("sandesh_unarchive");
    await expect(
      callExecute(tool, { project_id: "Demo", by: "Mainline - Demo" }),
    ).rejects.toThrow(/sandesh unarchive failed \(exit 1\)/);
  });
});

// ---------------------------------------------------------------------------
// AC2c — sandesh_search argv
// ---------------------------------------------------------------------------

describe("AC2c — sandesh_search argv", () => {
  test("minimal: recipient + query → search <query> --to <recipient>, no --limit/--offset/--from-project", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "status update" });
    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual(["search", "status update", "--to", "Mainline - Demo"]);
  });

  test("minimal search omits --limit", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--limit");
  });

  test("minimal search omits --offset", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--offset");
  });

  test("minimal search omits --from-project", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--from-project");
  });

  test("search has NO global --project prefix (parentless verb)", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping" });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args[0]).toBe("search");
    expect(args).not.toContain("--project");
  });

  test("limit provided → emits --limit N", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping", limit: 5 });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--limit");
    const idx = args.indexOf("--limit");
    expect(args[idx + 1]).toBe("5");
  });

  test("offset provided → emits --offset N", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, { recipient: "Mainline - Demo", query: "ping", offset: 20 });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--offset");
    const idx = args.indexOf("--offset");
    expect(args[idx + 1]).toBe("20");
  });

  test("sender_project provided → emits --from-project P", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      query: "ping",
      sender_project: "OtherProj",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--from-project");
    const idx = args.indexOf("--from-project");
    expect(args[idx + 1]).toBe("OtherProj");
  });

  test("all params: exact full argv", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      query: "deadline AND report",
      limit: 10,
      offset: 5,
      sender_project: "Alpha",
    });
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("search");
    expect(args).toContain("deadline AND report");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - Demo");
    expect(args).toContain("--limit");
    expect(args).toContain("10");
    expect(args).toContain("--offset");
    expect(args).toContain("5");
    expect(args).toContain("--from-project");
    expect(args).toContain("Alpha");
    // Still no --project prefix
    expect(args).not.toContain("--project");
  });

  test("sandesh_search schema: recipient + query required; limit, offset, sender_project optional", () => {
    const { capturedTools } = setup();
    const tool = capturedTools.get("sandesh_search");
    expect(tool).toBeDefined();
    const props = (tool!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("recipient");
    expect(props).toHaveProperty("query");
    expect(props).toHaveProperty("limit");
    expect(props).toHaveProperty("offset");
    expect(props).toHaveProperty("sender_project");
  });

  test("non-zero exit → execute throws with search verb in message", async () => {
    const { getTool } = setup({ stdout: "", stderr: "address not registered", code: 1, killed: false });
    const tool = getTool("sandesh_search");
    await expect(
      callExecute(tool, { recipient: "Ghost - Demo", query: "ping" }),
    ).rejects.toThrow(/sandesh search failed \(exit 1\)/);
  });
});

// ---------------------------------------------------------------------------
// AC2d — sandesh_inbox filter params
// ---------------------------------------------------------------------------

describe("AC2d — sandesh_inbox filter params", () => {
  test("sender filter: sender → --from <addr>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender: "Track 1 - Demo",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--from");
    const idx = args.indexOf("--from");
    expect(args[idx + 1]).toBe("Track 1 - Demo");
  });

  test("sender_project filter: sender_project → --from-project P", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender_project: "OtherProj",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--from-project");
    const idx = args.indexOf("--from-project");
    expect(args[idx + 1]).toBe("OtherProj");
  });

  test("kind filter: kind → --kind <k>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      kind: "request",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--kind");
    const idx = args.indexOf("--kind");
    expect(args[idx + 1]).toBe("request");
  });

  test("since filter: since → --since <ts>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      since: "2026-06-01T00:00:00Z",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--since");
    const idx = args.indexOf("--since");
    expect(args[idx + 1]).toBe("2026-06-01T00:00:00Z");
  });

  test("until filter: until → --until <ts>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      until: "2026-06-13T23:59:59Z",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--until");
    const idx = args.indexOf("--until");
    expect(args[idx + 1]).toBe("2026-06-13T23:59:59Z");
  });

  test("subject_like filter: subject_like → --subject <pattern>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      subject_like: "status",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--subject");
    const idx = args.indexOf("--subject");
    expect(args[idx + 1]).toBe("status");
  });

  test("omitting all filters → none of the filter flags appear", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--from");
    expect(args).not.toContain("--from-project");
    expect(args).not.toContain("--kind");
    expect(args).not.toContain("--since");
    expect(args).not.toContain("--until");
    expect(args).not.toContain("--subject");
  });

  test("all six filters combined → exact flag set", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender: "Track 1 - Demo",
      sender_project: "Demo",
      kind: "directive",
      since: "2026-01-01T00:00:00Z",
      until: "2026-12-31T23:59:59Z",
      subject_like: "deploy",
    });
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("inbox");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - Demo");
    expect(args).toContain("--from");
    expect(args).toContain("Track 1 - Demo");
    expect(args).toContain("--from-project");
    expect(args).toContain("Demo");
    expect(args).toContain("--kind");
    expect(args).toContain("directive");
    expect(args).toContain("--since");
    expect(args).toContain("2026-01-01T00:00:00Z");
    expect(args).toContain("--until");
    expect(args).toContain("2026-12-31T23:59:59Z");
    expect(args).toContain("--subject");
    expect(args).toContain("deploy");
  });

  test("sandesh_inbox schema gains sender, sender_project, kind, since, until, subject_like", () => {
    const { capturedTools } = setup();
    const tool = capturedTools.get("sandesh_inbox");
    expect(tool).toBeDefined();
    const props = (tool!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("sender");
    expect(props).toHaveProperty("sender_project");
    expect(props).toHaveProperty("kind");
    expect(props).toHaveProperty("since");
    expect(props).toHaveProperty("until");
    expect(props).toHaveProperty("subject_like");
  });
});

// ---------------------------------------------------------------------------
// AC2e — sandesh_fetch filter params
// ---------------------------------------------------------------------------

describe("AC2e — sandesh_fetch filter params", () => {
  test("sender filter: sender → --from <addr>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender: "Track 2 - Demo",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--from");
    const idx = args.indexOf("--from");
    expect(args[idx + 1]).toBe("Track 2 - Demo");
  });

  test("sender_project filter: sender_project → --from-project P", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender_project: "Beta",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--from-project");
    const idx = args.indexOf("--from-project");
    expect(args[idx + 1]).toBe("Beta");
  });

  test("kind filter: kind → --kind <k>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      kind: "fyi",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--kind");
    const idx = args.indexOf("--kind");
    expect(args[idx + 1]).toBe("fyi");
  });

  test("since filter: since → --since <ts>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      since: "2026-06-10T00:00:00Z",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--since");
    const idx = args.indexOf("--since");
    expect(args[idx + 1]).toBe("2026-06-10T00:00:00Z");
  });

  test("until filter: until → --until <ts>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      until: "2026-06-13T12:00:00Z",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--until");
    const idx = args.indexOf("--until");
    expect(args[idx + 1]).toBe("2026-06-13T12:00:00Z");
  });

  test("subject_like filter: subject_like → --subject <pattern>", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      subject_like: "urgent",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--subject");
    const idx = args.indexOf("--subject");
    expect(args[idx + 1]).toBe("urgent");
  });

  test("omitting all filters → none of the filter flags appear", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--from");
    expect(args).not.toContain("--from-project");
    expect(args).not.toContain("--kind");
    expect(args).not.toContain("--since");
    expect(args).not.toContain("--until");
    expect(args).not.toContain("--subject");
  });

  test("all six filters combined → exact flag set", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      sender: "Track 3 - Demo",
      sender_project: "Demo",
      kind: "request",
      since: "2026-05-01T00:00:00Z",
      until: "2026-06-01T00:00:00Z",
      subject_like: "handoff",
    });
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toContain("fetch");
    expect(args).toContain("--to");
    expect(args).toContain("Mainline - Demo");
    expect(args).toContain("--from");
    expect(args).toContain("Track 3 - Demo");
    expect(args).toContain("--from-project");
    expect(args).toContain("Demo");
    expect(args).toContain("--kind");
    expect(args).toContain("request");
    expect(args).toContain("--since");
    expect(args).toContain("2026-05-01T00:00:00Z");
    expect(args).toContain("--until");
    expect(args).toContain("2026-06-01T00:00:00Z");
    expect(args).toContain("--subject");
    expect(args).toContain("handoff");
  });

  test("sandesh_fetch schema gains sender, sender_project, kind, since, until, subject_like", () => {
    const { capturedTools } = setup();
    const tool = capturedTools.get("sandesh_fetch");
    expect(tool).toBeDefined();
    const props = (tool!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("sender");
    expect(props).toHaveProperty("sender_project");
    expect(props).toHaveProperty("kind");
    expect(props).toHaveProperty("since");
    expect(props).toHaveProperty("until");
    expect(props).toHaveProperty("subject_like");
  });

  test("filter flags interoperate with existing mark flag", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_fetch");
    await callExecute(tool, {
      recipient: "Mainline - Demo",
      project_id: "Demo",
      mark: false,
      sender: "Track 1 - Demo",
    });
    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--peek");
    expect(args).toContain("--from");
    expect(args).toContain("Track 1 - Demo");
  });
});
