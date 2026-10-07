"""Decisions made from user feedback (decisions.jsonl).

A decision is the agent's concrete conclusion from one or more stored feedback
entries. Every decision must cite at least one feedback id that exists in
feedback.jsonl and must say where its value came from:

    user            the user gave the number / judgment themselves
    agent_decision  the agent decided on the user's behalf from their words

A decision may change rules. Each rule change is applied through
etrace.container.rules (scoped supersession), and the decision record keeps
the resulting rule ids, statuses and rules.json version.

History is append-only. A decision is one 'decision' record ('D-YYYYMMDD-N');
mark_pending appends a 'pending' record saying why it has not been applied
yet. validate.py treats a pending decision as known-open instead of missed.

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime

from etrace.container import feedback, rules
from etrace.store import Store

NAME = "decisions"
VALUE_SOURCES = ("user", "agent_decision")
_CHANGE_KEYS = {"rule_id", "scope", "text", "expressed_at", "source_feedback_id", "reason"}


class DecisionError(Exception):
    """Invalid decision input or unknown decision."""


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise DecisionError("now must be a timezone-aware datetime")
    return now


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DecisionError(f"{field} is required (non-empty string)")
    return value


def _feedback_ids(data_dir, value):
    if not isinstance(value, (list, tuple)) or not value:
        raise DecisionError("a decision needs at least one source feedback id")
    ids = [_text(v, "feedback id") for v in value]
    known = {r["id"] for r in feedback.read_all(data_dir)}
    missing = [i for i in ids if i not in known]
    if missing:
        raise DecisionError(f"feedback not in feedback.jsonl: {missing}")
    return ids


def _rule_changes(value, feedback_ids, existing):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise DecisionError("rule_changes must be a list of dicts")
    changes = []
    for change in value:
        if not isinstance(change, dict):
            raise DecisionError("each rule change must be a dict")
        unknown = set(change) - _CHANGE_KEYS
        if unknown:
            raise DecisionError(f"unknown rule change keys: {sorted(unknown)}")
        for key in ("rule_id", "scope", "text", "expressed_at"):
            if key not in change:
                raise DecisionError(f"rule change is missing {key!r}")
        source = change.get("source_feedback_id", feedback_ids[0])
        if source not in feedback_ids:
            raise DecisionError(f"rule change source {source!r} is not one of the decision's feedback ids")
        if change["rule_id"] in existing:
            raise DecisionError(f"rule id {change['rule_id']!r} already exists")
        changes.append(dict(change, source_feedback_id=source))
    ids = [c["rule_id"] for c in changes]
    if len(set(ids)) != len(ids):
        raise DecisionError("rule ids within one decision must be unique")
    return changes


def read_all(data_dir):
    return _store(data_dir).read_all(NAME)


def _replay(records):
    decisions = {}
    for r in records:
        if r.get("type") == "decision":
            decisions[r["id"]] = dict(r, pending=None)
        elif r.get("type") == "pending":
            decisions[r["decision_id"]]["pending"] = {"reason": r["reason"], "at": r["at"]}
    return decisions


def all_decisions(data_dir):
    """Every decision (with its latest pending note or None), in the order made."""
    return list(_replay(read_all(data_dir)).values())


def get(data_dir, decision_id):
    try:
        return _replay(read_all(data_dir))[decision_id]
    except KeyError:
        raise KeyError(decision_id) from None


def decide(data_dir, feedback_ids, decision_text, value_source, rule_changes=None, now=None):
    """Store a decision and apply its rule changes. Returns the stored record.

    rule_changes: list of {'rule_id', 'scope', 'text', 'expressed_at',
    optional 'source_feedback_id' (defaults to the first feedback id, must be
    one of feedback_ids), optional 'reason'}.
    """
    store = _store(data_dir)
    at = _now(now)
    ids = _feedback_ids(store, feedback_ids)
    _text(decision_text, "decision_text")
    if value_source not in VALUE_SOURCES:
        raise DecisionError(f"value_source must be one of {VALUE_SOURCES}")
    existing = {r["id"] for r in rules.read(store)["rules"]}
    changes = _rule_changes(rule_changes, ids, existing)

    applied = []
    for c in changes:
        rule = rules.add_rule(store, c["rule_id"], c["scope"], c["text"], c["expressed_at"],
                              source_feedback_id=c["source_feedback_id"], reason=c.get("reason"))
        applied.append({"rule_id": rule["id"], "scope": rule["scope"], "status": rule["status"],
                        "superseded_by": rule["superseded_by"]})

    record = {
        "type": "decision",
        "feedback_ids": ids,
        "decision": decision_text,
        "value_source": value_source,
        "rule_changes": applied,
        "rules_version": rules.version(store),
        "decided_at": at.isoformat(),
    }
    stamp = at.date().isoformat().replace("-", "")
    with store.lock():
        n = sum(1 for r in store.read_all(NAME)
                if r.get("type") == "decision" and r["id"].startswith(f"D-{stamp}-")) + 1
        record["id"] = f"D-{stamp}-{n}"
        store.append_locked(NAME, record)
    return dict(record)


def mark_pending(data_dir, decision_id, reason, now=None):
    """Record that a decision is knowingly not applied yet (and why)."""
    _text(reason, "reason")
    at = _now(now).isoformat()
    store = _store(data_dir)
    with store.lock():
        records = store.read_all(NAME)
        if decision_id not in _replay(records):
            raise DecisionError(f"unknown decision {decision_id!r}")
        n = sum(1 for r in records if r.get("type") == "pending" and r["decision_id"] == decision_id) + 1
        store.append_locked(NAME, {"id": f"{decision_id}:pending:{n}", "type": "pending",
                                   "decision_id": decision_id, "reason": reason, "at": at})
    return get(store, decision_id)
