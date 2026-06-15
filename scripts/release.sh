#!/bin/bash

################################################################################
# release.sh
#
# CR-SAN-034 — branch-gated release pipeline driver for Sandesh.
#
# Wraps the git-flow release / hotfix finish + PyPI publish dispatch behind a
# small, branch-aware CLI so a release checkpoint cannot be triggered from the
# wrong branch.
#
# Subcommands:
#   checkpoint        Dispatch the PyPI publish workflow for the current branch
#                     (hotfix/* or release/* only).
#   finish <X.Y.Z>    Finish the git-flow hotfix/release and push main/develop
#                     + tags (hotfix/* or release/* only).
#   status            Print the current branch and derived version.
#
# Usage: ./scripts/release.sh <subcommand> [args] [--dry-run] [--verbose] [-h|--help]
################################################################################

set -euo pipefail

# ============================================================================
# Configuration & Defaults
# ============================================================================

# Exit codes
EXIT_SUCCESS=0
EXIT_ERROR=1
EXIT_USAGE=2

# Script variables (set via argument parsing)
SUBCOMMAND=""
VERSION=""
DRY_RUN=false
VERBOSE=false

# ============================================================================
# Helper Functions
# ============================================================================

# Print error message to stderr and exit
error() {
    echo "ERROR: $1" >&2
    exit "${2:-$EXIT_ERROR}"
}

# Print info message (to stderr to not interfere with stdout contracts)
info() {
    echo "$1" >&2
}

# Print debug message (only if verbose)
debug() {
    if [ "$VERBOSE" = true ]; then
        echo "DEBUG: $1" >&2
    fi
}

