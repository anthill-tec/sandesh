/**
 * CR-SAN-019 C2 AC5 / CR-SAN-048 AC10 — real-binary smoke test
 *
 * Exercises the REAL `sandesh` CLI through the shim's own argv-building
 * (catching CLI↔shim version/argv skew — finding #7 from the integration audit).
 *
 * Binary resolution (CR-SAN-048 AC10): $SANDESH_BIN → `<repo>/.venv/bin/sandesh`
 * → `sandesh` on PATH — `resolveSmokeBinary()`. The whole file SKIPS (not
 * fails) whenever the resolved binary doesn't answer `--version` at all, and
 * the AXI/wake suites additionally skip when it lacks `--format` (probed via
 * `--help`) — a CLI without `--format toon` cannot run any of the shim's verb
 * tools, since `runSandeshText` always adds `--format toon` (CR-SAN-047/048).
 *
 * Suites:
 *   1. `sandesh --version` exits 0 and prints a loose version (/^sandesh \S+/).
 *   2. setup → register → send → fetch round-trip via a real spawning
 *      `pi.exec` (drives the shim's own execute() to exercise its argv
 *      construction) into a temp $XDG_DATA_HOME; the fetch result decodes to
 *      an AXI/TOON envelope (CR-SAN-048 §S2 — no more human-text substring
 *      matching).
 *   3. (CR-SAN-048 AC10) `resolveSandesh` must honor `$SANDESH_BIN`: with it
 *      pointed at the resolved binary, `pi.exec` is called with that exact
 *      binary — not the bare `"sandesh"` name `resolveSandesh` hardcodes
 *      today.
 *   4. (CR-SAN-048 AC10) the supervised wake end to end against the real
 *      binary: `sandesh_notify_start` → a real `sandesh_send` → the
 *      supervisor's `sendUserMessage` fires naming the sent id → `stop` exits
 *      the child (< 30 s total).
 *
 * Hermetic: every spawn carries a temp $XDG_DATA_HOME; the real data home is
 * never read or written. All temp dirs are removed in the top-level afterAll.
 *
 * DRIFT notes honored (CR-SAN-019):
 *   DRIFT-1: install.sh must be current before running (binary on PATH must have --version).
 *   DRIFT-2: version assertion is loose (/^sandesh \S+/), NOT semver.
 *   DRIFT-3: real spawning pi.exec drives captured tools' execute().
 *   DRIFT-4: valid '<Orch> - <Project>' addresses with project part == project_id.
 *   DRIFT-5: guard is skipIf; skip branch is not mechanically asserted.
 */

