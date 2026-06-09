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
    """Absolute path to the packaged ``sandesh/migrations/`` directory.

    Resolved relative to this module's file so it works from an installed
    package (force-included into the wheel) as well as from a source checkout.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


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


def cmd_migrate(args):
    """CLI dispatch entry for `sandesh migrate`.

    The CLI calls this for every `migrate` invocation. The first thing it does is
    the dependency guard — without the [migrate] extra there is nothing to do but
    tell the user how to install it (and exit non-zero). The real subcommand flags
    (--all/--rollback/--check/--dump-schema/--diff) arrive in later cycles.
    """
    _require_deps()
    # Deps present: later cycles dispatch on args (--status/--all/...) here.
    return 0
