#!/usr/bin/env python3
"""migrate.py — the Sandesh schema-migration engine entrypoint.

This module is the single home for all `yoyo`/`jsonschema` use. It is imported
by `sandesh.cli` at module load (so the CLI can wire the `migrate` subcommand),
which means importing it must stay stdlib-pure: the heavy third-party deps are
**lazy-imported inside function bodies only** — never at module top.

The `[migrate]` extra (`pip install 'sandesh-relay[migrate]'`) provides yoyo +
jsonschema. When it is absent, `_require_deps()` prints a friendly install hint
and exits non-zero — no raw traceback leaks to the user.

Wave/cycle scope: this cycle wires only the friendly-absent guard and the CLI
dispatch entry. The apply/status/rollback/check/dump-schema/diff machinery lands
in later cycles.
"""

import os
import sys

# The pip install target for the optional migration dependencies.
_EXTRA_HINT = (
    "[sandesh] the migration engine needs the [migrate] extra.\n"
    "          Install it with:  pip install 'sandesh-relay[migrate]'"
)


def _require_deps():
    """Lazy-import yoyo + jsonschema; on ImportError print the friendly hint and
    exit non-zero.

    Returns the imported (yoyo, jsonschema) modules so callers can use them
    without re-importing. Kept inside the function body so importing this module
    never pulls the third-party deps into sys.modules (AC1).
    """
    try:
        import yoyo  # noqa: F401  (re-exported to caller)
        import jsonschema  # noqa: F401
    except ImportError:
        print(_EXTRA_HINT, file=sys.stderr)
        sys.exit(1)
    return yoyo, jsonschema


# --------------------------------------------------------------------------- #
# engine — migrations source, store DB path, apply, status
#
# All yoyo use is lazy-imported inside the function bodies below so importing
# this module stays stdlib-pure (AC1). The packaged migrations live alongside
# this module under sandesh/migrations/.

# The four core tables a pre-yoyo store already has (baseline-adoption probe).
_BASELINE_ID = "0001-baseline"
_CORE_TABLES = ("address", "message", "message_recipient", "notifier")


