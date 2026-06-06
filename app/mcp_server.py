"""app/mcp_server.py — Sandesh MCP server (stdio).

A thin adapter exposing Sandesh's verbs as MCP tools over stdio. It owns no business
logic — each tool resolves the project store and delegates to `sandesh_db.*` (the same
role `cli.py` plays for the terminal). The wake path stays the standalone `notify`
watcher; this server never re-invokes a sleeping agent (PRD §6).

`mcp` is the only third-party dependency and is imported ONLY here — the CLI path stays
stdlib-only. Run it via the venv interpreter (the `sandesh-mcp` wrapper); see install.sh.

Phase 2 / CR-SAN-001 (foundation + sandesh_setup). Later CRs add the remaining tools.
"""

import os
import sys

# Make the sibling library importable whether run as a script (app/ is sys.path[0])
# or imported as a module in tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandesh_db

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("sandesh")


def _resolve_project(project_id=None):
    """The passed `project_id`, else `$SANDESH_PROJECT` (D4) — the same precedence the
    CLI uses. Raises ValueError when neither is set."""
    project = project_id or os.environ.get("SANDESH_PROJECT")
    if not project:
        raise ValueError(
            "project_id is required (pass project_id or set $SANDESH_PROJECT)")
    return project


# Used by CR-SAN-002/003 tools that need a DB connection; sandesh_setup needs only
# the project (via _resolve_project), so it has no production caller in this CR.
def _ctx(project_id=None):
    """(project, store, con) for tools that need a DB connection — mirrors
    `cli.py::_ctx`. `project_id` falls back to `$SANDESH_PROJECT` (D4)."""
    project = _resolve_project(project_id)
    store = sandesh_db.store_dir(project)
    con = sandesh_db.connect(store)
    return project, store, con


@mcp.tool()
def sandesh_setup(project_id: str | None = None) -> str:
    """Provision a project's Sandesh store (idempotent). Returns the store dir path.

    project_id falls back to $SANDESH_PROJECT if omitted.
    """
    try:
        return sandesh_db.setup(_resolve_project(project_id))
    except (ValueError, PermissionError) as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def sandesh_addressbook(project_id: str | None = None) -> list[dict]:
    """List the project's addressbook entries. Returns list[dict].

    project_id falls back to $SANDESH_PROJECT if omitted.
    """
    con = None
    try:
        _project, _store, con = _ctx(project_id)
        return sandesh_db.addressbook(con)
    except (ValueError, PermissionError) as e:
        raise ToolError(str(e)) from e
    finally:
        if con is not None:
            con.close()


@mcp.tool()
def sandesh_inbox(
    project_id: str | None = None,
    recipient: str = "",
    unread_only: bool = True,
) -> list[dict]:
    """List a recipient's inbox messages. Returns list[dict].

    project_id falls back to $SANDESH_PROJECT if omitted.
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
    project_id: str | None = None,
    recipient: str = "",
    mark: bool = True,
) -> list[dict]:
    """Fetch a recipient's messages (with bodies). Returns list[dict].

    project_id falls back to $SANDESH_PROJECT if omitted.
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


@mcp.tool()
def sandesh_thread(project_id: str | None = None, msg_id: int = 0) -> list[dict]:
    """Walk a message's reply thread (root to leaf). Returns list[dict].

    project_id falls back to $SANDESH_PROJECT if omitted.
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


def main():
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
