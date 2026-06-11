# DN — Pi wake spike: can a Pi extension wake an idle agent?

**Status:** RESOLVED (2026-06-07) — **verdict: (a) native injection.**
**Type:** Design Note (spike result)
**Owner:** Mainline - Sandesh
**Related:** `PRD-pi-extension.md` §5 (the open crux this resolves), `PRD-mcp-server.md` §6 (the
Claude-Code wake constraint this contrasts with)
**Evidence base:** `earendil-works/pi@main` (fetched via `opensrc`), `packages/coding-agent/` —
`docs/extensions.md`, `docs/sdk.md`, `docs/rpc.md`, `src/core/extensions/types.ts`, and the
`examples/extensions/file-trigger.ts` example.

---

## The question (PRD-pi-extension §5)

> Can a Pi extension **trigger/enqueue a turn on an already-idle agent** (from a background
> watcher), or — like every other host — can it only act *within* an existing turn?

If yes → the Sandesh wake on Pi can be **native** (the extension wakes the agent itself). If no →
fall back to the Claude-Code model (an out-of-band background process; option c).

## Verdict — (a) NATIVE INJECTION

**A Pi extension can wake an idle agent from a background watcher.** This is a first-class,
documented capability — Pi is explicitly designed to be driven by external events, unlike Claude
Code (where re-invoking a sleeping agent is exclusive to the host's `run_in_background`).

### Evidence

1. **`ExtensionAPI.sendUserMessage(content, options?)`** — from `src/core/extensions/types.ts`:
   ```ts
   /** Send a user message to the agent. Always triggers a turn.
    *  When the agent is streaming, use deliverAs to specify how to queue the message. */
   sendUserMessage(
     content: string | (TextContent | ImageContent)[],
     options?: { deliverAs?: "steer" | "followUp" },
   ): void;
   ```
   "**Always triggers a turn**" — i.e. it starts an LLM turn even when the agent is idle.

2. **`ExtensionAPI.sendMessage(message, options?)`** — same file:
   ```ts
   sendMessage<T = unknown>(
     message: Pick<CustomMessage<T>, "customType" | "content" | "display" | "details">,
     options?: { triggerTurn?: boolean; deliverAs?: "steer" | "followUp" | "nextTurn" },
   ): void;
   ```
   `triggerTurn: true` → "If agent is idle, trigger an LLM response immediately" (`docs/extensions.md`).

3. **`ExtensionAPI.exec(command, args, options?): Promise<ExecResult>`** — shell out to the
   installed `sandesh` CLI (the verbs *and* the `notify` watcher).

4. **Canonical example `examples/extensions/file-trigger.ts`** — *exactly* the watcher→wake shape:
   ```ts
   export default function (pi: ExtensionAPI) {
     pi.on("session_start", async (_event, ctx) => {
       const triggerFile = "/tmp/agent-trigger.txt";
       fs.watch(triggerFile, () => {
         const content = fs.readFileSync(triggerFile, "utf-8").trim();
         if (content) {
           pi.sendMessage(
             { customType: "file-trigger", content: `External trigger: ${content}`, display: true },
             { triggerTurn: true },   // get the LLM to respond
           );
           fs.writeFileSync(triggerFile, "");
         }
       });
     });
   }
   ```
   A background `fs.watch` (running *outside* any turn) wakes the idle agent. `docs/extensions.md`
   lists *"External integrations (file watchers, webhooks, CI triggers)"* as a supported use case.

5. **Belt-and-suspenders (not needed, but available):** Pi's **SDK** (`createAgentSession()` →
   `session.prompt(...)`) and **RPC** mode (a `prompt` JSONL command) both inject turns
   programmatically — so even option (b) (external driver) is fully supported. But (a) is cleaner
   and sufficient.

## Why this matters — the wake stops being out-of-band

On Claude Code, Sandesh runs two channels (PRD-mcp-server §6): **MCP = verbs**, and the **wake** is
a separate `run_in_background` process, because an MCP server cannot re-invoke a sleeping agent.
**On Pi this split collapses:** one extension provides the verbs **and** owns the wake loop. No host
background-task tool, no second channel.

