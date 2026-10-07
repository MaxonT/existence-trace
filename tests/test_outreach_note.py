import datetime
import os
import tempfile
import unittest
from unittest import mock

from etrace import journal
from etrace.container import decisions, feedback, questions
from etrace.outreach import note
from etrace.outreach.note import OutreachError

TZ = datetime.timezone(datetime.timedelta(hours=-5))
NOW = datetime.datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
DAY = "2026-10-07"
LONG = "这是一段很长很长的原话，" * 20


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self._tmp.name, "data")
        self.journal = os.path.join(self._tmp.name, "journal")
        os.makedirs(self.data)
        os.makedirs(self.journal)

    def tearDown(self):
        self._tmp.cleanup()

    def ask(self, key="pad-evening", text="晚上那段我就是坐着发呆", date="2026-10-06"):
        return questions.add_question(
            self.data, "journal", date, {"kind": "quote", "text": text},
            ["茫然", "平静"], "晚上的状态是放空还是安静", ["pad.A"], key, now=NOW)

    def feedback_id(self, text="晚上那段算平静，不是茫然", date="2026-10-06"):
        feedback.ingest_note(self.data, date, [text], now=NOW)
        return [r for r in feedback.current(self.data) if r["text"] == text][0]["id"]

    def decide(self, text="把晚上独处评为平静", fid=None, now=NOW, reason="你说那是安静不是放空"):
        return decisions.decide(self.data, [fid or self.feedback_id()], text, "agent_decision", now=now,
                                rule_changes=[dict(rule_id=f"R-{text[-6:]}", scope=f"pad.{text[-6:]}",
                                                   text=text, expressed_at="2026-10-06T21:00:00-05:00",
                                                   reason=reason)])

    def write(self, date=DAY, scan_ok=True, **kw):
        kw.setdefault("now", NOW)
        return note.write(self.journal, self.data, date, scan_ok, **kw)

    def block(self, date=DAY):
        return journal.load(journal.note_path(self.journal, date)).block("ai:questions").content()

    def assertPhoneWidth(self, text):
        for line in text.splitlines():
            self.assertLessEqual(len(line), 120, line)


class QuestionsPartTest(Base):
    def test_numbered_questions_with_ids(self):
        q1 = self.ask()
        q2 = self.ask("g-b2", "下午一直在刷手机")
        r = self.write()
        body = self.block()
        self.assertIn(f"1. [{q1['id']}] 晚上的状态是放空还是安静", body)
        self.assertIn(f"2. [{q2['id']}]", body)
        self.assertIn("[Q-编号]", body)
        self.assertIn("「茫然」", body)
        self.assertEqual(r.notification["question_ids"], [q1["id"], q2["id"]])
        self.assertTrue(r.recorded)

    def test_follow_up_shown(self):
        q = self.ask()
        questions.follow_up(self.data, q["id"], "不急，有空再说", now=NOW)
        self.write()
        self.assertIn("再问一次：不急，有空再说", self.block())

    def test_no_questions_status(self):
        r = self.write()
        self.assertIn("目前没有新的重要疑问。", self.block())
        self.assertEqual(r.notification["daily_status"], questions.NO_QUESTIONS)

    def test_scan_failed_never_claims_no_questions(self):
        self.ask()
        r = self.write(scan_ok=False)
        body = self.block()
        self.assertIn("无法判断", body)
        self.assertNotIn("没有新的重要疑问", body)
        self.assertEqual(r.notification["daily_status"], questions.SCAN_FAILED)
        self.assertEqual(r.notification["question_ids"], [])

    def test_english(self):
        q = self.ask()
        self.write(language="en")
        body = self.block()
        self.assertIn(f"1. [{q['id']}]", body)
        self.assertIn("[Q-id]", body)
        self.assertIn("What the AI wants to ask you", journal.load(journal.note_path(self.journal, DAY))
                      .block("ai:questions").title())


