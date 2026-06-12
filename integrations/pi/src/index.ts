/**
 * Sandesh — Pi extension (CR-SAN-013)
 *
 * A thin TypeScript shim that registers the Sandesh messaging verbs as native
 * Pi tools. Each tool delegates to the installed `sandesh` CLI via `pi.exec(...)`.
 * Sandesh-core stays pure Python — this shim never imports messaging logic.
 *
 * C0 scope: the registration surface (9 tools, TypeBox parameter schemas).
 * C1 scope: each tool's execute() builds the `sandesh` CLI argv per the mapping
 * table, shells out via pi.exec, and maps the result to an AgentToolResult
 * (zero code → stdout text; non-zero → an error result surfacing stderr).
 */

import { Type } from "typebox";
import { StringEnum } from "@earendil-works/pi-ai";
import type {
  AgentToolResult,
  ExecResult,
  ExtensionAPI,
  ExtensionContext,
  SessionShutdownEvent,
  SessionStartEvent,
} from "@earendil-works/pi-coding-agent";

// ---------------------------------------------------------------------------
// Native wake loop (CR-SAN-014 C0) — backoff seam
// ---------------------------------------------------------------------------

/** Backoff cap (ms) for the wake loop's error branch (notify exit 1/other). */
const WAKE_BACKOFF_MS = 5000;

/**
 * Injectable sleep for the wake loop's error-branch backoff. Defaults to a real
 * timer; tests replace it with a no-op via {@link __setWakeSleepFn} so the
 * backoff path resolves immediately.
 */
let wakeSleepFn: () => Promise<void> = () =>
  new Promise<void>((res) => setTimeout(res, WAKE_BACKOFF_MS));

/** Test seam: inject the wake-loop backoff sleep (e.g. a no-op in tests). */
export function __setWakeSleepFn(fn: () => Promise<void>): void {
  wakeSleepFn = fn;
}

// ---------------------------------------------------------------------------
// Wake-loop lifecycle state (CR-SAN-014 C1)
// ---------------------------------------------------------------------------

/**
 * Single-loop guard: true while a wake loop is active. A second `session_start`
 * while the loop runs does NOT start a second concurrent loop.
 */
let wakeLoopRunning = false;

/**
 * Module-level AbortController for the active wake loop. `session_shutdown`
 * aborts it; its signal is threaded into every `pi.exec("sandesh", … notify …)`
 * call so a long-blocking notify is cancelled on shutdown.
 */
let wakeController: AbortController | undefined;

/**
 * Stopped flag set by `session_shutdown`. The wake loop checks it so it exits
 * and does not re-arm after a shutdown.
 */
let wakeStopped = false;

/**
 * Test seam: reset the wake-loop lifecycle state (single-loop guard, controller,
 * stopped flag) so a fresh `session_start` can start one loop. Mirrors
 * {@link __setWakeSleepFn}.
 */
export function __resetWakeState(): void {
  wakeLoopRunning = false;
  wakeController = undefined;
  wakeStopped = false;
}

/**
 * Notice surfaced when the CLI probe succeeds but this session's identity env
 * vars are unset. Names BOTH $SANDESH_ADDRESS and $SANDESH_PROJECT so the user
 * knows what to set to enable native wake (§S4 / AC4). Distinct from the
 * missing-CLI notice.
 */
const MISSING_ENV_NOTICE =
  "Sandesh native wake is disabled: $SANDESH_ADDRESS and $SANDESH_PROJECT are not both set. " +
  "Set $SANDESH_ADDRESS (this session's address) and $SANDESH_PROJECT (the project id) to enable it.";

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
const MIN_CLI_VERSION: readonly [number, number, number] = [0, 2, 0];

/**
 * Outdated-CLI notice (CR-SAN-032 §S3 / AC3). Surfaced once when the probe's
 * `sandesh --version` output parses below the required minimum (or is
 * unparseable). Names the required minimum and an upgrade hint.
 */
const OUTDATED_CLI_NOTICE =
  "sandesh CLI is too old for this extension: version 0.2.0 or newer is required. " +
  "Upgrade it with `uv tool install sandesh` or `pipx upgrade sandesh` " +
  "(or re-run the repo's install.sh).";

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

