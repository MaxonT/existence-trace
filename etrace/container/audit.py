"""Weekly implementation audit (audits/YYYY-Www.md and .json).

Did I really apply what I learned? weekly() walks every chain

    question -> feedback -> decision -> rule -> usage receipt

that started in the 7 days ending on as_of, plus every older chain that is
still open, and reports:

- broken links: an answered question whose feedback is missing; feedback with
  no decision; a decision citing unknown feedback; a rule change missing from
  rules.json or attributed to other feedback; a receipt without
  verified_readback; a receipt recorded before its decision or on rules older
  than the decision's rule changes; a decision past the grace period whose
  receipts never applied it (only no_match / not_applicable / blocked)
- decisions with no receipt at all (broken once past the grace period unless
  marked pending; pending ones are listed as known-open)
- one sampled applied receipt and one sampled not_applicable receipt, for a
  person to spot-check before/after
- the user's reaction to each decision: explicit approval, objection or
  another reply (feedback naming the decision id), or silence. Silence is never
  approval and is listed separately.

Structural completeness is not a pass: validate() only checks that the links
exist, while the audit also requires that every decision was really applied on
the rules it produced. passed is True only with zero broken chains.

Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime
import json
import os
import random
import re

from etrace.container import decisions, feedback, questions, rules, usage
from etrace.store import Store, write_text_atomic

GRACE = datetime.timedelta(days=2)  # same as validate.PENDING_AFTER
WINDOW_DAYS = 7
_DECISION_ID = re.compile(r"\bD-\d{8}-\d+\b")
_OBJECT = ("不赞成", "不同意", "反对", "不对", "不行", "推翻", "disagree", "object", "reject", "don't agree")
_APPROVE = ("赞成", "同意", "认可", "没问题", "agree", "approve", "sounds good", "ok", "yes")


class AuditError(Exception):
    """Invalid audit input."""


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _day(value):
    if isinstance(value, datetime.datetime):
        raise AuditError("as_of must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise AuditError(f"as_of must be YYYY-MM-DD, got {value!r}") from None


def _now(now, as_of):
    if now is None:
        return datetime.datetime.combine(as_of, datetime.time(23, 59, 59), datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise AuditError("now must be a timezone-aware datetime")
    return now


def _at(text):
    return datetime.datetime.fromisoformat(text)


def week_id(as_of):
    year, week, _ = _day(as_of).isocalendar()
    return f"{year}-W{week:02d}"


def _feedback_at(record):
    return record.get("received_at") or record["observed_at"]


def reaction(text):
    """'objected', 'approved' or 'replied' for a feedback text that names a decision."""
    low = text.lower()
    if any(w in low for w in _OBJECT):
        return "objected"
    if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low) if w.isascii() else w in low
           for w in _APPROVE):
        return "approved"
    return "replied"


class _Chain:
    def __init__(self, kind, root, started_at):
        self.kind = kind          # 'question' | 'feedback' | 'decision'
        self.root = root
        self.started_at = started_at
        self.question_id = None
        self.feedback_ids = []
        self.decision_ids = []
        self.receipt_ids = []
        self.problems = []
        self.open = []            # reasons the chain is not closed yet (not broken)

    def to_dict(self):
        return {"kind": self.kind, "root": self.root, "started_at": self.started_at,
                "question_id": self.question_id, "feedback_ids": self.feedback_ids,
                "decision_ids": self.decision_ids, "receipt_ids": self.receipt_ids,
                "broken": bool(self.problems), "problems": self.problems, "open": self.open}


def _check_decision(chain, d, ctx):
    """Append the decision's broken links / open reasons to chain."""
    did = d["id"]
    chain.decision_ids.append(did)
    missing = [i for i in d.get("feedback_ids") or [] if i not in ctx["feedback"]]
    if not d.get("feedback_ids") or missing:
        chain.problems.append(f"decision {did} cites feedback not in feedback.jsonl: {missing}")
    for c in d.get("rule_changes", []):
        rule = ctx["rules"].get(c["rule_id"])
        if rule is None:
            chain.problems.append(f"decision {did} rule {c['rule_id']} is missing from rules.json")
        elif rule.get("source_feedback_id") not in d.get("feedback_ids", []):
            chain.problems.append(f"decision {did} rule {c['rule_id']} is attributed to other feedback")

    decided = _at(d["decided_at"])
    receipts = ctx["receipts"].get(did, [])
    chain.receipt_ids.extend(r["id"] for r in receipts)
    for r in receipts:
        if r.get("verified_readback") is not True:
            chain.problems.append(f"receipt {r['id']} has no verified read-back")
        if _at(r["recorded_at"]) < decided:
            chain.problems.append(f"receipt {r['id']} was recorded before decision {did}")
        if d.get("rule_changes") and r["rule_version"] < d["rules_version"]:
            chain.problems.append(f"receipt {r['id']} used rules v{r['rule_version']}, "
                                  f"older than decision {did} (v{d['rules_version']})")
    applied = [r for r in receipts if r.get("verified_readback") is True
               and r["status"] in usage.APPLIED_STATUSES]
    if applied:
        return
    if d["pending"] is not None:
        chain.open.append(f"decision {did} is pending: {d['pending']['reason']}")
    elif ctx["now"] - decided <= GRACE:
        chain.open.append(f"decision {did} is within the {GRACE.days}-day grace period")
    elif receipts:
        statuses = sorted({r["status"] for r in receipts})
        chain.problems.append(f"decision {did} was never applied (receipts only: {', '.join(statuses)})")
    else:
        chain.problems.append(f"decision {did} has no usage receipt and is not pending")


