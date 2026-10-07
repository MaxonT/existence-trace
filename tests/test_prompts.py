import contextlib
import datetime
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from etrace import cli, config, journal, prompts
from etrace.container import rules
from etrace.store import Store

EXPRESSED = "2026-01-01T09:00:00+00:00"
# The CLI command each task's agent JSON is meant for.
TASK_COMMANDS = {
    "calibrate": ["question add FILE", "outreach send"],
    "feedback": ["feedback ingest", "decide FILE", "outreach send"],
    "daily": ["pad add FILE", "xp score FILE", "note write", "usage add FILE"],
    "g": ["g predict FILE", "g report", "usage add FILE"],
    "audit": ["audit weekly"],
}


def _commands(text):
    """CLI commands in the steps section, in order."""
    steps = re.split(r"^## (?:Steps|步骤)$", text, flags=re.M)[1]
    return re.findall(r"`python3 -m etrace ([^`]+)`", steps)


class PromptFilesTest(unittest.TestCase):
    def test_every_language_has_every_file(self):
        for lang in prompts.LANGUAGES:
            for task in ("_principles",) + prompts.TASKS:
                with self.subTest(lang=lang, task=task):
                    self.assertTrue(os.path.isfile(os.path.join(prompts.PROMPTS_DIR, lang, f"{task}.md")))

    def test_principles_list_all_nine(self):
        for lang in prompts.LANGUAGES:
            text = prompts.template("_principles", lang)
            numbers = re.findall(r"^(\d)\. ", text, re.M)
            self.assertEqual(numbers, [str(i) for i in range(1, 10)], lang)

    def test_unknown_task_or_language(self):
        with self.assertRaises(prompts.PromptError):
            prompts.template("weekly", "en")
        with self.assertRaises(prompts.PromptError):
            prompts.template("daily", "fr")


class RenderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        self.store = Store(os.path.join(self.root, "data"))

    def cfg(self, **values):
        values.setdefault("data_dir", self.root)
        return config.from_dict(values)

    def seed(self, preset, lang):
        from etrace import presets
        for rule in presets.load(preset)["rules"]["rules"]:
            rules.add_rule(self.store, rule["id"], rule["scope"], presets.label(rule["text"], lang), EXPRESSED)

    def test_every_prompt_for_both_presets_and_languages(self):
        for preset in ("default", "tiger-method"):
            self._tmp.cleanup()
            os.makedirs(self.root)
            self.seed(preset, "zh")
            version = rules.version(self.store)
            ids = [r["id"] for r in rules.active_rules(self.store)]
            for lang in prompts.LANGUAGES:
                cfg = self.cfg(preset=preset, language=lang)
                for task in prompts.TASKS:
                    with self.subTest(preset=preset, lang=lang, task=task):
                        text = prompts.render(task, cfg, self.store)
                        self.assertLess(len(text), prompts.MAX_CHARS)
                        self.assertNotIn("/Users/", text)
                        self.assertNotIn(self.root, text)
                        self.assertIn(cfg.timezone, text)
                        self.assertIn(", ".join(cfg.tasks[task]), text)
                        self.assertIn(f"{version}", text)
                        self.assertIn(preset, text)
                        for rid in ids:
                            self.assertIn(f"`{rid}`", text)
                        # every placeholder was filled ($ETRACE_CONFIG is literal text)
                        self.assertEqual(re.findall(r"\$(?!ETRACE_CONFIG)\w+", text), [])
                        self.assertIn(prompts.template("_principles", lang).splitlines()[1], text)

                        cmds = _commands(text)
                        self.assertEqual(cmds[0], f'checkpoint "{task}: before"')
                        self.assertEqual(cmds[-2], f'checkpoint "{task}: after"')
                        self.assertEqual(cmds[-1], "validate")
                        joined = "\n".join(cmds)
                        for cmd in TASK_COMMANDS[task]:
                            self.assertIn(cmd, joined)
                        honest = {"zh": "没做成的事要直说", "en": "Say plainly what did not work"}[lang]
                        self.assertIn(honest, text)

    def test_values_come_from_trace_toml(self):
        cfg = self.cfg(timezone="Asia/Tokyo", tasks={"feedback": ["07:05", "13:40"], "audit": "Sat 08:00"},
                       outreach="note", language="en")
        self.assertIn("(07:05, 13:40, Asia/Tokyo)", prompts.render("feedback", cfg, self.store))
        self.assertIn("(Sat 08:00, Asia/Tokyo)", prompts.render("audit", cfg, self.store))
        self.assertIn("(no active rules yet)", prompts.render("daily", cfg, self.store))

    def test_language_override(self):
        cfg = self.cfg(language="en")
        self.assertIn("任务：g", prompts.render("g", cfg, self.store, language="zh"))

    def test_superseded_rules_are_not_listed(self):
        rules.add_rule(self.store, "old", "pad:focus", "old focus rule", EXPRESSED)
        rules.add_rule(self.store, "new", "pad:focus", "new focus rule", "2026-02-01T09:00:00+00:00")
        text = prompts.render("daily", self.cfg(language="en"), self.store)
        self.assertIn("new focus rule", text)
        self.assertNotIn("old focus rule", text)

    def test_many_rules_stay_under_limit(self):
        for i in range(40):
            rules.add_rule(self.store, f"r{i:02d}", f"scope:{i:02d}", "long rule text " * 20, EXPRESSED)
        for lang in prompts.LANGUAGES:
            for task in prompts.TASKS:
                text = prompts.render(task, self.cfg(language=lang), self.store)
                self.assertLess(len(text), prompts.MAX_CHARS)
                self.assertIn("rules list", text)
                self.assertRegex(text, r"另有 \d+ 条|\d+ more active rules")


class CliPromptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "trace-data")
        self.config = os.path.join(self.root, "trace.toml")
        env = mock.patch.dict(os.environ, {cli.ENV_CONFIG: ""})
        env.start()
        self.addCleanup(env.stop)
        code, _, _ = self.run_cli("init", "--preset", "default", "--dir", self.root,
                                  "--language", "zh", config=False)
        self.assertEqual(code, 0)

    def run_cli(self, *argv, config=True, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        args = (["--config", self.config] if config else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin or "")):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_prompt_json(self):
        code, out, _ = self.run_cli("prompt", "calibrate")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertEqual((obj["task"], obj["language"]), ("calibrate", "zh"))
        self.assertEqual(obj["chars"], len(obj["prompt"]))
        self.assertIn("base-1-verbatim", obj["prompt"])
        self.assertNotIn(self.root, obj["prompt"])

    def test_prompt_raw_and_language(self):
        code, out, _ = self.run_cli("prompt", "audit", "--language", "en", "--raw")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Task: audit (Sun 22:15, America/Chicago)"))

    def test_prompt_unknown_task(self):
        code, out, err = self.run_cli("prompt", "weekly")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_note_write(self):
        today = datetime.datetime.now(cli.ZoneInfo("America/Chicago")).date().isoformat()
        note_file = os.path.join(self.root, "journal", f"{today}.md")
        before = journal.protected_hashes(journal.load(note_file))
        code, out, err = self.run_cli("note", "write", "today", "headline", "-", stdin="一条清楚的主线\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["block"], "ai:headline")
        doc = journal.load(note_file)
        self.assertIn("一条清楚的主线", doc.block("ai:headline").content())
        self.assertEqual(journal.protected_hashes(doc), before)

    def test_note_write_rejects_engine_blocks_and_missing_notes(self):
        self.assertEqual(self.run_cli("note", "write", "today", "pad", "-", stdin="x")[0], 2)
        code, _, err = self.run_cli("note", "write", "2001-01-01", "headline", "-", stdin="x")
        self.assertEqual(code, 2)
        self.assertIn("note new", err)


if __name__ == "__main__":
    unittest.main()
