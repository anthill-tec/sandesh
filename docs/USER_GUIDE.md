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
wake is handled for you once you start it.

```bash
pi install npm:@anthill-tec/sandesh-pi
```

The Sandesh messaging verbs (send, reply, fetch, inbox, thread, …) are exposed as
**Pi tools**, the same surface as the MCP server, plus `sandesh_status` (your home
view) and the three watcher tools below.

The key difference from the MCP route: the Pi extension **wakes the session itself
(native wake)**. You do not run `sandesh notify` by hand — the extension runs the
watcher as a supervised child (start it with `sandesh_notify_start`, or set
`SANDESH_AUTOSTART=1`) and re-invokes the session when mail addressed to you
arrives. So your loop is: register → start the watcher → keep working → the
extension wakes you on new mail → fetch → act → reply.

**Behaviour change in 0.4.0 — the wake is tool-started.** Up to 0.3.x the extension
armed the wake loop automatically at session start. From `0.4.0`:

- **Start it yourself** with the `sandesh_notify_start` tool (defaults to
  `$SANDESH_ADDRESS` / `$SANDESH_PROJECT`). Set `SANDESH_AUTOSTART=1` to restore the
  0.3.x auto-arm at session start (both identity vars must then be set).
- **Ambient status at session start:** with `$SANDESH_ADDRESS` and `$SANDESH_PROJECT`
  set, a ≤ 12-line status block (address, listening, unread) is injected before your
  first turn, so you know your mailbox state without a tool call.
