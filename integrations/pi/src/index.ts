/**
 * Sandesh — Pi extension (CR-SAN-013)
 *
 * A thin TypeScript shim that registers the Sandesh messaging verbs as native
 * Pi tools. Each tool delegates to the installed `sandesh` CLI via `pi.exec(...)`.
 * Sandesh-core stays pure Python — this shim never imports messaging logic.
 *
 * C0 scope: the registration surface (TypeBox parameter schemas) — originally
 * 9 tools; CR-SAN-032 extended it to 12 (archive/unarchive/search).
 * C1 scope: each tool's execute() builds the `sandesh` CLI argv per the mapping
 * table, shells out via pi.exec, and maps the result to an AgentToolResult
 * (zero code → stdout text; non-zero → an error result surfacing stderr).
 * CR-SAN-048: the 12 verb tools return AXI/TOON envelopes; `sandesh_status`
 * (home view) and `sandesh_notify_start/status/stop` (supervised wake) join
 * them — 16 tools — plus the `/sandesh-watcher` command.
 *
 * Binary resolution: the production path is unchanged — `sandesh` on PATH,
 * else `uvx --from sandesh-relay[migrate] sandesh` (CR-SAN-038). A non-empty
 * `$SANDESH_BIN` is a dev/test override (CR-SAN-048 AC10): every spawn — the
 * verbs, the `--version` probe and the notify wake — uses that exact binary,
 * with no uvx fallback.
 */

import { Type } from "typebox";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { encode } from "@toon-format/toon";
import { StringEnum } from "@earendil-works/pi-ai";
import type {
  AgentToolResult,
  ExtensionAPI,
  ExtensionContext,
  SessionShutdownEvent,
  SessionStartEvent,
} from "@earendil-works/pi-coding-agent";
import { decodeEnvelope } from "./toon";
import { unexportedIdentityKeys, unexportedIdentityNotice } from "./identity";
import { WakeSupervisor, type WatcherStatus } from "./wake";

// ---------------------------------------------------------------------------
// --- wake supervision (CR-SAN-048) ---
// ---------------------------------------------------------------------------

/**
 * The latest `ctx.ui` seen by a session handler / tool / command. `ctx` only
 * exists inside those, so the supervisor's `notify` dep routes through this
 * holder and no-ops until one has been captured.
 */
let latestUi: ExtensionContext["ui"] | undefined;

/** The supervisor of the current registration (one per `registerExtension`). */
let supervisor: WakeSupervisor | undefined;

/**
 * Test seam (also called at the top of {@link registerExtension}): stop every
 * watcher of the current supervisor and reset the resolved binary choice so a
 * fresh `session_start` re-probes.
 */
export function resetExtensionState(): void {
  supervisor?.stop();
  resetBinaryResolution();
}

/** Wire a supervisor to this extension's real host effects (§S3 deps). */
function makeSupervisor(pi: ExtensionAPI): WakeSupervisor {
  return new WakeSupervisor({
    exec: (cmd, args, { signal }) => pi.exec(cmd, args, { signal }),
    sendUserMessage: (text, opts) => pi.sendUserMessage(text, opts),
    notify: (text, level) => latestUi?.notify(text, level),
    now: Date.now,
    sleep: (ms) => new Promise<void>((res) => setTimeout(res, ms)),
    resolve: resolveSandesh,
  });
}

/** Error returned (as a result) when `sandesh_notify_start` cannot resolve its identity. */
const NOTIFY_START_UNRESOLVED =
  "address and project are unresolved: pass them or set $SANDESH_ADDRESS and $SANDESH_PROJECT";

/** Arming warning (§S5): `SANDESH_AUTOSTART=1` but the identity vars are not both set. */
const AUTOSTART_ENV_NOTICE =
  "SANDESH_AUTOSTART=1 but $SANDESH_ADDRESS and $SANDESH_PROJECT are not both set — " +
  "the Sandesh wake was not armed.";

/** Arming info (§S5): the wake is tool-started unless `SANDESH_AUTOSTART=1`. */
const TOOL_STARTED_NOTICE =
  "Sandesh wake is tool-started: call sandesh_notify_start (or set SANDESH_AUTOSTART=1)";

/**
 * Unexported-identity nudge (CR-SAN-051 §S1): warn once when `<ctx.cwd>/.env`
 * assigns an identity var that the process environment lacks. Read-only — never
 * writes `process.env`, reads no other file, and never throws.
 */
function nudgeUnexportedIdentity(ctx: ExtensionContext): void {
  let envText: string;
  try {
    envText = readFileSync(join(ctx.cwd, ".env"), "utf-8");
  } catch {
    // No/unreadable `.env` (ENOENT, EISDIR, missing cwd) is the normal case — nothing to nudge about.
    return;
  }
  const keys = unexportedIdentityKeys(envText, process.env);
  if (keys.length > 0) ctx.ui.notify(unexportedIdentityNotice(keys), "warning");
}

/**
 * Install-options notice surfaced when the `sandesh` CLI is not reachable.
 * Names the CLI and at least one install option (uv tool install / pipx /
 * install.sh) and mentions PATH (§S4 / AC7).
 */
const MISSING_CLI_NOTICE =
  "sandesh CLI not found on PATH. Install it with `uv tool install sandesh` or " +
  "`pipx install sandesh` (or run the repo's install.sh), then ensure it is on your PATH.";

/**
 * Minimum `sandesh` CLI version this extension requires (CR-SAN-032 §S3 / AC3).
 */
const MIN_CLI_VERSION: readonly [number, number, number] = [0, 4, 0];

/**
 * Outdated-CLI notice (CR-SAN-032 §S3 / AC3). Surfaced once when the probe's
 * `sandesh --version` output parses below the required minimum (or is
 * unparseable). Names the required minimum and an upgrade hint.
 */
