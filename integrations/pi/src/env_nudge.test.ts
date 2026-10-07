/**
 * CR-SAN-051 §S1 — RED: the unexported-identity nudge, exercised through the
 * REAL registered `session_start` handler of `./index`'s default export
 * (AC3, AC4, AC5, AC6).
 *
 * The expected notice text is reproduced LOCALLY in `expectedNotice()` below
 * rather than imported from `./identity` — `identity.ts` does not exist yet
 * (see identity.test.ts, which pins its exports byte-for-byte), and this file
 * must still compile/import `./index` cleanly so each test below runs and
 * reports its own pass/fail (only the AC6 "calls unexportedIdentityKeys("
 * test and the AC3 nudge assertions are expected to fail here; the AC4
 * silence cases may pass trivially against today's unmodified session_start
 * — see the RED report).
 *
 * Fixture pattern mirrors integrations/pi/src/ambient.test.ts (makeFakePi /
 * makeFakeCtx / flush) and the env save/restore pattern used across the
 * suite (SANDESH_ADDRESS, SANDESH_PROJECT, SANDESH_AUTOSTART, SANDESH_BIN).
 *
 * Design: docs/changes/CR-SAN-051-pi-env-identity-nudge.md §S1;
 * docs/research/PRD-axi-toon.md §4.6/§4.7.
 */

