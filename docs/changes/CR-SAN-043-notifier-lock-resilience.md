# CR-SAN-043 — Notifier lock-contention resilience (busy_timeout + write-retry + no-flap watcher)

**Status:** IN_PROGRESS
**Priority:** High (the out-of-band wake mechanism goes DOWN under heavy co-tenant CPU load —
exactly when long builds run and cross-session coordination matters most; reported recurring by
agents using Sandesh)
**Depends on:** — (hardens existing `sandesh_db.connect()` + `notifier_*` writes + `notify.run()`)
**Labels:** reliability, concurrency, watcher
**Wave:** post-0.3.2 → patch release **0.3.3**
**Design reference:** the lock-contention bug brief (verbatim traceback at `notify.py:53` →
`sandesh_db.notifier_acquire` INSERT `:827` → `sqlite3.OperationalError: database is locked`);
CLAUDE.md *Locked semantics* #7 (crash-safe liveness) and the wake-mechanism section; the
existing `connect()` PRAGMA-assertion pattern (`tests/test_global_store.py::test_connect_sets_wal_journal_mode`)
and notifier unit tests (`tests/test_sandesh.py`).

## Context
`sandesh_db.connect()` (`sandesh_db.py:202`) opens the global DB and sets `PRAGMA journal_mode=WAL`
(`:214`) but sets **no `PRAGMA busy_timeout`** anywhere — it relies solely on Python's default
`sqlite3.connect(timeout=5.0)` busy handler. WAL still serializes *writers* (one at a time). With a
Mainline + several Tracks each running a `sandesh notify` watcher, every poll writes the contended
`notifier` table (acquire + per-poll heartbeat + release). When a CPU-saturating co-tenant
(e.g. a full-workspace Rust build/test gate) starves SQLite, a writer can't commit inside the 5 s
window → the next writer gets `SQLITE_BUSY` → `database is locked`.

The `notifier_acquire` INSERT (`:827`), `notifier_heartbeat` (`:839`), `notifier_release` (`:856`),
`notifier_tombstone` (`:862`), `notifier_reap_if_stale` (`:869`) writes are **unguarded** — there is
no retry/backoff and no `OperationalError` handling on the write path. (The lone
`except sqlite3.OperationalError` at `:772` is in `search()` — FTS query validation, unrelated.) So
a transient, recoverable lock surfaces as an uncaught exception. `notify.run()` (`notify.py:42`)
only catches `ValueError` from `validate_address`; the lock error from the startup acquire (`:53`)
*or* from any in-loop write (`notifier_heartbeat`/`notifier_check`) propagates → the watcher exits 1
and the address flaps `listening:false`. Relaunches keep failing while the lock window stays hot, so
the wake mechanism is down precisely under sustained load.

**Fix (scope locked with owner — fixes 1+2+3 of the brief; 4 BEGIN IMMEDIATE / 5 writer-window are
deferred):**
1. Set a generous `PRAGMA busy_timeout` on **every** connection so SQLite blocks-and-retries
   internally instead of erroring under transient contention (cheapest, highest-impact).
2. Wrap the `notifier_*` writes in a bounded retry-with-jittered-backoff on the *locked* error so a
   transient lock that outlives `busy_timeout` still never crashes the caller (defense in depth).
   These writes are idempotent (upsert / heartbeat-stamp / delete-by-token), so retrying the whole
   execute+commit is safe.
3. Make `notify.run()` resilient at its boundary: a *locked* error from the startup acquire or any
   in-loop DB op is logged and retried on the poll cadence (bounded by the existing watch deadline →
   exit 2 on timeout), and **never** escapes as exit 1 — so `listening` does not flap.

PyPI/Python version is tag-derived (hatch-vcs). This ships as patch **0.3.3**. No schema change —
`busy_timeout` is a per-connection pragma, not table state — so **no migration**.

## Scope
- **§S1 — `busy_timeout` on every connection.** Add module constant `BUSY_TIMEOUT_MS = 30000` to
  `sandesh_db.py`. In `connect()`, after the WAL pragma and **before** the lazy auto-migrate block,
  execute `PRAGMA busy_timeout=<BUSY_TIMEOUT_MS>` so migrations and all subsequent ops inherit it.
