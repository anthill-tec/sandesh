/**
 * CR-SAN-021 Cycle B — Arm A version-sync guard (AC4 Arm A)
 *
 * Asserts that the three hand-maintained version strings are always in sync:
 *   - integrations/pi/package.json  .version
 *   - server.json                   .version
 *   - server.json                   .packages[0].version
 *
 * All three are "0.1.0" today and must stay equal at every release bump.
 * The test reads the live files (no hardcoded version) so it tracks future bumps.
 * Fails immediately if any one of the three drifts.
 *
 * Placement: integrations/pi/ bun test suite (always-on, no git tags required).
 * Arm B (release-tag agreement) is a CI step in publish-npm.yml (CR-SAN-021 §S4).
 */

import { test, expect, describe } from "bun:test";
import { readFileSync } from "fs";
import { resolve } from "path";

// ── path anchoring (mirrors src/package.test.ts) ─────────────────────────────
const PKG_DIR = resolve(import.meta.dir, "..");                  // integrations/pi
const PKG_PATH = resolve(PKG_DIR, "package.json");               // integrations/pi/package.json
const SERVER_PATH = resolve(PKG_DIR, "..", "..", "server.json"); // repo-root server.json

interface PackageJson {
  version?: string;
  [key: string]: unknown;
}

interface ServerJson {
  version?: string;
  packages?: Array<{ version?: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

const pkg: PackageJson = JSON.parse(readFileSync(PKG_PATH, "utf-8"));
const server: ServerJson = JSON.parse(readFileSync(SERVER_PATH, "utf-8"));

// ── AC4 Arm A — JSON internal consistency (always-on) ─────────────────────────

describe("version-sync guard — AC4 Arm A (package.json ↔ server.json ×2)", () => {
  test("package.json.version equals server.json.version", () => {
    expect(
      pkg.version,
      `package.json.version (${pkg.version}) must equal server.json.version (${server.version}) — bump both together`,
    ).toBe(server.version);
  });

  test("package.json.version equals server.json.packages[0].version", () => {
    const pkgEntry = server.packages?.[0]?.version;
    expect(
      pkg.version,
      `package.json.version (${pkg.version}) must equal server.json.packages[0].version (${pkgEntry}) — bump both together`,
    ).toBe(pkgEntry);
  });

  test("server.json.version equals server.json.packages[0].version (internal server.json consistency)", () => {
    const pkgEntry = server.packages?.[0]?.version;
    expect(
      server.version,
      `server.json.version (${server.version}) must equal server.json.packages[0].version (${pkgEntry}) — both fields in server.json must match`,
    ).toBe(pkgEntry);
  });
});
