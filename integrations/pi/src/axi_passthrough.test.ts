/**
 * CR-SAN-048 §S2 — RED: AXI pass-through tools (`--format toon`, AXI knobs,
 * errors-as-results) — AC2 + AC3.
 *
 * Mirrors the `execute.test.ts` harness: a fake `pi.exec` scripted per test,
 * captured `registerTool` calls, and `tool.execute(...)` invoked directly.
 *
 * Fixtures are built with `envelope(verb, fields, opts)`, a thin wrapper
 * around `@toon-format/toon`'s `encode({ axi: { verb, ok, ...fields,
 * context, warnings } })` so every fixture is a real, decodable TOON
 * envelope (PRD-axi-toon.md §4.1) — spec-exact, not hand-typed strings.
 *
 * RED reasons (see final report for the exact split):
 *   1. `runSandesh` does not add `--format toon` to any invocation today —
 *      every "pass-through" test's argv assertion fails.
 *   2. `fields`/`full`/`limit` are not declared on any tool's parameter
 *      schema and are not read by any `execute()` — both the schema
 *      assertions and the flag-emission assertions fail.
 *   3. `runSandesh` throws on exit 1/2 whenever stdout decodes to an
 *      envelope (only `unregister`+exit 3 is special-cased today) — the
 *      "errors as results" tests that expect a RESOLVED result fail.
 *   4/5. No re-rendering already holds (stdout is forwarded verbatim);
 *      description length already holds for all 12 tools; the `fields`/
 *      `full`/`limit` keyword-in-description checks fail for
 *      addressbook/inbox/fetch (thread's description already happens to
 *      contain "full" — a coincidental pass, not a real signal).
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition, ExecResult } from "@earendil-works/pi-coding-agent";
import { encode } from "@toon-format/toon";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ---------------------------------------------------------------------------
// Test harness (mirrors execute.test.ts)
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

function makeFakePi(execResult: ExecResult) {
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

/** Read `.content[0].text` off an AgentToolResult with the same cast the
 *  existing suite uses. */
function text(result: { content: unknown[] }): string {
  return (result.content[0] as { type: "text"; text: string }).text;
}

// ---------------------------------------------------------------------------
// Fixture builder — real TOON text via the reference encoder
// ---------------------------------------------------------------------------

interface EnvelopeOpts {
  ok?: boolean;
  help?: string[];
  error?: string;
  address?: string;
  warnings?: string[];
}

function envelope(verb: string, fields: Record<string, unknown> = {}, opts: EnvelopeOpts = {}): string {
  const ok = opts.ok ?? true;
  const context: Record<string, unknown> = { project: "Demo" };
  if (opts.address !== undefined) context.address = opts.address;
  const axi: Record<string, unknown> = { verb, ok, ...fields, context };
  if (opts.error !== undefined) axi.error = opts.error;
  if (opts.help !== undefined) axi.help = opts.help;
  axi.warnings = opts.warnings ?? [];
  return encode({ axi });
}

// One happy-path fixture per verb tool (§4.4 shapes, minimal but decodable).
const VERB_FIXTURES: Record<string, string> = {
  setup: envelope("setup", { result: "provisioned" }),
  register: envelope(
    "register",
    { address: "Mainline - Demo", project: "Demo", kind: "mainline", result: "registered" },
    { address: "Mainline - Demo" },
  ),
  unregister: envelope(
    "unregister",
    { address: "Track 1 - Demo", project: "Demo", kind: "track", result: "unregistered" },
    { address: "Track 1 - Demo" },
  ),
  addressbook: envelope("addressbook", {
    participants: [{ address: "Mainline - Demo", listening: false }],
    listening: "0/1",
  }),
  send: envelope(
    "send",
    { id: 1, to: "Mainline - Demo", kind: "request", subject: "hi", delivered: 1 },
    { address: "Track 1 - Demo", help: ["sandesh thread --id 1"] },
  ),
  reply: envelope(
    "reply",
    { id: 2, to: "Track 1 - Demo", subject: "Re: hi", re: 1, delivered: 1 },
    { address: "Mainline - Demo" },
  ),
  inbox: envelope(
    "inbox",
    { messages: [{ id: 1, from: "Track 1 - Demo", subject: "hi", unread: true }], unread: "1 of 1" },
    { address: "Mainline - Demo" },
  ),
  fetch: envelope(
    "fetch",
    { messages: [{ id: 1, from: "Track 1 - Demo", subject: "hi" }], marked_read: 1 },
    { address: "Mainline - Demo" },
  ),
  thread: envelope("thread", { messages: [{ id: 1, from: "Track 1 - Demo", subject: "hi" }] }),
  archive: envelope("archive", { project: "Demo", result: "archived" }),
  unarchive: envelope("unarchive", { project: "Demo", result: "active" }),
  search: envelope(
    "search",
    { hits: [{ id: 1, from: "Track 1 - Demo", subject: "hi" }], total: 1 },
    { address: "Mainline - Demo" },
  ),
};

