"""sandesh/mcp_server.py — Sandesh MCP server (stdio).

A thin adapter exposing Sandesh's verbs as MCP tools over stdio. It owns no business
logic — each tool resolves the project store and delegates to `sandesh_db.*` (the same
role `cli.py` plays for the terminal). The wake path stays the standalone `notify`
watcher; this server never re-invokes a sleeping agent (PRD §6).

`mcp` is the only third-party dependency and is imported ONLY here — the CLI path stays
stdlib-only. Exposed as the `sandesh-mcp` console script (entry point
`sandesh.mcp_server:main`); requires the `[mcp]` extra.

Phase 2 / CR-SAN-001 (foundation + sandesh_setup). Later CRs add the remaining tools.
"""

import os
import sys
from importlib import resources

from sandesh import sandesh_db

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations
    from typing import Annotated
    from pydantic import Field
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

_MISSING_MCP_MSG = (
    "sandesh-mcp requires the MCP extra. Install it with:\n"
    "  pipx install 'sandesh-relay[mcp]'   (or  pip install 'sandesh-relay[mcp]')"
)


def _resolve_project(project_id=None):
    """The passed `project_id`, else `$SANDESH_PROJECT` (D4) — the same precedence the
    CLI uses. Raises ValueError when neither is set."""
    project = project_id or os.environ.get("SANDESH_PROJECT")
    if not project:
        raise ValueError(
            "project_id is required (pass project_id or set $SANDESH_PROJECT)")
    return project


def _ctx(project_id=None):
    """(project, store, con) for tools that need a DB connection — mirrors
    `cli.py::_ctx`. `project_id` falls back to `$SANDESH_PROJECT` (D4)."""
    project = _resolve_project(project_id)
    store = sandesh_db.store_dir(project)
    con = sandesh_db.connect()
    return project, store, con


def _derive_or_resolve(project_id, addr):
    """The full derivation chain (CR-SAN-025 §S2): explicit `project_id` → it;
    else `$SANDESH_PROJECT`; else the `<Project>` part of `addr` (the calling
    address — `from_addr` for send/reply, `recipient` for fetch). An explicit
    or env project always wins; derivation only kicks in when BOTH are absent.
    Raises ValueError when no project can be determined (malformed `addr`)."""
    project = project_id or os.environ.get("SANDESH_PROJECT")
    if project:
        return project
    _orch, proj = sandesh_db.validate_address(addr)
    return proj