/**
 * Shell out to the `sandesh` CLI and map the result to an AgentToolResult:
 *   - exit 0   → success result carrying stdout,
 *   - non-zero → throw Error(verb + exit code + stderr); Pi catches it and
 *     sets isError on the tool result.
 */
async function runSandesh(
  pi: ExtensionAPI,
  verb: string,
  args: string[],
  signal?: AbortSignal,
): Promise<AgentToolResult<undefined>> {
  const r = await pi.exec("sandesh", args, { signal });
  // Tombstone-aware unregister (CR-SAN-019 §S1): unregister exit 3 means the
  // address's watcher was tombstoned (cooperative eviction) — a successful
  // disposition, not a failure. Scoped to unregister; every other verb's
  // exit 3 still throws via the generic guard below.
  if (verb === "unregister" && r.code === 3) {
    return { content: [{ type: "text", text: r.stdout || r.stderr }], details: undefined };
  }
  if (r.code !== 0) {
    throw new Error(`sandesh ${verb} failed (exit ${r.code}): ${r.stderr}`);
  }
  return { content: [{ type: "text", text: r.stdout }], details: undefined };
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
  project_id?: string;
}

interface FetchParams extends MessageFilterParams {
  recipient: string;
  mark?: boolean;
  project_id?: string;
}

interface ThreadParams {
  msg_id: number;
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

// ---------------------------------------------------------------------------
// Extension entry — register the 12 Sandesh verb tools
// ---------------------------------------------------------------------------

export default function registerExtension(pi: ExtensionAPI): void {
  // Each extension registration owns a fresh wake-loop lifecycle: clear the
  // single-loop guard / controller / stopped flag so this registration's first
  // session_start starts exactly one loop (the guard then persists across
  // re-entrant session_start until __resetWakeState() or the next registration).
  __resetWakeState();

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
    description: "List the active addresses registered in the project's addressbook.",
    promptSnippet:
      "List all participants and who is currently listening (live notifier).",
    parameters: Type.Object({
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: AddressbookParams, signal) => {
      const args = [...projectPrefix(params.project_id), "addressbook"];
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
      "List messages addressed to a recipient. By default shows unread only; set unread_only=false to include everything. " +
      "Optional filters narrow the list by sender, kind, time window, or subject; sender_project is the cross-project " +
      "proxy-stream filter (only messages whose sender belongs to that project).",
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
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: InboxParams, signal) => {
      const args = [...projectPrefix(params.project_id), "inbox", "--to", params.recipient];
      if (params.unread_only === false) args.push("--all");
      pushMessageFilters(args, params);
      return runSandesh(pi, "inbox", args, signal);
    },
  });

