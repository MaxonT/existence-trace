import json
import os
import tempfile
import unittest

from etrace.container import rules
from etrace.container.rules import RuleError
from etrace.store import Store


class RulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, rid, scope, when, text=None, fb=None):
        return rules.add_rule(self.dir, rid, scope, text or f"text {rid}", when, fb)

    def file(self):
        with open(os.path.join(self.dir, "rules.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_empty(self):
        self.assertEqual(rules.active_rules(self.dir), [])
        self.assertEqual(rules.version(self.dir), 0)

    def test_newer_rule_supersedes_same_scope(self):
        self.add("r1", "pad.focus", "2026-10-01T09:00:00-05:00", fb="fb-1")
        self.add("r2", "pad.focus", "2026-10-03T09:00:00-05:00", "专注时 A 给中档", fb="fb-2")
        active = rules.active_rules(self.dir)
        self.assertEqual([r["id"] for r in active], ["r2"])
        self.assertEqual(active[0]["source_feedback_id"], "fb-2")
        old = rules.get(self.dir, "r1")
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], "r2")
        self.assertTrue(old["superseded_reason"])
        self.assertEqual([r["id"] for r in rules.history(self.dir, "r1")], ["r1", "r2"])
        self.assertEqual(rules.history(self.dir, "r2"), rules.history(self.dir, "r1"))

    def test_custom_reason_is_kept(self):
        self.add("r1", "s", "2026-10-01T09:00:00+00:00")
        rules.add_rule(self.dir, "r2", "s", "t", "2026-10-02T09:00:00+00:00", "fb", reason="user changed mind")
        self.assertEqual(rules.get(self.dir, "r1")["superseded_reason"], "user changed mind")

    def test_earlier_expressed_rule_added_later_does_not_supersede(self):
        self.add("new", "g.weights", "2026-10-05T20:00:00-05:00")
        late = self.add("old", "g.weights", "2026-10-02T20:00:00-05:00")
        self.assertEqual(late["status"], "superseded")
        self.assertEqual(late["superseded_by"], "new")
        self.assertIn("expressed before", late["superseded_reason"])
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)], ["new"])
        self.assertEqual([r["id"] for r in rules.history(self.dir, "new")], ["old", "new"])

    def test_expressed_at_compares_across_offsets(self):
        self.add("a", "s", "2026-10-02T01:00:00+00:00")
        # 2026-10-01T21:00-05:00 == 2026-10-02T02:00Z: later, so it wins
        self.add("b", "s", "2026-10-01T21:00:00-05:00")
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)], ["b"])

    def test_different_scopes_coexist(self):
        self.add("p", "pad.focus", "2026-10-03T09:00:00+00:00")
        self.add("x", "xp.events", "2026-10-01T09:00:00+00:00")
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir)], ["p", "x"])
        self.assertEqual([r["id"] for r in rules.active_rules(self.dir, scope="xp.events")], ["x"])

    def test_version_increases_and_file_is_valid(self):
        self.add("a", "s1", "2026-10-01T09:00:00+00:00")
        self.assertEqual(self.file()["version"], 1)
        self.add("b", "s1", "2026-10-02T09:00:00+00:00")
        self.add("c", "s2", "2026-10-02T09:00:00+00:00")
        data = self.file()
        self.assertEqual(data["version"], 3)
        self.assertEqual(len(data["rules"]), 3)
        self.assertEqual(rules.get(self.dir, "a")["superseded_in_version"], 2)
        self.assertEqual(rules.get(self.dir, "c")["added_in_version"], 3)
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(".tmp-") or n == ".etrace.lock"]
        self.assertEqual(leftovers, [])

    def test_identical_readd_is_noop_but_conflict_raises(self):
        self.add("a", "s", "2026-10-01T09:00:00+00:00", "same")
        self.add("a", "s", "2026-10-01T09:00:00+00:00", "same")
        self.assertEqual(rules.version(self.dir), 1)
        with self.assertRaises(RuleError):
            self.add("a", "s", "2026-10-01T09:00:00+00:00", "different")

    def test_invalid_input(self):
        for bad in ("2026-10-01T09:00:00", "yesterday", 5):
            with self.assertRaises(RuleError):
                self.add("a", "s", bad)
        with self.assertRaises(RuleError):
            self.add("a", " ", "2026-10-01T09:00:00+00:00")
        with self.assertRaises(RuleError):
            rules.add_rule(self.dir, "a", "s", "", "2026-10-01T09:00:00+00:00")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "rules.json")))

    def test_accepts_store_instance(self):
        s = Store(self.dir)
        rules.add_rule(s, "a", "s", "t", "2026-10-01T09:00:00+00:00")
        self.assertEqual(len(rules.active_rules(s)), 1)

    def test_history_unknown_rule(self):
        with self.assertRaises(KeyError):
            rules.history(self.dir, "missing")


if __name__ == "__main__":
    unittest.main()
