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

# The four baseline tables a pre-yoyo store already has. This constant is ONLY
# the baseline-adoption probe (CR-SAN-022 DRIFT-4): shape functions enumerate
# sqlite_master dynamically and must never hard-code a table list.
_BASELINE_ID = "0001-baseline"
_BASELINE_TABLES = ("address", "message", "message_recipient", "notifier")


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


def _db_path():
    """Absolute path to the single global ``sandesh.db`` (CR-SAN-022 DEC-B).

    Routes through ``sandesh_db.db_path()`` so the XDG data-home logic (and any
    ``$XDG_DATA_HOME`` override) is honoured — no path duplication here. Ensures
    the parent directory exists so yoyo can create the file on first apply.
    """
    from . import sandesh_db
    db_path = sandesh_db.db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return db_path


def _backend():
    """Open a yoyo SQLite backend for the global Sandesh DB."""
    yoyo, _jsonschema = _require_deps()
    return yoyo.get_backend("sqlite:///" + _db_path())


def _read_migrations():
    """Read the packaged migrations as a yoyo ``MigrationList``."""
    yoyo, _jsonschema = _require_deps()
    return yoyo.read_migrations(migrations_dir())


def _baseline_tables_exist(db_path):
    """True if all four baseline tables already exist in ``db_path`` (a pre-yoyo
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
    return all(t in present for t in _BASELINE_TABLES)


def apply():
    """Apply pending migrations to the global Sandesh DB.

    Baseline-adoption glue (AC4): if the four baseline tables already exist (a
    pre-yoyo store) AND ``0001-baseline`` has not yet been recorded, MARK it
    applied — recording it without re-running the CREATE TABLE statements that
    would otherwise collide with the existing tables — then apply any remaining
    pending migrations normally. A brand-new empty store has no tables, so
    ``0001-baseline`` simply applies normally.
    """
    db_path = _db_path()
    backend = _backend()
    migrations = _read_migrations()

    baseline = next((m for m in migrations if m.id == _BASELINE_ID), None)
    if (
        baseline is not None
        and _baseline_tables_exist(db_path)
        and not backend.is_applied(baseline)
    ):
        # Pre-yoyo store: record the baseline as applied without running it.
        from yoyo.migrations import MigrationList
        backend.mark_migrations(MigrationList([baseline]))

    backend.apply_migrations(backend.to_apply(migrations))


def rollback():
    """Roll back the single most-recent applied migration on the global DB.

    ``backend.to_rollback`` returns the applied migrations in reverse order
    (most-recent first); we take only the first so a ``--rollback`` undoes one
    step (e.g. 0002 → 0002 pending again, 0001 stays applied). A no-op when
    nothing is applied.
    """
    yoyo, _jsonschema = _require_deps()
    backend = _backend()
    migrations = _read_migrations()
    to_rollback = backend.to_rollback(migrations)
    if not to_rollback:
        return
    from yoyo.migrations import MigrationList
    backend.rollback_migrations(MigrationList([to_rollback[0]]))


def status():
    """Return ``(applied_ids, pending_ids)`` for the global Sandesh DB.

    ``applied_ids`` are the migration ids already recorded in yoyo's tracking
    table; ``pending_ids`` are those not yet applied — both in migration order.
    """
    backend = _backend()
    migrations = _read_migrations()
    pending = {m.id for m in backend.to_apply(migrations)}
    applied_ids = [m.id for m in migrations if m.id not in pending]
    pending_ids = [m.id for m in migrations if m.id in pending]
    return applied_ids, pending_ids


def _live_shape(db_path):
    """Return the live DB shape in the §S3 snapshot format:

        {"<table>": {"<col>": {"type", "notnull", "pk", "default"}}}

    derived from ``PRAGMA table_info`` for every business table enumerated
    dynamically from ``sqlite_master`` (CR-SAN-022 DRIFT-4 — no hard-coded
    table list, so new tables like ``project`` are visible). Internal tables
    are excluded: ``sqlite_*`` plus yoyo's bookkeeping tables (``_yoyo*`` /
    ``yoyo*``) and the FTS family (``message_fts*`` — a derived, regenerable
    index, not schema-of-record; CR-SAN-027 §S1).
    """
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        tables = [
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if not r["name"].startswith(("sqlite_", "_yoyo", "yoyo", "message_fts"))
        ]
        shape = {}
        for table in tables:
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


def _snapshot_from_shape(shape):
    """Wrap a flat ``_live_shape`` dict into the §S3 snapshot structure:

        {"tables": {"<table>": {"columns": {"<col>": {…}}}}}
    """
    return {"tables": {table: {"columns": cols} for table, cols in shape.items()}}


def dump_schema():
    """Return the live shape of the global Sandesh DB in the §S3 snapshot
    structure ``{"tables": {table: {"columns": {col: {…}}}}}``.

    Read-only: derived purely from ``PRAGMA table_info`` via ``_live_shape``
    (dynamic ``sqlite_master`` enumeration). On a fully-migrated store this
    equals the committed ``current-schema.json`` (modulo key ordering).
    """
    return _snapshot_from_shape(_live_shape(_db_path()))


def _snapshot_to_tables_dict(snapshot):
    """Flatten a §S3 snapshot into ``{table: {col: {type,notnull,pk,default}}}``."""
    return {
        table: dict(table_data.get("columns", {}))
        for table, table_data in snapshot.get("tables", {}).items()
    }


def diff(old_snapshot_path):
    """Compare an OLD snapshot file against the freshly-dumped CURRENT live
    shape of the global Sandesh DB.

    Returns a dict with three lists:

      * ``added``   — column present in current but absent in old; each entry is
        ``{"table", "column", "current": {…}}``.
      * ``removed`` — column present in old but absent in current; each entry is
        ``{"table", "column", "old": {…}}``.
      * ``changed`` — column present in both but with a differing descriptor;
        each entry is ``{"table", "column", "old": {…}, "current": {…}}``.

    Read-only.
    """
    import json
    with open(old_snapshot_path) as fh:
        old_snapshot = json.load(fh)
    old = _snapshot_to_tables_dict(old_snapshot)
    current = _snapshot_to_tables_dict(dump_schema())

    added, removed, changed = [], [], []
    tables = sorted(set(old) | set(current))
    for table in tables:
        old_cols = old.get(table, {})
        cur_cols = current.get(table, {})
        for col in sorted(set(old_cols) | set(cur_cols)):
            in_old = col in old_cols
            in_cur = col in cur_cols
            if in_cur and not in_old:
                added.append(
                    {"table": table, "column": col, "current": cur_cols[col]}
                )
            elif in_old and not in_cur:
                removed.append(
                    {"table": table, "column": col, "old": old_cols[col]}
                )
            elif old_cols[col] != cur_cols[col]:
                changed.append(
                    {
                        "table": table,
                        "column": col,
                        "old": old_cols[col],
                        "current": cur_cols[col],
                    }
                )
    return {"added": added, "removed": removed, "changed": changed}


def _format_diff(report):
    """Render a diff report (``diff()`` return value) as human-readable text.

    Names each differing table/column so the developer can hand-write the next
    migration step. Returns the rendered text (a trailing-newline-free string).
    """
    lines = []
    for entry in report.get("added", []):
        lines.append(f"+ added   {entry['table']}.{entry['column']} {entry['current']!r}")
    for entry in report.get("removed", []):
        lines.append(f"- removed {entry['table']}.{entry['column']} {entry['old']!r}")
    for entry in report.get("changed", []):
        lines.append(
            f"~ changed {entry['table']}.{entry['column']} "
            f"old={entry['old']!r} current={entry['current']!r}"
        )
    if not lines:
        return "no differences: live shape matches the old snapshot"
    return "\n".join(lines)


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


def check():
    """Run the read-only ``--check`` gate against the global Sandesh DB.

    Returns an exit code (0 success, non-zero on pending) following the
    user-decided strictness:

      * **pending** — unapplied migrations → print/list them, return non-zero.
      * **drift** — live ``PRAGMA table_info`` shape ≠ ``current-schema.json`` →
        print a WARNING naming the drift, but return 0 (drift is non-fatal).
      * **clean** — fully migrated AND shape matches → return 0, no noise.

    Performs no writes.
    """
    _applied_ids, pending_ids = status()
    if pending_ids:
        print(
            "migrations pending (run `sandesh migrate`): "
            + ", ".join(pending_ids),
            file=sys.stderr,
        )
        return 1

    drifts = _drift(_db_path())
    if drifts:
        print("WARNING: schema drift detected (non-fatal):")
        for d in drifts:
            print(f"  - {d}")
        return 0

    print("OK: fully migrated, live shape matches the committed snapshot")
    return 0


def _format_status(applied_ids, pending_ids):
    """Render the global DB's migration status as a human-readable line set.

    Names every applied id (e.g. ``0001-baseline``) and conveys the pending
    count/ids — including an explicit "0 pending" when none remain.
    """
    applied_part = ", ".join(applied_ids) if applied_ids else "(none)"
    if pending_ids:
        pending_part = f"{len(pending_ids)} pending: " + ", ".join(pending_ids)
    else:
        pending_part = "0 pending"
    return f"applied: {applied_part}\n{pending_part}"


def cmd_migrate(args):
    """CLI dispatch entry for `sandesh migrate` (global DB — DEC-B).

    The engine targets the single global ``sandesh.db``; no project routing
    exists on this subcommand. Dispatches on the parsed flags:
      * default (bare ``migrate``) → apply pending migrations to the global DB.
      * ``--all`` → an alias of the bare apply (kept for compatibility;
        identical behaviour — there is only one DB to migrate).
      * ``--status`` → print applied/pending (read-only).
      * ``--rollback`` / ``--check`` / ``--dump-schema`` / ``--diff`` →
        the corresponding engine call, all against the global DB.

    The dependency guard runs first — without the [migrate] extra there is
    nothing to do but tell the user how to install it (and exit non-zero).
    """
    _require_deps()

    do_status = getattr(args, "status", False)
    do_check = getattr(args, "check", False)
    do_dump = getattr(args, "dump_schema", False)
    do_rollback = getattr(args, "rollback", False)
    diff_old = getattr(args, "diff", None)
    do_json = getattr(args, "json", False)

    if do_rollback:
        rollback()
        return 0

    if do_dump:
        import json
        print(json.dumps(dump_schema()))
        return 0

    if diff_old is not None:
        import json
        report = diff(diff_old)
        if do_json:
            print(json.dumps(report))
        else:
            print(_format_diff(report))
        return 0

    if do_check:
        return check()

    if do_status:
        applied_ids, pending_ids = status()
        print(_format_status(applied_ids, pending_ids))
        return 0

    # Apply (bare `migrate`; `--all` is an alias of the same single-DB apply).
    try:
        apply()
    except Exception as exc:  # noqa: BLE001 — surface the failure, exit non-zero
        print(f"[sandesh] migrate failed: {exc}", file=sys.stderr)
        return 1
    return 0