import { test, expect, describe, mock, beforeEach, afterEach } from "bun:test";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import type { ExecResult, ExtensionAPI, ExtensionContext, ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ─── Local mirror of identity.ts's §S1 notice string — see file header. ────

function expectedNotice(keys: string[]): string {
  return (
    `Sandesh identity found in ./.env but not exported: ${keys.join(", ")}. ` +
    "Load it at the shell (direnv: an .envrc containing `dotenv`, then `direnv allow`) and restart pi — " +
    "until then there is no ambient status and no wake."
  );
}

const NUDGE_PREFIX = "Sandesh identity found in ./.env but not exported:";

// ─── Fake exec — mirrors wake_lifecycle.test.ts / ambient.test.ts: `--version`
// resolves per `versionOk` (default true), `init` resolves ok, `notify` hangs
// forever so an unexpected wake-arm spawn times the test out loudly rather
// than silently passing. ────────────────────────────────────────────────────

function makeFakeExec(opts?: { versionOk?: boolean }) {
  const versionOk = opts?.versionOk ?? true;
  const calls: Array<{ cmd: string; args: string[] }> = [];
  const exec = mock((cmd: string, args: string[], _opts?: unknown): Promise<ExecResult> => {
    calls.push({ cmd, args });
    if (args.includes("--version")) {
      if (versionOk) return Promise.resolve({ stdout: "sandesh 0.4.2", stderr: "", code: 0, killed: false });
      return Promise.reject(new Error("ENOENT: sandesh not found"));
    }
    if (args.includes("init")) {
      return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
    }
    if (args.includes("notify")) {
      return new Promise<ExecResult>(() => {});
    }
    return Promise.resolve({ stdout: "", stderr: "", code: 0, killed: false });
  });
  return { exec, calls };
}

function makeFakePi(execOpts?: { versionOk?: boolean }) {
  const handlers = new Map<string, (event: unknown, ctx: ExtensionContext) => unknown>();
  const tools = new Map<string, ToolDefinition<any, any, any>>();
  const { exec, calls } = makeFakeExec(execOpts);
  const sendMessageMock = mock((_msg: unknown, _opts?: unknown): void => {});
  const sendUserMessageMock = mock((_text: string, _opts?: unknown): void => {});
  const fakePi = {
    registerTool: mock((tool: ToolDefinition<any, any, any>) => {
      tools.set(tool.name, tool);
    }),
    registerCommand: mock(() => {}),
    on: mock((event: string, handler: unknown) => {
      handlers.set(event, handler as (event: unknown, ctx: ExtensionContext) => unknown);
    }),
    exec,
    sendMessage: sendMessageMock,
    sendUserMessage: sendUserMessageMock,
  } as unknown as ExtensionAPI;
  return { fakePi, handlers, tools, calls };
}

/** Every `SANDESH_*` entry of the process environment, as a plain object. */
function sandeshEnv(): Record<string, string | undefined> {
  return Object.fromEntries(Object.entries(process.env).filter(([k]) => k.startsWith("SANDESH_")));
}

function makeFakeCtx(cwd?: string) {
  const notifyCalls: Array<{ msg: string; type?: string }> = [];
  const fakeCtx: Record<string, unknown> = {
    ui: {
      notify: mock((msg: string, type?: "info" | "warning" | "error") => {
        notifyCalls.push({ msg, type });
      }),
    },
  };
  if (cwd !== undefined) fakeCtx.cwd = cwd;
  return { fakeCtx: fakeCtx as unknown as ExtensionContext, notifyCalls };
}

async function flush(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

// ─── Per-test temp dirs (AC3/AC4/AC5 need a real `<cwd>/.env`) — tracked and
// removed in afterEach regardless of test outcome. ──────────────────────────

let tmpDirs: string[] = [];
function makeTmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-pi-nudge-"));
  tmpDirs.push(dir);
  return dir;
}

// ─── Env save/restore (identity + autostart + bin override). ──────────────

const ENV_KEYS = ["SANDESH_ADDRESS", "SANDESH_PROJECT", "SANDESH_AUTOSTART", "SANDESH_BIN"] as const;
const SAVED_ENV: Partial<Record<(typeof ENV_KEYS)[number], string>> = {};

function saveEnv(): void {
  for (const key of ENV_KEYS) SAVED_ENV[key] = process.env[key];
}
function restoreEnv(): void {
  for (const key of ENV_KEYS) {
    if (SAVED_ENV[key] === undefined) delete process.env[key];
    else process.env[key] = SAVED_ENV[key];
  }
}

beforeEach(() => {
  saveEnv();
});

afterEach(() => {
  restoreEnv();
  for (const dir of tmpDirs) fs.rmSync(dir, { recursive: true, force: true });
  tmpDirs = [];
});

const fakeSessionStartEvent = { type: "session_start", reason: "startup" } as const;

// ============================================================================
// AC3 — the nudge fires through the real session_start handler
// ============================================================================

describe("§S1 — the unexported-identity nudge (AC3)", () => {
  test("fires exactly once, at warning level, with the exact notice text, even when the CLI probe fails (missing-CLI path); identity vars remain unset afterwards", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    const tmpDir = makeTmpDir();
    fs.writeFileSync(path.join(tmpDir, ".env"), "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n");

    const { fakePi, handlers } = makeFakePi({ versionOk: false });
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;

    // If session_start let anything unexpected escape (e.g. from reading the
    // .env or building the notice), this await throwing IS a test failure.
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    const expected = expectedNotice(["SANDESH_ADDRESS", "SANDESH_PROJECT"]);
    const nudgeCalls = notifyCalls.filter((n) => n.type === "warning" && n.msg === expected);
    expect(nudgeCalls.length).toBe(1);

    expect(process.env.SANDESH_ADDRESS).toBeUndefined();
    expect(process.env.SANDESH_PROJECT).toBeUndefined();
  });
});

// ============================================================================
// AC4 — silence cases (a)-(f)
// ============================================================================

