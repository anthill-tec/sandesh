#!/usr/bin/env python3
"""cli.py — command-line interface for Sandesh (standalone, multi-project).

  setup / projects                      — provision + list projects
  register / unregister / addressbook   — the addressbook (durable identities)
  send / reply / inbox / fetch / thread — messages
  actioned                              — disposition (close a request)
  notify                                — block until 'to' mail arrives (the watcher)

Every data command needs a project: `--project <id>` or $SANDESH_PROJECT.
The store lives at  <data_home>/sandesh/projects/<project_id>/.
The caller's own address for send/reply/inbox/fetch comes from --from/--to or
$SANDESH_ADDRESS (falling back to $WF_TRACK).
"""

import argparse
import os
import sys

from sandesh import __version__
from sandesh import sandesh_db as sdb
from sandesh import notify as _notify
from sandesh import migrate as _migrate


def _project(args):
    p = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    if not p:
        sys.exit("[sandesh] ERROR: pass --project <id> (or set $SANDESH_PROJECT).")
    return p


def _ctx(args):
    """(project_id, store_dir, connection)."""
    project = _project(args)
    store = sdb.store_dir(project)
    return project, store, sdb.connect(store)


def _self_addr(args, flag):
    return getattr(args, flag, None) or os.environ.get("SANDESH_ADDRESS") or os.environ.get("WF_TRACK")


def _split(csv):
    return [x.strip() for x in csv.split(",") if x.strip()] if csv else []


def _read_body(args):
    if getattr(args, "body_file", None):
        with open(args.body_file, encoding="utf-8") as fh:
            return fh.read()
    return getattr(args, "body", None)


# --------------------------------------------------------------------------- #

def cmd_setup(args):
    project = _project(args)
    store = sdb.setup(project)
    print(f"project {project!r} ready → {store}")
    return 0


def cmd_projects(args):
    ps = sdb.list_projects()
    print("\n".join(ps) if ps else "(no projects set up)")
    return 0


def cmd_register(args):
    project, _, con = _ctx(args)
    try:
        sdb.register(con, args.address, kind=args.kind, display_name=args.name,
                     by=args.address, project=project)
    except ValueError as exc:
        sys.exit(f"[sandesh] {exc}")
    print(f"registered: {args.address}  (project={project}, kind={args.kind or '-'})")
    return 0


def cmd_unregister(args):
    project, _, con = _ctx(args)
    requester = _self_addr(args, "as_")
    if not requester:
        sys.exit("[sandesh] ERROR: pass --as '<your address>' (or set $SANDESH_ADDRESS).")
    try:
        verdict, pid = sdb.unregister(con, args.address, requester=requester, project=project)
    except (ValueError, PermissionError) as exc:
        sys.exit(f"[sandesh] {exc}")
    if verdict == "tombstoned":
        print(f"tombstone set on {args.address} (notifier pid {pid}). It stops within one poll; "
              f"re-run once `addressbook` shows it offline.")
        return 3
    print(f"unregistered: {args.address}")
    return 0


def cmd_addressbook(args):
    project, _, con = _ctx(args)
    book = sdb.addressbook(con)
    if not book:
        print(f"addressbook ({project}): empty")
        return 0
    print(f"{'ADDRESS':22} {'KIND':9} {'STATUS':9} {'LISTENING':10} REGISTERED")
    for b in book:
        print(f"{b['address']:22} {b['kind'] or '-':9} "
              f"{'active' if b['active'] else 'inactive':9} "
              f"{'● live' if b['listening'] else '○ offline':10} {b['registered_at']}")
    return 0


def cmd_send(args):
    project, store, con = _ctx(args)
    sender = _self_addr(args, "from_")
    if not sender:
        sys.exit("[sandesh] ERROR: pass --from '<your address>' (or set $SANDESH_ADDRESS).")
    try:
        mid = sdb.send(con, store, sender, to=_split(args.to), cc=_split(args.cc),
                       subject=args.subject, kind=args.kind, body_text=_read_body(args),
                       project=project)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"[sandesh] {exc}")
    kind = "subject-only" if not _read_body(args) else "with body"
    print(f"sent #{mid} ({kind}) from {sender} → to:[{args.to or ''}] cc:[{args.cc or ''}]")
    return 0


def cmd_reply(args):
    project, store, con = _ctx(args)
    sender = _self_addr(args, "from_")
    if not sender:
        sys.exit("[sandesh] ERROR: pass --from '<your address>' (or set $SANDESH_ADDRESS).")
    try:
        mid = sdb.reply(con, store, args.to_msg, sender, subject=args.subject,
                        body_text=_read_body(args), reply_all=args.all,
                        resolves=args.resolves, project=project)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"[sandesh] {exc}")
    print(f"replied #{mid} to #{args.to_msg}{' (parent → actioned)' if args.resolves else ''}")
    return 0


def _render(items, recipient):
    if not items:
        print(f"(no unread messages for {recipient})")
        return
    print(f"═══ {len(items)} message(s) · {recipient} ═══\n")
    for it in items:
        ref = f" · ↳ re #{it['in_reply_to'][0]} \"{it['in_reply_to'][1]}\"" if it["in_reply_to"] else ""
        tag = " · subject-only" if it["body"] is None else ""
        print(f"[#{it['id']}] {it['from']} · {it['created_at']} · {it['role']}{ref}{tag}")
        print(f"   {it['subject']}")
        if it["body"] is not None:
            print("   ───────────────")
            for line in it["body"].rstrip("\n").splitlines():
                print(f"   {line}")
        print()


