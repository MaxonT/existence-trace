"""Structural validation of the preference container chain.

question -> feedback -> decision -> rule -> receipt. validate() reports every
broken link it finds:

- an answered question without a feedback_id (or with one not in feedback.jsonl)
- a decision without a source feedback (none, or ids not in feedback.jsonl)
- a decision older than PENDING_AFTER (2 days) with no usage receipt that is
  not marked pending (decisions.mark_pending)
- a receipt without verified_readback=True

Only receipts count: reading rules.json never makes a decision "used".
Passing validate() means the chain is structurally complete, not that the
decisions were good (that is the weekly audit's job).
"""

import dataclasses
import datetime

from etrace.container import decisions, feedback, questions, usage

PENDING_AFTER = datetime.timedelta(days=2)


@dataclasses.dataclass
class ValidationResult:
    problems: list

    @property
    def ok(self):
        return not self.problems


def validate(data_dir, now=None):
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    problems = []
    feedback_ids = {r["id"] for r in feedback.read_all(data_dir)}

    for q in questions.all_questions(data_dir):
        if q["status"] == questions.ANSWERED and q.get("feedback_id") not in feedback_ids:
            problems.append(f"question {q['id']} is answered without a stored feedback_id")

    receipts = usage.read_all(data_dir)
    for r in receipts:
        if r.get("verified_readback") is not True:
            problems.append(f"receipt {r['id']} has no verified read-back")
    with_receipt = {d for r in receipts if r.get("verified_readback") is True
                    for d in r.get("decision_ids", [])}

    for d in decisions.all_decisions(data_dir):
        ids = d.get("feedback_ids") or []
        if not ids or any(i not in feedback_ids for i in ids):
            problems.append(f"decision {d['id']} lacks a stored source feedback")
        age = now - datetime.datetime.fromisoformat(d["decided_at"])
        if age > PENDING_AFTER and d["id"] not in with_receipt and d["pending"] is None:
            problems.append(f"decision {d['id']} is older than 2 days with no usage receipt and is not pending")
    return ValidationResult(problems)