// Tool name → CLI verb + minimal valid params.
const TOOLS: Array<{ name: string; verb: string; params: Record<string, unknown> }> = [
  { name: "sandesh_setup", verb: "setup", params: { project_id: "Demo" } },
  { name: "sandesh_register", verb: "register", params: { address: "Mainline - Demo", project_id: "Demo" } },
  { name: "sandesh_unregister", verb: "unregister", params: { address: "Track 1 - Demo", project_id: "Demo" } },
  { name: "sandesh_addressbook", verb: "addressbook", params: { project_id: "Demo" } },
  {
    name: "sandesh_send",
    verb: "send",
    params: { from: "Track 1 - Demo", to: ["Mainline - Demo"], subject: "hi", project_id: "Demo" },
  },
  { name: "sandesh_reply", verb: "reply", params: { parent_id: 1, project_id: "Demo" } },
  { name: "sandesh_inbox", verb: "inbox", params: { recipient: "Mainline - Demo", project_id: "Demo" } },
  { name: "sandesh_fetch", verb: "fetch", params: { recipient: "Mainline - Demo", project_id: "Demo" } },
  { name: "sandesh_thread", verb: "thread", params: { msg_id: 1, project_id: "Demo" } },
  { name: "sandesh_archive", verb: "archive", params: { project_id: "Demo", by: "Mainline - Demo" } },
  { name: "sandesh_unarchive", verb: "unarchive", params: { project_id: "Demo", by: "Mainline - Demo" } },
  { name: "sandesh_search", verb: "search", params: { recipient: "Mainline - Demo", query: "hello" } },
];

// ---------------------------------------------------------------------------
// AC2 — `--format toon` on every call, pass-through byte-identical
// ---------------------------------------------------------------------------

describe("AC2 — --format toon on every one of the 12 verb tools", () => {
  for (const { name, verb, params } of TOOLS) {
    test(`${name}: argv carries --format toon before the verb; result is the fixture byte-for-byte`, async () => {
      const fixture = VERB_FIXTURES[verb];
      const { getTool, execMock } = setup({ stdout: fixture, stderr: "", code: 0, killed: false });
      const tool = getTool(name);

      const result = await callExecute(tool, params);

      expect(execMock.mock.calls.length).toBe(1);
      const [, args] = execMock.mock.calls[0] as [string, string[]];

      const formatIdx = args.indexOf("--format");
      expect(formatIdx).toBeGreaterThanOrEqual(0);
      expect(args[formatIdx + 1]).toBe("toon");

      const verbIdx = args.indexOf(verb);
      expect(verbIdx).toBeGreaterThanOrEqual(0);
      expect(verbIdx).toBeGreaterThan(formatIdx);

      // Byte-identical pass-through (P1) — no re-rendering.
      expect(text(result)).toBe(fixture);

      const env = decodeEnvelope(text(result));
      expect(env.verb).toBe(verb);
      expect(env.ok).toBe(true);
    });
  }
});

// ---------------------------------------------------------------------------
// AC2 — AXI knobs: fields / full / limit
// ---------------------------------------------------------------------------

describe("AC2 — AXI knobs: sandesh_inbox fields/limit", () => {
  test("fields:['id','from','to'] maps to --fields id,from,to", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.inbox, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo", fields: ["id", "from", "to"] });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    const idx = args.indexOf("--fields");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(args[idx + 1]).toBe("id,from,to");
  });

  test("limit:20 maps to --limit 20", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.inbox, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo", limit: 20 });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    const idx = args.indexOf("--limit");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(args[idx + 1]).toBe("20");
  });

  test("omitted fields/limit add neither flag", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.inbox, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_inbox");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--fields");
    expect(args).not.toContain("--limit");
  });
});

