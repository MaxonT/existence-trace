import unittest

from etrace import __version__, render, ui

PLAIN = ui.Style(color=False, unicode=True, width=100)
COLOR = ui.Style(color=True, unicode=True, width=100, truecolor=True)
ASCII = ui.Style(color=False, unicode=False, width=100)

RESULTS = {
    "init": {"root": "/data/trace", "config": "/data/trace/trace.toml", "preset": "default",
             "rules_version": 9, "note": "/data/trace/journal/2026-10-12.md"},
    "validate": {"ok": True, "problems": []},
    "xp score": {"xp": "XP-20261012-r1", "date": "2026-10-12", "total": 63,
                 "week": {"start": "2026-10-12", "total": 63, "missing": ["2026-10-13", "2026-10-14"]}},
    "g report": {"date": "2026-10-12", "prediction": "P-20261012-1", "B": 5.8, "E": 8.06, "G": 6.93,
                 "calibration": {"enabled": False, "reason": "need 10 + 10 days"}, "text": "| table |",
                 "note": "/data/trace/journal/2026-10-12.md"},
    "g chart": {"chart": "/data/trace/charts/g.svg", "days": 5,
                "points": {"B": 5, "E": 5, "G": 2},
                "trend": {"B": [5, 6, 7, 6.5, 8], "E": [4, None, 5, 5.5, 6], "G": [None, None, None, 6, 7]}},
    "pad chart": {"chart": "/data/trace/charts/pad.svg", "points": 3, "note": "/data/trace/journal/x.md"},
    "rules list": {"version": 3, "rules": [
        {"id": "r1", "scope": "narrative", "text": "Keep the narrative short."},
        {"id": "r2", "scope": "pad", "text": "把 PAD 里的词当作提示，不当作最终的词。"}]},
    "audit weekly": {"week": "2026-W41", "passed": True, "broken": [], "report": "/data/audits/2026-W41.md"},
    "checkpoint": {"committed": True, "commit": "abcdef1234567890", "initialized": False, "pushed": False},
    "note": {"date": "2026-10-12", "created": True, "note": "/data/trace/journal/2026-10-12.md"},
    "import": {"dry_run": False, "entries": 3, "days": 2, "first": "2025-03-04", "last": "2025-03-05",
               "added": 2, "unchanged": 1, "new_notes": 2, "imported": [], "ran": [],
               "created_folder": "/data/trace", "paths": ["/data/my diary"], "ai_ready": False,
               "skipped": [{"path": "loose.txt", "reason": "no date in the name, folder, front matter or first line"}]},
    "ai": {"provider": "openai", "service": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
           "model": "deepseek-chat",
           "key": {"needed": True, "found": True, "masked": "…0123", "source": "ETRACE_AI_KEY",
                   "problem": None, "exposed": False}},
    "ai set": {"provider": "anthropic", "service": "Anthropic", "base_url": "https://api.anthropic.com",
               "model": "m-1",
               "key": {"needed": True, "found": True, "masked": "…9999", "source": "~/.config/etrace/ai.toml",
                       "problem": None, "exposed": False},
               "test": {"ok": True, "ms": 120, "reply": "pong", "tools": True},
               "next": "etrace run daily"},
    "ai test": {"ok": True, "ms": 80, "reply": "pong", "tools": False, "provider": "openai", "model": "m-2"},
    "ai models": {"count": 3, "models": ["a", "b", "c"], "current": "b"},
    "run": {"task": "daily", "provider": "openai", "model": "m-2", "finished": True,
            "steps": [{"call": "etrace note today", "exit_code": 0}, {"call": "etrace validate", "exit_code": 2},
                      {"call": "read journal/x.md"}, {"call": "etrace bad", "error": "blocked"}],
            "usage": {"input": 1234, "output": 56}, "final": "All done.\n\nSecond paragraph."},
}


def plain(style, key, result=None):
    return ui.strip_ansi(render.render(style, key, RESULTS[key] if result is None else result))


