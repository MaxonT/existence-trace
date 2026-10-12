"""`etrace import`: reading a diary from disk (importer.py) and bringing it in (cli.py)."""
import datetime
import json
import os
import tempfile
import unittest
from zoneinfo import ZoneInfo

from etrace import agent, importer, journal
from tests.test_cli_ai import AICliTest

TZ = ZoneInfo("America/Chicago")
D = datetime.date


def slurp(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(root, name, text, mode="w"):
    path = os.path.join(root, *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if mode == "w":
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    else:
        with open(path, mode) as fh:
            fh.write(text)
    return path


class FindDateTest(unittest.TestCase):
    def test_the_usual_ways_to_write_a_date(self):
        for text in ("2026-03-04", "diary 2026.3.4", "20260304", "2026年3月4日", "2026/03/04", "2026_03_04 x"):
            self.assertEqual(importer.find_date(text), D(2026, 3, 4), text)

    def test_things_that_are_not_dates(self):
        for text in ("2026-13-45", "12345678", "version 1.2.3", "2026-02-30", "no date", ""):
            self.assertIsNone(importer.find_date(text), text)

    def test_date_lines(self):
        for line in ("## 2026-03-04", "**2026-03-04**", "2026-03-04", "2026-03-04 Wednesday", "2026年3月4日 星期三",
                     "# 2026-03-04: a long headline about the day"):
            self.assertEqual(importer._date_line(line), D(2026, 3, 4), line)
        for line in ("2026-03-04 Dentist at nine", "I wrote this on 2026-03-04", "", "plain text", "x" * 80):
            self.assertIsNone(importer._date_line(line), line)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def scan(self, *paths, **kw):
        return importer.scan([os.path.join(self.root, p) if not os.path.isabs(p) else p for p in paths] or [self.root],
                             TZ, **kw)

    def test_one_day_per_file_from_name_folder_front_matter_and_first_line(self):
        write(self.root, "2026-03-04.md", "named\n")
        write(self.root, "2025/07/09.txt", "foldered\n")
        write(self.root, "misc/a.md", "---\ndate: 2024-01-02\ntags: [x]\n---\nfront matter\n")
        write(self.root, "misc/b.md", "# 2023年5月6日\nfirst line\n")
        entries, skipped = importer.scan([self.root], TZ)
        self.assertEqual(skipped, [])
        got = {e["date"]: e["text"] for e in entries}
        self.assertEqual(got[D(2026, 3, 4)], "named")
        self.assertEqual(got[D(2025, 7, 9)], "foldered")
        self.assertEqual(got[D(2024, 1, 2)], "front matter")
        self.assertEqual(got[D(2023, 5, 6)], "# 2023年5月6日\nfirst line")
        self.assertEqual([e["date"] for e in entries], sorted(got))

    def test_many_days_in_one_file(self):
        write(self.root, "log.md", "intro line\n\n# 2026-04-01 Tuesday\nShipped.\n\n## 2026-04-02\nTired.\nStill tired.\n"
                                   "\n**2026-04-03**\n\n2026-04-04\nBetter.\n")
        entries, skipped = importer.scan([self.root], TZ)
        self.assertEqual([(e["date"].day, e["text"]) for e in entries],
                         [(1, "Shipped."), (2, "Tired.\nStill tired."), (4, "Better.")])
        reasons = " ".join(s["reason"] for s in skipped)
        self.assertIn("empty day", reasons)
        self.assertIn("above the first date heading", reasons)

    def test_day_one_export_uses_each_entrys_own_time_zone(self):
        data = {"entries": [
            {"creationDate": "2025-05-02T03:30:00Z", "timeZone": "America/Chicago", "text": "late\\. night"},
            {"creationDate": "2025-05-02T03:30:00Z", "timeZone": "Asia/Tokyo", "text": "morning"},
            {"creationDate": "2025-05-02T03:30:00Z", "text": ""},
        ]}
        write(self.root, "Journal.json", json.dumps(data))
        entries, skipped = importer.scan([self.root], TZ)
        self.assertEqual([(str(e["date"]), e["text"]) for e in entries],
                         [("2025-05-01", "late. night"), ("2025-05-02", "morning")])
        self.assertEqual(len(skipped), 1)

    def test_undated_files_are_skipped_unless_told_otherwise(self):
        path = write(self.root, "loose.txt", "no date here\n")
        entries, skipped = importer.scan([self.root], TZ)
        self.assertEqual(entries, [])
        self.assertIn("--date", skipped[0]["reason"])
        entries, _ = importer.scan([path], TZ, default_date=D(2025, 1, 1))
        self.assertEqual(entries[0]["date"], D(2025, 1, 1))
        os.utime(path, (1_700_000_000, 1_700_000_000))   # 2023-11-14 in Chicago
        entries, _ = importer.scan([self.root], TZ, use_mtime=True)
        self.assertEqual(entries[0]["date"], D(2023, 11, 14))

    def test_junk_is_left_alone(self):
        write(self.root, ".hidden/2026-01-01.md", "hidden\n")
        write(self.root, ".git/2026-01-02.md", "git\n")
        write(self.root, "node_modules/2026-01-03.md", "deps\n")
        write(self.root, "photo-2026-01-04.jpg", "binary")
        write(self.root, "2026-01-05.docx", "word")
        write(self.root, "2026-01-06.md", "   \n")
        write(self.root, "2026-01-07.md", b"\xff\xfe\x00bad", mode="wb")
        write(self.root, "2026-01-08.md", "kept\r\nwith windows endings\r\n")
        entries, skipped = importer.scan([self.root], TZ)
        self.assertEqual([e["text"] for e in entries], ["kept\nwith windows endings"])
        self.assertEqual(sorted(s["path"] for s in skipped), ["2026-01-05.docx", "2026-01-06.md", "2026-01-07.md"])

    def test_a_file_named_directly_that_cannot_be_read_says_so(self):
        path = write(self.root, "notes.pdf", "x")
        _, skipped = importer.scan([path], TZ)
        self.assertIn(".pdf", skipped[0]["reason"])

    def test_bom_is_dropped(self):
        write(self.root, "2026-02-01.md", b"\xef\xbb\xbfhello", mode="wb")
        entries, _ = importer.scan([self.root], TZ)
        self.assertEqual(entries[0]["text"], "hello")


class ImportCommandTest(AICliTest):
    def setUp(self):
        super().setUp()
        self.diary = os.path.join(self.tmp, "diary")
        write(self.diary, "2025-03-04.md", "Went running, felt light.\n")
        write(self.diary, "2025-03-05.txt", "Rainy.\n")
        write(self.diary, "months/april.md", "# 2025-04-01\nShipped.\n\n## 2025-04-02\nTired.\n")

    def note(self, day):
        return os.path.join(self.root, "journal", f"{day}.md")

    def verbatim(self, day):
        return journal.load(self.note(day)).block("verbatim").content().strip()

    def test_imports_every_day_into_the_protected_original_block(self):
        code, out, err = self.js("import", self.diary)
        self.assertEqual(code, 0, err)
        self.assertEqual((out["entries"], out["days"], out["added"], out["new_notes"]), (4, 4, 4, 4))
        self.assertEqual((out["first"], out["last"]), ("2025-03-04", "2025-04-02"))
        self.assertEqual(self.verbatim("2025-03-04"), "Went running, felt light.")
        self.assertEqual(self.verbatim("2025-04-02"), "Tired.")
        self.assertEqual(self.js("validate")[0], 0)

    def test_importing_twice_changes_nothing(self):
        self.js("import", self.diary)
        before = {d: slurp(self.note(d)) for d in ("2025-03-04", "2025-04-01")}
        code, out, _ = self.js("import", self.diary)
        self.assertEqual(code, 0)
        self.assertEqual((out["added"], out["unchanged"], out["new_notes"]), (0, 4, 0))
        for d, text in before.items():
            self.assertEqual(slurp(self.note(d)), text)

    def test_a_day_that_already_has_a_note_keeps_everything_and_gets_a_dated_addition(self):
        self.js("note", "new", "2025-03-04")
        code, out, _ = self.js("import", self.diary)
        self.assertEqual(code, 0)
        self.assertEqual(out["new_notes"], 3)
        write(self.diary, "2025-03-04.md", "Went running, felt light.\n\nLater: it rained.\n")
        self.assertEqual(self.js("import", self.diary)[0], 0)
        text = self.verbatim("2025-03-04")
        self.assertIn("Went running, felt light.", text)
        self.assertIn("Added on", text)
        self.assertIn("happened on 2025-03-04", text)
        self.assertIn("Later: it rained.", text)
        self.assertEqual(self.js("validate")[0], 0)

    def test_dry_run_changes_nothing(self):
        code, out, _ = self.js("import", self.diary, "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["entries"], 4)
        self.assertEqual({r["status"] for r in out["imported"]}, {"would add"})
        self.assertFalse(os.path.exists(self.note("2025-03-04")))

    def test_future_days_and_marker_comments_are_skipped_not_fatal(self):
        write(self.diary, "2099-01-01.md", "from the future\n")
        write(self.diary, "2025-06-01.md", "<!-- trace:user:journal -->\n")
        code, out, _ = self.js("import", self.diary)
        self.assertEqual(code, 0)
        reasons = {s["path"]: s["reason"] for s in out["skipped"]}
        self.assertIn("future", reasons["2099-01-01.md (2099-01-01)"])
        self.assertIn("marker", reasons["2025-06-01.md (2025-06-01)"])
        self.assertFalse(os.path.exists(self.note("2025-06-01")))

    def test_refuses_its_own_journal(self):
        for path in (os.path.join(self.root, "journal"), self.root):
            code, _, err = self.js("import", path)
            self.assertEqual(code, 2)
            self.assertIn("own journal", err)

    def test_missing_path_is_an_input_error(self):
        code, _, err = self.js("import", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_no_path_is_a_usage_error(self):
        self.assertEqual(self.js("import")[0], 2)

    def test_nothing_found_says_so(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, out, _ = self.js("import", empty)
        self.assertEqual((code, out["entries"]), (0, 0))
        code, text, _ = self.cli("--pretty", "--no-color", "import", empty)
        self.assertIn("Nothing to import", text)

    def test_pretty_view(self):
        write(self.diary, "loose.txt", "undated\n")
        code, text, _ = self.cli("--pretty", "--no-color", "import", self.diary)
        self.assertEqual(code, 0)
        for needle in ("Imported 4 entries", "2025-03-04 to 2025-04-02", "1 skipped", "loose.txt",
                       "Original version", "etrace import PATH --run"):
            self.assertIn(needle, text)

    def test_one_command_from_nothing(self):
        """A fresh machine: no data folder yet, `etrace import PATH` makes one in the right language."""
        zh = os.path.join(self.tmp, "zh")
        write(zh, "2025-03-04.md", "今天去跑步，感觉很轻松。我想把这些记下来。\n")
        code, out, err = self.js("import", zh, config=False)
        self.assertEqual(code, 0, err)
        folder = os.path.join(self.tmp, "existence-trace-data")
        self.assertEqual(os.path.realpath(out["created_folder"]), os.path.realpath(folder))
        with open(os.path.join(folder, "trace.toml"), encoding="utf-8") as fh:
            self.assertIn('language = "zh"', fh.read())
        self.assertTrue(os.path.exists(os.path.join(folder, "journal", "2025-03-04.md")))
        code, out, _ = self.js("import", self.diary, config=False)         # now it finds that folder
        self.assertEqual(code, 0)
        self.assertNotIn("created_folder", out)

    def test_one_command_from_nothing_in_english(self):
        code, out, err = self.js("import", self.diary, config=False)
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.tmp, "existence-trace-data", "trace.toml"), encoding="utf-8") as fh:
            self.assertIn('language = "en"', fh.read())

    def test_dry_run_without_a_data_folder_creates_nothing(self):
        code, _, err = self.js("import", self.diary, "--dry-run", config=False)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "existence-trace-data")))
        self.assertIn("etrace init", err)

    def test_ai_set_on_a_fresh_machine_makes_the_data_folder_first(self):
        env = {"LANG": "zh_CN.UTF-8", "LC_ALL": "", "LC_MESSAGES": ""}
        code, out, err = self.js("ai", "set", "custom", "--url", self.api.url, "--model", "alpha-1",
                                 "--key-stdin", "--no-test", stdin="sk-abc-123456\n", config=False, env=env)
        self.assertEqual(code, 0, err)
        folder = os.path.join(self.tmp, "existence-trace-data")
        self.assertEqual(os.path.realpath(out["created_folder"]), os.path.realpath(folder))
        self.assertEqual(out["model"], "alpha-1")
        with open(os.path.join(folder, "trace.toml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('language = "zh"', text)
        self.assertNotIn("sk-abc", text)
        code, text, _ = self.cli("--pretty", "--no-color", "ai", "set", "custom", "--url", self.api.url,
                                 "--model", "alpha-1", "--no-test", config=False)
        self.assertEqual(code, 0)
        self.assertNotIn("Made your data folder", text)          # it exists now

    def test_the_ai_cannot_import(self):
        self.assertIn("import", agent.BLOCKED)


class ImportRunTest(AICliTest):
    def setUp(self):
        super().setUp()
        self.diary = os.path.join(self.tmp, "diary")
        write(self.diary, "2025-03-04.md", "One.\n")
        write(self.diary, "2025-03-05.md", "Two.\n")
        write(self.diary, "2025-03-06.md", "Three.\n")

    def test_run_needs_an_ai_and_touches_nothing_without_one(self):
        code, _, err = self.js("import", self.diary, "--run")
        self.assertEqual(code, 2)
        self.assertIn("etrace ai set", err)
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "2025-03-04.md")))

    def test_run_lets_the_ai_read_the_latest_days_one_by_one(self):
        self.setup_ai("--no-test")
        for _ in range(2):
            self.api.call(("etrace", {"args": ["rules", "list"]}))
            self.api.say("Done.")
        code, out, err = self.js("import", self.diary, "--run", "--days", "2")
        self.assertEqual(code, 0, err)
        self.assertEqual([r["date"] for r in out["ran"]], ["2025-03-05", "2025-03-06"])
        self.assertTrue(all(r["finished"] for r in out["ran"]))
        prompts_sent = [c["body"]["messages"][0]["content"] for c in self.api.chats]
        self.assertTrue(any("2025-03-05" in p and "not today" in p for p in prompts_sent))
        self.assertTrue(any("2025-03-06" in p for p in prompts_sent))
        self.assertFalse(any("2025-03-04" in p for p in prompts_sent))

    def test_a_day_the_ai_cannot_finish_stops_with_exit_1(self):
        self.setup_ai("--no-test")
        self.api.required_key = "rotated"
        code, out, err = self.js("import", self.diary, "--run", "--days", "1")
        self.assertEqual(code, 1)
        self.assertEqual(out["added"], 3)                        # the import itself was done
        self.assertFalse(out["ran"][0]["finished"])
        self.assertIn("2025-03-06", err)

    def test_days_must_be_positive(self):
        self.assertEqual(self.js("import", self.diary, "--days", "0")[0], 2)


