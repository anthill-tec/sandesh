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


if _MCP_AVAILABLE:
    SANDESH_INSTRUCTIONS = """Sandesh relays messages between cooperating agent orchestrators in the Model-B
workflow: a Mainline coordinator session plus parallel Track worker sessions that
cannot talk to each other directly. Addresses represent orchestrators, formatted
'Mainline - <Project>' or 'Track N - <Project>'.

Two channels, one boundary: this MCP server carries the VERBS (send, reply, fetch,
inbox, register, ...). The WAKE is out-of-band and is NOT an MCP tool — an MCP server
cannot re-invoke a sleeping agent. To be woken, an agent launches the standalone
`sandesh notify` process via its host's `run_in_background` mechanism; when mail
addressed to it arrives, that background watcher exits and the host wakes the agent,
which then calls sandesh_fetch.

Lifecycle without a status field: reading a message (fetch) means "received and now
being acted on"; sending a reply means done — reply signals completion of the
requested work. See the sandesh://usage resource for full Model-B scenarios."""


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
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
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
    ) -> list[dict]:
        """List an address's messages — a quick triage glance at what's pending. Unread by
        default; pass unread_only=False to include read messages too.

        Called by an address on its own mailbox when it wants to see what's waiting WITHOUT
        consuming it — unlike sandesh_fetch, this does NOT mark anything read. Read-only.
        Returns list[dict].
        """
        con = None
        try:
            _project, _store, con = _ctx(project_id)
            return [dict(r) for r in sandesh_db.inbox(con, recipient, unread_only)]
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool()
    def sandesh_fetch(
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
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
    ) -> list[dict]:
        """The real read: consolidate an address's unread messages (both `to` and `cc`) into
        one view — bodies read from file, subject-only entries shown as just the subject —
        and mark them read. This mutates per-recipient read state (so it is NOT read-only).

        This is what a session calls right after its `notify` watcher wakes it. Reading a
        message means "received and now being acted on" — the waiting sender can observe that
        read state. Pass mark=False to peek without consuming. Returns list[dict] (with
        thread refs).
        """
        con = None
        try:
            _project, store, con = _ctx(project_id)
            return sandesh_db.fetch(con, store, recipient, mark)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def sandesh_thread(
        project_id: Annotated[
            str | None,
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
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
            _project, _store, con = _ctx(project_id)
            return [dict(r) for r in sandesh_db.thread(con, msg_id)]
        except (ValueError, PermissionError) as e:
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
                  "store. Falls back to $SANDESH_PROJECT if omitted."),
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
        body_text: Annotated[
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
            project, store, con = _ctx(project_id)
            if isinstance(to, str):
                to = [to]
            if isinstance(cc, str):
                cc = [cc]
            return sandesh_db.send(
                con, store, from_addr, to, cc, subject, kind,
                body_text=body_text, project=project)
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
            Field(description="The project store router. Falls back to $SANDESH_PROJECT "
                  "if omitted."),
        ] = None,
        subject: Annotated[
            str | None,
            Field(description="Optional reply subject; defaults to 'Re: <parent subject>'. "
                  "Often the whole message (subject-only) when signalling completion."),
        ] = None,
        body_text: Annotated[
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
            project, store, con = _ctx(project_id)
            return sandesh_db.reply(
                con, store, parent_id, from_addr, subject, body_text, project=project)
        except (ValueError, PermissionError) as e:
            raise ToolError(str(e)) from e
        finally:
            if con is not None:
                con.close()


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
