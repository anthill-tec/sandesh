#!/usr/bin/env bash
# install.sh — PEP-668-safe own-venv install of Sandesh from this source checkout.
#
#   ./install.sh
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
# Per-project data lives under  <data_home>/sandesh/projects/<project_id>/  and is
# created by `sandesh setup --project <id>`.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/sandesh"
BINDIR="$HOME/.local/bin"
VENV="$DEST/.venv"

# Which extras to install. Default to the [mcp] extra so the MCP server works out
# of the box; a caller can override (e.g. SANDESH_INSTALL_EXTRAS="" for base-only).
EXTRAS="${SANDESH_INSTALL_EXTRAS-[mcp]}"

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
