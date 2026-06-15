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

# The agent surface the install targets — set by the --surface flag (§S1). Empty
# means "no --surface given" (fall through to the env/prompt/default precedence).
SURFACE=""

usage() {
  cat <<EOF
Sandesh installer — own-venv install/uninstall from this source checkout.

Usage:
  ./install.sh                          install Sandesh (default mode)
  ./install.sh --surface claude|pi|both|none
                                        choose the agent surface (claude/both ⇒ MCP,
                                        pi/none ⇒ no MCP); migrate on all
  ./install.sh --uninstall              remove the venv + launchers, KEEP the data store
  ./install.sh --uninstall --purge      also delete the data home ($DEST)
  ./install.sh -h | --help              show this help and exit

Install builds a venv at  $VENV  and symlinks the
sandesh / sandesh-mcp launchers into  $BINDIR.
EOF
}

# resolve_extras — decide the pip extras string per the §S1 precedence:
#   1. --surface flag (highest): claude|both ⇒ [mcp,migrate]; pi|none ⇒ [migrate]
#   2. else $SANDESH_INSTALL_EXTRAS (used verbatim, if SET — even if empty)
#   3. else if stdin is a tty → interactive prompt
#   4. else default [mcp,migrate]
# Echoes the resolved extras string on stdout.
resolve_extras() {
  if [ -n "$SURFACE" ]; then
    case "$SURFACE" in
      claude|both) echo "[mcp,migrate]" ;;
      pi|none)     echo "[migrate]" ;;
    esac
    return 0
  fi
  if [ "${SANDESH_INSTALL_EXTRAS+set}" = "set" ]; then
    echo "$SANDESH_INSTALL_EXTRAS"
    return 0
  fi
  if [ -t 0 ]; then
    local choice=""
    echo "Which agent surface? [claude] MCP / [pi] no-MCP / [both] / [none]" >&2
    read -r -p "surface (claude/pi/both/none) [claude]: " choice
    case "$choice" in
      pi|none) echo "[migrate]" ;;
      *)       echo "[mcp,migrate]" ;;
    esac
    return 0
  fi
  echo "[mcp,migrate]"
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
  --surface)
    SURFACE="${2:-}"
    case "$SURFACE" in
      claude|pi|both|none) : ;;
      *)
        echo "install.sh: invalid --surface value: '${SURFACE}' (expected claude|pi|both|none)" >&2
        usage >&2
        exit 2
        ;;
    esac
    ;;
  "")
    : # fall through to the install body below
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

# Resolve the extras now that --surface (if any) has been parsed.
EXTRAS="$(resolve_extras)"

echo "installing Sandesh:  $SRC  →  $DEST"
mkdir -p "$DEST/projects" "$BINDIR"

# --- own venv (PEP-668-safe) -------------------------------------------------
echo "creating venv:       $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip

# --- mandatory migrate on an existing store (§S3) ----------------------------
# If a global DB already exists this is an UPDATE: the store may be schema-behind
# and migration must never be silently skipped. Two parts enforce this:
#   1. Here: when the extras were NOT explicitly pinned via $SANDESH_INSTALL_EXTRAS
#      (i.e. resolved from --surface or the default), force `migrate` into the set
#      so the update venv always gets yoyo+jsonschema. We never override an
#      explicit operator pin — an explicit `SANDESH_INSTALL_EXTRAS` without migrate
#      is honoured verbatim, and `sandesh init` (below) then FAILS LOUDLY on the
#      behind store instead of silently skipping (AC4).
#   2. Below: if the (migrate-bearing) install can't be installed, we refuse to
#      fall back to a base install.
DB_EXISTS=0
if [ -f "$DEST/sandesh.db" ]; then
  DB_EXISTS=1
  if [ "${SANDESH_INSTALL_EXTRAS+set}" != "set" ]; then
    case "$EXTRAS" in
      *migrate*) : ;;
      "[mcp]")   EXTRAS="[mcp,migrate]" ;;
      "")        EXTRAS="[migrate]" ;;
      \[*\])     EXTRAS="${EXTRAS%]},migrate]" ;;
      *)         EXTRAS="[migrate]" ;;
    esac
  fi
fi

# --- pip-install the package into the venv -----------------------------------
# Best-effort: install WITH the requested extras so the chosen surface works
# immediately. If that fails (e.g. offline / no mcp in cache), fall back to a base
# install so the CLI still works — EXCEPT when an existing store made us require a
# migrate-bearing extra (§S3 force-add above): there a failure to install must FAIL
# the install, never silently fall back to a migration-incapable base.
if [ -n "$EXTRAS" ] && "$VENV/bin/python" -m pip install --quiet "$SRC$EXTRAS"; then
  echo "✓ installed package with extra: $EXTRAS"
elif [ -n "$EXTRAS" ] && [ "$DB_EXISTS" = "1" ] && case "$EXTRAS" in *migrate*) true ;; *) false ;; esac; then
  echo "install.sh: an existing sandesh.db requires the [migrate] extra, but" >&2
  echo "            installing '$EXTRAS' failed (offline / not in cache)." >&2
  echo "            refusing to continue without migration support (§S3)." >&2
  exit 1
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

# --- provision the global store via `sandesh init` (§S2 delegation) ----------
# A single delegated entry point replaces the old inline migrate → consolidate →
# reindex → admin block: `sandesh init` performs all four steps idempotently and
# is the one provisioning path shared by the installer and operators. `--yes`
# keeps it non-interactive (no admin prompt); `--admin` forwards $SANDESH_ADMIN
# when set. Under `set -e` a non-zero init (e.g. a schema-behind store with no
# [migrate] extra) ABORTS the install — surfaced as a clean '[sandesh]' message.
if [ -n "${SANDESH_ADMIN:-}" ]; then
  "$VENV/bin/sandesh" init --yes --admin "$SANDESH_ADMIN"
else
  "$VENV/bin/sandesh" init --yes
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
