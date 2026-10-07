import datetime
import tempfile
import unittest

from etrace.container import feedback, questions
from etrace.container.questions import QuestionError

NOW = datetime.datetime(2026, 10, 7, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))


class QuestionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def fields(self, **over):
        f = dict(
            source="journal",
            date="2026-10-06",
            evidence={"kind": "quote", "text": "下午写代码很顺，但晚上有点空"},
            candidates=["整日基调是专注", "整日基调是茫然"],
            uncertainty="晚上的空落感是否压过了下午的专注",
            dimensions=["pad.P", "pad.A"],
            dedupe_key="2026-10-06:pad:tone-conflict",
        )
        f.update(over)
        return f

    def add(self, **over):
        return questions.add_question(self.dir, now=NOW, **self.fields(**over))

    def feedback_id(self, text="[Q-20261006-1] 更接近专注"):
        feedback.ingest_note(self.dir, "2026-10-06", [text], now=NOW)
        return feedback.current(self.dir)[-1]["id"]

    def test_add_stores_all_fields(self):
        q = self.add()
        self.assertEqual(q["id"], "Q-20261006-1")
        stored = questions.get(self.dir, q["id"])
        self.assertEqual(stored["status"], "open")
        self.assertEqual(stored["candidates"], ["整日基调是专注", "整日基调是茫然"])
        self.assertEqual(stored["evidence"]["kind"], "quote")
        self.assertEqual(stored["asked_at"], NOW.isoformat())
        self.assertEqual(feedback.question_id("[Q-20261006-1] ok"), q["id"])

    def test_missing_or_empty_field_raises(self):
        required = ["source", "date", "evidence", "candidates", "uncertainty", "dimensions", "dedupe_key"]
        for name in required:
            with self.subTest(missing=name):
                f = self.fields()
                del f[name]
                with self.assertRaises(TypeError):
                    questions.add_question(self.dir, now=NOW, **f)
            for bad in (None, "", "  ", [], {}):
                with self.subTest(field=name, value=bad):
                    with self.assertRaises(QuestionError):
                        self.add(**{name: bad})
        self.assertEqual(questions.read_all(self.dir), [])

    def test_evidence_and_candidates_validation(self):
        bad = [
            {"evidence": {"kind": "guess", "text": "x"}},
            {"evidence": {"kind": "behavior", "text": ""}},
            {"evidence": "just a string"},
            {"candidates": ["only one"]},
            {"candidates": ["a", "b", "c"]},
            {"candidates": ["same", " same "]},
            {"date": "2026-13-01"},
            {"dimensions": ["pad.P", ""]},
        ]
        for over in bad:
            with self.subTest(over=over):
                with self.assertRaises(QuestionError):
                    self.add(**over)
        q = self.add(evidence={"kind": "behavior", "text": "23:40 才写日记"})
        self.assertEqual(q["evidence"]["kind"], "behavior")

    def test_naive_now_rejected(self):
        with self.assertRaises(QuestionError):
            questions.add_question(self.dir, now=datetime.datetime(2026, 10, 7), **self.fields())

    def test_dedupe_against_open_and_answered(self):
        q = self.add()
        with self.assertRaises(QuestionError):
            self.add()
        questions.answer(self.dir, q["id"], self.feedback_id(), now=NOW)
        with self.assertRaises(QuestionError):
            self.add()
        self.assertEqual(len(questions.all_questions(self.dir)), 1)

    def test_skipped_key_can_be_asked_again(self):
        q = self.add()
        questions.skip(self.dir, q["id"], reason="no longer relevant", now=NOW)
        q2 = self.add()
        self.assertEqual(q2["id"], "Q-20261006-2")
        self.assertEqual(questions.get(self.dir, q["id"])["status"], "skipped")

    def test_follow_up_at_most_once_and_only_while_open(self):
        q = self.add()
        questions.follow_up(self.dir, q["id"], "想再确认一下昨晚的感觉", now=NOW)
        self.assertEqual(questions.get(self.dir, q["id"])["follow_up"]["text"], "想再确认一下昨晚的感觉")
        with self.assertRaises(QuestionError):
            questions.follow_up(self.dir, q["id"], "again", now=NOW)
        other = self.add(dedupe_key="k2")
        questions.skip(self.dir, other["id"], now=NOW)
        with self.assertRaises(QuestionError):
            questions.follow_up(self.dir, other["id"], "late", now=NOW)

    def test_answer_requires_stored_feedback_id(self):
        q = self.add()
        for bad in (None, ""):
            with self.assertRaises(QuestionError):
                questions.answer(self.dir, q["id"], bad, now=NOW)
        with self.assertRaises(QuestionError):
            questions.answer(self.dir, q["id"], "2026-10-06:feedback:000000000000", now=NOW)
        fid = self.feedback_id()
        done = questions.answer(self.dir, q["id"], fid, now=NOW)
        self.assertEqual(done["status"], "answered")
        self.assertEqual(done["feedback_id"], fid)

    def test_closed_questions_cannot_change(self):
        q = self.add()
        fid = self.feedback_id()
        questions.answer(self.dir, q["id"], fid, now=NOW)
        with self.assertRaises(QuestionError):
            questions.skip(self.dir, q["id"], now=NOW)
        with self.assertRaises(QuestionError):
            questions.answer(self.dir, q["id"], fid, now=NOW)
        with self.assertRaises(QuestionError):
            questions.skip(self.dir, "Q-19990101-1", now=NOW)

    def test_history_is_append_only(self):
        q = self.add()
        questions.follow_up(self.dir, q["id"], "ping", now=NOW)
        questions.skip(self.dir, q["id"], now=NOW)
        records = questions.read_all(self.dir)
        self.assertEqual([r["type"] for r in records], ["question", "follow_up", "skipped"])
        self.assertNotIn("status", records[0])

    def test_daily_status(self):
        self.assertEqual(questions.daily_status(self.dir, "2026-10-07", scan_ok=True),
                         "no new important questions")
        self.assertEqual(questions.daily_status(self.dir, "2026-10-07", scan_ok=False),
                         "scan failed, cannot judge")
        q = self.add()
        status = questions.daily_status(self.dir, "2026-10-07", scan_ok=True)
        self.assertEqual([x["id"] for x in status], [q["id"]])
        self.assertEqual(questions.daily_status(self.dir, "2026-10-07", scan_ok=False),
                         "scan failed, cannot judge")
        self.assertEqual(questions.daily_status(self.dir, "2026-10-05", scan_ok=True),
                         "no new important questions")
        questions.skip(self.dir, q["id"], now=NOW)
        self.assertEqual(questions.daily_status(self.dir, "2026-10-07", scan_ok=True),
                         "no new important questions")

    def test_scan_ok_must_be_true(self):
        self.assertEqual(questions.daily_status(self.dir, "2026-10-07", scan_ok=None),
                         "scan failed, cannot judge")


if __name__ == "__main__":
    unittest.main()
