#!/bin/bash
# Run Ralph inside the macOS sandbox. Always use this instead of ralph.sh.
# Usage: ./scripts/ralph/safe-ralph.sh [max_iterations]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ "$(pwd)" != "$REPO_ROOT" ]]; then
  echo "Refusing to run: cd to $REPO_ROOT first." >&2
  exit 1
fi
GUARD_DIR="$(mktemp -d)"
trap 'rm -r "$GUARD_DIR"' EXIT
"$SCRIPT_DIR/make-guard.sh" "$GUARD_DIR"
export ETRACE_SANDBOXED=1
export ETRACE_GUARD_SETTINGS="$GUARD_DIR/guard-settings.json"
sandbox-exec -D HOME="$HOME" -f "$GUARD_DIR/guard.sb" \
  "$SCRIPT_DIR/ralph.sh" --tool claude "${1:-3}"