describe("§S1 — the unexported-identity nudge: silence (AC4)", () => {
  test("AC4(a) — both identity vars already set: no nudge even though .env also assigns them", async () => {
    process.env.SANDESH_ADDRESS = "Mainline - Demo";
    process.env.SANDESH_PROJECT = "Demo";
    delete process.env.SANDESH_AUTOSTART;
    const tmpDir = makeTmpDir();
    fs.writeFileSync(path.join(tmpDir, ".env"), "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n");

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });

  test("AC4(b) — <cwd>/.env does not exist: no nudge, no throw", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    const tmpDir = makeTmpDir(); // deliberately left empty

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });

  test("AC4(c) — .env assigns neither identity key: no nudge", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    const tmpDir = makeTmpDir();
    fs.writeFileSync(path.join(tmpDir, ".env"), "FOO=bar\nBAZ=qux\n");

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;
    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });

  test("AC4(d) — ctx.cwd is undefined: no nudge, session_start resolves without throwing", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(); // no cwd at all
    const startHandler = handlers.get("session_start")!;

    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });

  test("AC4(e) — <cwd>/.env is a directory (unreadable): no nudge, no throw", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    const tmpDir = makeTmpDir();
    fs.mkdirSync(path.join(tmpDir, ".env"));

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;

    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });

  test("AC4(f) — .env only in the PARENT of cwd: no walk-up, no nudge", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    const parentDir = makeTmpDir();
    fs.writeFileSync(path.join(parentDir, ".env"), "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n");
    const childDir = path.join(parentDir, "child");
    fs.mkdirSync(childDir);

    const { fakePi, handlers } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(childDir);
    const startHandler = handlers.get("session_start")!;

    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(false);
  });
});

// ============================================================================
// AC5 — SANDESH_AUTOSTART=1 co-occurrence: both notices fire, nothing spawns
// ============================================================================

describe("§S1 — the unexported-identity nudge: co-occurrence with autostart (AC5)", () => {
  test("SANDESH_AUTOSTART=1 with both vars unset and a .env assigning them: both AUTOSTART_ENV_NOTICE and the nudge fire; no notify exec spawned", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    process.env.SANDESH_AUTOSTART = "1";
    const tmpDir = makeTmpDir();
    fs.writeFileSync(path.join(tmpDir, ".env"), "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n");

    const { fakePi, handlers, calls } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    const startHandler = handlers.get("session_start")!;

    await startHandler(fakeSessionStartEvent, fakeCtx);
    await flush();

    const warnings = notifyCalls.filter((n) => n.type === "warning");

    // The existing AUTOSTART_ENV_NOTICE (wake_lifecycle.test.ts's own
    // assertion style: filter by severity, check it names both vars).
    const autostartNotices = warnings.filter((n) => n.msg.includes("SANDESH_AUTOSTART=1"));
    expect(autostartNotices.length).toBe(1);
    expect(autostartNotices[0]!.msg).toContain("SANDESH_ADDRESS");
    expect(autostartNotices[0]!.msg).toContain("SANDESH_PROJECT");

    // The nudge, exact text.
    const nudgeNotices = warnings.filter((n) => n.msg === expectedNotice(["SANDESH_ADDRESS", "SANDESH_PROJECT"]));
    expect(nudgeNotices.length).toBe(1);

    // No watcher armed.
    expect(calls.some((c) => c.args.includes("notify"))).toBe(false);
  });
});

// ============================================================================
// AC6 — caller existence + no ad-hoc process.env.SANDESH_ writes
// ============================================================================

describe("§S1 — AC6 the extension never writes the identity into process.env", () => {
  test("session_start (nudge fired from ./.env) plus a tool call leave every SANDESH_* env entry exactly as it was", async () => {
    delete process.env.SANDESH_ADDRESS;
    delete process.env.SANDESH_PROJECT;
    process.env.SANDESH_AUTOSTART = "1";
    const tmpDir = makeTmpDir();
    fs.writeFileSync(path.join(tmpDir, ".env"), "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n");
    const before = sandeshEnv();

    const { fakePi, handlers, tools } = makeFakePi();
    registerExtension(fakePi);
    const { fakeCtx, notifyCalls } = makeFakeCtx(tmpDir);
    await handlers.get("session_start")!(fakeSessionStartEvent, fakeCtx);
    await flush();
    expect(notifyCalls.some((n) => n.msg.startsWith(NUDGE_PREFIX))).toBe(true);

    const statusTool = tools.get("sandesh_notify_status");
    expect(statusTool).toBeDefined();
    await statusTool!.execute("test-call-id", {}, undefined, undefined, fakeCtx);
    await flush();

    expect(sandeshEnv()).toEqual(before);
    expect(process.env.SANDESH_ADDRESS).toBeUndefined();
    expect(process.env.SANDESH_PROJECT).toBeUndefined();
  });
});
