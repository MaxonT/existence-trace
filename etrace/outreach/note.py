"""Note outreach: the ai:questions block of a day's note (notifications.jsonl).

The block has two parts:

    questions        the open questions, numbered, each with its [Q-id] so the
                     user can answer in the feedback zone; or the daily status
                     sentence ('no new important questions' only after a
                     successful scan, otherwise 'scan failed, cannot judge')
    decision report  one or two summary sentences, then per decision what was
                     decided, why, and which feedback it came from, ending
                     with an invitation to say what the user agrees with

Which decisions are reported: every decision not yet reported in a
notification of an earlier day (so reruns on the same day keep the report).

A notifications.jsonl record is appended only after the note has been read
back and the block text found in it. A rerun that produces the same text for
the same day records nothing new.

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime
import hashlib
import os
import textwrap

from etrace import journal
from etrace.container import decisions, feedback, questions
from etrace.store import Store

NAME = "notifications"
BLOCK = "questions"
CHANNEL = "note"
LANGUAGES = ("zh", "en")
WIDTH = {"zh": 40, "en": 72}  # characters per line; well under 120, readable on a phone

_T = {
    "zh": {
        "intro": "下面是我想问你的问题。回答时在「给 AI 的反馈」区以 [Q-编号] 开头。",
        "evidence": {"quote": "依据（你的原话）：", "behavior": "依据（可核验的行为）："},
        "candidates": "可能是「{a}」，也可能是「{b}」。",
        "follow_up": "再问一次：",
        "status": {
            questions.NO_QUESTIONS: "目前没有新的重要疑问。",
            questions.SCAN_FAILED: "这次扫描失败，无法判断有没有新的疑问。",
        },
        "report": "### 决定报告",
        "summary": "我根据你的 {f} 条反馈做了 {d} 个决定，改动了 {r} 条规则。",
        "what": "我决定：",
        "why": "为什么：",
        "from": "参考了你的话：",
        "source": {"user": "这是你直接给出的判断。", "agent_decision": "这是我根据你的话代你作的决定。"},
        "rules": "规则：",
        "pending": "暂未应用：",
        "invite": "哪些你赞成、哪些不赞成？写在下面的反馈区就好，你随时可以推翻。",
        "quote": "「{t}」",
        "sep": "；",
    },
    "en": {
        "intro": "Here is what I want to ask you. To answer, start a note in the feedback zone with [Q-id].",
        "evidence": {"quote": "Evidence (your words): ", "behavior": "Evidence (a checkable behavior): "},
        "candidates": "It could be \"{a}\", or it could be \"{b}\".",
        "follow_up": "Asking once more: ",
        "status": {
            questions.NO_QUESTIONS: "No new important questions right now.",
            questions.SCAN_FAILED: "The scan failed this time, so I cannot tell whether there are new questions.",
        },
        "report": "### Decision report",
        "summary": "From {f} of your feedback notes I made {d} decisions and changed {r} rules.",
        "what": "I decided: ",
        "why": "Why: ",
        "from": "Based on your words: ",
        "source": {"user": "This is your own judgment.",
                   "agent_decision": "I decided this on your behalf from your words."},
        "rules": "Rules: ",
        "pending": "Not applied yet: ",
        "invite": "Which of these do you agree with, and which not? "
                  "Write it in the feedback zone below; you can overturn any of them.",
        "quote": "\"{t}\"",
        "sep": "; ",
    },
}


class OutreachError(Exception):
    """Bad outreach input, or the note did not contain the text after writing."""


class NoteResult:
    def __init__(self, path, text, notification, recorded):
        self.path = path
        self.text = text
        self.notification = notification  # the notifications.jsonl record (new or existing)
        self.recorded = recorded  # False when an identical notification already existed

    def __repr__(self):
        return f"NoteResult(path={self.path!r}, recorded={self.recorded})"


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise OutreachError("now must be a timezone-aware datetime")
    return now


def _lang(language):
    if language not in LANGUAGES:
        raise OutreachError(f"unknown language {language!r}; expected one of {', '.join(LANGUAGES)}")
    return language


def _wrap(text, language, first="", rest=None):
    """Wrap one paragraph to the language's width with a hanging indent."""
    rest = " " * len(first) if rest is None else rest
    lines = []
    for i, part in enumerate(text.split("\n")):
        lines += textwrap.wrap(part, WIDTH[language], initial_indent=first if i == 0 else rest,
                               subsequent_indent=rest, break_on_hyphens=False) or [rest.rstrip()]
    return "\n".join(lines)


def read_all(data_dir):
    return _store(data_dir).read_all(NAME)


def reported_decision_ids(data_dir, before=None):
    """Ids of decisions already reported in a notification (optionally dated before BEFORE)."""
    return {d for r in read_all(data_dir) if before is None or r["date"] < before
            for d in r.get("decision_ids", [])}