const OUTDATED_CLI_NOTICE =
  "sandesh CLI is too old for this extension: version 0.4.0 or newer is required. " +
  "Upgrade it with `uv tool install sandesh` or `pipx upgrade sandesh` " +
  "(or re-run the repo's install.sh).";

/**
 * Provision-nudge prefix (CR-SAN-038 §S2). Prepended to the CLI's own
 * `init --check` message; names `sandesh init` so the user knows the next step.
 */
const PROVISION_NUDGE_PREFIX =
  "Sandesh store not provisioned — run `sandesh init` to set it up:";

// ---------------------------------------------------------------------------
// uvx-on-demand binary resolution (CR-SAN-038 §S1)
// ---------------------------------------------------------------------------

/**
 * The `uvx` prefix args used when a local `sandesh` is not on PATH. The CLI is
 * invoked as `uvx --from sandesh-relay[migrate] sandesh …` — the extra carries
 * `migrate` (NOT `mcp`): the Pi surface mirrors the MCP tool set but needs the
 * migration extra for the CLI's own provisioning, never the MCP SDK.
 */
const UVX_PREFIX: readonly string[] = ["--from", "sandesh-relay[migrate]", "sandesh"];

/**
 * Resolved CLI binary choice (CR-SAN-038 §S1). Decided once by the
 * session_start `--version` probe and reused at every exec site (verbs,
 * the probe itself, the notify wake). `false` (the default) means "use the
 * local `sandesh` binary directly"; `true` means "fall back to `uvx`".
 */
let useUvx = false;

/**
 * Test seam companion to {@link resetExtensionState}: reset the resolved binary
 * choice so a fresh session_start re-probes from the local-binary default.
 */
function resetBinaryResolution(): void {
  useUvx = false;
}

/**
 * The `$SANDESH_BIN` dev/test override (CR-SAN-048 AC10), or `undefined` when
 * unset/empty. Read per call so tests can set it after module load.
 */
function explicitBinary(): string | undefined {
  const bin = process.env.SANDESH_BIN;
  return bin && bin.length > 0 ? bin : undefined;
}

/**
 * Given the desired `sandesh` argv, return the actual `(command, args)` for
 * `pi.exec`. `$SANDESH_BIN` set → `(SANDESH_BIN, args)` (no uvx fallback);
 * local sandesh → `("sandesh", args)`; uvx fallback →
 * `("uvx", ["--from", "sandesh-relay[migrate]", "sandesh", ...args])`.
 */
function resolveSandesh(args: string[]): [string, string[]] {
  const bin = explicitBinary();
  if (bin !== undefined) return [bin, args];
  if (useUvx) return ["uvx", [...UVX_PREFIX, ...args]];
  return ["sandesh", args];
}

/** Outcome of the session_start `--version` probe. */
interface ProbeResult {
  reachable: boolean;
  stdout: string;
}

/**
 * Resolve the CLI binary (§S1): probe the local `sandesh --version`; on an
 * exec rejection OR a non-zero exit, set {@link useUvx} and re-probe via
 * `uvx --from sandesh-relay[migrate] sandesh --version`. The CLI is considered
 * unreachable only when BOTH the local and the uvx probes fail. The resolved
 * choice persists in `useUvx` for every later exec site. With `$SANDESH_BIN`
 * set, only that binary is probed — an explicit override never falls back.
 */
async function probeVersion(pi: ExtensionAPI): Promise<ProbeResult> {
  const bin = explicitBinary();
  try {
    const r = await pi.exec(bin ?? "sandesh", ["--version"]);
    if (r.code === 0) return { reachable: true, stdout: r.stdout };
  } catch {
    // Local binary not found — fall through to the uvx retry.
  }
  if (bin !== undefined) return { reachable: false, stdout: "" };
  // Local probe failed (non-zero or rejection): fall back to uvx and re-probe.
  useUvx = true;
  try {
    const [cmd, args] = resolveSandesh(["--version"]);
    const r = await pi.exec(cmd, args);
    if (r.code === 0) return { reachable: true, stdout: r.stdout };
  } catch {
    // uvx probe also rejected — CLI is unreachable.
  }
  return { reachable: false, stdout: "" };
}

/**
 * Parse `sandesh --version` stdout against `^sandesh (\d+)\.(\d+)\.(\d+)` and
 * compare to MIN_CLI_VERSION. Unparseable output counts as too-old (§S3).
 */
function cliVersionOk(stdout: string): boolean {
  const m = /^sandesh (\d+)\.(\d+)\.(\d+)/.exec(stdout.trim());
  if (!m) return false;
  const major = Number(m[1]);
  const minor = Number(m[2]);
  const patch = Number(m[3]);
  const [minMajor, minMinor, minPatch] = MIN_CLI_VERSION;
  if (major !== minMajor) return major > minMajor;
  if (minor !== minMinor) return minor > minMinor;
  return patch >= minPatch;
}

// ---------------------------------------------------------------------------
// Shared parameter fragments
// ---------------------------------------------------------------------------

/**
 * `project_id` routes to a per-project store; falls back to `$SANDESH_PROJECT`
 * when omitted (same env contract as the CLI/MCP surface).
 */
const projectIdParam = Type.Optional(
  Type.String({
    description:
      "Project id routing to that project's store. Falls back to $SANDESH_PROJECT when omitted.",
  }),
);

/**
 * AXI knob fragments (CR-SAN-048 §S2): `fields` → `--fields a,b,c` (comma-joined),
 * `full` → `--full` (only when true). Omitted → no flag.
 */
const fieldsParam = Type.Optional(
  Type.Array(Type.String(), {
    description: "Select the columns returned (maps to --fields, comma-joined).",
  }),
);

const fullParam = Type.Optional(
  Type.Boolean({
    description: "When true, include complete message bodies (maps to --full).",
  }),
);

/**
 * Shared inbox/fetch filter fragment (CR-SAN-032 §S2). Six optional filters
 * mapping to the CLI's `--from --from-project --kind --since --until --subject`
 * flags; each is emitted only when its param is provided (omit-at-default).
 */
