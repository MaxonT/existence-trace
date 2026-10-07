import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from etrace import checkpoint
from etrace.checkpoint import CheckpointError
from etrace.store import Store

HAVE_GIT = shutil.which("git") is not None


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.data = os.path.join(self.root, "trace-data")
        # Isolate from the machine's git config (identity, signing, hooks); nothing is written there.
        empty = os.path.join(self.root, "gitconfig")
        open(empty, "w").close()
        env = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": empty,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": self.root,
        })
        env.start()
        self.addCleanup(env.stop)
        # A contributor's own GIT_AUTHOR_* / GIT_COMMITTER_* would override the identity under test.
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            clear = mock.patch.dict(os.environ)
            clear.start()
            self.addCleanup(clear.stop)
            os.environ.pop(name, None)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.data, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def commits(self, repo=None):
        return int(git(repo or self.data, "rev-list", "--count", "HEAD"))

    def test_initializes_repo_and_commits(self):
        self.write("journal/2026-10-07.md", "# 2026-10-07\n")
        Store(os.path.join(self.data, "data")).append("feedback", {"id": "f1", "text": "累"})
        r = checkpoint.checkpoint(self.data, "before daily")
        self.assertTrue(r.initialized)
        self.assertTrue(r.committed)
        self.assertFalse(r.pushed)
        self.assertTrue(os.path.isdir(os.path.join(self.data, ".git")))
        self.assertEqual(git(self.data, "rev-parse", "HEAD"), r.commit)
        self.assertEqual(git(self.data, "log", "-1", "--format=%s"), "before daily")
        files = git(self.data, "ls-files").splitlines()
        self.assertIn("journal/2026-10-07.md", files)
        self.assertIn("data/feedback.jsonl", files)
        self.assertIn(".gitignore", files)

    def test_no_changes_means_no_commit(self):
        self.write("a.txt", "1")
        first = checkpoint.checkpoint(self.data, "one")
        again = checkpoint.checkpoint(self.data, "two")
        self.assertFalse(again.committed)
        self.assertFalse(again.initialized)
        self.assertEqual(again.commit, first.commit)
        self.assertEqual(self.commits(), 1)

    def test_empty_new_dir_has_no_empty_commit(self):
        # Only the .gitignore exists, so the first checkpoint commits that file.
        r = checkpoint.checkpoint(self.data, "start")
        self.assertTrue(r.committed)
        self.assertFalse(checkpoint.checkpoint(self.data, "again").committed)
        self.assertEqual(self.commits(), 1)

    def test_commits_modifications_and_deletions(self):
        self.write("a.txt", "1")
        self.write("b.txt", "1")
        checkpoint.checkpoint(self.data, "one")
        self.write("a.txt", "2")
        os.remove(os.path.join(self.data, "b.txt"))
        r = checkpoint.checkpoint(self.data, "two")
        self.assertTrue(r.committed)
        self.assertEqual(self.commits(), 2)
        self.assertEqual(git(self.data, "ls-files").splitlines(), [".gitignore", "a.txt"])

    def test_lock_and_temp_files_are_ignored(self):
        self.write("data/.etrace.lock", "123")
        self.write("data/.tmp-abc", "partial")
        self.write("data/rules.json", "{}")
        checkpoint.checkpoint(self.data, "one")
        files = git(self.data, "ls-files").splitlines()
        self.assertIn("data/rules.json", files)
        self.assertNotIn("data/.etrace.lock", files)
        self.assertNotIn("data/.tmp-abc", files)

    def test_existing_gitignore_is_kept(self):
        self.write(".gitignore", "secret.txt\n")
        self.write("secret.txt", "x")
        checkpoint.checkpoint(self.data, "one")
        with open(os.path.join(self.data, ".gitignore"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "secret.txt\n")
        self.assertNotIn("secret.txt", git(self.data, "ls-files").splitlines())

    def test_data_dir_inside_another_repo_gets_its_own_repo(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self.write("a.txt", "1")
        r = checkpoint.checkpoint(self.data, "one")
        self.assertTrue(r.initialized)
        self.assertEqual(
            os.path.realpath(git(self.data, "rev-parse", "--show-toplevel")),
            os.path.realpath(self.data),
        )

    def test_uses_configured_identity_when_present(self):
        self.write("a.txt", "1")
        subprocess.run(["git", "init", "-q", self.data], check=True)
        git(self.data, "config", "user.name", "Example Person")
        git(self.data, "config", "user.email", "person@example.com")
        checkpoint.checkpoint(self.data, "one")
        self.assertEqual(git(self.data, "log", "-1", "--format=%an <%ae>"),
                         "Example Person <person@example.com>")

    def test_fallback_identity_is_not_written_to_config(self):
        self.write("a.txt", "1")
        checkpoint.checkpoint(self.data, "one")
        self.assertEqual(git(self.data, "log", "-1", "--format=%ae"), checkpoint.IDENTITY[1])
        proc = subprocess.run(["git", "-C", self.data, "config", "user.email"],
                              capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "")
        with open(os.environ["GIT_CONFIG_GLOBAL"], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_never_pushes_without_remote(self):
        self.write("a.txt", "1")
        with mock.patch.object(checkpoint, "_push") as push:
            r = checkpoint.checkpoint(self.data, "one")
        push.assert_not_called()
        self.assertFalse(r.pushed)
        self.assertEqual(git(self.data, "remote"), "")

    def test_pushes_to_explicit_remote(self):
        remote = os.path.join(self.root, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        self.write("a.txt", "1")
        r = checkpoint.checkpoint(self.data, "one", remote=remote)
        self.assertTrue(r.pushed, r.push_error)
        branch = git(self.data, "symbolic-ref", "--short", "HEAD")
        self.assertEqual(git(remote, "rev-parse", branch), r.commit)
        # Unchanged data still pushes (catches up after an earlier failed push) without a new commit.
        again = checkpoint.checkpoint(self.data, "two", remote=remote)
        self.assertFalse(again.committed)
        self.assertTrue(again.pushed)

    def test_never_force_pushes_over_diverged_remote(self):
        remote = os.path.join(self.root, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        self.write("a.txt", "1")
        checkpoint.checkpoint(self.data, "local")
        branch = git(self.data, "symbolic-ref", "--short", "HEAD")
        other = os.path.join(self.root, "other")
        subprocess.run(["git", "init", "-q", "-b", branch, other], check=True)
        with open(os.path.join(other, "x.txt"), "w") as fh:
            fh.write("other")
        git(other, "add", "-A")
        git(other, "-c", "user.name=o", "-c", "user.email=o@example.com", "commit", "-q", "-m", "other")
        git(other, "push", "-q", remote, branch)
        remote_head = git(remote, "rev-parse", branch)

        calls = []
        real_git = checkpoint._git

        def spy(repo, *args, **kw):
            calls.append(args)
            return real_git(repo, *args, **kw)

        self.write("a.txt", "2")
        with mock.patch.object(checkpoint, "_git", side_effect=spy):
            r = checkpoint.checkpoint(self.data, "local 2", remote=remote)
        self.assertTrue(r.committed)
        self.assertFalse(r.pushed)
        self.assertTrue(r.push_error)
        self.assertEqual(git(remote, "rev-parse", branch), remote_head)
        for args in calls:
            self.assertFalse(any(a.startswith(("-f", "--force", "+")) for a in args), args)
            self.assertFalse(any(":" in a and a.startswith("+") for a in args), args)

    def test_push_failure_does_not_raise(self):
        self.write("a.txt", "1")
        r = checkpoint.checkpoint(self.data, "one", remote=os.path.join(self.root, "missing.git"))
        self.assertTrue(r.committed)
        self.assertFalse(r.pushed)
        self.assertTrue(r.push_error)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            checkpoint.checkpoint(self.data, "  ")
        with self.assertRaises(ValueError):
            checkpoint.checkpoint(self.data, "m", remote="")


class CheckpointWithoutGitTest(unittest.TestCase):
    def test_missing_git_raises_checkpoint_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(checkpoint.shutil, "which", return_value=None):
            self.assertFalse(checkpoint.available())
            with self.assertRaisesRegex(CheckpointError, "not installed"):
                checkpoint.checkpoint(tmp, "m")
            self.assertFalse(os.path.exists(os.path.join(tmp, ".git")))


if __name__ == "__main__":
    unittest.main()