def _check_feedback(chain, fid, ctx):
    """Follow feedback fid to its decisions."""
    chain.feedback_ids.append(fid)
    record = ctx["feedback"].get(fid)
    if record is None:
        chain.problems.append(f"feedback {fid} is not in feedback.jsonl")
        return
    ds = ctx["by_feedback"].get(fid, [])
    if not ds:
        if ctx["now"] - _at(_feedback_at(record)) <= GRACE:
            chain.open.append(f"feedback {fid} has no decision yet (within grace period)")
        else:
            chain.problems.append(f"feedback {fid} has no decision")
    for d in ds:
        if d["id"] not in ctx["seen_decisions"]:
            ctx["seen_decisions"].add(d["id"])
            _check_decision(chain, d, ctx)


def weekly(data_dir, as_of, audits_dir, now=None, language="zh"):
    """Run the weekly audit for the 7 days ending on as_of and write the report.

    Returns the report dict (also written to audits_dir/YYYY-Www.json and .md).
    report['passed'] is True only if no chain is broken.
    """
    if language not in _T:
        raise AuditError(f"language must be one of {tuple(_T)}")
    store = _store(data_dir)
    end = _day(as_of)
    start = end - datetime.timedelta(days=WINDOW_DAYS - 1)
    now = _now(now, end)

    def in_window(stamp):
        return start <= _at(stamp).date() <= end

    all_feedback = feedback.read_all(store)
    superseded = {r["supersedes"] for r in all_feedback if r.get("supersedes")}
    all_decisions = decisions.all_decisions(store)
    receipts = usage.read_all(store)
    by_decision = {}
    for r in receipts:
        for did in r.get("decision_ids", []):
            by_decision.setdefault(did, []).append(r)
    by_feedback = {}
    for d in all_decisions:
        for fid in d.get("feedback_ids") or []:
            by_feedback.setdefault(fid, []).append(d)
    responses = {}  # decision id -> [(feedback id, reaction)]
    for f in all_feedback:
        for did in sorted(set(_DECISION_ID.findall(f.get("text", "")))):
            responses.setdefault(did, []).append((f["id"], reaction(f["text"]), _feedback_at(f)))
    ctx = {"feedback": {r["id"]: r for r in all_feedback},
           "rules": {r["id"]: r for r in rules.read(store)["rules"]},
           "receipts": by_decision, "by_feedback": by_feedback, "now": now,
           "seen_decisions": set()}

    chains = []
    seen_feedback = set()
    for q in questions.all_questions(store):
        chain = _Chain("question", q["id"], q["asked_at"])
        chain.question_id = q["id"]
        if q["status"] == questions.OPEN:
            chain.open.append(f"question {q['id']} is waiting for a reply")
        elif q["status"] == questions.ANSWERED:
            if not q.get("feedback_id"):
                chain.problems.append(f"question {q['id']} is answered without a feedback_id")
            else:
                seen_feedback.add(q["feedback_id"])
                _check_feedback(chain, q["feedback_id"], ctx)
        chains.append(chain)

    for f in all_feedback:
        fid = f["id"]
        if fid in seen_feedback or fid in superseded:
            continue
        if (fid not in by_feedback and _DECISION_ID.search(f.get("text", ""))
                and reaction(f["text"]) != "objected"):
            continue  # a reply to a decision report, listed under reactions; objections need a decision
        seen_feedback.add(fid)
        chain = _Chain("feedback", fid, _feedback_at(f))
        _check_feedback(chain, fid, ctx)
        chains.append(chain)

    for d in all_decisions:
        if d["id"] in ctx["seen_decisions"]:
            continue
        ctx["seen_decisions"].add(d["id"])
        chain = _Chain("decision", d["id"], d["decided_at"])
        _check_decision(chain, d, ctx)
        chains.append(chain)

    def recent(chain):
        stamps = [chain.started_at]
        stamps += [d["decided_at"] for d in all_decisions if d["id"] in chain.decision_ids]
        stamps += [r["recorded_at"] for r in receipts if r["id"] in chain.receipt_ids]
        return any(in_window(s) for s in stamps)

    audited = [c for c in chains
               if _at(c.started_at).date() <= end and (recent(c) or c.problems or c.open)]

    audited_decisions = [d for d in all_decisions
                         if any(d["id"] in c.decision_ids for c in audited)]
    no_receipt = [{"decision_id": d["id"], "decided_at": d["decided_at"],
                   "pending": d["pending"]} for d in audited_decisions if not by_decision.get(d["id"])]
    unverified = [r["id"] for r in receipts
                  if r.get("verified_readback") is not True and in_window(r["recorded_at"])]
    for rid in unverified:
        if not any(rid in c.receipt_ids for c in audited):
            orphan = _Chain("receipt", rid, next(r["recorded_at"] for r in receipts if r["id"] == rid))
            orphan.receipt_ids.append(rid)
            orphan.problems.append(f"receipt {rid} has no verified read-back")
            audited.append(orphan)

    reactions = {"approved": [], "objected": [], "replied": [], "silence": []}
    for d in audited_decisions:
        later = [(fid, r) for fid, r, at in responses.get(d["id"], []) if _at(at) >= _at(d["decided_at"])]
        if not later:
            reactions["silence"].append(d["id"])
        else:
            fid, r = later[-1]
            reactions[r].append({"decision_id": d["id"], "feedback_id": fid})

    week_receipts = sorted((r for r in receipts if r.get("verified_readback") is True
                            and in_window(r["recorded_at"])), key=lambda r: r["id"])
    rng = random.Random(week_id(end))

    def sample(statuses):
        pool = [r for r in week_receipts if r["status"] in statuses]
        if not pool:
            return None
        r = rng.choice(pool)
        return {k: r[k] for k in ("id", "task", "target", "status", "before", "after",
                                  "decision_ids", "feedback_ids", "rule_version")}

    broken = [c for c in audited if c.problems]
    report = {
        "week": week_id(end),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "generated_at": now.isoformat(),
        "passed": not broken,
        "chains": [c.to_dict() for c in audited],
        "broken_chains": len(broken),
        "decisions_without_receipt": no_receipt,
        "receipts_without_readback": unverified,
        "samples": {"applied": sample(usage.APPLIED_STATUSES), "not_applicable": sample(("not_applicable",))},
        "reactions": reactions,
    }
    os.makedirs(audits_dir, exist_ok=True)
    base = os.path.join(audits_dir, report["week"])
    write_text_atomic(base + ".json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text_atomic(base + ".md", render(report, language))
    return report


_T = {
    "zh": {
        "title": "# 每周落实审计 {week}（{start} – {end}）",
        "passed": "结论：通过。所有链条都完整，并且决定真的被用上了。",
        "failed": "结论：未通过。有 {n} 条链条断了。",
        "note": "结构完整不等于通过：每个决定都要有已核验、真正应用（changed / confirmed）的回执。",
        "broken": "## 断链", "open": "## 未闭环（不算断）", "none": "无。",
        "no_receipt": "## 没有回执的决定", "pending": "（已标记暂缓：{r}）",
        "unverified": "## 没有读回核验的回执",
        "samples": "## 抽查", "applied": "已应用", "not_applicable": "不适用",
        "sample": "- {label}：{id}（{task} → {target}，{status}）\n  原判断：{before}\n  新判断：{after}",
        "no_sample": "- {label}：本周没有可抽查的回执。",
        "reactions": "## 你对决定的态度",
        "approved": "明确赞成", "objected": "明确反对", "replied": "回复了但没表态",
        "silence": "没有回应（沉默不算赞成）",
    },
    "en": {
        "title": "# Weekly implementation audit {week} ({start} – {end})",
        "passed": "Result: passed. Every chain is complete and every decision was really applied.",
        "failed": "Result: failed. {n} chain(s) are broken.",
        "note": "Structural completeness is not a pass: every decision needs a verified receipt "
                "that really applied it (changed / confirmed).",
        "broken": "## Broken chains", "open": "## Still open (not broken)", "none": "None.",
        "no_receipt": "## Decisions without a receipt", "pending": " (marked pending: {r})",
        "unverified": "## Receipts without verified read-back",
        "samples": "## Spot checks", "applied": "applied", "not_applicable": "not applicable",
        "sample": "- {label}: {id} ({task} -> {target}, {status})\n  before: {before}\n  after: {after}",
        "no_sample": "- {label}: no receipt to sample this week.",
        "reactions": "## Your reaction to decisions",
        "approved": "explicitly approved", "objected": "explicitly objected",
        "replied": "replied without approving or objecting",
        "silence": "no reply (silence is not approval)",
    },
}


def render(report, language="zh"):
    """Markdown text of a report."""
    if language not in _T:
        raise AuditError(f"language must be one of {tuple(_T)}")
    t = _T[language]
    lines = [t["title"].format(week=report["week"], start=report["from"], end=report["to"]), ""]
    lines.append(t["passed"] if report["passed"] else t["failed"].format(n=report["broken_chains"]))
    lines += [t["note"], "", t["broken"]]
    broken = [p for c in report["chains"] for p in c["problems"]]
    lines += [f"- {p}" for p in broken] or [t["none"]]
    lines += ["", t["open"]]
    lines += [f"- {o}" for c in report["chains"] for o in c["open"]] or [t["none"]]
    lines += ["", t["no_receipt"]]
    lines += [f"- {d['decision_id']}" + (t["pending"].format(r=d["pending"]["reason"]) if d["pending"] else "")
              for d in report["decisions_without_receipt"]] or [t["none"]]
    lines += ["", t["unverified"]]
    lines += [f"- {r}" for r in report["receipts_without_readback"]] or [t["none"]]
    lines += ["", t["samples"]]
    for key in ("applied", "not_applicable"):
        s = report["samples"][key]
        lines.append(t["sample"].format(label=t[key], **s) if s else t["no_sample"].format(label=t[key]))
    lines += ["", t["reactions"]]
    for key in ("approved", "objected", "replied"):
        items = report["reactions"][key]
        lines.append(f"- {t[key]}：" if language == "zh" else f"- {t[key]}: ")
        lines[-1] += ", ".join(f"{i['decision_id']} ({i['feedback_id']})" for i in items) or "-"
    silence = ", ".join(report["reactions"]["silence"]) or "-"
    lines.append((f"- {t['silence']}：" if language == "zh" else f"- {t['silence']}: ") + silence)
    return "\n".join(lines) + "\n"
