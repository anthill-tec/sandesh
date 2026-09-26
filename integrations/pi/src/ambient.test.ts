/**
 * CR-SAN-048 §S2b — ambient context injection at session_start
 * (PRD-axi-toon.md §4.0 P7 (amended), AC3b (amended)).
 *
 * On session_start, when BOTH `$SANDESH_ADDRESS` and `$SANDESH_PROJECT` are
 * set, the extension runs the home view once (`sandesh --format toon
 * status`), strips the `bin:` and `description:` lines, and injects the rest
 * via `pi.sendMessage({customType: "sandesh-status", content: <envelope
 * text>, display: true}, {triggerTurn: false})` — a context message, not a
 * user turn (≤ 12 lines incl. `help[2]`). Nothing is injected when either
 * var is unset, and a probe failure never breaks session start (existing
 * guard).
 *
 * Fixture: the mocked `status` stdout is ENCODED from the real CLI envelope
 * shape (`cli._status_fields` + the axi wrapper: bin, description, project,
 * address, listening, unread, context.project, help[2], warnings) via
 * `@toon-format/toon` — not a hand-written text — so the "no bin/description
 * line", "still decodes" and "≤ 12 lines" assertions run against the real
 * shape the extension must strip (VERIFY C7 finding 2).
 *
 * The three negative-outcome tests ("SANDESH_ADDRESS unset", "SANDESH_PROJECT
 * unset", "status probe failure") are regression guards for the identity
 * gating and the never-throws guard.
 */

