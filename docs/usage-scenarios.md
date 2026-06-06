# Sandesh — Usage & Communication Scenarios

> **Purpose of this doc.** Source material for writing the **MCP tool docstrings**. It
> explains *who* uses Sandesh, *why*, and *exactly how* the orchestrators talk to each
> other — with concrete, end-to-end message exchanges and a tool-by-tool reference. A
> docstring author should be able to read a tool's section here and write a description
> that says what the tool is for, who calls it, when, and what each argument means.

---

## 1. The world Sandesh lives in

Sandesh is the messaging layer for a **"Model-B" parallel-orchestration workflow**:

- **Mainline** — one coordinator session. Owns the work queue (the CR/task list), decides
  *what* each worker does and *when*, files/edits specs, and resolves cross-worker
  conflicts. Address: `Mainline - <Project>`.
- **Tracks** — N worker sessions running in parallel (`Track 1`, `Track 2`, …), each
  executing one unit of work at a time in its own isolated workspace. Addresses:
  `Track <N> - <Project>`.

**Two hard constraints make a relay like Sandesh necessary:**

1. **Sessions cannot talk to each other directly.** Mainline and the Tracks are separate
   agent sessions; there is no shared channel between them except a relay.
2. **A *sleeping* session can only be re-invoked by its host's background-task mechanism.**
   A plain daemon, an OS signal, or an MCP server *cannot* push a turn into an idle agent.
   So "notify me when I get mail" must be a **blocking background process the session itself
   launches** — that process exiting is what wakes the agent.

