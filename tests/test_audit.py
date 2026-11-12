import datetime
import json
import os
import tempfile
import unittest

from etrace.container import audit, decisions, feedback, questions, rules, usage, validate
from etrace.container.audit import AuditError
from etrace.store import Store

TZ = datetime.timezone(datetime.timedelta(hours=-5))
AS_OF = "2026-10-11"  # a Sunday; window 2026-10-05 .. 2026-10-11
NOW = datetime.datetime(2026, 10, 11, 22, 15, tzinfo=TZ)


def at(day, hour=9):
    return datetime.datetime(2026, 10, day, hour, 0, tzinfo=TZ)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self._tmp.name, "data")
        self.audits = os.path.join(self._tmp.name, "audits")
        os.makedirs(self.data)

    def tearDown(self):
        self._tmp.cleanup()

    def chain(self, day=6, key="pad-evening", answer="[{qid}] 晚上那段算平静，不是茫然", decide=True,
              receipt="changed", receipt_day=None, rule_id=None):
        q = questions.add_question(
            self.data, "journal", f"2026-10-{day:02d}", {"kind": "quote", "text": "晚上我就是坐着"},
            ["茫然", "平静"], "放空还是安静", ["pad.A"], key, now=at(day))
        if answer is None:
            return q, None, None, None
        text = answer.format(qid=q["id"])
        feedback.ingest_note(self.data, f"2026-10-{day:02d}", [text], now=at(day, 12))
        fid = [r for r in feedback.read_all(self.data) if r["text"] == text][0]["id"]
        questions.answer(self.data, q["id"], fid, now=at(day, 12))
        if not decide:
            return q, fid, None, None
        rid = rule_id or f"R-{key}"
        d = decisions.decide(self.data, [fid], "把晚上独处评为平静", "agent_decision", now=at(day, 13),
                             rule_changes=[dict(rule_id=rid, scope=f"pad.{key}", text="安静记为平静",
                                                expressed_at=at(day, 12).isoformat())])
        u = None
        if receipt:
            u = usage.record(self.data, "daily", f"journal/2026-10-{day:02d}.md#pad", rules.version(self.data),
                             [fid], [d["id"]], "茫然", "平静" if receipt == "changed" else "茫然",
                             receipt, True, now=at(receipt_day or day, 19))
        return q, fid, d, u

    def run_audit(self, **kw):
        return audit.weekly(self.data, kw.pop("as_of", AS_OF), self.audits, now=kw.pop("now", NOW), **kw)

    def problems(self, report):
        return [p for c in report["chains"] for p in c["problems"]]


