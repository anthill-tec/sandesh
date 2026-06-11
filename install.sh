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

# Which extras to install. Default to [mcp,migrate] so the MCP server works out of
# the box AND the venv gets yoyo+jsonschema for `migrate --all` (DEC-2); a caller
# can override (e.g. SANDESH_INSTALL_EXTRAS="" for base-only).
EXTRAS="${SANDESH_INSTALL_EXTRAS-[mcp,migrate]}"

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

# CR-SAN-023 §S2b: assign the Sandesh admin from $SANDESH_ADMIN via an inline
# venv-python call — there is deliberately NO CLI verb for this (PRD O3: no
# agent-reachable surface may create/change the admin). The name is read from
# the environment INSIDE python (quote/injection-safe — never interpolated into
# the code), and the different-name re-assign refusal (ValueError) is caught
# there and surfaced as a notice, NOT an install abort (the python exits 0, so
# `set -e` lets the install COMPLETE). Unset → skip with a notice.
if [ -n "${SANDESH_ADMIN:-}" ]; then
  SANDESH_ADMIN="$SANDESH_ADMIN" "$VENV/bin/python" -c $'import os\nfrom sandesh import sandesh_db as s\ncon = s.connect()\ntry:\n    s.assign_admin(con, os.environ["SANDESH_ADMIN"])\n    print("✓ Sandesh admin assigned: %r" % s.admin_name(con))\nexcept ValueError as exc:\n    print("  NOTE: %s — keeping %r (install continues)" % (exc, s.admin_name(con)))\nfinally:\n    con.close()'
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
