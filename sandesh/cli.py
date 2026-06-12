#!/usr/bin/env python3
"""cli.py — command-line interface for Sandesh (standalone, multi-project).

  setup / projects                      — provision + list projects
  register / unregister / addressbook   — the addressbook (durable identities)
  send / reply / inbox / fetch / thread — messages
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
    return project, store, sdb.connect()


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
    con = sdb.connect()
    try:
        if getattr(args, "all", False):
            rows = con.execute(
                "SELECT project_id, state, xproj_granted_at FROM project "
                "ORDER BY project_id").fetchall()
        else:
            rows = con.execute(
                "SELECT project_id, state, xproj_granted_at FROM project "
                "WHERE state != 'tombstoned' ORDER BY project_id").fetchall()
        if not rows:
            print("(no projects set up)")
            return 0
        print(f"{'PROJECT':20} {'STATE':10} CROSS-PROJECT")
        for r in rows:
            print(f"{r['project_id']:20} {r['state']:10} "
                  f"{'✓' if r['xproj_granted_at'] else '-'}")
    finally:
        con.close()
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
    book = sdb.addressbook(con, project)
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
                        project=project)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"[sandesh] {exc}")
    print(f"replied #{mid} to #{args.to_msg}")
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
    try:
        rows = sdb.inbox(con, who, unread_only=not args.all,
                         sender=args.from_, sender_project=args.from_project,
                         kind=args.kind, since=args.since, until=args.until,
                         subject_like=args.subject)
    except ValueError as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
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
    try:
        items = sdb.fetch(con, store, who, mark=not args.peek,
                          sender=args.from_, sender_project=args.from_project,
                          kind=args.kind, since=args.since, until=args.until,
                          subject_like=args.subject)
    except ValueError as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
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
        if isinstance(m, dict) and "warning" in m:   # tombstoned hole (§S2)
            print(m["warning"])
            continue
        ind = "  " if m["in_reply_to"] else ""
        print(f"{ind}#{m['id']} {m['from_addr']} · {m['created_at']}")
        print(f"{ind}   {m['subject']}")
    return 0


def cmd_notify(args):
    return _notify.run(_project(args), args.to, args.timeout)


def cmd_migrate(args):
    # Delegate to the migration engine (heavy yoyo/jsonschema imports stay lazy
    # inside migrate.py). The dep guard there exits non-zero with a friendly hint
    # when the [migrate] extra is absent.
    return _migrate.cmd_migrate(args)


def cmd_grant(args):
    con = sdb.connect()
    try:
        sdb.grant_xproj(con, args.project, by=args.by)
    except (ValueError, PermissionError) as exc:
        # Print explicitly (not via SystemExit's message) so in-process callers
        # that capture stderr still see the error text.
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    print(f"cross-project sending granted to project {args.project!r} (by {args.by})")
    return 0


def cmd_revoke(args):
    con = sdb.connect()
    try:
        sdb.revoke_xproj(con, args.project, by=args.by)
    except (ValueError, PermissionError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    print(f"cross-project sending revoked for project {args.project!r} (by {args.by})")
    return 0


def cmd_archive(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            watchers = sdb.archive_preview(con, args.project, args.by)
            print(f"[dry-run] project {args.project!r} would become archived")
            if watchers:
                print(f"[dry-run] watchers to evict ({len(watchers)}):")
                for addr in watchers:
                    print(f"  {addr}")
            else:
                print("[dry-run] watchers to evict: none")
            print("[dry-run] nothing written")
            return 0
        sdb.archive(con, args.project, args.by, force=args.force)
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    print(f"archived project {args.project!r} (by {args.by}) — "
          f"read-only until unarchived; nothing deleted")
    return 0


def cmd_unarchive(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            sdb.unarchive_preview(con, args.project, args.by)
            print(f"[dry-run] project {args.project!r} would become active")
            print("[dry-run] nothing written")
            return 0
        sdb.unarchive(con, args.project, args.by)
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    print(f"unarchived project {args.project!r} (by {args.by}) — active again")
    return 0


def cmd_tombstone(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            counts = sdb.tombstone_preview(con, args.project, args.by)
            print(f"[dry-run] project {args.project!r} would become tombstoned:")
            print(f"  internal messages: {counts['internal_messages']} (rows purged)")
            print(f"  body files: {counts['body_files']} (deleted from disk)")
            print(f"  cross-project messages: {counts['cross_project_messages']} "
                  f"(rows survive; their bodies are lost)")
            print("[dry-run] nothing written")
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print(f"[sandesh] tombstoning project {args.project!r} is destructive "
                      f"and irreversible — pass --yes to confirm "
                      f"(stdin is not a terminal, cannot prompt)", file=sys.stderr)
                sys.exit(1)
            answer = input(
                f"tombstone project {args.project!r}? This permanently purges its "
                f"internal messages and deletes its body folder. [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("aborted — nothing changed")
                return 1
        sdb.tombstone_project(con, args.project, args.by, force=args.force)
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    print(f"tombstoned project {args.project!r} (by {args.by}) — internal history "
          f"purged, body folder deleted; cross-project envelopes survive")
    return 0


def cmd_consolidate(args):
    summaries = sdb.consolidate()
    if not summaries:
        print("nothing to consolidate — no legacy per-project stores found.")
        return 0
    for entry in summaries:
        print(f"consolidated {entry['project_id']}: "
              f"{entry['messages_imported']} message(s), "
              f"{entry['addresses_imported']} address(es) → sandesh.db.pre-global")
    return 0


# --------------------------------------------------------------------------- #

def cmd_search(args):
    con = sdb.connect()
    try:
        try:
            result = sdb.search(con, args.to, args.query, limit=args.limit,
                                offset=args.offset,
                                sender_project=args.from_project)
        except ValueError as exc:
            print(f"[sandesh] {exc}", file=sys.stderr)
            sys.exit(1)
    finally:
        con.close()
    if result.get("reindexed"):
        print("(index was empty — reindexed before searching)")
    if not result["hits"]:
        print(f"(no matches for {args.query!r})")
    for h in result["hits"]:
        print(f"[#{h['id']}] {h['from']} · {h['created_at']}")
        print(f"   {h['subject']}")
        print(f"   {h['snippet']}")
    print(f"total: {result['total']}")
    return 0


def cmd_reindex(args):
    con = sdb.connect()
    try:
        n = sdb.reindex(con)
    finally:
        con.close()
    print(f"reindexed {n} message(s)")
    return 0


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
    p = sub.add_parser("projects", parents=[common], help="list set-up projects")
    p.add_argument("--all", action="store_true",
                   help="include tombstoned projects (permanent markers)")
    p.set_defaults(fn=cmd_projects)

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
    p.set_defaults(fn=cmd_reply)

    # CR-SAN-026 §S3: server-side filter flags, mapped 1:1 onto the lib's
    # inbox/fetch filter params. --from-project is the headline — the
    # cross-project proxy stream (only mail whose SENDER belongs to that
    # sibling project).
    p = sub.add_parser("inbox", parents=[common], help="list a recipient's messages")
    p.add_argument("--to")
    p.add_argument("--all", action="store_true", help="include already-read")
    p.add_argument("--from-project", dest="from_project",
                   help="only mail whose sender belongs to this project "
                        "(the cross-project proxy stream)")
    p.add_argument("--from", dest="from_", help="only mail from this exact sender address")
    p.add_argument("--kind", help="only this message kind (request/directive/fyi)")
    p.add_argument("--since", help="only mail at/after this time "
                                   "(YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS', inclusive)")
    p.add_argument("--until", help="only mail at/before this time "
                                   "(YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS', inclusive; "
                                   "date-only means end of that day)")
    p.add_argument("--subject", help="case-insensitive substring match on subject")
    p.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("fetch", parents=[common], help="consolidate + read unread messages")
    p.add_argument("--to")
    p.add_argument("--peek", action="store_true", help="render without marking read")
    p.add_argument("--from-project", dest="from_project",
                   help="only mail whose sender belongs to this project "
                        "(the cross-project proxy stream)")
    p.add_argument("--from", dest="from_", help="only mail from this exact sender address")
    p.add_argument("--kind", help="only this message kind (request/directive/fyi)")
    p.add_argument("--since", help="only mail at/after this time "
                                   "(YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS', inclusive)")
    p.add_argument("--until", help="only mail at/before this time "
                                   "(YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS', inclusive; "
                                   "date-only means end of that day)")
    p.add_argument("--subject", help="case-insensitive substring match on subject")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("thread", parents=[common], help="show a message's reply chain")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(fn=cmd_thread)

    p = sub.add_parser("notify", parents=[common], help="block until 'to' mail arrives (the mailbox watcher)")
    p.add_argument("--to", required=True)
    p.add_argument("--timeout", type=int, default=_notify.DEFAULT_TIMEOUT_SECS)
    p.set_defaults(fn=cmd_notify)

    # CR-SAN-022 DEC-B: the migrate engine targets the single global DB, so the
    # migrate subparser deliberately does NOT inherit the common --project
    # parent (`sandesh migrate --project X` is an argparse error). The
    # pre-subcommand `sandesh --project X migrate` form still parses via the
    # top-level parser; migrate simply ignores the value.
    p = sub.add_parser("migrate",
                       help="apply/inspect schema migrations on the global DB "
                            "(needs the [migrate] extra)")
    p.add_argument("--status", action="store_true", help="report applied vs pending (no writes)")
    p.add_argument("--rollback", action="store_true",
                   help="roll back the single most-recent applied migration")
    p.add_argument("--all", action="store_true",
                   help="operate on every project store (apply is fail-fast)")
    p.add_argument("--check", action="store_true",
                   help="read-only gate: pending=non-zero, drift=warning (exit zero)")
    p.add_argument("--dump-schema", dest="dump_schema", action="store_true",
                   help="emit the live DB shape as JSON to stdout (read-only)")
    p.add_argument("--diff", metavar="OLD_SNAPSHOT",
                   help="compare an old snapshot file against the live shape (read-only)")
    p.add_argument("--json", dest="json", action="store_true",
                   help="machine-parseable JSON output for --diff")
    p.set_defaults(fn=cmd_migrate)

    # CR-SAN-022 §S3: one-time import of legacy per-project stores into the
    # global DB. Global like `migrate` — no --project needed (the
    # pre-subcommand `sandesh --project X consolidate` form still parses;
    # the value is simply ignored).
    p = sub.add_parser("consolidate",
                       help="import legacy per-project stores into the global DB "
                            "(one-time; legacy files become sandesh.db.pre-global)")
    p.set_defaults(fn=cmd_consolidate)

    # CR-SAN-027 §S3: full-text search over the caller's OWN mail. Parentless
    # like migrate/consolidate — the engine targets the single global DB, so
    # `search --project X` is an argparse error (no per-project routing).
    p = sub.add_parser("search",
                       help="full-text search over your own mail (FTS5 syntax: "
                           "\"quoted phrases\", AND/OR/NOT)")
    p.add_argument("query", help="the FTS5 query")
    p.add_argument("--to", required=True, help="your address (whose mail to search)")
    p.add_argument("--from-project", dest="from_project",
                   help="only hits whose sender belongs to this project")
    p.add_argument("--limit", type=int, default=20, help="page size (default 20)")
    p.add_argument("--offset", type=int, default=0, help="page start (default 0)")
    p.set_defaults(fn=cmd_search)

    # CR-SAN-027 §S2: rebuild the whole FTS index from the message rows + body
    # files. Parentless and arg-free — global DB, plumbing only.
    p = sub.add_parser("reindex",
                       help="rebuild the full-text search index from messages + bodies")
    p.set_defaults(fn=cmd_reindex)

    # CR-SAN-023 §S2: admin-only verbs (CLI-only — never MCP). Like migrate/
    # consolidate, these deliberately do NOT inherit parents=[common]: their
    # --project is the TARGET project of the grant, not routing context (avoids
    # the dual-position SUPPRESS trap). There is NO `sandesh admin` subcommand —
    # admin assignment happens only in install.sh via $SANDESH_ADMIN (PRD O3).
    p = sub.add_parser("grant",
                       help="grant cross-project sending to a project (Sandesh admin only)")
    p.add_argument("--cross-project", dest="cross_project", action="store_true",
                   required=True, help="the cross-project access grant (required)")
    p.add_argument("--project", required=True, help="the TARGET project receiving the grant")
    p.add_argument("--by", required=True, help="your admin name (must match the stored admin)")
    p.set_defaults(fn=cmd_grant)

    p = sub.add_parser("revoke",
                       help="revoke a project's cross-project grant (Sandesh admin only)")
    p.add_argument("--cross-project", dest="cross_project", action="store_true",
                   required=True, help="the cross-project access grant (required)")
    p.add_argument("--project", required=True, help="the TARGET project losing the grant")
    p.add_argument("--by", required=True, help="your admin name (must match the stored admin)")
    p.set_defaults(fn=cmd_revoke)

    # CR-SAN-024 §S3: project lifecycle verbs. Parentless like grant/revoke —
    # their --project is the TARGET project, not routing context. Two-tier
    # authz: archive/unarchive take the project's own Mainline (--by), the
    # destructive tombstone takes the install-assigned super-admin (--by) and
    # an interactive confirm (bypass with --yes). All three accept --dry-run
    # (report only, writes nothing).
    p = sub.add_parser("archive",
                       help="archive a project — read-only, reversible "
                            "(its own Mainline only)")
    p.add_argument("--project", required=True, help="the project to archive")
    p.add_argument("--by", required=True,
                   help="the project's own Mainline address")
    p.add_argument("--force", action="store_true",
                   help="reap watchers that ignore the eviction tombstone")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="report watchers to evict + would-be state; write nothing")
    p.set_defaults(fn=cmd_archive)

    p = sub.add_parser("unarchive",
                       help="reactivate an archived project (its own Mainline only)")
    p.add_argument("--project", required=True, help="the project to reactivate")
    p.add_argument("--by", required=True,
                   help="the project's own Mainline address")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="report would-be state; write nothing")
    p.set_defaults(fn=cmd_unarchive)

    p = sub.add_parser("tombstone",
                       help="permanently retire an ARCHIVED project — purges its "
                            "internal history + body folder (Sandesh admin only)")
    p.add_argument("--project", required=True, help="the project to tombstone")
    p.add_argument("--by", required=True,
                   help="your admin name (must match the stored admin)")
    p.add_argument("--force", action="store_true",
                   help="reap watchers that ignore the eviction tombstone")
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive confirmation")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="report would-be purge counts; write nothing")
    p.set_defaults(fn=cmd_tombstone)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