def migrations_dir():
    """Absolute path to the migrations directory.

    Honours the ``SANDESH_MIGRATIONS_DIR`` environment override (a test/dev hook
    matching the project's ``XDG_DATA_HOME`` / ``SANDESH_POLL_SECONDS`` pattern):
    when set, its value is returned verbatim. Otherwise the packaged
    ``sandesh/migrations/`` directory is resolved relative to this module's file
    so it works from an installed package (force-included into the wheel) as
    well as from a source checkout.
    """
    override = os.environ.get("SANDESH_MIGRATIONS_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def _schema_dir():
    """Absolute path to the packaged ``sandesh/schema/`` directory.

    Resolved relative to this module's file so it works from an installed
    package (force-included into the wheel) as well as from a source checkout —
    mirroring ``migrations_dir()``.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")


def _snapshot_path():
    """Absolute path to the derived ``current-schema.json`` snapshot."""
    return os.path.join(_schema_dir(), "current-schema.json")


def _db_path(project_id):
    """Absolute path to a project store's ``sandesh.db`` file.

    Routes through ``sandesh_db`` so the XDG data-home logic (and any
    ``$XDG_DATA_HOME`` override) is honoured — no path duplication here.
    """
    from . import sandesh_db
    store = sandesh_db.store_dir(project_id)
    os.makedirs(store, exist_ok=True)
    return os.path.join(store, sandesh_db.DB_FILE)


def _backend(project_id):
    """Open a yoyo SQLite backend for ``project_id``'s store DB."""
    yoyo, _jsonschema = _require_deps()
    db_path = _db_path(project_id)
    return yoyo.get_backend("sqlite:///" + db_path)


def _read_migrations():
    """Read the packaged migrations as a yoyo ``MigrationList``."""
    yoyo, _jsonschema = _require_deps()
    return yoyo.read_migrations(migrations_dir())


def _core_tables_exist(db_path):
    """True if all four core tables already exist in ``db_path`` (a pre-yoyo
    store provisioned by ``sandesh_db.setup`` before the migration engine)."""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        con.close()
    present = {r[0] for r in rows}
    return all(t in present for t in _CORE_TABLES)


def apply(project_id):
    """Apply pending migrations to ``project_id``'s store.

    Baseline-adoption glue (AC4): if the four core tables already exist (a
    pre-yoyo store) AND ``0001-baseline`` has not yet been recorded, MARK it
    applied — recording it without re-running the CREATE TABLE statements that
    would otherwise collide with the existing tables — then apply any remaining
    pending migrations normally. A brand-new empty store has no tables, so
    ``0001-baseline`` simply applies normally.
    """
    db_path = _db_path(project_id)
    backend = _backend(project_id)
    migrations = _read_migrations()

    baseline = next((m for m in migrations if m.id == _BASELINE_ID), None)
    if (
        baseline is not None
        and _core_tables_exist(db_path)
        and not backend.is_applied(baseline)
    ):
        # Pre-yoyo store: record the baseline as applied without running it.
        from yoyo.migrations import MigrationList
        backend.mark_migrations(MigrationList([baseline]))

    backend.apply_migrations(backend.to_apply(migrations))


def status(project_id):
    """Return ``(applied_ids, pending_ids)`` for ``project_id``'s store.

    ``applied_ids`` are the migration ids already recorded in yoyo's tracking
    table; ``pending_ids`` are those not yet applied — both in migration order.
    """
    backend = _backend(project_id)
    migrations = _read_migrations()
    pending = {m.id for m in backend.to_apply(migrations)}
    applied_ids = [m.id for m in migrations if m.id not in pending]
    pending_ids = [m.id for m in migrations if m.id in pending]
    return applied_ids, pending_ids


def _live_shape(db_path):
    """Return the live DB shape in the §S3 snapshot format:

        {"<table>": {"<col>": {"type", "notnull", "pk", "default"}}}

    derived from ``PRAGMA table_info`` for each of the four core tables. Only
    the core tables are compared (yoyo's ``_yoyo_migration`` bookkeeping table
    is intentionally excluded).
    """
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        shape = {}
        for table in _CORE_TABLES:
            cols = {}
            for r in con.execute(f"PRAGMA table_info({table})").fetchall():
                cols[r["name"]] = {
                    "type": r["type"],
                    "notnull": r["notnull"],
                    "pk": r["pk"],
                    "default": r["dflt_value"],
                }
            shape[table] = cols
    finally:
        con.close()
    return shape


def _drift(db_path):
    """Compare the live DB shape against ``current-schema.json``.

    Returns a list of human-readable drift descriptions (empty when the live
    shape matches the snapshot for every core table/column). Each description
    names the drifting table and, where applicable, column so the user can
    diagnose the mismatch.
    """
    import json
    with open(_snapshot_path()) as fh:
        snapshot = json.load(fh)
    expected_tables = snapshot.get("tables", {})
    live = _live_shape(db_path)

    drifts = []
    for table, table_data in expected_tables.items():
        expected_cols = table_data.get("columns", {})
        live_cols = live.get(table)
        if live_cols is None:
            drifts.append(f"table '{table}' is missing from the live store")
            continue
        for col, expected in expected_cols.items():
            if col not in live_cols:
                drifts.append(f"{table}.{col} is missing from the live store")
                continue
            if live_cols[col] != expected:
                drifts.append(
                    f"{table}.{col} differs: expected {expected!r}, live {live_cols[col]!r}"
                )
        for col in live_cols:
            if col not in expected_cols:
                drifts.append(f"{table}.{col} is an unexpected column in the live store")
    return drifts


def check(project_id):
    """Run the read-only ``--check`` gate for ``project_id``'s store.

    Returns an exit code (0 success, non-zero on pending) following the
    user-decided strictness:

      * **pending** — unapplied migrations → print/list them, return non-zero.
      * **drift** — live ``PRAGMA table_info`` shape ≠ ``current-schema.json`` →
        print a WARNING naming the drift, but return 0 (drift is non-fatal).
      * **clean** — fully migrated AND shape matches → return 0, no noise.

    Performs no writes.
    """
    applied_ids, pending_ids = status(project_id)
    if pending_ids:
        print(
            f"[{project_id}] migrations pending (run `sandesh migrate`): "
            + ", ".join(pending_ids),
            file=sys.stderr,
        )
        return 1

    db_path = _db_path(project_id)
    drifts = _drift(db_path)
    if drifts:
        print(f"[{project_id}] WARNING: schema drift detected (non-fatal):")
        for d in drifts:
            print(f"  - {d}")
        return 0

    # Clean: do not echo the project id here — a project id can incidentally
    # contain the substring 'drift'/'pending', which a clean-store check must
    # never surface in its output.
    print("OK: fully migrated, live shape matches the committed snapshot")
    return 0


def _format_status(project_id, applied_ids, pending_ids):
    """Render a single project's migration status as a human-readable line set.

    Names every applied id (e.g. ``0001-baseline``) and conveys the pending
    count/ids — including an explicit "0 pending" when none remain.
    """
    applied_part = ", ".join(applied_ids) if applied_ids else "(none)"
    if pending_ids:
        pending_part = f"{len(pending_ids)} pending: " + ", ".join(pending_ids)
    else:
        pending_part = "0 pending"
    return (
        f"[{project_id}] applied: {applied_part}\n"
        f"[{project_id}] {pending_part}"
    )


def cmd_migrate(args):
    """CLI dispatch entry for `sandesh migrate`.

    Dispatches on the parsed flags:
      * default (no ``--status``/``--all``) → apply pending migrations to the
        single ``--project`` store.
      * ``--status`` → print applied/pending for the project (read-only).
      * ``--all`` → operate on every store discovered via
        ``sandesh_db.list_projects()``. With ``--status`` it reports each store;
        otherwise it applies to each. Apply is **fail-fast**: the first store
        whose apply raises aborts the run with a non-zero exit and stores ordered
        after it are left untouched.

    The dependency guard runs first — without the [migrate] extra there is
    nothing to do but tell the user how to install it (and exit non-zero).
    """
    _require_deps()

    do_all = getattr(args, "all", False)
    do_status = getattr(args, "status", False)
    do_check = getattr(args, "check", False)

    if do_check:
        project_id = _project_from_args(args)
        return check(project_id)

    if do_all:
        from . import sandesh_db
        projects = sandesh_db.list_projects()
        if do_status:
            for pid in projects:
                applied_ids, pending_ids = status(pid)
                print(_format_status(pid, applied_ids, pending_ids))
            return 0
        # Apply to each store, fail-fast on the first error.
        for pid in projects:
            try:
                apply(pid)
            except Exception as exc:  # noqa: BLE001 — surface which store failed, then abort
                print(
                    f"[sandesh] migrate --all aborted: project {pid!r} failed: {exc}",
                    file=sys.stderr,
                )
                return 1
        return 0

    # Single-project paths.
    project_id = _project_from_args(args)
    if do_status:
        applied_ids, pending_ids = status(project_id)
        print(_format_status(project_id, applied_ids, pending_ids))
        return 0

    apply(project_id)
    return 0


def _project_from_args(args):
    """Resolve the project id from parsed CLI args / ``$SANDESH_PROJECT``.

    Mirrors ``cli._project``: ``--project`` (either position) takes precedence,
    falling back to ``$SANDESH_PROJECT``; absent both, exit with a friendly error.
    """
    project_id = getattr(args, "project", None) or os.environ.get("SANDESH_PROJECT")
    if not project_id:
        print(
            "[sandesh] ERROR: pass --project <id> (or set $SANDESH_PROJECT).",
            file=sys.stderr,
        )
        sys.exit(2)
    return project_id
