import datetime
import json
import os
import tempfile
import unittest

from etrace.container import decisions, feedback, questions, rules, usage, validate
from etrace.container.decisions import DecisionError
from etrace.container.usage import UsageError

TZ = datetime.timezone(datetime.timedelta(hours=-5))
NOW = datetime.datetime(2026, 10, 7, 9, 0, tzinfo=TZ)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def feedback_id(self, text="晚上那段算平静，不是茫然", date="2026-10-06"):
        feedback.ingest_note(self.dir, date, [text], now=NOW)
        return [r for r in feedback.current(self.dir) if r["text"] == text][0]["id"]

    def change(self, **over):
        c = dict(rule_id="R-pad-evening", scope="pad.evening",
                 text="晚上独处的安静记为平静", expressed_at="2026-10-06T21:00:00-05:00")
        c.update(over)
        return c

    def decide(self, fid=None, now=NOW, **over):
        kw = dict(decision_text="把晚上独处评为平静", value_source="agent_decision",
                  rule_changes=[self.change()])
        kw.update(over)
        return decisions.decide(self.dir, [fid or self.feedback_id()], now=now, **kw)


class DecisionsTest(Base):
    def test_decide_appends_and_applies_rules(self):
        fid = self.feedback_id()
        d = self.decide(fid)
        self.assertEqual(d["id"], "D-20261007-1")
        self.assertEqual(d["feedback_ids"], [fid])
        self.assertEqual(d["value_source"], "agent_decision")
        self.assertEqual(d["rules_version"], 1)
        self.assertEqual(d["rule_changes"][0]["status"], "active")
        rule = rules.get(self.dir, "R-pad-evening")
        self.assertEqual(rule["source_feedback_id"], fid)
        self.assertEqual(decisions.read_all(self.dir)[0]["id"], d["id"])

    def test_rule_change_supersedes_with_reason(self):
        fid = self.feedback_id()
        self.decide(fid)
        fid2 = self.feedback_id("还是算淡定吧")
        d2 = decisions.decide(self.dir, [fid2], "改为淡定", "user", now=NOW, rule_changes=[
            self.change(rule_id="R-pad-evening-2", text="记为淡定",
                        expressed_at="2026-10-07T08:00:00-05:00", reason="用户改口")])
        self.assertEqual(d2["id"], "D-20261007-2")
        old = rules.get(self.dir, "R-pad-evening")
        self.assertEqual((old["status"], old["superseded_by"], old["superseded_reason"]),
                         ("superseded", "R-pad-evening-2", "用户改口"))

    def test_decision_without_rule_changes(self):
        d = self.decide(rule_changes=None)
        self.assertEqual(d["rule_changes"], [])
        self.assertEqual(rules.version(self.dir), 0)

    def test_requires_existing_feedback(self):
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, [], "x", "user", now=NOW)
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, ["2026-10-06:feedback:nope"], "x", "user", now=NOW)
        self.assertEqual(decisions.read_all(self.dir), [])

    def test_value_source_and_text_validated(self):
        fid = self.feedback_id()
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, [fid], "x", "model", now=NOW)
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, [fid], " ", "user", now=NOW)

    def test_bad_rule_change_applies_nothing(self):
        fid = self.feedback_id()
        bad = [self.change(), self.change(rule_id="R-2", source_feedback_id="other")]
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, [fid], "x", "user", rule_changes=bad, now=NOW)
        with self.assertRaises(DecisionError):
            decisions.decide(self.dir, [fid], "x", "user", rule_changes=[{"rule_id": "R"}], now=NOW)
        self.assertEqual(rules.version(self.dir), 0)
        self.assertEqual(decisions.read_all(self.dir), [])

    def test_mark_pending(self):
        d = self.decide()
        p = decisions.mark_pending(self.dir, d["id"], "等明天日报再用", now=NOW)
        self.assertEqual(p["pending"]["reason"], "等明天日报再用")
        with self.assertRaises(DecisionError):
            decisions.mark_pending(self.dir, "D-19990101-1", "x", now=NOW)


