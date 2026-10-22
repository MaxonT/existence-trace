"""What: after a command succeeds, ask the user once whether to star this repo on GitHub.
Why: people who like a tool rarely go back to the page to star it; one question at the right moment helps, more would nag.
How: maybe_ask("owner/repo", "tool") at the end of a successful run. Asked a single time per user (a marker
     file under ~/.config/<tool>/ remembers it even after a no or Ctrl-C), only in an interactive terminal,
     never in CI or with NO_STAR_PROMPT=1. Default is no. A yes stars through the user's own `gh` login,
     otherwise prints the link. Nothing here can fail the command that just ran. Stdlib only.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

GH_TIMEOUT = 15


def _isatty(stream):
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def marker_path(tool, env):
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / tool / "star-prompt-asked"


def maybe_ask(repo, tool, stdin=None, stderr=None, env=None, run=subprocess.run, which=shutil.which):
    """Ask once whether to star ``repo`` ("owner/name"). Returns what happened."""
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    env = os.environ if env is None else env
    if env.get("NO_STAR_PROMPT") or env.get("CI"):
        return "skipped: disabled"
    if not (_isatty(stdin) and _isatty(stderr)):
        return "skipped: not a terminal"
    marker = marker_path(tool, env)
    if marker.exists():
        return "skipped: already asked"
    try:  # remember first, so the question can never repeat
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("asked\n", encoding="utf-8")
    except OSError:
        return "skipped: cannot remember the answer"
    url = "https://github.com/%s" % repo
    try:
        stderr.write("\nEnjoying %s? Star %s on GitHub? [y/N] " % (tool, repo))
        stderr.flush()
        answer = stdin.readline().strip().lower()
    except (KeyboardInterrupt, EOFError, OSError, ValueError):
        stderr.write("\n")
        return "declined"
    if answer not in ("y", "yes"):
        return "declined"
    gh = which("gh")
    if gh:
        try:
            done = run([gh, "api", "-X", "PUT", "/user/starred/%s" % repo, "--silent"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=GH_TIMEOUT)
            if done.returncode == 0:
                stderr.write("Starred %s. Thank you!\n" % repo)
                return "starred"
        except (OSError, subprocess.SubprocessError):
            pass
    stderr.write("Thank you! Star it here: %s\n" % url)
    return "link"
