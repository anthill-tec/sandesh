# CR-SAN-049 — Dev-tooling hygiene: pin twine/packaging floors + real-store guard for tests

**Status:** PENDING
**Priority:** Medium (chore — dev venv parity with CI + a safety rail)
**Depends on:** —
**Labels:** chore, dev-tooling, tests, safety
**Wave:** 16 · **Release:** 0.4.0
**Design reference:** owner rulings 2026-09-26 — (a) "file a chore to pin" after the baseline
`test_twine_check_passes` failure; (b) "tests must use a mock store, never the real one — it will mess up
other running projects" after a VERIFY probe created a `Demo` project in the shared global store.

## Context
1. **CI ≠ dev venv.** `publish-pypi.yml` installs `twine` unpinned and got `twine 7.0.0 / packaging 26.3`,
   which accept the `Metadata-Version: 2.5` that `hatchling ≥ 1.30` now writes. The dev venv carries
   `twine 6.2.0 / packaging 26.2`, so `tests/test_publish_workflow.py::test_twine_check_passes` fails locally
   with `'2.5' is not a valid metadata version` while CI is green — a permanent red in the baseline that
   every gap-analysis and pre-merge gate has to explain away.
2. **The real store is shared.** `~/.local/share/sandesh/sandesh.db` is the ONE global DB used by every
   project on the machine (Model B, Crucible, …). Any test or probe that forgets `XDG_DATA_HOME` writes into
   it; a shell-prefix `XDG_DATA_HOME=… cmd` can be silently dropped by tool wrappers (that is how the
   incident happened). Today nothing stops it.

## Scope
- **§S1 — dev extra with floors.** Add `[project.optional-dependencies] dev = ["twine>=7.0",
  "packaging>=26.3", "build>=1.2", "unittest-xml-reporting>=3.2", "coverage>=7.6"]` (no runtime effect;
  `[mcp]`/`[migrate]` unchanged). `publish-pypi.yml` installs `twine>=7.0` explicitly (no behaviour change,
  just the same floor). CLAUDE.md "How to run": dev venv = `pip install -e '.[mcp,migrate,dev]'`.
- **§S2 — real-store guard + lifecycle.** New `tests/_store_guard.py` imported first by every test module
  (one line: `import tests._store_guard  # noqa`) (`tests/` is a namespace package — no `__init__.py`). At import it creates ONE
  per-process `tempfile.TemporaryDirectory(prefix="sandesh-tests-")` under the SYSTEM temp root
  (`tempfile.gettempdir()` — `/tmp`, tmpfs on the dev box, so nothing survives a reboot; never `$HOME`, never
  the repo), registers `atexit` cleanup, and **re-points `XDG_DATA_HOME` to it** — unconditionally, unless the
  incoming value already resolves under the system temp root (a harness-supplied temp store is respected).
  The dev shell exports `XDG_DATA_HOME=~/.local/share` globally, so "set to the real store" is the NORMAL
  case and must be overridden, not refused. The only bypass is `SANDESH_TESTS_ALLOW_REAL_STORE=1` (never set;
  exists so the intent is explicit). Test classes that need their own store use the shared
  `tests/_store_guard.TempStore` mixin: `setUp` → `self._tmp = tempfile.TemporaryDirectory(prefix=
  "sandesh-<test>-")` + `os.environ["XDG_DATA_HOME"] = self._tmp.name`; `tearDown` → close connections,
  restore the previous env value, `self._tmp.cleanup()` — so no test rolls its own. `sandesh_db.db_path()`
  gains no change.
- **§S3 — subprocess discipline.** Every test that spawns Sandesh passes an explicit `env` with an
  isolated temporary `XDG_DATA_HOME`; guard-specific subprocess tests may omit the variable only when
  exercising the guard's unset-input behavior.
- **§S3b — `axi.py` import form.** `from sandesh import _toon` → `import sandesh._toon as _toon` (same
  binding; clears a Pyright "unknown import symbol" false positive on underscore submodules). No behaviour
  change; `tests/test_axi_envelope.py` + `test_toon_vendor.py` stay green.
- **§S4 — docs.** CLAUDE.md gotcha: "the global store is shared by every project — tests/probes/agents MUST
  use a temp `XDG_DATA_HOME` set in-process or in the subprocess env, never as a shell prefix; the guard
  refuses otherwise."

## Acceptance criteria
- **AC1** — With `pip install -e '.[dev]'` the dev venv reports `twine ≥ 7.0` and `packaging ≥ 26.3`;
  `tests/test_publish_workflow.py` is fully green locally (`test_twine_check_passes` PASSED).
- **AC2** — `tests/test_store_guard.py` (each case in a subprocess with a controlled env): with
  `XDG_DATA_HOME` unset → after import it is a path under `tempfile.gettempdir()`; with it set to
  `~/.local/share` → after import it is a DIFFERENT path under the temp root (re-pointed) and
  `sandesh_db.db_path()` resolves under that temp path; with it set to an existing dir under the temp root →
  unchanged (respected); with `SANDESH_TESTS_ALLOW_REAL_STORE=1` → unchanged (bypass). The guard's temp dir
  is removed at interpreter exit (assert after the subprocess ends).
- **AC3** — Every `tests/test_*.py` imports `tests._store_guard` before any `sandesh` import. Subprocesses
  that run Sandesh use an explicit temporary `XDG_DATA_HOME`; guard-specific tests cover unset input.
- **AC4** — Representative test files run standalone with `XDG_DATA_HOME` pointed at an isolated
  temporary directory and exit 0; no test reads, lists, or probes the shared real store.
- **AC4b** — Lifecycle: after the full suite, no `sandesh-tests-*`/`sandesh-*` directory remains under the
  system temp root (the guard's `atexit` and every `TempStore.cleanup()` ran); a test that deliberately
  fails mid-way still cleans its store (tearDown runs); the guard's temp root is under `tempfile.gettempdir()`
  and NOT under `$HOME`.
- **AC5** — CI `build` job unchanged in outcome (`twine check` PASSED) with the explicit `twine>=7.0` floor.

## Estimated size
Small-medium — one pyproject block, a ~60-line guard module (guard + `TempStore` mixin + atexit), a mechanical
import/setUp-tearDown sweep over ~63 test files, three guard tests, the one-line `axi.py` import, docs.

## Non-goals
- No change to `sandesh_db.db_path()` semantics or to the installed tool.
- No coverage config changes (the python-crucible client's coverage source list is Crucible's concern).
