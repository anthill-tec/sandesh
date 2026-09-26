/**
 * CR-SAN-046 §S1 — `npm pack --json` shape normalizer (test-support only).
 *
 * `npm pack --dry-run --json` changed its root shape between npm majors:
 *   - npm <= 11 prints a top-level ARRAY, one entry per packed package.
 *   - npm >= 12 prints a top-level OBJECT keyed by package name.
 * `packedFilePaths` accepts both so `package.test.ts` (AC3) stays green on the
 * workstation (npm 12) and the CI `build-check` job (npm 10) alike. Anything
 * else — or an entry without an array `files` — is rejected loudly rather than
 * surfacing as an opaque TypeError deep in a `beforeAll`.
 *
 * NOT shipped in the tarball: `package.json` `files` whitelists only
 * `src/index.ts`, `README.md` and `LICENSE`.
 */

export interface NpmPackFile {
  path: string;
  size: number;
  mode: number;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Extract the packed file paths from `npm pack --dry-run --json` stdout.
 *
 * @throws Error with the message prefix `unrecognised npm pack --json shape:`
 *   (followed by the first 200 characters of `stdout`) when the input is not
 *   JSON, the root is not an array / plain object, is empty, or its first
 *   entry lacks an array `files`.
 */
export function packedFilePaths(stdout: string): string[] {
  const fail = (): never => {
    throw new Error("unrecognised npm pack --json shape: " + stdout.slice(0, 200));
  };

  let root: unknown;
  try {
    root = JSON.parse(stdout);
  } catch {
    return fail();
  }

  let entry: unknown;
  if (Array.isArray(root)) {
    entry = root[0];
  } else if (isPlainObject(root)) {
    entry = Object.values(root)[0];
  } else {
    return fail();
  }

  if (!isPlainObject(entry) || !Array.isArray(entry.files)) {
    return fail();
  }

  return entry.files.map((f: unknown): string => {
    if (!isPlainObject(f) || typeof f.path !== "string") {
      return fail();
    }
    return f.path;
  });
}