class RenderTest(unittest.TestCase):
    def test_every_view_draws_in_every_style(self):
        for key, result in RESULTS.items():
            for style in (PLAIN, COLOR, ASCII):
                text = render.render(style, key, result)
                self.assertIsInstance(text, str, key)
                self.assertTrue(text.strip(), key)
                self.assertEqual("\x1b" in text, style.color, key)
                if not style.unicode and key != "rules list":  # rules text may be Chinese
                    self.assertTrue(ui.strip_ansi(text).isascii(), (key, style))

    def test_views_fit_the_terminal(self):
        narrow = ui.Style(color=False, unicode=True, width=60)
        for key, result in RESULTS.items():
            if key in ("init",):
                continue  # paths are printed whole on purpose
            for line in render.render(narrow, key, result).split("\n"):
                if "/data/" in line:
                    continue  # so are paths
                self.assertLessEqual(ui.visible_len(line), 60, (key, line))

    def test_validate(self):
        self.assertIn("All clear", plain(PLAIN, "validate"))
        bad = plain(PLAIN, "validate", {"ok": False, "problems": ["journal/x.md: broken", "data: bad"]})
        self.assertIn("2 problem(s)", bad)
        self.assertIn("journal/x.md: broken", bad)
        self.assertIn("✘", bad)

    def test_xp_score(self):
        text = plain(PLAIN, "xp score")
        self.assertIn("63", text)
        self.assertIn("2 day(s) missing", text)
        self.assertIn("XP-20261012-r1", text)
        # an event-heavy day over 100 must still draw a bar of fixed width
        big = RESULTS["xp score"] | {"total": 140}
        self.assertIn("140", plain(PLAIN, "xp score", big))

    def test_g_report(self):
        text = plain(PLAIN, "g report")
        for needle in ("B stability", "E exploration", "5.8", "8.1", "6.9", "Calibration off",
                       "need 10 + 10 days", "P-20261012-1"):
            self.assertIn(needle, text)
        on = RESULTS["g report"] | {"calibration": {"enabled": True, "reason": "gain 0.4"}}
        self.assertIn("Calibration on", plain(PLAIN, "g report", on))
        missing = RESULTS["g report"] | {"B": None, "G": None}
        self.assertIn("not enough items yet", plain(PLAIN, "g report", missing))

    def test_g_chart_sparklines(self):
        text = plain(PLAIN, "g chart")
        self.assertIn("Trend chart written", text)
        self.assertIn("lines drawn: B, E", text)       # G has only 2 points: not drawn
        self.assertNotIn("lines drawn: B, E, G", text)
        self.assertIn("▁", text)
        self.assertIn("/data/trace/charts/g.svg", text)
        no_trend = {k: v for k, v in RESULTS["g chart"].items() if k != "trend"}
        self.assertIn("Trend chart written", plain(PLAIN, "g chart", no_trend))

    def test_rules_list_with_chinese(self):
        text = plain(PLAIN, "rules list")
        self.assertIn("Keep the narrative short.", text)
        self.assertIn("把 PAD 里的词", text)
        self.assertIn("2 rule(s)", text)
        self.assertIn("no rules", plain(PLAIN, "rules list", {"version": 1, "rules": []}))

    def test_audit(self):
        self.assertIn("Audit passed", plain(PLAIN, "audit weekly"))
        failed = plain(PLAIN, "audit weekly", RESULTS["audit weekly"] | {"passed": False, "broken": ["fb-1"]})
        self.assertIn("Audit failed", failed)
        self.assertIn("fb-1", failed)

    def test_checkpoint(self):
        text = plain(PLAIN, "checkpoint")
        self.assertIn("Committed", text)
        self.assertIn("abcdef1234", text)
        self.assertNotIn("abcdef12345", text)
        nothing = plain(PLAIN, "checkpoint", {"committed": False, "commit": None, "initialized": True, "pushed": True})
        self.assertIn("Nothing new to commit", nothing)
        self.assertIn("new repository initialised", nothing)

    def test_init_shows_banner_and_next_steps(self):
        text = plain(PLAIN, "init")
        self.assertIn(ui.TAGLINE, text)
        self.assertIn("Your data folder is ready", text)
        self.assertIn("export ETRACE_CONFIG=/data/trace/trace.toml", text)
        for step in ("etrace ai set", "etrace note", "etrace run daily"):
            self.assertIn(step, text)

    def test_import_view(self):
        text = plain(PLAIN, "import")
        for needle in ("Made your data folder", "Imported 3 entries", "2025-03-04 to 2025-03-05", "1 skipped",
                       "loose.txt", "etrace import '/data/my diary' --run"):
            self.assertIn(needle, text)
        dry = plain(PLAIN, "import", dict(RESULTS["import"], dry_run=True, created_folder=None))
        self.assertIn("Would import 3 entries", dry)
        self.assertIn("Next", dry)
        none = plain(PLAIN, "import", dict(RESULTS["import"], entries=0, days=0, first=None, last=None,
                                           skipped=[], created_folder=None))
        self.assertIn("Nothing to import", none)
        many = dict(RESULTS["import"], skipped=[{"path": f"f{i}.txt", "reason": "empty"} for i in range(12)])
        self.assertIn("4 more", plain(PLAIN, "import", many))
        ran = dict(RESULTS["import"], ran=[{"date": "2025-03-05", "finished": True, "steps": 6},
                                           {"date": "2025-03-06", "finished": False, "steps": 2, "error": "boom"}])
        text = plain(PLAIN, "import", ran)
        self.assertIn("2025-03-05  the AI finished", text)
        self.assertIn("stopped", text)
        self.assertIn("boom", text)

    def test_import_next_steps_follow_what_the_user_has_done(self):
        base = dict(RESULTS["import"], created_folder=None)
        text = plain(PLAIN, "import", base)
        self.assertIn("etrace ai set", text)
        self.assertIn("etrace import '/data/my diary' --run", text)        # the path as typed, quoted
        ready = plain(PLAIN, "import", dict(base, ai_ready=True))
        self.assertNotIn("etrace ai set", ready)
        self.assertIn("etrace import '/data/my diary' --run", ready)
        self.assertIn("etrace run daily --date 2025-03-05", ready)
        dry = plain(PLAIN, "import", dict(base, dry_run=True))
        self.assertIn("etrace import '/data/my diary'", dry)
        self.assertIn("do it for real", dry)
        ran = plain(PLAIN, "import", dict(base, ran=[{"date": "2025-03-05", "finished": True, "steps": 3}]))
        self.assertNotIn("Next", ran)
        narrow = ui.strip_ansi(render.render(ui.Style(width=40), "import", dict(base, ai_ready=True)))
        self.assertIn("etrace import '/data/my diary' --run", narrow)   # a command is never cut

    def test_import_lists_the_days(self):
        rows = [{"date": f"2025-03-{d:02d}", "source": f"2025-03-{d:02d}.md", "chars": 40 + d, "created": True,
                 "status": "already there" if d == 2 else "added"} for d in range(1, 15)]
        text = plain(PLAIN, "import", dict(RESULTS["import"], imported=rows, entries=14, days=14))
        self.assertIn("+ 2025-03-01  2025-03-01.md", text)
        self.assertIn("= 2025-03-02", text)
        self.assertIn("41 chars", text)
        self.assertIn("and 2 more", text)
        self.assertNotIn("2025-03-14", text)

    def test_run_view_does_not_repeat_streamed_steps(self):
        shown = ui.strip_ansi(render.render(PLAIN, "run", RESULTS["run"], streamed=True))
        self.assertNotIn("etrace note today", shown)          # it was printed while it ran
        self.assertIn("etrace validate", shown)               # a failure is repeated
        self.assertIn("etrace bad", shown)
        self.assertIn("The AI finished", shown)
        self.assertIn("etrace note today", plain(PLAIN, "run"))   # not streamed: the full list
        clean = dict(RESULTS["run"], steps=[{"call": "etrace rules list", "exit_code": 0}])
        self.assertNotIn("etrace rules list", ui.strip_ansi(render.render(PLAIN, "run", clean, streamed=True)))

    def test_progress_day_separator(self):
        line = ui.strip_ansi(render.progress_line(PLAIN, {"day": "2025-03-05"}))
        self.assertIn("2025-03-05", line)
        self.assertIn("2025-03-05", render.progress_line(ASCII, {"day": "2025-03-05"}))
        self.assertTrue(render.progress_line(ASCII, {"day": "2025-03-05"}).isascii())

    def test_ai_views(self):
        self.assertIn("etrace ai set openai", plain(PLAIN, "ai", {"provider": None}))
        shown = plain(PLAIN, "ai")
        self.assertIn("…0123", shown)
        self.assertIn("deepseek-chat", shown)
        lost = dict(RESULTS["ai"], key={"needed": True, "found": False, "masked": None, "source": None,
                                        "problem": "no key", "exposed": True})
        self.assertIn("no key", plain(PLAIN, "ai", lost))
        self.assertIn("chmod 600", plain(PLAIN, "ai", lost))
        local = dict(RESULTS["ai"], model=None, key={"needed": False, "found": False, "problem": None})
        self.assertIn("none needed", plain(PLAIN, "ai", local))
        self.assertIn("Tool calling works", plain(PLAIN, "ai set"))
        self.assertIn("needs tool calling", plain(PLAIN, "ai test"))
        failed = plain(PLAIN, "ai test", {"ok": False, "error": "HTTP 401"})
        self.assertIn("HTTP 401", failed)
        self.assertIn("more", plain(PLAIN, "ai models", {"count": 45, "models": [f"m{i}" for i in range(45)]}))

    def test_run_view_and_progress(self):
        text = plain(PLAIN, "run")
        self.assertIn("The AI finished", text)
        self.assertIn("4 step(s), 2 failed", text)
        self.assertIn("Second paragraph.", text)
        self.assertIn("1,234 in", text)
        self.assertIn("did not finish", plain(PLAIN, "run", dict(RESULTS["run"], finished=False, final="")))
        many = dict(RESULTS["run"], steps=[{"call": f"etrace x{i}", "exit_code": 0} for i in range(20)])
        self.assertIn("8 earlier step(s)", plain(PLAIN, "run", many))
        for step in RESULTS["run"]["steps"]:
            self.assertTrue(ui.strip_ansi(render.progress_line(PLAIN, step)).strip())

    def test_generic_view_handles_any_shape(self):
        result = {"note": "/x/y.md", "ok": True, "n": 3, "ratio": 0.5, "nothing": None,
                  "ids": ["a", "b"], "items": [{"a": 1}], "nested": {"k": "v", "deep": {"z": 1}}}
        text = ui.strip_ansi(render.render(PLAIN, "note new", result))
        for needle in ("note", "/x/y.md", "✔", "0.5", "—", "a, b", "1 item(s)", "nested", "k"):
            self.assertIn(needle, text)
        self.assertIsInstance(render.render(PLAIN, "weird", {}), str)
        self.assertIn("plain", ui.strip_ansi(render.render(PLAIN, "x", "plain")))

    def test_a_view_that_cannot_draw_returns_none(self):
        self.assertIsNone(render.render(PLAIN, "validate", {"unexpected": 1}))
        self.assertIsNone(render.render(PLAIN, "g report", {"B": "not a number"}))

    def test_help_screen_lists_every_command_once(self):
        groups = (("One", ("a", "b")), ("Two", ("c",)))
        commands = {"a": "do a", "b": "do b", "c": "do c"}
        text = ui.strip_ansi(render.help_screen(PLAIN, groups, commands))
        for name, helptext in commands.items():
            self.assertIn(helptext, text)
        for opt in ("--json", "--pretty", "--no-color", "--config", "--version"):
            self.assertIn(opt, text)
        self.assertIn(__version__, text)
        self.assertNotIn("\n" + ui.LOGO[0], text)  # plain (no colour) help uses the one-line banner
        big = ui.strip_ansi(render.help_screen(COLOR, groups, commands))
        self.assertIn(ui.LOGO[0], big)

    def test_home_screen(self):
        none = ui.strip_ansi(render.home_screen(PLAIN, None))
        self.assertIn("No data folder found yet", none)
        self.assertIn("etrace ai set", none)
        self.assertIn("etrace import ~/path/to/diary --run", none)
        status = {"root": "/d", "preset": "default", "language": "en", "timezone": "America/Chicago",
                  "note": "/d/journal/x.md", "note_exists": True}
        text = ui.strip_ansi(render.home_screen(PLAIN, status))
        self.assertIn("/d/journal/x.md", text)
        self.assertIn("America/Chicago", text)
        missing = ui.strip_ansi(render.home_screen(PLAIN, dict(status, note_exists=False)))
        self.assertIn("not created yet", missing)

    def test_home_screen_never_cuts_a_command_on_a_narrow_terminal(self):
        text = ui.strip_ansi(render.home_screen(ui.Style(width=40), None))
        self.assertIn("etrace import ~/path/to/diary --run", text)
        self.assertNotIn("…", text)


if __name__ == "__main__":
    unittest.main()
