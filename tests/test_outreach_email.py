import datetime
import email
import email.policy
import email.utils
import imaplib
import os
import smtplib
import tempfile
import unittest
from unittest import mock

from etrace import journal
from etrace.container import feedback, questions
from etrace.outreach import email as mail
from etrace.outreach.email import EmailError

TZ = datetime.timezone(datetime.timedelta(hours=-5))
NOW = datetime.datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
DAY = "2026-10-07"
BOX = "trace-bot@example.com"
USER = "alex@example.org"
SECRET = "pa55-not-real"

CREDS = f"""
address = "{BOX}"
password = "{SECRET}"
smtp_host = "smtp.example.com"
imap_host = "imap.example.com"
user_address = "{USER}"
"""


class FakeIMAP:
    """In-memory IMAP server: folders map name -> list of raw messages."""

    def __init__(self, server):
        self.server = server
        self.folder = None

    def login(self, user, password):
        if self.server.fail_login:
            raise imaplib.IMAP4.error(f"login failed for {user} with {password}")
        self.server.logins.append(user)
        return "OK", [b"logged in"]

    def append(self, folder, flags, date, raw):
        self.server.folders.setdefault(folder.strip('"'), []).append(raw)
        return "OK", [b"appended"]

    def select(self, folder, readonly=False):
        name = folder.strip('"')
        if name not in self.server.folders:
            return "NO", [b"no such folder"]
        self.folder = name
        return "OK", [str(len(self.server.folders[name])).encode()]

    def _msgs(self):
        return [email.message_from_bytes(r, policy=email.policy.default) for r in self.server.folders[self.folder]]

    def search(self, charset, *criteria):
        hits = []
        for n, msg in enumerate(self._msgs(), 1):
            if criteria[0] == "HEADER":
                ok = str(msg.get(criteria[1], "")) == criteria[2].strip('"')
            else:
                ok = criteria[1].strip('"') in str(msg.get("Subject", ""))
            if ok:
                hits.append(str(n).encode())
        return "OK", [b" ".join(hits)]

    def fetch(self, num, what):
        raw = self.server.folders[self.folder][int(num) - 1]
        return "OK", [(num + b" (RFC822 {%d}" % len(raw), raw), b")"]

    def logout(self):
        return "BYE", [b""]


class FakeSMTP:
    def __init__(self, server):
        self.server = server

    def starttls(self):
        self.server.tls = True

    def login(self, user, password):
        if self.server.smtp_error:
            raise self.server.smtp_error

    def send_message(self, msg):
        self.server.outbox.append(msg)
        if self.server.auto_save:
            self.server.folders["Sent"].append(msg.as_bytes())

    def quit(self):
        pass


