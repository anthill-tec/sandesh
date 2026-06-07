/**
 * Sandesh — Pi extension (CR-SAN-013)
 *
 * A thin TypeScript shim that registers the Sandesh messaging verbs as native
 * Pi tools. Each tool delegates to the installed `sandesh` CLI via `pi.exec(...)`.
 * Sandesh-core stays pure Python — this shim never imports messaging logic.
 *
 * C0 scope: the registration surface only (9 tools, TypeBox parameter schemas).
 * The CLI argv construction + AgentToolResult mapping land in C1.
 */

import { Type } from "typebox";
import { StringEnum } from "@earendil-works/pi-ai";
import type { AgentToolResult, ExtensionAPI } from "@earendil-works/pi-coding-agent";

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
 * C0 stub: minimal AgentToolResult satisfying the type. C1 replaces each body
 * with the real `pi.exec("sandesh", argv, { signal })` invocation + output
 * mapping (zero code → stdout text; non-zero → stderr error result).
 */
const stubExecute = async (): Promise<AgentToolResult<undefined>> => ({
  content: [{ type: "text", text: "" }],
  details: undefined,
});

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
    execute: stubExecute,
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
    execute: stubExecute,
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
    execute: stubExecute,
  });

  // sandesh_addressbook — list registered addresses.
  pi.registerTool({
    name: "sandesh_addressbook",
    label: "Sandesh: Addressbook",
    description: "List the active addresses registered in the project's addressbook.",
    parameters: Type.Object({
      project_id: projectIdParam,
    }),
    execute: stubExecute,
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
    execute: stubExecute,
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
    execute: stubExecute,
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
    execute: stubExecute,
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
    execute: stubExecute,
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
    execute: stubExecute,
  });
}
