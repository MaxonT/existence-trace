"""No personal data ships: scan every repository file for emails, Notion-style ids and home paths."""
import os
import re
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The design doc still holds the read-only inventory of the owner's system; it is rewritten
# before the repository goes public and is the one file this scan skips.
EXCLUDED = {"docs/SYSTEM_DESIGN.md"}
SKIP_DIRS = {".git", "__pycache__"}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
# RFC 2606 reserved domains, safe for examples and tests.
ALLOWED_DOMAINS = ("example.com", "example.org", "example.net")
HEX32 = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})"
                   r"(?![0-9A-Fa-f])")
# A home path is the prefix followed by a user name; the bare prefix may appear in rules text.
HOME_PATH = re.compile("/" + "Users" + r"/[A-Za-z0-9._-]")


def repo_files():
    """Tracked files plus new untracked ones (so a file is checked before its first commit)."""
    if shutil.which("git") and os.path.isdir(os.path.join(REPO, ".git")):
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout.decode("utf-8")
        return sorted(p for p in out.split("\0") if p and os.path.isfile(os.path.join(REPO, p)))
    found = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), REPO).replace(os.sep, "/"))
    return sorted(found)


def find_problems(rel, text):
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in EMAIL.finditer(line):
            domain = m.group(1).lower()
            if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS):
                problems.append(f"{rel}:{lineno}: email {m.group(0)!r}")
        for m in HEX32.finditer(line):
            problems.append(f"{rel}:{lineno}: Notion-style id {m.group(0)!r}")
        if HOME_PATH.search(line):
            problems.append(f"{rel}:{lineno}: absolute home path")
    return problems


class PrivacyScanTest(unittest.TestCase):
    def test_repository_has_no_personal_data(self):
        files = [f for f in repo_files() if f not in EXCLUDED]
        self.assertIn("tests/test_privacy.py", files)
        problems = []
        for rel in files:
            with open(os.path.join(REPO, rel), "rb") as fh:
                raw = fh.read()
            if b"\0" in raw:
                continue  # binary
            problems += find_problems(rel, raw.decode("utf-8", errors="replace"))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_scanner_catches_each_kind(self):
        home = "/" + "Users" + "/someone/notes.md"
        text = "\n".join([
            "write to me at real.person" + "@" + "gmail.com",
            "fine: a@example.com, b@mail.example.org",
            "page " + "0123456789abcdef" * 2,
            "uuid 01234567-89ab-" + "cdef-0123-456789abcdef",
            "path " + home,
            "the bare prefix /" + "Users" + "/ alone is fine; so is a 31-hex " + "a" * 31,
        ])
        problems = find_problems("x.md", text)
        self.assertEqual([p.split(": ", 1)[0] for p in problems], ["x.md:1", "x.md:3", "x.md:4", "x.md:5"])


if __name__ == "__main__":
    unittest.main()