  // sandesh_fetch — fetch (and mark read) a recipient's messages.
  pi.registerTool({
    name: "sandesh_fetch",
    label: "Sandesh: Fetch",
    description:
      "Fetch the messages addressed to a recipient, marking them read. Set mark=false to peek without marking. " +
      "Optional filters narrow the fetch by sender, kind, time window, or subject; sender_project is the " +
      "cross-project proxy-stream filter (only messages whose sender belongs to that project).",
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
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: FetchParams, signal) => {
      const args = [...projectPrefix(params.project_id), "fetch", "--to", params.recipient];
      if (params.mark === false) args.push("--peek");
      pushMessageFilters(args, params);
      return runSandesh(pi, "fetch", args, signal);
    },
  });

  // sandesh_thread — walk a message's reply chain.
  pi.registerTool({
    name: "sandesh_thread",
    label: "Sandesh: Thread",
    description: "Walk the reply chain of a message, showing the full conversation thread.",
    promptSnippet:
      "Print a message's full reply chain (root → leaf) to reconstruct a conversation.",
    parameters: Type.Object({
      msg_id: Type.Number({ description: "Id of a message in the thread to walk." }),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: ThreadParams, signal) => {
      const args = [...projectPrefix(params.project_id), "thread", "--id", String(params.msg_id)];
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

  // Missing-CLI prerequisite probe (AC7) + native wake loop (CR-SAN-014 C0).
  // On session start, probe `sandesh --version`; if the CLI is unreachable
  // (exec rejects or non-zero code), surface a one-time install notice via
  // ctx.ui.notify (the probe never blocks tool registration and never throws).
  // When the probe succeeds and the session identity is known, start a detached
  // wake loop that arms `sandesh notify` and surfaces unread mail.
  pi.on("session_start", async (_event: SessionStartEvent, ctx: ExtensionContext): Promise<void> => {
    let probeOk = false;
    try {
      const r = await pi.exec("sandesh", ["--version"]);
      if (r.code === 0) {
        // §S3 version gate: a CLI below MIN_CLI_VERSION (or unparseable
        // output) takes the missing-CLI-style path — one-time warning,
        // wake loop NOT armed. Tool registration stays static/unblocked.
        if (cliVersionOk(r.stdout)) {
          probeOk = true;
        } else {
          ctx.ui.notify(OUTDATED_CLI_NOTICE, "warning");
        }
      } else {
        ctx.ui.notify(MISSING_CLI_NOTICE, "warning");
      }
    } catch {
      ctx.ui.notify(MISSING_CLI_NOTICE, "warning");
    }

    if (!probeOk) return;

    // Probe-gated wake loop: only arm when this session's identity is known.
    // When env is missing, surface a distinct notice naming both env vars
    // (AC4) and do NOT start the loop.
    const self = process.env.SANDESH_ADDRESS;
    const project = process.env.SANDESH_PROJECT;
    if (!self || !project) {
      ctx.ui.notify(MISSING_ENV_NOTICE, "warning");
      return;
    }

    // Single-loop guard: a second session_start while a loop runs must not
    // spawn a second concurrent loop.
    if (wakeLoopRunning) return;
    wakeLoopRunning = true;
    wakeStopped = false;
    wakeController = new AbortController();

    // Fire-and-forget: the detached loop owns its own lifetime; the handler
    // must not await it (it blocks on `sandesh notify`).
    void wakeLoop(pi, project, self, wakeController.signal);
  });

  // session_shutdown — stop the wake loop: abort any in-flight notify and set
  // the stopped flag so the loop exits and does not re-arm (AC5).
  pi.on("session_shutdown", async (_event: SessionShutdownEvent, _ctx: ExtensionContext): Promise<void> => {
    wakeStopped = true;
    if (wakeController) wakeController.abort();
  });
}

// ---------------------------------------------------------------------------
// Wake loop — arms `sandesh notify` and reacts to its exit code (W1 design).
// ---------------------------------------------------------------------------

/**
 * Detached wake loop. Repeatedly runs `sandesh --project <P> notify --to <self>`
 * and dispatches on the notify exit code:
 *   - 0           → unread mail: prompt the agent to fetch, then re-arm.
 *   - 2           → timeout: re-arm silently.
 *   - 3 | 4 | 5   → terminal (tombstoned / evicted / dedup): stop.
 *   - 1 | other   → error: back off (injectable sleep) then re-arm.
 */
async function wakeLoop(
  pi: ExtensionAPI,
  project: string,
  self: string,
  signal: AbortSignal,
): Promise<void> {
  let stopped = false;
  // Respect the module-level stopped flag so an abort/shutdown breaks the loop.
  while (!stopped && !wakeStopped) {
    const r: ExecResult = await pi.exec(
      "sandesh",
      ["--project", project, "notify", "--to", self],
      { signal },
    );
    // A shutdown during the awaited notify must prevent any re-arm.
    if (wakeStopped) break;
    switch (r.code) {
      case 0:
        // deliverAs "followUp": ignored when idle (immediate turn), queued when the
        // agent is mid-turn — without it Pi's prompt() throws while streaming and the
        // wake message is silently lost (CR-SAN-031 / PE11).
        try {
          pi.sendUserMessage(
            `You have unread Sandesh mail — call sandesh_fetch for "${self}", then act on it.`,
            { deliverAs: "followUp" },
          );
        } catch {
          // The real Pi wrapper is void/catching; this guards host variations where a
          // synchronous throw would otherwise kill the loop. Swallow and re-arm.
        }
        break; // re-arm
      case 2:
        break; // re-arm, no message
      case 3:
      case 4:
      case 5:
        stopped = true; // terminal — stop the loop
        break;
      default:
        await wakeSleepFn(); // backoff, then re-arm
        break;
    }
  }
  // The single-loop guard stays set for this session's lifetime once a loop has
  // started; only __resetWakeState() clears it so a fresh session_start (after a
  // reset) can start exactly one new loop (AC5f/AC5h).
}
