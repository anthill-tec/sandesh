"""_migrate_helpers.py — shared migration-chain durability helpers for tests.

CR-SAN-023 C1 un-pinning (per the CR-SAN-022 C1 precedent): fixtures that need
"the store as it was before migration NNNN" must NOT hard-code how many
one-step rollbacks reach that state — that count changes every time the chain
grows. ``rollback_until`` lets a fixture target the migration it MEANS, so the
test survives future migrations unchanged.
"""


def rollback_until(migration_id, rollback_fn, status_fn, max_steps=10):
    """Roll back one step at a time until ``migration_id`` appears in pending.

    ``rollback_fn`` performs ONE one-step rollback (API or CLI); ``status_fn``
    returns ``(applied_ids, pending_ids)``. Bounded: hard-fails with
    ``AssertionError`` after ``max_steps`` so a broken rollback can never loop
    forever.
    """
    for _ in range(max_steps):
        _applied, pending = status_fn()
        if migration_id in pending:
            return
        rollback_fn()
    _applied, pending = status_fn()
    if migration_id not in pending:
        raise AssertionError(
            f"rollback_until: {migration_id!r} still not pending after "
            f"{max_steps} one-step rollbacks; pending={pending!r}"
        )
