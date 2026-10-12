"""The terminal side of the etrace CLI: pretty vs JSON, colour switches, help, home, errors."""
import argparse
import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from etrace import cli, render, ui

TZ = "America/Chicago"
CLEAN_ENV = {"FORCE_COLOR": "", "NO_COLOR": "", "TERM": "xterm-256color", "COLORTERM": "truecolor",
             cli.ENV_CONFIG: ""}


class Stream(io.StringIO):
    def __init__(self, tty=False, encoding="utf-8"):
        super().__init__()
        self._tty, self._encoding = tty, encoding

    def isatty(self):
        return self._tty

    @property
    def encoding(self):
        return self._encoding


class PrettyCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "trace-data")
        self.config = os.path.join(self.root, "trace.toml")
        self.today = datetime.datetime.now(ZoneInfo(TZ)).date()
        code, out, _ = self.run_cli("init", "--preset", "default", "--dir", self.root, "--language", "en",
                                    "--timezone", TZ)
        self.assertEqual(code, 0)

    def run_cli(self, *argv, tty=False, err_tty=False, encoding="utf-8", env=None, config=True):
        out, err = Stream(tty, encoding), Stream(err_tty, encoding)
        full_env = dict(CLEAN_ENV, **(env or {}))
        args = (["--config", self.config] if config and os.path.exists(self.config) else []) + list(argv)
        with mock.patch.dict(os.environ, full_env), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def write_json(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    def predict_days(self, n):
        for i in range(n):
            day = (self.today - datetime.timedelta(days=n - 1 - i)).isoformat()
            items = [{"code": c, "value": 5 + (i + k) % 4, "provenance": "self_report", "source": "note",
                      "confidence": "high"} for k, c in enumerate(("B1", "B2", "B3", "B4", "E1", "E2", "E3", "E4"))]
            code, _, err = self.run_cli("g", "predict", self.write_json("g.json", {"date": day, "items": items}))
            self.assertEqual(code, 0, err)

    # --- JSON for machines, pretty for people -----------------------------------------

    def test_piped_output_stays_json(self):
        code, out, err = self.run_cli("validate")
        self.assertEqual((code, json.loads(out), err), (0, {"ok": True, "problems": []}, ""))

    def test_terminal_output_is_pretty_and_coloured(self):
        code, out, _ = self.run_cli("validate", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("\x1b[", out)
        text = ui.strip_ansi(out)
        self.assertIn("All clear", text)
        self.assertNotIn("{", text)

    def test_pretty_flag_forces_pretty_on_a_pipe_without_colour(self):
        code, out, _ = self.run_cli("--pretty", "validate")
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out)
        self.assertIn("All clear", out)

    def test_json_flag_forces_json_in_a_terminal(self):
        code, out, _ = self.run_cli("validate", "--json", tty=True)
        self.assertEqual(json.loads(out), {"ok": True, "problems": []})
        self.assertNotIn("\x1b", out)

    def test_output_flags_work_before_and_after_the_command(self):
        a = self.run_cli("--pretty", "rules", "list")[1]
        b = self.run_cli("rules", "list", "--pretty")[1]
        c = self.run_cli("rules", "--pretty", "list")[1]
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertIn("rule(s)", a)

    def test_take_flags(self):
        self.assertEqual(cli._take_flags(["--json", "a", "--no-color", "b"]), (["a", "b"], "json", True))
        self.assertEqual(cli._take_flags(["x", "--pretty"]), (["x"], "pretty", False))
        self.assertEqual(cli._take_flags(["--json", "a", "--", "--pretty"]), (["a", "--", "--pretty"], "json", False))
        self.assertEqual(cli._take_flags([]), ([], None, False))
        self.assertEqual(cli._take_flags(["--pretty", "--json"])[1], "json")  # the last one wins

    # --- colour switches -------------------------------------------------------------------

    def test_colour_switches(self):
        has = lambda *a, **k: "\x1b[" in self.run_cli(*a, **k)[1]
        self.assertTrue(has("validate", tty=True))
        self.assertFalse(has("validate", "--no-color", tty=True))
        self.assertFalse(has("validate", tty=True, env={"NO_COLOR": "1"}))
        self.assertTrue(has("--pretty", "validate", env={"FORCE_COLOR": "1"}))
        self.assertFalse(has("validate", "--no-color", tty=True, env={"FORCE_COLOR": "1"}))
        self.assertFalse(has("validate", tty=True, env={"TERM": "dumb"}))

    def test_non_utf8_terminal_gets_an_ascii_look(self):
        code, out, _ = self.run_cli("validate", "--no-color", tty=True, encoding="ascii")
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii(), out)
        self.assertIn("All clear", out)
        code, out, _ = self.run_cli(tty=True, encoding="ascii", config=False)
        self.assertTrue(ui.strip_ansi(out).isascii())

    # --- commands --------------------------------------------------------------------------

    def test_init_pretty_shows_banner_and_next_steps(self):
        root = os.path.join(self.tmp, "second")
        code, out, _ = self.run_cli("init", "--preset", "tiger-method", "--dir", root, "--pretty", "--no-color",
                                    config=False)
        self.assertEqual(code, 0)
        self.assertIn(ui.LOGO[0], out)
        self.assertIn("Your data folder is ready", out)
        self.assertIn("export ETRACE_CONFIG=" + os.path.join(root, "trace.toml"), out)  # whole, never cut
        self.assertNotIn("…", out)
        self.assertIn("tiger-method", out)

    def test_g_chart_json_has_the_trend_and_pretty_draws_it(self):
        self.predict_days(5)
        code, out, _ = self.run_cli("g", "chart", self.today.isoformat(), "--days", "5")
        data = json.loads(out)
        self.assertEqual(sorted(data["trend"]), ["B", "E", "G"])
        self.assertEqual({len(v) for v in data["trend"].values()}, {5})
        self.assertEqual(data["points"], {"B": 5, "E": 5, "G": 5})
        code, out, _ = self.run_cli("g", "chart", self.today.isoformat(), "--days", "5", "--pretty", "--no-color")
        self.assertIn("Trend chart written", out)
        self.assertTrue(any(c in out for c in ui.SPARKS))
        self.assertIn(data["chart"], out)

    def test_g_report_pretty(self):
        self.predict_days(1)
        code, out, _ = self.run_cli("g", "report", "--pretty", "--no-color")
        self.assertEqual(code, 0, out)
        for needle in ("B stability", "E exploration", "Calibration off"):
            self.assertIn(needle, out)

    def test_xp_score_pretty(self):
        levels = {"rhythm": 3, "learning": 2, "boundaries": 3, "action": 2, "reflection": 3, "connection": 2}
        f = self.write_json("xp.json", {"date": self.today.isoformat(), "levels": levels, "ordinary": 6})
        code, out, err = self.run_cli("xp", "score", f, "--pretty", "--no-color")
        self.assertEqual(code, 0, err)
        self.assertIn("today", out)
        self.assertIn("/ 100", out)

    def test_rules_list_pretty_lists_the_rules(self):
        code, out, _ = self.run_cli("rules", "list", "--pretty", "--no-color")
        data = json.loads(self.run_cli("rules", "list")[1])
        self.assertEqual(code, 0)
        for rule in data["rules"]:
            self.assertIn(rule["id"], out)

    def test_every_other_command_still_prints_something_pretty(self):
        code, out, _ = self.run_cli("note", "new", "yesterday", "--pretty", "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("note", out)
        self.assertNotIn("{", out)
        code, out, _ = self.run_cli("audit", "weekly", "--pretty", "--no-color")
        self.assertIn(("Audit passed" if code == 0 else "Audit failed"), out)

    def test_raw_prompt_is_never_prettified(self):
        code, raw, _ = self.run_cli("prompt", "daily", "--raw", tty=True)
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", raw)
        self.assertNotIn(ui.LOGO[0], raw)
        self.assertIn("etrace", raw)

    def test_a_view_that_fails_falls_back_to_json(self):
        with mock.patch.object(render, "render", return_value=None):
            code, out, _ = self.run_cli("validate", "--pretty")
        self.assertEqual(json.loads(out), {"ok": True, "problems": []})

    # --- failures and errors ---------------------------------------------------------------

    def test_failed_validate_is_pretty_and_still_exits_1(self):
        bad = os.path.join(self.root, "journal", "2026-01-01.md")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("<!-- trace:ai:pad -->\n<!-- trace:ai:pad -->\n")
        code, out, err = self.run_cli("validate", "--pretty", "--no-color")
        self.assertEqual(code, 1)
        self.assertIn("problem(s) found", out)
        self.assertIn("2026-01-01.md", out)
        self.assertTrue(err.startswith("failed: "))

    def test_errors_keep_their_text_and_only_gain_colour_in_a_terminal(self):
        code, _, err = self.run_cli("frobnicate")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error: usage"), err)
        self.assertNotIn("\x1b", err)
        code, _, err = self.run_cli("frobnicate", err_tty=True)
        self.assertEqual(code, 2)
        self.assertIn("\x1b[", err)
        self.assertIn("✘", err)
        self.assertIn("error:", ui.strip_ansi(err))
        code, _, err = self.run_cli("frobnicate", err_tty=True, env={"NO_COLOR": "1"})
        self.assertTrue(err.startswith("error: usage"), err)

    def test_stdout_and_stderr_are_styled_independently(self):
        code, out, err = self.run_cli("frobnicate", tty=False, err_tty=True)
        self.assertEqual(out, "")
        self.assertIn("\x1b[", err)
        code, out, err = self.run_cli("validate", tty=True, err_tty=False)
        self.assertIn("\x1b[", out)
        self.assertEqual(err, "")

    # --- help and home ---------------------------------------------------------------------

    def test_help_screen_in_a_terminal(self):
        out = Stream(tty=True)
        with mock.patch.dict(os.environ, CLEAN_ENV), mock.patch("sys.stdout", out):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        text = ui.strip_ansi(out.getvalue())
        self.assertIn("\x1b[", out.getvalue())
        self.assertIn(ui.LOGO[0], text)
        for opt in ("--json", "--pretty", "--no-color"):
            self.assertIn(opt, text)

    def test_help_screen_piped_is_compact_and_plain(self):
        out = Stream(tty=False)
        with mock.patch.dict(os.environ, CLEAN_ENV), mock.patch("sys.stdout", out):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out.getvalue())
        self.assertNotIn(ui.LOGO[0], out.getvalue())
        self.assertIn("etrace [options] COMMAND", out.getvalue())

    def test_help_lists_every_command_exactly_once(self):
        parser = cli.build_parser()
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        listed = [name for _, names in cli.HELP_GROUPS for name in names]
        self.assertEqual(sorted(listed), sorted(action.choices))
        out = Stream()
        with mock.patch.dict(os.environ, CLEAN_ENV), mock.patch("sys.stdout", out):
            cli.main(["-h"])
        for name, helped in ((a.dest, a.help) for a in action._choices_actions):
            self.assertIn(name, out.getvalue())
            self.assertIn(helped, out.getvalue())

    # --- every help page -------------------------------------------------------------------

    def all_parsers(self):
        return list(cli._walk(cli.build_parser()))

    def help_of(self, parser, tty=False, encoding="utf-8", env=None):
        out = Stream(tty=tty, encoding=encoding)
        with mock.patch.dict(os.environ, dict(CLEAN_ENV, **(env or {}))), mock.patch("sys.stdout", out):
            text = parser.format_help()
        return text

    def test_there_are_34_help_pages_and_each_one_renders(self):
        parsers = self.all_parsers()
        self.assertEqual(len(parsers), 34)
        for parser in parsers:
            for tty, enc in ((False, "utf-8"), (True, "utf-8"), (False, "ascii")):
                text = self.help_of(parser, tty, enc)
                self.assertTrue(text.strip(), parser.prog)
                self.assertEqual("\x1b[" in text, tty, parser.prog)
                self.assertTrue(text.strip().split("\n")[0].count("etrace") >= 1 or parser.prog == "etrace", parser.prog)
                if enc == "ascii":
                    self.assertTrue(text.isascii(), parser.prog)
                self.assertFalse(any(c in text for c in "●○◯•"), parser.prog)

    def test_every_help_page_is_reachable_with_help_and_exits_0(self):
        for parser in self.all_parsers():
            argv = parser.prog.split()[1:] + ["--help"]
            code, out, err = self.run_cli(*argv, config=False)
            self.assertEqual((code, err), (0, ""), argv)
            self.assertTrue(out.strip(), argv)

    def test_every_help_line_fits_the_terminal(self):
        for parser in self.all_parsers():
            with mock.patch.object(ui.shutil, "get_terminal_size", return_value=os.terminal_size((72, 24))):
                text = self.help_of(parser, tty=True)
            for line in ui.strip_ansi(text).split("\n"):
                self.assertLessEqual(ui.visible_len(line), 72, (parser.prog, line))

    def test_every_subcommand_has_a_summary_and_every_argument_some_help(self):
        for parser in self.all_parsers():
            if parser.prog != "etrace":
                self.assertTrue(parser.description, parser.prog)
            for action in parser._actions:
                if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                    continue
                if action.dest in ("json", "pretty", "no_color"):
                    continue
                self.assertTrue(action.help, (parser.prog, action.dest))

    def test_group_page_lists_its_commands(self):
        text = self.help_of(self.parser_for("g"))
        for name in ("predict", "feedback", "report", "chart"):
            self.assertIn(name, text)
        self.assertIn("G estimates", text)
        self.assertNotIn("usage: etrace", text)  # not argparse's own layout

    def test_leaf_page_shows_arguments_options_defaults_and_choices(self):
        text = self.help_of(self.parser_for("init"))
        for needle in ("--preset {default,tiger-method}", "--dir DIR", "America/Chicago",
                       "--language {zh,en}", "create a fresh data dir from a preset", "-h, --help", "--no-color"):
            self.assertIn(needle, text)
        chart = self.help_of(self.parser_for("g chart"))
        self.assertIn("--days DAYS", chart)
        self.assertIn("window ending on DATE (default 30)", chart)
        self.assertEqual(chart.count("(default"), 1)  # a default already in the help text is not repeated
        write = self.help_of(self.parser_for("note write"))
        self.assertIn("{headline,xp,narrative}", write)
        self.assertIn("agent JSON", self.help_of(self.parser_for("usage add")))

    def test_help_page_in_colour_keeps_its_text(self):
        plain = ui.strip_ansi(self.help_of(self.parser_for("g report"), tty=True))
        self.assertEqual(plain, self.help_of(self.parser_for("g report")))

    def parser_for(self, path):
        return next(p for p in self.all_parsers() if p.prog == "etrace " + path)

    def test_a_bad_option_is_still_an_error_not_a_help_page(self):
        code, _, err = self.run_cli("g", "report", "--bogus")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error: usage"), err)

    def test_version_is_unchanged(self):
        code, out, _ = self.run_cli("--version", tty=True)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^etrace \d+\.\d+\.\d+\n$")

    def test_bare_command_shows_the_home_screen(self):
        code, out, err = self.run_cli(tty=True, config=False, env={cli.ENV_CONFIG: self.config})
        self.assertEqual((code, err), (0, ""))
        text = ui.strip_ansi(out)
        self.assertIn(ui.LOGO[0], text)
        self.assertIn(self.root, text)
        self.assertIn("default", text)
        self.assertIn(TZ, text)
        self.assertIn("Start here", text)

    def test_bare_command_without_a_data_folder(self):
        empty = os.path.join(self.tmp, "nowhere")
        os.makedirs(empty)
        cwd = os.getcwd()
        os.chdir(empty)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(cli, "DEFAULT_ROOT", os.path.join(empty, "none")):
            code, out, err = self.run_cli(tty=False, config=False)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("No data folder found yet", out)
        self.assertIn("etrace init --preset default", out)

    def test_bare_command_notes_a_missing_note(self):
        os.remove(os.path.join(self.root, "journal", f"{self.today.isoformat()}.md"))
        code, out, _ = self.run_cli(config=False, env={cli.ENV_CONFIG: self.config})
        self.assertIn("not created yet", out)


if __name__ == "__main__":
    unittest.main()