if _MCP_AVAILABLE:
    SANDESH_INSTRUCTIONS = """Sandesh relays messages between cooperating agent orchestrators in the Model-B
workflow: a Mainline coordinator session plus parallel Track worker sessions that
cannot talk to each other directly. Addresses represent orchestrators, formatted
'Mainline - <Project>' or 'Track N - <Project>'.

Two channels, one boundary: this MCP server carries the VERBS (send, reply, fetch,
inbox, register, ...). The WAKE is out-of-band and is NOT an MCP tool — an MCP server
cannot re-invoke a sleeping agent.

TO RECEIVE MESSAGES YOU MUST ENABLE LISTENING. Right after registering your address,
you MUST launch the standalone watcher `sandesh notify --to "<your address>"` via your
host's background-task tool (e.g. run_in_background) — do NOT run it inline, it blocks.
When that background watcher EXITS, it means mail addressed to you has arrived: call
`sandesh_fetch` to read it, act on it, then RELAUNCH `sandesh notify` the same way to
keep listening. Repeat that loop for the life of your session. Sending (sandesh_send /
sandesh_reply) needs NO listener — only receiving does. The full loop, exit codes and
Model-B walkthrough live in the `sandesh://usage` resource.

Lifecycle without a status field: reading a message (fetch) means "received and now
being acted on"; sending a reply means done — reply signals completion of the
requested work. See the sandesh://usage resource for full Model-B scenarios.

Cross-project messaging exists but is gated behind a one-time per-project admin
grant, and granting is CLI-only — a human operator must run
`sandesh grant --cross-project --project <Project> --by <admin>`; there is no MCP
grant tool. If a send fails with "cross-project sending not approved for project
...", ask a human to run that grant — do not retry.

Project lifecycle: sandesh_archive is a reversible, read-only pause — while a
project is archived its participants can neither send nor receive, and
sandesh_unarchive restores it (messages and read state survive untouched).
Tombstoning is a destructive, backend-admin CLI action with no MCP tool; a
tombstoned project's traffic is hidden from all reads (inbox/fetch/thread).

Finding mail: sandesh_inbox and sandesh_fetch accept composable filters
(sender, sender_project, kind, since, until, subject_like). The sender_project
filter is the cross-project PROXY STREAM: when two parallel, interdependent
projects collaborate under a grant, filtering your own mailbox by the other
project's id yields just that counterpart's traffic — a virtual per-project
channel inside one inbox. For keyword lookup, sandesh_search runs an FTS5
full-text search over YOUR OWN mail only (subjects + bodies, read or unread);
it never crosses inbox boundaries and never marks anything read. Results are
bm25-ranked with snippets and paginated via limit/offset (`total` is the full
match count — page with offset until you have it all). Quoted phrases and
AND/OR/NOT work; a `reindexed: true` flag in the result just means the index
was lazily rebuilt first (harmless)."""


    mcp = FastMCP("sandesh", instructions=SANDESH_INSTRUCTIONS)

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    def sandesh_setup(
        project_id: Annotated[
            str | None,
            Field(description="The project store router; equals the <Project> part of every "
                  "address. Falls back to $SANDESH_PROJECT if omitted."),
        ] = None,
    ) -> str:
        """Provision a project's Sandesh store (create the DB + message dirs). Idempotent —
        safe to call repeatedly; run it once per project before anything else.

        Called by anyone bootstrapping a project (typically Mainline at the start of a
        workflow). One Sandesh install serves many projects side by side, each in its own
        isolated store. Returns the store dir path.

        Next: register your address, then enable listening with a background
        `sandesh notify` (see sandesh_register).
        """
        try:
            return sandesh_db.setup(_resolve_project(project_id))
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def sandesh_addressbook(
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
        ] = None,
    ) -> list[dict]:
        """List all participants in the project's addressbook with their active/inactive
        status and who is currently listening (has a live notifier watcher).

        Called by anyone (Mainline or a Track) to see the roster — who exists and who is
        online to receive a wake. Read-only; changes nothing. Returns list[dict].
        """
        con = None
        try:
            project, _store, con = _ctx(project_id)
            return sandesh_db.addressbook(con, project)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def sandesh_inbox(
        project_id: Annotated[
            str | None,
            Field(description="Accepted for compatibility but unused — inbox is "
                  "recipient-keyed on the global DB and needs no project routing."),
        ] = None,
        recipient: Annotated[
            str,
            Field(description="The address whose inbox to list — your own address, format "
                  "'<Orchestrator> - <Project>' (e.g. 'Mainline - Nai')."),
        ] = "",
        unread_only: Annotated[
            bool,
            Field(description="When True (default) list only unread messages; set False to "
                  "include already-read ones."),
        ] = True,
        sender: Annotated[
            str | None,
            Field(description="Only messages whose sender exactly matches this address. "
                  "None (default) = no constraint."),
        ] = None,
        sender_project: Annotated[
            str | None,
            Field(description="The cross-project proxy-stream filter: only messages whose "
                  "SENDER belongs to this project — filter your own mailbox by a "
                  "collaborating project's id to read just that counterpart's traffic "
                  "as a virtual channel. None (default) = no constraint."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="Only messages of this kind ('request'/'directive'/'fyi'/"
                  "'reply'). None (default) = no constraint."),
        ] = None,
        since: Annotated[
            str | None,
            Field(description="Only messages created at/after this timestamp — "
                  "'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS', inclusive. None = no lower bound."),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="Only messages created at/before this timestamp — "
                  "'YYYY-MM-DD' (extends to end of day) or 'YYYY-MM-DD HH:MM:SS', "
                  "inclusive. None = no upper bound."),
        ] = None,
        subject_like: Annotated[
            str | None,
            Field(description="Case-insensitive literal substring the subject must "
                  "contain. None (default) = no constraint."),
        ] = None,
    ) -> list[dict]:
        """List an address's messages — a quick triage glance at what's pending. Unread by
        default; pass unread_only=False to include read messages too.

        Called by an address on its own mailbox when it wants to see what's waiting WITHOUT
        consuming it — unlike sandesh_fetch, this does NOT mark anything read. The optional
        filters (sender, sender_project, kind, since, until, subject_like) compose; each
        None means "no constraint". Read-only. Returns list[dict].
        """
        con = None
        try:
            con = sandesh_db.connect()
            return [dict(r) for r in sandesh_db.inbox(
                con, recipient, unread_only, sender=sender,
                sender_project=sender_project, kind=kind, since=since,
                until=until, subject_like=subject_like)]
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool()
    def sandesh_fetch(
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT, "
                  "else derived from recipient's <Project> part."),
        ] = None,
        recipient: Annotated[
            str,
            Field(description="The address whose mail to read — your own address, format "
                  "'<Orchestrator> - <Project>' (e.g. 'Mainline - Nai')."),
        ] = "",
        mark: Annotated[
            bool,
            Field(description="When True (default) mark the fetched messages read; set False "
                  "to peek (render without marking, so they stay unread)."),
        ] = True,
        sender: Annotated[
            str | None,
            Field(description="Only messages whose sender exactly matches this address. "
                  "None (default) = no constraint."),
        ] = None,
        sender_project: Annotated[
            str | None,
            Field(description="The cross-project proxy-stream filter: only messages whose "
                  "SENDER belongs to this project — fetch (and mark read) just a "
                  "collaborating project's traffic, leaving the rest unread. "
                  "None (default) = no constraint."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="Only messages of this kind ('request'/'directive'/'fyi'/"
                  "'reply'). None (default) = no constraint."),
        ] = None,
        since: Annotated[
            str | None,
            Field(description="Only messages created at/after this timestamp — "
                  "'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS', inclusive. None = no lower bound."),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="Only messages created at/before this timestamp — "
                  "'YYYY-MM-DD' (extends to end of day) or 'YYYY-MM-DD HH:MM:SS', "
                  "inclusive. None = no upper bound."),
        ] = None,
        subject_like: Annotated[
            str | None,
            Field(description="Case-insensitive literal substring the subject must "
                  "contain. None (default) = no constraint."),
        ] = None,
    ) -> list[dict]:
        """The real read: consolidate an address's unread messages (both `to` and `cc`) into
        one view — bodies read from file, subject-only entries shown as just the subject —
        and mark them read. This mutates per-recipient read state (so it is NOT read-only).

        This is what a session calls right after its `notify` watcher wakes it. Reading a
        message means "received and now being acted on" — the waiting sender can observe that
        read state. Pass mark=False to peek without consuming. The optional filters (sender,
        sender_project, kind, since, until, subject_like) compose — only the matching subset
        is rendered and marked; non-matching unread mail stays unread. Returns list[dict]
        (with thread refs).
        """
        con = None
        try:
            _project, store, con = _ctx(_derive_or_resolve(project_id, recipient))
            return sandesh_db.fetch(
                con, store, recipient, mark, sender=sender,
                sender_project=sender_project, kind=kind, since=since,
                until=until, subject_like=subject_like)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def sandesh_thread(
        project_id: Annotated[
            str | None,
            Field(description="Accepted for compatibility but unused — thread is "
                  "msg_id-keyed on the global DB and needs no project routing."),
        ] = None,
        msg_id: Annotated[
            int,
            Field(description="Any message id within the thread; the full chain it belongs "
                  "to is returned root to leaf."),
        ] = 0,
    ) -> list[dict]:
        """Print a message's full reply chain (root to leaf), following in_reply_to links, so
        any party can reconstruct a conversation's context.

        Called by anyone to reconstruct a request→reply exchange — pass any message id in the
        thread and get the whole conversation in order. Read-only. Returns list[dict].
        """
        con = None
        try:
            con = sandesh_db.connect()
            return [dict(r) for r in sandesh_db.thread(con, msg_id)]
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def sandesh_search(
        recipient: Annotated[
            str,
            Field(description="The address whose mail to search — your own address, format "
                  "'<Orchestrator> - <Project>' (e.g. 'Mainline - Nai')."),
        ],
        query: Annotated[
            str,
            Field(description="The FTS5 search query — bare terms, quoted phrases "
                  "(\"deploy pipeline\"), and AND/OR/NOT operators. A malformed query "
                  "(e.g. an unterminated quote) is rejected with an error."),
        ],
        project_id: Annotated[
            str | None,
            Field(description="Accepted for compatibility but unused — search is "
                  "recipient-keyed on the global DB and needs no project routing."),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Max hits per page (default 20)."),
        ] = 20,
        offset: Annotated[
            int,
            Field(description="Number of ranked hits to skip — page through results "
                  "with offset=0, limit, 2*limit, ... until `total` is covered."),
        ] = 0,
        sender_project: Annotated[
            str | None,
            Field(description="The cross-project proxy-stream filter: only hits whose "
                  "SENDER belongs to this project. None (default) = no constraint."),
        ] = None,
    ) -> dict:
        """Full-text search (FTS5) over an address's OWN mail — subjects + bodies, read or
        unread, to and cc alike. The query supports bare terms, quoted phrases
        ('"gateway timeout"'), and AND/OR/NOT operators.

        The boundary: search only ever sees messages addressed to `recipient` — it never
        crosses into another address's inbox (mail you merely SENT is not searched). It is
        pure query: nothing is ever marked read.

        Pagination: returns {hits, total, limit, offset} — `hits` is the bm25-ranked page
        (best match first, each hit an envelope + a `snippet` highlight), `total` the full
        match count; advance `offset` by `limit` to page. A `reindexed: true` key means the
        FTS index was empty and was lazily rebuilt before querying (one-time, harmless).
        """
        con = None
        try:
            con = sandesh_db.connect()
            return sandesh_db.search(
                con, recipient, query, limit=limit, offset=offset,
                sender_project=sender_project)
        except (ValueError,) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool()
    def sandesh_register(
        addr: Annotated[
            str,
            Field(description="The address to add, format '<Orchestrator> - <Project>' where "
                  "<Orchestrator> is 'Mainline' or 'Track <N>' and <Project> equals project_id "
                  "(e.g. 'Track 4 - Nai'). A malformed or wrong-project address is rejected."),
        ],
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="The participant role: 'mainline' or 'track'. Advisory metadata."),
        ] = None,
        display_name: Annotated[
            str | None,
            Field(description="An optional human-friendly name for the address."),
        ] = None,
        by: Annotated[
            str | None,
            Field(description="The address performing the registration (the requester), "
                  "for authorization/audit."),
        ] = None,
    ) -> str:
        """Add an address to the project's addressbook (self-register). Rejects an active
        duplicate; reactivates a previously-removed address.

        Called by a joining orchestrator (Mainline or a new Track) once, at the start of its
        participation, before it can send or receive. Returns the registered address.

        After registering you MUST enable listening to receive messages: launch
        `sandesh notify --to "<your address>"` in the background (via your host's
        run_in_background tool); when it exits, call sandesh_fetch then relaunch it.
        """
        con = None
        try:
            project, _store, con = _ctx(project_id)
            sandesh_db.register(con, addr, kind, display_name, by, project)
            return addr
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    def sandesh_unregister(
        recipient: Annotated[
            str,
            Field(description="The address to remove, format '<Orchestrator> - <Project>'."),
        ],
        requester: Annotated[
            str,
            Field(description="The address requesting the removal. Mainline may remove anyone; "
                  "any address may remove itself."),
        ],
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
        ] = None,
    ) -> list[str | int | None]:
        """Remove an address from the addressbook. Authorization: Mainline may remove anyone;
        any address may remove itself.

        Called by Mainline (or an address on itself) to retire a participant. If the target's
        `notify` watcher is still live it cannot be cross-session killed — this tombstones it
        first (returns ('tombstoned', pid)); re-run once the watcher is offline to complete
        the soft-delete. Otherwise returns ('unregistered', None).
        """
        con = None
        try:
            project, _store, con = _ctx(project_id)
            return list(sandesh_db.unregister(con, recipient, requester, project))
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool()
    def sandesh_send(
        from_addr: Annotated[
            str,
            Field(description="The sender's own address, format '<Orchestrator> - <Project>' "
                  "(e.g. 'Track 1 - Nai'). Validated against the address format."),
        ],
        subject: Annotated[
            str,
            Field(description="The message subject (mandatory). Omit a body and the subject IS "
                  "the content (a subject-only message)."),
        ],
        project_id: Annotated[
            str | None,
            Field(description="The project store router; routes to that project's isolated "
                  "store. Falls back to $SANDESH_PROJECT, else derived from from_addr's "
                  "<Project> part."),
        ] = None,
        to: Annotated[
            list[str] | None,
            Field(description="Primary recipients as a LIST of addresses. Each `to` recipient's "
                  "notify watcher WAKES on this message (act-on-this-now). Use "
                  "to=['all-tracks'] to broadcast to every active address except the sender."),
        ] = None,
        cc: Annotated[
            list[str] | None,
            Field(description="Copy recipients as a LIST of addresses. Cc is SILENT — it does "
                  "NOT wake anyone; the cc'd address sees the message on its next fetch "
                  "(for-your-awareness, conserves agent turns)."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="Advisory message kind for the reader: 'request' (Track→Mainline), "
                  "'directive' (Mainline→Track), or 'fyi' (awareness)."),
        ] = None,
        body: Annotated[
            str | None,
            Field(description="Optional message body. Omit it for a subject-only message "
                  "(no body file is written)."),
        ] = None,
    ) -> int:
        """Send a message to one or more addresses. `subject` is mandatory; omit a body and
        the subject IS the content (subject-only).

        Called by Mainline or a Track to communicate — e.g. a Track sends kind='request' when
        it hits a decision only Mainline can make; Mainline sends kind='directive' to assign
        or unblock work.

        `to` and `cc` are LISTS of addresses. The key semantic gotcha: **To wakes the
        recipient** (its notify watcher fires — "act on this now"), while **Cc is silent** —
        a cc does NOT wake anyone; the cc'd address only sees it on its next fetch (awareness,
        saving agent turns). `to=['all-tracks']` broadcasts to every active address minus the
        sender. Returns the new message id.
        """
        con = None
        try:
            project, store, con = _ctx(_derive_or_resolve(project_id, from_addr))
            if isinstance(to, str):
                to = [to]
            if isinstance(cc, str):
                cc = [cc]
            return sandesh_db.send(
                con, store, from_addr, to, cc, subject, kind,
                body_text=body, project=project)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool()
    def sandesh_reply(
        parent_id: Annotated[
            int,
            Field(description="The id of the original message being replied to. The reply is "
                  "threaded under it via in_reply_to, and defaults its recipient to that "
                  "message's sender and its subject to 'Re: …'."),
        ],
        from_addr: Annotated[
            str,
            Field(description="The replier's own address, format '<Orchestrator> - <Project>'."),
        ],
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT, "
                  "else derived from from_addr's <Project> part."),
        ] = None,
        subject: Annotated[
            str | None,
            Field(description="Optional reply subject; defaults to 'Re: <parent subject>'. "
                  "Often the whole message (subject-only) when signalling completion."),
        ] = None,
        body: Annotated[
            str | None,
            Field(description="Optional reply body; omit for a subject-only reply."),
        ] = None,
    ) -> int:
        """Reply to a message; threads it under the original via in_reply_to and defaults the
        recipient to the original message's sender and the subject to 'Re: …'.

        Called by the recipient of a message to respond — crucially, a recipient uses a reply
        to signal **completion**: when it finishes the requested work it replies to the
        original message (often subject-only, the subject stating what was done). There is no
        separate status field — read = "being acted on", reply = "done". `parent_id` is the
        original message's id (any party can later walk the chain with sandesh_thread).
        Returns the new reply id.
        """
        con = None
        try:
            project, store, con = _ctx(_derive_or_resolve(project_id, from_addr))
            return sandesh_db.reply(
                con, store, parent_id, from_addr, subject, body, project=project)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    def sandesh_archive(
        project_id: Annotated[
            str,
            Field(description="The project to archive. REQUIRED — lifecycle ops act on "
                  "an explicit target only; there is NO $SANDESH_PROJECT fallback."),
        ],
        by: Annotated[
            str,
            Field(description="The address performing the archive — must be the "
                  "project's own Mainline ('Mainline - <Project>')."),
        ],
        force: Annotated[
            bool,
            Field(description="When True, forcibly reap a notifier watcher that does "
                  "not exit cooperatively within the eviction wait; when False "
                  "(default) such a watcher makes the archive refuse, unchanged."),
        ] = False,
    ) -> str:
        """Archive a project — a REVERSIBLE soft-close (sandesh_unarchive restores it).

        While archived, the project's participants can neither send nor receive
        messages; existing messages and read state stay intact (nothing is deleted).
        Live `notify` watchers are cooperatively evicted first, so this call may
        block for up to ~2× the poll interval while they exit; a non-cooperating
        watcher makes the call refuse (state unchanged) unless force=True, which
        reaps it anyway.

        Called by the project's own Mainline (`by`) when the project's work is done
        or paused. Returns a confirmation naming the project and its new state.
        """
        con = None
        try:
            con = sandesh_db.connect()
            sandesh_db.archive(con, project_id, by, force=force)
            return f"project '{project_id}' is now archived"
        except (ValueError, PermissionError, RuntimeError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    def sandesh_unarchive(
        project_id: Annotated[
            str,
            Field(description="The archived project to reactivate. REQUIRED — lifecycle "
                  "ops act on an explicit target only; there is NO $SANDESH_PROJECT "
                  "fallback."),
        ],
        by: Annotated[
            str,
            Field(description="The address performing the unarchive — must be the "
                  "project's own Mainline ('Mainline - <Project>')."),
        ],
    ) -> str:
        """Unarchive a project — reverses sandesh_archive (state back to 'active').

        The project's participants can send and receive again; messages, read state
        and any cross-project grant survive the archive→unarchive round-trip
        untouched. Participants should relaunch their `notify` watchers (archive
        evicted them).

        Called by the project's own Mainline (`by`) to resume a paused project.
        Returns a confirmation naming the project and its new state.
        """
        con = None
        try:
            con = sandesh_db.connect()
            sandesh_db.unarchive(con, project_id, by)
            return f"project '{project_id}' is now active"
        except (ValueError, PermissionError, RuntimeError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.prompt()
    def setup(project_id: str) -> str:
        """Guided turn: provision a project's Sandesh store."""
        return (
            f"Provision the Sandesh store for project '{project_id}'. "
            f"Call the sandesh_setup tool with project_id='{project_id}'. "
            "It is idempotent, so it is safe to run once at the start of the workflow. "
            "Then register your address and enable listening with a background "
            "`sandesh notify` (see the register prompt)."
        )


    @mcp.prompt()
    def register(addr: str, kind: str = "", project_id: str = "") -> str:
        """Guided turn: register an address, then enable listening."""
        kind_clause = f" with kind='{kind}'" if kind else ""
        proj_clause = f" in project '{project_id}'" if project_id else ""
        return (
            f"Register the address '{addr}'{proj_clause}{kind_clause}. "
            f"Call the sandesh_register tool with addr='{addr}'"
            + (f", kind='{kind}'" if kind else "")
            + (f", project_id='{project_id}'" if project_id else "")
            + ". "
            "Then, to RECEIVE messages, you MUST enable listening: launch "
            f"`sandesh notify --to \"{addr}\"` via your host's background-task tool "
            "(run_in_background) — never inline, it blocks. When that background watcher "
            "exits, call sandesh_fetch to read your mail, act on it, then relaunch "
            "`sandesh notify` the same way to keep listening."
        )


    @mcp.prompt()
    def unregister(recipient: str, requester: str) -> str:
        """Guided turn: remove an address from the addressbook."""
        return (
            f"Unregister the address '{recipient}'. "
            f"Call the sandesh_unregister tool with recipient='{recipient}' and "
            f"requester='{requester}'. Mainline may remove anyone; any address may "
            "remove itself. If the target's notify watcher is still live the call "
            "returns ('tombstoned', pid) — re-run sandesh_unregister once it is offline."
        )


    @mcp.prompt()
    def archive(project_id: str, by: str) -> str:
        """Guided turn: archive (reversibly pause) a project."""
        return (
            f"Archive project '{project_id}'. "
            f"Call the sandesh_archive tool with project_id='{project_id}' and by='{by}'. "
            "This is a REVERSIBLE soft-close (sandesh_unarchive restores it): while "
            "archived, participants can neither send nor receive, but nothing is deleted. "
            "`by` must be the project's own Mainline."
        )


    @mcp.prompt()
    def unarchive(project_id: str, by: str) -> str:
        """Guided turn: reactivate an archived project."""
        return (
            f"Unarchive project '{project_id}'. "
            f"Call the sandesh_unarchive tool with project_id='{project_id}' and by='{by}'. "
            "This reverses sandesh_archive (state back to 'active'); messages, read state "
            "and any cross-project grant survive untouched. Participants should relaunch "
            "their `sandesh notify` watchers afterwards. `by` must be the project's own Mainline."
        )


    _USAGE_FALLBACK = """# Sandesh — Usage (fallback)

    The usage-scenarios document could not be located on disk. Sandesh is a Model-B
    agent-messaging relay (Mainline coordinator + parallel Track workers). For the full
    scenarios, consult the bundled sandesh/data/usage-scenarios.md, the MCP tool docstrings
    (each tool's description explains who calls it and why), and the project repository
    at https://github.com/anthill-tec/sandesh.
    """


    def _read_usage_doc() -> str:
        """Return the bundled usage-scenarios.md (package data), else a stub.

        Reads sandesh/data/usage-scenarios.md via importlib.resources, which works
        both from a source checkout/editable install and from the built wheel — no
        dev/install divergence and no dependence on the CWD. Falls back to a
        non-empty stub if the resource cannot be found or read."""
        try:
            res = resources.files("sandesh").joinpath("data/usage-scenarios.md")
            text = res.read_text(encoding="utf-8")
            if text.strip():
                return text
        except (FileNotFoundError, OSError, ModuleNotFoundError):
            pass
        return _USAGE_FALLBACK


    @mcp.resource("sandesh://usage", mime_type="text/markdown")
    def usage_doc() -> str:
        """The Sandesh usage & communication-scenarios document (Model-B walkthroughs,
        tool-by-tool reference). Read-only; served from bundled package data
        (sandesh/data/usage-scenarios.md)."""
        return _read_usage_doc()


def main():
    """Run the MCP server over stdio.

    When the optional `mcp` extra is not installed, print a friendly, actionable
    message naming the install fix and exit non-zero — never surface a raw
    ImportError traceback (CR-SAN-008 §S6/AC8)."""
    if not _MCP_AVAILABLE:
        print(_MISSING_MCP_MSG, file=sys.stderr)
        raise SystemExit(1)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
