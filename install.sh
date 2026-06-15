#!/usr/bin/env bash
# install.sh — PEP-668-safe own-venv install (and uninstall) of Sandesh from this
# source checkout.
#
#   ./install.sh                       install (default)
#   ./install.sh --uninstall [--purge] remove the install (--purge also wipes data)
#   ./install.sh -h | --help           show usage and exit
#
# This is the offline / from-source FALLBACK installer (README leads with `uv`,
# then `pipx`). It needs only `python3` (with the `venv` module) and `pip`, NOT
# pipx — so it works on externally-managed (PEP 668) distros.
#
# It builds its OWN virtualenv at  $XDG_DATA_HOME/sandesh/.venv  (default
# ~/.local/share/sandesh/.venv), pip-installs the `sandesh-relay` package from
# this checkout into it, and symlinks the `sandesh` + `sandesh-mcp` console
# scripts onto $HOME/.local/bin so they are on PATH.
#
# `--uninstall` removes the venv and the two `sandesh*` launchers but KEEPS the
# data store; add `--purge` to also delete the whole data home ($DEST).
#
# Per-project data lives under  <data_home>/sandesh/projects/<project_id>/  and is
# created by `sandesh setup --project <id>`.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/sandesh"
BINDIR="$HOME/.local/bin"
VENV="$DEST/.venv"

# Which extras to install. Default to [mcp,migrate] so the MCP server works out of
# the box AND the venv gets yoyo+jsonschema for `migrate --all` (DEC-2); a caller
# can override (e.g. SANDESH_INSTALL_EXTRAS="" for base-only).
EXTRAS="${SANDESH_INSTALL_EXTRAS-[mcp,migrate]}"

usage() {
  cat <<EOF
Sandesh installer — own-venv install/uninstall from this source checkout.

Usage:
  ./install.sh                        install Sandesh (default mode)
  ./install.sh --uninstall            remove the venv + launchers, KEEP the data store
  ./install.sh --uninstall --purge    also delete the data home ($DEST)
  ./install.sh -h | --help            show this help and exit

Install builds a venv at  $VENV  and symlinks the
sandesh / sandesh-mcp launchers into  $BINDIR.
EOF
}

# --- uninstall ---------------------------------------------------------------
do_uninstall() {
  local purge="$1"
  echo "uninstalling Sandesh"

  # Detect whether anything was actually present, so a clean env prints a notice.
  local present=0
  [ -e "$BINDIR/sandesh" ] || [ -L "$BINDIR/sandesh" ] && present=1
  [ -e "$BINDIR/sandesh-mcp" ] || [ -L "$BINDIR/sandesh-mcp" ] && present=1
  [ -e "$VENV" ] && present=1
  if [ "$purge" = "1" ]; then
    [ -e "$DEST" ] && present=1
  fi

  # Remove ONLY the two launchers (never $BINDIR itself) and the venv.
  rm -f "$BINDIR/sandesh" "$BINDIR/sandesh-mcp"
  rm -rf "$VENV"

  if [ "$purge" = "1" ]; then
    rm -rf "$DEST"
    echo "✓ removed launchers, venv, and the data home ($DEST) — --purge"
  else
    echo "✓ removed launchers and venv"
    echo "  data kept: the data store under $DEST was preserved."
    echo "             re-run with --purge to delete it too."
  fi

  if [ "$present" != "1" ]; then
    echo "  nothing to do — Sandesh was already removed (nothing found to uninstall)."
  fi

  echo "  reminder: also deregister the MCP server with:  claude mcp remove sandesh"
}

# --- argument dispatch -------------------------------------------------------
case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --uninstall)
    if [ "${2:-}" = "--purge" ]; then
      do_uninstall 1
    else
      do_uninstall 0
    fi
    exit 0
    ;;
  "")
    : # fall through to the install body below
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

echo "installing Sandesh:  $SRC  →  $DEST"
mkdir -p "$DEST/projects" "$BINDIR"

# --- own venv (PEP-668-safe) -------------------------------------------------
echo "creating venv:       $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip

# --- pip-install the package into the venv -----------------------------------
# Best-effort: install WITH the [mcp] extra so the server works immediately. If
# that fails (e.g. offline / no mcp in cache), fall back to a base install so the
# CLI still works, and note how to add the extra later.
if [ -n "$EXTRAS" ] && "$VENV/bin/python" -m pip install --quiet "$SRC$EXTRAS"; then
  echo "✓ installed package with extra: $EXTRAS"
