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

# --- MCP server: dedicated venv (the CLI stays stdlib-only; only the MCP
#     server needs the third-party `mcp` SDK). -------------------------------
echo "creating MCP venv:   $DEST/.venv"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DEST/.venv/bin/python" -m pip install --quiet "mcp>=1.27,<2"

# Self-locating MCP wrapper — resolves its own real path (like bin/sandesh) so
# it works through the ~/.local/bin/sandesh-mcp symlink. Execs the venv python
# on app/mcp_server.py.
cat > "$DEST/bin/sandesh-mcp" <<'EOF'
#!/usr/bin/env bash
# Sandesh MCP server launcher — runs the MCP server from the install (or repo)
# location using the dedicated venv python. Resolves its own real path so it
# works whether invoked directly or via a symlink.
set -euo pipefail
self="$(readlink -f "${BASH_SOURCE[0]}")"
here="$(dirname "$self")"
exec "$here/../.venv/bin/python" "$here/../app/mcp_server.py" "$@"
EOF
chmod +x "$DEST/bin/sandesh-mcp"
ln -sf "$DEST/bin/sandesh-mcp" "$BINDIR/sandesh-mcp"

echo "✓ installed → $DEST"
echo "✓ launcher  → $BINDIR/sandesh"
echo "✓ MCP venv  → $DEST/.venv"
echo "✓ MCP wrap  → $BINDIR/sandesh-mcp"
case ":$PATH:" in
  *":$BINDIR:"*) : ;;
  *) echo "  NOTE: $BINDIR is not on \$PATH — add it to use 'sandesh' directly." ;;
esac
echo
echo "next:  sandesh setup --project <id>   then   sandesh --project <id> register --address 'Mainline - <id>' --kind mainline"
