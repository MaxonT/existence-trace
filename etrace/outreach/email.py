"""Email outreach: send questions by SMTP and read replies by IMAP.

Used when trace.toml says outreach = "note+email". Secrets live in the TOML
file at credentials.email, never in trace.toml; they are never logged and
never written into a record (error reasons are scrubbed of the password):

    address        the mailbox the system sends from and reads replies in
    password
    smtp_host      smtp_port (465)    smtp_security ("ssl" | "starttls")
    imap_host      imap_port (993)
    username       login name (default: address)
    user_address   where questions go and replies come from (default: address)
    sent_folder    IMAP folder holding sent mail (default: "Sent")
    inbox          IMAP folder holding replies (default: "INBOX")
    save_to_sent   append the sent message to sent_folder over IMAP, for
                   servers that do not do it themselves (default: false)

Sending (notifications.jsonl, channel 'email'): one message per date + kind.
A date + kind already marked 'sent' is not sent again. A message counts as
'sent' only after it is found in the Sent folder by its Message-ID; otherwise
the record says 'failed' (SMTP refused it) or 'unknown' (sent, but not seen
in the Sent folder) with a reason. A rerun after 'failed'/'unknown' first
looks for the earlier Message-ID in the Sent folder before sending again.

Replies (feedback.jsonl, source 'email'): a message counts only when it is
sent to the mailbox address by user_address, dated after the question email,
and its subject carries the tag '[etrace DATE KIND]'. Quoted older text and
signatures are stripped. The reply is split into entries by lines starting
with [Q-id]; text before the first tag answers the only question when the
email asked exactly one. Feedback id = 'email:MSGHASH12:QUESTION_ID' (or
':general'), so a message + question pair is stored once.

The body carries only what note.render produces (the questions, their
evidence and the decision report); journal text is never read here.

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime
import email
import email.policy
import email.utils
import hashlib
import imaplib
import re
import smtplib
import time
import tomllib
from email.message import EmailMessage

from etrace import journal
from etrace.container import feedback
from etrace.outreach import note
from etrace.store import InvalidRecord, Store

NAME = note.NAME
CHANNEL = "email"
KINDS = ("questions", "report", "audit")
SENT, FAILED, UNKNOWN = "sent", "failed", "unknown"
SECURITY = ("ssl", "starttls")
_TAG = re.compile(r"\[etrace (\d{4}-\d{2}-\d{2}) ([a-z]+)\]")
_QUESTION = re.compile(r"^\[(Q-[A-Za-z0-9_.:-]+)\]")
_QUOTE_HEADER = re.compile(
    r"^(On .+ wrote:\s*$|在.+写道[:：]\s*$|-{2,}\s*Original Message\s*-{2,}|-{2,}\s*原始邮件\s*-{2,}"
    r"|From: .+|发件人[:：].+)",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(r"^(-- ?|—— ?|Sent from my .+|发自我的.+)$")

_REQUIRED = ("address", "password", "smtp_host", "imap_host")
_DEFAULTS = {
    "smtp_port": 465,
    "smtp_security": "ssl",
    "imap_port": 993,
    "username": None,
    "user_address": None,
    "sent_folder": "Sent",
    "inbox": "INBOX",
    "save_to_sent": False,
}

_T = {
    "zh": {
        "subject": {"questions": "今天想问你的问题", "report": "决定报告", "audit": "每周落实审计"},
        "hello": "这是 existence-trace 发来的消息，只带回答所需的最少内容。",
        "reply": "直接回复这封邮件即可。回答某个问题时，那一段以 [Q-编号] 开头。",
    },
    "en": {
        "subject": {"questions": "Questions for today", "report": "Decision report", "audit": "Weekly audit"},
        "hello": "This is a message from existence-trace. It carries only what you need to answer.",
        "reply": "Just reply to this email. To answer a question, start that part with [Q-id].",
    },
}


class EmailError(Exception):
    """Bad credentials file or bad email outreach input."""


class Credentials:
    """Email settings from the credentials file. repr never shows the password."""

    def __init__(self, values):
        self.__dict__.update(values)

    def __repr__(self):
        return f"Credentials(address={self.address!r}, smtp_host={self.smtp_host!r}, imap_host={self.imap_host!r})"


class SendResult:
    def __init__(self, notification, recorded):
        self.notification = notification  # the notifications.jsonl record (new or existing)
        self.recorded = recorded  # False when date + kind was already sent

    @property
    def status(self):
        return self.notification["status"]

    def __repr__(self):
        return f"SendResult(status={self.status!r}, recorded={self.recorded})"


class ReplyResult:
    """What one reply read did. skipped lists (message_id, reason)."""

    def __init__(self):
        self.added = []
        self.unchanged = 0
        self.skipped = []
        self.error = None

    @property
    def ok(self):
        return self.error is None


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise EmailError("now must be a timezone-aware datetime")
    return now


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_credentials(path):
    """Read and validate the credentials TOML file. Errors never include values."""
    if not path:
        raise EmailError("credentials.email is not set in trace.toml")
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except OSError as exc:
        raise EmailError(f"cannot read email credentials file: {exc.strerror}") from None
    except tomllib.TOMLDecodeError:
        raise EmailError("email credentials file is not valid TOML") from None
    unknown = [k for k in raw if k not in _REQUIRED and k not in _DEFAULTS]
    if unknown:
        raise EmailError(f"unknown keys in email credentials file: {', '.join(sorted(unknown))}")
    values = {}
    for key in _REQUIRED:
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise EmailError(f"email credentials file needs {key} (non-empty string)")
        values[key] = raw[key]
    for key, default in _DEFAULTS.items():
        values[key] = raw.get(key, default)
    for key in ("smtp_port", "imap_port"):
        if not isinstance(values[key], int) or isinstance(values[key], bool):
            raise EmailError(f"{key} must be an integer")
    if values["smtp_security"] not in SECURITY:
        raise EmailError(f"smtp_security must be one of {', '.join(SECURITY)}")
    if not isinstance(values["save_to_sent"], bool):
        raise EmailError("save_to_sent must be true or false")
    values["username"] = values["username"] or values["address"]
    values["user_address"] = values["user_address"] or values["address"]
    return Credentials(values)


def _reason(exc, creds):
    text = f"{type(exc).__name__}: {exc}"
    return text.replace(creds.password, "***") if creds.password else text


def tag(date, kind):
    return f"[etrace {date} {kind}]"


def compose(data_dir, date, scan_ok, language="zh", kind="questions", decision_ids=None, summary=None):
    """(subject, body, question ids, decision ids) for DATE's email.

    The body is note.render's text between a one-line greeting and the reply
    instructions: never journal text.
    """
    if kind not in KINDS:
        raise EmailError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    T = _T[note._lang(language)]
    day = journal._iso(date)
    text, qids, dids = note.render(data_dir, day, scan_ok, language, decision_ids, summary)
    body = "\n\n".join([note._wrap(T["hello"], language), text, note._wrap(T["reply"], language)]) + "\n"
    return f"{tag(day, kind)} {T['subject'][kind]}", body, qids, dids


def _imap(creds):
    imap = imaplib.IMAP4_SSL(creds.imap_host, creds.imap_port)
    imap.login(creds.username, creds.password)
    return imap


def _logout(imap):
    try:
        imap.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def _folder(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _in_sent(creds, message_id, message=None):
    """True when MESSAGE_ID is in the Sent folder (after appending MESSAGE if given)."""
    imap = _imap(creds)
    try:
        if message is not None:
            typ, _ = imap.append(_folder(creds.sent_folder), "(\\Seen)",
                                 imaplib.Time2Internaldate(time.time()), message.as_bytes())
            if typ != "OK":
                raise imaplib.IMAP4.error(f"APPEND to {creds.sent_folder} returned {typ}")
        typ, _ = imap.select(_folder(creds.sent_folder), readonly=True)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"cannot open folder {creds.sent_folder}")
        typ, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
        return typ == "OK" and bool(data and data[0] and data[0].split())
    finally:
        _logout(imap)


def _smtp_send(creds, message):
    if creds.smtp_security == "ssl":
        smtp = smtplib.SMTP_SSL(creds.smtp_host, creds.smtp_port)
    else:
        smtp = smtplib.SMTP(creds.smtp_host, creds.smtp_port)
    try:
        if creds.smtp_security == "starttls":
            smtp.starttls()
        smtp.login(creds.username, creds.password)
        smtp.send_message(message)
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            pass


def _message(creds, subject, body, message_id, at):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = creds.address
    msg["To"] = creds.user_address
    msg["Date"] = email.utils.format_datetime(at)
    msg["Message-ID"] = message_id
    msg.set_content(body)
    return msg


def _append(store, record, day):
    with store.lock():
        records = store.read_all(NAME)
        record["id"] = f"N-{day.replace('-', '')}-{sum(1 for r in records if r['date'] == day) + 1}"
        store.append_locked(NAME, record)
    return record


def email_records(data_dir, date=None, kind=None):
    return [r for r in note.read_all(data_dir) if r.get("channel") == CHANNEL
            and (date is None or r["date"] == date) and (kind is None or r.get("kind") == kind)]


def send(data_dir, creds, date, scan_ok, language="zh", kind="questions",
         decision_ids=None, summary=None, now=None):
    """Email DATE's questions (or report) once per date + kind. Returns a SendResult."""
    at = _now(now)
    store = _store(data_dir)
    day = journal._iso(date)
    previous = email_records(store, day, kind)
    done = [r for r in previous if r["status"] == SENT]
    if done:
        return SendResult(done[-1], False)

    subject, body, qids, dids = compose(store, day, scan_ok, language, kind, decision_ids, summary)
    record = {
        "channel": CHANNEL,
        "kind": kind,
        "date": day,
        "status": None,
        "reason": None,
        "verified_readback": False,
        "question_ids": qids,
        "decision_ids": dids,
        "language": language,
        "subject_tag": tag(day, kind),
        "content_hash": _hash(body),
        "message_id": None,
        "sent_at": at.isoformat(),
    }

    # An earlier attempt may have gone out after all: look before sending again.
    for old in reversed(previous):
        if not old.get("message_id"):
            continue
        try:
            found = _in_sent(creds, old["message_id"])
        except (imaplib.IMAP4.error, OSError) as exc:
            record.update(status=UNKNOWN, message_id=old["message_id"],
                          reason=f"could not check Sent folder before resending: {_reason(exc, creds)}")
            return SendResult(_append(store, record, day), True)
        if found:
            record.update(status=SENT, verified_readback=True, message_id=old["message_id"],
                          content_hash=old["content_hash"], sent_at=old["sent_at"],
                          question_ids=old["question_ids"], decision_ids=old["decision_ids"],
                          reason=f"found earlier attempt {old['id']} in Sent folder")
            return SendResult(_append(store, record, day), True)

    domain = creds.address.rpartition("@")[2] or None
    record["message_id"] = email.utils.make_msgid("etrace", domain=domain)
    message = _message(creds, subject, body, record["message_id"], at)
    try:
        _smtp_send(creds, message)
    except (smtplib.SMTPException, OSError) as exc:
        record.update(status=FAILED, reason=_reason(exc, creds))
        return SendResult(_append(store, record, day), True)
    try:
        found = _in_sent(creds, record["message_id"], message if creds.save_to_sent else None)
    except (imaplib.IMAP4.error, OSError) as exc:
        record.update(status=UNKNOWN, reason=f"could not check Sent folder: {_reason(exc, creds)}")
        return SendResult(_append(store, record, day), True)
    if found:
        record.update(status=SENT, verified_readback=True)
    else:
        record.update(status=UNKNOWN, reason=f"message not found in folder {creds.sent_folder}")
    return SendResult(_append(store, record, day), True)


