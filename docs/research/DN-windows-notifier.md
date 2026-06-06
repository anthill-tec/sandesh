# DN — Windows portability of the `notify` watcher & liveness

**Status:** OPEN (design note / spike — no code change yet)
**Related:** `PRD-distribution.md` §4 (cross-platform scope), `CLAUDE.md` "The wake mechanism"

A design note, not a CR: pip/pipx makes Sandesh **installable** on Windows, but the **runtime**
`notify` watcher + notifier-liveness use POSIX-isms. This records exactly what breaks and the
options, so a future "Windows runtime" CR (if we pursue it) starts from analysis, not guesswork.

## The POSIX-isms (exact)

1. **PID-liveness via `os.kill(pid, 0)`** — `sandesh_db._pid_alive()` (`app/sandesh_db.py:362`)
   probes whether a notifier's process is still alive with signal `0`. On **Windows**,
   `os.kill` does **not** support signal `0` as a liveness probe (it only accepts
   `CTRL_C_EVENT`/`CTRL_BREAK_EVENT`, else terminates) — so `_pid_alive` is wrong on Windows.
   This feeds `notifier_live()` → `notifier_acquire()` (stale-row reaping) and the
   crash-safe self-heal. **This is the main blocker.**
2. **Signal handlers** — `notify.py:71-72` installs `SIGTERM`/`SIGINT` handlers for clean exit.
   Windows Python supports `SIGINT`; `SIGTERM` handling is limited/not delivered like POSIX.
   Lower severity: cooperative shutdown is driven by the **DB tombstone poll**, not signals —
   signals only make kill/Ctrl-C exit tidy.

Everything else is portable: the poll loop (`sqlite3` + `time.sleep`), `os.getpid()`,
`socket.gethostname()`, `uuid`. And the **wake itself is host-driven** (`run_in_background`),
not OS-signal-driven — so the architecture is sound cross-platform; only these two helpers leak
POSIX assumptions.

## Why it matters

The notifier's value is its **crash-safe liveness**: a SIGKILL'd watcher leaves a stale row that
the next `notifier_acquire` reaps, keyed on (a) PID gone OR (b) `heartbeat_at` older than
`HEARTBEAT_STALE_SECS` (60). On Windows, branch (a) misfires; only the heartbeat-staleness
branch (b) would work — so liveness still self-heals, just **up to ~60 s slower** and with a
wrong instantaneous "is it alive?" answer in `addressbook`'s `listening` column.

## Options

| # | Approach | Dep | Notes |
|---|---|---|---|
| **A** | Windows PID check via **`ctypes`** (`OpenProcess` + `GetExitCodeProcess`/`WAIT_TIMEOUT`) behind a `sys.platform` branch | none (stdlib `ctypes`) | keeps stdlib-only; most faithful; modest Windows-specific code |
| **B** | Optional **`psutil`** dep for `pid_exists()` | +psutil | simplest code; breaks stdlib-only purity; extra dep |
| **C** | **Degrade to heartbeat-staleness only** on Windows (skip the PID branch) | none | smallest change; liveness lags ~60 s; instantaneous `listening` less accurate |
| **D** | **Declare Windows runtime unsupported** for now (install works; document the caveat) | none | zero work; honest; defer A/C until there's demand |

Signal handlers (secondary): guard registration with `sys.platform` — register `SIGINT`
always, `SIGTERM` only where supported; tombstone polling already covers cooperative shutdown.

## Recommendation (for when Windows runtime is on the table)

**A or C**, both stdlib-only and aligned with the project's no-third-party-runtime rule. A is
the faithful fix; C is the cheap "good enough" (heartbeat already provides the fallback). Avoid
B (psutil) unless other needs justify the dep. Until there's real Windows demand, **D** is a
defensible interim — pip-install works, runtime is documented as Linux/macOS.

## Open questions

- Is Windows a real target audience, or is "installable everywhere, runs on Linux/macOS" enough?
- If yes: A (ctypes, faithful) vs C (heartbeat-only, cheap)?
- Does the `notify` background process behave under the host's Windows `run_in_background`
  equivalent (process spawn + exit-code surfacing)? Needs a spike on the actual host.

No code change until this is decided; this DN is the input to a future CR if Windows runtime is
pursued.
