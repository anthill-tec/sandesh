/**
 * CR-SAN-051 §S1 — RED: `integrations/pi/src/identity.ts` unit tests (AC1, AC2).
 *
 * `identity.ts` does not exist yet — this import fails to resolve, which is
 * the expected RED for this file (a compile/import error from a
 * not-yet-existing SUT symbol counts as RED per the sub-agent procedure).
 *
 * Design: docs/changes/CR-SAN-051-pi-env-identity-nudge.md §S1;
 * docs/research/PRD-axi-toon.md §4.6 (v1.2 unexported-identity nudge).
 */

import { test, expect, describe } from "bun:test";
import { IDENTITY_KEYS, unexportedIdentityKeys, unexportedIdentityNotice } from "./identity";

// ─── AC2 fixture: the exact §S1 notice string, copied byte-for-byte from the
// spec's template literal (keys.join(", ") substituted for both keys). ────────
const EXPECTED_NOTICE_BOTH_KEYS =
  "Sandesh identity found in ./.env but not exported: SANDESH_ADDRESS, SANDESH_PROJECT. " +
  "Load it at the shell (direnv: an .envrc containing `dotenv`, then `direnv allow`) and restart pi — " +
  "until then there is no ambient status and no wake.";

describe("IDENTITY_KEYS", () => {
  test("is exactly [SANDESH_ADDRESS, SANDESH_PROJECT] in that order", () => {
    expect(IDENTITY_KEYS).toEqual(["SANDESH_ADDRESS", "SANDESH_PROJECT"]);
  });
});

describe("unexportedIdentityKeys (§S1, AC1)", () => {
  const ENV_TEXT_BOTH = "SANDESH_PROJECT=Demo\nSANDESH_ADDRESS=Mainline - Demo\n";

  test("both keys assigned in envText, env empty -> both keys, in IDENTITY_KEYS order", () => {
    expect(unexportedIdentityKeys(ENV_TEXT_BOTH, {})).toEqual(["SANDESH_ADDRESS", "SANDESH_PROJECT"]);
  });

  test("env.SANDESH_ADDRESS already set -> only SANDESH_PROJECT is reported", () => {
    expect(unexportedIdentityKeys(ENV_TEXT_BOTH, { SANDESH_ADDRESS: "Mainline - Demo" })).toEqual([
      "SANDESH_PROJECT",
    ]);
  });

  test("both keys already set in env -> empty array", () => {
    expect(
      unexportedIdentityKeys(ENV_TEXT_BOTH, {
        SANDESH_ADDRESS: "Mainline - Demo",
        SANDESH_PROJECT: "Demo",
      }),
    ).toEqual([]);
  });

  test("env.SANDESH_PROJECT set to the empty string counts as unset -> reported", () => {
    expect(
      unexportedIdentityKeys(ENV_TEXT_BOTH, {
        SANDESH_ADDRESS: "Mainline - Demo",
        SANDESH_PROJECT: "",
      }),
    ).toEqual(["SANDESH_PROJECT"]);
  });

  test('"export SANDESH_ADDRESS=x" counts as an assignment', () => {
    expect(unexportedIdentityKeys("export SANDESH_ADDRESS=x", {})).toEqual(["SANDESH_ADDRESS"]);
  });

  test('a commented-out line ("# SANDESH_ADDRESS=x") does NOT count as an assignment', () => {
    expect(unexportedIdentityKeys("# SANDESH_ADDRESS=x", {})).toEqual([]);
  });

  test('a prefixed key name ("XSANDESH_ADDRESS=x") does NOT count', () => {
    expect(unexportedIdentityKeys("XSANDESH_ADDRESS=x", {})).toEqual([]);
  });

  test('a suffixed key name ("SANDESH_ADDRESSES=x") does NOT count', () => {
    expect(unexportedIdentityKeys("SANDESH_ADDRESSES=x", {})).toEqual([]);
  });

  test('empty envText ("") -> empty array', () => {
    expect(unexportedIdentityKeys("", {})).toEqual([]);
  });

  test("the value is never read/parsed/returned: a bogus, unparseable value still counts only as 'assigned'", () => {
    // Regression guard against accidentally returning key=value pairs instead
    // of bare key names, or rejecting malformed values.
    const keys = unexportedIdentityKeys("SANDESH_ADDRESS=***not a real address***\n", {});
    expect(keys).toEqual(["SANDESH_ADDRESS"]);
  });
});

describe("unexportedIdentityNotice (§S1, AC2)", () => {
  test("equals the §S1 template string byte-for-byte for both keys", () => {
    expect(unexportedIdentityNotice(["SANDESH_ADDRESS", "SANDESH_PROJECT"])).toBe(EXPECTED_NOTICE_BOTH_KEYS);
  });

  test("contains ./.env, direnv allow, and both key names", () => {
    const notice = unexportedIdentityNotice(["SANDESH_ADDRESS", "SANDESH_PROJECT"]);
    expect(notice).toContain("./.env");
    expect(notice).toContain("direnv allow");
    expect(notice).toContain("SANDESH_ADDRESS");
    expect(notice).toContain("SANDESH_PROJECT");
  });

  test("a single-key notice joins with just that key (no stray comma)", () => {
    const notice = unexportedIdentityNotice(["SANDESH_PROJECT"]);
    expect(notice).toBe(
      "Sandesh identity found in ./.env but not exported: SANDESH_PROJECT. " +
        "Load it at the shell (direnv: an .envrc containing `dotenv`, then `direnv allow`) and restart pi — " +
        "until then there is no ambient status and no wake.",
    );
  });
});