- **§S2 — lock predicate + retry helper.** Add to `sandesh_db.py`:
  - `is_locked_error(exc) -> bool` — True iff `exc` is a `sqlite3.OperationalError` whose message
    contains `"database is locked"` (match the substring `locked`, case-insensitive); False for any
    other `OperationalError` (e.g. `"no such table"`) and any non-`OperationalError`.
  - module constant `LOCK_RETRY_ATTEMPTS = 6` and `_retry_locked(fn, *, attempts=LOCK_RETRY_ATTEMPTS,
    sleep=time.sleep)` — calls `fn()` and returns its value; on a *locked* error retries up to
    `attempts` total tries with jittered exponential backoff (`sleep` is injectable for tests);
    re-raises a non-locked error **immediately** (no retry) and re-raises the last *locked* error
    after `attempts` are exhausted.
- **§S3 — notifier writes retry.** Wrap the execute+commit of every notifier write function
  (`notifier_acquire`, `notifier_heartbeat`, `notifier_release`, `notifier_tombstone`,
  `notifier_reap_if_stale`) in `_retry_locked(...)`. Behaviour and return values are otherwise
  unchanged (acquire still returns `(ok, reason)`; the dedup check is unaffected).
- **§S4 — `notify.run()` no-flap boundary.** Wrap the startup `notifier_acquire` and the per-iteration
  DB ops (`notifier_check` / `notifier_heartbeat` / `unread_to`) so a `is_locked_error` exception is
  caught, logged (`[notify] DB busy … staying up, recheck in <interval>s`), and retried on the next
  poll — bounded by the existing `deadline` (a lock that spans the whole window → exit **2**, timed
  out). A non-locked error still propagates unchanged. Existing exit codes (0/2/3/4/5) keep their
  meaning; a transient lock no longer maps to exit 1.
- **§S5 — docs.** Add a CLAUDE.md *Gotchas* line documenting `busy_timeout` + the `_retry_locked`
  write wrapper + the no-flap watcher boundary (why: SQLite WAL serializes writers; the watcher must
  survive transient co-tenant contention). No README change (develop branch).
- **§S6 — real-contention integration test** (added by owner decision). Beyond the injected-lock unit
  tests (which verify the retry/guard *logic*), add ONE threaded integration test that exercises the
  fix end-to-end against *real* SQLite locking: N threads, each with its own `connect()` (real WAL
  writer serialization + real `busy_timeout` + real `_retry_locked`), concurrently running
  `notifier_acquire`/`heartbeat`/`release` on the shared global DB. This closes the blind spot that
  the injected tests cannot cover — that `busy_timeout`+retry actually *absorb* contention rather than
  merely that the retry path is wired.

## Acceptance criteria
- **AC1 — busy_timeout on every connection.** After `sandesh_db.connect()`,
  `con.execute("PRAGMA busy_timeout").fetchone()[0] == 30000`, and `sandesh_db.BUSY_TIMEOUT_MS == 30000`.
  (Model: `test_connect_sets_wal_journal_mode`.)
- **AC2 — `is_locked_error` predicate.** `is_locked_error(sqlite3.OperationalError("database is locked"))`
  is `True`; `is_locked_error(sqlite3.OperationalError("no such table: x"))` is `False`;
  `is_locked_error(ValueError("database is locked"))` is `False`.
- **AC3 — `_retry_locked` retries then succeeds.** A callable that raises
  `sqlite3.OperationalError("database is locked")` on its first 2 invocations then returns a sentinel:
  `_retry_locked(fn, sleep=<noop>)` returns the sentinel and `fn` was invoked exactly 3 times.
- **AC4 — `_retry_locked` exhausts + fast-fails non-lock errors.** A callable that always raises the
  locked error → `_retry_locked(fn, attempts=N, sleep=<noop>)` raises `sqlite3.OperationalError` after
  exactly `N` invocations. A callable raising `OperationalError("no such table")` → propagates on the
  **first** call (invoked once, not retried). A callable raising a non-`OperationalError` → propagates
  immediately.