const messageFilterParams = {
  sender: Type.Optional(
    Type.String({
      description: "Filter: only messages from this sender address (maps to --from).",
    }),
  ),
  sender_project: Type.Optional(
    Type.String({
      description:
        "Filter: the cross-project proxy-stream filter — only messages whose sender belongs to this project (maps to --from-project).",
    }),
  ),
  kind: Type.Optional(
    StringEnum(["request", "directive", "fyi"], {
      description: "Filter: only messages of this kind (maps to --kind).",
    }),
  ),
  since: Type.Optional(
    Type.String({
      description:
        "Filter: only messages created at or after this timestamp (maps to --since). CLI-accepted formats: YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (ISO-8601 'T…Z' values are rejected by the CLI).",
    }),
  ),
  until: Type.Optional(
    Type.String({
      description:
        "Filter: only messages created at or before this timestamp (maps to --until). CLI-accepted formats: YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (ISO-8601 'T…Z' values are rejected by the CLI).",
    }),
  ),
  subject_like: Type.Optional(
    Type.String({
      description: "Filter: only messages whose subject matches this pattern (maps to --subject).",
    }),
  ),
};

// ---------------------------------------------------------------------------
// execute() helpers — argv construction + result mapping
// ---------------------------------------------------------------------------

/**
 * Resolve the `["--project", P]` prefix: explicit `project_id` wins, else the
 * `$SANDESH_PROJECT` env (read at call time), else no prefix (the CLI/its own
 * env handles it).
 */
function projectPrefix(projectId?: string): string[] {
  const project = projectId ?? process.env.SANDESH_PROJECT;
  return project ? ["--project", project] : [];
}

/**
 * Append the inbox/fetch filter flags (CR-SAN-032 §S2) to `args`, emitting
 * each CLI flag only when its param is provided.
 */
function pushMessageFilters(args: string[], params: MessageFilterParams): void {
  if (params.sender !== undefined) args.push("--from", params.sender);
  if (params.sender_project !== undefined) args.push("--from-project", params.sender_project);
  if (params.kind !== undefined) args.push("--kind", params.kind);
  if (params.since !== undefined) args.push("--since", params.since);
  if (params.until !== undefined) args.push("--until", params.until);
  if (params.subject_like !== undefined) args.push("--subject", params.subject_like);
}

/** Append `--fields a,b,c` when the AXI `fields` knob is provided (CR-SAN-048 §S2). */
function pushFields(args: string[], fields?: string[]): void {
  if (fields !== undefined) args.push("--fields", fields.join(","));
}

/** True when `stdout` decodes to an AXI envelope (CR-SAN-048 §S2 errors-as-results). */
function isEnvelope(stdout: string): boolean {
  try {
    decodeEnvelope(stdout);
    return true;
  } catch {
    return false;
  }
}

/**
 * Shell out to the `sandesh` CLI (always with the `--format toon` global
 * option ahead of the verb — CR-SAN-048 §S2) and map the result to an
 * AgentToolResult:
 *   - exit 0   → the AXI envelope on stdout, passed through untouched,
 *   - non-zero whose stdout decodes to an envelope → RETURNED as the result
 *     (errors are results: `ok:false` + `error` + `help[]`, usage errors, the
 *     unregister `tombstoned` disposition on exit 3 — CR-SAN-019 §S1 — are all
 *     envelopes the agent reads, not failures),
 *   - non-zero with undecodable/empty stdout (CLI missing, crash) → throw
 *     Error(verb + exit code + stderr); Pi catches it and sets isError.
 */
async function runSandeshText(
  pi: ExtensionAPI,
  verb: string,
  args: string[],
  signal?: AbortSignal,
): Promise<string> {
  const [cmd, resolvedArgs] = resolveSandesh(["--format", "toon", ...args]);
  const r = await pi.exec(cmd, resolvedArgs, { signal });
  if (r.code !== 0 && !isEnvelope(r.stdout)) {
    throw new Error(`sandesh ${verb} failed (exit ${r.code}): ${r.stderr}`);
  }
  return r.stdout;
}

/** Wrap envelope text as the tool result. */
function textResult(text: string): AgentToolResult<undefined> {
  return { content: [{ type: "text", text }], details: undefined };
}

/** {@link runSandeshText} mapped to an AgentToolResult. */
async function runSandesh(
  pi: ExtensionAPI,
  verb: string,
  args: string[],
  signal?: AbortSignal,
): Promise<AgentToolResult<undefined>> {
  return textResult(await runSandeshText(pi, verb, args, signal));
}

// ---------------------------------------------------------------------------
// In-extension envelopes (CR-SAN-048 §S4) — PRD §4.1 shape
// ---------------------------------------------------------------------------

/** The `context:` block of an in-extension envelope (only defined keys emitted). */
interface EnvelopeContext {
  project?: string;
  address?: string;
}

/**
 * Encode `{axi:{verb, ok, ...fields, context, warnings:[]}}` as TOON text.
 * `fields` may carry `ok:false` + `error` to build an error result.
 */
function envelopeText(verb: string, fields: Record<string, unknown>, context: EnvelopeContext = {}): string {
  const ctx: Record<string, string> = {};
  if (context.project !== undefined) ctx.project = context.project;
  if (context.address !== undefined) ctx.address = context.address;
  return encode({ axi: { verb, ok: true, ...fields, context: ctx, warnings: [] } });
}

function notifyContextProject(sup: WakeSupervisor, address?: string): string | undefined {
  const watchers = sup.status();
  if (address !== undefined) {
    return watchers.find((w) => w.address === address)?.project || process.env.SANDESH_PROJECT || undefined;
  }
  if (watchers.length === 0) return process.env.SANDESH_PROJECT || undefined;
  const project = watchers[0].project;
  if (watchers.some((watcher) => watcher.project !== project)) return undefined;
  return project || process.env.SANDESH_PROJECT || undefined;
}

