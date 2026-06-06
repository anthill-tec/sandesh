#!/usr/bin/env bash
# install.sh — install Sandesh into the XDG data dir + put a launcher on PATH.
#
#   ./install.sh
#
# Copies app/ + bin/ to  $XDG_DATA_HOME/sandesh/  (default ~/.local/share/sandesh/)
# and symlinks the launcher to ~/.local/bin/sandesh. Per-project data lives under
# <that>/projects/<project_id>/ and is created by `sandesh setup --project <id>`.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/sandesh"
BINDIR="$HOME/.local/bin"

echo "installing Sandesh:  $SRC  →  $DEST"
mkdir -p "$DEST/app" "$DEST/bin" "$DEST/projects"
cp "$SRC/app/"*.py "$DEST/app/"
cp "$SRC/bin/sandesh" "$DEST/bin/sandesh"
chmod +x "$DEST/bin/sandesh"

mkdir -p "$BINDIR"
ln -sf "$DEST/bin/sandesh" "$BINDIR/sandesh"

echo "✓ installed → $DEST"
echo "✓ launcher  → $BINDIR/sandesh"
case ":$PATH:" in
  *":$BINDIR:"*) : ;;
  *) echo "  NOTE: $BINDIR is not on \$PATH — add it to use 'sandesh' directly." ;;
esac
echo
echo "next:  sandesh setup --project <id>   then   sandesh --project <id> register --address 'Mainline - <id>' --kind mainline"