import { test, expect, describe, beforeAll, afterAll, mock } from "bun:test";
import { spawnSync, spawn } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import type { ExtensionAPI, ExecResult, ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";
import { decodeEnvelope } from "./toon";

// ---------------------------------------------------------------------------
// Binary resolution (CR-SAN-048 AC10): $SANDESH_BIN → <repo>/.venv/bin/sandesh
// → PATH `sandesh`.
// ---------------------------------------------------------------------------

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const VENV_SANDESH = path.join(REPO_ROOT, ".venv", "bin", "sandesh");

function isExecutableFile(p: string): boolean {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function resolveSmokeBinary(): string {
  const envBin = process.env.SANDESH_BIN;
  if (envBin && isExecutableFile(envBin)) return envBin;
  if (isExecutableFile(VENV_SANDESH)) return VENV_SANDESH;
  return "sandesh";
}

function probeAvailable(bin: string): boolean {
  try {
    const r = spawnSync(bin, ["--version"], { encoding: "utf-8" });
    return r.status === 0;
  } catch {
    return false;
  }
}

function probeHasFormat(bin: string): boolean {
  try {
    const r = spawnSync(bin, ["--help"], { encoding: "utf-8" });
    return (r.stdout ?? "").includes("--format");
  } catch {
    return false;
  }
}

const SMOKE_BIN = resolveSmokeBinary();
const sandeshAvailable = probeAvailable(SMOKE_BIN);
const sandeshHasFormat = sandeshAvailable && probeHasFormat(SMOKE_BIN);

// ---------------------------------------------------------------------------
// Temp stores — isolated XDG_DATA_HOME dirs so the real data home is never
// touched (CR-SAN-049 rule). Removed in the top-level afterAll.
// ---------------------------------------------------------------------------

let tmpDir: string;
let bindTmpDir: string;
let wakeTmpDir: string;

beforeAll(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-smoke-"));
  bindTmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-smoke-bin-"));
  wakeTmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-smoke-wake-"));
});

afterAll(() => {
  for (const d of [tmpDir, bindTmpDir, wakeTmpDir]) {
    if (d) fs.rmSync(d, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Shared harness bits
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

/** Call a tool's execute() — the SUT only uses params and pi.exec. */
async function callExecute(
  tool: CapturedTool,
  params: Record<string, unknown>,
): Promise<ReturnType<CapturedTool["execute"]>> {
  return tool.execute("smoke-call-id", params, undefined, undefined, {} as any);
}

/** Read `.content[0].text` off an AgentToolResult. */
function text(result: { content: unknown[] }): string {
  return (result.content[0] as { type: "text"; text: string }).text;
}

/**
 * Real synchronous spawning `pi.exec` factory. Builds an ExtensionAPI whose
 * exec() actually spawns the process with spawnSync (XDG_DATA_HOME overridden
 * to xdgDataHome) and returns an ExecResult. Every call is recorded in
 * `execCalls` (cmd + args) so tests can assert which binary was invoked.
 *
 * `remapLocalBinary`: when true, a bare `"sandesh"` cmd (what today's
 * `resolveSandesh` hardcodes for the local-binary path) is substituted with
 * the resolved smoke binary (SMOKE_BIN) before spawning — this is what makes
 * suite 2 (the plain round-trip) exercise the RESOLVED binary without
 * depending on `$SANDESH_BIN` production support (that is suite 3's own RED).
 */
function makeRealPi(
  xdgDataHome: string,
  opts?: { remapLocalBinary?: boolean },
): {
  realPi: ExtensionAPI;
  capturedTools: Map<string, CapturedTool>;
  execCalls: Array<{ cmd: string; args: string[] }>;
  sendUserMessageMock: ReturnType<typeof mock>;
} {
  const capturedTools = new Map<string, CapturedTool>();
  const execCalls: Array<{ cmd: string; args: string[] }> = [];
  const sendUserMessageMock = mock((_text: string, _opts?: unknown) => {});
  const remap = opts?.remapLocalBinary ?? false;

  const realPi = {
    registerTool: (tool: CapturedTool) => {
      capturedTools.set(tool.name, tool);
    },
    registerCommand: (_name: string, _def: unknown) => {},
    exec: async (cmd: string, args: string[], _opts?: unknown): Promise<ExecResult> => {
      execCalls.push({ cmd, args });
      const resolvedCmd = remap && cmd === "sandesh" ? SMOKE_BIN : cmd;
      const r = spawnSync(resolvedCmd, args, {
        encoding: "utf-8",
        env: {
          ...process.env,
          XDG_DATA_HOME: xdgDataHome,
        },
      });
      return {
        stdout: r.stdout ?? "",
        stderr: r.stderr ?? "",
        code: r.status ?? 1,
        killed: r.signal != null,
      };
    },
    sendUserMessage: sendUserMessageMock,
    on: (_event: string, _handler: unknown) => {},
  } as unknown as ExtensionAPI;

  return { realPi, capturedTools, execCalls, sendUserMessageMock };
}

interface AsyncExecResult extends ExecResult {
  signalCode?: string | null;
}

/**
 * Real ASYNC spawning `pi.exec` factory (child_process.spawn, not spawnSync)
 * — required for the wake end-to-end suite, where the notify child blocks
 * for the whole watcher lifetime while the test concurrently sends mail and
 * stops the watcher. Honors an AbortSignal (kills the child on abort), the
 * same contract WakeSupervisor relies on for `stop()`.
 */
function execAsyncReal(
  cmd: string,
  args: string[],
  xdgDataHome: string,
  signal?: AbortSignal,
): Promise<AsyncExecResult> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      env: { ...process.env, XDG_DATA_HOME: xdgDataHome },
      signal,
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (d: Buffer) => {
      stdout += d.toString();
    });
    child.stderr?.on("data", (d: Buffer) => {
      stderr += d.toString();
    });
    // Swallow 'error' (e.g. the abort's own ENOENT/AbortError) — 'close'
    // still fires afterwards and is the sole resolution point.
    child.on("error", () => {});
    child.on("close", (code: number | null, signalCode: NodeJS.Signals | null) => {
      resolve({
        stdout,
        stderr,
        code: code ?? 1,
        killed: signalCode != null,
        signalCode: signalCode ?? undefined,
      });
    });
  });
}

function makeAsyncRealPi(xdgDataHome: string): {
  realPi: ExtensionAPI;
  capturedTools: Map<string, CapturedTool>;
  sendUserMessageMock: ReturnType<typeof mock>;
} {
  const capturedTools = new Map<string, CapturedTool>();
  const sendUserMessageMock = mock((_text: string, _opts?: unknown) => {});

  const realPi = {
    registerTool: (tool: CapturedTool) => {
      capturedTools.set(tool.name, tool);
    },
    registerCommand: (_name: string, _def: unknown) => {},
    exec: (cmd: string, args: string[], opts?: { signal?: AbortSignal }): Promise<ExecResult> => {
      return execAsyncReal(cmd, args, xdgDataHome, opts?.signal) as unknown as Promise<ExecResult>;
    },
    sendUserMessage: sendUserMessageMock,
    on: (_event: string, _handler: unknown) => {},
  } as unknown as ExtensionAPI;

  return { realPi, capturedTools, sendUserMessageMock };
}

function getTool(tools: Map<string, CapturedTool>, name: string): CapturedTool {
  const t = tools.get(name);
  if (!t) throw new Error(`Tool "${name}" not registered`);
  return t;
}

// ---------------------------------------------------------------------------
// Suite 1: --version smoke check (DRIFT-2: loose assertion)
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable)("sandesh --version smoke (AC5)", () => {
  test("sandesh --version exits 0 and prints a parseable version string", () => {
    const r = spawnSync(SMOKE_BIN, ["--version"], { encoding: "utf-8" });
    expect(r.status).toBe(0);
    const output = (r.stdout ?? "").trim();
    expect(output).toMatch(/^sandesh \S+/);
  });
});

