"""Genuine questions (questions.jsonl).

The system asks only real, evidence-based questions. Every question must carry:

    source         where the doubt came from (e.g. 'journal', 'g-self')
    date           the day the evidence belongs to (YYYY-MM-DD)
    evidence       {'kind': 'quote' | 'behavior', 'text': ...}: the user's own
                   words or a behavior anyone could verify
    candidates     exactly two different possible judgments
    uncertainty    what exactly is unclear
    dimensions     the dimensions an answer would change (e.g. ['pad.A'])
    dedupe_key     stable key for "the same question"

A dedupe_key that is already open or answered is rejected (a skipped question
may be asked again). A still-open question can be followed up at most once.

History is append-only: a question is one 'question' record; its follow-up and
its closing (answered | skipped) are later records that point at it via
question_id. status() replays them. Lifecycle: open -> answered | skipped, and
answered requires the feedback_id of a stored feedback record.

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime

from etrace.container import feedback
from etrace.store import Store

NAME = "questions"
OPEN, ANSWERED, SKIPPED = "open", "answered", "skipped"
EVIDENCE_KINDS = ("quote", "behavior")
NO_QUESTIONS = "no new important questions"
SCAN_FAILED = "scan failed, cannot judge"


class QuestionError(Exception):
    """Invalid question, duplicate question, or an illegal lifecycle step."""


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise QuestionError("now must be a timezone-aware datetime")
    return now


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise QuestionError(f"{field} is required (non-empty string)")
    return value


def _date(value):
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise QuestionError(f"date must be YYYY-MM-DD, got {value!r}") from None


def _evidence(value):
    if not isinstance(value, dict):
        raise QuestionError("evidence is required: {'kind': 'quote'|'behavior', 'text': ...}")
    if value.get("kind") not in EVIDENCE_KINDS:
        raise QuestionError(f"evidence kind must be one of {EVIDENCE_KINDS}")
    _text(value.get("text"), "evidence text")
    return {"kind": value["kind"], "text": value["text"]}


def _candidates(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise QuestionError("candidates must be exactly two possible judgments")
    a, b = (_text(c, "candidate judgment") for c in value)
    if a.strip() == b.strip():
        raise QuestionError("the two candidate judgments must differ")
    return [a, b]


def _dimensions(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise QuestionError("dimensions must list at least one affected dimension")
    return [_text(d, "dimension") for d in value]


def read_all(data_dir):
    return _store(data_dir).read_all(NAME)


def _replay(records):
    """Fold records into {question_id: question dict with status/follow_up/closing fields}."""
    questions = {}
    for r in records:
        kind = r.get("type")
        if kind == "question":
            questions[r["id"]] = dict(r, status=OPEN, follow_up=None,
                                      feedback_id=None, closed_at=None, skip_reason=None)
        elif kind == "follow_up":
            questions[r["question_id"]]["follow_up"] = {"text": r["text"], "at": r["at"]}
        elif kind in (ANSWERED, SKIPPED):
            q = questions[r["question_id"]]
            q.update(status=kind, closed_at=r["at"], feedback_id=r.get("feedback_id"),
                     skip_reason=r.get("reason"))
    return questions


def all_questions(data_dir):
    """Every question with its replayed status, in the order asked."""
    return list(_replay(read_all(data_dir)).values())


def get(data_dir, question_id):
    try:
        return _replay(read_all(data_dir))[question_id]
    except KeyError:
        raise KeyError(question_id) from None


def open_questions(data_dir, date=None):
    """Open questions, optionally only those whose date is on or before date."""
    limit = None if date is None else _date(date)
    return [q for q in all_questions(data_dir)
            if q["status"] == OPEN and (limit is None or q["date"] <= limit)]


def add_question(data_dir, source, date, evidence, candidates, uncertainty, dimensions,
                 dedupe_key, now=None):
    """Validate and store a new open question. Returns the stored record.

    The id is 'Q-YYYYMMDD-N' (N counts questions with the same date), short
    enough for the user to type as [Q-...] in the feedback zone.
    """
    record = {
        "type": "question",
        "source": _text(source, "source"),
        "date": _date(date),
        "evidence": _evidence(evidence),
        "candidates": _candidates(candidates),
        "uncertainty": _text(uncertainty, "uncertainty"),
        "dimensions": _dimensions(dimensions),
        "dedupe_key": _text(dedupe_key, "dedupe_key"),
        "asked_at": _now(now).isoformat(),
    }
    store = _store(data_dir)
    with store.lock():
        records = store.read_all(NAME)
        questions = _replay(records)
        for q in questions.values():
            if q["dedupe_key"] == record["dedupe_key"] and q["status"] in (OPEN, ANSWERED):
                raise QuestionError(f"dedupe_key {record['dedupe_key']!r} already {q['status']} as {q['id']}")
        n = sum(1 for q in questions.values() if q["date"] == record["date"]) + 1
        record["id"] = f"Q-{record['date'].replace('-', '')}-{n}"
        store.append_locked(NAME, record)
    return dict(record)


def _transition(data_dir, question_id, kind, extra, now, check=None):
    at = _now(now).isoformat()
    store = _store(data_dir)
    with store.lock():
        questions = _replay(store.read_all(NAME))
        q = questions.get(question_id)
        if q is None:
            raise QuestionError(f"unknown question {question_id!r}")
        if q["status"] != OPEN:
            raise QuestionError(f"question {question_id} is {q['status']}, not open")
        if check:
            check(q)
        record = dict(extra, id=f"{question_id}:{kind}", type=kind, question_id=question_id, at=at)
        store.append_locked(NAME, record)
    return get(store, question_id)


def follow_up(data_dir, question_id, text, now=None):
    """Gently follow up a still-open question. Allowed once per question."""
    _text(text, "follow-up text")

    def once(q):
        if q["follow_up"] is not None:
            raise QuestionError(f"question {question_id} was already followed up once")

    return _transition(data_dir, question_id, "follow_up", {"text": text}, now, once)


def answer(data_dir, question_id, feedback_id, now=None):
    """Close a question as answered by a stored feedback record."""
    _text(feedback_id, "feedback_id")
    if not any(r["id"] == feedback_id for r in feedback.read_all(data_dir)):
        raise QuestionError(f"feedback {feedback_id!r} is not in feedback.jsonl")
    return _transition(data_dir, question_id, ANSWERED, {"feedback_id": feedback_id}, now)


def skip(data_dir, question_id, reason=None, now=None):
    """Close a question without an answer. Its dedupe_key may be asked again later."""
    return _transition(data_dir, question_id, SKIPPED, {"reason": reason}, now)


def daily_status(data_dir, date, scan_ok):
    """The once-a-day status: the open questions (list), or a status sentence.

    'no new important questions' is only claimed after a successful scan; a
    failed scan never pretends there is nothing to ask.
    """
    if scan_ok is not True:
        return SCAN_FAILED
    questions = open_questions(data_dir, date)
    return questions if questions else NO_QUESTIONS
