/**
 * CR-SAN-015 C0 — RED: npm publish-readiness contract
 *
 * Asserts AC1 + AC2 + AC3:
 *   AC1: integrations/pi/package.json has all required npm publish metadata:
 *        name, version, description, keywords (includes "pi-package"), license,
 *        repository (with directory "integrations/pi"), publishConfig.access "public",
 *        files allowlist (present and non-empty).
 *   AC2: pi.extensions intact; peerDependencies for Pi-bundled packages remain "*".
 *   AC3: npm pack --dry-run --json produces a tarball that INCLUDES src/index.ts,
 *        README.md, LICENSE and EXCLUDES all *.test.ts files.
 *
 * All AC1/AC3 assertions FAIL on the current package.json (missing metadata,
 * no files allowlist) and against the current npm pack output (ships *.test.ts,
 * bun.lock, tsconfig.json; lacks README.md + LICENSE).
 *
 * AC2 assertions PASS now (green baseline — they guard against regression).
 */

import { test, expect, describe, beforeAll } from "bun:test";
import { readFileSync } from "fs";
import { resolve } from "path";
import { spawnSync } from "child_process";

// ---------------------------------------------------------------------------
// Load package.json once for all tests
// ---------------------------------------------------------------------------

const PKG_DIR = resolve(import.meta.dir, "..");
const PKG_PATH = resolve(PKG_DIR, "package.json");

interface PackageJson {
  name?: string;
  version?: string;
  description?: string;
  keywords?: string[];
  license?: string;
  repository?: {
    type?: string;
    url?: string;
    directory?: string;
  };
  publishConfig?: {
    access?: string;
  };
  files?: string[];
  pi?: {
    extensions?: string[];
  };
  peerDependencies?: Record<string, string>;
  [key: string]: unknown;
}

let pkg: PackageJson;

beforeAll(() => {
  pkg = JSON.parse(readFileSync(PKG_PATH, "utf-8")) as PackageJson;
});

// ---------------------------------------------------------------------------
// AC1 — package.json metadata assertions
// ---------------------------------------------------------------------------

describe("package.json — npm publish metadata (AC1)", () => {
  test("name is @anthill-tec/sandesh-pi", () => {
    expect(pkg.name).toBe("@anthill-tec/sandesh-pi");
  });

  test("version is present and non-empty", () => {
    expect(typeof pkg.version).toBe("string");
    expect((pkg.version ?? "").length).toBeGreaterThan(0);
  });

  test("description is present and non-empty", () => {
    expect(typeof pkg.description).toBe("string");
    expect((pkg.description ?? "").length).toBeGreaterThan(0);
  });

  test("keywords is an array", () => {
    expect(Array.isArray(pkg.keywords)).toBe(true);
  });

  test('keywords includes "pi-package" (required for pi.dev/packages gallery)', () => {
    expect(Array.isArray(pkg.keywords)).toBe(true);
    expect(pkg.keywords).toContain("pi-package");
  });

  test('license is "GPL-3.0-only"', () => {
    expect(pkg.license).toBe("GPL-3.0-only");
  });

  test("repository is present with a url", () => {
    expect(pkg.repository).toBeDefined();
    expect(typeof (pkg.repository as PackageJson["repository"])?.url).toBe("string");
    expect(((pkg.repository as PackageJson["repository"])?.url ?? "").length).toBeGreaterThan(0);
  });

  test('repository.directory is "integrations/pi"', () => {
    expect((pkg.repository as PackageJson["repository"])?.directory).toBe("integrations/pi");
  });

  test('publishConfig.access is "public"', () => {
    expect(pkg.publishConfig).toBeDefined();
    expect(pkg.publishConfig?.access).toBe("public");
  });

  test("files allowlist is present and non-empty", () => {
    expect(Array.isArray(pkg.files)).toBe(true);
    expect((pkg.files ?? []).length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// AC2 — Pi manifest + peerDependencies (baseline guards — should stay green)
// ---------------------------------------------------------------------------

describe("package.json — Pi manifest integrity (AC2)", () => {
  test('pi.extensions includes "./src/index.ts"', () => {
    expect(Array.isArray(pkg.pi?.extensions)).toBe(true);
    expect(pkg.pi?.extensions).toContain("./src/index.ts");
  });

  test('@earendil-works/pi-coding-agent peerDependency is "*"', () => {
    expect(pkg.peerDependencies?.["@earendil-works/pi-coding-agent"]).toBe("*");
  });

  test('@earendil-works/pi-ai peerDependency is "*"', () => {
    expect(pkg.peerDependencies?.["@earendil-works/pi-ai"]).toBe("*");
  });

  test('typebox peerDependency is "*"', () => {
    expect(pkg.peerDependencies?.["typebox"]).toBe("*");
  });
});

// ---------------------------------------------------------------------------
// AC3 — npm pack --dry-run contents
// ---------------------------------------------------------------------------

interface NpmPackFile {
  path: string;
  size: number;
  mode: number;
}

interface NpmPackResult {
  id: string;
  name: string;
  version: string;
  files: NpmPackFile[];
  [key: string]: unknown;
}

describe("npm pack --dry-run contents (AC3)", () => {
  let packedFiles: string[];

  beforeAll(() => {
    // Run npm pack --dry-run --json from the package directory.
    // spawnSync is synchronous — safe in beforeAll.
    const result = spawnSync("/usr/bin/npm", ["pack", "--dry-run", "--json"], {
      cwd: PKG_DIR,
      encoding: "utf-8",
    });

    if (result.status !== 0) {
      throw new Error(`npm pack failed (exit ${result.status ?? "null"}): ${result.stderr}`);
    }

    const parsed = JSON.parse(result.stdout) as NpmPackResult[];
    packedFiles = parsed[0].files.map((f) => f.path);
  });

  test("packed tarball includes src/index.ts", () => {
    expect(packedFiles).toContain("src/index.ts");
  });

  test("packed tarball includes README.md", () => {
    expect(packedFiles).toContain("README.md");
  });

  test("packed tarball includes LICENSE", () => {
    expect(packedFiles).toContain("LICENSE");
  });

  test("packed tarball EXCLUDES src/index.test.ts", () => {
    expect(packedFiles).not.toContain("src/index.test.ts");
  });

  test("packed tarball EXCLUDES src/execute.test.ts", () => {
    expect(packedFiles).not.toContain("src/execute.test.ts");
  });

  test("packed tarball EXCLUDES src/prereq.test.ts", () => {
    expect(packedFiles).not.toContain("src/prereq.test.ts");
  });

  test("packed tarball EXCLUDES src/wake.test.ts", () => {
    expect(packedFiles).not.toContain("src/wake.test.ts");
  });

  test("packed tarball EXCLUDES src/wake_lifecycle.test.ts", () => {
    expect(packedFiles).not.toContain("src/wake_lifecycle.test.ts");
  });

  test("packed tarball contains no *.test.ts files (no stray test files)", () => {
    const testFiles = packedFiles.filter((p) => p.endsWith(".test.ts"));
    expect(testFiles).toEqual([]);
  });
});