- **AC5 — notifier writes survive a transient lock.** With a connection proxy that injects one
  `database is locked` on the first write statement then delegates to a real connection (and `sleep`
  stubbed to a no-op): `notifier_acquire` returns `(True, "acquired")` and the row exists with the
  given token; `notifier_heartbeat` advances `heartbeat_at`; `notifier_release` removes the row — each
  despite the injected transient lock.
- **AC6 — `notify.run()` stays up through a transient lock (no exit 1).** With `notify.sdb.notifier_acquire`
  stubbed to raise the locked error once then succeed, `notify.sdb.unread_to` stubbed to return `[1]`,
  and sleep stubbed to a no-op: `notify.run(project, address, timeout=...)` returns **0** and raises
  nothing (the acquire was retried, not crashed). A **non-locked** `OperationalError` raised from a
  poll op still propagates (genuine errors are not swallowed).
- **AC7 — a lock that outlives the watch window → exit 2, never 1, never a traceback.** With the
  startup acquire stubbed to always raise the locked error, a deadline that is already elapsed
  (small `timeout` / stubbed `monotonic`), and sleep stubbed to a no-op: `notify.run(...)` returns
  **2** (timed out) and raises nothing.
- **AC8 — no regression + scope.** Full `tests/test_sandesh.py` and `tests/test_global_store.py` stay
  green; the only production files changed are `sandesh/sandesh_db.py` and `sandesh/notify.py` (plus
  the new/extended tests and the CLAUDE.md gotcha line). No schema/migration change; `schema/current-schema.json`
  is untouched and the CI schema-snapshot gate still passes.
- **AC9 — real contention is absorbed (integration).** A threaded test running ≥8 concurrent writers
  (each its own `connect()`) × ≥50 `acquire`/`heartbeat`/`release` cycles each against the shared
  global DB completes with **zero** `sqlite3.OperationalError` escaping any thread. Non-vacuity is
  demonstrated in the RED step: with the fix neutralized (`BUSY_TIMEOUT_MS=0` + `LOCK_RETRY_ATTEMPTS=1`)
  the same test surfaces `database is locked`; with the fix in place it is green.

## Estimated size
Small — one constant + one PRAGMA line in `connect()`, one predicate + one retry helper, a one-line
wrap on five notifier writes, a boundary guard in `notify.run()`, and new unit tests on the existing
`test_global_store.py` / `test_sandesh.py` harnesses (+ a small connection-proxy fixture and a couple
of `notify.run()` monkeypatch tests).

## Risks / open questions
- **Testing transient locks deterministically.** Resolved: do NOT spawn real contention — inject the
  `database is locked` `OperationalError` via a connection proxy (writes) / monkeypatched `notify.sdb.*`
  (run loop), and stub `sleep` so backoff adds no wall-clock time. The fix is exercised, the test is
  fast and deterministic.
- **Whole-op retry double-apply.** Resolved: the wrapped writes are idempotent (upsert, heartbeat
  stamp, delete-by-token), so retrying execute+commit cannot corrupt state even if the lock surfaced
  after a partial commit.
- **busy_timeout vs deferred→immediate lock promotion.** The notifier writes are single-statement
  writes (the `notifier_live` SELECT runs in autocommit before the write begins the implicit txn), so
  the SQLITE_BUSY-on-promotion edge case `busy_timeout` cannot retry does not apply here. The brief's
  fix 4 (explicit `BEGIN IMMEDIATE`) is deferred — not needed for these single-write ops.

## Non-goals
- Fix 4 (autocommit + explicit `BEGIN IMMEDIATE` refactor across `sandesh_db.py`) and fix 5
  (writer-window audit — heartbeat is already once per poll). Deferred; revisit only if contention
  recurs after 1+2+3.
- An env-overridable busy_timeout (`$SANDESH_BUSY_TIMEOUT_MS`). A fixed 30 s is sufficient; add tuning
  later if needed.
- **Multi-*process* contention tests.** A threaded contention test (§S6/AC9) IS in scope; spawning real
  OS processes is not — WAL writer contention is per-connection, so threads with separate connections
  reproduce it faithfully at far lower CI cost/flakiness. Any schema/MCP/CLI surface change is also out.