// ---------------------------------------------------------------------------
// Suite 2: round-trip via the shim's execute() with a real spawning pi.exec
// setup → register (Mainline + Track 1) → send → fetch (DRIFT-3, DRIFT-4)
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable || !sandeshHasFormat)(
  "sandesh shim round-trip via real pi.exec: setup→register→send→fetch (AC5)",
  () => {
    const PROJECT_ID = "Smoke";
    const MAINLINE_ADDR = "Mainline - Smoke";
    const TRACK_ADDR = "Track 1 - Smoke";
    const SUBJECT = "smoke-test-subject-unique-42";

    let fetchEnvelope: ReturnType<typeof decodeEnvelope>;

    beforeAll(async () => {
      const { realPi, capturedTools } = makeRealPi(tmpDir, { remapLocalBinary: true });
      registerExtension(realPi);

      // 1. setup — provision the project store
      const setupResult = await callExecute(getTool(capturedTools, "sandesh_setup"), {
        project_id: PROJECT_ID,
      });
      expect(decodeEnvelope(text(setupResult)).ok).toBe(true);

      // 2. register Mainline (recipient of the send)
      await callExecute(getTool(capturedTools, "sandesh_register"), {
        address: MAINLINE_ADDR,
        kind: "mainline",
        project_id: PROJECT_ID,
      });

      // 3. register Track 1 (the sender)
      await callExecute(getTool(capturedTools, "sandesh_register"), {
        address: TRACK_ADDR,
        kind: "track",
        project_id: PROJECT_ID,
      });

      // 4. send from Track 1 to Mainline with a distinctive subject
      await callExecute(getTool(capturedTools, "sandesh_send"), {
        from: TRACK_ADDR,
        to: [MAINLINE_ADDR],
        subject: SUBJECT,
        project_id: PROJECT_ID,
      });

      // 5. fetch for Mainline — this is the assertion source
      const fetchResult = await callExecute(getTool(capturedTools, "sandesh_fetch"), {
        recipient: MAINLINE_ADDR,
        project_id: PROJECT_ID,
      });
      fetchEnvelope = decodeEnvelope(text(fetchResult));
    });

    test("fetch tool result decodes to an ok AXI envelope for the fetch verb", () => {
      expect(fetchEnvelope.verb).toBe("fetch");
      expect(fetchEnvelope.ok).toBe(true);
    });

    test("fetched envelope's messages field contains exactly the sent subject", () => {
      const messages = fetchEnvelope.fields.messages as Array<Record<string, unknown>>;
      expect(messages).toHaveLength(1);
      expect(messages[0]?.subject).toBe(SUBJECT);
    });

    test("fetched envelope's messages field references the sender address", () => {
      const messages = fetchEnvelope.fields.messages as Array<Record<string, unknown>>;
      expect(messages[0]?.from).toBe(TRACK_ADDR);
    });
  },
);

