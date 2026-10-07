"""Usage receipts (usage.jsonl): proof that a rule or decision was really used.

Reading rules.json proves nothing. A receipt is written only after a task has
written its product (daily note, G score, ...) and read it back to verify the
write, so record() refuses anything but verified_readback=True. Each receipt
names the task, the target product, the rules.json version used, the feedback
and decision ids applied, the judgment before and after, and a status:

    changed         the rule changed the output (before != after)
    confirmed       the rule was applied and the output already agreed
    no_match        the rule was checked but did not match this input
    not_applicable  the rule does not apply to this task/target
    blocked         the rule could not be applied (reason in 'after')

Only changed and confirmed count as applied (applied()).

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime

from etrace.container import decisions, feedback, rules
from etrace.store import Store

NAME = "usage"
STATUSES = ("changed", "confirmed", "no_match", "not_applicable", "blocked")
APPLIED_STATUSES = ("changed", "confirmed")


class UsageError(Exception):
    """Invalid receipt (unverified write, unknown ids, bad status...)."""


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise UsageError("now must be a timezone-aware datetime")
    return now


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise UsageError(f"{field} is required (non-empty string)")
    return value


def _ids(value, field, known):
    if not isinstance(value, (list, tuple)):
        raise UsageError(f"{field} must be a list")
    ids = [_text(v, field) for v in value]
    missing = [i for i in ids if i not in known]
    if missing:
        raise UsageError(f"unknown {field}: {missing}")
    return ids


def read_all(data_dir):
    return _store(data_dir).read_all(NAME)


def record(data_dir, task, target, rule_version, feedback_ids, decision_ids, before, after,
           status, verified_readback, now=None):
    """Store one receipt after a verified write-back. Returns the stored record."""
    if verified_readback is not True:
        raise UsageError("a receipt needs verified_readback=True (write the product and read it back first)")
    if status not in STATUSES:
        raise UsageError(f"status must be one of {STATUSES}")
    store = _store(data_dir)
    _text(task, "task")
    _text(target, "target")
    if not isinstance(rule_version, int) or isinstance(rule_version, bool) or rule_version < 0:
        raise UsageError("rule_version must be a non-negative int")
    if rule_version > rules.version(store):
        raise UsageError(f"rule_version {rule_version} is newer than rules.json")
    fids = _ids(feedback_ids, "feedback_ids", {r["id"] for r in feedback.read_all(store)})
    dids = _ids(decision_ids, "decision_ids", {d["id"] for d in decisions.all_decisions(store)})
    if not fids and not dids:
        raise UsageError("a receipt must name at least one feedback_id or decision_id")
    if status == "changed" and before == after:
        raise UsageError("status 'changed' needs before != after")
    at = _now(now)
    rec = {
        "task": task,
        "target": target,
        "rule_version": rule_version,
        "feedback_ids": fids,
        "decision_ids": dids,
        "before": before,
        "after": after,
        "status": status,
        "verified_readback": True,
        "recorded_at": at.isoformat(),
    }
    stamp = at.date().isoformat().replace("-", "")
    with store.lock():
        n = sum(1 for r in store.read_all(NAME) if r["id"].startswith(f"U-{stamp}-")) + 1
        rec["id"] = f"U-{stamp}-{n}"
        store.append_locked(NAME, rec)
    return dict(rec)


def receipts_for(data_dir, decision_id):
    """Every verified receipt that names decision_id."""
    return [r for r in read_all(data_dir)
            if decision_id in r.get("decision_ids", []) and r.get("verified_readback") is True]


def applied(data_dir, decision_id):
    """True only if a verified receipt shows the decision changed or confirmed an output."""
    return any(r["status"] in APPLIED_STATUSES for r in receipts_for(data_dir, decision_id))
