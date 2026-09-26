/**
 * CR-SAN-048 §S1 — RED: `src/toon.ts` decoder wrapper (D5, settled).
 *
 * `decodeEnvelope(text: string): AxiEnvelope` decodes an AXI/TOON envelope
 * (PRD-axi-toon.md §4.1) into a typed shape:
 *   { verb, ok, context, warnings, help?, error?, fields }
 * where `fields` carries everything under `axi:` other than
 * verb/ok/context/help/warnings/error.
 *
 * Fixtures below are copied VERBATIM from the dispatch prompt — the exact
 * TOON text the 0.4.0 CLI emits for `addressbook`, `notify` (final envelope)
 * and a failed `send`.
 *
 * RED reason: `./toon` does not exist yet, and `@toon-format/toon` is not a
 * dependency (not installed) — this file fails to even resolve its imports,
 * which is valid RED (Mode 1: not-yet-existing SUT symbol / unresolved
 * module).
 */

import { test, expect, describe } from "bun:test";
import { decodeEnvelope, type AxiEnvelope } from "./toon";
import { decode } from "@toon-format/toon";

// ─── Fixtures (verbatim 0.4.0 CLI TOON output) ──────────────────────────────

const ADDRESSBOOK_TEXT = `axi:
  verb: addressbook
  ok: true
  participants[2]{address,listening}:
    Mainline - Demo,false
    Track 1 - Demo,true
  listening: 1/2
  context:
    project: Demo
  help[2]: "sandesh --project Demo send --from <addr> --to <addr> --subject \\"<subject>\\"",sandesh --project Demo notify --to <addr>
  warnings: []
`;

const NOTIFY_TEXT = `axi:
  verb: notify
  ok: true
  exit: 0
  address: Mainline - Demo
  project: Demo
  unread[2]: 12,13
  context:
    project: Demo
    address: Mainline - Demo
  warnings: []
`;

const SEND_FAILURE_TEXT = `axi:
  verb: send
  ok: false
  error: "no recipients (after excluding the sender)"
  context:
    project: Demo
    address: Track 1 - Demo
  warnings: []
`;

// ─── addressbook fixture ─────────────────────────────────────────────────────

describe("decodeEnvelope — addressbook fixture", () => {
  test("verb/ok/context decode to their exact values", () => {
    const env: AxiEnvelope = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.verb).toBe("addressbook");
    expect(env.ok).toBe(true);
    expect(env.context).toEqual({ project: "Demo" });
  });

  test("fields.participants is an array of 2 objects with boolean listening", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.fields.participants).toEqual([
      { address: "Mainline - Demo", listening: false },
      { address: "Track 1 - Demo", listening: true },
    ]);
  });

  test("fields.listening is the aggregate string '1/2'", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.fields.listening).toBe("1/2");
  });

  test("help has exactly 2 entries and the first contains the unescaped --subject template", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.help).toHaveLength(2);
    expect(env.help?.[0]).toContain('--subject "<subject>"');
    expect(env.help?.[1]).toBe("sandesh --project Demo notify --to <addr>");
  });

  test("warnings decodes to an empty array", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.warnings).toEqual([]);
  });

  test("error is undefined on a successful envelope", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    expect(env.error).toBeUndefined();
  });
});

// ─── notify fixture (final envelope) ────────────────────────────────────────

describe("decodeEnvelope — notify (final envelope) fixture", () => {
  test("verb/ok decode to their exact values", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    expect(env.verb).toBe("notify");
    expect(env.ok).toBe(true);
  });

  test("fields.exit is the number 0", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    expect(env.fields.exit).toBe(0);
  });

  test("fields.unread deep-equals [12, 13] as numbers (the de-dup key)", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    expect(env.fields.unread).toEqual([12, 13]);
    // Bound: exactly two ids, not "at least two" — a runaway decode that
    // flattens/duplicates entries must fail this.
    expect((env.fields.unread as number[]).length).toBe(2);
  });

  test("context carries both project and address", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    expect(env.context).toEqual({ project: "Demo", address: "Mainline - Demo" });
  });

  test("warnings decodes to an empty array", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    expect(env.warnings).toEqual([]);
  });
});

// ─── send failure fixture ───────────────────────────────────────────────────

describe("decodeEnvelope — send failure fixture", () => {
  test("ok is false and error is the exact message", () => {
    const env = decodeEnvelope(SEND_FAILURE_TEXT);
    expect(env.verb).toBe("send");
    expect(env.ok).toBe(false);
    expect(env.error).toBe("no recipients (after excluding the sender)");
  });

  test("help is undefined (no help[] on this envelope)", () => {
    const env = decodeEnvelope(SEND_FAILURE_TEXT);
    expect(env.help).toBeUndefined();
  });

  test("context carries project and address; warnings is empty", () => {
    const env = decodeEnvelope(SEND_FAILURE_TEXT);
    expect(env.context).toEqual({ project: "Demo", address: "Track 1 - Demo" });
    expect(env.warnings).toEqual([]);
  });
});

// ─── non-envelope input ─────────────────────────────────────────────────────

describe("decodeEnvelope — non-envelope input rejects with a named error", () => {
  test('plain text "hello" throws "not an AXI envelope:" prefixed error', () => {
    expect(() => decodeEnvelope("hello")).toThrow(/^not an AXI envelope:/);
  });

  test('empty string "" throws "not an AXI envelope:" prefixed error', () => {
    expect(() => decodeEnvelope("")).toThrow(/^not an AXI envelope:/);
  });

  test("a TOON doc without an axi: root throws 'not an AXI envelope:' prefixed error", () => {
    const text = "foo:\n  bar: 1\n";
    expect(() => decodeEnvelope(text)).toThrow(/^not an AXI envelope:/);
  });
});

// ─── round trip via the reference @toon-format/toon library ────────────────

describe("decodeEnvelope — round-trips through the reference @toon-format/toon decoder", () => {
  test("addressbook fixture: decode(text) equals {axi: {...}} reconstructed from decodeEnvelope", () => {
    const env = decodeEnvelope(ADDRESSBOOK_TEXT);
    const expected = {
      axi: {
        verb: env.verb,
        ok: env.ok,
        ...env.fields,
        context: env.context,
        ...(env.help !== undefined ? { help: env.help } : {}),
        warnings: env.warnings,
      },
    };
    expect(decode(ADDRESSBOOK_TEXT)).toEqual(expected);
  });

  test("notify fixture: decode(text) equals {axi: {...}} reconstructed from decodeEnvelope", () => {
    const env = decodeEnvelope(NOTIFY_TEXT);
    const expected = {
      axi: {
        verb: env.verb,
        ok: env.ok,
        ...env.fields,
        context: env.context,
        warnings: env.warnings,
      },
    };
    expect(decode(NOTIFY_TEXT)).toEqual(expected);
  });

  test("send failure fixture: decode(text) equals {axi: {...}} reconstructed from decodeEnvelope", () => {
    const env = decodeEnvelope(SEND_FAILURE_TEXT);
    const expected = {
      axi: {
        verb: env.verb,
        ok: env.ok,
        error: env.error,
        context: env.context,
        warnings: env.warnings,
      },
    };
    expect(decode(SEND_FAILURE_TEXT)).toEqual(expected);
  });
});