// ---------------------------------------------------------------------------
// Suite 3 (CR-SAN-048 AC10): $SANDESH_BIN override.
//
// RED today: resolveSandesh() ignores $SANDESH_BIN entirely — it always
// returns the bare "sandesh" (PATH) name (or the uvx fallback). With
// $SANDESH_BIN pointed at the resolved smoke binary, pi.exec is still called
// with cmd === "sandesh" instead of the resolved path, so the first
// assertion fails; and since PATH's `sandesh` (0.3.6) doesn't understand
// `--format` (which runSandeshText always adds), the second test's call
// throws instead of returning a decodable envelope.
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable || !sandeshHasFormat)(
  "resolveSandesh honors $SANDESH_BIN (CR-SAN-048 AC10)",
  () => {
    const PROJECT_ID = "BinOverride";
    const originalSandeshBin = process.env.SANDESH_BIN;
    let capturedTools: Map<string, CapturedTool>;
    let execCalls: Array<{ cmd: string; args: string[] }>;

    beforeAll(() => {
      process.env.SANDESH_BIN = SMOKE_BIN;
      const harness = makeRealPi(bindTmpDir);
      capturedTools = harness.capturedTools;
      execCalls = harness.execCalls;
      registerExtension(harness.realPi);
      // Provision the project directly with the resolved binary (bypassing
      // the shim entirely) so `setup` itself never confounds the assertions
      // below — they are about which binary `addressbook` invokes.
      spawnSync(SMOKE_BIN, ["--project", PROJECT_ID, "setup"], {
        encoding: "utf-8",
        env: { ...process.env, XDG_DATA_HOME: bindTmpDir },
      });
    });

    afterAll(() => {
      if (originalSandeshBin === undefined) delete process.env.SANDESH_BIN;
      else process.env.SANDESH_BIN = originalSandeshBin;
    });

    test("sandesh_addressbook spawns the resolved $SANDESH_BIN binary, not bare 'sandesh'", async () => {
      const tool = getTool(capturedTools, "sandesh_addressbook");
      await callExecute(tool, { project_id: PROJECT_ID }).catch(() => undefined);
      const last = execCalls.at(-1);
      expect(last?.cmd).toBe(process.env.SANDESH_BIN);
    });

    test("sandesh_addressbook decodes a real 'addressbook' envelope from the $SANDESH_BIN binary", async () => {
      const tool = getTool(capturedTools, "sandesh_addressbook");
      const result = await callExecute(tool, { project_id: PROJECT_ID });
      const env = decodeEnvelope(text(result));
      expect(env.verb).toBe("addressbook");
    });
  },
);