/**
 * The `WatcherStatus` columns selectable via the notify tools' `fields` knob
 * (CR-SAN-050 §S5), in the canonical order; the default row keeps the
 * three-column shape.
 */
const WATCHER_FIELDS = [
  "address",
  "project",
  "running",
  "pid",
  "startedAt",
  "lastExit",
  "lastIds",
  "timeoutExits",
] as const;
type WatcherField = (typeof WATCHER_FIELDS)[number];
const DEFAULT_WATCHER_FIELDS: readonly WatcherField[] = ["address", "running", "lastExit"];

const watcherFieldsParam = Type.Optional(
  Type.Array(Type.String(), {
    description:
      `Select the watcher columns returned, in the given order (valid: ${WATCHER_FIELDS.join(", ")}; ` +
      `default: ${DEFAULT_WATCHER_FIELDS.join(", ")}).`,
  }),
);

function isWatcherField(name: string): name is WatcherField {
  return (WATCHER_FIELDS as readonly string[]).includes(name);
}

/**
 * Resolve the `fields` knob to the column list (caller's order; the default
 * when omitted), or the error text naming the unknown name(s) and the valid set.
 */
function resolveWatcherFields(
  fields: string[] | undefined,
): { ok: true; fields: readonly WatcherField[] } | { ok: false; error: string } {
  if (fields === undefined) return { ok: true, fields: DEFAULT_WATCHER_FIELDS };
  const unknown = fields.filter((f) => !isWatcherField(f));
  if (unknown.length > 0) {
    return { ok: false, error: `unknown field(s) ${unknown.join(", ")} — valid: ${WATCHER_FIELDS.join(", ")}` };
  }
  return { ok: true, fields: fields.filter(isWatcherField) };
}

/** One `watchers[]` row with the chosen columns; an absent `pid` renders `null`. */
function watcherRow(w: WatcherStatus, fields: readonly WatcherField[]): Record<string, unknown> {
  const row: Record<string, unknown> = {};
  for (const f of fields) row[f] = w[f] ?? null;
  return row;
}

/** One human line per watcher (the `/sandesh-watcher status` view). */
function watcherLines(watchers: WatcherStatus[]): string {
  if (watchers.length === 0) return "Sandesh watchers: none";
  return watchers
    .map((w) => `${w.address}: ${w.running ? "running" : "stopped"} (last exit ${w.lastExit ?? "-"})`)
    .join("\n");
}

/**
 * `watcher: running|stopped` for the home view (§S2 P8): "running" iff the
 * watcher for `$SANDESH_ADDRESS` is running (any watcher, when the env is unset).
 */
function watcherState(sup: WakeSupervisor): "running" | "stopped" {
  const self = process.env.SANDESH_ADDRESS || undefined;
  const running = sup.status().some((w) => w.running && (self === undefined || w.address === self));
  return running ? "running" : "stopped";
}

/**
 * Insert `  watcher: <state>` inside the `axi:` block of a status envelope so
 * the text stays a valid TOON document: before `  context:` when present,
 * else right after `  ok:`.
 */
function withWatcherLine(text: string, state: "running" | "stopped"): string {
  const lines = text.split("\n");
  let at = lines.findIndex((l) => l.startsWith("  context:"));
  if (at === -1) {
    const okAt = lines.findIndex((l) => l.startsWith("  ok:"));
    at = okAt === -1 ? lines.length : okAt + 1;
  }
  lines.splice(at, 0, `  watcher: ${state}`);
  return lines.join("\n");
}

/**
 * The ambient block (S2b / PRD 4.0 P7): the status envelope minus its
 * `  bin:` and `  description:` lines. Both are top-level scalars of the
 * `axi:` block (one line each), so dropping them leaves a valid TOON document
 * (at most 12 lines incl. `help[2]`). Only the exactly-2-space-indented keys
 * are removed so nested keys are never touched.
 */
function ambientBlock(text: string): string {
  return text
    .split("\n")
    .filter((l) => !/^  (bin|description): /.test(l))
    .join("\n");
}

// ---------------------------------------------------------------------------
// Per-tool parameter shapes (Static<> of the TypeBox schemas)
// ---------------------------------------------------------------------------

interface SetupParams {
  project_id?: string;
}

interface RegisterParams {
  address: string;
  kind?: string;
  name?: string;
  project_id?: string;
}

interface UnregisterParams {
  address: string;
  requester?: string;
  project_id?: string;
}

interface AddressbookParams {
  fields?: string[];
  project_id?: string;
}

interface SendParams {
  from: string;
  to: string[];
  cc?: string[];
  subject: string;
  kind?: string;
  body?: string;
  project_id?: string;
}

interface ReplyParams {
  parent_id: number;
  from?: string;
  subject?: string;
  body?: string;
  project_id?: string;
}

/** Shared inbox/fetch filter shape (CR-SAN-032 §S2). */
interface MessageFilterParams {
  sender?: string;
  sender_project?: string;
  kind?: string;
  since?: string;
  until?: string;
  subject_like?: string;
}

interface InboxParams extends MessageFilterParams {
  recipient: string;
  unread_only?: boolean;
  fields?: string[];
  limit?: number;
  project_id?: string;
}

interface FetchParams extends MessageFilterParams {
  recipient: string;
  mark?: boolean;
  full?: boolean;
  project_id?: string;
}

interface ThreadParams {
  msg_id: number;
  fields?: string[];
  full?: boolean;
  project_id?: string;
}

interface ArchiveParams {
  project_id: string;
  by: string;
  dry_run?: boolean;
  force?: boolean;
}

interface UnarchiveParams {
  project_id: string;
  by: string;
  dry_run?: boolean;
}

interface SearchParams {
  recipient: string;
  query: string;
  limit?: number;
  offset?: number;
  sender_project?: string;
}

interface NotifyStartParams {
  address?: string;
  project?: string;
  fields?: string[];
}