else
  if [ -n "$EXTRAS" ]; then
    echo "  NOTE: install of '$EXTRAS' failed (offline?) — falling back to base install."
  fi
  "$VENV/bin/python" -m pip install --quiet "$SRC"
  echo "✓ installed package (base, stdlib-only)"
  echo "  NOTE: the MCP server needs the extra. Add it later with:"
  echo "        pipx install 'sandesh-relay[mcp]'   (or re-run with network access)"
fi

# --- symlink the console scripts onto PATH -----------------------------------
# pip places `sandesh` and `sandesh-mcp` in the venv bin dir with a shebang to the
# venv python, so they work correctly through these symlinks.
ln -sf "$VENV/bin/sandesh" "$BINDIR/sandesh"
if [ -x "$VENV/bin/sandesh-mcp" ]; then
  ln -sf "$VENV/bin/sandesh-mcp" "$BINDIR/sandesh-mcp"
fi

# --- run migrations on existing stores (DEC-2/DEC-3) --------------------------
# Probe the venv directly for the [migrate] deps (yoyo+jsonschema). Distinguish
# the missing-extra case from a real migration error by the ACTUAL venv contents,
# not the exit code: with deps present we run `migrate --all` under `set -e` so a
# genuine migration error ABORTS the install (DEC-3); with deps absent we skip and
# print a notice, letting the install complete (AC2). `migrate --all` honours the
# same $XDG_DATA_HOME the installer used, so a bare invocation targets these stores.
if "$VENV/bin/python" -c "import yoyo, jsonschema" 2>/dev/null; then
  echo "running migrations on existing stores …"
  "$VENV/bin/sandesh" migrate --all      # under set -e: a real migration error ABORTS the install (DEC-3)
  echo "✓ migrations applied (migrate --all)"
else
  echo "  NOTE: migrations skipped — the [migrate] extra is not installed."
  echo "        install it and migrate later:  pip install 'sandesh-relay[migrate]' && sandesh migrate --all"
fi

# --- consolidate legacy per-project stores (CR-SAN-022 §S3) -------------------
# Stdlib-only (no [migrate] extra needed), hence OUTSIDE the yoyo probe above.
# Idempotent: imported stores become sandesh.db.pre-global and are skipped on
# re-run. Under `set -e` a genuine consolidation error ABORTS the install.
echo "consolidating legacy per-project stores into the global DB …"
"$VENV/bin/sandesh" consolidate
echo "✓ consolidation done"

# CR-SAN-027 §S2: rebuild the full-text search index once on install/update —
# covers pre-FTS history and freshly consolidated stores. Stdlib-only (FTS5 is
# compiled into the interpreter), so it needs no extra and runs unconditionally.
# Idempotent. Under `set -e` a genuine error ABORTS the install.
echo "rebuilding the full-text search index …"
"$VENV/bin/sandesh" reindex
echo "✓ search index rebuilt"

# CR-SAN-023 §S2b: assign the Sandesh admin from $SANDESH_ADMIN via an inline
# venv-python call — there is deliberately NO CLI verb for this (PRD O3: no
# agent-reachable surface may create/change the admin). The name is read from
# the environment INSIDE python (quote/injection-safe — never interpolated into
# the code), and the different-name re-assign refusal (ValueError) is caught
# there and surfaced as a notice, NOT an install abort (the python exits 0, so
# `set -e` lets the install COMPLETE). Unset → skip with a notice.
if [ -n "${SANDESH_ADMIN:-}" ]; then
  SANDESH_ADMIN="$SANDESH_ADMIN" "$VENV/bin/python" - <<'PY'
import os
from sandesh import sandesh_db as s
con = s.connect()
try:
    s.assign_admin(con, os.environ["SANDESH_ADMIN"])
    print("✓ Sandesh admin assigned: %r" % s.admin_name(con))
except ValueError as exc:
    print("  NOTE: %s — keeping %r (install continues)" % (exc, s.admin_name(con)))
finally:
    con.close()
PY
else
  echo "  NOTE: \$SANDESH_ADMIN not set — admin assignment skipped."
  echo "        assign later by re-running:  SANDESH_ADMIN=<name> ./install.sh"
fi

echo "✓ venv      → $VENV"
echo "✓ launcher  → $BINDIR/sandesh"
if [ -x "$VENV/bin/sandesh-mcp" ]; then
  echo "✓ MCP entry → $BINDIR/sandesh-mcp"
fi
case ":$PATH:" in
  *":$BINDIR:"*) : ;;
  *) echo "  NOTE: $BINDIR is not on \$PATH — add it to use 'sandesh' directly." ;;
esac
echo
echo "next:  sandesh setup --project <id>   then   sandesh --project <id> register --address 'Mainline - <id>' --kind mainline"
