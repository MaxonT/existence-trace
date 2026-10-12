#!/usr/bin/env bash
# existence-trace installer. Installs the `etrace` command from GitHub.
#
#   curl -fsSL https://maxont.github.io/existence-trace/install.sh | bash
#   bash install.sh --dry-run        # show what would happen, change nothing
#
# What it does:  installs the Python package (stdlib only) with uv, pipx or pip --user.
# What it never does: edit shell config, touch ~/.ssh, create your data directory,
# or read any of your files. Create the data directory yourself with `etrace init`.
set -euo pipefail

REPO="https://github.com/MaxonT/existence-trace"
REF="main"
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --ref) REF="${2:?--ref needs a branch, tag or commit}"; shift ;;
    -h|--help) sed -n '2,11p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

SPEC="git+${REPO}@${REF}"

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else say "+ $*"; "$@"; fi
}

if ! command -v python3 >/dev/null 2>&1; then
  say "error: python3 not found. existence-trace needs Python 3.11 or newer." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  say "error: Python 3.11+ required, found $(python3 -V 2>&1)." >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  say "installer: uv"
  run uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
  say "installer: pipx"
  run pipx install --force "$SPEC"
elif python3 -m pip --version >/dev/null 2>&1; then
  say "installer: pip --user"
  run python3 -m pip install --user --upgrade "$SPEC"
else
  say "error: need one of uv, pipx or pip. Install uv (https://docs.astral.sh/uv/) and rerun." >&2
  exit 1
fi

if [ "$DRY" = 1 ]; then
  say "dry-run finished: nothing was changed."
  exit 0
fi

if command -v etrace >/dev/null 2>&1; then
  say "installed: $(etrace --version)"
else
  say "installed, but \`etrace\` is not on PATH yet."
  say "uv: run \`uv tool update-shell\`; pipx: run \`pipx ensurepath\`; pip: add \$(python3 -m site --user-base)/bin to PATH."
  say "Until then you can use: python3 -m etrace"
fi

cat <<'EOF'

Next, create your own data directory (it lives outside any repository):

  etrace init --language en
  etrace ai set
  etrace run daily

Prefer your own agent? Run:  etrace prompt daily --raw
EOF