- **Tool results are AXI envelopes** in TOON encoding — the same `--format toon`
  output described under [Machine output for agents](#machine-output-for-agents---format-toonjson);
  CLI failures come back as `ok: false` results with `error` and `help[]`.
- **Inspect or stop the watcher** with `/sandesh-watcher status` and
  `/sandesh-watcher stop [address]` (tools: `sandesh_notify_status`,
  `sandesh_notify_stop`).

**Your identity.** The extension reads `$SANDESH_ADDRESS` and `$SANDESH_PROJECT` from
the environment only — it never reads a `.env` file. If your project keeps them in
`./.env`, load it at the shell with [direnv](https://direnv.net): put an `.envrc`
containing `dotenv` next to it, add the shell hook (`direnv hook fish | source` in your
fish config, or `eval "$(direnv hook bash)"` in `.bashrc`), then run `direnv allow`
once, and start pi from that directory. If the identity is in `./.env` but not exported,
the extension warns at session start — until you fix that there is no ambient status
and no wake.

> Pi needs the `sandesh` CLI **≥ 0.4.2** available on the machine (installed, or run
> on demand via `uvx`) — the extension shells out to it and refuses an older CLI at
> session start. See [docs/INSTALL.md](INSTALL.md).

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

### Re-reading mail you have already read

`fetch` returns only unread mail, so once a request is read its body has to come back
another way. None of these marks anything read:

- **One conversation:** `thread` returns the whole reply chain with the bodies of the
  messages you sent or received — name yourself with `--as`:
  ```bash
  sandesh thread --id <id> --as '<your address>'
  ```
  Without `--as` (and no `$SANDESH_ADDRESS`), the chain comes back without bodies and
  the machine envelope counts them as `withheld: <n>` with a hint to add `--as`. An
  `--as` that is not a valid address for the project is an error.
- **A batch:** `inbox --with-body` adds the bodies of the listed rows, read or unread:
  ```bash
  sandesh inbox --to '<your address>' --all --with-body --limit 10
  ```
  Pair it with `--limit`: without it you get up to 50 rows, each body up to 500
  characters (`--full` removes the cut) — a lot of text for an agent's context.
- **Find it first:** `search` hits carry a `snippet` of the matching text, so you can
  spot the right message id before reading it
  (`sandesh search --to '<your address>' "gateway timeout"`; `--fields` picks columns).

The MCP and Pi tools take the same options: `sandesh_thread(msg_id, requester="<your
address>")`, `sandesh_inbox(recipient, unread_only=False, with_body=True)` (Pi also
takes `full`), and `sandesh_search(..., fields=["id", "snippet"])` (Pi; the MCP
`sandesh_search` always returns the snippet).

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

---

## Machine output for agents (`--format toon|json`)

When an agent (not a human) drives the CLI, ask for a **machine envelope** instead of
the tables: every invocation then writes exactly **one [AXI](https://axi.md) envelope
to stdout** — as [TOON](https://toonformat.dev) or JSON (same dict, two encodings) —
while the human text goes to **stderr**. Exit codes are unchanged, so scripts that
branch on `$?` keep working; parse stdout, ignore stderr.

- **Select it:** `--format {human,toon,json}` before *or* after the verb
  (`sandesh --format toon inbox …` ≡ `sandesh inbox … --format toon`), or set
  `$SANDESH_FORMAT`. Default `human`; an invalid env value exits 2 naming the three.
- **Envelope shape:** `axi:` → `verb`, `ok`, the verb's flat result fields, `context`
  (`project`, plus `address` on the verbs that act for one), optional `help[N]`
  (1–3 next-step command templates with `<addr>`/`<id>` placeholders), and `warnings`
  (always present, `[]` when clean). A failure is `ok: false` + `error: "<the same
  message human mode prints>"`, on **stdout**, with the same exit code (1; usage
  errors — unknown flag/verb, bad `--fields` — exit 2 with a `help[]` naming `--help`).
- **Keep it small:** lists show a 2–4-column default; `--fields <csv>` widens
  `addressbook` (`address,kind,status,listening,registered`), `inbox`
  (`id,from,to,cc,kind,subject,created,re,unread`), `thread`
  (`id,from,subject,created,re`) and `search`
  (`id,from,subject,kind,created,role,snippet`; default `id,from,subject,snippet`) —
  an unknown name exits 2 listing the valid set.
  `inbox --limit N` caps rows (default 50; the `unread: n of total` aggregate is never
  sliced). `fetch` bodies are cut at **500 chars** with a
  `(truncated, N chars total)` suffix and a `help[]` pointing at `fetch … --full`;
  `--full` (also accepted by `thread`) returns complete bodies. `thread` bodies follow
  the same 500-char cut (with a `help[]` pointing at `thread … --full`) and are shown
  per message only when the caller — `--as '<address>'`, else `$SANDESH_ADDRESS` —
  is that message's sender or a recipient; other messages in the chain list without a
  body and are counted as `withheld: <n>`. A read verb given an unknown `--project`
  fails with the known project ids (and a `did you mean` on a case-only mismatch).
- **Idempotent no-ops are `ok: true`:** `register` of an existing address →
  `result: already`; `unregister` of an absent one → `result: absent`; `archive` of an
  archived project → `result: already` (human mode still exits as before).
- **Home view:** a bare `sandesh` (machine mode only) or `sandesh status` answers
  "who am I, am I listening, how much is unread" in one call — it needs
  `$SANDESH_PROJECT` (or `--project`) **and** `$SANDESH_ADDRESS`, else `ok: false`
  naming both, exit 2.

`sandesh --project Demo --format toon addressbook`:

```text
axi:
  verb: addressbook
  ok: true
  participants[2]{address,listening}:
    Mainline - Demo,false
    Track 1 - Demo,false
  listening: 0/2
  context:
    project: Demo
  help[2]: "sandesh --project Demo send --from <addr> --to <addr> --subject \"<subject>\"",sandesh --project Demo notify --to <addr>
  warnings: []
```

`sandesh --project Demo inbox --to "Track 1 - Demo" --format toon`:

```text
axi:
  verb: inbox
  ok: true
  messages[2]{id,from,subject,unread}:
    1,Mainline - Demo,CR-308 started,true
    2,Mainline - Demo,"heads-up: freeze Friday",true
  unread: 2 of 2
  context:
    project: Demo
    address: Track 1 - Demo
  help[2]: sandesh --project Demo fetch --to <addr>,sandesh --project Demo thread --id <id>
  warnings: []
```

**The listener in machine mode.** `sandesh notify … --format toon` sends its progress
lines to stderr and, on **every** way it stops, writes one final envelope
(`verb: notify`, `exit`, `address`, `project`, `unread`) — so the agent that launched
it in the background can read *why* without scraping the banner:

| It stopped because… | exit | `ok` | what else is in the envelope |
|---|---|---|---|
| mail arrived | 0 | `true` | `unread[N]: <ids>` — the triggering message ids, ascending (the only case where it is non-empty) |
| timeout, nothing arrived | 2 | `true` | `unread: []` |
| a duplicate listener already held the address | 5 | `true` | `unread: []` |
| the project was retired — do not relaunch | 3 | `false` | `error` |
| taken over by another listener — do not relaunch | 4 | `false` | `error` |
| startup/runtime error | 1 | `false` | `error` |
| killed by a signal (SIGTERM/SIGINT) | 128+N | `false` | `unread: []` + `error: terminated by SIGTERM (143)` |
