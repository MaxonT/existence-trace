#!/bin/bash
# Build the sandbox profile and Claude permission settings for one Ralph run.
# Usage: make-guard.sh OUT_DIR
# Reads the directories to protect from scripts/ralph/protected-paths.local
# (one path per line, "~" allowed, "#" comments; this file is NOT in git).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:?usage: make-guard.sh OUT_DIR}"
LIST="$SCRIPT_DIR/protected-paths.local"

if [[ ! -f "$LIST" ]]; then
  if [[ "${ETRACE_NO_PROTECTED_PATHS:-}" == "1" ]]; then
    : > "$OUT/protected.list"
  else
    echo "Missing $LIST" >&2
    echo "Copy protected-paths.example to protected-paths.local and list the directories Ralph must never touch." >&2
    echo "(Set ETRACE_NO_PROTECTED_PATHS=1 only if you really have none.)" >&2
    exit 1
  fi
else
  : > "$OUT/protected.list"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    [[ "$line" == "~" || "$line" == "~/"* ]] && line="$HOME${line#\~}"
    if [[ "$line" != /* || "$line" == *'"'* || "$line" == *'\'* || "$line" == "/" || "$line" == "$HOME" ]]; then
      echo "Refusing unsafe protected path: $line" >&2
      exit 1
    fi
    echo "$line" >> "$OUT/protected.list"
  done < "$LIST"
fi

cp "$SCRIPT_DIR/guard.sb" "$OUT/guard.sb"
while IFS= read -r path; do
  printf '(deny file-read* file-write* (subpath "%s"))\n' "$path" >> "$OUT/guard.sb"
done < "$OUT/protected.list"

python3 - "$OUT" "$HOME" <<'PY'
import json, sys
out, home = sys.argv[1], sys.argv[2]
deny = ["mcp__*"]
for path in open(f"{out}/protected.list").read().split("\n"):
    if not path:
        continue
    pat = "~" + path[len(home):] if path.startswith(home + "/") else "/" + path
    deny += [f"Read({pat}/**)", f"Edit({pat}/**)"]
json.dump({"permissions": {"deny": deny}}, open(f"{out}/guard-settings.json", "w"), indent=2)
PY
