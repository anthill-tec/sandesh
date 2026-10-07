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
Output format: `--format {human,toon,json}` (before or after the subcommand) or
$SANDESH_FORMAT; default human. In the machine modes the human printers go to
stderr and one AXI envelope (sandesh.axi) is written to stdout (CR-SAN-047 §S3).
"""

import argparse
import contextlib
import io
import os
import re
import sqlite3
import sys

from sandesh import __version__
from sandesh import axi
from sandesh import sandesh_db as sdb
from sandesh import notify as _notify
from sandesh import migrate as _migrate


def _project(args):
    p = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    if not p:
        sys.exit("[sandesh] ERROR: pass --project <id> (or set $SANDESH_PROJECT).")
    return p


_CONNECTIONS = []      # every _con()/_ctx() connection; main() closes them on the way out


def _con():
    """A tracked global-DB connection (the project-free half of _ctx()): closed
    by main() when the verb finishes, so verbs that take no project (search,
    projects) share the same seam instead of a local try/finally."""
    con = sdb.connect()
    _CONNECTIONS.append(con)
    return con


def _ctx(args):
    """(project_id, store_dir, connection). The connection is closed by main()
    when the verb finishes (an unclosed sqlite3 connection is cyclic garbage —
    it would otherwise surface as a ResourceWarning at an arbitrary GC point)."""
    project = _project(args)
    store = sdb.store_dir(project)
    return project, store, _con()


def _close_connections():
    while _CONNECTIONS:
        _CONNECTIONS.pop().close()


def _self_addr(args, flag):
    return getattr(args, flag, None) or os.environ.get("SANDESH_ADDRESS") or os.environ.get("WF_TRACK")


def _require_own_addr(args, flag, placeholder):
    """The caller's own address from its `flag` (else $SANDESH_ADDRESS); a
    missing one is the house `[sandesh] ERROR: pass …` exit."""
    address = _self_addr(args, flag)
    if not address:
        sys.exit(f"[sandesh] ERROR: pass {placeholder} (or set $SANDESH_ADDRESS).")
    return address


def _split(csv):
    return [x.strip() for x in csv.split(",") if x.strip()] if csv else []


def _positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _read_body(args):
    if getattr(args, "body_file", None):
        with open(args.body_file, encoding="utf-8") as fh:
            return fh.read()
    return getattr(args, "body", None)


# --------------------------------------------------------------------------- #
# --format plumbing (CR-SAN-047 §S3 / §S4b)

_ERR_PREFIX = "[sandesh] "


def _prescan(argv, flag):
    """Last `flag X` / `flag=X` value anywhere in argv, without argparse
    (None if absent) — so usage errors can be reported in the right format."""
    value = None
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            value = argv[i + 1]
        elif tok.startswith(flag + "="):
            value = tok[len(flag) + 1:]
    return value


def _prescan_format(argv):
    """`--format` from argv → $SANDESH_FORMAT → human (unvalidated)."""
    return _prescan(argv, "--format") or os.environ.get("SANDESH_FORMAT") or "human"


def _resolve_format(args):
    """args.format → $SANDESH_FORMAT → human; an invalid env value exits 2."""
    fmt = getattr(args, "format", None) or os.environ.get("SANDESH_FORMAT") or "human"
    if fmt not in axi.FORMATS:
        print(f"{_ERR_PREFIX}invalid $SANDESH_FORMAT {fmt!r} — expected one of: "
              f"{', '.join(axi.FORMATS)}", file=sys.stderr)
        sys.exit(2)
    return fmt


def _axi_context(args):
    """`{project, address?}` for the envelope; address is the verb's OWN address
    (its --from/--as/--address/--to flag, keyed by verb — `inbox`'s --from is a
    FILTER, not the caller) or $SANDESH_ADDRESS."""
    context = {}
    project = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    if project:
        context["project"] = project
    flag = _OWN_ADDRESS_FLAG.get(getattr(args, "cmd", None))
    if flag:
        address = _self_addr(args, flag)
        if address:
            context["address"] = address
    return context


# The verb's own-address flag (dest name) — §S4: context.address on
# register/unregister/inbox/fetch/notify/send/reply only.
_OWN_ADDRESS_FLAG = {
    "register": "address", "unregister": "as_", "inbox": "to", "fetch": "to",
    "notify": "to", "send": "from_", "reply": "from_",
}


class _UsageError(Exception):
    """A machine-mode usage error raised by an `axi_<verb>` handler (e.g.
    `tombstone` without --yes, the home view without its env) — _run_machine
    emits the same `ok:false error help[]` envelope argparse errors get (exit 2)."""


class _Failed(Exception):
    """A handler's non-zero outcome that still has fields worth reporting (the
    steps `init` completed before stopping): _run_machine emits `ok:false` +
    those fields + `error` (the human stderr text) and exits `rc`."""

    def __init__(self, rc, message, fields):
        super().__init__(message)
        self.rc = rc
        self.fields = dict(fields)


def _usage_envelope(verb, prog, message, context):
    """The exit-2 usage-error envelope (§S4b): `ok:false error` + `help[]`
    naming the verb's --help (and, at the top level, the valid global flags)."""
    help_ = [f"{prog} --help"]
    if verb == "sandesh":
        help_.append("sandesh [--project <id>] [--format {human,toon,json}] <verb> ...")
    env = axi.error_envelope(verb, ValueError(message), context or {})
    env.help = help_
    return env


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors become an `ok:false` AXI envelope on
    stdout (exit 2) when the pre-scanned format is a machine mode; in human
    mode argparse's usage-on-stderr behaviour is unchanged. `axi_format` /
    `axi_context` are stamped on by build_parser (subparsers inherit the class
    via argparse's default parser_class=type(parent))."""

    axi_format = "human"
    axi_context = None

    def error(self, message):
        if self.axi_format not in ("toon", "json"):
            return super().error(message)
        parts = self.prog.split()
        verb = parts[1] if len(parts) > 1 else "sandesh"
        axi.emit(_usage_envelope(verb, self.prog, message, self.axi_context), self.axi_format)
        return self.exit(2)


def _run_machine(args, fmt):
    """Machine-mode dispatch: the handler's prints land on stderr; one envelope
    on stdout. Verbs in AXI_FN run their `axi_<verb>` handler → `(rc, fields)`
    or `(rc, fields, help)` (a RETURNED non-zero rc is still `ok:true`;
    failures RAISE — ValueError/PermissionError/FileNotFoundError/RuntimeError →
    error_envelope + exit 1; _UsageError → the usage envelope + exit 2).
    Other verbs fall back to the human handler with empty fields. Either way
    `sys.exit('[sandesh] …')` → error_envelope + exit 1 (the code a real process
    reports for that idiom); a non-zero exit int (or, on the fallback path, a
    non-zero return) → a generic error envelope."""
    verb = args.cmd
    context = _axi_context(args)
    fn = AXI_FN.get(verb)
    if fn is not None and verb in _EMITS_OWN_ENVELOPE:
        # notify (§S5): the watcher writes its own final envelope to the stdout
        # current at entry and its progress to stderr — no redirect or second
        # envelope. Startup failures before the watcher owns output are emitted here.
        try:
            return fn(args)[0]
        except SystemExit as exc:
            if not isinstance(exc.code, str):
                raise
            msg = exc.code[len(_ERR_PREFIX):] if exc.code.startswith(_ERR_PREFIX) else exc.code
            axi.emit(axi.error_envelope(verb, ValueError(msg), context), fmt)
            return 1
    rc, error, fields, help_, exited = 0, None, {}, [], False
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if fn is not None:
                rc, fields, *rest = fn(args)
                help_ = rest[0] if rest else []
            else:
                rc = args.fn(args) or 0
    except _UsageError as exc:
        axi.emit(_usage_envelope(verb, f"sandesh {verb}", str(exc), context), fmt)
        return 2
    except _Failed as exc:
        rc, error, fields = exc.rc, str(exc), exc.fields
    except sdb.MigrationRequired as exc:
        rc, error = 1, str(exc)
    except (ValueError, PermissionError, FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        rc, error = 1, str(exc)
    except SystemExit as exc:
        code, exited = exc.code, True
        if isinstance(code, str):
            rc = 1
            error = code[len(_ERR_PREFIX):] if code.startswith(_ERR_PREFIX) else code
        else:
            rc = code or 0
    if rc and error is None and (exited or fn is None):
        error = f"{verb} failed (exit {rc})"
    if error is None:
        env = axi.Envelope(verb, True, fields, context, help_)
    else:
        env = axi.error_envelope(verb, ValueError(error), context)
        if fields:                      # _Failed: the partial result + error
            env.fields = {**fields, **env.fields}
    axi.emit(env, fmt)
    return rc


# --------------------------------------------------------------------------- #

def _print_setup(project, store):
    print(f"project {project!r} ready → {store}")


def cmd_setup(args):
    project = _project(args)
    store = sdb.setup(project)
    _print_setup(project, store)
    return 0


def _project_rows(con, include_all):
    if include_all:
        return con.execute(
            "SELECT project_id, state, xproj_granted_at FROM project "
            "ORDER BY project_id").fetchall()
    return con.execute(
        "SELECT project_id, state, xproj_granted_at FROM project "
        "WHERE state != 'tombstoned' ORDER BY project_id").fetchall()


def _print_projects(rows):
    if not rows:
        print("(no projects set up)")
        return
    print(f"{'PROJECT':20} {'STATE':10} CROSS-PROJECT")
    for r in rows:
        print(f"{r['project_id']:20} {r['state']:10} "
              f"{'✓' if r['xproj_granted_at'] else '-'}")


def cmd_projects(args):
    con = sdb.connect()
    try:
        _print_projects(_project_rows(con, getattr(args, "all", False)))
    finally:
        con.close()
    return 0


def _print_registered(args, project):
    print(f"registered: {args.address}  (project={project}, kind={args.kind or '-'})")


def cmd_register(args):
    project, _, con = _ctx(args)
    try:
        sdb.register(con, args.address, kind=args.kind, display_name=args.name,
                     by=args.address, project=project)
    except ValueError as exc:
        sys.exit(f"[sandesh] {exc}")
    _print_registered(args, project)
    return 0


def _print_unregister(args, verdict, pid):
    if verdict == "tombstoned":
        print(f"tombstone set on {args.address} (notifier pid {pid}). It stops within one poll; "
              f"re-run once `addressbook` shows it offline.")
        return 3
    print(f"unregistered: {args.address}")
    return 0


def cmd_unregister(args):
    project, _, con = _ctx(args)
    requester = _require_own_addr(args, "as_", "--as '<your address>'")
    try:
        verdict, pid = sdb.unregister(con, args.address, requester=requester, project=project)
    except (ValueError, PermissionError) as exc:
        sys.exit(f"[sandesh] {exc}")
    return _print_unregister(args, verdict, pid)


def _print_addressbook(project, book):
    if not book:
        print(f"addressbook ({project}): empty")
        return
    print(f"{'ADDRESS':22} {'KIND':9} {'STATUS':9} {'LISTENING':10} REGISTERED")
    for b in book:
        print(f"{b['address']:22} {b['kind'] or '-':9} "
              f"{'active' if b['active'] else 'inactive':9} "
              f"{'● live' if b['listening'] else '○ offline':10} {b['registered_at']}")


def cmd_addressbook(args):
    project, _, con = _ctx(args)
    book = sdb.addressbook(con, project)
    _print_addressbook(project, book)
    return 0


def _print_sent(args, mid, sender):
    kind = "subject-only" if not _read_body(args) else "with body"
    print(f"sent #{mid} ({kind}) from {sender} → to:[{args.to or ''}] cc:[{args.cc or ''}]")


def cmd_send(args):
    project, store, con = _ctx(args)
    sender = _require_own_addr(args, "from_", "--from '<your address>'")
    try:
        mid = sdb.send(con, store, sender, to=_split(args.to), cc=_split(args.cc),
                       subject=args.subject, kind=args.kind, body_text=_read_body(args),
                       project=project)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"[sandesh] {exc}")
    _print_sent(args, mid, sender)
    return 0


def _print_replied(args, mid):
    print(f"replied #{mid} to #{args.to_msg}")


def cmd_reply(args):
    project, store, con = _ctx(args)
    sender = _require_own_addr(args, "from_", "--from '<your address>'")
    try:
        mid = sdb.reply(con, store, args.to_msg, sender, subject=args.subject,
                        body_text=_read_body(args), reply_all=args.all,
                        project=project)
    except (ValueError, FileNotFoundError) as exc:
        sys.exit(f"[sandesh] {exc}")
    _print_replied(args, mid)
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


def _inbox_rows(con, args, who, unread_only):
    """sdb.inbox with the CLI's filter flags mapped 1:1."""
    return sdb.inbox(con, who, unread_only=unread_only,
                     sender=args.from_, sender_project=args.from_project,
                     kind=args.kind, since=args.since, until=args.until,
                     subject_like=args.subject)


def _print_inbox(rows, show_all):
    print(f"{'#':>5} {'FROM':16} {'ROLE':4} {'READ':5} SUBJECT")
    for r in rows:
        print(f"{r['id']:>5} {r['from_addr']:16} {r['role']:4} "
              f"{'·' if r['read_at'] is None else '✓':5} {r['subject']}")
    print(f"({len(rows)} {'unread' if not show_all else 'total'})")


def cmd_inbox(args):
    _, _, con = _ctx(args)
    who = _require_own_addr(args, "to", "--to '<address>'")
    try:
        rows = _inbox_rows(con, args, who, not args.all)
    except ValueError as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    _print_inbox(rows, args.all)
    return 0


def _fetch_items(con, store, args, who, *, mark=None):
    """sdb.fetch with the CLI's filter flags mapped 1:1."""
    if mark is None:
        mark = not args.peek
    return sdb.fetch(con, store, who, mark=mark,
                     sender=args.from_, sender_project=args.from_project,
                     kind=args.kind, since=args.since, until=args.until,
                     subject_like=args.subject)


def _print_fetch(items, who, peek):
    _render(items, who)
    if items and not peek:
        print(f"(marked {len(items)} read)")


def cmd_fetch(args):
    _, store, con = _ctx(args)
    who = _require_own_addr(args, "to", "--to '<address>'")
    try:
        items = _fetch_items(con, store, args, who)
    except ValueError as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    _print_fetch(items, who, args.peek)
    return 0


def _is_hole(m):
    """A tombstoned-hole marker in a thread chain (sandesh_db.thread, CR-SAN-024)."""
    return isinstance(m, dict) and "warning" in m


def _print_thread(chain):
    for m in chain:
        if _is_hole(m):
            print(m["warning"])
            continue
        ind = "  " if m["in_reply_to"] else ""
        print(f"{ind}#{m['id']} {m['from_addr']} · {m['created_at']}")
        print(f"{ind}   {m['subject']}")


def cmd_thread(args):
    _, _, con = _ctx(args)
    chain = sdb.thread(con, args.id)
    if not chain:
        sys.exit(f"[sandesh] no such message #{args.id}")
    _print_thread(chain)
    return 0


def cmd_notify(args):
    return _notify.run(_project(args), args.to, args.timeout)


def axi_notify(args):
    """Machine-mode notify: `run()` with the resolved format emits its own final
    envelope (see `_EMITS_OWN_ENVELOPE`), so `_run_machine` must not emit one."""
    rc = _notify.run(_project(args), args.to, args.timeout, fmt=_resolve_format(args))
    return rc, {}


def cmd_migrate(args):
    # Delegate to the migration engine (heavy yoyo/jsonschema imports stay lazy
    # inside migrate.py). The dep guard there exits non-zero with a friendly hint
    # when the [migrate] extra is absent.
    return _migrate.cmd_migrate(args)


def _print_granted(args):
    print(f"cross-project sending granted to project {args.project!r} (by {args.by})")


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
    _print_granted(args)
    return 0


def _print_revoked(args):
    print(f"cross-project sending revoked for project {args.project!r} (by {args.by})")


def cmd_revoke(args):
    con = sdb.connect()
    try:
        sdb.revoke_xproj(con, args.project, by=args.by)
    except (ValueError, PermissionError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    _print_revoked(args)
    return 0


def _print_archived(args):
    print(f"archived project {args.project!r} (by {args.by}) — "
          f"read-only until unarchived; nothing deleted")


def _print_archive_preview(args, watchers):
    print(f"[dry-run] project {args.project!r} would become archived")
    if watchers:
        print(f"[dry-run] watchers to evict ({len(watchers)}):")
        for addr in watchers:
            print(f"  {addr}")
    else:
        print("[dry-run] watchers to evict: none")
    print("[dry-run] nothing written")


def cmd_archive(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            _print_archive_preview(args, sdb.archive_preview(con, args.project, args.by))
            return 0
        sdb.archive(con, args.project, args.by, force=args.force)
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    _print_archived(args)
    return 0


def _print_unarchived(args):
    print(f"unarchived project {args.project!r} (by {args.by}) — active again")


def _print_unarchive_preview(args):
    print(f"[dry-run] project {args.project!r} would become active")
    print("[dry-run] nothing written")


def cmd_unarchive(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            sdb.unarchive_preview(con, args.project, args.by)
            _print_unarchive_preview(args)
            return 0
        sdb.unarchive(con, args.project, args.by)
    except (ValueError, PermissionError, RuntimeError) as exc:
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    _print_unarchived(args)
    return 0


def _print_tombstone_preview(args, counts):
    print(f"[dry-run] project {args.project!r} would become tombstoned:")
    print(f"  internal messages: {counts['internal_messages']} (rows purged)")
    print(f"  body files: {counts['body_files']} (deleted from disk)")
    print(f"  cross-project messages: {counts['cross_project_messages']} "
          f"(rows survive; their bodies are lost)")
    print("[dry-run] nothing written")


def cmd_tombstone(args):
    con = sdb.connect()
    try:
        if args.dry_run:
            _print_tombstone_preview(args, sdb.tombstone_preview(con, args.project, args.by))
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
        if isinstance(exc, ValueError) and "already tombstoned" in str(exc):
            print(f"[sandesh] project {args.project!r} already tombstoned — "
                  f"nothing to do", file=sys.stderr)
            return 0
        print(f"[sandesh] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()
    _print_tombstoned(args)
    return 0


def _print_tombstoned(args):
    print(f"tombstoned project {args.project!r} (by {args.by}) — internal history "
          f"purged, body folder deleted; cross-project envelopes survive")


def cmd_consolidate(args):
    summaries = sdb.consolidate()
    if not summaries:
        print("nothing to consolidate — no legacy per-project stores found.")
        return 0
    for entry in summaries:
        if entry.get("skipped"):
            print(f"skipped {entry['project_id']}: not a legacy store "
                  f"({entry['reason']}) — file left untouched")
            continue
        print(f"consolidated {entry['project_id']}: "
              f"{entry['messages_imported']} message(s), "
              f"{entry['addresses_imported']} address(es) → sandesh.db.pre-global")
    return 0


# --------------------------------------------------------------------------- #

def _print_search(args, result):
    if result.get("reindexed"):
        print("(index was empty — reindexed before searching)")
    if not result["hits"]:
        print(f"(no matches for {args.query!r})")
    for h in result["hits"]:
        print(f"[#{h['id']}] {h['from']} · {h['created_at']}")
        print(f"   {h['subject']}")
        print(f"   {h['snippet']}")
    print(f"total: {result['total']}")


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
    _print_search(args, result)
    return 0


def cmd_reindex(args):
    con = sdb.connect()
    try:
        n = sdb.reindex(con)
    finally:
        con.close()
    print(f"reindexed {n} message(s)")
    return 0


def cmd_init(args):
    """Global provisioning sweep (CR-SAN-036 §S3) — idempotent.

    In order: migrate → consolidate → reindex → admin. Reuses the existing
    library pieces; never reimplements them. CLI-only (never an MCP tool).

      1. Migrate: if the [migrate] extra is importable, apply pending migrations
         via the engine. If it is absent, detect whether the store is *behind*
         (the stdlib-only _store_is_behind probe): if behind, exit non-zero with
         the install-method remediation; if current/empty, print a
         migrate-skipped notice and continue.
      2. Consolidate: import any legacy per-project stores (stdlib-only).
      3. Reindex: rebuild the FTS index.
      4. Admin: resolve from --admin > $SANDESH_ADMIN > interactive prompt (only
         when stdin is a tty and not --yes) > skip-with-notice; then assign_admin.
         A different-name re-assign surfaces the library's refusal (non-zero exit).

    With --check (CR-SAN-038 §S0): run a read-only, non-mutating status probe and
    return WITHOUT the provisioning sweep. It reports one of three states and
    writes nothing to disk (no DB creation, no migrate/consolidate/reindex/assign):
      * store absent      → non-zero, suggest `sandesh init`;
      * admin unset        → non-zero, suggest `sandesh init`;
      * fully provisioned  → exit 0.
    """
    if getattr(args, "check", False):
        return _cmd_init_check()

    # 1. migrate -----------------------------------------------------------
    try:
        import yoyo  # noqa: F401
        import jsonschema  # noqa: F401
        have_migrate = True
    except ImportError:
        have_migrate = False

    if have_migrate:
        _migrate.apply()
    else:
        # [migrate] absent: probe whether the store is schema-behind without
        # importing yoyo. A behind store cannot self-heal → exit with remediation.
        os.makedirs(sdb.root_dir(), exist_ok=True)
        import sqlite3
        probe = sqlite3.connect(sdb.db_path())
        probe.row_factory = sqlite3.Row
        try:
            probe.executescript(sdb._SCHEMA)
            probe.commit()
            behind = sdb._store_is_behind(probe)
        finally:
            probe.close()
        if behind:
            print("[sandesh] " + str(sdb.MigrationRequired(
                "Sandesh store schema is behind and the [migrate] extra is not "
                "installed, so it cannot be auto-upgraded.\n"
                "          Install it with:  " + sdb.install_method_hint())),
                file=sys.stderr)
            sys.exit(1)
        print("migrate: skipped — the [migrate] extra is not installed "
              "(store schema is current).")

    # 2. consolidate -------------------------------------------------------
    summaries = sdb.consolidate()
    imported = sum(1 for e in summaries if not e.get("skipped"))
    print(f"consolidate: {imported} legacy store(s) imported.")

    # 3. reindex -----------------------------------------------------------
    con = sdb.connect()
    try:
        n = sdb.reindex(con)
        print(f"reindex: {n} message(s) indexed.")

        # 4. admin ---------------------------------------------------------
        name = getattr(args, "admin", None) or os.environ.get("SANDESH_ADMIN")
        if not name and not getattr(args, "yes", False) and sys.stdin.isatty():
            entered = input("Sandesh admin name (blank to skip): ").strip()
            name = entered or None
        if name:
            try:
                sdb.assign_admin(con, name)
            except ValueError as exc:
                print(f"[sandesh] {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"admin: {name!r} assigned.")
        else:
            print("admin: skipped — no name given "
                  "(pass --admin <name> or set $SANDESH_ADMIN).")
    finally:
        con.close()

    print("init: done.")
    return 0


def _cmd_init_check():
    """Read-only provisioning probe for `sandesh init --check` (CR-SAN-038 §S0).

    Reports the store's provisioning state and writes NOTHING. Never calls
    sdb.connect() (which would CREATE the DB via executescript(_SCHEMA)); the
    admin check opens the existing DB read-only (URI mode=ro) so no bytes change.

    Returns 0 when fully provisioned; 1 when the store is absent or the admin
    is unset (each with a distinct remediation message that names `sandesh init`).
    """
    db = sdb.db_path()
    if not os.path.exists(db):
        print(
            "[sandesh] store not found — no sandesh.db at "
            f"{db}. Run `sandesh init` to provision the global store.",
            file=sys.stderr,
        )
        return 1

    import sqlite3
    # immutable=1 (not just mode=ro): a WAL-mode store would otherwise create
    # the -wal/-shm sidecar files on open. immutable promises no concurrent
    # writer so SQLite skips the WAL machinery → zero new files, zero byte change.
    con = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    try:
        admin = sdb.admin_name(con)
    finally:
        con.close()

    if admin is None:
        print(
            "[sandesh] admin not assigned — the store exists but has no Sandesh "
            "admin. Run `sandesh init --admin <name>` (or set $SANDESH_ADMIN) "
            "to finish provisioning.",
            file=sys.stderr,
        )
        return 1

    print(f"init --check: provisioned (admin {admin!r}).")
    return 0


DESCRIPTION = ("Sandesh — a SQLite-backed mailbox + wake relay for cooperating "
               "agent sessions.")


def _status_identity(args):
    """(project, address) for the home view: --project/$SANDESH_PROJECT +
    $SANDESH_ADDRESS; a missing one raises _UsageError naming both vars."""
    project = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    address = os.environ.get("SANDESH_ADDRESS")
    if not project or not address:
        raise _UsageError("the home view needs your identity: set $SANDESH_PROJECT "
                          "(or pass --project) and $SANDESH_ADDRESS")
    try:
        sdb.validate_address(address, project)
    except ValueError as exc:
        raise _UsageError(str(exc)) from exc
    return project, address


def _status_fields(project, address):
    con = sdb.connect_readonly()
    listening, unread = False, 0
    if con is not None:
        try:
            listening = sdb.notifier_live(con, address) is not None
            unread = len(sdb.inbox(con, address, unread_only=True))
        finally:
            con.close()
    return {"bin": _bin_path(), "description": DESCRIPTION, "project": project,
            "address": address, "listening": listening, "unread": unread}


def _bin_path():
    """The entry point's absolute path with $HOME collapsed to `~`."""
    path = os.path.abspath(sys.argv[0] or ".")
    home = os.path.expanduser("~")
    if home and (path == home or path.startswith(home + os.sep)):
        path = "~" + path[len(home):]
    return path


def cmd_status(args):
    """`sandesh status` (§S4b) — the read-only dashboard, human form."""
    try:
        project, address = _status_identity(args)
    except _UsageError as exc:
        sys.exit(f"[sandesh] ERROR: {exc}")
    f = _status_fields(project, address)
    state = "● listening" if f["listening"] else "○ not listening"
    print(f"{f['address']} @ {f['project']} · {state} · {f['unread']} unread")
    return 0


# --------------------------------------------------------------------------- #
# Machine-mode verb handlers (CR-SAN-047 §S4). Each `axi_<verb>(args)` does the
# verb's work, prints the human lines (→ stderr under _run_machine) and returns
# `(rc, fields)`; failures RAISE (ValueError/PermissionError/…) and _run_machine
# turns them into the error envelope. Lists emit the DEFAULT columns unless
# `--fields` names a subset of the FULL set (PRD §4.0 P2/P4/P5).

ADDRESSBOOK_FIELDS = ("address", "kind", "status", "listening", "registered")
ADDRESSBOOK_DEFAULT = ("address", "listening")
INBOX_FIELDS = ("id", "from", "to", "cc", "kind", "subject", "created", "re", "unread")
INBOX_DEFAULT = ("id", "from", "subject", "unread")
INBOX_LIMIT = 50
THREAD_FIELDS = ("id", "from", "subject", "created", "re")
THREAD_DEFAULT = ("id", "from", "subject")
SEARCH_FIELDS = ("id", "from", "subject", "kind", "created", "role", "snippet")
SEARCH_DEFAULT = ("id", "from", "subject", "snippet")
BODY_LIMIT = 500          # P3: fetch bodies are cut here unless --full


def _tmpl(project, rest):
    """A `help[]` command template (P9): `sandesh --project <P> <rest>` — the
    project is carried forward (placeholder when unknown); message ids and
    addresses stay `<id>`/`<addr>` placeholders, never concrete values."""
    return f"sandesh --project {project or '<project>'} {rest}"


_SEND_TMPL = 'send --from <addr> --to <addr> --subject "<subject>"'


def _truncate(text):
    """(text, truncated?) — the first BODY_LIMIT chars + the size suffix (P3:
    never omit a body, always state its size)."""
    if len(text) <= BODY_LIMIT:
        return text, False
    return f"{text[:BODY_LIMIT]} (truncated, {len(text)} chars total)", True


def _fields_arg(valid):
    """argparse `type` for `--fields <csv>`: a subset of `valid` (given order
    kept); an unknown name → ArgumentTypeError naming it AND the valid set, which
    argparse routes through parser.error() (exit 2; an envelope in machine mode)."""
    def parse(csv):
        names = _split(csv)
        bad = [n for n in names if n not in valid]
        if bad or not names:
            raise argparse.ArgumentTypeError(
                f"unknown field(s) {', '.join(bad) or '(none)'} — valid: {', '.join(valid)}")
        return names
    return parse


def _pick(row, cols):
    return {c: row[c] for c in cols}


def _joined(recipients, role):
    return ";".join(recipients[role])


def axi_addressbook(args):
    project, _, con = _ctx(args)
    cols = args.fields or ADDRESSBOOK_DEFAULT
    book = sdb.addressbook(con, project)
    _print_addressbook(project, book)
    live = sum(1 for b in book if b["listening"])
    if not book:
        participants = f"0 registered in {project}"
        help_ = [_tmpl(project, "register --address <addr> --kind mainline|track")]
    else:
        participants = [_pick({
            "address": b["address"], "kind": b["kind"],
            "status": "active" if b["active"] else "inactive",
            "listening": b["listening"], "registered": b["registered_at"],
        }, cols) for b in book]
        help_ = [_tmpl(project, _SEND_TMPL), _tmpl(project, "notify --to <addr>")]
    return 0, {"participants": participants, "listening": f"{live}/{len(book)}"}, help_


def axi_inbox(args):
    project, _, con = _ctx(args)
    who = _require_own_addr(args, "to", "--to '<address>'")
    cols = args.fields or INBOX_DEFAULT
    rows = _inbox_rows(con, args, who, not args.all)
    _print_inbox(rows, args.all)
    # The aggregate counts the recipient's WHOLE (filtered) mailbox, read or
    # not, regardless of --all; --limit slices only the rows.
    everything = rows if args.all else _inbox_rows(con, args, who, False)
    unread = sum(1 for r in everything if r["read_at"] is None)
    rows = rows[:args.limit]
    recipients = sdb.message_recipients(con, [r["id"] for r in rows])
    messages = [_pick({
        "id": r["id"], "from": r["from_addr"],
        "to": _joined(recipients[r["id"]], "to"),
        "cc": _joined(recipients[r["id"]], "cc"),
        "kind": r["kind"], "subject": r["subject"], "created": r["created_at"],
        "re": r["in_reply_to"], "unread": r["read_at"] is None,
    }, cols) for r in rows] or f"0 unread for {who}"
    if rows:
        help_ = [_tmpl(project, "fetch --to <addr>"), _tmpl(project, "thread --id <id>")]
    else:
        help_ = [_tmpl(project, _SEND_TMPL)]
    return 0, {"messages": messages, "unread": f"{unread} of {len(everything)}"}, help_


def axi_fetch(args):
    project, store, con = _ctx(args)
    who = _require_own_addr(args, "to", "--to '<address>'")
    items = _fetch_items(con, store, args, who, mark=False)
    if not items:
        _print_fetch(items, who, args.peek)
        return 0, {"messages": f"0 unread for {who}", "marked_read": 0}
    recipients = sdb.message_recipients(con, [it["id"] for it in items])
    rows = [{
        "id": it["id"], "from": it["from"],
        "to": _joined(recipients[it["id"]], "to"),
        "cc": _joined(recipients[it["id"]], "cc"),
        "kind": it["kind"], "subject": it["subject"], "created": it["created_at"],
        "re": it["in_reply_to"][0] if it["in_reply_to"] else None,
    } for it in items]
    bodies, cut = {}, False
    for it in items:
        if it["body"] is None:
            continue
        text, was_cut = (it["body"], False) if args.full else _truncate(it["body"])
        bodies[str(it["id"])] = text
        cut = cut or was_cut
    help_ = [_tmpl(project, "fetch --to <addr> --full  (complete bodies)")] if cut else []
    if not args.peek:
        sdb.mark_read(con, who, [it["id"] for it in items])
    _print_fetch(items, who, args.peek)
    return 0, {"messages": rows, "bodies": bodies,
               "marked_read": 0 if args.peek else len(items)}, help_


def _delivery_fields(con, mid, kind, subject):
    """send/reply confirmation: the recipients as actually written (expanded,
    deduped, sender dropped) + `delivered` = the recipient rows created."""
    recipients = sdb.message_recipients(con, [mid])[mid]
    return {"id": mid, "to": _joined(recipients, "to"), "cc": _joined(recipients, "cc"),
            "kind": kind, "subject": subject,
            "delivered": len(recipients["to"]) + len(recipients["cc"])}


def axi_send(args):
    project, store, con = _ctx(args)
    sender = _require_own_addr(args, "from_", "--from '<your address>'")
    mid = sdb.send(con, store, sender, to=_split(args.to), cc=_split(args.cc),
                   subject=args.subject, kind=args.kind, body_text=_read_body(args),
                   project=project)
    _print_sent(args, mid, sender)
    return 0, _delivery_fields(con, mid, args.kind, args.subject), [_tmpl(project, "thread --id <id>")]


def axi_reply(args):
    project, store, con = _ctx(args)
    sender = _require_own_addr(args, "from_", "--from '<your address>'")
    mid = sdb.reply(con, store, args.to_msg, sender, subject=args.subject,
                    body_text=_read_body(args), reply_all=args.all, project=project)
    _print_replied(args, mid)
    row = con.execute("SELECT kind, subject FROM message WHERE id=?", (mid,)).fetchone()
    fields = _delivery_fields(con, mid, row["kind"], row["subject"])
    fields["re"] = args.to_msg
    return 0, fields, [_tmpl(project, "thread --id <id>")]


def axi_register(args):
    project, _, con = _ctx(args)
    fields = {"address": args.address, "project": project, "kind": args.kind,
              "result": "registered"}
    help_ = [_tmpl(project, "notify --to <addr>")]
    try:
        sdb.register(con, args.address, kind=args.kind, display_name=args.name,
                     by=args.address, project=project)
    except sdb.AlreadyRegistered:          # P6: idempotent no-op, not a failure
        fields["result"] = "already"
        return 0, fields, help_
    _print_registered(args, project)
    return 0, fields, help_


def axi_unregister(args):
    project, _, con = _ctx(args)
    requester = _require_own_addr(args, "as_", "--as '<your address>'")
    sdb.unregister_guards(con, args.address, requester, project=project)
    kind = next((b["kind"] for b in sdb.addressbook(con, project)
                 if b["address"] == args.address), None)
    fields = {"address": args.address, "project": project, "kind": kind, "result": "absent"}
    if not sdb.is_active(con, args.address):    # P6: already gone → no-op
        return 0, fields
    verdict, pid = sdb.unregister(con, args.address, requester=requester, project=project)
    fields["result"] = verdict
    return _print_unregister(args, verdict, pid), fields


def _axi_lifecycle(args, target, op, done):
    """archive/unarchive in machine mode: `op(con)` performs the change and
    returns the evicted watchers; AlreadyInState with the project already in
    `target` → `result: already` (P6); any other refusal propagates."""
    con = sdb.connect()
    try:
        fields = {"project": args.project, "state": target}
        try:
            fields["evicted"] = op(con)
        except sdb.AlreadyInState:
            if sdb.project_state(con, args.project) != target:
                raise
            fields["result"] = "already"
            return 0, fields
    finally:
        con.close()
    done(args)
    return 0, fields


def _axi_dry_run(args, target, preview):
    """archive/unarchive/tombstone `--dry-run` in machine mode (CR-SAN-050 §S3):
    `preview(con)` runs the library's read-only preview (its guards still
    raise → error envelope), prints the human preview (stderr under
    _run_machine) and returns the preview fields; the envelope is those plus
    `dry_run: true`, `project` and the would-be `state`. Nothing is written."""
    _, _, con = _ctx(args)
    fields = {"dry_run": True, "project": args.project, "state": target}
    fields.update(preview(con))
    return 0, fields


def axi_archive(args):
    if args.dry_run:
        def preview(con):
            evicted = sdb.archive_preview(con, args.project, args.by)
            _print_archive_preview(args, evicted)
            return {"evicted": evicted}
        return _axi_dry_run(args, "archived", preview)

    def op(con):
        evicted = sdb.archive_preview(con, args.project, args.by)
        sdb.archive(con, args.project, args.by, force=args.force)
        return evicted
    return _axi_lifecycle(args, "archived", op, _print_archived)


def axi_unarchive(args):
    if args.dry_run:
        def preview(con):
            sdb.unarchive_preview(con, args.project, args.by)
            _print_unarchive_preview(args)
            return {"evicted": []}
        return _axi_dry_run(args, "active", preview)

    def op(con):
        sdb.unarchive(con, args.project, args.by)
        return []
    return _axi_lifecycle(args, "active", op, _print_unarchived)


# --- the remaining verbs (§S4 last sentences, PRD §4.4 last row) ---------------

def axi_search(args):
    project = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    result = sdb.search(_con(), args.to, args.query, limit=args.limit,
                        offset=args.offset, sender_project=args.from_project)
    _print_search(args, result)
    cols = args.fields or SEARCH_DEFAULT
    hits = [_pick({"id": h["id"], "from": h["from"], "subject": h["subject"],
                   "kind": h["kind"], "created": h["created_at"], "role": h["role"],
                   "snippet": h["snippet"]}, cols)
            for h in result["hits"]] or f'0 for "{args.query}"'
    help_ = [_tmpl(project, "thread --id <id>")] if result["hits"] else []
    return 0, {"hits": hits, "total": result["total"], "limit": result["limit"],
               "offset": result["offset"]}, help_


def axi_thread(args):
    project, store, con = _ctx(args)
    cols = args.fields or THREAD_DEFAULT
    chain = sdb.thread(con, args.id)
    if not chain:
        raise ValueError(f"no such message #{args.id}")
    _print_thread(chain)
    projects_dir = os.path.dirname(os.path.normpath(store))
    caller = getattr(args, "as_", None)
    if caller:
        try:
            sdb.validate_address(caller, project)
        except ValueError as e:
            raise ValueError(f"--as {caller!r}: {e}") from e
    else:
        caller = os.environ.get("SANDESH_ADDRESS") or os.environ.get("WF_TRACK")
        if caller:
            try:
                sdb.validate_address(caller, project)
            except ValueError:
                caller = None  # an invalid env caller is treated as absent (0.4.0 rule)
    rows, bodies, cut, withheld = [], {}, False, 0
    for message in chain:
        if _is_hole(message):
            continue
        rows.append(_pick({"id": message["id"], "from": message["from_addr"],
                           "subject": message["subject"], "created": message["created_at"],
                           "re": message["in_reply_to"]}, cols))
        if not message["body_path"]:
            continue
        if not caller or (caller != message["from_addr"] and not con.execute(
                "SELECT 1 FROM message_recipient WHERE message_id=? AND recipient=?",
                (message["id"], caller)).fetchone()):
            withheld += 1
            continue
        path = sdb.message_body_path(con, projects_dir, message)
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except FileNotFoundError:
            body = f"(body file missing: {path})"
        text, was_cut = (body, False) if args.full else _truncate(body)
        bodies[str(message["id"])] = text
        cut = cut or was_cut
    help_ = [_tmpl(project, "thread --id <id> --full  (complete bodies)")] if cut else []
    payload = {"chain": rows, "bodies": bodies,
               "incomplete": any(_is_hole(m) for m in chain)}
    if withheld:
        payload["withheld"] = withheld
        help_.append(_tmpl(project, "thread --id <id> --as '<your address>'"))
    return 0, payload, help_


def axi_projects(args):
    rows = _project_rows(_con(), getattr(args, "all", False))
    _print_projects(rows)
    projects = [{"project": r["project_id"], "state": r["state"],
                 "cross_project": bool(r["xproj_granted_at"])} for r in rows] or "0 set up"
    return 0, {"projects": projects}, [_tmpl(None, "setup"), _tmpl(None, "addressbook")]


def axi_setup(args):
    project = _project(args)
    store = sdb.setup(project)
    _print_setup(project, store)
    return 0, {"project": project, "store": store}


def _axi_xproj(args, op, done, granted):
    _, _, con = _ctx(args)
    op(con, args.project, by=args.by)
    done(args)
    return 0, {"project": args.project, "cross_project": granted}


def axi_grant(args):
    return _axi_xproj(args, sdb.grant_xproj, _print_granted, True)


def axi_revoke(args):
    return _axi_xproj(args, sdb.revoke_xproj, _print_revoked, False)


def axi_tombstone(args):
    """No prompts in machine mode (P6): without --yes (and not --dry-run) it is
    a usage error; --dry-run returns the purge-count preview (§S3); with --yes
    the library call runs directly so a refusal's reason reaches the envelope
    (an already-tombstoned project is `result: already`)."""
    if not args.yes and not args.dry_run:
        raise _UsageError("tombstone in machine mode needs --yes "
                          "(no interactive confirmation)")
    if args.dry_run:
        def preview(con):
            counts = sdb.tombstone_preview(con, args.project, args.by)
            _print_tombstone_preview(args, counts)
            return {"messages": counts["internal_messages"],
                    "bodies": counts["body_files"],
                    "cross_project": counts["cross_project_messages"]}
        return _axi_dry_run(args, "tombstoned", preview)
    fields = {"project": args.project, "state": "tombstoned"}
    try:
        sdb.tombstone_project(_con(), args.project, args.by, force=args.force)
    except ValueError as exc:
        if "already tombstoned" not in str(exc):
            raise
        fields["result"] = "already"
        return 0, fields
    _print_tombstoned(args)
    return 0, fields


_STEP_LINE = re.compile(r"^([A-Za-z][\w .-]*?): (.+)$")


def axi_steps(args):
    """init/migrate/consolidate/reindex: run the human handler, replaying its
    stdout/stderr (stdout lands on stderr under _run_machine) and turning each
    stdout line into a `{step, result}` row (`step: result` lines split; others
    are the verb's). A non-zero return 	a _Failed with the steps so far + the
    handler's stderr text as the error (the exit code equals human mode's)."""
    out, err = io.StringIO(), io.StringIO()
    exit_detail = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = args.fn(args) or 0
    except SystemExit as exc:
        code = exc.code
        rc = code if isinstance(code, int) else (1 if code else 0)
        exit_detail = code if isinstance(code, str) else None
    finally:
        print(out.getvalue(), end="")
        print(err.getvalue(), end="", file=sys.stderr)
    steps = []
    for line in out.getvalue().splitlines():
        if not line.strip():
            continue
        m = _STEP_LINE.match(line)
        steps.append({"step": m.group(1), "result": m.group(2)} if m
                     else {"step": args.cmd, "result": line.strip()})
    if rc:
        lines = [ln[len(_ERR_PREFIX):] if ln.startswith(_ERR_PREFIX) else ln
                 for ln in err.getvalue().splitlines() if ln.strip()]
        detail = "\n".join(lines) or exit_detail or f"{args.cmd} failed (exit {rc})"
        if detail.startswith(_ERR_PREFIX):
            detail = detail[len(_ERR_PREFIX):]
        raise _Failed(rc, detail, {"steps": steps})
    return 0, {"steps": steps}


def axi_status(args):
    """The home view (P8/P10): `sandesh` with no subcommand or `sandesh status`."""
    project, address = _status_identity(args)
    fields = _status_fields(project, address)
    return 0, fields, [_tmpl(project, "fetch --to <addr>"), _tmpl(project, "notify --to <addr>")]


AXI_FN = {
    "addressbook": axi_addressbook, "inbox": axi_inbox, "fetch": axi_fetch,
    "send": axi_send, "reply": axi_reply, "register": axi_register,
    "unregister": axi_unregister, "archive": axi_archive, "unarchive": axi_unarchive,
    "search": axi_search, "thread": axi_thread, "projects": axi_projects,
    "setup": axi_setup, "grant": axi_grant, "revoke": axi_revoke,
    "tombstone": axi_tombstone, "status": axi_status, "notify": axi_notify,
    "init": axi_steps, "migrate": axi_steps, "consolidate": axi_steps, "reindex": axi_steps,
}

# Verbs whose AXI handler writes its own final envelope (notify: on every exit
# path incl. signals) — _run_machine calls them directly, outside its stdout
# redirect, and emits nothing itself.
_EMITS_OWN_ENVELOPE = frozenset({"notify"})


def build_parser(axi_format="human", axi_context=None):
    # --project is shared so it works BOTH before and after the subcommand:
    #   sandesh --project X setup    AND    sandesh setup --project X
    # Same SUPPRESS idiom for --format (CR-SAN-047 §S3), on its own parent so the
    # verbs that take no routing --project (or define their own) still accept
    # --format after the verb.
    fmt_common = _Parser(add_help=False)
    fmt_common.add_argument("--format", choices=list(axi.FORMATS), default=argparse.SUPPRESS,
                            help="output format (overrides $SANDESH_FORMAT; default human)")
    common = _Parser(add_help=False, parents=[fmt_common])
    # SUPPRESS: an absent --project in one position must not clobber a value given in
    # the other (so it works both before AND after the subcommand).
    common.add_argument("--project", default=argparse.SUPPRESS,
                        help="project id (overrides $SANDESH_PROJECT)")

    # _Parser (and, via argparse's default parser_class=type(parent), every
    # subparser) turns usage errors into an AXI envelope when the pre-scanned
    # format is toon/json; the format/context are stamped on at the end.
    ap = _Parser(prog="sandesh", parents=[common],
                 description="Sandesh messaging CLI (standalone).",
                 formatter_class=argparse.RawDescriptionHelpFormatter,
                 epilog=(
                     "machine output (for agents):\n"
                     "  --format {human,toon,json}   before or after the verb, or $SANDESH_FORMAT;\n"
                     "                               default human. toon/json print one AXI envelope\n"
                     "                               on stdout (human text -> stderr; exit codes\n"
                     "                               unchanged). Envelope shape, --fields/--limit/\n"
                     "                               --full, the home view and the notify exit table:\n"
                     "                               docs/USER_GUIDE.md, section \"Machine output for\n"
                     "                               agents\" (https://axi.md, https://toonformat.dev)\n"
                     "  sandesh <verb> --help        the per-verb reference"
                 ))
    ap.add_argument("--version", action="version", version=f"sandesh {__version__}")
    # required=False (§S4b): a bare `sandesh` is the machine-mode home view;
    # main() re-creates argparse's "required" error for human mode.
    sub = ap.add_subparsers(dest="cmd", required=False)

    sub.add_parser("status", parents=[common],
                   help="the home view: your address, listening state + unread count "
                        "(needs $SANDESH_PROJECT/$SANDESH_ADDRESS)").set_defaults(fn=cmd_status)

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
    p = sub.choices["addressbook"]
    p.add_argument("--fields", type=_fields_arg(ADDRESSBOOK_FIELDS), default=None,
                   metavar="CSV", help="machine-mode columns (subset of "
                   f"{','.join(ADDRESSBOOK_FIELDS)}; default {','.join(ADDRESSBOOK_DEFAULT)})")

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
    p.add_argument("--fields", type=_fields_arg(INBOX_FIELDS), default=None, metavar="CSV",
                   help="machine-mode columns (subset of "
                        f"{','.join(INBOX_FIELDS)}; default {','.join(INBOX_DEFAULT)})")
    p.add_argument("--limit", type=_positive_int, default=INBOX_LIMIT,
                   help=f"machine-mode row cap (default {INBOX_LIMIT}; the aggregate is unsliced)")
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
    p.add_argument("--full", action="store_true",
                   help=f"machine-mode: complete bodies (default: first {BODY_LIMIT} chars)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("thread", parents=[common], help="show a message's reply chain")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--fields", type=_fields_arg(THREAD_FIELDS), default=None, metavar="CSV",
                   help="machine-mode columns (subset of "
                        f"{','.join(THREAD_FIELDS)}; default {','.join(THREAD_DEFAULT)})")
    p.add_argument("--full", action="store_true",
                   help=f"machine-mode: complete bodies (default: first {BODY_LIMIT} chars)")
    p.add_argument("--as", dest="as_",
                   help="your address (default $SANDESH_ADDRESS, then $WF_TRACK)")
    p.set_defaults(fn=cmd_thread)

    p = sub.add_parser(
        "notify", parents=[common],
        help="block until 'to' mail arrives (the mailbox watcher)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run this in the background via your host's background-task mechanism;\n"
            "when it stops, your harness wakes you. It can stop for several reasons:\n"
            "\n"
            "Why the listener stopped\n"
            "  mail arrived   new 'to' mail is waiting — resume and `sandesh fetch`.\n"
            "  timed out      no mail arrived before --timeout expired — just relaunch\n"
            "                 the listener and keep waiting.\n"
            "  project retired (tombstoned) — the whole project was permanently\n"
            "                 retired; do NOT relaunch the listener.\n"
            "  taken over     another listener (another notifier) evicted this one and\n"
            "                 took the address over — do not relaunch.\n"
            "  already live   a duplicate listener was already running for this address\n"
            "                 (dedup) — this one did not start.\n"
            "  error          a usage or config problem — fix it, then relaunch.\n"
        ),
    )
    p.add_argument("--to", required=True)
    p.add_argument("--timeout", type=int, default=_notify.DEFAULT_TIMEOUT_SECS)
    p.set_defaults(fn=cmd_notify)

    # CR-SAN-022 DEC-B: the migrate engine targets the single global DB, so the
    # migrate subparser deliberately does NOT inherit the common --project
    # parent (`sandesh migrate --project X` is an argparse error). The
    # pre-subcommand `sandesh --project X migrate` form still parses via the
    # top-level parser; migrate simply ignores the value.
    p = sub.add_parser("migrate", parents=[fmt_common],
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
    p = sub.add_parser("consolidate", parents=[fmt_common],
                       help="import legacy per-project stores into the global DB "
                            "(one-time; legacy files become sandesh.db.pre-global)")
    p.set_defaults(fn=cmd_consolidate)

    # CR-SAN-027 §S3: full-text search over the caller's OWN mail. Parentless
    # like migrate/consolidate — the engine targets the single global DB, so
    # `search --project X` is an argparse error (no per-project routing).
    p = sub.add_parser("search", parents=[fmt_common],
                       help="full-text search over your own mail (FTS5 syntax: "
                           "\"quoted phrases\", AND/OR/NOT)")
    p.add_argument("query", help="the FTS5 query")
    p.add_argument("--to", required=True, help="your address (whose mail to search)")
    p.add_argument("--from-project", dest="from_project",
                        help="only hits whose sender belongs to this project")
    p.add_argument("--fields", type=_fields_arg(SEARCH_FIELDS), default=None, metavar="CSV",
                   help="machine-mode columns (subset of "
                        f"{','.join(SEARCH_FIELDS)}; default {','.join(SEARCH_DEFAULT)})")
    p.add_argument("--limit", type=_positive_int, default=20, help="page size (default 20)")
    p.add_argument("--offset", type=int, default=0, help="page start (default 0)")
    p.set_defaults(fn=cmd_search)

    # CR-SAN-027 §S2: rebuild the whole FTS index from the message rows + body
    # files. Parentless and arg-free — global DB, plumbing only.
    p = sub.add_parser("reindex", parents=[fmt_common],
                       help="rebuild the full-text search index from messages + bodies")
    p.set_defaults(fn=cmd_reindex)

    # CR-SAN-023 §S2: admin-only verbs (CLI-only — never MCP). Like migrate/
    # consolidate, these deliberately do NOT inherit parents=[common]: their
    # --project is the TARGET project of the grant, not routing context (avoids
    # the dual-position SUPPRESS trap). There is NO `sandesh admin` subcommand —
    # admin assignment happens only in install.sh via $SANDESH_ADMIN (PRD O3).
    p = sub.add_parser("grant", parents=[fmt_common],
                       help="grant cross-project sending to a project (Sandesh admin only)")
    p.add_argument("--cross-project", dest="cross_project", action="store_true",
                   required=True, help="the cross-project access grant (required)")
    p.add_argument("--project", required=True, help="the TARGET project receiving the grant")
    p.add_argument("--by", required=True, help="your admin name (must match the stored admin)")
    p.set_defaults(fn=cmd_grant)

    p = sub.add_parser("revoke", parents=[fmt_common],
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
    p = sub.add_parser("archive", parents=[fmt_common],
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

    p = sub.add_parser("unarchive", parents=[fmt_common],
                       help="reactivate an archived project (its own Mainline only)")
    p.add_argument("--project", required=True, help="the project to reactivate")
    p.add_argument("--by", required=True,
                   help="the project's own Mainline address")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="report would-be state; write nothing")
    p.set_defaults(fn=cmd_unarchive)

    p = sub.add_parser("tombstone", parents=[fmt_common],
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

    # CR-SAN-036 §S3: global provisioning sweep (migrate → consolidate →
    # reindex → admin). Parentless like migrate/consolidate/reindex — it
    # provisions the single global DB, so it takes no --project. CLI-only
    # (never an MCP tool — AC6).
    p = sub.add_parser("init", parents=[fmt_common],
                       help="provision the global store "
                            "(migrate + consolidate + reindex + admin)")
    p.add_argument("--admin", help="assign the Sandesh admin name "
                                   "(or set $SANDESH_ADMIN)")
    p.add_argument("--yes", action="store_true",
                   help="non-interactive: skip the admin prompt when no name given")
    p.add_argument("--check", action="store_true",
                   help="read-only status probe: report provisioning state and "
                        "exit (writes nothing; non-zero if store absent or admin unset)")
    p.set_defaults(fn=cmd_init)
    for parser in (ap, *sub.choices.values()):
        parser.axi_format = axi_format
        parser.axi_context = axi_context
    return ap


def _parse(argv, pre_fmt, pre_ctx):
    """Build + parse. A missing subcommand in human mode is argparse's usual
    "required" error (unchanged, exit 2); in machine mode it is the home view
    (§S4b), resolved by main(). The parser is a local here so it is released
    as soon as parsing is done."""
    parser = build_parser(pre_fmt, pre_ctx)
    args = parser.parse_args(argv)
    if args.cmd is None and _resolve_format(args) == "human":
        parser.error("the following arguments are required: cmd")
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # Pre-argparse format resolution so usage errors can be envelopes (§S4b).
    pre_fmt = _prescan_format(argv)
    pre_ctx = {}
    pre_project = _prescan(argv, "--project") or os.environ.get("SANDESH_PROJECT")
    if pre_project:
        pre_ctx["project"] = pre_project
    args = _parse(argv, pre_fmt, pre_ctx)
    fmt = _resolve_format(args)
    if args.cmd is None:                 # machine mode, no subcommand: the home view
        args.cmd, args.fn = "status", cmd_status
    try:
        if fmt != "human":
            return _run_machine(args, fmt)
        try:
            return args.fn(args)
        except sdb.MigrationRequired as exc:
            # A schema-behind store with no [migrate] extra: surface the library's
            # message as a clean '[sandesh]' line (never a raw traceback) and exit
            # non-zero (CR-SAN-037 AC4).
            print(f"[sandesh] {exc}", file=sys.stderr)
            return 1
    finally:
        _close_connections()


if __name__ == "__main__":
    sys.exit(main())
