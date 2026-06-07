/**
 * CR-SAN-013 C0 — RED: 9-tool registration surface
 *
 * Asserts AC2 + AC3:
 *   AC2: registerTool called exactly 9 times with the exact tool names.
 *   AC3: each captured def has non-empty name/label/description and a TypeBox
 *        parameters object; spot-checks properties on sandesh_send, sandesh_reply,
 *        sandesh_register.
 *
 * This test imports from ./index which does NOT yet exist → import error → RED.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Minimal ExtensionAPI fake — only registerTool is exercised in C0. */
function makeFakePi() {
  const capturedTools: ToolDefinition[] = [];

  const registerTool = mock((tool: ToolDefinition) => {
    capturedTools.push(tool);
  });

  const fakePi = {
    registerTool,
    // Stub the rest of ExtensionAPI so TypeScript is satisfied at the call site.
    // C0 only exercises registerTool; all other members are unused.
    exec: mock(async () => ({ stdout: "", stderr: "", code: 0, killed: false })),
    on: mock(() => {}),
  } as unknown as ExtensionAPI;

  return { fakePi, capturedTools, registerTool };
}

// ---------------------------------------------------------------------------
// AC2 — exactly 9 tools, exact names, sandesh_actioned absent
// ---------------------------------------------------------------------------

describe("registerExtension — registration surface (AC2)", () => {
  test("calls registerTool exactly 9 times", () => {
    const { fakePi, registerTool } = makeFakePi();
    registerExtension(fakePi);
    expect(registerTool.mock.calls.length).toBe(9);
  });

  test("registers exactly the 9 specified tool names", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const names = capturedTools.map((t) => t.name).sort();
    expect(names).toEqual([
      "sandesh_addressbook",
      "sandesh_fetch",
      "sandesh_inbox",
      "sandesh_register",
      "sandesh_reply",
      "sandesh_send",
      "sandesh_setup",
      "sandesh_thread",
      "sandesh_unregister",
    ]);
  });

  test("does NOT register sandesh_actioned", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const names = capturedTools.map((t) => t.name);
    expect(names).not.toContain("sandesh_actioned");
  });

  test("does NOT register sandesh_notify", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const names = capturedTools.map((t) => t.name);
    expect(names).not.toContain("sandesh_notify");
  });
});

// ---------------------------------------------------------------------------
// AC3 — each tool has non-empty name/label/description and TypeBox parameters
// ---------------------------------------------------------------------------

describe("registerExtension — ToolDefinition shape (AC3)", () => {
  test("every tool has a non-empty name, label and description", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    for (const tool of capturedTools) {
      expect(typeof tool.name).toBe("string");
      expect(tool.name.length).toBeGreaterThan(0);
      expect(typeof tool.label).toBe("string");
      expect(tool.label.length).toBeGreaterThan(0);
      expect(typeof tool.description).toBe("string");
      expect(tool.description.length).toBeGreaterThan(0);
    }
  });

  test("every tool has a TypeBox parameters object (kind === 'object')", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    for (const tool of capturedTools) {
      expect(tool.parameters).toBeDefined();
      // TypeBox Type.Object() has kind === "object" and a properties bag
      expect(typeof tool.parameters).toBe("object");
      expect((tool.parameters as Record<string, unknown>).properties).toBeDefined();
    }
  });

  test("every tool has an execute function", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    for (const tool of capturedTools) {
      expect(typeof tool.execute).toBe("function");
    }
  });

  // sandesh_send — mapping table: from, to[], cc[]?, subject, body?, project_id?
  test("sandesh_send parameters include from, to, cc, subject, project_id", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const send = capturedTools.find((t) => t.name === "sandesh_send");
    expect(send).toBeDefined();
    const props = (send!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("from");
    expect(props).toHaveProperty("to");
    expect(props).toHaveProperty("cc");
    expect(props).toHaveProperty("subject");
    expect(props).toHaveProperty("project_id");
  });

  // sandesh_reply — mapping table: parent_id, from?
  test("sandesh_reply parameters include parent_id and from", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const reply = capturedTools.find((t) => t.name === "sandesh_reply");
    expect(reply).toBeDefined();
    const props = (reply!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("parent_id");
    expect(props).toHaveProperty("from");
  });

  // sandesh_register — mapping table: address
  test("sandesh_register parameters include address", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const register = capturedTools.find((t) => t.name === "sandesh_register");
    expect(register).toBeDefined();
    const props = (register!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("address");
  });

  // sandesh_inbox — mapping table: recipient, unread_only?
  test("sandesh_inbox parameters include recipient", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const inbox = capturedTools.find((t) => t.name === "sandesh_inbox");
    expect(inbox).toBeDefined();
    const props = (inbox!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("recipient");
  });

  // sandesh_fetch — mapping table: recipient, mark?
  test("sandesh_fetch parameters include recipient", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const fetch = capturedTools.find((t) => t.name === "sandesh_fetch");
    expect(fetch).toBeDefined();
    const props = (fetch!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("recipient");
  });

  // sandesh_thread — mapping table: msg_id
  test("sandesh_thread parameters include msg_id", () => {
    const { fakePi, capturedTools } = makeFakePi();
    registerExtension(fakePi);
    const thread = capturedTools.find((t) => t.name === "sandesh_thread");
    expect(thread).toBeDefined();
    const props = (thread!.parameters as Record<string, unknown>).properties as Record<string, unknown>;
    expect(props).toHaveProperty("msg_id");
  });
});