class Server:
    def __init__(self):
        self.folders = {"INBOX": [], "Sent": []}
        self.outbox = []
        self.logins = []
        self.auto_save = True
        self.fail_login = False
        self.smtp_error = None
        self.tls = False


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self._tmp.name, "data")
        os.makedirs(self.data)
        self.server = Server()
        patches = [
            mock.patch.object(mail.imaplib, "IMAP4_SSL", lambda host, port: FakeIMAP(self.server)),
            mock.patch.object(mail.smtplib, "SMTP_SSL", lambda host, port: FakeSMTP(self.server)),
            mock.patch.object(mail.smtplib, "SMTP", lambda host, port: FakeSMTP(self.server)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.creds = self.write_creds(CREDS)

    def tearDown(self):
        self._tmp.cleanup()

    def write_creds(self, text):
        path = os.path.join(self._tmp.name, "email.toml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return mail.load_credentials(path)

    def ask(self, key="pad-evening", text="晚上那段我就是坐着发呆"):
        return questions.add_question(
            self.data, "journal", "2026-10-06", {"kind": "quote", "text": text},
            ["茫然", "平静"], "晚上的状态是放空还是安静", ["pad.A"], key, now=NOW)["id"]

    def send(self, **kw):
        kw.setdefault("now", NOW)
        return mail.send(self.data, self.creds, DAY, True, **kw)

    def reply(self, body, subject=None, sender=USER, to=BOX, date=None, mid=None, sent=None):
        sent = sent or self.server.outbox[-1]
        msg = email.message.EmailMessage()
        msg["Subject"] = subject or "Re: " + str(sent["Subject"])
        msg["From"] = sender
        msg["To"] = to
        msg["Date"] = email.utils.format_datetime(date or NOW + datetime.timedelta(hours=2))
        msg["Message-ID"] = mid or email.utils.make_msgid("reply", domain="example.org")
        msg["In-Reply-To"] = str(sent["Message-ID"])
        msg.set_content(body)
        self.server.folders["INBOX"].append(msg.as_bytes())
        return str(msg["Message-ID"])

    def notifications(self):
        return mail.email_records(self.data)


class CredentialsTest(Base):
    def test_defaults(self):
        c = self.creds
        self.assertEqual((c.smtp_port, c.imap_port, c.smtp_security), (465, 993, "ssl"))
        self.assertEqual((c.username, c.user_address, c.sent_folder, c.inbox), (BOX, USER, "Sent", "INBOX"))

    def test_repr_hides_password(self):
        self.assertNotIn(SECRET, repr(self.creds))

    def test_missing_key_error_names_key_not_value(self):
        with self.assertRaises(EmailError) as cm:
            self.write_creds(f'address = "{BOX}"\npassword = "{SECRET}"\nsmtp_host = "s"\n')
        self.assertIn("imap_host", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))

    def test_unknown_key_and_bad_values(self):
        for extra in ('typo = 1', 'smtp_security = "plain"', 'imap_port = "993"', 'save_to_sent = "yes"'):
            with self.assertRaises(EmailError):
                self.write_creds(CREDS + extra + "\n")

    def test_bad_toml_and_missing_file(self):
        with self.assertRaises(EmailError) as cm:
            self.write_creds(f'password = "{SECRET}" oops')
        self.assertNotIn(SECRET, str(cm.exception))
        with self.assertRaises(EmailError):
            mail.load_credentials(os.path.join(self._tmp.name, "nope.toml"))
        with self.assertRaises(EmailError):
            mail.load_credentials(None)


class ComposeTest(Base):
    def test_body_has_questions_and_reply_instructions_only(self):
        qid = self.ask()
        subject, body, qids, dids = mail.compose(self.data, DAY, True)
        self.assertTrue(subject.startswith("[etrace 2026-10-07 questions]"))
        self.assertIn(qid, body)
        self.assertIn("[Q-编号]", body)
        self.assertEqual((qids, dids), ([qid], []))

    def test_never_carries_journal_text(self):
        jdir = os.path.join(self._tmp.name, "journal")
        doc = journal.new_note(DAY, "default", "zh")
        journal.append_verbatim(doc, "今天的私密日记全文，不该出现在邮件里")
        journal.save(doc, journal.note_path(jdir, DAY))
        self.ask()
        _, body, _, _ = mail.compose(self.data, DAY, True)
        self.assertNotIn("私密日记全文", body)

    def test_status_when_no_questions_and_english(self):
        subject, body, _, _ = mail.compose(self.data, DAY, True, language="en")
        self.assertIn("No new important questions", body)
        self.assertIn("Questions for today", subject)

    def test_unknown_kind(self):
        with self.assertRaises(EmailError):
            mail.compose(self.data, DAY, True, kind="spam")


class SendTest(Base):
    def test_sent_only_after_found_in_sent_folder(self):
        qid = self.ask()
        r = self.send()
        self.assertEqual(r.status, "sent")
        n = r.notification
        self.assertTrue(n["verified_readback"])
        self.assertEqual((n["channel"], n["kind"], n["date"], n["question_ids"]), ("email", "questions", DAY, [qid]))
        self.assertEqual(n["id"], "N-20261007-1")
        msg = self.server.outbox[0]
        self.assertEqual((msg["From"], msg["To"]), (BOX, USER))
        self.assertEqual(str(msg["Message-ID"]), n["message_id"])

    def test_dedupe_by_date_and_kind(self):
        self.ask()
        self.send()
        again = self.send()
        self.assertFalse(again.recorded)
        self.assertEqual(len(self.server.outbox), 1)
        self.assertEqual(len(self.notifications()), 1)
        other = self.send(kind="report")
        self.assertTrue(other.recorded)
        self.assertEqual(len(self.server.outbox), 2)

    def test_not_in_sent_folder_is_unknown(self):
        self.server.auto_save = False
        r = self.send()
        self.assertEqual(r.status, "unknown")
        self.assertFalse(r.notification["verified_readback"])
        self.assertIn("not found", r.notification["reason"])

    def test_save_to_sent_appends_then_verifies(self):
        self.server.auto_save = False
        self.creds = self.write_creds(CREDS + "save_to_sent = true\n")
        self.assertEqual(self.send().status, "sent")
        self.assertEqual(len(self.server.folders["Sent"]), 1)

    def test_smtp_failure_is_failed_and_scrubs_password(self):
        self.server.smtp_error = smtplib.SMTPAuthenticationError(535, f"bad password {SECRET}".encode())
        r = self.send()
        self.assertEqual(r.status, "failed")
        self.assertIn("SMTPAuthenticationError", r.notification["reason"])
        with open(os.path.join(self.data, "notifications.jsonl"), encoding="utf-8") as fh:
            self.assertNotIn(SECRET, fh.read())

    def test_imap_failure_is_unknown_and_scrubs_password(self):
        self.server.fail_login = True
        r = self.send()
        self.assertEqual(r.status, "unknown")
        self.assertNotIn(SECRET, r.notification["reason"])
        self.assertIn("***", r.notification["reason"])

    def test_retry_after_failure_sends_again(self):
        self.server.smtp_error = OSError("connection refused")
        self.assertEqual(self.send().status, "failed")
        self.server.smtp_error = None
        r = self.send()
        self.assertEqual(r.status, "sent")
        self.assertEqual(r.notification["id"], "N-20261007-2")

    def test_retry_after_unknown_finds_earlier_message_without_resending(self):
        self.server.auto_save = False
        first = self.send().notification
        self.server.folders["Sent"].append(self.server.outbox[0].as_bytes())  # it did arrive, late
        r = self.send()
        self.assertEqual(r.status, "sent")
        self.assertEqual(r.notification["message_id"], first["message_id"])
        self.assertEqual(len(self.server.outbox), 1)

    def test_starttls(self):
        self.creds = self.write_creds(CREDS + 'smtp_security = "starttls"\nsmtp_port = 587\n')
        self.assertEqual(self.send().status, "sent")
        self.assertTrue(self.server.tls)

    def test_note_notifications_unaffected(self):
        from etrace.outreach import note
        self.send()
        jdir = os.path.join(self._tmp.name, "journal")
        r = note.write(jdir, self.data, DAY, True, now=NOW)
        self.assertEqual(r.notification["id"], "N-20261007-2")


class ReplyTest(Base):
    def setUp(self):
        super().setUp()
        self.qid = self.ask()
        self.send()

    def read(self):
        return mail.read_replies(self.data, self.creds, now=NOW + datetime.timedelta(hours=3))

    def test_reply_stored_verbatim_with_question(self):
        mid = self.reply("那段算平静，不是茫然。\n")
        r = self.read()
        self.assertTrue(r.ok)
        [rec] = r.added
        self.assertEqual(rec["text"], "那段算平静，不是茫然。")
        self.assertEqual(rec["question_id"], self.qid)
        self.assertEqual(rec["source"], "email")
        self.assertEqual(rec["message_id"], mid)
        self.assertEqual(rec["refers_to_date"], DAY)
        self.assertEqual(rec["id"], mail.feedback_id(mid, self.qid))
        # usable as an answer
        questions.answer(self.data, self.qid, rec["id"], now=NOW + datetime.timedelta(hours=3))

    def test_quotes_and_signature_stripped(self):
        body = ("平静。\n\n-- \nAlex\n\nOn Wed, Oct 7, 2026 at 9:00 AM <trace-bot@example.com> wrote:\n"
                "> 晚上那段我就是坐着发呆\n")
        self.reply(body)
        self.assertEqual(self.read().added[0]["text"], "平静。")
        self.reply("可以\n> 旧的引用\n在 2026年10月7日 写道：\n更旧的内容\n", mid="<r2@example.org>")
        self.assertEqual(self.read().added[0]["text"], "可以")

    def test_split_by_question_tags(self):
        q2 = self.ask("g-b2", "下午一直在刷手机")
        self.server.outbox.clear()
        mail.send(self.data, self.creds, DAY, True, kind="report", now=NOW)
        self.reply(f"总体还行\n\n[{self.qid}] 平静\n\n[{q2}] 是逃避\n")
        added = {r["question_id"]: r["text"] for r in self.read().added}
        self.assertEqual(added, {None: "总体还行", self.qid: f"[{self.qid}] 平静", q2: f"[{q2}] 是逃避"})

    def test_dedupe_by_message_and_question(self):
        self.reply("平静")
        self.assertEqual(len(self.read().added), 1)
        again = self.read()
        self.assertEqual((again.added, again.unchanged), ([], 1))
        self.assertEqual(len(feedback.read_all(self.data)), 1)

    def test_filters(self):
        self.reply("wrong sender", sender="someone@example.net")
        self.reply("wrong recipient", to="other@example.net")
        self.reply("too early", date=NOW - datetime.timedelta(minutes=1))
        self.reply("no tag", subject="Re: [etrace hello]")
        self.reply("unknown tag", subject="Re: [etrace 2026-10-01 questions]")
        self.reply("> only quoted\n")
        r = self.read()
        self.assertEqual(r.added, [])
        reasons = [why for _, why in r.skipped]
        self.assertEqual(len(reasons), 6)
        self.assertTrue(any("before the question" in why for why in reasons))

    def test_own_question_in_inbox_is_ignored(self):
        self.server.folders["INBOX"].append(self.server.outbox[0].as_bytes())
        r = self.read()
        self.assertEqual((r.added, r.skipped), ([], []))

    def test_imap_failure_reported_without_password(self):
        self.reply("平静")
        self.server.fail_login = True
        r = self.read()
        self.assertFalse(r.ok)
        self.assertNotIn(SECRET, r.error)
        self.assertEqual(feedback.read_all(self.data), [])

    def test_nothing_sent_means_no_imap(self):
        empty = os.path.join(self._tmp.name, "empty")
        os.makedirs(empty)
        self.server.logins.clear()
        self.assertTrue(mail.read_replies(empty, self.creds, now=NOW).ok)
        self.assertEqual(self.server.logins, [])

    def test_note_intake_does_not_supersede_email_feedback(self):
        self.reply("平静")
        rec = self.read().added[0]
        feedback.ingest_note(self.data, DAY, ["笔记里的另一条反馈"], now=NOW)
        self.assertIn(rec["id"], {r["id"] for r in feedback.current(self.data)})


class StripTest(unittest.TestCase):
    def test_strip_reply(self):
        self.assertEqual(mail.strip_reply("ok\r\n\r\nSent from my phone\r\n"), "ok")
        self.assertEqual(mail.strip_reply("a\n-----Original Message-----\nb"), "a")
        self.assertEqual(mail.strip_reply("> x\n"), "")

    def test_split_single_question_takes_untagged_text(self):
        self.assertEqual(mail.split_reply("yes", ["Q-1"]), [("Q-1", "yes")])
        self.assertEqual(mail.split_reply("yes", ["Q-1", "Q-2"]), [(None, "yes")])


if __name__ == "__main__":
    unittest.main()