describe("AC2 — AXI knobs: sandesh_addressbook fields", () => {
  test("fields:['address','kind','status'] maps to --fields address,kind,status", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.addressbook, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_addressbook");

    await callExecute(tool, { project_id: "Demo", fields: ["address", "kind", "status"] });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    const idx = args.indexOf("--fields");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(args[idx + 1]).toBe("address,kind,status");
  });

  test("omitted fields adds no flag", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.addressbook, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_addressbook");

    await callExecute(tool, { project_id: "Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--fields");
  });
});

describe("AC2 — AXI knobs: sandesh_fetch/sandesh_thread full", () => {
  test("sandesh_fetch: full:true maps to --full", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.fetch, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo", full: true });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--full");
  });

  test("sandesh_fetch: full:false does NOT add --full", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.fetch, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo", full: false });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--full");
  });

  test("sandesh_fetch: full omitted does NOT add --full", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.fetch, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_fetch");

    await callExecute(tool, { recipient: "Mainline - Demo", project_id: "Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--full");
  });

  test("sandesh_thread: full:true maps to --full", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.thread, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_thread");

    await callExecute(tool, { msg_id: 1, project_id: "Demo", full: true });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).toContain("--full");
  });

  test("sandesh_thread: full:false/omitted does NOT add --full", async () => {
    const { getTool, execMock } = setup({ stdout: VERB_FIXTURES.thread, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_thread");

    await callExecute(tool, { msg_id: 1, project_id: "Demo" });

    const [, args] = execMock.mock.calls[0] as [string, string[]];
    expect(args).not.toContain("--full");
  });
});

describe("AC2 — AXI knob param schemas declare the right TypeBox types", () => {
  test("sandesh_inbox parameters declare fields (array) and limit (integer|number)", () => {
    const { getTool } = setup();
    const props = (getTool("sandesh_inbox").parameters as any).properties;
    expect(props.fields).toBeDefined();
    expect(props.fields.type).toBe("array");
    expect(props.limit).toBeDefined();
    expect(["integer", "number"]).toContain(props.limit.type);
  });

  test("sandesh_addressbook parameters declare fields (array)", () => {
    const { getTool } = setup();
    const props = (getTool("sandesh_addressbook").parameters as any).properties;
    expect(props.fields).toBeDefined();
    expect(props.fields.type).toBe("array");
  });

  test("sandesh_fetch parameters declare full (boolean)", () => {
    const { getTool } = setup();
    const props = (getTool("sandesh_fetch").parameters as any).properties;
    expect(props.full).toBeDefined();
    expect(props.full.type).toBe("boolean");
  });

  test("sandesh_thread parameters declare full (boolean)", () => {
    const { getTool } = setup();
    const props = (getTool("sandesh_thread").parameters as any).properties;
    expect(props.full).toBeDefined();
    expect(props.full.type).toBe("boolean");
  });
});

// ---------------------------------------------------------------------------
// AC3 — errors as results
// ---------------------------------------------------------------------------