class UsageTest(Base):
    def receipt(self, d, **over):
        kw = dict(task="daily", target="journal/2026-10-07.md#pad", rule_version=1,
                  feedback_ids=d["feedback_ids"], decision_ids=[d["id"]],
                  before="茫然", after="平静", status="changed", verified_readback=True)
        kw.update(over)
        return usage.record(self.dir, now=NOW, **kw)

    def test_record_and_applied(self):
        d = self.decide()
        r = self.receipt(d)
        self.assertEqual(r["id"], "U-20261007-1")
        self.assertTrue(r["verified_readback"])
        self.assertTrue(usage.applied(self.dir, d["id"]))
        self.assertEqual(len(usage.receipts_for(self.dir, d["id"])), 1)

    def test_verified_readback_must_be_true(self):
        d = self.decide()
        for value in (False, None, 1, "yes"):
            with self.assertRaises(UsageError):
                self.receipt(d, verified_readback=value)
        self.assertEqual(usage.read_all(self.dir), [])

    def test_status_and_fields_validated(self):
        d = self.decide()
        with self.assertRaises(UsageError):
            self.receipt(d, status="applied")
        with self.assertRaises(UsageError):
            self.receipt(d, status="changed", after="茫然")
        with self.assertRaises(UsageError):
            self.receipt(d, rule_version=5)
        with self.assertRaises(UsageError):
            self.receipt(d, decision_ids=["D-19990101-1"])
        with self.assertRaises(UsageError):
            self.receipt(d, feedback_ids=[], decision_ids=[])
        with self.assertRaises(UsageError):
            self.receipt(d, task="")

    def test_all_statuses_accepted(self):
        d = self.decide()
        for status in usage.STATUSES:
            self.receipt(d, status=status, after="平静" if status == "changed" else "茫然")
        self.assertEqual(len(usage.read_all(self.dir)), 5)

    def test_non_applied_statuses_do_not_count(self):
        d = self.decide()
        for status in ("no_match", "not_applicable", "blocked"):
            self.receipt(d, status=status, after="茫然")
        self.assertFalse(usage.applied(self.dir, d["id"]))


class ValidateTest(Base):
    def test_clean_chain_passes(self):
        fid = self.feedback_id()
        d = self.decide(fid)
        usage.record(self.dir, "daily", "journal/2026-10-07.md#pad", 1, [fid], [d["id"]],
                     "茫然", "平静", "changed", True, now=NOW)
        result = validate.validate(self.dir, now=NOW + datetime.timedelta(days=5))
        self.assertTrue(result.ok, result.problems)

    def test_reading_rules_without_receipt_is_not_applied(self):
        d = self.decide()
        # A task reads the active rules (and even the decision) but writes no receipt.
        self.assertEqual(len(rules.active_rules(self.dir)), 1)
        decisions.get(self.dir, d["id"])
        self.assertFalse(usage.applied(self.dir, d["id"]))
        self.assertEqual(usage.receipts_for(self.dir, d["id"]), [])
        result = validate.validate(self.dir, now=NOW + datetime.timedelta(days=3))
        self.assertFalse(result.ok)
        self.assertIn("no usage receipt", result.problems[0])

    def test_two_day_window_and_pending(self):
        d = self.decide()
        self.assertTrue(validate.validate(self.dir, now=NOW + datetime.timedelta(days=2)).ok)
        self.assertFalse(validate.validate(self.dir, now=NOW + datetime.timedelta(days=2, seconds=1)).ok)
        decisions.mark_pending(self.dir, d["id"], "等用户确认", now=NOW)
        self.assertTrue(validate.validate(self.dir, now=NOW + datetime.timedelta(days=9)).ok)

    def test_decision_without_source_feedback_fails(self):
        self.decide()
        path = os.path.join(self.dir, "decisions.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "D-20261007-9", "type": "decision", "feedback_ids": [],
                                 "decision": "x", "value_source": "user", "rule_changes": [],
                                 "rules_version": 1, "decided_at": NOW.isoformat()}) + "\n")
        result = validate.validate(self.dir, now=NOW)
        self.assertEqual(result.problems, ["decision D-20261007-9 lacks a stored source feedback"])

    def test_answered_question_without_feedback_fails(self):
        q = questions.add_question(
            self.dir, "journal", "2026-10-06", {"kind": "quote", "text": "晚上有点空"},
            ["平静", "茫然"], "晚上的空是哪种", ["pad.P"], "k1", now=NOW)
        with open(os.path.join(self.dir, "questions.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": f"{q['id']}:answered", "type": "answered",
                                 "question_id": q["id"], "at": NOW.isoformat()}) + "\n")
        result = validate.validate(self.dir, now=NOW)
        self.assertEqual(len(result.problems), 1)
        self.assertIn(q["id"], result.problems[0])

    def test_unverified_receipt_on_disk_fails(self):
        d = self.decide()
        with open(os.path.join(self.dir, "usage.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "U-x", "decision_ids": [d["id"]], "status": "changed",
                                 "verified_readback": False}) + "\n")
        result = validate.validate(self.dir, now=NOW + datetime.timedelta(days=3))
        self.assertFalse(usage.applied(self.dir, d["id"]))
        self.assertEqual(len(result.problems), 2)


