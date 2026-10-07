import datetime
import os
import tempfile
import unittest

from etrace import journal
from etrace.container import feedback

TZ = datetime.timezone(datetime.timedelta(hours=-5))
NOW = datetime.datetime(2026, 10, 7, 12, 30, tzinfo=TZ)
LATER = datetime.datetime(2026, 10, 7, 20, 30, tzinfo=TZ)


class FeedbackIntakeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = os.path.join(self._tmp.name, "journal")
        self.data = os.path.join(self._tmp.name, "data")
        os.makedirs(self.journal)

    def tearDown(self):
        self._tmp.cleanup()

    def note(self, date, feedback_text=None, language="zh"):
        path = journal.note_path(self.journal, date)
        if not os.path.exists(path):
            journal.create_note(self.journal, date, language=language)
        if feedback_text is not None:
            # simulate the user editing their own block (not via journal.save)
            doc = journal.load(path)
            block = doc.block("user:feedback")
            block.body = block.title() + "\n" + feedback_text + "\n\n"
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(doc.render())
        return path

    def ingest(self, now=NOW):
        return feedback.ingest_notes(self.journal, self.data, now=now)

    def test_template_placeholder_never_ingested(self):
        self.note("2026-10-06")
        self.note("2026-10-07", language="en")
        result = self.ingest()
        self.assertEqual(result.added, [])
        self.assertTrue(result.ok)
        self.assertEqual(feedback.read_all(self.data), [])

    def test_hint_left_in_place_is_not_ingested(self):
        hint = "<!-- hint: 写给 AI 的话，空行分隔多条。 -->\n\n"
        self.note("2026-10-07", hint + "下午其实很平静")
        records = self.ingest().added
        self.assertEqual([r["text"] for r in records], ["下午其实很平静"])

    def test_entries_split_by_blank_line_and_stored_verbatim(self):
        self.note("2026-10-07", "第一条\n  第二行保留缩进  \n\n\n[Q-2026-10-07-1] 是专注，不是紧张\n\nthird")
        result = self.ingest()
        texts = [r["text"] for r in result.added]
        self.assertEqual(texts, ["第一条\n  第二行保留缩进  ", "[Q-2026-10-07-1] 是专注，不是紧张", "third"])
        first, answer, third = result.added
        self.assertEqual(answer["question_id"], "Q-2026-10-07-1")
        self.assertIsNone(first["question_id"])
        for r in result.added:
            self.assertEqual(r["refers_to_date"], "2026-10-07")
            self.assertEqual(r["observed_at"], NOW.isoformat())
            self.assertTrue(r["id"].startswith("2026-10-07:feedback:"))
            self.assertEqual(r["id"], feedback.entry_id("2026-10-07", r["text"]))
            self.assertIsNone(r["supersedes"])
            self.assertEqual(r["version"], 1)
        self.assertEqual(len({r["id"] for r in result.added}), 3)

    def test_rerun_does_not_duplicate(self):
        self.note("2026-10-07", "一\n\n二")
        self.assertEqual(len(self.ingest().added), 2)
        again = self.ingest(LATER)
        self.assertEqual(again.added, [])
        self.assertEqual(again.unchanged, 2)
        self.assertEqual(len(feedback.read_all(self.data)), 2)
        self.assertEqual(feedback.read_all(self.data)[0]["observed_at"], NOW.isoformat())

    def test_same_text_in_two_notes_is_two_entries(self):
        self.note("2026-10-06", "同一句话")
        self.note("2026-10-07", "同一句话")
        self.assertEqual(len(self.ingest().added), 2)

    def test_edited_entry_becomes_linked_new_version(self):
        self.note("2026-10-07", "早上 P 偏低\n\n晚上很开心")
        v1 = {r["text"]: r for r in self.ingest().added}
        self.note("2026-10-07", "早上 P 偏低，因为没睡好\n\n晚上很开心")
        result = self.ingest(LATER)
        self.assertEqual(len(result.added), 1)
        edited = result.added[0]
        self.assertEqual(edited["text"], "早上 P 偏低，因为没睡好")
        self.assertEqual(edited["supersedes"], v1["早上 P 偏低"]["id"])
        self.assertEqual(edited["version"], 2)
        self.assertEqual(edited["observed_at"], LATER.isoformat())
        # history is kept; current() shows only the latest version
        self.assertEqual(len(feedback.read_all(self.data)), 3)
        self.assertEqual(
            sorted(r["text"] for r in feedback.current(self.data)),
            sorted(["早上 P 偏低，因为没睡好", "晚上很开心"]),
        )
        # a second edit links to the second version
        self.note("2026-10-07", "早上 P 偏低，因为没睡好，也有点担心\n\n晚上很开心")
        third = self.ingest(LATER).added[0]
        self.assertEqual(third["supersedes"], edited["id"])
        self.assertEqual(third["version"], 3)

    def test_edited_answer_links_by_question_id(self):
        self.note("2026-10-07", "别的事\n\n[Q-7] 是专注")
        first = {r["question_id"]: r for r in self.ingest().added}
        self.note("2026-10-07", "[Q-7] 其实是紧张\n\n别的事")
        edited = self.ingest(LATER).added[0]
        self.assertEqual(edited["supersedes"], first["Q-7"]["id"])

    def test_new_unrelated_entry_is_not_an_edit(self):
        self.note("2026-10-07", "第一条")
        self.ingest()
        self.note("2026-10-07", "第一条\n\n完全无关的新反馈")
        added = self.ingest(LATER).added
        self.assertEqual(len(added), 1)
        self.assertIsNone(added[0]["supersedes"])

    def test_bad_note_reported_others_ingested(self):
        self.note("2026-10-07", "好的反馈")
        bad = os.path.join(self.journal, "2026-10-06.md")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("<!-- trace:user:feedback -->\nx\n<!-- trace:user:feedback -->\ny\n")
        with open(os.path.join(self.journal, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("not a dated note\n")
        result = self.ingest()
        self.assertFalse(result.ok)
        self.assertEqual([e[0] for e in result.errors], ["2026-10-06.md"])
        self.assertEqual([r["text"] for r in result.added], ["好的反馈"])

    def test_intake_never_changes_the_note(self):
        path = self.note("2026-10-07", "一\n\n二")
        with open(path, "rb") as fh:
            before = fh.read()
        self.ingest()
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_naive_now_rejected(self):
        with self.assertRaises(feedback.FeedbackError):
            feedback.ingest_notes(self.journal, self.data, now=datetime.datetime(2026, 10, 7))


if __name__ == "__main__":
    unittest.main()