def render_questions(items, language="zh"):
    """Text for the questions part. ITEMS is questions.daily_status(...) output."""
    T = _T[_lang(language)]
    if isinstance(items, str):
        if items not in T["status"]:
            raise OutreachError(f"unknown status {items!r}")
        return _wrap(T["status"][items], language)
    parts = [_wrap(T["intro"], language)]
    for n, q in enumerate(items, 1):
        head = f"{n}. "
        lines = [_wrap(f"[{q['id']}] {q['uncertainty']}", language, head)]
        ind = " " * len(head)
        a, b = q["candidates"]
        lines.append(_wrap(T["candidates"].format(a=a, b=b), language, ind))
        ev = q["evidence"]
        text = T["quote"].format(t=ev["text"]) if ev["kind"] == "quote" else ev["text"]
        lines.append(_wrap(T["evidence"][ev["kind"]] + text, language, ind))
        if q.get("follow_up"):
            lines.append(_wrap(T["follow_up"] + q["follow_up"]["text"], language, ind))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _why(decision, T):
    reasons = [c["reason"] for c in decision.get("rule_changes", []) if c.get("reason")]
    return T["sep"].join(reasons + [T["source"][decision["value_source"]]])


def render_report(decision_list, feedback_by_id, language="zh", summary=None):
    """Text for the decision report, or '' when there is nothing to report."""
    T = _T[_lang(language)]
    if not decision_list:
        return ""
    if summary is None:
        fids = {f for d in decision_list for f in d["feedback_ids"]}
        changed = sum(len(d.get("rule_changes", [])) for d in decision_list)
        summary = T["summary"].format(f=len(fids), d=len(decision_list), r=changed)
    parts = [T["report"], _wrap(summary.strip(), language)]
    for n, d in enumerate(decision_list, 1):
        head = f"{n}. "
        ind = " " * len(head)
        lines = [_wrap(T["what"] + d["decision"], language, head)]
        lines.append(_wrap(T["why"] + _why(d, T), language, ind))
        quotes = T["sep"].join(T["quote"].format(t=feedback_by_id[f]["text"]) if f in feedback_by_id else f
                               for f in d["feedback_ids"])
        lines.append(_wrap(T["from"] + quotes, language, ind))
        if d.get("rule_changes"):
            rules = ", ".join(f"{c['rule_id']} ({c['status']})" for c in d["rule_changes"])
            lines.append(_wrap(T["rules"] + rules, language, ind))
        if d.get("pending"):
            lines.append(_wrap(T["pending"] + d["pending"]["reason"], language, ind))
        parts.append("\n".join(lines))
    parts.append(_wrap(T["invite"], language))
    return "\n\n".join(parts)


def render(data_dir, date, scan_ok, language="zh", decision_ids=None, summary=None):
    """(block text, question ids, decision ids) for DATE's ai:questions block."""
    store = _store(data_dir)
    day = journal._iso(date)
    status = questions.daily_status(store, day, scan_ok)
    known = {d["id"]: d for d in decisions.all_decisions(store)}
    if decision_ids is None:
        old = reported_decision_ids(store, before=day)
        decision_ids = [i for i in known if i not in old]
    missing = [i for i in decision_ids if i not in known]
    if missing:
        raise OutreachError(f"unknown decisions: {missing}")
    chosen = [known[i] for i in decision_ids]
    fb = {r["id"]: r for r in feedback.read_all(store)}
    text = render_questions(status, language)
    report = render_report(chosen, fb, language, summary)
    if report:
        text += "\n\n" + report
    qids = [] if isinstance(status, str) else [q["id"] for q in status]
    return text, qids, list(decision_ids)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(journal_dir, data_dir, date, scan_ok, language="zh", preset="default",
          decision_ids=None, summary=None, now=None):
    """Write DATE's ai:questions block and record the notification. Returns a NoteResult.

    Creates the day's note if it does not exist yet. User and verbatim blocks
    are protected by journal.save. The notification is recorded only after
    the note is read back and the block text is found in it.
    """
    at = _now(now)
    store = _store(data_dir)
    day = journal._iso(date)
    text, qids, dids = render(store, day, scan_ok, language, decision_ids, summary)

    path = journal.note_path(journal_dir, day)
    doc = journal.load(path) if os.path.exists(path) else journal.new_note(day, preset, _lang(language))
    journal.replace_ai_block(doc, BLOCK, text)
    journal.save(doc, path)
    if text.strip("\n") not in journal.load(path).block(f"ai:{BLOCK}").content():
        raise OutreachError(f"block text not found in {path} after writing")

    if scan_ok is not True:
        status = questions.SCAN_FAILED
    else:
        status = None if qids else questions.NO_QUESTIONS
    record = {
        "channel": CHANNEL,
        "date": day,
        "status": "sent",
        "verified_readback": True,
        "question_ids": qids,
        "decision_ids": dids,
        "daily_status": status,
        "language": language,
        "content_hash": _hash(text),
        "sent_at": at.isoformat(),
    }
    stamp = day.replace("-", "")
    with store.lock():
        records = store.read_all(NAME)
        same_day = [r for r in records if r["date"] == day and r["channel"] == CHANNEL]
        if same_day and same_day[-1]["content_hash"] == record["content_hash"]:
            return NoteResult(path, text, same_day[-1], False)
        record["id"] = f"N-{stamp}-{sum(1 for r in records if r['date'] == day) + 1}"
        store.append_locked(NAME, record)
    return NoteResult(path, text, record, True)