def strip_reply(text):
    """The new part of a reply: quoted older text and the signature removed."""
    kept = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _SIGNATURE.match(line.rstrip()) or _QUOTE_HEADER.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip("\n")


def split_reply(text, question_ids):
    """[(question_id or None, text)] entries of a stripped reply."""
    entries = []
    current, lines = None, []
    for line in text.split("\n"):
        m = _QUESTION.match(line.lstrip())
        if m:
            if "\n".join(lines).strip():
                entries.append((current, "\n".join(lines).strip()))
            current, lines = m.group(1), [line]
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        entries.append((current, "\n".join(lines).strip()))
    if len(question_ids) == 1:
        entries = [(q or question_ids[0], t) for q, t in entries]
    merged = {}
    for q, t in entries:
        merged[q] = merged[q] + "\n\n" + t if q in merged else t
    return list(merged.items())


def _addresses(msg, *headers):
    values = [v for h in headers for v in msg.get_all(h, [])]
    return {a.lower() for _, a in email.utils.getaddresses([str(v) for v in values]) if a}


def _plain_text(msg):
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part is not None else None


def feedback_id(message_id, question_id):
    return f"email:{_hash(message_id)[:12]}:{question_id or 'general'}"


def _parse(raw, creds, sent, result):
    """Feedback records for one fetched message (list), or None when it does not count."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    mid = str(msg.get("Message-ID", "")).strip()
    if not mid:
        result.skipped.append((None, "no Message-ID"))
        return None
    m = _TAG.search(str(msg.get("Subject", "")))
    if not m or (m.group(1), m.group(2)) not in sent:
        result.skipped.append((mid, "subject has no known etrace tag"))
        return None
    question = sent[(m.group(1), m.group(2))]
    if mid == question["message_id"]:
        return None  # our own question
    if creds.address.lower() not in _addresses(msg, "To", "Cc"):
        result.skipped.append((mid, "not sent to the mailbox address"))
        return None
    if _addresses(msg, "From") != {creds.user_address.lower()}:
        result.skipped.append((mid, "not from the user's address"))
        return None
    try:
        received = email.utils.parsedate_to_datetime(str(msg["Date"]))
    except (TypeError, ValueError):
        result.skipped.append((mid, "no valid Date header"))
        return None
    if received.tzinfo is None:
        received = received.replace(tzinfo=datetime.timezone.utc)
    if received <= datetime.datetime.fromisoformat(question["sent_at"]):
        result.skipped.append((mid, "dated before the question was sent"))
        return None
    body = _plain_text(msg)
    if body is None:
        result.skipped.append((mid, "no text/plain part"))
        return None
    entries = split_reply(strip_reply(body), question["question_ids"])
    if not entries:
        result.skipped.append((mid, "no new text after stripping quotes and signature"))
        return None
    return [{
        "id": feedback_id(mid, qid),
        "source": "email",
        "message_id": mid,
        "notification_id": question["id"],
        "refers_to_date": question["date"],
        "received_at": received.isoformat(),
        "text": text,
        "content_hash": _hash(text),
        "question_id": qid,
        "supersedes": None,
        "version": 1,
    } for qid, text in entries]


def read_replies(data_dir, creds, now=None):
    """Store new replies to sent question emails in feedback.jsonl. Returns a ReplyResult.

    An IMAP failure is reported in result.error and nothing is stored.
    """
    observed_at = _now(now).isoformat()
    store = _store(data_dir)
    result = ReplyResult()
    sent = {}
    for r in email_records(store):
        if r["status"] in (SENT, UNKNOWN) and r.get("message_id"):
            sent[(r["date"], r["kind"])] = r  # latest attempt per date + kind
    if not sent:
        return result
    try:
        imap = _imap(creds)
        try:
            typ, _ = imap.select(_folder(creds.inbox), readonly=True)
            if typ != "OK":
                raise imaplib.IMAP4.error(f"cannot open folder {creds.inbox}")
            typ, data = imap.search(None, "SUBJECT", '"[etrace "')
            if typ != "OK":
                raise imaplib.IMAP4.error(f"SEARCH returned {typ}")
            raws = []
            for num in (data[0] or b"").split():
                typ, parts = imap.fetch(num, "(RFC822)")
                if typ != "OK":
                    raise imaplib.IMAP4.error(f"FETCH returned {typ}")
                raws += [p[1] for p in parts if isinstance(p, tuple)]
        finally:
            _logout(imap)
    except (imaplib.IMAP4.error, OSError) as exc:
        result.error = _reason(exc, creds)
        return result

    known = {r["id"] for r in feedback.read_all(store)}
    for raw in raws:
        for record in _parse(raw, creds, sent, result) or []:
            if record["id"] in known:
                result.unchanged += 1
                continue
            record["observed_at"] = observed_at
            try:
                store.append(feedback.NAME, record)
            except InvalidRecord:
                result.unchanged += 1
                continue
            known.add(record["id"])
            result.added.append(record)
    return result
