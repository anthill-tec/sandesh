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
  ExtensionAPI,
  ExtensionContext,
  SessionStartEvent,
} from "@earendil-works/pi-coding-agent";

/**
 * Install-options notice surfaced when the `sandesh` CLI is not reachable.
 * Names the CLI and at least one install option (uv tool install / pipx /
 * install.sh) and mentions PATH (§S4 / AC7).
 */
const MISSING_CLI_NOTICE =
  "sandesh CLI not found on PATH. Install it with `uv tool install sandesh` or " +
  "`pipx install sandesh` (or run the repo's install.sh), then ensure it is on your PATH.";

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
 * Shell out to the `sandesh` CLI and map the result to an AgentToolResult:
 *   - exit 0   → success result carrying stdout,
 *   - non-zero → error result surfacing the verb, exit code, and stderr.
 */
async function runSandesh(
  pi: ExtensionAPI,
  verb: string,
  args: string[],
  signal?: AbortSignal,
): Promise<AgentToolResult<undefined>> {
  const r = await pi.exec("sandesh", args, { signal });
  if (r.code === 0) {
    return { content: [{ type: "text", text: r.stdout }], details: undefined };
  }
  return {
    content: [
      {
        type: "text",
        text: `sandesh ${verb} failed (exit ${r.code}): ${r.stderr}`,
      },
    ],
    details: undefined,
  };
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

interface InboxParams {
  recipient: string;
  unread_only?: boolean;
  project_id?: string;
}

interface FetchParams {
  recipient: string;
  mark?: boolean;
  project_id?: string;
}

interface ThreadParams {
  msg_id: number;
  project_id?: string;
}

// ---------------------------------------------------------------------------
// Extension entry — register the 9 Sandesh verb tools
// ---------------------------------------------------------------------------

export default function registerExtension(pi: ExtensionAPI): void {
  // sandesh_setup — provision a project's store (idempotent).
  pi.registerTool({
    name: "sandesh_setup",
    label: "Sandesh: Setup",
    description:
      "Provision the Sandesh store for a project (idempotent). Run once before registering addresses or sending messages.",
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
      "List messages addressed to a recipient. By default shows unread only; set unread_only=false to include everything.",
    parameters: Type.Object({
      recipient: Type.String({ description: "Address whose inbox to list." }),
      unread_only: Type.Optional(
        Type.Boolean({
          description: "When true (default) show only unread; false shows all messages.",
        }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: InboxParams, signal) => {
      const args = [...projectPrefix(params.project_id), "inbox", "--to", params.recipient];
      if (params.unread_only === false) args.push("--all");
      return runSandesh(pi, "inbox", args, signal);
    },
  });

  // sandesh_fetch — fetch (and mark read) a recipient's messages.
  pi.registerTool({
    name: "sandesh_fetch",
    label: "Sandesh: Fetch",
    description:
      "Fetch the messages addressed to a recipient, marking them read. Set mark=false to peek without marking.",
    parameters: Type.Object({
      recipient: Type.String({ description: "Address whose messages to fetch." }),
      mark: Type.Optional(
        Type.Boolean({
          description: "When true (default) mark fetched messages read; false peeks without marking.",
        }),
      ),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: FetchParams, signal) => {
      const args = [...projectPrefix(params.project_id), "fetch", "--to", params.recipient];
      if (params.mark === false) args.push("--peek");
      return runSandesh(pi, "fetch", args, signal);
    },
  });

  // sandesh_thread — walk a message's reply chain.
  pi.registerTool({
    name: "sandesh_thread",
    label: "Sandesh: Thread",
    description: "Walk the reply chain of a message, showing the full conversation thread.",
    parameters: Type.Object({
      msg_id: Type.Number({ description: "Id of a message in the thread to walk." }),
      project_id: projectIdParam,
    }),
    execute: async (_callId, params: ThreadParams, signal) => {
      const args = [...projectPrefix(params.project_id), "thread", "--id", String(params.msg_id)];
      return runSandesh(pi, "thread", args, signal);
    },
  });

  // Missing-CLI prerequisite probe (AC7). On session start, probe
  // `sandesh --version`; if the CLI is unreachable (exec rejects or non-zero
  // code), surface a one-time install notice via ctx.ui.notify. The probe never
  // blocks tool registration and never throws.
  pi.on("session_start", async (_event: SessionStartEvent, ctx: ExtensionContext): Promise<void> => {
    try {
      const r = await pi.exec("sandesh", ["--version"]);
      if (r.code !== 0) {
        ctx.ui.notify(MISSING_CLI_NOTICE, "warning");
      }
    } catch {
      ctx.ui.notify(MISSING_CLI_NOTICE, "warning");
    }
  });
}
