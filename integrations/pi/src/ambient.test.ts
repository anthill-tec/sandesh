/**
 * CR-SAN-048 §S2b — RED: ambient context injection at session_start
 * (PRD-axi-toon.md §4.6 "Ambient context" P7, AC3b).
 *
 * On session_start, when BOTH `$SANDESH_ADDRESS` and `$SANDESH_PROJECT` are
 * set, the extension runs the home view once (`sandesh --format toon
 * status`) and injects it via `pi.sendMessage({customType: "sandesh-status",
 * content: <envelope text>, display: true}, {triggerTurn: false})` — a
 * context message, not a user turn (≤6 lines). Nothing is injected when
 * either var is unset, and a probe failure never breaks session start
 * (existing guard).
 *
 * RED reason: today's session_start handler never reads or calls
 * `pi.sendMessage` at all — it only probes `--version`/`init --check` and
 * (conditionally) starts the old `wakeLoop`. Every test here fails because
 * `sendMessageMock.mock.calls.length` stays 0 in the "both vars set" case,
 * where the spec requires exactly 1.
 *
 * Coincidental passes (documented per the axi_passthrough.test.ts precedent
 * — "a coincidental pass, not a real signal"): the three negative-outcome
 * tests ("SANDESH_ADDRESS unset", "SANDESH_PROJECT unset", "status probe
 * failure") already pass against TODAY's code, because today's code never
 * calls `pi.sendMessage` under ANY circumstance — the ambient feature does
 * not exist yet, so "sendMessage was not called" trivially holds regardless
 * of env state. These are legitimate regression guards once GREEN ships (a
 * correct GREEN that stops gating on the vars, or lets a probe failure
 * throw, would break them), but they carry no RED signal today; the file's
 * one real RED is the "both vars set" positive test.
 */

import { test, expect, describe, mock } from "bun:test";
import type { ExecResult, ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ─── A minimal, hand-written ≤6-line TOON status envelope. The real CLI's
// encoder produces compact output for the home view; this fixture stands in
// for the mocked `pi.exec` call so the "≤6 lines" assertion is a pass-through
// fidelity check (GREEN must not mangle/lengthen it), not an encoder test. ──

const STATUS_ENVELOPE_TEXT =
  "axi:\n  verb: status\n  ok: true\n  address: Mainline - Demo\n  listening: true\n  unread: 0";

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
  test("both identity vars set → exactly one sendMessage(customType:sandesh-status, display:true, {triggerTurn:false}) whose content decodes to verb:status and is ≤6 lines", async () => {
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

      const env = decodeEnvelope(msg.content);
      expect(env.verb).toBe("status");
      expect(msg.content.split("\n").length).toBeLessThanOrEqual(6);
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