# Show usage information (to stdout)
usage() {
    cat <<'EOF'
Usage: release.sh <subcommand> [args] [--dry-run] [--verbose] [-h|--help]

Branch-gated release pipeline driver for Sandesh.

Subcommands:
  set-version <X.Y.Z>
                    Rewrite the manual manifest version strings to X.Y.Z and
                    commit them. Allowed only on hotfix/* or release/* branches.
                    Touches: integrations/pi/package.json, server.json.

  checkpoint        Dispatch the PyPI publish workflow for the current branch.
                    Allowed only on hotfix/* or release/* branches.
                    Runs: gh workflow run publish-pypi.yml --ref <branch>

  finish <X.Y.Z>    Finish the git-flow hotfix/release and push main/develop
                    + tags. Allowed only on hotfix/* or release/* branches.
                    Runs: git flow <kind> finish <X.Y.Z>
                          git push origin main develop --tags

  status            Print the current branch and the derived version
                    (git describe --tags, leading 'v' stripped). Exit 0.

Options:
  --dry-run         Print the commands that would run without executing them.
  --verbose         Print debug output.
  -h, --help        Show this help and exit 0.
EOF
}

# Current branch name
current_branch() {
    git rev-parse --abbrev-ref HEAD
}

# Require the current branch to be hotfix/* or release/*; exit 2 otherwise.
# Echoes the branch name on success.
require_release_branch() {
    local branch
    branch="$(current_branch)"
    case "$branch" in
        hotfix/*|release/*)
            echo "$branch"
            ;;
        *)
            error "must be run on a hotfix/* or release/* branch (current: $branch)" "$EXIT_USAGE"
            ;;
    esac
}

# Derive the git-flow kind (hotfix|release) from the current branch prefix.
branch_kind() {
    local branch="$1"
    case "$branch" in
        hotfix/*) echo "hotfix" ;;
        release/*) echo "release" ;;
        *) error "cannot derive git-flow kind from branch: $branch" "$EXIT_USAGE" ;;
    esac
}

# ============================================================================
# Subcommand implementations
# ============================================================================

# Rewrite the manual manifest version strings to $VERSION and commit them.
# Branch-gated (hotfix/* or release/* only) and version-validated (X.Y.Z).
cmd_set_version() {
    local branch
    branch="$(require_release_branch)"

    # Validate the version string (must be exactly X.Y.Z).
    if [ -z "$VERSION" ] || ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        error "set-version requires a version X.Y.Z (got: '$VERSION')" "$EXIT_USAGE"
    fi

    # Anchor manifest paths to the repo root, not the cwd, so the script is
    # correct when invoked by absolute path.
    local root
    root="$(git rev-parse --show-toplevel)"

    local manifests=(
        "$root/integrations/pi/package.json"
        "$root/server.json"
    )

    # Collect the manifests that actually exist (absent ones are skipped).
    local present=()
    local f
    for f in "${manifests[@]}"; do
        if [ -f "$f" ]; then
            present+=("$f")
        fi
    done

    if [ "$DRY_RUN" = true ]; then
        echo "set-version: would set version to $VERSION in:"
        if [ "${#present[@]}" -eq 0 ]; then
            echo "  (no manifests found)"
        else
            for f in "${present[@]}"; do
                echo "  $f"
            done
        fi
        return "$EXIT_SUCCESS"
    fi

    if [ "${#present[@]}" -eq 0 ]; then
        info "set-version: no manifests found; nothing to do"
        return "$EXIT_SUCCESS"
    fi

    # Format-preserving rewrite: replace only the values of "version": keys,
    # leaving all other bytes/formatting untouched (no JSON re-serialization).
    for f in "${present[@]}"; do
        python3 - "$f" "$VERSION" <<'PY'
import re
import sys

path, new_version = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    text = fh.read()
text = re.sub(
    r'("version"\s*:\s*")[^"]*(")',
    lambda m: m.group(1) + new_version + m.group(2),
    text,
)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
    done

    git add "${present[@]}"
    git commit -m "chore(release): set manual manifests to $VERSION"
}

cmd_checkpoint() {
    local branch
    branch="$(require_release_branch)"

    local gh_cmd="gh workflow run publish-pypi.yml --ref $branch"

    if [ "$DRY_RUN" = true ]; then
        echo "$gh_cmd"
        return "$EXIT_SUCCESS"
    fi

    debug "dispatching: $gh_cmd"
    gh workflow run publish-pypi.yml --ref "$branch"
}

cmd_finish() {
    local branch
    branch="$(require_release_branch)"

    if [ -z "$VERSION" ]; then
        error "finish requires a version: release.sh finish <X.Y.Z>" "$EXIT_USAGE"
    fi

    local kind
    kind="$(branch_kind "$branch")"

    # Pass -m so git-flow does not open the annotated-tag editor (which
    # GIT_MERGE_AUTOEDIT does not suppress) — otherwise finish hangs/aborts non-interactively.
    local flow_cmd="git flow $kind finish -m \"Release $VERSION\" $VERSION"
    local push_cmd="git push origin main develop --tags"

    if [ "$DRY_RUN" = true ]; then
        echo "$flow_cmd"
        echo "$push_cmd"
        return "$EXIT_SUCCESS"
    fi

    debug "finishing: $flow_cmd"
    GIT_MERGE_AUTOEDIT=no git flow "$kind" finish -m "Release $VERSION" "$VERSION"
    debug "pushing: $push_cmd"
    git push origin main develop --tags
}

cmd_status() {
    local branch version
    branch="$(current_branch)"

    # Derive version from the latest tag; tolerate no-tag gracefully.
    if version="$(git describe --tags 2>/dev/null)"; then
        version="${version#v}"
    else
        version="(no tag)"
    fi

    echo "branch:  $branch"
    echo "version: $version"
}

# ============================================================================
# Argument parsing
# ============================================================================

POSITIONAL=()
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit "$EXIT_SUCCESS"
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        -*)
            usage >&2
            echo "ERROR: unknown flag: $1" >&2
            exit "$EXIT_USAGE"
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

# First positional = subcommand, second = version (for finish)
if [ "${#POSITIONAL[@]}" -ge 1 ]; then
    SUBCOMMAND="${POSITIONAL[0]}"
fi
if [ "${#POSITIONAL[@]}" -ge 2 ]; then
    VERSION="${POSITIONAL[1]}"
fi

if [ -z "$SUBCOMMAND" ]; then
    usage >&2
    echo "ERROR: no subcommand given" >&2
    exit "$EXIT_USAGE"
fi

# ============================================================================
# Dispatch
# ============================================================================

case "$SUBCOMMAND" in
    set-version)
        cmd_set_version
        ;;
    checkpoint)
        cmd_checkpoint
        ;;
    finish)
        cmd_finish
        ;;
    status)
        cmd_status
        ;;
    *)
        usage >&2
        echo "ERROR: unknown subcommand: $SUBCOMMAND" >&2
        exit "$EXIT_USAGE"
        ;;
esac