import { test, expect, describe, mock } from "bun:test";
import { encode } from "@toon-format/toon";
import type { ExecResult, ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ─── The REAL `sandesh --format toon status` envelope shape, encoded with the
// same TOON encoder the CLI's output is decoded by. `bin`/`description` are
// the two lines the extension must strip before injecting. ───────────────────

const STATUS_ENVELOPE_TEXT = encode({
  axi: {
    verb: "status",
    ok: true,
    bin: "~/x/sandesh",
    description: "Sandesh — SQLite-backed relay mailbox for cooperating agent sessions",
    project: "Demo",
    address: "Mainline - Demo",
    listening: false,
    unread: 1,
    context: { project: "Demo" },
    help: ["a", "b"],
    warnings: [],
  },
});

function makeFakeExec(statusResult: ExecResult) {
  const calls: Array<{ cmd: string; args: string[] }> = [];
  const exec = mock((cmd: string, args: string[], _opts?: unknown): Promise<ExecResult> => {
    calls.push({ cmd, args });
    if (args.includes("--version")) {
      return Promise.resolve({ stdout: "sandesh 0.4.0", stderr: "", code: 0, killed: false });
    }
    if (args.includes("init")) {
      return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
    }
    if (args.includes("status")) {
      return Promise.resolve(statusResult);
    }
    // "notify" (arming) is not exercised by these tests (SANDESH_AUTOSTART is
    // never set here) — hang forever so an unexpected spawn would time the
    // test out loudly rather than silently returning a misleading result.
    return new Promise<ExecResult>(() => {});
  });
  return { exec, calls };
}

function makeFakePi(statusResult: ExecResult) {
  const handlers = new Map<string, (event: unknown, ctx: ExtensionContext) => unknown>();
  const { exec, calls } = makeFakeExec(statusResult);
  const sendMessageMock = mock((_msg: unknown, _opts?: unknown): void => {});
  const sendUserMessageMock = mock((_text: string, _opts?: unknown): void => {});
  const fakePi = {
    registerTool: mock(() => {}),
    registerCommand: mock(() => {}),
    on: mock((event: string, handler: unknown) => {
      handlers.set(event, handler as (event: unknown, ctx: ExtensionContext) => unknown);
    }),
    exec,
    sendMessage: sendMessageMock,
    sendUserMessage: sendUserMessageMock,
  } as unknown as ExtensionAPI;
  return { fakePi, handlers, calls, sendMessageMock, sendUserMessageMock };
}

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

async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

const SAVED_ENV: Partial<Record<string, string>> = {};
function saveEnv(): void {
  SAVED_ENV.SANDESH_ADDRESS = process.env.SANDESH_ADDRESS;
  SAVED_ENV.SANDESH_PROJECT = process.env.SANDESH_PROJECT;
}
function restoreEnv(): void {
  if (SAVED_ENV.SANDESH_ADDRESS === undefined) delete process.env.SANDESH_ADDRESS;
  else process.env.SANDESH_ADDRESS = SAVED_ENV.SANDESH_ADDRESS;
  if (SAVED_ENV.SANDESH_PROJECT === undefined) delete process.env.SANDESH_PROJECT;
  else process.env.SANDESH_PROJECT = SAVED_ENV.SANDESH_PROJECT;
}

const fakeSessionStartEvent = { type: "session_start", reason: "startup" } as const;

describe("Ambient context injection at session_start (§S2b, AC3b)", () => {
  test("fixture sanity: the real status shape carries bin:/description: lines before stripping", () => {
    // Guards the fixture itself — if the encoder ever inlined these keys the
    // positive test's "no bin:/description: line" assertion would be vacuous.
    const lines = STATUS_ENVELOPE_TEXT.split("\n");
    expect(lines.some((l) => /^  bin: /.test(l))).toBe(true);
    expect(lines.some((l) => /^  description: /.test(l))).toBe(true);
    expect(decodeEnvelope(STATUS_ENVELOPE_TEXT).fields.bin).toBe("~/x/sandesh");
  });

  test("both identity vars set → exactly one sendMessage(customType:sandesh-status, display:true, {triggerTurn:false}) whose content is the status envelope minus bin:/description:, still decodes, and is ≤12 lines", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    try {
      const { fakePi, handlers, sendMessageMock } = makeFakePi({
        stdout: STATUS_ENVELOPE_TEXT,
        stderr: "",
        code: 0,
        killed: false,
      });
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(sendMessageMock.mock.calls.length).toBe(1);
      const [msg, opts] = sendMessageMock.mock.calls[0] as [
        { customType: string; content: string; display: boolean },
        { triggerTurn: boolean },
      ];
      expect(msg.customType).toBe("sandesh-status");
      expect(msg.display).toBe(true);
      expect(opts).toEqual({ triggerTurn: false });

      // AC3b (amended): no bin:/description: line survives the strip …
      const lines = msg.content.split("\n");
      expect(lines.some((l) => /^\s*bin:/.test(l))).toBe(false);
      expect(lines.some((l) => /^\s*description:/.test(l))).toBe(false);
      // … it is still a valid TOON envelope carrying the home fields …
      const env = decodeEnvelope(msg.content);
      expect(env.verb).toBe("status");
      expect(env.ok).toBe(true);
      expect(env.fields.address).toBe("Mainline - Demo");
      expect(env.fields.listening).toBe(false);
      expect(env.fields.unread).toBe(1);
      expect(env.fields.bin).toBeUndefined();
      expect(env.fields.description).toBeUndefined();
      expect(env.help).toEqual(["a", "b"]);
      expect(env.context.project).toBe("Demo");
      // … and fits the ≤ 12-line budget (incl. help[2]).
      expect(lines.length).toBeLessThanOrEqual(12);
    } finally {
      restoreEnv();
    }
  });

  test("SANDESH_ADDRESS unset → sendMessage is never called", async () => {
    saveEnv();
    delete process.env.SANDESH_ADDRESS;
    process.env.SANDESH_PROJECT = "Demo";
    try {
      const { fakePi, handlers, sendMessageMock } = makeFakePi({
        stdout: STATUS_ENVELOPE_TEXT,
        stderr: "",
        code: 0,
        killed: false,
      });
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(sendMessageMock.mock.calls.length).toBe(0);
    } finally {
      restoreEnv();
    }
  });

  test("SANDESH_PROJECT unset → sendMessage is never called", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    delete process.env.SANDESH_PROJECT;
    try {
      const { fakePi, handlers, sendMessageMock } = makeFakePi({
        stdout: STATUS_ENVELOPE_TEXT,
        stderr: "",
        code: 0,
        killed: false,
      });
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(sendMessageMock.mock.calls.length).toBe(0);
    } finally {
      restoreEnv();
    }
  });

  test("status probe failure (exit 1, undecodable stdout) → no sendMessage, no throw, session start completes", async () => {
    saveEnv();
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    try {
      const { fakePi, handlers, sendMessageMock } = makeFakePi({
        stdout: "",
        stderr: "boom",
        code: 1,
        killed: false,
      });
      registerExtension(fakePi);
      const { fakeCtx } = makeFakeCtx();
      const startHandler = handlers.get("session_start")!;

      // If session_start let the probe failure escape, this await would throw
      // and fail the test — that IS the "no throw" assertion.
      await startHandler(fakeSessionStartEvent, fakeCtx);
      await flush();

      expect(sendMessageMock.mock.calls.length).toBe(0);
    } finally {
      restoreEnv();
    }
  });
});