// ---------------------------------------------------------------------------
// Suite 4 (CR-SAN-048 AC10): supervised wake end-to-end against the real
// binary — start → send → wake → stop, all under 30s.
//
// RED today (depends on suite 3's defect): with $SANDESH_BIN unhonored, the
// notify child spawns as plain "sandesh" (PATH 0.3.6), which rejects
// `--format toon` immediately — no blocking watcher is ever actually
// running, so no mail is ever observed and `sendUserMessage` never fires;
// the 20s poll below times out and the test fails on that account.
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable || !sandeshHasFormat)(
  "supervised wake end-to-end via the real $SANDESH_BIN binary (CR-SAN-048 AC10)",
  () => {
    const PROJECT_ID = "WakeE2E";
    const MAINLINE_ADDR = "Mainline - WakeE2E";
    const TRACK_ADDR = "Track 1 - WakeE2E";
    const originalSandeshBin = process.env.SANDESH_BIN;
    const originalPoll = process.env.SANDESH_POLL_SECONDS;
    let activeCapturedTools: Map<string, CapturedTool> | undefined;

    beforeAll(() => {
      process.env.SANDESH_BIN = SMOKE_BIN;
      process.env.SANDESH_POLL_SECONDS = "3";
    });

    afterAll(async () => {
      // Unconditional stop, even on failure — never leave a child running.
      if (activeCapturedTools) {
        const stopTool = activeCapturedTools.get("sandesh_notify_stop");
        if (stopTool) {
          await callExecute(stopTool, {}).catch(() => undefined);
        }
      }
      if (originalSandeshBin === undefined) delete process.env.SANDESH_BIN;
      else process.env.SANDESH_BIN = originalSandeshBin;
      if (originalPoll === undefined) delete process.env.SANDESH_POLL_SECONDS;
      else process.env.SANDESH_POLL_SECONDS = originalPoll;
    });

    test(
      "start → send → sendUserMessage wake → stop, end to end",
      async () => {
        const { realPi, capturedTools, sendUserMessageMock } = makeAsyncRealPi(wakeTmpDir);
        activeCapturedTools = capturedTools;
        registerExtension(realPi);

        // 1. provision + register both addresses
        const setupResult = await callExecute(getTool(capturedTools, "sandesh_setup"), {
          project_id: PROJECT_ID,
        });
        expect(decodeEnvelope(text(setupResult)).ok).toBe(true);

        await callExecute(getTool(capturedTools, "sandesh_register"), {
          address: MAINLINE_ADDR,
          kind: "mainline",
          project_id: PROJECT_ID,
        });
        await callExecute(getTool(capturedTools, "sandesh_register"), {
          address: TRACK_ADDR,
          kind: "track",
          project_id: PROJECT_ID,
        });

        // 2. start the watcher for Mainline
        const startResult = await callExecute(getTool(capturedTools, "sandesh_notify_start"), {
          address: MAINLINE_ADDR,
          project: PROJECT_ID,
        });
        const startEnv = decodeEnvelope(text(startResult));
        expect(startEnv.fields.already).toBe(false);

        // 3. send from Track 1 to Mainline
        const sendResult = await callExecute(getTool(capturedTools, "sandesh_send"), {
          from: TRACK_ADDR,
          to: [MAINLINE_ADDR],
          subject: "wake-e2e-ping",
          project_id: PROJECT_ID,
        });
        const sendEnv = decodeEnvelope(text(sendResult));
        const sentId = sendEnv.fields.id as number;
        expect(typeof sentId).toBe("number");

        // 4. await the wake (poll up to 20s — the CLI's own poll floor is 3s)
        const wakeDeadline = Date.now() + 20_000;
        while (sendUserMessageMock.mock.calls.length === 0 && Date.now() < wakeDeadline) {
          await new Promise((r) => setTimeout(r, 250));
        }
        expect(sendUserMessageMock.mock.calls.length).toBeGreaterThan(0);
        const [wakeText, wakeOpts] = sendUserMessageMock.mock.calls[0] as [
          string,
          { deliverAs: string },
        ];
        expect(wakeText).toContain(String(sentId));
        expect(wakeOpts.deliverAs).toBe("followUp");

        // 5. status shows the watcher running
        const statusResult = await callExecute(getTool(capturedTools, "sandesh_notify_status"), {});
        const statusEnv = decodeEnvelope(text(statusResult));
        const watchers = statusEnv.fields.watchers as Array<{ address: string; running: boolean }>;
        expect(watchers.find((w) => w.address === MAINLINE_ADDR)?.running).toBe(true);

        // 6. stop it — stopped:1
        const stopResult = await callExecute(getTool(capturedTools, "sandesh_notify_stop"), {
          address: MAINLINE_ADDR,
        });
        const stopEnv = decodeEnvelope(text(stopResult));
        expect(stopEnv.fields.stopped).toBe(1);

        // 7. within 5s the child is gone — the CLI's own addressbook agrees
        const stopDeadline = Date.now() + 5_000;
        let mainlineListening = true;
        while (Date.now() < stopDeadline) {
          const check = spawnSync(
            SMOKE_BIN,
            ["--project", PROJECT_ID, "--format", "toon", "addressbook"],
            { encoding: "utf-8", env: { ...process.env, XDG_DATA_HOME: wakeTmpDir } },
          );
          const checkEnv = decodeEnvelope(check.stdout ?? "");
          const participants = checkEnv.fields.participants as Array<{
            address: string;
            listening: boolean;
          }>;
          mainlineListening = participants.find((p) => p.address === MAINLINE_ADDR)?.listening ?? true;
          if (!mainlineListening) break;
          await new Promise((r) => setTimeout(r, 250));
        }
        expect(mainlineListening).toBe(false);
      },
      30_000,
    );
  },
);
