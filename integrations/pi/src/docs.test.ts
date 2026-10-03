/**
 * CR-SAN-048 §S6 — docs pins: README.md tool table (12 → 16 tools) and
 * docs/USER_GUIDE.md §Pi (the 0.4.0 arming behaviour change).
 *
 * RED today:
 *   - README.md's tool table only lists the original 9 verb tools (setup,
 *     register, unregister, addressbook, send, reply, inbox, fetch, thread)
 *     — archive/unarchive/search (CR-SAN-032), sandesh_status, and the three
 *     notify_* tools (CR-SAN-048) are entirely absent; it never mentions
 *     SANDESH_AUTOSTART, sandesh_notify_start, or the AXI/TOON envelope
 *     result format.
 *   - docs/USER_GUIDE.md's "## For Pi extension users" section describes the
 *     OLD always-on wake loop and never mentions SANDESH_AUTOSTART,
 *     sandesh_notify_start, or the 0.4.0 release that changes the arming
 *     default.
 */

import { test, expect, describe } from "bun:test";
import * as fs from "fs";
import * as path from "path";

const README_PATH = path.join(__dirname, "..", "README.md");
const USER_GUIDE_PATH = path.join(__dirname, "..", "..", "..", "docs", "USER_GUIDE.md");

/** The extension's 16 registered tool names (index.ts `name: "sandesh_..."`). */
const TOOL_NAMES = [
  "sandesh_setup",
  "sandesh_register",
  "sandesh_unregister",
  "sandesh_addressbook",
  "sandesh_send",
  "sandesh_reply",
  "sandesh_inbox",
  "sandesh_fetch",
  "sandesh_thread",
  "sandesh_archive",
  "sandesh_unarchive",
  "sandesh_search",
  "sandesh_status",
  "sandesh_notify_start",
  "sandesh_notify_status",
  "sandesh_notify_stop",
];

