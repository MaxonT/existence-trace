#!/usr/bin/env bash
# existence-trace installer. Installs the `etrace` command.
#
#   curl -fsSL https://maxont.github.io/existence-trace/install.sh | bash
#   bash install.sh --dry-run        # show what would happen, change nothing
#
# What it does:  downloads the latest release wheel from GitHub (about 200 KB) and installs it
# (stdlib only, no other packages) with uv, pipx or pip --user. Falls back to `git+` if the
# download is not possible. Prints every step, so you always see what it is doing.
# What it never does: edit shell config, touch ~/.ssh, create your data directory,
# or read any of your files. `etrace import` or `etrace init` create the data directory.
set -euo pipefail

REPO="MaxonT/existence-trace"
REF=""
DRY=0
TOTAL=3

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --ref) REF="${2:?--ref needs a branch, tag or commit}"; shift ;;
    -h|--help) sed -n '2,11p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
step() { printf '\n[%s/%s] %s\n' "$1" "$TOTAL" "$2"; }
# Installers read nothing from stdin: under `curl | bash` stdin is this very script.
run() {
  if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else say "+ $*"; "$@" </dev/null; fi
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/etrace-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

step 1 "Checking Python (needs 3.11 or newer)"
if ! command -v python3 >/dev/null 2>&1; then
  say "error: python3 not found. existence-trace needs Python 3.11 or newer." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' </dev/null; then
  say "error: Python 3.11+ required, found $(python3 -V 2>&1)." >&2
  exit 1
fi
say "ok: $(python3 -V 2>&1)"

step 2 "Getting existence-trace"
SPEC=""
if [ -z "$REF" ]; then
  say "asking GitHub for the latest release..."
  URL="$(curl -fsSL --connect-timeout 15 --max-time 30 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | python3 -c 'import sys, json
d = json.load(sys.stdin)
print(next(a["browser_download_url"] for a in d["assets"] if a["name"].endswith(".whl")))' 2>/dev/null || true)"
  if [ -n "$URL" ]; then
    say "latest: $(basename "$URL")"
    if [ "$DRY" = 1 ]; then
      say "[dry-run] download $URL"
      SPEC="$TMP/$(basename "$URL")"
    else
      SPEC="$TMP/$(basename "$URL")"
      say "downloading (a few seconds on a normal connection)..."
      if ! curl -fL --progress-bar --connect-timeout 15 --max-time 300 -o "$SPEC" "$URL" </dev/null; then
        say "download failed; trying git instead." >&2
        SPEC=""
      fi
    fi
  else
    say "could not read the latest release; trying git instead." >&2
  fi
fi
if [ -z "$SPEC" ]; then
  if ! command -v git >/dev/null 2>&1; then
    say "error: cannot download the release and git is not installed. Check your network and retry." >&2
    exit 1
  fi
  SPEC="git+https://github.com/$REPO@${REF:-main}"
  say "will install from $SPEC (this clones the repository, it can take a minute)"
fi

step 3 "Installing"
if command -v uv >/dev/null 2>&1; then
  say "installer: uv"
  run uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
  say "installer: pipx"
  run pipx install --force "$SPEC"
elif python3 -m pip --version >/dev/null 2>&1 </dev/null; then
  say "installer: pip --user"
  run python3 -m pip install --user --upgrade --disable-pip-version-check "$SPEC"
else
  say "error: need one of uv, pipx or pip. Install uv (https://docs.astral.sh/uv/) and rerun." >&2
  exit 1
fi

if [ "$DRY" = 1 ]; then
  say ""
  say "dry-run finished: nothing was changed."
  exit 0
fi

say ""
if command -v etrace >/dev/null 2>&1; then
  say "Done. $(etrace --version)"
else
  say "Installed, but \`etrace\` is not on PATH yet."
  say "uv: run \`uv tool update-shell\`; pipx: run \`pipx ensurepath\`; pip: add \$(python3 -m site --user-base)/bin to PATH."
  say "Until then you can use: python3 -m etrace"
fi

cat <<'EOF'

Next (each is one short command):

  etrace ai set                              connect an AI (your key stays outside the data folder)
  etrace import ~/path/to/diary --run        bring in your old diary (folder, files or Day One) and let the AI read it

No old diary?  etrace init  then  etrace note  then  etrace run daily
Prefer your own agent?  etrace prompt daily --raw
EOF
