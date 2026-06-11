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
                               minus the sender (cross-project send is blocked
                               until CR-SAN-023's grant).
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

DB_FILE = "sandesh.db"
MESSAGES_DIR = "messages"
HEARTBEAT_STALE_SECS = 60             # a notifier silent longer than this is presumed dead
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


def _require_admin(con, by):
    """Guard for admin-only operations: `by` must equal the stored admin name."""
    stored = admin_name(con)
    if stored is None:
        raise PermissionError(
            "no admin assigned — re-run install.sh with $SANDESH_ADMIN")
    if by != stored:
        raise PermissionError(
            "only the Sandesh admin may grant/revoke cross-project access")


def grant_xproj(con, project_id, by):
    """Grant cross-project sending to a project (admin-only). Idempotent: an
    already-granted project keeps its original timestamp + grantor."""
    _require_admin(con, by)
    if project_state(con, project_id) is None:
        raise ValueError(f"unknown project '{project_id}'")
    con.execute(
        "UPDATE project SET xproj_granted_at=datetime('now'), xproj_granted_by=? "
        "WHERE project_id=? AND xproj_granted_at IS NULL",
        (by, project_id))
    con.commit()


def revoke_xproj(con, project_id, by):
    """Revoke a project's cross-project grant (admin-only, project-wide — every
    participant loses access at once). Idempotent on an ungranted project."""
    _require_admin(con, by)
    if project_state(con, project_id) is None:
        raise ValueError(f"unknown project '{project_id}'")
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

def inbox(con, recipient, unread_only=True):
    q = ("SELECT m.id, m.from_addr, m.subject, m.kind, m.in_reply_to, "
         "m.body_path, m.created_at, r.role, r.read_at "
         "FROM message m JOIN message_recipient r ON r.message_id = m.id "
         "WHERE r.recipient = ? ")
    if unread_only:
        q += "AND r.read_at IS NULL "
    q += "ORDER BY m.created_at, m.id"
    return con.execute(q, (recipient,)).fetchall()


def unread_to(con, recipient):
    """Ids of unread messages where `recipient` is a 'to' — the NOTIFY/wake filter
    (Cc is deliberately excluded: cc never wakes, it's swept up by fetch)."""
    return [r["id"] for r in con.execute(
        "SELECT m.id FROM message m JOIN message_recipient r ON r.message_id = m.id "
        "WHERE r.recipient = ? AND r.role = 'to' AND r.read_at IS NULL ORDER BY m.id",
        (recipient,)).fetchall()]


def mark_read(con, recipient, ids):
    con.executemany(
        "UPDATE message_recipient SET read_at = datetime('now') "
        "WHERE message_id = ? AND recipient = ? AND read_at IS NULL",
        [(i, recipient) for i in ids])
    con.commit()


def fetch(con, store, recipient, mark=True):
    """Consolidate this recipient's unread messages (to + cc) — bodies read from their
    FULL path, subject-only entries carry no body — and (default) mark them read."""
    rows = inbox(con, recipient, unread_only=True)
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
    """The reply chain from the root down to msg_id (ascending by id)."""
    chain, cur = [], con.execute("SELECT * FROM message WHERE id=?", (msg_id,)).fetchone()
    while cur is not None:
        chain.append(cur)
        cur = (con.execute("SELECT * FROM message WHERE id=?", (cur["in_reply_to"],)).fetchone()
               if cur["in_reply_to"] else None)
    chain.reverse()
    return chain


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