class PastDayPromptTest(AICliTest):
    def test_daily_and_g_can_point_at_a_past_day(self):
        for task in ("daily", "g"):
            code, text, err = self.cli("prompt", task, "--date", "2025-03-04", "--raw")
            self.assertEqual(code, 0, err)
            self.assertIn("2025-03-04", text)
            self.assertIn("not today", text)
        code, text, _ = self.cli("prompt", "daily", "--date", "2025-03-04", "--language", "zh", "--raw")
        self.assertIn("不是今天", text)

    def test_no_date_means_the_usual_prompt(self):
        _, text, _ = self.cli("prompt", "daily", "--raw")
        self.assertNotIn("not today", text)

    def test_other_tasks_cannot_take_a_date(self):
        code, _, err = self.cli("prompt", "calibrate", "--date", "2025-03-04", "--raw")
        self.assertEqual(code, 2)
        self.assertIn("only daily and g", err)
        self.assertEqual(self.js("prompt", "daily", "--date", "garbage")[0], 2)

    def test_run_date_reaches_the_model(self):
        self.setup_ai("--no-test")
        self.api.say("Done.")
        code, out, err = self.js("run", "daily", "--date", "2025-03-04")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["date"], "2025-03-04")
        body = self.api.chats[0]["body"]["messages"]
        self.assertIn("2025-03-04", body[0]["content"])
        self.assertIn("for the day 2025-03-04", body[1]["content"])
        code, _, err = self.js("run", "audit", "--date", "2025-03-04")
        self.assertEqual(code, 1)
        self.assertIn("only daily and g", err)


if __name__ == "__main__":
    unittest.main()
