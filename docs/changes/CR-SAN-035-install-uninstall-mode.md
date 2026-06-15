# CR-SAN-035 — `install.sh --uninstall [--purge]` (installer self-removal)

**Status:** PENDING
**Priority:** Low (tooling symmetry — an installer should remove itself)
**Depends on:** —
**Labels:** tooling, installer, dx
**Wave:** post-0.2.2 tooling
**Design reference:** `install.sh` (the script itself — no PRD; tooling) + the uninstall contract
executed manually this session (remove symlinks + venv; `--purge` also removes the data store;
advise `claude mcp remove`).

## Context

`install.sh` is install-only — it takes no arguments and runs its install body unconditionally
(`set -euo pipefail`, vars `DEST`/`BINDIR`/`VENV` at the top). There is no `uninstall.sh` and no
`--uninstall` mode, so removing a from-source install means deleting the symlinks, the venv, and
(optionally) the data store by hand. This CR adds the symmetric self-removal path.

## Scope

### §S1 — argument dispatch (default unchanged)
- Parse the first arg with `${1:-}` (so `set -u` doesn't trip on no-args):
  - no args → **install** (the current body, unchanged).
  - `--uninstall` (optionally followed by `--purge`) → **uninstall** path (§S2); the install body
    MUST NOT run (no venv is built).
  - `-h` | `--help` → usage block (§S3) to stdout, exit 0; no install.
  - anything else → usage to **stderr**, exit **2**.
- The install body (current lines ~29–128) is gated so it runs only in install mode.

### §S2 — uninstall logic
- Always: `rm -f "$BINDIR/sandesh" "$BINDIR/sandesh-mcp"` (remove ONLY those two symlinks; never
  remove `$BINDIR` itself) and `rm -rf "$VENV"` (the venv = software). Never touch `$SRC` (checkout).
- `--purge`: additionally `rm -rf "$DEST"` (the whole data home — `sandesh.db` + `projects/` +
  any `.pre-global` backups). Without `--purge`, the data store is preserved.
- Idempotent: `rm -f`/`rm -rf` on absent paths exit 0; a re-run on an already-clean system exits 0
  and prints an "already removed / nothing to do" notice.
- Output: list what was removed (or note already-absent), and ALWAYS print the reminder
  `remove the MCP registration too:  claude mcp remove sandesh` (install.sh does not own that
  registration, so it advises rather than executes). A non-`--purge` run prints a note that the
  data store was kept and how to purge it.

### §S3 — usage / header
- A `usage()` block documenting: `./install.sh` (install), `./install.sh --uninstall [--purge]`,
  `./install.sh -h|--help`. Update the file header comment to mention the uninstall mode.

### §S4 — tests (`tests/test_install.py`, new `UninstallShTest`)
- Mirror `InstallShTest`'s isolated `HOME`+`XDG_DATA_HOME` env. Fabricate a footprint (no real pip
  install): `mkdir $XDG/sandesh/.venv`, a `$XDG/sandesh/sandesh.db` file, `$XDG/sandesh/projects/`,
  and `ln -s` the two launchers into `$HOME/.local/bin`. Drive `bash install.sh --uninstall …`.

### §S5 — docs
- README `install.sh` subsection gains one line: `./install.sh --uninstall [--purge]`.

## Acceptance criteria

- [ ] **AC1 — uninstall removes software, keeps data.** With the fabricated footprint,
      `install.sh --uninstall` exits 0; both `$BINDIR/sandesh` and `$BINDIR/sandesh-mcp` are gone
      and `$VENV` is gone; `sandesh.db` and `projects/` remain; stdout contains the
      `claude mcp remove sandesh` reminder and a "data kept" note.
- [ ] **AC2 — `--purge` removes data too.** `install.sh --uninstall --purge` exits 0; the symlinks
      are gone AND the entire `$DEST` directory (incl. `sandesh.db` + `projects/`) is gone.
- [ ] **AC3 — idempotent.** Running `install.sh --uninstall` against an already-clean env exits 0
      with no error and an "already removed / nothing to do" notice.
- [ ] **AC4 — help.** `install.sh -h` and `install.sh --help` exit 0 and print a usage block naming
      the install default, `--uninstall`, and `--purge`.
- [ ] **AC5 — default + bad flag.** `install.sh` with no args still performs the full install
      (existing `InstallShTest` stays green); an unknown flag (e.g. `--bogus`) exits 2 with usage on
      stderr and builds no venv.
- [ ] **AC6 — scoping safety.** Uninstall removes only the two `sandesh*` symlinks in `$BINDIR` (a
      sibling file placed in `$BINDIR` is untouched), never deletes `$BINDIR` itself, and never
      touches the source checkout `$SRC`.

## Estimated size
Small — arg dispatch + an uninstall branch + a usage block in one bash file, one test class, a
one-line README note.

## Risks / open questions
- `--purge` is destructive (the data store). It is opt-in (default keeps data), the docs/output
  state it plainly, and there is no `-y` auto-confirm in scope — the flag itself is the consent.

## Non-goals
- Touching the Claude Code MCP registration (advised via a printed reminder, never executed).
- An uninstall path for the `uv tool` / `pipx` / AUR installs (those have their own removal —
  `uv tool uninstall`, `pipx uninstall`, pacman); this is for the `install.sh` from-source install.
- A separate `uninstall.sh` (the mode lives in `install.sh` for symmetry/discoverability).
