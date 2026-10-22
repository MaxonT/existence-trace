"""What: tests for star_prompt: asks once, default no, never outside a terminal or in CI, falls back to the link.
Why: a star prompt that repeats, stars by default or breaks a command would hurt users and the project.
How: python3 -m unittest discover -s tests
"""
import io
import subprocess
import tempfile
import unittest

from etrace import star_prompt


class Tty(io.StringIO):
    def isatty(self):
        return True


class Done:
    def __init__(self, code):
        self.returncode = code


class StarPromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ask(self, answer="y\n", env=None, tty=True, run=None, gh="/usr/bin/gh", stdin=None, out_tty=True):
        self.calls = []

        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            return run(cmd) if run else Done(0)

        stdin = stdin or (Tty(answer) if tty else io.StringIO(answer))
        self.err = Tty() if tty else io.StringIO()
        e = {"XDG_CONFIG_HOME": self.tmp.name}
        e.update(env or {})
        return star_prompt.maybe_ask("MaxonT/demo", "demo", stdin=stdin, stdout=Tty() if out_tty else io.StringIO(),
                                     stderr=self.err, env=e,
                                     run=fake_run, which=lambda name: gh)

    def test_yes_stars_through_gh(self):
        self.assertEqual(self.ask(), "starred")
        self.assertEqual(self.calls, [["/usr/bin/gh", "api", "-X", "PUT", "/user/starred/MaxonT/demo", "--silent"]])
        self.assertIn("[y/N]", self.err.getvalue())
        self.assertIn("Starred MaxonT/demo", self.err.getvalue())

    def test_default_is_no(self):
        self.assertEqual(self.ask(answer="\n"), "declined")
        self.assertEqual(self.calls, [])

    def test_asked_only_once(self):
        self.ask(answer="n\n")
        self.assertEqual(self.ask(), "skipped: already asked")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.err.getvalue(), "")

    def test_never_asks_outside_a_terminal(self):
        self.assertEqual(self.ask(tty=False), "skipped: not a terminal")
        self.assertEqual(self.err.getvalue(), "")
        self.assertFalse(star_prompt.marker_path("demo", {"XDG_CONFIG_HOME": self.tmp.name}).exists())

    def test_never_asks_when_stdout_is_captured(self):
        # path=$(tool new ...): stdin and stderr are the terminal, stdout is not
        self.assertEqual(self.ask(out_tty=False), "skipped: not a terminal")
        self.assertEqual(self.err.getvalue(), "")

    def test_off_switch_and_ci(self):
        self.assertEqual(self.ask(env={"NO_STAR_PROMPT": "1"}), "skipped: disabled")
        self.assertEqual(self.ask(env={"CI": "true"}), "skipped: disabled")

    def test_without_gh_prints_link(self):
        self.assertEqual(self.ask(gh=None), "link")
        self.assertIn("https://github.com/MaxonT/demo", self.err.getvalue())

    def test_gh_failure_falls_back_to_link(self):
        def boom(cmd):
            raise subprocess.TimeoutExpired(cmd, 15)
        self.assertEqual(self.ask(run=boom), "link")
        self.assertIn("https://github.com/MaxonT/demo", self.err.getvalue())

    def test_ctrl_c_counts_as_no(self):
        class Interrupt(Tty):
            def readline(self, *a):
                raise KeyboardInterrupt
        self.assertEqual(self.ask(stdin=Interrupt()), "declined")
        self.assertEqual(self.ask(), "skipped: already asked")


if __name__ == "__main__":
    unittest.main()
