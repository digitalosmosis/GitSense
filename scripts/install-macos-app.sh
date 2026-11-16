#!/usr/bin/env bash
# Install GitSense as a macOS app that opens from Finder, Launchpad and Spotlight.
#
# Usage: scripts/install-macos-app.sh [--dev] [DIR]
#   DIR    where GitSense.app goes (default: /Applications, or ~/Applications)
#   --dev  run the app from this checkout, so code edits apply on the next launch
#
# Without --dev, GitSense is installed with `uv tool install`,
# so the app keeps working if this checkout moves or is deleted.
set -euo pipefail

usage() {
    sed -n '4,6p' "$0" | sed 's/^# \{0,1\}//'
}

dev=0
dir=()
for arg in "$@"; do
    case "$arg" in
        --dev) dev=1 ;;
        -h | --help) usage; exit 0 ;;
        -*) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
        *)
            if [ "${#dir[@]}" -gt 0 ]; then
                echo "Give at most one folder." >&2
                exit 2
            fi
            dir=("$arg")
            ;;
    esac
done

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This script is for macOS. On other systems run gitsense from a terminal." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install it with one of these, then run this script again:" >&2
    echo "  brew install uv" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

if [ "$dev" -eq 1 ]; then
    echo "Setting up the development environment in $repo"
    uv sync
    uv run gitsense --install-app ${dir[@]+"${dir[@]}"}
else
    echo "Installing the gitsense command with uv"
    uv tool install --force "$repo"
    gitsense_bin="$(uv tool dir --bin)/gitsense"
    "$gitsense_bin" --install-app ${dir[@]+"${dir[@]}"}
fi
