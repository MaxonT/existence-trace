"""End to end: the fictional persona in examples/persona runs the whole loop through the CLI."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from etrace import cli, journal
from etrace.container import questions, rules, usage

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA = os.path.join(REPO, "examples", "persona")
AGENT = os.path.join(PERSONA, "agent")
DAYS = ("2026-03-02", "2026-03-03", "2026-03-04")


class PersonaLoopTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "trace-data")
        self.config = os.path.join(self.root, "trace.toml")
        self.data = os.path.join(self.root, "data")
        env = mock.patch.dict(os.environ, {cli.ENV_CONFIG: ""})
        env.start()
        self.addCleanup(env.stop)
        with open(os.path.join(PERSONA, "expected.json"), encoding="utf-8") as fh:
            self.expected = json.load(fh)

    def run_cli(self, *argv, config=True):
        out, err = io.StringIO(), io.StringIO()
        args = (["--config", self.config] if config else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        self.assertEqual(code, 0, f"{' '.join(argv)} failed: {err.getvalue()}")
        return json.loads(out.getvalue())

    def agent(self, name):
        return os.path.join(AGENT, name)

    def note(self, day):
        return os.path.join(self.root, "journal", f"{day}.md")

    def test_persona_runs_the_whole_loop(self):
        # init, then the persona's three days of notes
        self.run_cli("init", "--preset", "default", "--dir", self.root, "--language", "en",
                     "--timezone", "UTC", config=False)
        for day in DAYS:
            shutil.copy(os.path.join(PERSONA, "journal", f"{day}.md"), self.note(day))
        protected = {day: journal.load(self.note(day)).protected_hashes() for day in DAYS}

        # the agent asks one real question about day two
        out = self.run_cli("question", "add", self.agent("question.json"))
        qid = out["question"]["id"]
        self.assertEqual(qid, self.expected["question_id"])

        # feedback intake answers it
        out = self.run_cli("feedback", "ingest")
        self.assertEqual(out["added"], self.expected["feedback_ids"])
        self.assertEqual(out["answered"], [qid])
        self.assertEqual(questions.get(self.data, qid)["status"], questions.ANSWERED)

        # decide: one decision, three new rules
        version_before = rules.version(self.data)
        out = self.run_cli("decide", self.agent("decision.json"))
        did = out["decision"]["id"]
        new_rules = sorted(r["id"] for r in rules.active_rules(self.data) if not r["scope"].startswith("principle:"))
        self.assertEqual(new_rules, self.expected["new_rules"])
        self.assertGreater(rules.version(self.data), version_before)

        # daily outputs for each day
        for day in DAYS:
            out = self.run_cli("pad", "add", self.agent(f"{day}-pad.json"))
            self.assertEqual(out["days"][day]["problems"], self.expected["pad_problems"][day])
            self.assertTrue(os.path.exists(self.run_cli("pad", "chart", day)["chart"]))
            out = self.run_cli("g", "predict", self.agent(f"{day}-g.json"))
            self.assertAlmostEqual(out["G"], self.expected["G"][day])
            self.assertTrue(out["training_eligible"])
            self.run_cli("g", "report", "--date", day)
            out = self.run_cli("xp", "score", self.agent(f"{day}-xp.json"))
            self.assertEqual(out["total"], self.expected["xp"][day])
            for block in ("headline", "narrative"):
                self.run_cli("note", "write", day, block, self.agent(f"{day}-{block}.md"))
        self.assertEqual(out["week"]["total"], self.expected["xp_week"])
        out = self.run_cli("outreach", "send", "--scan", "ok", "--date", DAYS[-1])
        self.assertTrue(out["note"]["recorded"])

        # the user's own words never changed
        for day in DAYS:
            doc = journal.load(self.note(day))
            self.assertEqual(doc.protected_hashes(), protected[day])
        with open(self.note(DAYS[-1]), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("first paid cake order", body)
        self.assertIn("Decision report", body)
        self.assertIn("G 7.3", body)

        # the receipt proves the decision was used in a real note
        with open(self.agent("receipt.json"), encoding="utf-8") as fh:
            receipt = json.load(fh)
        receipt.update(rule_version=rules.version(self.data), decision_ids=[did])
        receipt_file = os.path.join(self.tmp, "receipt.json")
        with open(receipt_file, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh)
        self.run_cli("usage", "add", receipt_file)
        self.assertTrue(usage.applied(self.data, did))

        # usage, validate, weekly audit
        self.assertEqual(self.run_cli("validate"), {"ok": True, "problems": []})
        out = self.run_cli("audit", "weekly")
        self.assertTrue(out["passed"])
        self.assertEqual(out["broken"], [])
        self.assertTrue(os.path.exists(out["report"]))


if __name__ == "__main__":
    unittest.main()