Sandesh provides exactly this: a durable, queryable **mailbox per address**, plus a blocking
**`notify` watcher** each session runs in the background so it *wakes* when mail addressed to
it arrives. Everything is local, SQLite-backed, multi-project (every call carries a
`project_id` routing to that project's own store).

> Addresses **represent orchestrators**, but Sandesh itself is domain-agnostic — it's a
> general agent-to-agent messaging primitive. The "Mainline / Track" roles are convention,
> enforced only by the address-format validator (`<Orchestrator> - <Project>`).

---

## 2. Core concepts (the vocabulary the docstrings should use)

- **Address** — a durable identity in the addressbook, format `'<Orchestrator> - <Project>'`
  (e.g. `Mainline - Nai`, `Track 2 - Nai`). Registered once; persists across sessions.
- **Message** — an envelope: `from`, `subject` (always present), optional `body` (an md file;
  omit it and the **subject IS the content** — a "subject-only" message), `kind`
  (`request` / `directive` / `fyi` / `reply`), `status` (`open` → `actioned` → `closed`),
  and an optional `in_reply_to` thread link.
- **Recipients** — each message has one or more recipients, each with a **role**:
  - **To** — a primary recipient. **A `to` recipient's `notify` watcher WAKES** on the message.
  - **Cc** — a copy recipient. **Cc does NOT wake** anyone; it's delivered silently and the
    cc'd address sees it on its next `fetch`. (To = "act on this now"; Cc = "for your awareness".)
- **Per-recipient read state** — read/unread is tracked **per recipient**, so a broadcast or a
  cc stays unread for the others after one reads it.
- **`all-tracks`** — a reserved recipient keyword. As a `to`, it **broadcasts** to every active
  address **except the sender**; each recipient's watcher wakes on its own copy.
- **notify (the wake)** — a blocking watcher a session runs in the background; it exits (waking
  the agent) when the address has unread **`to`** mail. **This is NOT an MCP tool** (see §6).
- **fetch (the read)** — pulls all of an address's unread messages (to + cc) into one
  consolidated view and marks them read.
- **Disposition vs read** — `read_at` (per recipient) = "seen"; `status` (per message) =
  "what became of it". A *request* can be **read** by Mainline yet still **open** until it's
  resolved, then `actioned`. A `reply --resolves` actions the parent in one step.
- **Thread** — `in_reply_to` links a reply to its parent; the full chain is a conversation.

---

## 3. The day-to-day session loop

Every session — Mainline and each Track — runs the same shape:

```
on session start:
  (first time only) register my address in the addressbook
  launch  notify --to "<my address>"  in the BACKGROUND   # the wake watcher

loop:
  ── blocked in notify until 'to' mail arrives (or I finish a unit of work) ──
  on wake:
     fetch --to "<my address>"        # read the consolidated unread (to + cc), marks read
     act on it (do work / reply / re-schedule / start a task)
     relaunch notify --to "<my address>"   # keep listening
```

- **A Track** additionally checks its work queue at each boundary and does the work; it sends
  Mainline a **request** when it hits something only Mainline can decide, and reads Mainline's
  **directives**.
- **Mainline** spends its time consuming Track **requests**, deciding, updating the queue, and
  sending **directives** (or **replies**) back.

`notify` exits with distinct codes the loop branches on: `0` = mail (→ fetch + relaunch),
`2` = timeout (→ relaunch), `3` = tombstoned/evicted (→ do **not** relaunch — you're being
removed), `5` = a notifier was already live for this address (dedup; don't start a second).

---

## 4. Communication scenarios (concrete, end-to-end)

These mirror real exchanges in a parallel-CR workflow. Each names the tools it exercises.

### S1 — A Track raises a request; Mainline resolves it (the bread-and-butter)
A Track, mid-task, discovers something only the coordinator can decide (a stale spec, a
scope gap, an unowned test population, a design fork). It **sends a `request` to Mainline**
and *holds* — it does not proceed on its own judgement.

```
# Track 1 → Mainline: "the spec I'm about to start is stale; re-scope it"
sandesh send --from "Track 1 - Nai" --to "Mainline - Nai" --kind request \
   --subject "CR-310 spec stale — needs re-audit before I start" --body-file gap-analysis.md
```
→ Mainline's `notify` wakes → `fetch --to "Mainline - Nai"` (reads the gap-analysis body) →
Mainline acts (re-audits, edits the spec, adjusts the queue) → **replies, resolving the thread**:
```
sandesh reply --to-msg <id> --from "Mainline - Nai" --resolves \
   --subject "CR-310 re-spec done — cleared to start" --body-file directive.md
```
→ Track 1's `notify` wakes → `fetch` (sees the reply threaded under its own request) → starts.
The `--resolves` marks the original request **actioned** in the same call — no loose ends.
*Tools: `send`, `notify`(wake), `fetch`, `reply`.*

### S2 — Mainline directs a Track (assignment / go-signal)
Mainline assigns new work, or clears a Track to begin something that was blocked.
```
sandesh send --from "Mainline - Nai" --to "Track 2 - Nai" --kind directive \
   --subject "CR-317 (geo-eval) assigned to you — gated until CR-307 merges"
```
→ Track 2's `notify` wakes → `fetch` → it picks up the assignment (and, in this workflow,
consults its scheduler for the exact gate). *Tools: `send`, `fetch`.*

### S3 — Broadcast to all Tracks (a coordination announcement)
Mainline announces something every Track must know — a policy change, a re-trigger, a window
opening/closing.
```
sandesh send --from "Mainline - Nai" --to all-tracks --kind directive \
   --subject "Cull re-triggered as per-crate chains — re-read your queue before starting"
```
→ `all-tracks` expands to every active address **except Mainline**; **each Track's `notify`
wakes** on its own copy; Mainline (the sender) gets none. *Tools: `send` (broadcast).*

### S4 — To-wakes, Cc-silent (act vs awareness)
Mainline needs Track 2 to act, but wants Track 1 and Track 3 merely *aware* (e.g. a change
that touches a shared boundary).
```
sandesh send --from "Mainline - Nai" --to "Track 2 - Nai" --cc "Track 1 - Nai,Track 3 - Nai" \
   --kind directive --subject "checkpoint.rs handoff: Track 2 takes the 3 barrier tests"
```
→ **Track 2 wakes** (it's `to`); **Track 1 & Track 3 do NOT wake** — they each see the message
on their next `fetch`. Conserves agent turns: you don't re-invoke a session for an FYI.
*Tools: `send` (to + cc).*

### S5 — Terse, subject-only pings (acks, status)
High-frequency, one-line signals don't need a body — the subject *is* the message.
```
sandesh send --from "Track 3 - Nai" --to "Mainline - Nai" --kind fyi \
   --subject "CR-314 merged — chain advanced to CR-307"
```
No file is written; `fetch` renders it as a header + subject with no body block.
*Tools: `send` (subject-only).*

### S6 — A threaded back-and-forth
A request needs a clarification before it can be resolved:
```
Track 2  → request   #41  "CR-308 batch — which serialization?"
Mainline → reply     #42  (in_reply_to #41) "need the file-overlap map first — send it?"
Track 2  → reply     #43  (in_reply_to #42) "<overlap map>"
Mainline → reply     #44  (in_reply_to #43, --resolves) "per-crate chains; #41 actioned"
```
`thread --id 44` prints the whole chain top-to-bottom so any party can reconstruct context.
*Tools: `reply`, `thread`.*

### S7 — Disposition: read ≠ done
Mainline `fetch`es a request (it's now **read**), but resolving it takes real work spread over
time. The request stays **open** until Mainline finishes, then closes it:
```
sandesh actioned --id 41 --status actioned     # or 'closed'
```
(Usually done implicitly via `reply --resolves`, but `actioned` is the explicit lever — e.g. a
request that's superseded or won't be done gets `--status closed`.) *Tools: `fetch`, `actioned`.*

### S8 — Checking your mailbox without consuming it
A session wants to see what's pending without marking anything read (e.g. a triage glance):
```
sandesh inbox --to "Mainline - Nai"            # list unread (or --all for read+unread)
sandesh fetch --to "Mainline - Nai" --peek     # render the bodies WITHOUT marking read
```
*Tools: `inbox`, `fetch --peek`.*

### S9 — Onboarding & teardown (addressbook lifecycle)
A new Track joins the roster; a retired Track leaves.
```
sandesh setup --project Nai                                    # once per project
sandesh register --address "Track 4 - Nai" --kind track        # new participant
sandesh addressbook                                            # who exists + who's listening
sandesh unregister --address "Track 4 - Nai" --as "Mainline - Nai"   # retire (Mainline-privileged)
```
Removing an address whose watcher is *live* doesn't kill it directly (you can't cross-session
kill); `unregister` sets a **tombstone** the watcher sees on its next poll and self-terminates,
then the address is deactivated. *Tools: `setup`, `register`, `addressbook`, `unregister`.*

### S10 — Cross-Track awareness (Track → Track)
Usually Tracks route through Mainline, but a Track can directly inform another about something
affecting it (Mainline typically `cc`'d for the record):
```
sandesh send --from "Track 3 - Nai" --to "Track 1 - Nai" --cc "Mainline - Nai" --kind fyi \
   --subject "barrier_tests.rs: I left 3 CheckpointManager tests for your CR"
```
*Tools: `send` (Track-to-Track + cc).*

---

## 5. Tool-by-tool reference (for the docstrings)

Every tool takes **`project_id`** (the store router; falls back to `$SANDESH_PROJECT`). Tools
that act *as* an address resolve the caller's own address from the given arg or
`$SANDESH_ADDRESS`. Addresses must match `'<Orchestrator> - <Project>'` and the `<Project>`
must equal `project_id`.

| Tool | One-line purpose | Typical caller | Key args | Returns |
|---|---|---|---|---|
| **`sandesh_setup`** | Provision a project's store (create DB + dirs). Idempotent — run once before anything else. | anyone (bootstrap) | `project_id` | the store path |
| **`sandesh_register`** | Add an address to the addressbook (self-register). Rejects an active duplicate; reactivates a previously-removed one. | a joining orchestrator | `project_id`, `address`, `kind?` (`mainline`/`track`), `name?` | confirmation |
| **`sandesh_unregister`** | Remove an address. **Mainline may remove anyone; any address may remove itself.** If its watcher is live, tombstones it first (returns "tombstoned"; re-run once offline). | Mainline (or self) | `project_id`, `address`, `as` (requester) | `unregistered` or `tombstoned`+pid |
| **`sandesh_addressbook`** | List all participants with active/inactive status and **who is currently listening** (live notifier). | anyone | `project_id` | rows (address, kind, active, listening) |
| **`sandesh_send`** | Send a message. `subject` is mandatory; omit a body ⇒ subject-only. `to`/`cc` are comma-separated; `to: all-tracks` broadcasts (minus sender). **To wakes the recipient; Cc is silent.** | Mainline or a Track | `project_id`, `from`, `to?`, `cc?`, `subject`, `kind?` (`request`/`directive`/`fyi`), `body?`/`body_file?` | new message id |
| **`sandesh_reply`** | Reply to a message; threads via `in_reply_to`. Defaults the recipient to the parent's sender and the subject to `"Re: …"`. `reply_all` cc's the parent's recipients; `resolves` marks the parent **actioned**. | the recipient of a message | `project_id`, `to_msg` (parent id), `from`, `subject?`, `body?`, `all?`, `resolves?` | new message id |
| **`sandesh_inbox`** | List an address's messages (unread by default; `all` includes read). A quick triage view — does **not** mark anything read. | any address (its own) | `project_id`, `to` (the address), `all?` | message rows |
| **`sandesh_fetch`** | The real read: consolidate an address's unread messages (to + cc) into one view — bodies read from file, subject-only entries shown as just the subject — and **mark them read** (`peek` renders without marking). This is what a session calls after `notify` wakes it. | any address (its own) | `project_id`, `to` (the address), `peek?` | consolidated messages (+ thread refs) |
| **`sandesh_thread`** | Print a message's full reply chain (root → leaf) so any party can reconstruct a conversation's context. | anyone | `project_id`, `id` (any message in the thread) | the chain |
| **`sandesh_actioned`** | Set a message's **disposition** (`open` / `actioned` / `closed`) — the explicit lever for closing a request that isn't resolved via `reply --resolves` (e.g. superseded → `closed`). | Mainline (the resolver) | `project_id`, `id`, `status?` (default `actioned`) | confirmation |

**Not exposed as a tool — `notify` (the wake watcher).** `notify` is a *blocking background
process*, not a request/response verb. Re-invoking a sleeping agent is the host's
background-task mechanism's job — an MCP server cannot do it. So an agent launches `notify`
via its own background mechanism (not over MCP) and uses the MCP tools above for everything
else. Document this boundary in the server's top-level description so callers don't look for a
"wait for mail" tool.

---

## 6. Conventions reference

- **Address format** — `'<Orchestrator> - <Project>'`, where `<Orchestrator>` is `Mainline`
  or `Track <N>` and `<Project>` equals `project_id`. Validated at `register` and `send`; a
  typo (`Track 22 - Nai`, wrong project) is rejected at the call, not silently dropped.
- **`kind`** — `request` (Track → Mainline, "decide this"), `directive` (Mainline → Track,
  "do this"), `fyi` (awareness), `reply` (set automatically by `sandesh_reply`). It's advisory
  metadata for the reader, not enforced routing.
- **`project_id`** — every call carries it (or `$SANDESH_PROJECT`); it routes to that project's
  isolated store. One Sandesh install serves many projects side by side.
- **Env defaults** (for the CLI / a baked-in MCP env) — `$SANDESH_PROJECT` (default
  `project_id`), `$SANDESH_ADDRESS` (the caller's own address for `from`/`to`),
  `$SANDESH_POLL_SECONDS` (the `notify` cadence; default 10).
- **History is kept** — nothing is deleted; `read_at` and `status` are state transitions, so
  the full request→resolution record is queryable after the fact.

---

## 7. Why this matters for the docstrings

A good MCP docstring for each tool should convey, in the caller's terms:
1. **What it does** (the one-line purpose above).
2. **Who calls it and when** — e.g. *"a Track calls `sandesh_send` with `kind='request'` when it
   hits a decision only Mainline can make"*; *"a session calls `sandesh_fetch` right after its
   `notify` watcher wakes it"*.
3. **The semantic gotchas** that change behaviour — **To wakes / Cc is silent**, `all-tracks`
   excludes the sender, subject-only when no body, `reply --resolves` closes the parent,
   `unregister` of a live address tombstones first.
4. **That `notify` (the wake) is deliberately not a tool** — so callers understand the verbs
   here are on-demand and the wake is a separate background process.

Lean on the scenarios in §4 for the "when/why" — they're the realistic exchanges the tools exist to serve.
