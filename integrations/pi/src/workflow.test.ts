/**
 * CR-SAN-021 Cycle C — RED: publish-npm.yml workflow assertions (AC1 + AC4 Arm B)
 *
 * Asserts structural properties of .github/workflows/publish-npm.yml via
 * Bun.YAML.parse (available in Bun 1.3.14).
 *
 * Path anchor (DRIFT-3 convention): resolved from import.meta.dir (integrations/pi/src)
 * up three levels to the repo root, then into .github/workflows/.
 *
 * ALL tests are expected RED now — the workflow file does not exist.
 * The file-exists test fails first; the remainder are structured to fail
 * with informative messages rather than throw, where possible.
 *
 * Properties asserted (AC1 + AC4 Arm B):
 *   1.  File exists.
 *   2.  on.release.types includes "published".
 *   3.  on.workflow_dispatch is present.
 *   4.  on.pull_request is present.
 *   5.  on.push.branches includes "develop".
 *   6.  A job with if: referencing github.event_name == 'release' (release-gated publish).
 *   7.  Publish job declares environment: npm.
 *   8.  Publish job declares permissions.id-token: write.
 *   9.  NPM_TOKEN does NOT appear anywhere in the file (OIDC only — no secret-based auth).
 *  10.  A step running "npm install -g npm@latest" (npm ≥ 11.5.1 upgrade before publish).
 *  11.  A step running "npm publish" with --access public from working-directory: integrations/pi.
 *  12.  oven-sh/setup-bun action present (bun runtime).
 *  13.  actions/setup-node action present with registry-url (node+npm runtime).
 *  14.  tsc --noEmit appears (build-check step).
 *  15.  bun test appears (build-check step).
 *  16.  npm pack --dry-run appears (build-check step).
 *  17.  workflow_dispatch path includes a dry-run step (npm publish --dry-run or npm pack).
 *  18.  AC4 Arm B: a step references github.ref_name (or github.event.release.tag_name)
 *       in a version-equality check touching package.json and server.json.
 */

import { test, expect, describe, beforeAll } from "bun:test";
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

// ---------------------------------------------------------------------------
// Path resolution (DRIFT-3 convention)
// ---------------------------------------------------------------------------

const PKG_DIR = resolve(import.meta.dir, "..");                                    // integrations/pi
const WF_PATH = resolve(PKG_DIR, "..", "..", ".github", "workflows", "publish-npm.yml");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type YamlDoc = Record<string, any>;

let wfText = "";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let wf: YamlDoc = {};
let fileExists = false;

beforeAll(() => {
  fileExists = existsSync(WF_PATH);
  if (fileExists) {
    wfText = readFileSync(WF_PATH, "utf-8");
    wf = Bun.YAML.parse(wfText) as YamlDoc;
  }
});

// ---------------------------------------------------------------------------
// Helper: find all jobs in the workflow
// ---------------------------------------------------------------------------
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function allJobs(): Array<[string, any]> {
  if (!wf.jobs) return [];
  return Object.entries(wf.jobs as Record<string, unknown>);
}

// Find the publish job — the one with an `if` that references a release event
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function findPublishJob(): [string, any] | undefined {
  return allJobs().find(([, job]) => {
    const ifExpr: string = (job as { if?: string }).if ?? "";
    return ifExpr.includes("release") || ifExpr.includes("github.event_name");
  });
}

// Collect all step `run` strings across the entire workflow
function allStepRuns(): string[] {
  if (!wf.jobs) return [];
  const runs: string[] = [];
  for (const [, job] of allJobs()) {
    const steps: Array<{ run?: string; uses?: string }> = (job as { steps?: Array<{ run?: string; uses?: string }> }).steps ?? [];
    for (const step of steps) {
      if (step.run) runs.push(step.run);
    }
  }
  return runs;
}

// Collect all step `uses` values across the entire workflow
function allStepUses(): string[] {
  if (!wf.jobs) return [];
  const uses: string[] = [];
  for (const [, job] of allJobs()) {
    const steps: Array<{ run?: string; uses?: string }> = (job as { steps?: Array<{ run?: string; uses?: string }> }).steps ?? [];
    for (const step of steps) {
      if (step.uses) uses.push(step.uses);
    }
  }
  return uses;
}

