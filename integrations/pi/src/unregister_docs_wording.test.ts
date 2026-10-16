/**
 * CR-SAN-055 §S4, AC7 — RED: integrations/pi/README.md's `sandesh_unregister`
 * tool-table row must mention that the tool stops THIS session's own watcher
 * first (§S2, already implemented in index.ts — see the CR-SAN-055 §S2
 * comment at `sandesh_unregister`'s execute path) before it calls the CLI.
 *
 * RED reason: today's row reads "remove (soft-delete) an address; tombstones
 * a live watcher first." — it never mentions the session's OWN watcher being
 * stopped (only the generic cooperative-tombstone mechanic).
 */

import { test, expect, describe } from "bun:test";
import * as fs from "fs";
import * as path from "path";

const README_PATH = path.join(__dirname, "..", "README.md");

/** Pull the `| \`sandesh_unregister\` | ... |` row out of the tool table. */
function extractUnregisterRow(markdown: string): string {
  const match = markdown.match(/\|\s*`sandesh_unregister`\s*\|([^\n]*)\|/);
  if (match === null) {
    throw new Error("README.md is missing the `sandesh_unregister` tool-table row");
  }
  return match[1];
}

describe("integrations/pi/README.md — sandesh_unregister row (CR-SAN-055 §S4, AC7)", () => {
  const readme = fs.readFileSync(README_PATH, "utf-8");
  const row = extractUnregisterRow(readme);

  test("mentions stopping the session's own watcher first", () => {
    expect(row.toLowerCase()).toMatch(/own watcher/);
  });
});
