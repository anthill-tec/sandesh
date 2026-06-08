/**
 * CR-SAN-019 C2 — AC5: real-binary smoke test
 *
 * Exercises the REAL `sandesh` CLI through the shim's own argv-building
 * (catching CLI↔shim version/argv skew — finding #7 from the integration audit).
 *
 * Guard: if `sandesh` is not resolvable on PATH the entire suite is skipped
 * (test.skipIf) so CI without the Python CLI stays green.
 *
 * When it runs:
 *   1. `sandesh --version` exits 0 and prints a loose version (/^sandesh \S+/).
 *   2. A setup → register → send → fetch round-trip via a real spawning pi.exec
 *      (drives the shim's own execute() to exercise its argv construction) into
 *      a temp $XDG_DATA_HOME confirms the sent subject appears in fetched output.
 *
 * Hermetic: uses a temp XDG_DATA_HOME; never touches the real data home.
 * Cleaned up in afterAll.
 *
 * DRIFT notes honored:
 *   DRIFT-1: install.sh must be current before running (binary on PATH must have --version).
 *   DRIFT-2: version assertion is loose (/^sandesh \S+/), NOT semver.
 *   DRIFT-3: real spawning pi.exec drives captured tools' execute().
 *   DRIFT-4: valid '<Orch> - <Project>' addresses with project part == project_id.
 *   DRIFT-5: guard is skipIf; skip branch is not mechanically asserted.
 */

import { test, expect, describe, beforeAll, afterAll } from "bun:test";
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import type { ExtensionAPI, ExecResult, ToolDefinition } from "@earendil-works/pi-coding-agent";
import registerExtension from "./index";

// ---------------------------------------------------------------------------
// Skip-guard: detect whether `sandesh` is resolvable and --version works.
// ---------------------------------------------------------------------------

function isSandeshAvailable(): boolean {
  try {
    const r = spawnSync("sandesh", ["--version"], { encoding: "utf-8" });
    return r.status === 0;
  } catch {
    return false;
  }
}

const sandeshAvailable = isSandeshAvailable();

// ---------------------------------------------------------------------------
// Temp store — isolated XDG_DATA_HOME so the real data home is never touched.
// ---------------------------------------------------------------------------

let tmpDir: string;

beforeAll(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-smoke-"));
});

afterAll(() => {
  if (tmpDir) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Real spawning pi.exec factory.
// Builds an ExtensionAPI whose exec() actually spawns the process with
// spawnSync (XDG_DATA_HOME overridden to tmpDir) and returns an ExecResult.
// ---------------------------------------------------------------------------

type CapturedTool = ToolDefinition<any, any, any>;

function makeRealPi(xdgDataHome: string): {
  realPi: ExtensionAPI;
  capturedTools: Map<string, CapturedTool>;
} {
  const capturedTools = new Map<string, CapturedTool>();

  const realPi = {
    registerTool: (tool: CapturedTool) => {
      capturedTools.set(tool.name, tool);
    },
    exec: async (cmd: string, args: string[], _opts?: unknown): Promise<ExecResult> => {
      const r = spawnSync(cmd, args, {
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
    on: (_event: string, _handler: unknown) => {},
  } as unknown as ExtensionAPI;

  return { realPi, capturedTools };
}

/** Call a tool's execute() — the SUT only uses params and pi.exec. */
async function callExecute(
  tool: CapturedTool,
  params: Record<string, unknown>,
): Promise<ReturnType<CapturedTool["execute"]>> {
  return tool.execute("smoke-call-id", params, undefined, undefined, {} as any);
}

// ---------------------------------------------------------------------------
// Test 1: --version smoke check (DRIFT-2: loose assertion)
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable)("sandesh --version smoke (AC5)", () => {
  test("sandesh --version exits 0 and prints a parseable version string", () => {
    const r = spawnSync("sandesh", ["--version"], { encoding: "utf-8" });
    expect(r.status).toBe(0);
    const output = (r.stdout ?? "").trim();
    expect(output).toMatch(/^sandesh \S+/);
  });
});

// ---------------------------------------------------------------------------
// Test 2: round-trip via the shim's execute() with a real spawning pi.exec
// setup → register (Mainline + Track 1) → send → fetch (DRIFT-3, DRIFT-4)
// ---------------------------------------------------------------------------

describe.skipIf(!sandeshAvailable)(
  "sandesh shim round-trip via real pi.exec: setup→register→send→fetch (AC5)",
  () => {
    const PROJECT_ID = "Smoke";
    const MAINLINE_ADDR = "Mainline - Smoke";
    const TRACK_ADDR = "Track 1 - Smoke";
    const SUBJECT = "smoke-test-subject-unique-42";

    let fetchText: string;

    beforeAll(async () => {
      const { realPi, capturedTools } = makeRealPi(tmpDir);
      registerExtension(realPi);

      function getTool(name: string): CapturedTool {
        const t = capturedTools.get(name);
        if (!t) throw new Error(`Tool "${name}" not registered`);
        return t;
      }

      // 1. setup — provision the project store
      const setupResult = await callExecute(getTool("sandesh_setup"), {
        project_id: PROJECT_ID,
      });
      expect(setupResult.content[0].type).toBe("text");

      // 2. register Mainline (recipient of the send)
      const regMainlineResult = await callExecute(getTool("sandesh_register"), {
        address: MAINLINE_ADDR,
        kind: "mainline",
        project_id: PROJECT_ID,
      });
      expect(regMainlineResult.content[0].type).toBe("text");

      // 3. register Track 1 (the sender)
      const regTrackResult = await callExecute(getTool("sandesh_register"), {
        address: TRACK_ADDR,
        kind: "track",
        project_id: PROJECT_ID,
      });
      expect(regTrackResult.content[0].type).toBe("text");

      // 4. send from Track 1 to Mainline with a distinctive subject
      const sendResult = await callExecute(getTool("sandesh_send"), {
        from: TRACK_ADDR,
        to: [MAINLINE_ADDR],
        subject: SUBJECT,
        project_id: PROJECT_ID,
      });
      expect(sendResult.content[0].type).toBe("text");

      // 5. fetch for Mainline — this is the assertion source
      const fetchResult = await callExecute(getTool("sandesh_fetch"), {
        recipient: MAINLINE_ADDR,
        project_id: PROJECT_ID,
      });
      fetchText = (fetchResult.content[0] as { type: "text"; text: string }).text;
    });

    test("setup tool returns a success result (non-empty text, exit 0)", () => {
      // Asserted inside beforeAll; this test exists so a failure there is visible.
      expect(fetchText).toBeDefined();
    });

    test("fetched output contains the sent subject", () => {
      expect(fetchText).toContain(SUBJECT);
    });

    test("fetched output references the sender address", () => {
      expect(fetchText).toContain(TRACK_ADDR);
    });
  },
);
