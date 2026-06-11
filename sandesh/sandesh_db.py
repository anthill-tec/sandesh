"""sandesh_db.py — Sandesh: a standalone inter-orchestrator messaging store.

'Sandesh' (संदेश, Sanskrit/Hindi: *message / dispatch*) is a SQLite-backed maildir
for cooperating agent/orchestrator sessions. It is **standalone** (pure Python
stdlib — no third-party deps) and **multi-project**: all projects share ONE
global DB (WAL) under the XDG data dir; every call carries a `project_id` that
scopes it (enrolled in the `project` tracker table) and routes body files to
that project's folder.

  <data_home>/sandesh/sandesh.db                                 (the ONE global DB, WAL — all projects;
                                                                  data_home = $XDG_DATA_HOME or ~/.local/share)
  <data_home>/sandesh/projects/<project_id>/                     (per-project body folder)
  <data_home>/sandesh/projects/<project_id>/messages/msg-<id>.md

`body_path` is stored as a FULL absolute path.

Model — five tables:
  address            the addressbook (durable identity; '<Orchestrator> - <Project>')
  message            the envelope (subject REQUIRED; body_path NULL = subject-only)
  message_recipient  per-message addressees (role 'to'/'cc' + per-recipient read_at)
  notifier           per-session poller liveness (pid/token/heartbeat/tombstone)
  project            the project tracker (state: active | archived | tombstoned)

Semantics:
  - To wakes / Cc silent     notify fires only on role='to'; fetch pulls to+cc.
  - all-tracks broadcast      expands to the sender's project's active addresses
                               minus the sender (never crosses projects).
  - cross-project grant        sends to another project need the admin's per-project
                               grant (grant_xproj/revoke_xproj); denied otherwise.
  - per-recipient read         read_at lives on message_recipient, not the message.
  - keep history              nothing deleted; read_at (per recipient) is the only "seen" signal.
  - subject-only               body_path NULL → the subject IS the content.
  - reply threading            message.in_reply_to links a reply to its parent.
  - crash-safe liveness        notifier reaped via dead-pid / stale-heartbeat.
  - cooperative eviction       tombstone flag → the poller self-terminates.
  - validated addresses        '<Orchestrator> - <Project>'; project must match project_id.
"""

import os
import re
import shutil
import time

DB_FILE = "sandesh.db"
MESSAGES_DIR = "messages"
HEARTBEAT_STALE_SECS = 60             # a notifier silent longer than this is presumed dead
POLL_FLOOR_SECS = 3                   # minimum watcher poll interval (seconds)
DEFAULT_POLL_SECS = 10                # default watcher poll interval (seconds)
BROADCAST = "all-tracks"              # reserved recipient keyword (not a real address)