class ReportTest(Base):
    def test_report_summary_what_why_feedback_invitation(self):
        d = self.decide()
        self.write()
        body = self.block()
        self.assertIn("### 决定报告", body)
        self.assertIn("我根据你的 1 条反馈做了 1 个决定，改动了 1 条规则。", body)
        self.assertIn("1. 我决定：把晚上独处评为平静", body)
        self.assertIn("为什么：你说那是安静不是放空", body)
        self.assertIn("参考了你的话：「晚上那段算平静，不是茫然」", body)
        self.assertTrue(body.rstrip().endswith("你随时可以推翻。"))
        self.assertLess(body.index("目前没有新的重要疑问"), body.index("### 决定报告"))
        self.assertEqual(note.read_all(self.data)[0]["decision_ids"], [d["id"]])

    def test_custom_summary_and_explicit_ids(self):
        d1 = self.decide()
        self.decide("中午算专注", self.feedback_id("中午其实很专注"))
        self.write(decision_ids=[d1["id"]], summary="今天只改了一处。")
        body = self.block()
        self.assertIn("今天只改了一处。", body)
        self.assertNotIn("中午算专注", body)

    def test_unknown_decision_rejected(self):
        with self.assertRaises(OutreachError):
            self.write(decision_ids=["D-20260101-9"])

    def test_no_decisions_no_report(self):
        self.write()
        self.assertNotIn("决定报告", self.block())

    def test_earlier_day_reports_not_repeated(self):
        self.decide(now=NOW - datetime.timedelta(days=1))
        self.write(date="2026-10-06")
        d2 = self.decide("中午算专注", self.feedback_id("中午其实很专注"))
        r = self.write()
        body = self.block()
        self.assertNotIn("把晚上独处评为平静", body)
        self.assertIn("中午算专注", body)
        self.assertEqual(r.notification["decision_ids"], [d2["id"]])

    def test_same_day_rerun_keeps_report(self):
        self.decide()
        self.write()
        self.decide("中午算专注", self.feedback_id("中午其实很专注"))
        r = self.write(now=NOW + datetime.timedelta(hours=3))
        body = self.block()
        self.assertIn("把晚上独处评为平静", body)
        self.assertIn("中午算专注", body)
        self.assertEqual(len(r.notification["decision_ids"]), 2)

    def test_english_report(self):
        self.decide()
        self.write(language="en")
        body = self.block()
        self.assertIn("### Decision report", body)
        self.assertIn("I decided: ", body)
        self.assertIn("Why: ", body)
        self.assertIn("Based on your words: \"", body)
        self.assertIn("Which of these do you agree with", body)


class NotificationTest(Base):
    def test_identical_rerun_records_nothing(self):
        self.ask()
        self.write()
        r = self.write(now=NOW + datetime.timedelta(hours=1))
        self.assertFalse(r.recorded)
        self.assertEqual(len(note.read_all(self.data)), 1)

    def test_changed_content_records_again(self):
        self.write()
        self.ask()
        r = self.write()
        self.assertTrue(r.recorded)
        self.assertEqual([n["id"] for n in note.read_all(self.data)], ["N-20261007-1", "N-20261007-2"])

    def test_record_fields(self):
        r = self.write()
        n = r.notification
        self.assertEqual((n["channel"], n["status"], n["date"]), ("note", "sent", DAY))
        self.assertTrue(n["verified_readback"])
        self.assertEqual(n["sent_at"], NOW.isoformat())

    def test_no_record_when_readback_fails(self):
        real_save = journal.save
        journal.create_note(self.journal, DAY)
        # the write silently lands without our text
        with mock.patch.object(journal, "save", side_effect=lambda doc, path: real_save(journal.new_note(DAY), path)):
            with self.assertRaises(OutreachError):
                self.write()
        self.assertEqual(note.read_all(self.data), [])

    def test_protected_blocks_untouched(self):
        path = journal.create_note(self.journal, DAY)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("<!-- hint: 随便写。这里的文字 AI 不会改动。 -->", "今天的日记")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        before = journal.load(path).protected_hashes()
        self.ask()
        self.write()
        self.assertEqual(journal.load(path).protected_hashes(), before)

    def test_creates_note_when_missing(self):
        r = self.write(date="2026-10-08")
        self.assertTrue(os.path.exists(r.path))

    def test_bad_language_and_now(self):
        with self.assertRaises(OutreachError):
            self.write(language="fr")
        with self.assertRaises(OutreachError):
            self.write(now=datetime.datetime(2026, 10, 7))


class WidthTest(Base):
    def test_lines_fit_phone_width(self):
        for lang in ("zh", "en"):
            with self.subTest(lang=lang):
                self.ask(f"k-{lang}", LONG + " " + "word " * 60)
                fid = self.feedback_id(LONG + lang)
                self.decide(LONG[:30] + lang, fid, reason="a long reason " * 20)
                r = self.write(date=f"2026-10-0{8 if lang == 'zh' else 9}", language=lang)
                self.assertPhoneWidth(r.text)
                self.assertPhoneWidth(self.block(r.notification["date"]))


if __name__ == "__main__":
    unittest.main()
