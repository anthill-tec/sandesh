/**
 * CR-SAN-046 §S1 — npm pack --json shape normalizer (AC1, AC2)
 *
 * `packedFilePaths` (integrations/pi/src/npm_pack.ts, written in GREEN) parses
 * `npm pack --dry-run --json` output and tolerates both known root shapes:
 *   - npm <= 11: a top-level ARRAY, one entry per packed package.
 *   - npm >= 12: a top-level OBJECT keyed by package name.
 * Anything else — or an entry missing `files` — throws an Error whose message
 * starts with "unrecognised npm pack --json shape:".
 */

import { describe, test, expect } from "bun:test";
import { packedFilePaths } from "./npm_pack";

// ── shared fixture data — identical `files` entries in both root shapes ─────

const FILES = [
  { path: "LICENSE", size: 35150, mode: 420 },
  { path: "README.md", size: 4821, mode: 420 },
  { path: "package.json", size: 1102, mode: 420 },
  { path: "src/index.ts", size: 9834, mode: 420 },
];

const EXPECTED_PATHS = ["LICENSE", "README.md", "package.json", "src/index.ts"];

// npm <= 11 shape — a top-level array, one entry per packed package.
const ARRAY_FIXTURE = [
  {
    id: "@anthill-tec/sandesh-pi@0.3.6",
    name: "@anthill-tec/sandesh-pi",
    version: "0.3.6",
    size: 12345,
    unpackedSize: 51907,
    shasum: "deadbeefcafebabe0123456789abcdef01234567",
    integrity: "sha512-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ==",
    filename: "anthill-tec-sandesh-pi-0.3.6.tgz",
    entryCount: 4,
    bundled: [],
    files: FILES,
  },
];

// npm >= 12 shape — a top-level object keyed by package name.
const KEYED_FIXTURE = {
  "@anthill-tec/sandesh-pi": {
    id: "@anthill-tec/sandesh-pi@0.3.6",
    name: "@anthill-tec/sandesh-pi",
    version: "0.3.6",
    size: 12345,
    unpackedSize: 51907,
    shasum: "deadbeefcafebabe0123456789abcdef01234567",
    integrity: "sha512-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ==",
    filename: "anthill-tec-sandesh-pi-0.3.6.tgz",
    entryCount: 4,
    bundled: [],
    files: FILES,
  },
};

// ── AC1 — both known shapes normalize to the same file-path list ───────────

describe("packedFilePaths — AC1 (npm <=11 array shape / npm >=12 keyed-object shape)", () => {
  test("npm <=11 array-root fixture yields exactly [LICENSE, README.md, package.json, src/index.ts]", () => {
    expect(packedFilePaths(JSON.stringify(ARRAY_FIXTURE))).toEqual(EXPECTED_PATHS);
  });

  test("npm >=12 keyed-object-root fixture yields exactly [LICENSE, README.md, package.json, src/index.ts]", () => {
    expect(packedFilePaths(JSON.stringify(KEYED_FIXTURE))).toEqual(EXPECTED_PATHS);
  });

  test("array shape and keyed-object shape produce identical results for the same files", () => {
    const fromArray = packedFilePaths(JSON.stringify(ARRAY_FIXTURE));
    const fromKeyed = packedFilePaths(JSON.stringify(KEYED_FIXTURE));
    expect(fromArray).toEqual(fromKeyed);
  });
});

// ── AC2 — unrecognised shapes throw with the exact message prefix ──────────

describe("packedFilePaths — AC2 (unrecognised shape guard)", () => {
  const badInputs: Array<[string, string]> = [
    ["empty array", "[]"],
    ["empty object", "{}"],
    ["null", "null"],
    ["bare number", "42"],
    ["array entry missing files", '[{"id":"x"}]'],
  ];

  for (const [label, input] of badInputs) {
    test(`throws with prefix "unrecognised npm pack --json shape:" for ${label} (${input})`, () => {
      expect(() => packedFilePaths(input)).toThrow(/^unrecognised npm pack --json shape:/);
    });
  }
});
