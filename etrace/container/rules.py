"""Rules with scoped supersession (rules.json).

Within one scope the newest user instruction wins. "Newest" is decided by
expressed_at, the time the user originally expressed it, never by write time:
a rule expressed earlier but added later goes straight into history as
superseded by the newer one. A superseded rule leaves active_rules() but stays in
rules.json with superseded_by and superseded_reason, so the evidence of who
replaced whom (and why) is kept. Different scopes never affect each other.

rules.json = {"version": N, "rules": [...]}. Every change bumps version by one
and is written atomically under the store lock (read-modify-write).

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime
import json
import os

from etrace.store import Store, write_json_atomic

FILENAME = "rules.json"
_FIELDS = ("id", "scope", "text", "expressed_at", "source_feedback_id")


class RuleError(Exception):
    """Invalid rule input, or a rule id reused with different content."""


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _path(store):
    return os.path.join(store.data_dir, FILENAME)


def _read(store):
    path = _path(store)
    if not os.path.exists(path):
        return {"version": 0, "rules": []}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("version"), int) or not isinstance(data.get("rules"), list):
        raise RuleError(f"{FILENAME} is malformed")
    return data


def _when(value):
    """Parse expressed_at into an aware datetime. Naive times are ambiguous and rejected."""
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.datetime.fromisoformat(value)
        except ValueError:
            raise RuleError(f"expressed_at is not an ISO datetime: {value!r}") from None
    else:
        raise RuleError("expressed_at must be an ISO datetime string or datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise RuleError(f"expressed_at needs a timezone offset: {value!r}")
    return dt


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise RuleError(f"{field} must be a non-empty string")
    return value


def read(data_dir):
    """Return the whole rules.json content ({'version': 0, 'rules': []} if missing)."""
    return _read(_store(data_dir))


def version(data_dir):
    return read(data_dir)["version"]


def add_rule(data_dir, rule_id, scope, text, expressed_at, source_feedback_id=None, reason=None):
    """Add a rule and apply scoped supersession. Returns the stored rule.

    Re-adding an identical rule (same id and content) is a no-op; reusing an id
    with different content raises RuleError.
    """
    _text(rule_id, "rule_id")
    _text(scope, "scope")
    _text(text, "text")
    when = _when(expressed_at)
    if source_feedback_id is not None:
        _text(source_feedback_id, "source_feedback_id")
    new = {
        "id": rule_id,
        "scope": scope,
        "text": text,
        "expressed_at": when.isoformat(),
        "source_feedback_id": source_feedback_id,
    }
    store = _store(data_dir)
    with store.lock():
        data = _read(store)
        rules = data["rules"]
        for rule in rules:
            if rule["id"] == rule_id:
                if all(rule.get(k) == new[k] for k in _FIELDS):
                    return dict(rule)
                raise RuleError(f"rule id {rule_id!r} already exists with different content")
        version = data["version"] + 1
        new.update(added_in_version=version, status="active", superseded_by=None,
                   superseded_reason=None, superseded_in_version=None)
        current = [r for r in rules if r["scope"] == scope and r["status"] == "active"]
        for old in current:
            if _when(old["expressed_at"]) <= when:
                old.update(
                    status="superseded",
                    superseded_by=rule_id,
                    superseded_reason=reason
                    or f"newer instruction in scope {scope!r} (expressed {new['expressed_at']})",
                    superseded_in_version=version,
                )
            else:
                new.update(
                    status="superseded",
                    superseded_by=old["id"],
                    superseded_reason=f"expressed before active rule {old['id']!r} "
                    f"(expressed {old['expressed_at']}) in scope {scope!r}",
                    superseded_in_version=version,
                )
        rules.append(new)
        data["version"] = version
        write_json_atomic(_path(store), data)
    return dict(new)


def active_rules(data_dir, scope=None):
    """Rules in force, at most one per scope, ordered by scope."""
    rules = [r for r in read(data_dir)["rules"] if r["status"] == "active"]
    if scope is not None:
        rules = [r for r in rules if r["scope"] == scope]
    return sorted(rules, key=lambda r: r["scope"])


def get(data_dir, rule_id):
    for rule in read(data_dir)["rules"]:
        if rule["id"] == rule_id:
            return rule
    raise KeyError(rule_id)


def history(data_dir, rule_id):
    """Every rule in rule_id's scope (active and superseded), oldest expression first."""
    scope = get(data_dir, rule_id)["scope"]
    rules = [r for r in read(data_dir)["rules"] if r["scope"] == scope]
    return sorted(rules, key=lambda r: (_when(r["expressed_at"]), r["added_in_version"]))