def cmd_inbox(args):
    _, _, con = _ctx(args)
    who = _self_addr(args, "to")
    if not who:
        sys.exit("[sandesh] ERROR: pass --to '<address>' (or set $SANDESH_ADDRESS).")
    rows = sdb.inbox(con, who, unread_only=not args.all)
    print(f"{'#':>5} {'FROM':16} {'ROLE':4} {'READ':5} SUBJECT")
    for r in rows:
        print(f"{r['id']:>5} {r['from_addr']:16} {r['role']:4} "
              f"{'·' if r['read_at'] is None else '✓':5} {r['subject']}")
    print(f"({len(rows)} {'unread' if not args.all else 'total'})")
    return 0


def cmd_fetch(args):
    _, store, con = _ctx(args)
    who = _self_addr(args, "to")
    if not who:
        sys.exit("[sandesh] ERROR: pass --to '<address>' (or set $SANDESH_ADDRESS).")
    items = sdb.fetch(con, store, who, mark=not args.peek)
    _render(items, who)
    if items and not args.peek:
        print(f"(marked {len(items)} read)")
    return 0


def cmd_thread(args):
    _, _, con = _ctx(args)
    chain = sdb.thread(con, args.id)
    if not chain:
        sys.exit(f"[sandesh] no such message #{args.id}")
    for m in chain:
        ind = "  " if m["in_reply_to"] else ""
        print(f"{ind}#{m['id']} {m['from_addr']} · {m['created_at']} · {m['status']}")
        print(f"{ind}   {m['subject']}")
    return 0


def cmd_actioned(args):
    _, _, con = _ctx(args)
    sdb.set_status(con, args.id, args.status)
    print(f"#{args.id} → {args.status}")
    return 0


def cmd_notify(args):
    return _notify.run(_project(args), args.to, args.timeout)


def cmd_migrate(args):
    # Delegate to the migration engine (heavy yoyo/jsonschema imports stay lazy
    # inside migrate.py). The dep guard there exits non-zero with a friendly hint
    # when the [migrate] extra is absent.
    return _migrate.cmd_migrate(args)


# --------------------------------------------------------------------------- #

def build_parser():
    # --project is shared so it works BOTH before and after the subcommand:
    #   sandesh --project X setup    AND    sandesh setup --project X
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS: an absent --project in one position must not clobber a value given in
    # the other (so it works both before AND after the subcommand).
    common.add_argument("--project", default=argparse.SUPPRESS,
                        help="project id (overrides $SANDESH_PROJECT)")

    ap = argparse.ArgumentParser(prog="sandesh", parents=[common],
                                 description="Sandesh messaging CLI (standalone).")
    ap.add_argument("--version", action="version", version=f"sandesh {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", parents=[common],
                   help="provision a project (create store + init DB)").set_defaults(fn=cmd_setup)
    sub.add_parser("projects", parents=[common], help="list set-up projects").set_defaults(fn=cmd_projects)

    p = sub.add_parser("register", parents=[common], help="self-register an address")
    p.add_argument("--address", required=True)
    p.add_argument("--kind", choices=["mainline", "track"])
    p.add_argument("--name")
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("unregister", parents=[common], help="remove an address (Mainline: anyone; else: self)")
    p.add_argument("--address", required=True)
    p.add_argument("--as", dest="as_", help="your address (or $SANDESH_ADDRESS)")
    p.set_defaults(fn=cmd_unregister)

    sub.add_parser("addressbook", parents=[common],
                   help="list participants + who's listening").set_defaults(fn=cmd_addressbook)

    p = sub.add_parser("send", parents=[common], help="send a message")
    p.add_argument("--from", dest="from_", help="sender (or $SANDESH_ADDRESS)")
    p.add_argument("--to", help="comma-separated recipients (or 'all-tracks')")
    p.add_argument("--cc")
    p.add_argument("--subject", required=True)
    p.add_argument("--kind", choices=["request", "directive", "fyi"])
    p.add_argument("--body", help="inline body text")
    p.add_argument("--body-file", dest="body_file", help="md file as body (omit → subject-only)")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("reply", parents=[common], help="reply to a message")
    p.add_argument("--to-msg", dest="to_msg", type=int, required=True)
    p.add_argument("--from", dest="from_")
    p.add_argument("--subject")
    p.add_argument("--body")
    p.add_argument("--body-file", dest="body_file")
    p.add_argument("--all", action="store_true", help="reply-all (cc the parent's recipients)")
    p.add_argument("--resolves", action="store_true", help="mark the parent 'actioned'")
    p.set_defaults(fn=cmd_reply)

    p = sub.add_parser("inbox", parents=[common], help="list a recipient's messages")
    p.add_argument("--to")
    p.add_argument("--all", action="store_true", help="include already-read")
    p.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("fetch", parents=[common], help="consolidate + read unread messages")
    p.add_argument("--to")
    p.add_argument("--peek", action="store_true", help="render without marking read")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("thread", parents=[common], help="show a message's reply chain")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(fn=cmd_thread)

    p = sub.add_parser("actioned", parents=[common], help="set a message's disposition")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--status", default="actioned", choices=["open", "actioned", "closed"])
    p.set_defaults(fn=cmd_actioned)

    p = sub.add_parser("notify", parents=[common], help="block until 'to' mail arrives (the mailbox watcher)")
    p.add_argument("--to", required=True)
    p.add_argument("--timeout", type=int, default=_notify.DEFAULT_TIMEOUT_SECS)
    p.set_defaults(fn=cmd_notify)

    p = sub.add_parser("migrate", parents=[common],
                       help="apply/inspect schema migrations (needs the [migrate] extra)")
    p.add_argument("--status", action="store_true", help="report applied vs pending (no writes)")
    p.add_argument("--all", action="store_true",
                   help="operate on every project store (apply is fail-fast)")
    p.add_argument("--check", action="store_true",
                   help="read-only gate: pending=non-zero, drift=warning (exit zero)")
    p.set_defaults(fn=cmd_migrate)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
