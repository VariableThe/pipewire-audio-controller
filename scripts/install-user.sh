#!/bin/sh
# Explicit user-local GUI installation. Does not install audio routing.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$repo_dir/scripts/install_user.py" "$@"