// ---------------------------------------------------------------------------
// §1 — File existence (the primary RED gate)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — file existence (AC1)", () => {
  test("workflow file exists at .github/workflows/publish-npm.yml", () => {
    expect(
      fileExists,
      `Expected ${WF_PATH} to exist — create the publish-npm.yml workflow file`,
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §2 — Trigger assertions (AC1)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — triggers (AC1)", () => {
  test('on.release.types includes "published"', () => {
    expect(fileExists).toBe(true);
    const types: string[] = wf?.on?.release?.types ?? [];
    expect(types).toContain("published");
  });

  test("on.workflow_dispatch is present", () => {
    expect(fileExists).toBe(true);
    expect(wf?.on).toHaveProperty("workflow_dispatch");
  });

  test("on.pull_request is present", () => {
    expect(fileExists).toBe(true);
    expect(wf?.on).toHaveProperty("pull_request");
  });

  test('on.push.branches includes "develop"', () => {
    expect(fileExists).toBe(true);
    const branches: string[] = wf?.on?.push?.branches ?? [];
    expect(branches).toContain("develop");
  });
});

// ---------------------------------------------------------------------------
// §3 — Publish job: release-gated + OIDC (AC1)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — publish job: release-gated + OIDC (AC1)", () => {
  test("a job with if: referencing github.event_name == 'release' exists (release-gated publish)", () => {
    expect(fileExists).toBe(true);
    const releaseJob = allJobs().find(([, job]) => {
      const ifExpr: string = (job as { if?: string }).if ?? "";
      return ifExpr.includes("github.event_name") && ifExpr.includes("release");
    });
    expect(
      releaseJob,
      "Expected a job with `if: github.event_name == 'release'` (or equivalent) for the publish gate",
    ).toBeDefined();
  });

  test("publish job declares environment: npm", () => {
    expect(fileExists).toBe(true);
    const publishEntry = findPublishJob();
    expect(publishEntry).toBeDefined();
    const [, job] = publishEntry!;
    const env: string | { name?: string } = job.environment ?? "";
    const envName = typeof env === "string" ? env : (env as { name?: string }).name ?? "";
    expect(
      envName,
      "Publish job must declare `environment: npm` for OIDC trusted publishing",
    ).toBe("npm");
  });

  test("publish job declares permissions.id-token: write", () => {
    expect(fileExists).toBe(true);
    const publishEntry = findPublishJob();
    expect(publishEntry).toBeDefined();
    const [, job] = publishEntry!;
    const perms: Record<string, string> = (job as { permissions?: Record<string, string> }).permissions ?? {};
    expect(
      perms["id-token"],
      "Publish job must declare `permissions: id-token: write` for OIDC",
    ).toBe("write");
  });

  test("NPM_TOKEN does NOT appear anywhere in the file (OIDC only — no secret-based auth)", () => {
    expect(fileExists).toBe(true);
    expect(
      wfText.includes("NPM_TOKEN"),
      "NPM_TOKEN must NOT appear in the workflow — use OIDC trusted publishing only",
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §4 — npm upgrade + publish steps (AC1)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — npm upgrade + publish steps (AC1)", () => {
  test('a step runs "npm install -g npm@latest" (npm ≥ 11.5.1 required for OIDC)', () => {
    expect(fileExists).toBe(true);
    const runs = allStepRuns();
    const found = runs.some((r) => r.includes("npm install -g npm@latest"));
    expect(
      found,
      "Expected a step `run: npm install -g npm@latest` before publish (npm ≥ 11.5.1 for OIDC)",
    ).toBe(true);
  });

  test('publish step runs "npm publish" with --access public from working-directory: integrations/pi', () => {
    expect(fileExists).toBe(true);
    // Check the text contains 'npm publish' and '--access public' and working-directory: integrations/pi
    const hasPublish = wfText.includes("npm publish") && wfText.includes("--access public");
    const hasWorkingDir = wfText.includes("working-directory: integrations/pi") ||
      wfText.includes("working-directory: './integrations/pi'") ||
      wfText.includes('working-directory: "integrations/pi"');
    expect(
      hasPublish,
      "Expected `npm publish --access public` in the workflow",
    ).toBe(true);
    expect(
      hasWorkingDir,
      "Expected `working-directory: integrations/pi` in the workflow (publish must run from the package dir)",
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §5 — Runtimes: both bun and node must be set up (AC1)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — runtimes: bun + node (AC1)", () => {
  test("oven-sh/setup-bun action is present (bun runtime for bun test + prepublishOnly)", () => {
    expect(fileExists).toBe(true);
    const uses = allStepUses();
    const found = uses.some((u) => u.startsWith("oven-sh/setup-bun"));
    expect(
      found,
      "Expected a step `uses: oven-sh/setup-bun` to set up bun",
    ).toBe(true);
  });

  test("actions/setup-node action with registry-url is present (node+npm runtime)", () => {
    expect(fileExists).toBe(true);
    // Check for setup-node in uses AND registry-url in the file text
    const uses = allStepUses();
    const hasSetupNode = uses.some((u) => u.startsWith("actions/setup-node"));
    const hasRegistryUrl = wfText.includes("registry-url");
    expect(
      hasSetupNode,
      "Expected a step `uses: actions/setup-node` to set up node+npm",
    ).toBe(true);
    expect(
      hasRegistryUrl,
      "Expected `registry-url` in actions/setup-node config (required for npm auth context)",
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §6 — Build-check steps (AC1): tsc, bun test, npm pack --dry-run
// ---------------------------------------------------------------------------

describe("publish-npm.yml — build-check steps (AC1)", () => {
  test("tsc --noEmit appears (type-check step)", () => {
    expect(fileExists).toBe(true);
    const found = allStepRuns().some((r) => r.includes("tsc --noEmit"));
    expect(
      found,
      "Expected a step containing `tsc --noEmit` for type-checking",
    ).toBe(true);
  });

  test("bun test appears (test step)", () => {
    expect(fileExists).toBe(true);
    const found = allStepRuns().some((r) => r.includes("bun test"));
    expect(
      found,
      "Expected a step containing `bun test`",
    ).toBe(true);
  });

  test("npm pack --dry-run appears (pack check step)", () => {
    expect(fileExists).toBe(true);
    const found = allStepRuns().some((r) => r.includes("npm pack --dry-run") || r.includes("npm pack"));
    expect(
      found,
      "Expected a step containing `npm pack --dry-run`",
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §7 — workflow_dispatch dry-run path (AC1)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — workflow_dispatch dry-run path (AC1)", () => {
  test("a dry-run step exists (npm publish --dry-run or npm pack) gated to workflow_dispatch", () => {
    expect(fileExists).toBe(true);
    // The dry-run step may be in a separate job gated to workflow_dispatch
    // or in a step with a conditional. We assert the text contains the pattern.
    const hasDryRunStep = wfText.includes("npm publish --dry-run") || (
      wfText.includes("npm pack") && wfText.includes("workflow_dispatch")
    );
    expect(
      hasDryRunStep,
      "Expected a dry-run path (npm publish --dry-run or npm pack) reachable via workflow_dispatch",
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §8 — AC4 Arm B: release-tag version-equality check (AC4)
// ---------------------------------------------------------------------------

describe("publish-npm.yml — AC4 Arm B: release-tag version check (AC4)", () => {
  test("a step references github.ref_name (or github.event.release.tag_name) for version resolution", () => {
    expect(fileExists).toBe(true);
    const hasRefName =
      wfText.includes("github.ref_name") ||
      wfText.includes("github.event.release.tag_name");
    expect(
      hasRefName,
      "Expected a step referencing `github.ref_name` or `github.event.release.tag_name` to resolve the release tag version",
    ).toBe(true);
  });

  test("the version-check step touches package.json", () => {
    expect(fileExists).toBe(true);
    // Find a run step that references both ref_name/tag_name AND package.json
    const runs = allStepRuns();
    const found = runs.some(
      (r) =>
        (r.includes("ref_name") || r.includes("tag_name")) &&
        r.includes("package.json"),
    );
    // Also accept the pattern in the raw text (e.g. multi-line shell with env vars)
    const foundInText =
      wfText.includes("package.json") &&
      (wfText.includes("ref_name") || wfText.includes("tag_name"));
    expect(
      found || foundInText,
      "Expected the version-check step to reference package.json alongside the release tag",
    ).toBe(true);
  });

  test("the version-check step touches server.json", () => {
    expect(fileExists).toBe(true);
    const runs = allStepRuns();
    const found = runs.some(
      (r) =>
        (r.includes("ref_name") || r.includes("tag_name")) &&
        r.includes("server.json"),
    );
    const foundInText =
      wfText.includes("server.json") &&
      (wfText.includes("ref_name") || wfText.includes("tag_name"));
    expect(
      found || foundInText,
      "Expected the version-check step to reference server.json alongside the release tag",
    ).toBe(true);
  });

  test("the version-check step reads server.json from the repo root (not integrations/pi/server.json)", () => {
    expect(fileExists).toBe(true);
    // server.json lives ONLY at the repo root. GitHub Actions `run:` steps execute
    // from GITHUB_WORKSPACE (the repo root) by default, so the read MUST be
    // `require('./server.json')`, NOT `require('./integrations/pi/server.json')`.
    expect(
      wfText.includes("require('./server.json')") ||
        wfText.includes('require("./server.json")'),
      "Expected the Arm B step to read server.json from the repo root: require('./server.json')",
    ).toBe(true);
    expect(
      wfText.includes("integrations/pi/server.json"),
      "Arm B must NOT reference integrations/pi/server.json — that file does not exist (server.json is at the repo root)",
    ).toBe(false);
  });

  test("the version-check step reads package.json from integrations/pi", () => {
    expect(fileExists).toBe(true);
    // package.json DOES live under integrations/pi, so the package.json read stays
    // `require('./integrations/pi/package.json')`.
    expect(
      wfText.includes("require('./integrations/pi/package.json')") ||
        wfText.includes('require("./integrations/pi/package.json")'),
      "Expected the Arm B step to read package.json from integrations/pi: require('./integrations/pi/package.json')",
    ).toBe(true);
  });
});
