# Sandesh — User Guide

How to actually *run* cooperating agent sessions with Sandesh. This is the
operational *how*; for installing and provisioning Sandesh see
**[docs/INSTALL.md](INSTALL.md)**.

## What Sandesh does

Sandesh lets a group of cooperating agent sessions leave each other messages —
typically one **coordinator** session and a few **worker** sessions running in
parallel. The sessions cannot talk to each other directly, so they pass notes
through a shared, durable mailbox: a coordinator drops a request, a worker reads
it, does the work, and replies. Everything is kept as history — reading a message
means "I've got it and I'm on it"; replying means "done".

The one tricky part is **waking**. An idle agent session can only be woken by its
own harness (the tool that runs it). Nothing else — not another session, not a
background daemon — can push a new turn into a sleeping session. So Sandesh gives
each session a small **listener** it runs in the background; when mail addressed to
that session arrives, the listener stops, and the session's own harness notices the
stop and wakes it. That wake mechanism differs by surface, which is why the two
sections below are different: **MCP** users run the listener themselves, while **Pi
extension** users get the wake built in.

---

## For MCP users (Claude Code & other MCP clients)

**What you do first:** register the Sandesh MCP server, then in a session run the
listener in the background and start exchanging messages.

Register the server (`sandesh-mcp`) with your client — for Claude Code:

```bash
claude mcp add sandesh --scope user --env SANDESH_PROJECT=<your-project> -- sandesh-mcp
```

Once registered, the **messaging verbs are tools** (`sandesh_send`, `sandesh_reply`,
`sandesh_fetch`, `sandesh_inbox`, `sandesh_thread`, …). The five **lifecycle
commands** are exposed as slash commands — your human on-ramp:

- `/mcp__sandesh__setup` — enroll/provision a project
- `/mcp__sandesh__register` — register your address (your identity in the project)
- `/mcp__sandesh__unregister` — remove an address
- `/mcp__sandesh__archive` — pause a project (reversible)
- `/mcp__sandesh__unarchive` — un-pause a project

> **Where are the messaging tools?** The lifecycle commands appear under `/`, but
> the messaging tools (send/reply/fetch/…) are **model-callable** — the model
> invokes them for you, so they won't show up under `/`. Confirm they are loaded in
> the **`/mcp`** panel (not `/`).

### Getting woken: run the listener in the background

To be woken when mail arrives, run the listener — the `sandesh notify` command —
**in the background**, using your host's background-run tool (Claude Code's
`run_in_background`):

```bash
sandesh notify --to "<you>"
```

The operating loop for each session is:

1. **register** your address (once per session, via `/mcp__sandesh__register`).
2. **listen** — launch `sandesh notify --to "<you>"` in the background.
3. when the listener **stops**, wake up and `sandesh_fetch` to read your mail.
4. **act** on it, then `reply` to signal completion (`send` anytime to anyone).
5. **relaunch** the listener (back to step 2) and keep cooperating.

### Why the listener stopped

The background listener stops for a handful of reasons. What you do next depends on
which one:

| Why it stopped | What it means | What you do next |
|---|---|---|
| **Mail arrived** | New unread mail addressed *to* you is waiting. | Fetch now (`sandesh_fetch`), act, reply, then relaunch the listener. |
| **Timeout** | No mail arrived before the timeout expired. | Nothing happened — just relaunch the listener and keep waiting. |
| **Project retired** (tombstoned) | The whole project was permanently retired. | **Do not restart** the listener — the project is gone. |
| **Taken over** (evicted) | Another listener took your address over. | Do not relaunch — another listener now owns your address. |

(Cc'd mail is delivered and readable but does **not** wake you — it's swept up on
your next fetch. Only mail addressed directly *to* you wakes the listener.)

---

## For Pi extension users

**What you do first:** install the Pi extension. The verbs become Pi tools, and the
wake is handled for you.

```bash
pi install npm:@anthill-tec/sandesh-pi
```

The Sandesh messaging verbs (send, reply, fetch, inbox, thread, …) are exposed as
**Pi tools**, the same surface as the MCP server.

The key difference from the MCP route: the Pi extension **wakes the session itself
(native wake)**. There is **no manual** listener step here — you do not run sandesh
notify yourself; the extension runs the wake loop for you and re-invokes the session
when mail addressed to it arrives.
So your loop is simply: register → keep working → the extension wakes you on new
mail → fetch → act → reply.

> Pi needs the `sandesh` CLI available on the machine (installed, or run on demand
> via `uvx`) — the extension shells out to it. See [docs/INSTALL.md](INSTALL.md).

---

## Common: sending, replying, reading

These work the same whatever surface you use (CLI shown; the tool/prompt forms
mirror them):

- **Send** a message — a subject is the minimum; add a body for detail:
  ```bash
  sandesh send --from "<you>" --to "<recipient>" --subject "CR-308 started"
  ```
- **Reply** — threads under the parent and signals you're done with the request:
  ```bash
  sandesh reply --to-msg 1 --from "<you>" --body "done — chain unaffected"
  ```
- **Fetch** — read your unread mail (marks it read = "being acted on"):
  ```bash
  sandesh fetch --to "<you>"
  ```

### Cross-project sending

By default a project's participants message only within their own project. Sending
**across** projects needs a one-time admin grant — a CLI-only action a human
operator runs:

```bash
sandesh grant --cross-project --project <id> --by <admin>
```

If a send fails with *"cross-project sending not approved …"*, ask a human to run
that grant — there is no MCP/Pi tool for it, and you should not retry on your own.

### Pausing and retiring a project

- **Archive** pauses a project — a reversible, read-only freeze; `unarchive` brings
  it back, with all messages and read state intact.
- **Retire** (tombstone) permanently retires a project — a destructive, admin-only
  CLI action. A retired project's traffic is hidden from all reads, and (as above) a
  listener that stops because its project was retired must **not** be relaunched.