describe("AC3 — errors as results", () => {
  test("sandesh_send: exit 1 with an ok:false envelope on stdout resolves (not throws) with error+help[1]", async () => {
    const errEnvelope = envelope(
      "send",
      {},
      {
        ok: false,
        error: "no recipients (after excluding the sender)",
        help: ["sandesh addressbook"],
        address: "Track 1 - Demo",
      },
    );
    const { getTool } = setup({ stdout: errEnvelope, stderr: "[sandesh] no recipients", code: 1, killed: false });
    const tool = getTool("sandesh_send");

    const result = await callExecute(tool, {
      from: "Track 1 - Demo",
      to: ["Mainline - Demo"],
      subject: "hi",
      project_id: "Demo",
    });

    expect(text(result)).toBe(errEnvelope);
    const env = decodeEnvelope(text(result));
    expect(env.ok).toBe(false);
    expect(env.error).toBe("no recipients (after excluding the sender)");
    expect(env.help).toHaveLength(1);
  });

  test("sandesh_send: exit 1 with undecodable/empty stdout still throws verb+code+stderr (unchanged)", async () => {
    const { getTool } = setup({ stdout: "", stderr: "boom", code: 1, killed: false });
    const tool = getTool("sandesh_send");

    let thrown: unknown;
    try {
      await callExecute(tool, {
        from: "Track 1 - Demo",
        to: ["Mainline - Demo"],
        subject: "hi",
        project_id: "Demo",
      });
    } catch (e) {
      thrown = e;
    }
    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain("sandesh send failed (exit 1)");
    expect((thrown as Error).message).toContain("boom");
  });

  test("sandesh_send: exit 2 usage error with an ok:false envelope resolves as a result (not thrown)", async () => {
    const usageEnvelope = envelope(
      "send",
      {},
      { ok: false, error: "unknown flag --bogus", help: ["sandesh send --help"] },
    );
    const { getTool } = setup({ stdout: usageEnvelope, stderr: "", code: 2, killed: false });
    const tool = getTool("sandesh_send");

    const result = await callExecute(tool, {
      from: "Track 1 - Demo",
      to: ["Mainline - Demo"],
      subject: "hi",
      project_id: "Demo",
    });

    expect(text(result)).toBe(usageEnvelope);
    const env = decodeEnvelope(text(result));
    expect(env.ok).toBe(false);
    expect(env.error).toBe("unknown flag --bogus");
  });

  test("sandesh_unregister: exit 3 with an ok:true tombstoned envelope resolves byte-identical", async () => {
    const tombstoned = envelope(
      "unregister",
      { address: "Track 1 - Demo", project: "Demo", result: "tombstoned" },
      { address: "Track 1 - Demo" },
    );
    const { getTool } = setup({ stdout: tombstoned, stderr: "", code: 3, killed: false });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, { address: "Track 1 - Demo", project_id: "Demo" });

    expect(text(result)).toBe(tombstoned);
    const env = decodeEnvelope(text(result));
    expect(env.ok).toBe(true);
    expect(env.fields.result).toBe("tombstoned");
  });

  test("sandesh_unregister: exit 0 with an ok:true absent envelope resolves byte-identical", async () => {
    const absent = envelope(
      "unregister",
      { address: "Track 1 - Demo", project: "Demo", result: "absent" },
      { address: "Track 1 - Demo" },
    );
    const { getTool } = setup({ stdout: absent, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_unregister");

    const result = await callExecute(tool, { address: "Track 1 - Demo", project_id: "Demo" });

    expect(text(result)).toBe(absent);
    const env = decodeEnvelope(text(result));
    expect(env.fields.result).toBe("absent");
  });
});

// ---------------------------------------------------------------------------
// P1 — no re-rendering: exact bytes, not trimmed
// ---------------------------------------------------------------------------

describe("P1 — no re-rendering", () => {
  test("stdout with trailing newline and exact spacing is returned byte-identical (=== not trimmed)", async () => {
    const raw = "axi:\n  verb: addressbook\n  ok: true\n  context:\n    project: Demo\n  warnings: []\n\n";
    const { getTool } = setup({ stdout: raw, stderr: "", code: 0, killed: false });
    const tool = getTool("sandesh_addressbook");

    const result = await callExecute(tool, { project_id: "Demo" });

    expect(text(result)).toBe(raw);
    expect(text(result).endsWith("\n\n")).toBe(true);
    expect(text(result)).not.toBe(raw.trim());
  });
});

// ---------------------------------------------------------------------------
// P10 — tool descriptions: concise + name their knobs
// ---------------------------------------------------------------------------

describe("P10 — tool descriptions", () => {
  test("every one of the 12 verb tools has a description ≤ 320 chars", () => {
    const { capturedTools } = setup();
    for (const { name } of TOOLS) {
      const tool = capturedTools.get(name);
      expect(tool).toBeDefined();
      expect((tool as CapturedTool).description.length).toBeLessThanOrEqual(320);
    }
  });

  test("sandesh_addressbook description mentions the fields knob", () => {
    const { getTool } = setup();
    expect(getTool("sandesh_addressbook").description).toContain("fields");
  });

  test("sandesh_inbox description mentions the fields and limit knobs", () => {
    const { getTool } = setup();
    const d = getTool("sandesh_inbox").description;
    expect(d).toContain("fields");
    expect(d).toContain("limit");
  });

  test("sandesh_fetch description mentions the full knob", () => {
    const { getTool } = setup();
    expect(getTool("sandesh_fetch").description).toContain("full");
  });

  test("sandesh_thread description mentions the full knob", () => {
    const { getTool } = setup();
    expect(getTool("sandesh_thread").description).toContain("full");
  });
});