## Recommended wake design (for the wake CR)

**W1 — reuse `sandesh notify` as the doorbell (recommended).** The proven, liveness-aware watcher
already exists; the extension just translates its exit into a Pi turn. At `session_start`, start a
**detached async loop** (not awaited by any turn):

```ts
pi.on("session_start", async (_e, ctx) => {
  const self = process.env.SANDESH_ADDRESS!;        // this session's address
  const project = process.env.SANDESH_PROJECT!;
  (async function wakeLoop() {
    for (;;) {
      const r = await pi.exec("sandesh", ["--project", project, "notify", "--to", self]);
      if (r.code === 0) {                            // mail landed
        pi.sendUserMessage(
          `You have unread Sandesh mail. Call sandesh_fetch for "${self}", then act on it.`,
        );                                           // always triggers a turn
      } else if (r.code === 2) {                     // timeout → just re-arm
        continue;
      } else if (r.code === 3 || r.code === 4) {     // tombstoned/evicted → stop
        break;
      } else if (r.code === 5) {                     // a notifier was already live → stop (dedup)
        break;
      } else {                                       // error → back off, re-arm
        await sleep(backoff());
      }
    }
  })();
});
```
- The blocking `notify` is the doorbell (same exit-code contract as Claude Code: `0` mail / `2`
  timeout / `3` tombstoned / `4` evicted / `5` dedup); the **re-invocation is native** via
  `sendUserMessage` instead of `run_in_background`.
- `pi.exec` returns a `Promise<ExecResult>`; running the loop **detached** (fire-and-forget from
  `session_start`) means it never blocks a turn — it sits in the event loop, resolving when `notify`
  exits.
- Reuses Sandesh-core unchanged (PE3) — the extension stays a thin shim.

**W2 — alternatives (fallbacks, not recommended):** a `setInterval` polling `sandesh inbox` via
`exec`, or `fs.watch` on the project's `messages/` dir. Both reimplement what `notify` already does
(poll + liveness) — prefer W1.

**Wake payload — decision for the wake CR:** either (i) `sendUserMessage("…fetch and act…")` and let
the agent call the `sandesh_fetch` tool (keeps the agent in control; recommended), or (ii) pre-fetch
via `exec sandesh fetch` and inject the content with `sendMessage({…}, {triggerTurn:true})`. Lean
(i) for simplicity + auditability.

## Open implementation questions (for the wake CR, not blockers)

- **Lifecycle/cleanup:** stop the loop on `session_shutdown`; ensure only one loop per session
  (Sandesh's own notifier dedup, exit `5`, already guards cross-process duplicates).
- **`exec` longevity:** confirm a long-blocking `pi.exec` (a `notify` that blocks for its full
  timeout) is fine held in a detached promise (expected — it's just a child process; the timeout
  floor keeps it bounded). Validate in the wake CR with a real Pi session.
- **Address/project source:** `$SANDESH_ADDRESS` + `$SANDESH_PROJECT` (the extension reads them, or
  a `pi.registerCommand` / setting supplies them).
- **Steering vs new turn:** if the user is mid-turn when mail arrives, `sendUserMessage` with
  `deliverAs: "followUp"` defers it cleanly until idle (no interruption).

## Impact on PRD-pi-extension / CR breakdown

- **§5 is resolved → the wake CR can be designed now** (outcome a; design = W1).
- The PRD's "verbs can ship independently; wake is a follow-on" still holds, but the wake is no
  longer an open research risk — it's a known, native, ~one-file loop.
- Revised CR set from the PRD §6:
  1. **Pi extension scaffold + verb tools** (PE1–PE5; TS extension, `registerTool` over the CLI).
  2. **Pi native wake** (this DN → W1: the `session_start` watcher loop + `sendUserMessage`). No
     separate spike needed.
  3. **Packaging/listing** on `pi.dev/packages` (npm:/git:).

## Non-goals (unchanged)

- No change to Sandesh-core or the Python package (the extension shells the CLI).
- Not the MCP/registry path (that's for Claude Code / Cursor — CR-SAN-011).
