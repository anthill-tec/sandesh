/**
 * CR-SAN-054 C6 — RED: Pi tool params `fields`/`requester`/`with_body`/`full`
 * for the read-mail surfaces (AC2, AC5, AC8-Pi).
 *
 * Mirrors the `execute.test.ts` / `surface_catchup.test.ts` / `axi_passthrough.test.ts`
 * harness: a fake `pi.exec` scripted per test, captured `registerTool` calls, and
 * `tool.execute(...)` invoked directly. Fixtures are built with a thin `envelope()`
 * wrapper around `@toon-format/toon`'s `encode()` so every fixture is a real,
 * decodable TOON envelope (PRD-axi-toon.md §4.1) — spec-exact, not hand-typed strings.
 *
 * Covers (per CR-SAN-054-read-mail-readable.md):
 *   AC2      — `sandesh_search` gains `fields: string[]` → `--fields a,b,c`.
 *   AC5      — `sandesh_thread` gains `requester: string` → `--as <addr>` (one argv element).
 *   AC8 (Pi) — `sandesh_inbox` gains `with_body: boolean` / `full: boolean` →
 *              `--with-body` / `--full`.
 *
 * RED reasons:
 *   - `sandesh_search`'s parameter schema has no `fields` property and its
 *     `execute()` never reads `params.fields`, so no `--fields` flag is ever
 *     emitted — the argv-equality assertion fails.
 *   - `sandesh_thread`'s parameter schema has no `requester` property and its
 *     `execute()` never reads `params.requester`, so `--as` is never emitted.
 *   - `sandesh_inbox`'s parameter schema has no `with_body`/`full` properties
 *     and its `execute()` never reads them, so neither flag is ever emitted.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import { encode } from "@toon-format/toon";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ---------------------------------------------------------------------------
// Test harness (mirrors execute.test.ts / surface_catchup.test.ts)
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

function makeFakePi(execResult: ExecResult) {
  const capturedTools = new Map<string, CapturedTool>();
  const execMock = mock(
    async (_cmd: string, _args: string[], _opts?: unknown): Promise<ExecResult> => execResult,
  );
  const fakePi = {
    registerCommand: mock(() => {}),
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

function setup(execResult: ExecResult = { stdout: "ok-output", stderr: "", code: 0, killed: false }) {
  const harness = makeFakePi(execResult);
  registerExtension(harness.fakePi);
  function getTool(name: string): CapturedTool {
    const t = harness.capturedTools.get(name);
    if (!t) throw new Error(`Tool "${name}" not registered`);
    return t;
  }
  return { ...harness, getTool };
}

/** Read `.content[0].text` off an AgentToolResult (mirrors axi_passthrough.test.ts). */
function text(result: { content: unknown[] }): string {
  return (result.content[0] as { type: "text"; text: string }).text;
}

function props(tool: CapturedTool): Record<string, any> {
  return (tool.parameters as Record<string, unknown>).properties as Record<string, any>;
}

// ---------------------------------------------------------------------------
// Fixture builder — real TOON text via the reference encoder
// ---------------------------------------------------------------------------

interface EnvelopeOpts {
  ok?: boolean;
  address?: string;
}

function envelope(verb: string, fields: Record<string, unknown> = {}, opts: EnvelopeOpts = {}): string {
  const ok = opts.ok ?? true;
  const context: Record<string, unknown> = { project: "Demo" };
  if (opts.address !== undefined) context.address = opts.address;
  const axi: Record<string, unknown> = { verb, ok, ...fields, context, warnings: [] };
  return encode({ axi });
}

// ---------------------------------------------------------------------------
// AC2 — sandesh_search gains `fields: string[]` → `--fields a,b,c`
// ---------------------------------------------------------------------------

