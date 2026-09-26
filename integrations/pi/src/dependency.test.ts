/**
 * CR-SAN-048 §S1 AC1 — RED: `@toon-format/toon` runtime dependency proof.
 *
 * AC1 (package.json + npm pack + install proof):
 *   - `dependencies["@toon-format/toon"]` === "^4.1.1"
 *   - `@toon-format/*` is NOT in `peerDependencies` (it's a real dependency,
 *     not a Pi-bundled peer like @earendil-works/* or typebox)
 *   - `npm pack --dry-run --json` lists `src/toon.ts` in the `files`
 *     whitelist (it must grow to ship the new wrapper) and still excludes
 *     every `*.test.ts`
 *   - install proof: `npm pack` the package into a temp dir, `npm install`
 *     the tarball into a throwaway prefix (temp HOME/npm_config_cache,
 *     `--ignore-scripts`, never `~/.pi`/`~/.npm`) →
 *     `node_modules/@toon-format/toon/package.json` exists there.
 *
 * RED reason: today `@toon-format/toon` is absent from `dependencies`,
 * `src/toon.ts` does not exist so it is not in the `files` whitelist / the
 * pack output, and consequently the installed tarball has no
 * `@toon-format/toon` under `node_modules` either.
 */

import { test, expect, describe, beforeAll, afterAll } from "bun:test";
import { readFileSync } from "fs";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { resolve } from "path";
import { spawnSync } from "child_process";
import { packedFilePaths } from "./npm_pack";

const PKG_DIR = resolve(import.meta.dir, "..");
const PKG_PATH = resolve(PKG_DIR, "package.json");

interface PackageJson {
  dependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
  files?: string[];
  [key: string]: unknown;
}

let pkg: PackageJson;

beforeAll(() => {
  pkg = JSON.parse(readFileSync(PKG_PATH, "utf-8")) as PackageJson;
});

// ---------------------------------------------------------------------------
// AC1 — package.json dependency declaration
// ---------------------------------------------------------------------------

describe("package.json — @toon-format/toon runtime dependency (AC1)", () => {
  test('dependencies["@toon-format/toon"] is exactly "^4.1.1"', () => {
    expect(pkg.dependencies?.["@toon-format/toon"]).toBe("^4.1.1");
  });

  test('"@toon-format/toon" is NOT declared in peerDependencies', () => {
    expect(pkg.peerDependencies?.["@toon-format/toon"]).toBeUndefined();
  });

  test("no @toon-format/* key exists anywhere in peerDependencies", () => {
    const peerKeys = Object.keys(pkg.peerDependencies ?? {});
    const toonPeerKeys = peerKeys.filter((k) => k.startsWith("@toon-format/"));
    expect(toonPeerKeys).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// AC1 — npm pack --dry-run contents (files whitelist grows)
// ---------------------------------------------------------------------------

describe("npm pack --dry-run --json — files whitelist includes src/toon.ts (AC1/AC9)", () => {
  let packedFiles: string[];

  beforeAll(() => {
    const result = spawnSync("npm", ["pack", "--dry-run", "--json"], {
      cwd: PKG_DIR,
      encoding: "utf-8",
      shell: true,
    });

    if (result.status !== 0) {
      throw new Error(`npm pack failed (exit ${result.status ?? "null"}): ${result.stderr}`);
    }

    packedFiles = packedFilePaths(result.stdout);
  });

  test("packed tarball includes src/toon.ts", () => {
    expect(packedFiles).toContain("src/toon.ts");
  });

  test("packed tarball still includes src/index.ts", () => {
    expect(packedFiles).toContain("src/index.ts");
  });

  test("packed tarball contains no *.test.ts files (no stray test files, incl. toon.test.ts)", () => {
    const testFiles = packedFiles.filter((p) => p.endsWith(".test.ts"));
    expect(testFiles).toEqual([]);
  });

  test('packed tarball specifically excludes "src/toon.test.ts"', () => {
    expect(packedFiles).not.toContain("src/toon.test.ts");
  });
});

// ---------------------------------------------------------------------------
// AC1 — install proof: npm pack the real tarball, npm install it in a
// throwaway prefix, confirm @toon-format/toon lands under node_modules.
//
// Hermetic: everything lives under a temp dir created via os.tmpdir();
// HOME and npm_config_cache are redirected into that temp dir so nothing
// touches the real ~/.npm cache config or ~/.pi. --ignore-scripts skips any
// postinstall. Cleaned up in afterAll regardless of outcome.
// ---------------------------------------------------------------------------

describe("npm pack + npm install — @toon-format/toon lands under node_modules (AC1)", () => {
  let tmpDir: string;
  let installDir: string;
  let tarballPath: string;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandesh-pi-toon-install-"));
    installDir = path.join(tmpDir, "install");
    fs.mkdirSync(installDir, { recursive: true });
  });

  afterAll(() => {
    if (tmpDir) {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test(
    "npm pack --pack-destination <tmp> produces a tarball containing @toon-format/toon after install",
    () => {
      const isolatedEnv = {
        ...process.env,
        HOME: tmpDir,
        npm_config_cache: path.join(tmpDir, "npm-cache"),
      };

      const packResult = spawnSync(
        "npm",
        ["pack", "--pack-destination", tmpDir, "--json"],
        { cwd: PKG_DIR, encoding: "utf-8", shell: true, env: isolatedEnv },
      );
      if (packResult.status !== 0) {
        throw new Error(`npm pack (install-proof) failed (exit ${packResult.status ?? "null"}): ${packResult.stderr}`);
      }
      const packJson = JSON.parse(packResult.stdout);
      const entry = Array.isArray(packJson) ? packJson[0] : Object.values(packJson)[0];
      const filename = (entry as { filename: string }).filename;
      tarballPath = path.join(tmpDir, filename);
      expect(fs.existsSync(tarballPath)).toBe(true);

      const installResult = spawnSync(
        "npm",
        [
          "install",
          "--prefix",
          installDir,
          tarballPath,
          "--no-audit",
          "--no-fund",
          "--ignore-scripts",
        ],
        { cwd: tmpDir, encoding: "utf-8", shell: true, env: isolatedEnv },
      );
      if (installResult.status !== 0) {
        throw new Error(`npm install (install-proof) failed (exit ${installResult.status ?? "null"}): ${installResult.stderr}`);
      }

      const toonPkgJsonPath = path.join(
        installDir,
        "node_modules",
        "@toon-format",
        "toon",
        "package.json",
      );
      expect(fs.existsSync(toonPkgJsonPath)).toBe(true);
    },
    60000,
  );
});