ADDRESS_RE = re.compile(r"^(?P<orch>Mainline|Track \d+) - (?P<proj>[A-Za-z][A-Za-z0-9_]*)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS address (
    address       TEXT PRIMARY KEY,                 -- '<Orchestrator> - <Project>'  (unique → rejects dupes)
    kind          TEXT,                             -- 'mainline' | 'track'
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,    -- soft-delete (history-safe)
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    registered_by TEXT,
    project       TEXT                              -- the address's <Project> part (exact-match scoping key)
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_addr   TEXT NOT NULL,
    subject     TEXT NOT NULL,                      -- always present (the minimal content)
    kind        TEXT,                               -- request | directive | fyi | reply
    in_reply_to INTEGER REFERENCES message(id),     -- thread link (NULL = top-level)
    body_path   TEXT,                               -- NULL = subject-only; else FULL path to messages/msg-<id>.md
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS message_recipient (
    message_id INTEGER NOT NULL REFERENCES message(id),
    recipient  TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'to',          -- 'to' (wakes) | 'cc' (silent)
    read_at    TEXT,                                -- NULL = unread (per recipient!)
    PRIMARY KEY (message_id, recipient)
);
CREATE TABLE IF NOT EXISTS notifier (
    recipient    TEXT PRIMARY KEY,                  -- one live poller per address (dedup key)
    pid          INTEGER,
    token        TEXT,                              -- uuid per launch (guards PID reuse)
    host         TEXT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    tombstone    BOOLEAN NOT NULL DEFAULT FALSE     -- 1 = shutdown requested (cooperative eviction)
);
CREATE TABLE IF NOT EXISTS project (
    project_id    TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active','archived','tombstoned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at   TEXT,
    tombstoned_at TEXT,
    xproj_granted_at TEXT,                              -- NULL = cross-project not granted
    xproj_granted_by TEXT                               -- admin identity that granted it
);
CREATE TABLE IF NOT EXISTS admin (
    id          INTEGER PRIMARY KEY CHECK (id = 1),     -- single row, enforced
    name        TEXT NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(subject, body);
"""


# --------------------------------------------------------------------------- #
# store location + connection + provisioning

def data_home():
    """XDG data home — $XDG_DATA_HOME or ~/.local/share."""
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def root_dir():
    """The Sandesh root — <data_home>/sandesh (holds app/, bin/, projects/)."""
    return os.path.join(data_home(), "sandesh")


def store_dir(project_id):
    """A project's data store dir — <data_home>/sandesh/projects/<project_id>/."""
    if not project_id:
        raise ValueError("project_id is required")
    return os.path.join(root_dir(), "projects", project_id)


def db_path():
    """The global Sandesh DB file — <data_home>/sandesh/sandesh.db."""
    return os.path.join(root_dir(), DB_FILE)


def connect():
    """Open (creating tables if absent) the global Sandesh DB at db_path(), WAL mode."""
    import sqlite3
    os.makedirs(root_dir(), exist_ok=True)
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    con.commit()
    return con


def setup(project_id):
    """Provision a project: enroll it in the tracker (INSERT 'active' if absent;
    no-op if already active/archived; refuse a tombstoned id) and create its
    messages/ body dir. Idempotent — safe to re-run. Returns the store dir."""
    store = store_dir(project_id)                 # validates project_id non-empty
    con = connect()
    try:
        state = project_state(con, project_id)
        if state == "tombstoned":
            raise ValueError("project id retired (tombstoned) — choose a new id")
        if state is None:
            con.execute(
                "INSERT INTO project (project_id, state) VALUES (?, 'active')",
                (project_id,))
            con.commit()
    finally:
        con.close()
    os.makedirs(os.path.join(store, MESSAGES_DIR), exist_ok=True)
    return store


def list_projects(include_tombstoned=False):
    """Enrolled project_ids from the tracker, sorted — active+archived by default;
    tombstoned ids only with include_tombstoned=True."""
    sql = "SELECT project_id FROM project"
    if not include_tombstoned:
        sql += " WHERE state != 'tombstoned'"
    sql += " ORDER BY project_id"
    con = connect()
    try:
        return [row["project_id"] for row in con.execute(sql).fetchall()]
    finally:
        con.close()


def project_state(con, project_id):
    """The tracker state for a project — 'active' | 'archived' | 'tombstoned' —
    or None if it has never been enrolled."""
    row = con.execute(
        "SELECT state FROM project WHERE project_id=?", (project_id,)).fetchone()
    return row["state"] if row is not None else None


def _require_active_project(con, project_id):
    """Tracker-state guard (CR-SAN-023 §S3): the project must be enrolled AND
    active. Raises a distinct ValueError per state — unknown / archived /
    tombstoned — so callers surface exact, actionable errors."""
    state = project_state(con, project_id)
    if state is None:
        raise ValueError(f"unknown project '{project_id}'")
    if state == "archived":
        raise ValueError(f"project '{project_id}' is archived")
    if state == "tombstoned":
        raise ValueError(f"project '{project_id}' is tombstoned")


# --------------------------------------------------------------------------- #
# admin + cross-project grant (CR-SAN-023 §S2 / §S2b)

def assign_admin(con, name):
    """Assign the Sandesh admin (single row, id=1 — enforced by the schema CHECK).
    Empty table → INSERT; same name already stored → no-op; a DIFFERENT stored
    name → ValueError (never silently re-assigns). Install-time only — there is
    deliberately NO CLI/MCP surface for this (PRD O3)."""
    row = con.execute("SELECT name FROM admin WHERE id=1").fetchone()
    if row is not None:
        if row["name"] == name:
            return
        raise ValueError("admin already assigned — refusing to silently re-assign")
    con.execute("INSERT INTO admin (id, name) VALUES (1, ?)", (name,))
    con.commit()


def admin_name(con):
    """The assigned Sandesh admin name (str), or None when no admin is assigned."""
    row = con.execute("SELECT name FROM admin WHERE id=1").fetchone()
    return row["name"] if row is not None else None


def _require_admin(con, by, action="grant/revoke cross-project access"):
    """Guard for admin-only operations: `by` must equal the stored admin name."""
    stored = admin_name(con)
    if stored is None:
        raise PermissionError(
            "no admin assigned — re-run install.sh with $SANDESH_ADMIN")
    if by != stored:
        raise PermissionError(f"only the Sandesh admin may {action}")


def grant_xproj(con, project_id, by):
    """Grant cross-project sending to a project (admin-only). Idempotent: an
    already-granted project keeps its original timestamp + grantor. Requires an
    ACTIVE project (CR-SAN-024 DEC-E): archived/tombstoned are refused."""
    _require_admin(con, by)
    _require_active_project(con, project_id)
    con.execute(
        "UPDATE project SET xproj_granted_at=datetime('now'), xproj_granted_by=? "
        "WHERE project_id=? AND xproj_granted_at IS NULL",
        (by, project_id))
    con.commit()


def revoke_xproj(con, project_id, by):
    """Revoke a project's cross-project grant (admin-only, project-wide — every
    participant loses access at once). Idempotent on an ungranted project.
    Requires an ACTIVE project (CR-SAN-024 DEC-E): lifecycle transitions never
    touch the grant columns, so archived/tombstoned revokes are refused."""
    _require_admin(con, by)
    _require_active_project(con, project_id)
    con.execute(
        "UPDATE project SET xproj_granted_at=NULL, xproj_granted_by=NULL "
        "WHERE project_id=?",
        (project_id,))
    con.commit()


def xproj_granted(con, project_id):
    """True iff the project currently holds the cross-project grant."""
    row = con.execute(
        "SELECT xproj_granted_at FROM project WHERE project_id=?",
        (project_id,)).fetchone()
    return bool(row is not None and row["xproj_granted_at"] is not None)


# --------------------------------------------------------------------------- #
# address book

def validate_address(addr, project=None):
    """Enforce '<Orchestrator> - <Project>'. Returns (orchestrator, project) or raises."""
    m = ADDRESS_RE.match(addr or "")
    if not m:
        raise ValueError(
            f"bad address {addr!r}: expected '<Orchestrator> - <Project>', "
            f"e.g. 'Track 2 - Nai' or 'Mainline - Nai' "
            f"(Orchestrator = 'Mainline' or 'Track <N>')")
    if project and m.group("proj") != project:
        raise ValueError(
            f"address project {m.group('proj')!r} != project_id {project!r}")
    return m.group("orch"), m.group("proj")


def register(con, addr, kind=None, display_name=None, by=None, project=None):
    """Self-register an address. Rejects an already-active duplicate; reactivates a
    previously-unregistered one; otherwise inserts."""
    _orch, proj = validate_address(addr, project)
    _require_active_project(con, proj)       # §S3: enrolled + active before any write
    row = con.execute("SELECT active FROM address WHERE address=?", (addr,)).fetchone()
    if row is not None:
        if row["active"]:
            raise ValueError(f"address already registered: {addr}")
        con.execute(
            "UPDATE address SET active=TRUE, kind=COALESCE(?,kind), "
            "display_name=COALESCE(?,display_name), registered_at=datetime('now'), "
            "registered_by=?, project=? WHERE address=?",
            (kind, display_name, by or addr, proj, addr))
    else:
        con.execute(
            "INSERT INTO address (address, kind, display_name, registered_by, project) "
            "VALUES (?,?,?,?,?)",
            (addr, kind, display_name, by or addr, proj))
    con.commit()


def deactivate(con, addr):
    """Soft-delete (active=FALSE). History retained; excluded from sends + all-tracks."""
    con.execute("UPDATE address SET active=FALSE WHERE address=?", (addr,))
    con.commit()


def is_active(con, addr):
    r = con.execute("SELECT active FROM address WHERE address=?", (addr,)).fetchone()
    return bool(r and r["active"])


def addressbook(con, project):
    """The project's addresses (active first), each annotated with live-notifier status."""
    rows = con.execute(
        "SELECT * FROM address WHERE project=? ORDER BY active DESC, address",
        (project,)).fetchall()
    return [{
        "address": r["address"], "kind": r["kind"],
        "active": bool(r["active"]), "registered_at": r["registered_at"],
        "listening": notifier_live(con, r["address"]) is not None,
    } for r in rows]


def active_addresses(con, project):
    """The project's active addresses, sorted — the `all-tracks` expansion pool."""
    return [r["address"] for r in con.execute(
        "SELECT address FROM address WHERE active=TRUE AND project=? ORDER BY address",
        (project,)).fetchall()]


def _address_project(con, addr):
    """The project an address belongs to — its address-row `project` column, falling
    back to the parsed '<Project>' part when the row is absent or unpopulated."""
    row = con.execute("SELECT project FROM address WHERE address=?", (addr,)).fetchone()
    if row is not None and row["project"]:
        return row["project"]
    _orch, proj = validate_address(addr)
    return proj


# --------------------------------------------------------------------------- #
# sending

def _expand_recipients(con, to_list, cc_list, sender, sender_project):
    """(recipient, role) pairs: expand `all-tracks` (within the sender's project),
    drop the sender, dedup with To winning over Cc, preserving To-then-Cc order."""
    def expand(lst):
        out = []
        for a in (lst or []):
            out.extend(active_addresses(con, sender_project) if a == BROADCAST else [a])
        return out

    tos = [a for a in expand(to_list) if a != sender]
    ccs = [a for a in expand(cc_list) if a != sender]
    role = {}
    for a in ccs:
        role.setdefault(a, "cc")
    for a in tos:
        role[a] = "to"                      # To always wins over Cc
    seen, ordered = set(), []
    for a in tos + ccs:
        if a not in seen:
            seen.add(a)
            ordered.append((a, role[a]))
    return ordered


def send(con, store, from_addr, to=None, cc=None, subject="", kind=None,
         body_text=None, in_reply_to=None, project=None, validate=True):
    """Insert a message. `subject` is mandatory; `body_text` None/'' ⇒ subject-only
    (no file). Returns the new message id."""
    if not subject:
        raise ValueError("subject is required (it is the minimal message content)")
    to, cc = (to or []), (cc or [])
    sender_proj = project
    if validate:
        _orch, sender_proj = validate_address(from_addr, project)
        _require_active_project(con, sender_proj)            # §S3 sender side
        for a in to + cc:                    # complete for ALL recipients BEFORE any insert
            if a == BROADCAST:
                continue
            if not is_active(con, a):
                raise ValueError(f"unknown or inactive recipient: {a!r}")
            rp = _address_project(con, a)
            if rp != sender_proj:                            # §S2 grant gate, then §S3
                if not xproj_granted(con, sender_proj):
                    raise ValueError(
                        f"cross-project sending not approved for project "
                        f"'{sender_proj}' — ask the Sandesh admin")
                _require_active_project(con, rp)
    recips = _expand_recipients(con, to, cc, from_addr, sender_proj)
    if not recips:
        raise ValueError("no recipients (after excluding the sender)")

    cur = con.execute(
        "INSERT INTO message (from_addr, subject, kind, in_reply_to) VALUES (?,?,?,?)",
        (from_addr, subject, kind, in_reply_to))
    mid = cur.lastrowid

    if body_text:
        msg_dir = os.path.abspath(os.path.join(store, MESSAGES_DIR))
        os.makedirs(msg_dir, exist_ok=True)
        full = os.path.join(msg_dir, f"msg-{mid}.md")          # FULL absolute body path
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body_text)
        con.execute("UPDATE message SET body_path=? WHERE id=?", (full, mid))

    # FTS index entry (CR-SAN-027 §S2): rowid = message id; subject-only
    # messages index the subject with an empty body. Inside the same
    # transaction as the message row — committed (or rolled back) together.
    con.execute(
        "INSERT INTO message_fts (rowid, subject, body) VALUES (?,?,?)",
        (mid, subject, body_text or ""))

    con.executemany(
        "INSERT INTO message_recipient (message_id, recipient, role) VALUES (?,?,?)",
        [(mid, r, role) for r, role in recips])
    con.commit()
    return mid


def reply(con, store, parent_id, from_addr, subject=None, body_text=None,
          reply_all=False, kind="reply", project=None):
    """Reply to message <parent_id>: `to` defaults to the parent's sender, subject to
    'Re: …', in_reply_to links the thread. `reply_all` cc's the parent's other
    recipients."""
    parent = con.execute("SELECT * FROM message WHERE id=?", (parent_id,)).fetchone()
    if parent is None:
        raise ValueError(f"no such message #{parent_id}")
    to = [parent["from_addr"]]
    cc = []
    if reply_all:
        cc = [r["recipient"] for r in con.execute(
            "SELECT recipient FROM message_recipient WHERE message_id=?", (parent_id,)).fetchall()
            if r["recipient"] not in (from_addr, parent["from_addr"])]
    if not subject:
        s = parent["subject"]
        subject = s if s.lower().startswith("re:") else f"Re: {s}"
    return send(con, store, from_addr, to, cc, subject, kind, body_text,
                in_reply_to=parent_id, project=project)


# --------------------------------------------------------------------------- #
# receiving

THREAD_HOLE_WARNING = "incomplete chain — message(s) removed (project tombstoned)"


def _tombstoned_projects(con):
    """The set of tombstoned project ids — the per-call read-filter set
    (CR-SAN-024 §S2 / DRIFT-3). Computed ONCE per read call; empty in the
    common no-tombstones case, which lets readers skip filtering entirely."""
    return {r["project_id"] for r in con.execute(
        "SELECT project_id FROM project WHERE state='tombstoned'")}


def _is_tombstoned_sender(con, addr, tombstoned):
    """True iff `addr`'s project is in the `tombstoned` set. Resolution via
    _address_project — the suffix fallback matters because a tombstoned
    project's address rows were purged (DRIFT-3)."""
    if not tombstoned:
        return False
    return _address_project(con, addr) in tombstoned


_TS_FULL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_TS_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_ts(value, end_of_day=False):
    """Normalize a since/until bound (CR-SAN-026 §S1): a full
    'YYYY-MM-DD HH:MM:SS' passes through; a date-only 'YYYY-MM-DD' stays as-is
    for a lower bound but extends to inclusive end-of-day ('… 23:59:59') when
    `end_of_day` (an until) — a lexicographic compare would otherwise exclude
    the entire named day. Anything else raises ValueError."""
    if _TS_FULL_RE.match(value):
        return value
    if _TS_DATE_RE.match(value):
        return f"{value} 23:59:59" if end_of_day else value
    raise ValueError(
        f"invalid timestamp {value!r} — expected 'YYYY-MM-DD' or "
        f"'YYYY-MM-DD HH:MM:SS'")


def inbox(con, recipient, unread_only=True, *, sender=None, sender_project=None,
          kind=None, since=None, until=None, subject_like=None):
    """The recipient's messages (to + cc), oldest first. Rows whose SENDER's
    project is tombstoned are hidden (CR-SAN-024 §S2) — filtered python-side
    against the per-call tombstoned set, BEFORE the optional filters; archived
    projects' traffic displays fully.

    Composable server-side filters (CR-SAN-026 §S1), None = no constraint:
    `sender` exact-matches from_addr; `sender_project` matches the sender's
    project (via the _address_project seam, python-side like the tombstone
    rule); `kind` exact-matches; `since`/`until` bound created_at inclusively
    (see _normalize_ts); `subject_like` is a case-insensitive literal
    substring (LIKE wildcards in the value are escaped)."""
    q = ("SELECT m.id, m.from_addr, m.subject, m.kind, m.in_reply_to, "
         "m.body_path, m.created_at, r.role, r.read_at "
         "FROM message m JOIN message_recipient r ON r.message_id = m.id "
         "WHERE r.recipient = ? ")
    params = [recipient]
    if unread_only:
        q += "AND r.read_at IS NULL "
    if sender is not None:
        q += "AND m.from_addr = ? "
        params.append(sender)
    if kind is not None:
        q += "AND m.kind = ? "
        params.append(kind)
    if since is not None:
        q += "AND m.created_at >= ? "
        params.append(_normalize_ts(since))
    if until is not None:
        q += "AND m.created_at <= ? "
        params.append(_normalize_ts(until, end_of_day=True))
    if subject_like is not None:
        q += "AND lower(m.subject) LIKE ? ESCAPE '\\' "
        literal = (subject_like.lower()
                   .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
        params.append(f"%{literal}%")
    q += "ORDER BY m.created_at, m.id"
    rows = con.execute(q, params).fetchall()
    tombstoned = _tombstoned_projects(con)
    if tombstoned:                        # hidden traffic never matches anything
        rows = [r for r in rows
                if not _is_tombstoned_sender(con, r["from_addr"], tombstoned)]
    if sender_project is not None:
        rows = [r for r in rows
                if _address_project(con, r["from_addr"]) == sender_project]
    return rows


def unread_to(con, recipient):
    """Ids of unread messages where `recipient` is a 'to' — the NOTIFY/wake filter
    (Cc is deliberately excluded: cc never wakes, it's swept up by fetch).
    Applies the §S2 tombstoned-sender filter — a watcher must not wake for
    invisible mail."""
    rows = con.execute(
        "SELECT m.id, m.from_addr "
        "FROM message m JOIN message_recipient r ON r.message_id = m.id "
        "WHERE r.recipient = ? AND r.role = 'to' AND r.read_at IS NULL ORDER BY m.id",
        (recipient,)).fetchall()
    tombstoned = _tombstoned_projects(con)
    if not tombstoned:
        return [r["id"] for r in rows]
    return [r["id"] for r in rows
            if not _is_tombstoned_sender(con, r["from_addr"], tombstoned)]


def mark_read(con, recipient, ids):
    con.executemany(
        "UPDATE message_recipient SET read_at = datetime('now') "
        "WHERE message_id = ? AND recipient = ? AND read_at IS NULL",
        [(i, recipient) for i in ids])
    con.commit()


def fetch(con, store, recipient, mark=True, *, sender=None, sender_project=None,
          kind=None, since=None, until=None, subject_like=None):
    """Consolidate this recipient's unread messages (to + cc) — bodies read from their
    FULL path, subject-only entries carry no body — and (default) mark them read.

    Rides the inbox filter params (CR-SAN-026 §S2): only the matching subset is
    rendered and (when `mark`) marked read — non-matching unread mail stays unread."""
    rows = inbox(con, recipient, unread_only=True, sender=sender,
                 sender_project=sender_project, kind=kind, since=since,
                 until=until, subject_like=subject_like)
    items = []
    for r in rows:
        body = None
        if r["body_path"]:
            path = r["body_path"]
            if not os.path.isabs(path):                    # legacy relative → resolve under store
                path = os.path.join(store, path)
            try:
                with open(path, encoding="utf-8") as fh:   # compiled from the full path
                    body = fh.read()
            except FileNotFoundError:
                body = f"(body file missing: {path})"
        parent = None
        if r["in_reply_to"]:
            p = con.execute("SELECT subject FROM message WHERE id=?", (r["in_reply_to"],)).fetchone()
            parent = (r["in_reply_to"], p["subject"] if p else "?")
        items.append({
            "id": r["id"], "from": r["from_addr"], "subject": r["subject"],
            "role": r["role"], "kind": r["kind"], "created_at": r["created_at"],
            "in_reply_to": parent, "body": body,
        })
    if mark and items:
        mark_read(con, recipient, [it["id"] for it in items])
    return items


def thread(con, msg_id):
    """The reply chain from the root down to msg_id (ascending by id).

    Nodes whose SENDER's project is tombstoned are replaced by a synthetic
    warning entry `{"warning": THREAD_HOLE_WARNING}` (CR-SAN-024 §S2);
    consecutive hidden nodes collapse to ONE warning entry. A requested
    msg_id that is itself from a tombstoned project yields a chain of just
    the warning entry (non-empty — the row exists, its sender is invisible)."""
    chain, cur = [], con.execute("SELECT * FROM message WHERE id=?", (msg_id,)).fetchone()
    while cur is not None:
        chain.append(cur)
        cur = (con.execute("SELECT * FROM message WHERE id=?", (cur["in_reply_to"],)).fetchone()
               if cur["in_reply_to"] else None)
    chain.reverse()
    tombstoned = _tombstoned_projects(con)
    if not tombstoned:                    # hot path: no tombstones → raw chain
        return chain
    out = []
    for node in chain:
        if _is_tombstoned_sender(con, node["from_addr"], tombstoned):
            if not (out and isinstance(out[-1], dict) and "warning" in out[-1]):
                out.append({"warning": THREAD_HOLE_WARNING})
        else:
            out.append(node)
    return out


# --------------------------------------------------------------------------- #
# notifier liveness (per-session poller registry)

def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, TypeError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def notifier_live(con, recipient):
    """The notifier row IF a poller is genuinely alive (fresh heartbeat + live pid),
    else None. Self-healing check that compensates for SIGKILL / crash."""
    r = con.execute("SELECT * FROM notifier WHERE recipient=?", (recipient,)).fetchone()
    if r is None:
        return None
    age = con.execute("SELECT (julianday('now') - julianday(?)) * 86400.0",
                      (r["heartbeat_at"],)).fetchone()[0]
    if age is not None and age > HEARTBEAT_STALE_SECS:
        return None
    if not _pid_alive(r["pid"]):
        return None
    return r


def notifier_acquire(con, recipient, pid, token, host):
    """(False, reason) if a notifier is already live for `recipient` (dedup);
    (True, 'acquired') after taking/refreshing the row otherwise."""
    live = notifier_live(con, recipient)
    if live is not None:
        return (False, f"another notifier already live for {recipient!r} (pid {live['pid']})")
    con.execute(
        "INSERT INTO notifier (recipient,pid,token,host,started_at,heartbeat_at,tombstone) "
        "VALUES (?,?,?,?,datetime('now'),datetime('now'),FALSE) "
        "ON CONFLICT(recipient) DO UPDATE SET pid=excluded.pid, token=excluded.token, "
        "host=excluded.host, started_at=datetime('now'), heartbeat_at=datetime('now'), "
        "tombstone=FALSE",
        (recipient, pid, token, host))
    con.commit()
    return (True, "acquired")


def notifier_heartbeat(con, recipient, token):
    con.execute("UPDATE notifier SET heartbeat_at=datetime('now') WHERE recipient=? AND token=?",
                (recipient, token))
    con.commit()


def notifier_check(con, recipient, token):
    """Per-iteration state for the poller: 'ok' | 'tombstoned' | 'evicted'."""
    r = con.execute("SELECT token, tombstone FROM notifier WHERE recipient=?", (recipient,)).fetchone()
    if r is None or r["token"] != token:
        return "evicted"
    if r["tombstone"]:
        return "tombstoned"
    return "ok"


def notifier_release(con, recipient, token):
    """Remove my row on clean exit — only if it is still mine (never clobber a successor)."""
    con.execute("DELETE FROM notifier WHERE recipient=? AND token=?", (recipient, token))
    con.commit()


def notifier_tombstone(con, recipient):
    """Request a cooperative shutdown of `recipient`'s live notifier."""
    con.execute("UPDATE notifier SET tombstone=TRUE WHERE recipient=?", (recipient,))
    con.commit()


def notifier_reap_if_stale(con, recipient):
    """Force-remove a dead/stale notifier row (fallback when a tombstone is ignored)."""
    if notifier_live(con, recipient) is None:
        con.execute("DELETE FROM notifier WHERE recipient=?", (recipient,))
        con.commit()
        return True
    return False


def unregister(con, recipient, requester, project=None):
    """Remove a participant. Auth: within a project, Mainline may remove anyone and
    anyone may remove self; a foreign project's address may NOT be removed.
    Live notifier → tombstone it, return ('tombstoned', pid); else reap stale, soft-delete,
    return ('unregistered', None)."""
    orch_req, req_proj = validate_address(requester, project)
    if recipient != requester and orch_req != "Mainline":
        raise PermissionError("only Mainline may remove another participant")
    if _address_project(con, recipient) != req_proj:
        raise PermissionError(
            f"cannot unregister {recipient!r}: it is not in project {req_proj!r} "
            f"(cross-project removal is not allowed)")
    live = notifier_live(con, recipient)
    if live is not None:
        notifier_tombstone(con, recipient)
        return ("tombstoned", live["pid"])
    notifier_reap_if_stale(con, recipient)
    deactivate(con, recipient)
    return ("unregistered", None)


# project lifecycle (CR-SAN-024 §S1)

def poll_interval():
    """The watcher poll interval in seconds — $SANDESH_POLL_SECONDS, default 10
    (also the fallback for a non-numeric value), floor 3. Canonical home
    (CR-SAN-024 DRIFT-5): `notify` delegates here, and `archive()`'s bounded
    eviction wait defaults to 2 poll cycles."""
    raw = os.environ.get("SANDESH_POLL_SECONDS")
    try:
        val = int(raw) if raw else DEFAULT_POLL_SECS
    except ValueError:
        val = DEFAULT_POLL_SECS
    return max(val, POLL_FLOOR_SECS)


def _require_project_mainline(project_id, by):
    """Authz guard for archive/unarchive (CR-SAN-024 §S3): `by` must validate
    to ('Mainline', project_id) — format-based, honor-system (the `unregister`
    house pattern). Raises PermissionError naming the requirement."""
    try:
        orch, proj = validate_address(by)
    except ValueError as exc:
        raise PermissionError(
            f"archive/unarchive of project '{project_id}' requires its own "
            f"Mainline ('Mainline - {project_id}'); got invalid address "
            f"{by!r}: {exc}") from exc
    if orch != "Mainline" or proj != project_id:
        raise PermissionError(
            f"only the project's own Mainline ('Mainline - {project_id}') may "
            f"archive/unarchive project '{project_id}'; got {by!r}")


def _evict_project_notifiers(con, project_id, *, force, wait_secs, op):
    """Cooperatively evict every live notifier of the project's addresses:
    notifier_tombstone → bounded wait of `wait_secs` (default 2 poll cycles) →
    notifier_reap_if_stale sweep. Raises RuntimeError (naming `op`) if a watcher
    stays live past the wait, unless `force`, which reaps the surviving row(s)
    anyway. Shared seam for archive() and tombstone_project()."""
    if wait_secs is None:
        wait_secs = 2 * poll_interval()
    addresses = [r["address"] for r in con.execute(
        "SELECT address FROM address WHERE project=?", (project_id,))]
    live = [a for a in addresses if notifier_live(con, a) is not None]
    for addr in live:
        notifier_tombstone(con, addr)
    if live:
        deadline = time.monotonic() + wait_secs
        while live and time.monotonic() < deadline:
            time.sleep(0.05)
            live = [a for a in live if notifier_live(con, a) is not None]
    for addr in addresses:
        notifier_reap_if_stale(con, addr)
    still_live = [a for a in addresses if notifier_live(con, a) is not None]
    if still_live:
        if not force:
            raise RuntimeError(
                f"cannot {op} project '{project_id}': live notifier(s) for "
                f"{still_live} ignored the tombstone past the {wait_secs}s wait "
                f"— retry, or pass force=True to reap them anyway")
        for addr in still_live:
            con.execute("DELETE FROM notifier WHERE recipient=?", (addr,))
        con.commit()


def _archive_guards(con, project_id, by):
    """State + authz guards shared by archive() and archive_preview() — the
    dry-run raises exactly the same errors as the real operation."""
    state = project_state(con, project_id)
    if state is None:
        raise ValueError(f"unknown project '{project_id}'")
    if state != "active":
        raise ValueError(f"project '{project_id}' is not active")
    _require_project_mainline(project_id, by)


def _unarchive_guards(con, project_id, by):
    """State + authz guards shared by unarchive() and unarchive_preview()."""
    state = project_state(con, project_id)
    if state is None:
        raise ValueError(f"unknown project '{project_id}'")
    if state != "archived":
        raise ValueError(f"project '{project_id}' is not archived")
    _require_project_mainline(project_id, by)


def _tombstone_guards(con, project_id, by):
    """State + authz guards shared by tombstone_project() and
    tombstone_preview(): archived-only (the two-step), super-admin-only `by`."""
    state = project_state(con, project_id)
    if state is None:
        raise ValueError(f"unknown project '{project_id}'")
    if state == "tombstoned":
        raise ValueError(f"project '{project_id}' is already tombstoned")
    if state == "active":
        raise ValueError(
            f"project '{project_id}' is active — archive it first")
    _require_admin(con, by, action="tombstone a project")


def _live_watchers(con, project_id):
    """The project's addresses whose notifier rows are genuinely live (fresh
    heartbeat + live pid) — the watchers an archive/tombstone would evict."""
    return [r["address"] for r in con.execute(
        "SELECT address FROM address WHERE project=?", (project_id,))
        if notifier_live(con, r["address"]) is not None]


def _internal_message_ids(con, project_id):
    """The internal-message id set (DRIFT-2 step 1), computed while the
    project's address rows still exist: internal = sender's project ==
    `project_id` AND no recipient row resolves to another project."""
    return [r["id"] for r in con.execute(
        "SELECT m.id FROM message m"
        "  JOIN address sa ON sa.address = m.from_addr"
        " WHERE sa.project = ?"
        "   AND NOT EXISTS ("
        "       SELECT 1 FROM message_recipient mr"
        "         JOIN address ra ON ra.address = mr.recipient"
        "        WHERE mr.message_id = m.id AND ra.project != ?)",
        (project_id, project_id))]


def archive(con, project_id, by, *, force=False, wait_secs=None):
    """Archive an active project (Mainline-only `by`): cooperatively evict every
    live notifier of its addresses (notifier_tombstone → bounded wait of
    `wait_secs`, default 2 poll cycles → notifier_reap_if_stale sweep), then set
    state='archived' + archived_at. Deletes NOTHING else. Refuses — state
    unchanged — if a watcher stays live past the wait, unless `force`, which
    reaps the surviving row(s) anyway."""
    _archive_guards(con, project_id, by)
    _evict_project_notifiers(
        con, project_id, force=force, wait_secs=wait_secs, op="archive")
    con.execute(
        "UPDATE project SET state='archived', archived_at=datetime('now') "
        "WHERE project_id=?", (project_id,))
    con.commit()


def unarchive(con, project_id, by):
    """Reactivate an archived project (Mainline-only `by`): state='active',
    archived_at cleared. Grant columns are untouched (CR-SAN-024 DEC-E) — a
    grant set while active survives the archive→unarchive round-trip."""
    _unarchive_guards(con, project_id, by)
    con.execute(
        "UPDATE project SET state='active', archived_at=NULL WHERE project_id=?",
        (project_id,))
    con.commit()


def tombstone_project(con, project_id, by, *, force=False, wait_secs=None):
    """Tombstone an ARCHIVED project (super-admin-only `by` — CR-SAN-024 §S1):
    evict any live notifiers (the archive() seam, same `force` semantics), then
    purge in DRIFT-2 order within ONE transaction:
      1. compute the internal message-id set WHILE the address rows still exist
         (internal = sender's project == project_id AND no recipient resolves
         to another project),
      2. delete those messages' message_recipient rows, then the message rows
         (cross-project rows AND their recipient rows — including this
         project's recipient rows on surviving messages — SURVIVE: PRD D6),
      3. delete the project's notifier + address rows,
      4. delete the projects/<id>/ folder entirely (bodies — T1
         content-dies-with-origin),
      5. set state='tombstoned' + tombstoned_at (the permanent marker row —
         created_at and xproj_granted_* untouched).
    Refusals (state/authz) change nothing."""
    _tombstone_guards(con, project_id, by)

    _evict_project_notifiers(
        con, project_id, force=force, wait_secs=wait_secs, op="tombstone")

    # DRIFT-2 step 1: internal ids computed while address rows still exist.
    internal_ids = _internal_message_ids(con, project_id)
    # FTS text destruction (CR-SAN-027 §S2 / T1): every message SENT BY this
    # project — internal AND surviving cross-project ones — loses its index
    # text copy (the body files die with the folder; the index must not
    # retain the text). Computed by sender BEFORE the address-row purge
    # (DRIFT-2 ordering); messages the project merely RECEIVED keep their
    # index rows (the content belongs to the sender's project).
    sent_ids = [r["id"] for r in con.execute(
        "SELECT m.id FROM message m JOIN address sa ON sa.address = m.from_addr"
        " WHERE sa.project = ?", (project_id,))]
    if sent_ids:
        placeholders = ",".join("?" * len(sent_ids))
        con.execute(
            f"DELETE FROM message_fts WHERE rowid IN ({placeholders})",
            sent_ids)
    # Step 2: internal message_recipient rows, then internal message rows.
    if internal_ids:
        placeholders = ",".join("?" * len(internal_ids))
        con.execute(
            f"DELETE FROM message_recipient WHERE message_id IN ({placeholders})",
            internal_ids)
        con.execute(
            f"DELETE FROM message WHERE id IN ({placeholders})", internal_ids)
    # Step 3: notifier rows (resolved via address) THEN the address rows.
    con.execute(
        "DELETE FROM notifier WHERE recipient IN "
        "(SELECT address FROM address WHERE project=?)", (project_id,))
    con.execute("DELETE FROM address WHERE project=?", (project_id,))
    # Step 4: the whole body folder (a missing dir is fine — already gone).
    shutil.rmtree(store_dir(project_id), ignore_errors=True)
    # Step 5: the permanent marker.
    con.execute(
        "UPDATE project SET state='tombstoned', tombstoned_at=datetime('now') "
        "WHERE project_id=?", (project_id,))
    con.commit()


def archive_preview(con, project_id, by):
    """Dry-run report for archive() (CR-SAN-024 §S3): runs the SAME state +
    authz guards (errors still raise), then returns the list of live-watcher
    addresses an actual archive would evict. Writes nothing."""
    _archive_guards(con, project_id, by)
    return _live_watchers(con, project_id)


def unarchive_preview(con, project_id, by):
    """Dry-run for unarchive(): the same guards (errors still raise); a clean
    return means the project would become active. Writes nothing."""
    _unarchive_guards(con, project_id, by)


def tombstone_preview(con, project_id, by):
    """Dry-run report for tombstone_project() (CR-SAN-024 AC7): the same state
    + authz guards, then — WITHOUT writing — what an actual tombstone would
    destroy: internal message rows purged, body files deleted, and the
    cross-project messages whose bodies would be lost (their rows survive).
    Returns {'internal_messages', 'body_files', 'cross_project_messages'}."""
    _tombstone_guards(con, project_id, by)
    internal = len(_internal_message_ids(con, project_id))
    sent_total = con.execute(
        "SELECT COUNT(*) FROM message m JOIN address sa ON sa.address = m.from_addr"
        " WHERE sa.project = ?", (project_id,)).fetchone()[0]
    messages_dir = os.path.join(store_dir(project_id), "messages")
    body_files = len(os.listdir(messages_dir)) if os.path.isdir(messages_dir) else 0
    return {
        "internal_messages": internal,
        "body_files": body_files,
        "cross_project_messages": sent_total - internal,
    }


# one-time legacy-store consolidation (CR-SAN-022 §S3)

def _legacy_address_project(row, has_project_col):
    """The project for a legacy address row — its `project` column when present
    and populated, else derived from the address text exactly like the 0003
    backfill (the suffix after the FIRST ' - ')."""
    if has_project_col and row["project"]:
        return row["project"]
    addr = row["address"]
    return addr.split(" - ", 1)[1] if " - " in addr else None


def _consolidate_store(con, project_id, legacy_path):
    """Import one legacy per-project store into the global DB (one transaction),
    then rename it to `<legacy_path>.pre-global`. Returns the summary dict."""
    import sqlite3
    src = sqlite3.connect(legacy_path)
    src.row_factory = sqlite3.Row
    addresses_imported = messages_imported = 0
    try:
        addr_cols = {r["name"] for r in src.execute("PRAGMA table_info(address)")}
        has_project_col = "project" in addr_cols
        with con:                                   # one global-DB transaction per store
            # addresses — verbatim, skipping ones the global DB already has;
            # `project` populated from the column when present, else derived.
            for r in src.execute("SELECT * FROM address"):
                if con.execute("SELECT 1 FROM address WHERE address=?",
                               (r["address"],)).fetchone() is not None:
                    continue
                con.execute(
                    "INSERT INTO address (address, kind, display_name, active, "
                    "registered_at, registered_by, project) VALUES (?,?,?,?,?,?,?)",
                    (r["address"], r["kind"], r["display_name"], r["active"],
                     r["registered_at"], r["registered_by"],
                     _legacy_address_project(r, has_project_col)))
                addresses_imported += 1

            # messages — explicit columns (tolerates a legacy `status` column by
            # never selecting it); first pass inserts with in_reply_to NULL and
            # builds the old→new id map, second pass relinks via the map
            # (dangling refs stay NULL). body_path is absolute → files unmoved.
            id_map = {}
            rows = src.execute(
                "SELECT id, from_addr, subject, kind, in_reply_to, body_path, "
                "created_at FROM message ORDER BY id").fetchall()
            for r in rows:
                cur = con.execute(
                    "INSERT INTO message (from_addr, subject, kind, in_reply_to, "
                    "body_path, created_at) VALUES (?,?,?,NULL,?,?)",
                    (r["from_addr"], r["subject"], r["kind"],
                     r["body_path"], r["created_at"]))
                id_map[r["id"]] = cur.lastrowid
                messages_imported += 1
            for r in rows:
                if r["in_reply_to"] is None:
                    continue
                new_parent = id_map.get(r["in_reply_to"])
                if new_parent is not None:
                    con.execute("UPDATE message SET in_reply_to=? WHERE id=?",
                                (new_parent, id_map[r["id"]]))

            # recipients — remapped message_id; recipient/role/read_at verbatim.
            for r in src.execute(
                    "SELECT message_id, recipient, role, read_at FROM message_recipient"):
                new_mid = id_map.get(r["message_id"])
                if new_mid is None:                  # orphan row (no such message)
                    continue
                con.execute(
                    "INSERT INTO message_recipient (message_id, recipient, role, read_at) "
                    "VALUES (?,?,?,?)",
                    (new_mid, r["recipient"], r["role"], r["read_at"]))

            # notifier rows are deliberately NOT imported — watchers re-acquire.

            # enroll the project active if absent
            if project_state(con, project_id) is None:
                con.execute(
                    "INSERT INTO project (project_id, state) VALUES (?, 'active')",
                    (project_id,))
    finally:
        src.close()
    os.rename(legacy_path, legacy_path + ".pre-global")  # after the committed import
    return {"project_id": project_id,
            "messages_imported": messages_imported,
            "addresses_imported": addresses_imported}


def consolidate():
    """One-time import of legacy per-project stores into the global DB.

    Scans <root_dir()>/projects/*/sandesh.db; for each legacy store: imports
    address rows (project populated), message rows with remapped ids (reply
    chains relinked, dangling in_reply_to → NULL, body_path verbatim — files
    unmoved), message_recipient rows with remapped message_id, enrolls the
    project 'active', then renames the legacy DB → sandesh.db.pre-global.
    Idempotent: renamed stores are skipped on re-run; notifier rows are never
    imported. Returns a list of per-project summary dicts
    ({'project_id', 'messages_imported', 'addresses_imported'})."""
    projects_dir = os.path.join(root_dir(), "projects")
    summaries = []
    if not os.path.isdir(projects_dir):
        return summaries
    con = connect()
    try:
        for project_id in sorted(os.listdir(projects_dir)):
            legacy = os.path.join(projects_dir, project_id, DB_FILE)
            if not os.path.isfile(legacy):           # .pre-global-only dirs: no-op
                continue
            summaries.append(_consolidate_store(con, project_id, legacy))
    finally:
        con.close()
    return summaries