class ScopeNamingTest(Base):
    """A new scope next to active rules under one prefix must say why it is new."""

    LENGTH = dict(rule_id="narrative-length", scope="narrative:length",
                  text="Keep the daily narrative to three or four sentences.",
                  expressed_at="2026-09-20T21:00:00-05:00")
    BAD_DAYS = dict(rule_id="bad-day-detail", scope="narrative:bad-days",
                    text="On low days, write a longer narrative with detail.",
                    expressed_at="2026-10-06T21:00:00-05:00")

    def setUp(self):
        super().setUp()
        self.first = self.feedback_id("Keep it short.", date="2026-09-20")
        decisions.decide(self.dir, [self.first], "short narrative", "user", now=NOW,
                         rule_changes=[dict(self.LENGTH)])
        self.fid = self.feedback_id("On bad days I want more. Don't cut it short.")

    def second(self, *changes):
        return decisions.decide(self.dir, [self.fid], "more on bad days", "agent_decision", now=NOW,
                                rule_changes=list(changes))

    def test_new_sibling_scope_without_reason_is_refused_and_lists_the_rules(self):
        with self.assertRaises(DecisionError) as cm:
            self.second(dict(self.BAD_DAYS))
        msg = str(cm.exception)
        for part in ("narrative:bad-days", "narrative:length", "narrative-length",
                     "three or four sentences", "scope_reason", "do not guess"):
            self.assertIn(part, msg)
        # nothing was written: one rule, one decision, the feedback still has no decision
        self.assertEqual(rules.version(self.dir), 1)
        self.assertEqual(len(decisions.all_decisions(self.dir)), 1)

    def test_reason_is_kept_in_the_record_with_the_scopes_it_was_checked_against(self):
        d = self.second(dict(self.BAD_DAYS, scope_reason="only low days; the short rule covers the rest"))
        change = d["rule_changes"][0]
        self.assertEqual(change["sibling_scopes"], ["narrative:length"])
        self.assertEqual(change["scope_reason"], "only low days; the short rule covers the rest")
        self.assertEqual(decisions.get(self.dir, d["id"])["rule_changes"][0]["scope_reason"],
                         change["scope_reason"])
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)], ["bad-day-detail", "narrative-length"])

    def test_reusing_the_scope_replaces_and_needs_no_reason(self):
        d = self.second(dict(self.BAD_DAYS, scope="narrative:length"))
        self.assertNotIn("scope_reason", d["rule_changes"][0])
        self.assertNotIn("sibling_scopes", d["rule_changes"][0])
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)], ["bad-day-detail"])
        self.assertEqual(rules.get(self.dir, "narrative-length")["superseded_by"], "bad-day-detail")

    def test_reason_on_a_scope_that_is_not_new_is_refused(self):
        with self.assertRaises(DecisionError) as cm:
            self.second(dict(self.BAD_DAYS, scope="narrative:length", scope_reason="a new idea"))
        self.assertIn("is not new", str(cm.exception))
        self.assertIn("narrative-length", str(cm.exception))
        self.assertEqual(rules.version(self.dir), 1)

    def test_narrowing_rewrites_the_old_rule_in_the_same_decision(self):
        d = self.second(
            dict(self.LENGTH, rule_id="narrative-length-2", expressed_at="2026-10-06T21:00:00-05:00",
                 text="Keep the narrative to three or four sentences, except on low days."),
            dict(self.BAD_DAYS, scope_reason="low days only; the length rule now excludes them"))
        self.assertEqual([c["status"] for c in d["rule_changes"]], ["active", "active"])
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)],
                         ["bad-day-detail", "narrative-length-2"])
        self.assertEqual(rules.get(self.dir, "narrative-length")["status"], "superseded")

    def test_changes_in_one_decision_count_as_siblings_of_each_other(self):
        fid = self.feedback_id("two things about questions")
        one = dict(rule_id="q-low", scope="questions:low-energy", text="Ask less when I am tired.",
                   expressed_at="2026-10-06T21:00:00-05:00")
        two = dict(rule_id="q-tired", scope="questions:tired", text="Ask about sleep first.",
                   expressed_at="2026-10-06T21:00:00-05:00")
        with self.assertRaises(DecisionError) as cm:
            decisions.decide(self.dir, [fid], "x", "user", now=NOW, rule_changes=[one, two])
        self.assertIn("questions:low-energy", str(cm.exception))
        self.assertEqual(rules.version(self.dir), 1)   # the first change was not applied either
        d = decisions.decide(self.dir, [fid], "x", "user", now=NOW,
                             rule_changes=[one, dict(two, scope_reason="what to ask, not how much")])
        self.assertNotIn("sibling_scopes", d["rule_changes"][0])
        self.assertEqual(d["rule_changes"][1]["sibling_scopes"], ["questions:low-energy"])

    def test_new_prefix_needs_no_reason_and_a_dot_is_not_a_prefix(self):
        d = self.second(dict(self.BAD_DAYS, scope="pad:self-word"))
        self.assertNotIn("sibling_scopes", d["rule_changes"][0])
        fid = self.feedback_id("evenings")
        decisions.decide(self.dir, [fid], "x", "user", now=NOW, rule_changes=[
            self.change(), self.change(rule_id="R-pad-night", scope="pad.night")])
        self.assertEqual(rules.prefix("narrative:length"), "narrative")
        self.assertEqual(rules.prefix("pad.evening"), "pad.evening")

    def test_superseded_rules_are_not_siblings(self):
        self.second(dict(self.BAD_DAYS, scope="narrative:length"))   # narrative-length is history now
        fid = self.feedback_id("and a calm tone")
        with self.assertRaises(DecisionError) as cm:
            decisions.decide(self.dir, [fid], "x", "user", now=NOW, rule_changes=[
                dict(rule_id="tone", scope="narrative:tone", text="Calm tone.",
                     expressed_at="2026-10-07T08:00:00-05:00")])
        self.assertIn("[bad-day-detail]", str(cm.exception))
        self.assertNotIn("[narrative-length]", str(cm.exception))

    def test_blank_reason_is_refused(self):
        with self.assertRaises(DecisionError):
            self.second(dict(self.BAD_DAYS, scope_reason="  "))
        with self.assertRaises(DecisionError):
            self.second(dict(self.BAD_DAYS, scope_reason=3))


if __name__ == "__main__":
    unittest.main()
