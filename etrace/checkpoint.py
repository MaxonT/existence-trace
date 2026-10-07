"""Local private git checkpoints of the user's data directory.

checkpoint(data_dir, message) makes data_dir a git repository if it is not one
yet and commits everything in it, but only when something changed (never an
empty commit). Nothing is pushed unless a remote is passed explicitly
(trace.toml `git_remote`), and a push is never forced: a remote that has
diverged is reported, not overwritten.

The user's global git config is never written. When no commit identity is
configured, a neutral one is passed on the command line for that commit only.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass

REMOTE_NAME = "etrace"
IDENTITY = ("existence-trace", "etrace@localhost")
GITIGNORE = "# Written by existence-trace: lock and temp files are never versioned.\n.etrace.lock\n.tmp-*\n"


class CheckpointError(RuntimeError):
    """git is missing or a local git command failed."""


@dataclass(frozen=True)
class CheckpointResult:
    committed: bool
    commit: str | None  # HEAD after the call (None if the repo still has no commits)
    initialized: bool  # a new repository was created by this call
    pushed: bool
    push_error: str | None = None


def available():
    return shutil.which("git") is not None


def _git(repo, *args, check=True):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise CheckpointError("git is not installed") from None
    if check and proc.returncode != 0:
        raise CheckpointError(f"git {args[0]} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def _is_repo_root(repo):
    proc = _git(repo, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        return False
    return os.path.realpath(proc.stdout.strip()) == os.path.realpath(repo)


def _head(repo):
    proc = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _identity_args(repo):
    args = []
    if not _git(repo, "config", "user.name", check=False).stdout.strip():
        args += ["-c", f"user.name={IDENTITY[0]}"]
    if not _git(repo, "config", "user.email", check=False).stdout.strip():
        args += ["-c", f"user.email={IDENTITY[1]}"]
    return args


def _set_remote(repo, url):
    current = _git(repo, "remote", "get-url", REMOTE_NAME, check=False)
    if current.returncode != 0:
        _git(repo, "remote", "add", REMOTE_NAME, url)
    elif current.stdout.strip() != url:
        _git(repo, "remote", "set-url", REMOTE_NAME, url)


def _push(repo, url):
    """Plain push of the current branch. Returns an error string or None."""
    try:
        _set_remote(repo, url)
    except CheckpointError as exc:
        return str(exc)
    branch = _git(repo, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()
    if not branch:
        return "HEAD is detached; not pushing"
    proc = _git(repo, "push", REMOTE_NAME, f"refs/heads/{branch}:refs/heads/{branch}", check=False)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or "git push failed"
    return None


def checkpoint(data_dir, message, remote=None):
    """Commit all changes in data_dir to its local git repository.

    remote: a git URL to push to after committing; None (the default) never pushes.
    Raises CheckpointError if git is missing or a local step fails. A failed push
    does not raise; it is returned in CheckpointResult.push_error.
    """
    if not isinstance(message, str) or not message.strip():
        raise ValueError("checkpoint message must be a non-empty string")
    if remote is not None and (not isinstance(remote, str) or not remote.strip()):
        raise ValueError("remote must be a non-empty string or None")
    if not available():
        raise CheckpointError("git is not installed")
    repo = os.path.abspath(os.path.expanduser(os.fspath(data_dir)))
    os.makedirs(repo, exist_ok=True)

    initialized = False
    if not _is_repo_root(repo):
        _git(repo, "init", "-q")
        initialized = True
    ignore = os.path.join(repo, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as fh:
            fh.write(GITIGNORE)

    _git(repo, "add", "-A")
    committed = False
    if _git(repo, "status", "--porcelain").stdout.strip():
        _git(repo, *_identity_args(repo), "commit", "-q", "-m", message)
        committed = True
    head = _head(repo)

    pushed, push_error = False, None
    if remote is not None and head is not None:
        push_error = _push(repo, remote)
        pushed = push_error is None
    return CheckpointResult(committed, head, initialized, pushed, push_error)