/** Slice out the "## For Pi extension users" section (up to the next level-2 heading). */
function extractPiSection(markdown: string): string {
  const heading = "## For Pi extension users";
  const start = markdown.indexOf(heading);
  if (start === -1) {
    throw new Error(`USER_GUIDE.md is missing the '${heading}' section`);
  }
  const rest = markdown.slice(start + heading.length);
  const nextHeadingOffset = rest.search(/\n## /);
  return nextHeadingOffset === -1 ? rest : rest.slice(0, nextHeadingOffset);
}

describe("integrations/pi/README.md — tool table (CR-SAN-048 §S6)", () => {
  const readme = fs.readFileSync(README_PATH, "utf-8");

  test("mentions all 16 registered tool names", () => {
    const missing = TOOL_NAMES.filter((name) => !readme.includes(name));
    expect(missing).toEqual([]);
  });

  test("documents the SANDESH_AUTOSTART arming variable", () => {
    expect(readme).toContain("SANDESH_AUTOSTART");
  });

  test("documents sandesh_notify_start as the tool-started wake entry point", () => {
    expect(readme).toContain("sandesh_notify_start");
  });

  test("documents the AXI/TOON envelope result format", () => {
    expect(readme).toMatch(/--format toon|AXI/);
  });
});

describe("integrations/pi/README.md — wake supervision contract", () => {
  const readme = fs.readFileSync(README_PATH, "utf-8");
  const supervision = readme.split("Supervision rules:\n")[1]?.split("\n\nInspect")[0] ?? "";
  const bullets = supervision.split(/\n(?=- )/);
  const exit5 = bullets.find((bullet: string) => bullet.startsWith("- **Exit 5 ")) ?? "";
  const stopStart = bullets.find((bullet: string) => bullet.startsWith("- **One watcher per address:")) ?? "";

  test("documents one delayed exit-5 retry before quiet stop", () => {
    expect(exit5).toContain("one quiet retry after 30 s");
    expect(exit5).toContain("entry stays `running`");
    expect(exit5).toContain("second exit 5 in a row stops the loop quietly");
  });

  test("documents start deferral until a stopped child's exit", () => {
    expect(stopStart).toContain("spawns its child only");
    expect(stopStart).toContain("when the stopped one has exited");
  });
});

describe("docs/USER_GUIDE.md §Pi — 0.4.0 arming behaviour change (CR-SAN-048 §S6)", () => {
  const guide = fs.readFileSync(USER_GUIDE_PATH, "utf-8");
  const piSection = extractPiSection(guide);

  test("documents the SANDESH_AUTOSTART arming variable", () => {
    expect(piSection).toContain("SANDESH_AUTOSTART");
  });

  test("documents sandesh_notify_start as the tool-started wake entry point", () => {
    expect(piSection).toContain("sandesh_notify_start");
  });

  test("names the 0.4.0 release that introduces the arming behaviour change", () => {
    expect(piSection).toContain("0.4.0");
  });
});

// ============================================================================
// CR-SAN-051 §S2 — RED: identity-loading guidance (AC7)
// ============================================================================

describe("docs/USER_GUIDE.md §Pi — identity-loading guidance (CR-SAN-051 §S2, AC7)", () => {
  const guide = fs.readFileSync(USER_GUIDE_PATH, "utf-8");
  const piSection = extractPiSection(guide);

  test("tells the reader to run `direnv allow` once", () => {
    expect(piSection).toContain("direnv allow");
  });

  test("names the .envrc `dotenv` directive", () => {
    expect(piSection).toContain("dotenv");
  });

  test("gives the fish direnv hook line", () => {
    expect(piSection).toContain("direnv hook fish");
  });

  test("gives the bash direnv hook line", () => {
    expect(piSection).toContain("direnv hook bash");
  });

  test("describes the session-start warning for an unexported identity", () => {
    expect(piSection).toContain("not exported");
  });

  test("no longer describes the ambient block as '≤ 6-line' (corrected to '≤ 12-line', CR-SAN-048 amendment)", () => {
    expect(piSection).not.toContain("≤ 6-line");
  });
});

describe("integrations/pi/README.md — .env / direnv loading pointer (CR-SAN-051 §S2, AC7)", () => {
  const readme = fs.readFileSync(README_PATH, "utf-8");

  test("mentions direnv", () => {
    expect(readme).toContain("direnv");
  });

  test("mentions .env", () => {
    expect(readme).toContain(".env");
  });

  test("no longer describes the ambient block as '≤ 6-line' (corrected to '≤ 12-line', CR-SAN-048 amendment)", () => {
    expect(readme).not.toContain("≤ 6-line");
  });
});

// ============================================================================
// CR-SAN-052 §S3 — PRD-axi-toon §4.7 state-machine row for a throwing host dep (AC7)
// ============================================================================

const PRD_AXI_TOON_PATH = path.join(__dirname, "..", "..", "..", "docs", "research", "PRD-axi-toon.md");

/** Slice out the "### 4.7 Wake supervision" section (up to the next heading). */
function extractPrdSection47(markdown: string): string {
  const start = markdown.indexOf("### 4.7 ");
  if (start === -1) throw new Error("PRD-axi-toon.md is missing the '### 4.7' section");
  const rest = markdown.slice(start + 1);
  const next = rest.search(/\n#{2,3} /);
  return next === -1 ? rest : rest.slice(0, next);
}

describe("docs/research/PRD-axi-toon.md §4.7 — throwing-host-dep transition (CR-SAN-052 §S3, AC7)", () => {
  const section = extractPrdSection47(fs.readFileSync(PRD_AXI_TOON_PATH, "utf-8"));
  const rows = section
    .split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|").slice(1, -1).map((cell) => cell.trim()));

  test("the state-machine table has a row whose Event cell contains 'throws' and whose Action cell contains 'no relaunch'", () => {
    const match = rows.filter(
      (cells) => cells.length === 2 && cells[0].includes("throws") && cells[1].includes("no relaunch"),
    );
    expect(match.length).toBe(1);
  });
});

// ============================================================================
// CR-SAN-053 §AC8 — per-session wake: docs pins
// ============================================================================

describe("docs/research/PRD-axi-toon.md §4.7 — per-registration wake docs (CR-SAN-053, AC8)", () => {
  const section = extractPrdSection47(fs.readFileSync(PRD_AXI_TOON_PATH, "utf-8"));

  test("documents the wake as scoped per registration", () => {
    expect(section).toContain("per registration");
  });

  test("documents the unrequested-stop UI notify", () => {
    expect(section).toContain("unrequested");
  });
});

/** Slice out the "## Wake" section (up to the next level-2 heading). */
function extractWakeSection(markdown: string): string {
  const heading = "## Wake";
  const start = markdown.indexOf(heading);
  if (start === -1) throw new Error("README.md is missing the '## Wake' section");
  const rest = markdown.slice(start + heading.length);
  const next = rest.search(/\n## /);
  return next === -1 ? rest : rest.slice(0, next);
}

describe("integrations/pi/README.md §Wake — sub-agent wording (CR-SAN-053, AC8)", () => {
  const readme = fs.readFileSync(README_PATH, "utf-8");
  const wakeSection = extractWakeSection(readme);

  test("mentions a sub-agent session in the wake-scope explanation", () => {
    expect(wakeSection).toContain("sub-agent");
  });

  test("the sandesh_notify_start tool-table row documents its ok:false error case", () => {
    const row = readme.split("\n").find((line) => line.startsWith("| `sandesh_notify_start`"));
    expect(row).toBeDefined();
    expect(row as string).toContain("ok:false");
  });
});

