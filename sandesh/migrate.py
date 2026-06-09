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