interface NotifyStatusParams {
  fields?: string[];
}

interface NotifyStopParams {
  address?: string;
}

// ---------------------------------------------------------------------------
// Extension entry: the 12 verb tools + sandesh_status + 3 notify tools (16)
// ---------------------------------------------------------------------------

export default function registerExtension(pi: ExtensionAPI): void {
  // Each registration owns a fresh supervisor and re-probes the binary.
  resetExtensionState();
  const sup = makeSupervisor(pi);
  supervisor = sup;

  // sandesh_setup — provision a project's store (idempotent).
  pi.registerTool({
    name: "sandesh_setup",
    label: "Sandesh: Setup",
    description:
      "Provision the Sandesh store for a project (idempotent). Run once before registering addresses or sending messages.",
    promptSnippet:
      "Provision a project's Sandesh store (create DB + dirs); run once before anything else.",
    parameters: Type.Object({
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: SetupParams, signal) => {
      const args = [...projectPrefix(params.project_id), "setup"];
      return runSandesh(pi, "setup", args, signal);
    },
  });

  // sandesh_register — add a durable identity to the addressbook.
  pi.registerTool({
    name: "sandesh_register",
    label: "Sandesh: Register",
    description:
      "Register a durable address in the project's addressbook. Addresses follow the format '<Orchestrator> - <Project>' (e.g. 'Mainline - Demo', 'Track 1 - Demo').",
    promptSnippet:
      "Add an address to the project's addressbook (self-register on join).",
    parameters: Type.Object({
      address: Type.String({
        description: "Address to register, formatted '<Orchestrator> - <Project>'.",
      }),
      kind: Type.Optional(
        StringEnum(["mainline", "track"], {
          description: "Address role: 'mainline' (coordinator) or 'track' (worker).",
        }),
      ),
      name: Type.Optional(
        Type.String({ description: "Optional human-readable display name." }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: RegisterParams, signal) => {
      const args = [...projectPrefix(params.project_id), "register", "--address", params.address];
      if (params.kind !== undefined) args.push("--kind", params.kind);
      if (params.name !== undefined) args.push("--name", params.name);
      return runSandesh(pi, "register", args, signal);
    },
  });

  // sandesh_unregister — soft-delete an address (cooperative).
  pi.registerTool({
    name: "sandesh_unregister",
    label: "Sandesh: Unregister",
    description:
      "Remove an address from the addressbook. Mainline may unregister anyone; any address may unregister itself.",
    promptSnippet:
      "Remove an address (Mainline may remove anyone; any address may remove itself).",
    parameters: Type.Object({
      address: Type.String({ description: "Address to unregister." }),
      requester: Type.Optional(
        Type.String({
          description: "Address performing the removal (authorization check).",
        }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: UnregisterParams, signal) => {
      const args = [...projectPrefix(params.project_id), "unregister", "--address", params.address];
      if (params.requester !== undefined) args.push("--as", params.requester);
      return runSandesh(pi, "unregister", args, signal);
    },
  });

  // sandesh_addressbook — list registered addresses.
  pi.registerTool({
    name: "sandesh_addressbook",
    label: "Sandesh: Addressbook",
    description:
      "List the active addresses registered in the project's addressbook, with who is listening. " +
      "Use fields to select the columns returned.",
    promptSnippet:
      "List all participants and who is currently listening (live notifier).",
    parameters: Type.Object({
      fields: fieldsParam,
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: AddressbookParams, signal) => {
      const args = [...projectPrefix(params.project_id), "addressbook"];
      pushFields(args, params.fields);
      return runSandesh(pi, "addressbook", args, signal);
    },
  });

  // sandesh_send — send a message to one or more recipients.
  pi.registerTool({
    name: "sandesh_send",
    label: "Sandesh: Send",
    description:
      "Send a message. Recipients in 'to' wake on their next notify; recipients in 'cc' are delivered silently (Cc never wakes — it is swept up on the recipient's next fetch). Use the reserved 'all-tracks' recipient to broadcast to every active address except the sender.",
    promptSnippet:
      "Send a message to another orchestrator — To wakes the recipient, Cc is silent.",
    promptGuidelines: [
      "'to' recipients are woken on their next notify — use the To role for any recipient that must act on the message.",
      "'cc' recipients are delivered silently — Cc never wakes a watcher (awareness only); it is swept up on the recipient's next fetch.",
      "to: [\"all-tracks\"] broadcasts to every active address except the sender.",
      "'subject' is mandatory; omit a body for a subject-only message.",
    ],
    parameters: Type.Object({
      from: Type.String({ description: "Sender address." }),
      to: Type.Array(Type.String(), {
        description: "Recipients that should be woken (to-role). Wakes on next notify.",
      }),
      cc: Type.Optional(
        Type.Array(Type.String(), {
          description: "Silent recipients (cc-role). Delivered but never wakes a watcher.",
        }),
      ),
      subject: Type.String({ description: "Message subject (the minimal content)." }),
      kind: Type.Optional(
        StringEnum(["request", "directive", "fyi"], {
          description: "Message kind: 'request', 'directive', or 'fyi'.",
        }),
      ),
      body: Type.Optional(
        Type.String({
          description: "Optional message body. When omitted the message is subject-only.",
        }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: SendParams, signal) => {
      const args = [...projectPrefix(params.project_id), "send", "--from", params.from];
      args.push("--to", params.to.join(","));
      if (params.cc !== undefined) args.push("--cc", params.cc.join(","));
      args.push("--subject", params.subject);
      if (params.kind !== undefined) args.push("--kind", params.kind);
      if (params.body !== undefined) args.push("--body", params.body);
      return runSandesh(pi, "send", args, signal);
    },
  });

  // sandesh_reply — reply to a message, threading on its id.
  pi.registerTool({
    name: "sandesh_reply",
    label: "Sandesh: Reply",
    description:
      "Reply to a message, threading on the original. 'parent_id' is the original message's id; the reply defaults its recipient to the parent's sender and prefixes the subject with 'Re:'.",
    promptSnippet:
      "Reply to a message (threads under it); a recipient uses this to signal completion.",
    promptGuidelines: [
      "'parent_id' is the original message's id — the message you are replying to.",
      "Reply defaults its recipient to the parent's sender and the subject to 'Re: …'.",
      "Read ≠ done: completion is signalled by a reply (read = being acted on, reply = done), often subject-only.",
    ],
    parameters: Type.Object({
      parent_id: Type.Number({
        description: "Id of the original message being replied to.",
      }),
      from: Type.Optional(Type.String({ description: "Sender address." })),
      subject: Type.Optional(
        Type.String({ description: "Override subject (defaults to 'Re: <parent subject>')." }),
      ),
      body: Type.Optional(Type.String({ description: "Optional reply body." })),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: ReplyParams, signal) => {
      const args = [...projectPrefix(params.project_id), "reply", "--to-msg", String(params.parent_id)];
      if (params.from !== undefined) args.push("--from", params.from);
      if (params.subject !== undefined) args.push("--subject", params.subject);
      if (params.body !== undefined) args.push("--body", params.body);
      return runSandesh(pi, "reply", args, signal);
    },
  });

  // sandesh_inbox — list messages for a recipient.
  pi.registerTool({
    name: "sandesh_inbox",
    label: "Sandesh: Inbox",
    description:
      "List messages addressed to a recipient (unread only by default; unread_only=false shows all). " +
      "Filters: sender, kind, since/until, subject, or sender_project (cross-project proxy stream). " +
      "fields selects the columns returned; limit caps the number of messages.",
    promptSnippet:
      "List an address's messages without consuming them (triage; does not mark read). " +
      "Filter by sender, kind, time, subject, or sender_project (the cross-project proxy stream).",
    parameters: Type.Object({
      recipient: Type.String({ description: "Address whose inbox to list." }),
      unread_only: Type.Optional(
        Type.Boolean({
          description: "When true (default) show only unread; false shows all messages.",
        }),
      ),
      ...messageFilterParams,
      fields: fieldsParam,
      limit: Type.Optional(
        Type.Integer({
          minimum: 1,
          description: "Max number of messages to list (maps to --limit).",
        }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: InboxParams, signal) => {
      const args = [...projectPrefix(params.project_id), "inbox", "--to", params.recipient];
      if (params.unread_only === false) args.push("--all");
      pushMessageFilters(args, params);
      pushFields(args, params.fields);
      if (params.limit !== undefined) args.push("--limit", String(params.limit));
      return runSandesh(pi, "inbox", args, signal);
    },
  });

  // sandesh_fetch — fetch (and mark read) a recipient's messages.
  pi.registerTool({
    name: "sandesh_fetch",
    label: "Sandesh: Fetch",
    description:
      "Fetch the messages addressed to a recipient, marking them read (mark=false peeks without marking). " +
      "Filters: sender, kind, since/until, subject, or sender_project (cross-project proxy stream). " +
      "full=true includes complete message bodies.",
    promptSnippet:
      "Read an address's unread messages (consolidates to+cc, marks read) — call after notify wakes you. " +
      "Filter by sender, kind, time, subject, or sender_project (the cross-project proxy stream).",
    promptGuidelines: [
      "Consolidates the address's unread to+cc messages into one view and marks them read.",
      "Set mark=false (or use sandesh_inbox) to peek without consuming.",
    ],
    parameters: Type.Object({
      recipient: Type.String({ description: "Address whose messages to fetch." }),
      mark: Type.Optional(
        Type.Boolean({
          description: "When true (default) mark fetched messages read; false peeks without marking.",
        }),
      ),
      ...messageFilterParams,
      full: fullParam,
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: FetchParams, signal) => {
      const args = [...projectPrefix(params.project_id), "fetch", "--to", params.recipient];
      if (params.mark === false) args.push("--peek");
      pushMessageFilters(args, params);
      if (params.full === true) args.push("--full");
      return runSandesh(pi, "fetch", args, signal);
    },
  });

  // sandesh_thread — walk a message's reply chain.
  pi.registerTool({
    name: "sandesh_thread",
    label: "Sandesh: Thread",
    description:
      "Walk the reply chain of a message, showing the full conversation thread. " +
      "fields selects the columns returned; full=true includes complete message bodies.",
    promptSnippet:
      "Print a message's full reply chain (root → leaf) to reconstruct a conversation.",
    parameters: Type.Object({
      msg_id: Type.Number({ description: "Id of a message in the thread to walk." }),
      fields: fieldsParam,
      full: fullParam,
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: ThreadParams, signal) => {
      const args = [...projectPrefix(params.project_id), "thread", "--id", String(params.msg_id)];
      pushFields(args, params.fields);
      if (params.full === true) args.push("--full");
      return runSandesh(pi, "thread", args, signal);
    },
  });

  // sandesh_archive — archive a project (reversible lifecycle pause).
  pi.registerTool({
    name: "sandesh_archive",
    label: "Sandesh: Archive",
    description:
      "Archive a project (Mainline-tier, reversible): an archived project can no longer send or receive messages, reads stay intact, and live watchers are evicted. Reverse with sandesh_unarchive. Use dry_run to preview without writing.",
    promptSnippet:
      "Archive a project (reversible pause) — no send/receive while archived, reads intact, watchers evicted.",
    parameters: Type.Object({
      project_id: Type.String({ description: "Project id to archive." }),
      by: Type.String({
        description: "Address performing the archive (the project's own Mainline).",
      }),
      dry_run: Type.Optional(
        Type.Boolean({
          description: "When true, preview the archive (eviction counts) without writing.",
        }),
      ),
      force: Type.Optional(
        Type.Boolean({
          description: "When true, proceed even if live watchers must be evicted.",
        }),
      ),
    }),
    execute: async (_callId, params: ArchiveParams, signal) => {
      const args = ["archive", "--project", params.project_id, "--by", params.by];
      if (params.dry_run === true) args.push("--dry-run");
      if (params.force === true) args.push("--force");
      return runSandesh(pi, "archive", args, signal);
    },
  });

  // sandesh_unarchive — restore an archived project to active.
  pi.registerTool({
    name: "sandesh_unarchive",
    label: "Sandesh: Unarchive",
    description:
      "Unarchive a project (Mainline-tier, the reverse of sandesh_archive): restores an archived project to active so it can send and receive again. Reads were never blocked while archived. Use dry_run to preview without writing.",
    promptSnippet:
      "Restore an archived project to active (reverse of sandesh_archive).",
    parameters: Type.Object({
      project_id: Type.String({ description: "Project id to unarchive." }),
      by: Type.String({
        description: "Address performing the unarchive (the project's own Mainline).",
      }),
      dry_run: Type.Optional(
        Type.Boolean({
          description: "When true, preview the unarchive without writing.",
        }),
      ),
    }),
    execute: async (_callId, params: UnarchiveParams, signal) => {
      const args = ["unarchive", "--project", params.project_id, "--by", params.by];
      if (params.dry_run === true) args.push("--dry-run");
      return runSandesh(pi, "unarchive", args, signal);
    },
  });

  // sandesh_search — full-text search over the caller's own mailbox.
  pi.registerTool({
    name: "sandesh_search",
    label: "Sandesh: Search",
    description:
      "Full-text search the messages addressed to you (own-mailbox only). Query uses FTS5 syntax: quoted phrases, AND/OR/NOT. Results are bm25-ranked with snippets; paginate with limit/offset (CLI defaults: limit 20, offset 0). Never marks anything read. A lazy-reindex notice from the CLI is passed through verbatim.",
    promptSnippet:
      "Full-text search your own mailbox (FTS5 syntax; bm25-ranked snippets; paginate with limit/offset; never marks read).",
    parameters: Type.Object({
      recipient: Type.String({
        description: "Your address — search is scoped to this recipient's own mailbox.",
      }),
      query: Type.String({
        description: "FTS5 query: quoted phrases, AND/OR/NOT operators.",
      }),
      limit: Type.Optional(
        Type.Integer({
          description: "Max results per page (CLI default 20 when omitted).",
        }),
      ),
      offset: Type.Optional(
        Type.Integer({
          description: "Pagination offset (CLI default 0 when omitted).",
        }),
      ),
      sender_project: Type.Optional(
        Type.String({
          description:
            "Filter: the cross-project proxy-stream filter — only messages whose sender belongs to this project (maps to --from-project).",
        }),
      ),
    }),
    execute: async (_callId, params: SearchParams, signal) => {
      const args = ["search", params.query, "--to", params.recipient];
      if (params.limit !== undefined) args.push("--limit", String(params.limit));
      if (params.offset !== undefined) args.push("--offset", String(params.offset));
      if (params.sender_project !== undefined) args.push("--from-project", params.sender_project);
      return runSandesh(pi, "search", args, signal);
    },
  });

  // sandesh_status — the CLI home view plus the supervisor's watcher state (§S2 P8).
  pi.registerTool({
    name: "sandesh_status",
    label: "Sandesh: Status",
    description:
      "Show this session's Sandesh home view (address, listening, unread) plus watcher: running|stopped for the in-session wake watcher.",
    promptSnippet: "Home view: your address, listening state, unread count and the wake watcher state.",
    parameters: Type.Object({}),
    execute: async (_callId, _params: Record<string, never>, signal, _onUpdate, ctx) => {
      latestUi = ctx.ui;
      const home = await runSandeshText(pi, "status", ["status"], signal);
      return textResult(withWatcherLine(home, watcherState(sup)));
    },
  });

  // sandesh_notify_start — start (or report) the supervised wake watcher for an address.
  pi.registerTool({
    name: "sandesh_notify_start",
    label: "Sandesh: Notify Start",
    description:
      "Start the supervised wake watcher for an address (defaults to $SANDESH_ADDRESS / $SANDESH_PROJECT). " +
      "One watcher per address: starting an already-running address returns already:true and spawns nothing.",
    promptSnippet: "Start your wake watcher so unread mail wakes you (one per address; idempotent).",
    parameters: Type.Object({
      address: Type.Optional(
        Type.String({ description: "Address to watch. Falls back to $SANDESH_ADDRESS when omitted." }),
      ),
      project: Type.Optional(
        Type.String({ description: "Project id. Falls back to $SANDESH_PROJECT when omitted." }),
      ),
      fields: watcherFieldsParam,
    }),
    execute: async (_callId, params: NotifyStartParams, _signal, _onUpdate, ctx) => {
      latestUi = ctx.ui;
      const fields = resolveWatcherFields(params.fields);
      if (!fields.ok) {
        return textResult(envelopeText("notify_start", { ok: false, error: fields.error }));
      }
      const address = params.address ?? process.env.SANDESH_ADDRESS;
      const project = params.project ?? process.env.SANDESH_PROJECT;
      if (!address || !project) {
        return textResult(envelopeText("notify_start", { ok: false, error: NOTIFY_START_UNRESOLVED }));
      }
      const r = sup.start(address, project);
      return textResult(
        envelopeText(
          "notify_start",
          { already: r.already, watchers: sup.status().map((w) => watcherRow(w, fields.fields)) },
          { project, address },
        ),
      );
    },
  });

  // sandesh_notify_status — the supervisor's watcher table.
  pi.registerTool({
    name: "sandesh_notify_status",
    label: "Sandesh: Notify Status",
    description:
      "List the in-session wake watchers: address, running, lastExit by default; " +
      "`fields` selects any of address, project, running, pid, startedAt, lastExit, lastIds, timeoutExits.",
    promptSnippet: "List your wake watchers and whether each is running.",
    parameters: Type.Object({ fields: watcherFieldsParam }),
    execute: async (_callId, params: NotifyStatusParams, _signal, _onUpdate, ctx) => {
      latestUi = ctx.ui;
      const fields = resolveWatcherFields(params.fields);
      if (!fields.ok) {
        return textResult(envelopeText("notify_status", { ok: false, error: fields.error }));
      }
      return textResult(
        envelopeText(
          "notify_status",
          { watchers: sup.status().map((w) => watcherRow(w, fields.fields)) },
          { project: notifyContextProject(sup) },
        ),
      );
    },
  });

  // sandesh_notify_stop — stop one watcher (or all when no address is given).
  pi.registerTool({
    name: "sandesh_notify_stop",
    label: "Sandesh: Notify Stop",
    description: "Stop the wake watcher for an address, or every watcher when address is omitted.",
    promptSnippet: "Stop your wake watcher(s).",
    parameters: Type.Object({
      address: Type.Optional(Type.String({ description: "Address whose watcher to stop; omit to stop all." })),
    }),
    execute: async (_callId, params: NotifyStopParams, _signal, _onUpdate, ctx) => {
      latestUi = ctx.ui;
      // Resolve the project before stop() so the entry's project is read while
      // it is still the addressed watcher (stop keeps entries; this is just order).
      const project = notifyContextProject(sup, params.address);
      const { stopped } = sup.stop(params.address);
      return textResult(envelopeText("notify_stop", { stopped }, { project, address: params.address }));
    },
  });

  // /sandesh-watcher status | stop [address] — the user-facing view of the supervisor.
  pi.registerCommand("sandesh-watcher", {
    description: "Sandesh wake watchers: /sandesh-watcher status | stop [address]",
    handler: async (args, ctx) => {
      latestUi = ctx.ui;
      const [sub = "status", ...rest] = args.trim().split(/\s+/);
      const address = rest.length > 0 ? rest.join(" ") : undefined;
      if (sub === "stop") {
        const { stopped } = sup.stop(address);
        ctx.ui.notify(`Sandesh watchers stopped: ${stopped}`, "info");
        return;
      }
      if (sub !== "status" && sub !== "") {
        ctx.ui.notify("usage: /sandesh-watcher status | stop [address]", "warning");
        return;
      }
      ctx.ui.notify(watcherLines(sup.status()), "info");
    },
  });

  // Session start: the unexported-identity nudge (CR-SAN-051 §S1), the CLI
  // probe (AC7) + version gate + provision nudge, then the ambient home view
  // (§S2b) and the wake arming (§S5). Nothing here throws.
  pi.on("session_start", async (_event: SessionStartEvent, ctx: ExtensionContext): Promise<void> => {
    latestUi = ctx.ui;
    nudgeUnexportedIdentity(ctx);
    let probeOk = false;
    // uvx-on-demand (§S1): probe the local `sandesh` first; if it rejects or
    // exits non-zero, fall back to `uvx --from sandesh-relay[migrate] sandesh`
    // and re-probe. The resolved choice (useUvx) is stored module-wide and
    // reused by every later exec (verbs + the notify wake).
    const probe = await probeVersion(pi);
    if (probe.reachable) {
      // §S3 version gate: a CLI below MIN_CLI_VERSION (or unparseable
      // output) takes the missing-CLI-style path — one-time warning,
      // wake NOT armed. Tool registration stays static/unblocked.
      if (cliVersionOk(probe.stdout)) {
        probeOk = true;
      } else {
        ctx.ui.notify(OUTDATED_CLI_NOTICE, "warning");
      }
    } else {
      ctx.ui.notify(MISSING_CLI_NOTICE, "warning");
    }

    if (!probeOk) return;

    // Provision nudge (§S2): after a passing version check, run a read-only
    // `sandesh init --check`. Non-zero → emit a one-line nudge naming
    // `sandesh init` and surfacing the probe's own store-absent / admin-unset
    // message verbatim. Exit 0 (provisioned) → no nudge. Never throws.
    try {
      const [checkCmd, checkArgs] = resolveSandesh(["init", "--check"]);
      const check = await pi.exec(checkCmd, checkArgs);
      if (check.code !== 0) {
        const reason = (check.stderr || check.stdout).trim();
        ctx.ui.notify(`${PROVISION_NUDGE_PREFIX} ${reason}`.trim(), "warning");
      }
    } catch {
      // A probe rejection here must not break session start; skip the nudge.
    }

    const self = process.env.SANDESH_ADDRESS;
    const project = process.env.SANDESH_PROJECT;
    const identified = Boolean(self && project);

    // Ambient context (§S2b): with a known identity, inject the home view once
    // as a context message (not a user turn), minus the bin/description lines.
    // A failed/undecodable probe or an ok:false envelope injects nothing and
    // never breaks session start.
    if (identified) {
      try {
        const home = await runSandeshText(pi, "status", ["status"]);
        if (decodeEnvelope(home).ok) {
          pi.sendMessage(
            { customType: "sandesh-status", content: ambientBlock(home), display: true, details: undefined },
            { triggerTurn: false },
          );
        }
      } catch {
        // The home-view probe must not break session start; skip the injection.
      }
    }

    // Arming (§S5 / D4): only SANDESH_AUTOSTART=1 with both identity vars
    // starts the watcher — through the same start the tool uses.
    if (process.env.SANDESH_AUTOSTART === "1") {
      if (self && project) sup.start(self, project);
      else ctx.ui.notify(AUTOSTART_ENV_NOTICE, "warning");
    } else if (identified) {
      ctx.ui.notify(TOOL_STARTED_NOTICE, "info");
    }
  });

  // session_shutdown — stop every watcher (children aborted; no relaunch).
  pi.on("session_shutdown", async (_event: SessionShutdownEvent, _ctx: ExtensionContext): Promise<void> => {
    sup.stop();
  });
}