class WeeklyTest(Base):
    def test_complete_applied_chain_passes_and_writes_files(self):
        self.chain()
        report = self.run_audit()
        self.assertTrue(report["passed"], self.problems(report))
        self.assertEqual(report["week"], "2026-W41")
        self.assertEqual(len(report["chains"]), 1)
        c = report["chains"][0]
        self.assertEqual(c["kind"], "question")
        self.assertEqual(len(c["feedback_ids"]), 1)
        self.assertEqual(len(c["decision_ids"]), 1)
        self.assertEqual(len(c["receipt_ids"]), 1)
        md = os.path.join(self.audits, "2026-W41.md")
        js = os.path.join(self.audits, "2026-W41.json")
        self.assertTrue(os.path.exists(md))
        with open(js, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["passed"], True)
        with open(md, encoding="utf-8") as fh:
            self.assertIn("通过", fh.read())

    def test_structural_completeness_alone_is_not_a_pass(self):
        # validate() is happy: every link exists and the decision has a verified receipt...
        self.chain(receipt="not_applicable")
        self.assertTrue(validate.validate(self.data, now=NOW).ok)
        # ...but the decision was never really applied.
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any("never applied" in p for p in self.problems(report)))
        with open(os.path.join(self.audits, "2026-W41.md"), encoding="utf-8") as fh:
            self.assertIn("未通过", fh.read())

    def test_answered_feedback_without_decision_is_broken(self):
        self.chain(decide=False)
        self.assertTrue(validate.validate(self.data, now=NOW).ok)
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any("has no decision" in p for p in self.problems(report)))

    def test_feedback_without_decision_within_grace_is_open(self):
        self.chain(day=11, decide=False)
        report = self.run_audit()
        self.assertTrue(report["passed"])
        self.assertTrue(any("grace" in o for c in report["chains"] for o in c["open"]))

    def test_decision_with_no_receipt(self):
        _, _, d, _ = self.chain(receipt=None)
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual([x["decision_id"] for x in report["decisions_without_receipt"]], [d["id"]])
        self.assertTrue(any("no usage receipt" in p for p in self.problems(report)))

    def test_pending_decision_is_open_not_broken(self):
        _, _, d, _ = self.chain(receipt=None)
        decisions.mark_pending(self.data, d["id"], "下次日报才会用到", now=at(8))
        report = self.run_audit()
        self.assertTrue(report["passed"])
        self.assertEqual(report["decisions_without_receipt"][0]["pending"]["reason"], "下次日报才会用到")

    def test_receipt_on_older_rules_is_broken(self):
        _, fid, d, _ = self.chain(receipt=None)
        usage.record(self.data, "daily", "journal/2026-10-06.md#pad", d["rules_version"] - 1, [fid], [d["id"]],
                     "茫然", "平静", "changed", True, now=at(7, 19))
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any("older than decision" in p for p in self.problems(report)))

    def test_receipt_without_readback_is_reported(self):
        _, _, d, _ = self.chain()
        store = Store(self.data)
        with store.lock():
            store.append_locked("usage", {"id": "U-20261008-9", "task": "g", "target": "g", "rule_version": 1,
                                          "feedback_ids": [], "decision_ids": [d["id"]], "before": 1,
                                          "after": 2, "status": "changed", "verified_readback": False,
                                          "recorded_at": at(8).isoformat()})
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["receipts_without_readback"], ["U-20261008-9"])

    def test_answered_question_with_missing_feedback_is_broken(self):
        q, _, _, _ = self.chain(answer=None)
        store = Store(self.data)
        with store.lock():
            store.append_locked("questions", {"id": f"{q['id']}:answered", "type": "answered",
                                              "question_id": q["id"], "feedback_id": "gone", "at": at(7).isoformat()})
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any("gone" in p for p in self.problems(report)))

    def test_old_closed_chains_are_skipped_old_open_ones_kept(self):
        self.chain(day=1, key="old-closed")
        q, _, _, _ = self.chain(day=2, key="old-open", answer=None)
        self.chain(day=3, key="old-broken", receipt=None)
        report = self.run_audit()
        roots = {c["root"] for c in report["chains"]}
        self.assertNotIn("Q-20261001-1", roots)
        self.assertIn(q["id"], roots)
        self.assertIn("Q-20261003-1", roots)
        self.assertFalse(report["passed"])

    def test_samples_one_applied_and_one_not_applicable(self):
        _, fid, d, _ = self.chain()
        usage.record(self.data, "g", "g/2026-10-07", rules.version(self.data), [fid], [d["id"]],
                     "n/a", "n/a", "not_applicable", True, now=at(7, 20))
        report = self.run_audit()
        self.assertEqual(report["samples"]["applied"]["status"], "changed")
        self.assertEqual(report["samples"]["not_applicable"]["status"], "not_applicable")
        self.assertEqual(report, self.run_audit())  # deterministic for the same week

    def test_missing_samples_are_reported_as_none(self):
        report = self.run_audit()
        self.assertTrue(report["passed"])
        self.assertIsNone(report["samples"]["applied"])
        self.assertIsNone(report["samples"]["not_applicable"])
        with open(os.path.join(self.audits, "2026-W41.md"), encoding="utf-8") as fh:
            self.assertIn("没有可抽查", fh.read())

    def test_approval_and_silence_are_reported_separately(self):
        _, _, d1, _ = self.chain(day=6, key="a")
        _, _, d2, _ = self.chain(day=7, key="b")
        _, _, d3, _ = self.chain(day=8, key="c")
        feedback.ingest_note(self.data, "2026-10-09", [f"{d1['id']} 我赞成", f"{d3['id']} 不同意，晚上其实很累"],
                             now=at(9, 12))
        decisions.decide(self.data, [r["id"] for r in feedback.read_all(self.data) if "不同意" in r["text"]],
                         "撤回", "user", now=at(9, 13))
        report = self.run_audit(language="en")
        r = report["reactions"]
        self.assertEqual([x["decision_id"] for x in r["approved"]], [d1["id"]])
        self.assertEqual([x["decision_id"] for x in r["objected"]], [d3["id"]])
        self.assertIn(d2["id"], r["silence"])
        self.assertNotIn(d1["id"], r["silence"])
        with open(os.path.join(self.audits, "2026-W41.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("explicitly approved", text)
        self.assertIn("silence is not approval", text)

    def test_reaction_words(self):
        self.assertEqual(audit.reaction("D-20261006-1 我不同意"), "objected")
        self.assertEqual(audit.reaction("D-20261006-1 I disagree"), "objected")
        self.assertEqual(audit.reaction("D-20261006-1 agree"), "approved")
        self.assertEqual(audit.reaction("D-20261006-1 同意"), "approved")
        self.assertEqual(audit.reaction("D-20261006-1 我再想想"), "replied")

    def test_negated_or_hedged_approval_is_never_approved(self):
        for text in ("I do not agree", "not ok", "我不认可", "I don't agree", "不是很赞同", "这个不太对", "这个不行"):
            self.assertEqual(audit.reaction(f"D-20261006-1 {text}"), "objected", text)
        for text in ("Yes but no", "同意，不过第二条要改", "同意，不用改了", "what does this mean"):
            self.assertEqual(audit.reaction(f"D-20261006-1 {text}"), "replied", text)
        for text in ("我同意", "没问题", "OK", "sounds good", "approve"):
            self.assertEqual(audit.reaction(f"D-20261006-1 {text}"), "approved", text)

    def test_rule_without_decision_record_is_broken_in_audit_and_validate(self):
        q, fid, d, u = self.chain()
        rules.add_rule(self.data, "R-orphan", "pad.orphan", "an interrupted decision left this",
                       at(7, 12).isoformat(), source_feedback_id=fid)
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any("R-orphan" in p and "no decision record" in p for p in self.problems(report)))
        result = validate.validate(self.data, now=NOW)
        self.assertFalse(result.ok)
        self.assertTrue(any("R-orphan" in p for p in result.problems))

    def test_starter_rules_without_source_are_not_orphans(self):
        rules.add_rule(self.data, "base-1", "principle:verbatim", "starter rule", at(5).isoformat())
        self.assertTrue(validate.validate(self.data, now=NOW).ok)
        self.assertTrue(self.run_audit()["passed"])

    def pair(self, same_decision=False, reason="low days only", prefix="narrative"):
        """Two active rules in sibling scopes; returns their decision ids."""
        ids = []
        for day, text in ((5, "Keep it short."), (6, "On bad days I want more.")):
            feedback.ingest_note(self.data, f"2026-10-{day:02d}", [text], now=at(day, 12))
            ids.append([r for r in feedback.read_all(self.data) if r["text"] == text][0]["id"])
        short = dict(rule_id="narrative-length", scope=f"{prefix}:length", text="Three or four sentences.",
                     expressed_at=at(5, 12).isoformat())
        more = dict(rule_id="bad-day-detail", scope=f"{prefix}:bad-days", text="Longer on low days.",
                    expressed_at=at(6, 12).isoformat(), scope_reason=reason)
        if same_decision:
            d = decisions.decide(self.data, ids, "both", "user", now=at(6, 13),
                                 rule_changes=[short, dict(more, source_feedback_id=ids[1])])
            return d["id"], d["id"]
        d1 = decisions.decide(self.data, [ids[0]], "short", "user", now=at(5, 13), rule_changes=[short])
        d2 = decisions.decide(self.data, [ids[1]], "more", "agent_decision", now=at(6, 13), rule_changes=[more])
        return d1["id"], d2["id"]

    def receipt(self, did, day, status, target=None):
        return usage.record(self.data, "daily", target or f"journal/2026-10-{day:02d}.md#ai:narrative",
                            rules.version(self.data), [], [did], "four sentences",
                            "nine sentences" if status == "changed" else "four sentences", status, True,
                            now=at(day, 19))

    def test_sibling_rules_cited_for_one_output_are_listed_not_broken(self):
        short, more = self.pair()
        self.receipt(short, 7, "confirmed")                 # a good day: only the short rule is cited
        self.receipt(more, 8, "changed")                    # a bad day: the agent had to pick
        self.receipt(short, 8, "not_applicable")
        report = self.run_audit(language="en")
        self.assertTrue(report["passed"], self.problems(report))
        self.assertEqual(report["scope_overlaps"], [{
            "rule_ids": ["bad-day-detail", "narrative-length"],
            "scopes": ["narrative:bad-days", "narrative:length"],
            "outputs": ["daily -> journal/2026-10-08.md#ai:narrative"],
            "only_one_applied": 1, "scope_reason": "low days only"}])
        with open(os.path.join(self.audits, "2026-W41.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("## Rules under one prefix cited for the same output", md)
        self.assertIn("narrative:bad-days (bad-day-detail) and narrative:length (narrative-length): "
                      "cited together for 1 output(s); on 1 only one of the two was applied.", md)
        self.assertIn("reason given for the new scope: low days only", md)

    def test_one_receipt_naming_both_decisions_is_an_overlap(self):
        short, more = self.pair()
        usage.record(self.data, "daily", "journal/2026-10-08.md#ai:narrative", rules.version(self.data),
                     [], [short, more], "four sentences", "nine sentences", "changed", True, now=at(8, 19))
        overlaps = self.run_audit()["scope_overlaps"]
        self.assertEqual([(p["rule_ids"], p["only_one_applied"]) for p in overlaps],
                         [(["bad-day-detail", "narrative-length"], 0)])

    def test_no_overlap_without_a_shared_output(self):
        short, more = self.pair()
        self.receipt(short, 7, "confirmed")
        self.receipt(more, 8, "changed")
        report = self.run_audit()
        self.assertEqual(report["scope_overlaps"], [])
        with open(os.path.join(self.audits, "2026-W41.md"), encoding="utf-8") as fh:
            self.assertNotIn("同一前缀", fh.read())

    def test_rules_from_one_decision_are_not_an_overlap(self):
        d, _ = self.pair(same_decision=True)
        self.receipt(d, 8, "changed")
        self.assertEqual(self.run_audit()["scope_overlaps"], [])

    def test_overlap_ignores_other_prefixes_superseded_rules_and_older_weeks(self):
        short, more = self.pair()
        _, _, other, _ = self.chain(day=8, receipt=None)    # scope pad.pad-evening: another prefix
        self.receipt(other["id"], 8, "changed")
        self.receipt(more, 8, "changed")
        self.receipt(short, 8, "blocked")
        self.assertEqual(len(self.run_audit()["scope_overlaps"]), 1)
        # the same receipts are outside next week's window
        later = self.run_audit(as_of="2026-10-18", now=datetime.datetime(2026, 10, 18, 22, 15, tzinfo=TZ))
        self.assertEqual(later["scope_overlaps"], [])
        # once the old rule is replaced, the pair is gone
        feedback.ingest_note(self.data, "2026-10-09", ["Always long."], now=at(9, 12))
        fid = [r for r in feedback.read_all(self.data) if r["text"] == "Always long."][0]["id"]
        decisions.decide(self.data, [fid], "long", "user", now=at(9, 13), rule_changes=[
            dict(rule_id="narrative-long", scope="narrative:length", text="Always long.",
                 expressed_at=at(9, 12).isoformat())])
        self.assertEqual(self.run_audit()["scope_overlaps"], [])

    def test_bad_input(self):
        with self.assertRaises(AuditError):
            audit.weekly(self.data, "2026-13-01", self.audits)
        with self.assertRaises(AuditError):
            audit.weekly(self.data, AS_OF, self.audits, now=datetime.datetime(2026, 10, 11))
        with self.assertRaises(AuditError):
            audit.weekly(self.data, AS_OF, self.audits, now=NOW, language="fr")


if __name__ == "__main__":
    unittest.main()