describe("AC2 — sandesh_search fields param", () => {
  test("recipient + query + fields:[id,snippet] → exact argv with --fields id,snippet", async () => {
    const fixture = envelope(
      "search",
      { hits: [{ id: 1, snippet: "[gateway] timeout" }], total: 1 },
      { address: "Mainline - Demo" },
    );
    const { getTool, execMock } = setup({ stdout: fixture, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_search");

    await callExecute(tool, {
      recipient: "Mainline - Demo",
      query: "gateway",
      fields: ["id", "snippet"],
    });

    expect(execMock.mock.calls.length).toBe(1);
    const [cmd, args] = execMock.mock.calls[0] as [string, string[]];
    expect(cmd).toBe("sandesh");
    expect(args).toEqual([
      "--format",
      "toon",
      "search",
      "gateway",
      "--to",
      "Mainline - Demo",
      "--fields",
      "id,snippet",
    ]);
  });

  test("fields omitted → argv has no --fields", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_search");

    await callExecute(tool, { recipient: "Mainline - Demo", query: "gateway" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--fields");
  });

  test("parameters schema declares `fields` (array) with a description", () => {
    const { getTool } = setup();
    const p = props(getTool("sandesh_search"));
    expect(p.fields).toBeDefined();
    expect(p.fields.type).toBe("array");
    expect(typeof p.fields.description).toBe("string");
    expect(p.fields.description.length).toBeGreaterThan(0);
  });

  test("result decodes the fake CLI's TOON stdout unchanged, with snippet intact", async () => {
    const fixture = envelope(
      "search",
      { hits: [{ id: 1, snippet: "[gateway] timeout" }], total: 1 },
      { address: "Mainline - Demo" },
    );
    const { getTool } = setup({ stdout: fixture, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_search");

    const result = await callExecute(tool, {
      recipient: "Mainline - Demo",
      query: "gateway",
      fields: ["id", "snippet"],
    });

    expect(text(result)).toBe(fixture);
    const env = decodeEnvelope(text(result));
    expect(env.verb).toBe("search");
    expect(env.ok).toBe(true);
    expect((env.fields.hits as any[])[0].snippet).toBe("[gateway] timeout");
  });
});

// ---------------------------------------------------------------------------
// AC5 — sandesh_thread gains `requester: string` → `--as <addr>` (--as precedent)
// ---------------------------------------------------------------------------

describe("AC5 — sandesh_thread requester param", () => {
  test("msg_id + requester → argv contains --as then 'Mainline - Demo' as one element", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_thread");

    await callExecute(tool, { msg_id: 7, requester: "Mainline - Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    const idx = args.indexOf("--as");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(args[idx + 1]).toBe("Mainline - Demo");
  });

  test("requester omitted → argv has no --as", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_thread");

    await callExecute(tool, { msg_id: 7 });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--as");
  });

  test("parameters schema declares `requester` (string) with a description", () => {
    const { getTool } = setup();
    const p = props(getTool("sandesh_thread"));
    expect(p.requester).toBeDefined();
    expect(p.requester.type).toBe("string");
    expect(typeof p.requester.description).toBe("string");
    expect(p.requester.description.length).toBeGreaterThan(0);
  });

  test("result decodes the fake CLI's TOON stdout unchanged, with body intact", async () => {
    const fixture = envelope("thread", {
      messages: [{ id: 7, from: "Track 1 - Demo", subject: "hi", body: "gateway timeout" }],
    });
    const { getTool } = setup({ stdout: fixture, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_thread");

    const result = await callExecute(tool, { msg_id: 7, requester: "Mainline - Demo" });

    expect(text(result)).toBe(fixture);
    const env = decodeEnvelope(text(result));
    expect(env.verb).toBe("thread");
    expect((env.fields.messages as any[])[0].body).toBe("gateway timeout");
  });
});

// ---------------------------------------------------------------------------
// AC8 (Pi half) — sandesh_inbox gains `with_body` / `full` → `--with-body` / `--full`
// ---------------------------------------------------------------------------

describe("AC8-Pi — sandesh_inbox with_body/full params", () => {
  test("recipient + unread_only:false + with_body:true + full:true → argv contains --all, --with-body, --full", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, {
      recipient: "Mainline - Demo",
      unread_only: false,
      with_body: true,
      full: true,
    });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--all");
    expect(args).toContain("--with-body");
    expect(args).toContain("--full");
  });

  test("with_body omitted → argv has no --with-body", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, { recipient: "Mainline - Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--with-body");
  });

  test("full omitted (with_body:true alone) → argv has --with-body but no --full", async () => {
    const { getTool, execMock } = setup();
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, { recipient: "Mainline - Demo", with_body: true });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--with-body");
    expect(args).not.toContain("--full");
  });

  test("parameters schema declares `with_body` and `full` (boolean) with descriptions", () => {
    const { getTool } = setup();
    const p = props(getTool("sandesh_inbox"));
    expect(p.with_body).toBeDefined();
    expect(p.with_body.type).toBe("boolean");
    expect(typeof p.with_body.description).toBe("string");
    expect(p.with_body.description.length).toBeGreaterThan(0);
    expect(p.full).toBeDefined();
    expect(p.full.type).toBe("boolean");
    expect(typeof p.full.description).toBe("string");
    expect(p.full.description.length).toBeGreaterThan(0);
  });

  test("result decodes the fake CLI's TOON stdout unchanged, with bodies intact", async () => {
    const fixture = envelope(
      "inbox",
      {
        messages: [{ id: 1, from: "Track 1 - Demo", subject: "hi", unread: true }],
        bodies: { "1": "gateway timeout" },
        unread: "1 of 1",
      },
      { address: "Mainline - Demo" },
    );
    const { getTool } = setup({ stdout: fixture, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_inbox");

    const result = await callExecute(tool, {
      recipient: "Mainline - Demo",
      unread_only: false,
      with_body: true,
      full: true,
    });

    expect(text(result)).toBe(fixture);
    const env = decodeEnvelope(text(result));
    expect(env.verb).toBe("inbox");
    expect((env.fields.bodies as Record<string, string>)["1"]).toBe("gateway timeout");
  });
});
