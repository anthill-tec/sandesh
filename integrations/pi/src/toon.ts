/**
 * AXI/TOON envelope decoder (CR-SAN-048 §S1, D5 settled).
 *
 * Wraps the reference `@toon-format/toon` decoder and lifts the CLI's
 * `axi:` envelope (PRD-axi-toon.md §4.1) into a typed shape. Everything under
 * `axi:` other than the known keys lands in `fields`.
 */

import { decode } from "@toon-format/toon";

export interface AxiEnvelope {
  verb: string;
  ok: boolean;
  context: Record<string, unknown>;
  warnings: string[];
  help?: string[];
  error?: string;
  fields: Record<string, unknown>;
}

const KNOWN_KEYS = new Set(["verb", "ok", "context", "help", "warnings", "error"]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function notEnvelope(text: string): Error {
  return new Error("not an AXI envelope: " + text.slice(0, 120));
}

export function decodeEnvelope(text: string): AxiEnvelope {
  let root: unknown;
  try {
    root = decode(text);
  } catch {
    throw notEnvelope(text);
  }
  if (!isPlainObject(root) || !isPlainObject(root.axi)) throw notEnvelope(text);
  const axi = root.axi;
  const { verb, ok, context, help, warnings, error } = axi;
  if (typeof verb !== "string" || typeof ok !== "boolean") throw notEnvelope(text);

  const fields: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(axi)) {
    if (!KNOWN_KEYS.has(key)) fields[key] = value;
  }

  const env: AxiEnvelope = {
    verb,
    ok,
    context: isPlainObject(context) ? context : {},
    warnings: Array.isArray(warnings) ? (warnings as string[]) : [],
    fields,
  };
  if (Array.isArray(help)) env.help = help as string[];
  if (typeof error === "string") env.error = error;
  return env;
}
