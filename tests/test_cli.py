import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from etrace import cli
from etrace.container import decisions, feedback, questions, rules, usage
from etrace.models import g, pad, xp
from etrace.store import Store

TZ = "America/Chicago"
EXPRESSED = "2026-01-01T09:00:00+00:00"


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "trace-data")
        self.config = os.path.join(self.root, "trace.toml")
        env = mock.patch.dict(os.environ, {cli.ENV_CONFIG: ""})
        env.start()
        self.addCleanup(env.stop)
        code, out, _ = self.run_cli("init", "--preset", "default", "--dir", self.root,
                                    "--language", "en", "--timezone", TZ, config=False)
        self.assertEqual(code, 0)
        self.init_out = out
        self.today = datetime.datetime.now(ZoneInfo(TZ)).date().isoformat()
        self.data = os.path.join(self.root, "data")

    def run_cli(self, *argv, config=True):
        out, err = io.StringIO(), io.StringIO()
        args = (["--config", self.config] if config else []) + list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        text = out.getvalue()
        return code, (json.loads(text) if text.strip() else None), err.getvalue()

    def write_json(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))
        return path

    def note(self):
        return os.path.join(self.root, "journal", f"{self.today}.md")

    def add_feedback(self, text):
        with open(self.note(), encoding="utf-8") as fh:
            body = fh.read()
        head = "<!-- trace:user:feedback -->\n## Feedback for the AI\n"
        self.assertIn(head, body)
        with open(self.note(), "w", encoding="utf-8") as fh:
            fh.write(body.replace(head, f"{head}\n{text}\n", 1))

    def question(self, **over):
        q = {"source": "note", "date": self.today, "evidence": {"kind": "quote", "text": "tired all day"},
             "candidates": ["poor sleep", "heavy work"], "uncertainty": "why so tired?",
             "dimensions": ["P"], "dedupe_key": "tired"}
        q.update(over)
        return q

    # --- init ----------------------------------------------------------------

    def test_init_makes_a_fresh_data_dir_ready_for_day_one(self):
        for name in ("journal", "data", "audits", "charts"):
            self.assertTrue(os.path.isdir(os.path.join(self.root, name)))
        for name in ("pad.json", "g.json", "xp.json", "rules.json", "question_templates.json"):
            self.assertTrue(os.path.exists(os.path.join(self.root, "presets", "default", name)))
        self.assertTrue(os.path.exists(self.note()))
        self.assertEqual(self.init_out["note"], self.note())
        active = rules.active_rules(self.data)
        self.assertEqual(len(active), 9)
        self.assertTrue(all(r["scope"].startswith("principle:") for r in active))
        with open(self.config, encoding="utf-8") as fh:
            self.assertIn('data_dir = "."', fh.read())
        code, out, _ = self.run_cli("validate")
        self.assertEqual((code, out["ok"]), (0, True))

    def test_init_refuses_a_used_dir(self):
        code, _, err = self.run_cli("init", "--preset", "default", "--dir", self.root, config=False)
        self.assertEqual(code, 2)
        self.assertIn("not a fresh data dir", err)

    def test_init_unknown_preset_is_bad_input(self):
        code, _, err = self.run_cli("init", "--preset", "nope", "--dir", os.path.join(self.tmp, "x"),
                                    config=False)
        self.assertEqual(code, 2)
        self.assertIn("preset", err)

    def test_init_tiger_method_preset(self):
        root = os.path.join(self.tmp, "tiger")
        code, out, _ = self.run_cli("init", "--preset", "tiger-method", "--dir", root, config=False)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out["note"]))
        self.assertGreater(len(rules.active_rules(os.path.join(root, "data"))), 9)

    def test_config_from_environment(self):
        with mock.patch.dict(os.environ, {cli.ENV_CONFIG: self.config}):
            code, out, _ = self.run_cli("rules", "list", config=False)
        self.assertEqual(code, 0)
        self.assertEqual(out["version"], rules.version(self.data))

    def test_missing_config_is_bad_input(self):
        code, _, err = self.run_cli("--config", os.path.join(self.tmp, "none.toml"), "validate", config=False)
        self.assertEqual(code, 1)  # validate exits 1 on any failure
        code, _, err = self.run_cli("--config", os.path.join(self.tmp, "none.toml"), "rules", "list",
                                    config=False)
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_unknown_command_exits_2(self):
        code, _, err = self.run_cli("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)

    # --- notes and questions -----------------------------------------------------

    def test_note_new(self):
        code, out, _ = self.run_cli("note", "new", "2026-10-08")
        self.assertEqual(code, 0)
        self.assertTrue(out["note"].endswith("2026-10-08.md"))
        code, _, err = self.run_cli("note", "new", "2026-10-08")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        code, _, err = self.run_cli("note", "new", "10/08")
        self.assertEqual(code, 2)
        self.assertIn("date:", err)

    def test_question_add_and_field_errors(self):
        code, out, _ = self.run_cli("question", "add", self.write_json("q.json", self.question()))
        self.assertEqual(code, 0)
        self.assertEqual(out["question"]["id"], f"Q-{self.today.replace('-', '')}-1")

        bad = self.question(color="red")
        del bad["dedupe_key"]
        code, out, err = self.run_cli("question", "add", self.write_json("bad.json", bad))
        self.assertEqual(code, 2)
        self.assertIsNone(out)
        self.assertIn("error: dedupe_key: required", err)
        self.assertIn("error: color: unknown field", err)

        code, _, err = self.run_cli("question", "add", self.write_json("dup.json", self.question()))
        self.assertEqual(code, 2)
        self.assertIn("dedupe_key", err)

        code, _, err = self.run_cli("question", "add", self.write_json("broken.json", "{not json"))
        self.assertEqual(code, 2)
        self.assertIn("json: line 1", err)
        code, _, err = self.run_cli("question", "add", os.path.join(self.tmp, "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("file:", err)
        self.assertEqual(len(questions.all_questions(self.data)), 1)

    def test_question_add_from_stdin(self):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(self.question()))):
            code, out, _ = self.run_cli("question", "add", "-")
        self.assertEqual(code, 0)
        self.assertEqual(out["question"]["dedupe_key"], "tired")

    # --- the feedback -> decision -> receipt chain ----------------------------------

    def test_feedback_decide_usage_validate_audit(self):
        self.run_cli("question", "add", self.write_json("q.json", self.question()))
        qid = f"Q-{self.today.replace('-', '')}-1"
        self.add_feedback(f"[{qid}] mostly poor sleep")

        code, out, _ = self.run_cli("feedback", "ingest")
        self.assertEqual(code, 0)
        self.assertEqual(len(out["added"]), 1)
        self.assertEqual(out["answered"], [qid])
        self.assertTrue(out["scan_ok"])
        self.assertEqual(questions.get(self.data, qid)["status"], questions.ANSWERED)
        fid = out["added"][0]

        code, out, _ = self.run_cli("feedback", "ingest")
        self.assertEqual((code, out["added"], out["unchanged"]), (0, [], 1))

        dec = {"feedback_ids": [fid], "decision_text": "ask about sleep first", "value_source": "user",
               "rule_changes": [{"rule_id": "r-sleep", "scope": "questions:tired", "text": "ask about sleep",
                                 "expressed_at": EXPRESSED, "reason": "user said so"}]}
        code, out, _ = self.run_cli("decide", self.write_json("d.json", dec))
        self.assertEqual(code, 0)
        did = out["decision"]["id"]
        self.assertEqual(rules.get(self.data, "r-sleep")["status"], "active")

        code, out, err = self.run_cli("decide", self.write_json("d2.json", {"feedback_ids": ["nope"],
                                                                             "decision_text": "x",
                                                                             "value_source": "user"}))
        self.assertEqual(code, 2)
        self.assertIn("feedback", err)

        code, out, _ = self.run_cli("outreach", "send", "--scan", "ok")
        self.assertEqual(code, 0)
        self.assertTrue(out["note"]["recorded"])
        self.assertNotIn("email", out)
        with open(self.note(), encoding="utf-8") as fh:
            self.assertIn("ask about sleep first", fh.read())

        code, out, _ = self.run_cli("rules", "list", "--scope", "questions:tired")
        self.assertEqual([r["id"] for r in out["rules"]], ["r-sleep"])

        receipt = {"task": "daily", "target": f"journal/{self.today}.md", "rule_version": rules.version(self.data),
                   "decision_ids": [did], "before": "no sleep question", "after": "sleep question",
                   "status": "changed", "verified_readback": True}
        code, out, _ = self.run_cli("usage", "add", self.write_json("u.json", receipt))
        self.assertEqual(code, 0)
        self.assertTrue(usage.applied(self.data, did))

        code, _, err = self.run_cli("usage", "add", self.write_json("u2.json", dict(receipt, verified_readback=False)))
        self.assertEqual(code, 2)
        self.assertIn("verified_readback", err)

        code, out, _ = self.run_cli("validate")
        self.assertEqual((code, out["ok"]), (0, True))

        code, out, _ = self.run_cli("audit", "weekly")
        self.assertEqual(code, 0)
        self.assertTrue(out["passed"])
        self.assertTrue(os.path.exists(out["report"]))

    def test_outreach_requires_scan_flag(self):
        code, _, err = self.run_cli("outreach", "send")
        self.assertEqual(code, 2)
        self.assertIn("--scan", err)

    def test_outreach_scan_failed_never_claims_no_questions(self):
        code, _, _ = self.run_cli("outreach", "send", "--scan", "failed")
        self.assertEqual(code, 0)
        with open(self.note(), encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("no new important questions", body.lower())

    def test_rules_list_all_includes_superseded(self):
        store = Store(self.data)
        feedback.ingest_note(store, self.today, ["one"], now=datetime.datetime.now(datetime.timezone.utc))
        fid = feedback.read_all(store)[0]["id"]
        decisions.decide(store, [fid], "a", "user", [{"rule_id": "a1", "scope": "s", "text": "a",
                                                     "expressed_at": EXPRESSED}])
        decisions.decide(store, [fid], "b", "user", [{"rule_id": "a2", "scope": "s", "text": "b",
                                                     "expressed_at": "2026-02-01T09:00:00+00:00"}])
        _, active, _ = self.run_cli("rules", "list", "--scope", "s")
        _, every, _ = self.run_cli("rules", "list", "--scope", "s", "--all")
        self.assertEqual([r["id"] for r in active["rules"]], ["a2"])
        self.assertEqual(sorted(r["id"] for r in every["rules"]), ["a1", "a2"])

    # --- validate failures -------------------------------------------------------------

    def test_validate_exits_1_on_broken_chain(self):
        Store(self.data).append("decisions", {"id": "D-20260101-1", "type": "decision",
                                              "feedback_ids": ["ghost"], "decision": "x",
                                              "value_source": "user", "rule_changes": [],
                                              "rules_version": 0, "decided_at": EXPRESSED})
        code, out, err = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertTrue(out["problems"])
        self.assertIn("failed", err)

    def test_validate_exits_1_on_bad_note(self):
        with open(os.path.join(self.root, "journal", "2026-01-01.md"), "w", encoding="utf-8") as fh:
            fh.write("<!-- trace:ai:pad -->\n<!-- trace:ai:pad -->\n")
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertTrue(any("2026-01-01.md" in p for p in out["problems"]))

    def test_validate_exits_1_on_corrupt_store(self):
        with open(os.path.join(self.data, "feedback.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{oops\n")
        code, out, _ = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])

    # --- PAD -----------------------------------------------------------------------------

    def pad_points(self):
        return {"points": [
            {"date": self.today, "slot": "morning", "P": 3, "A": 2, "D": 3, "provenance": "self_report"},
            {"date": self.today, "slot": "noon", "P": 4, "A": 3, "D": 3, "provenance": "self_report"},
            {"date": self.today, "slot": "15:30", "P": 4, "A": 4, "D": 4, "provenance": "model_inference",
             "source": "journal", "confidence": "medium"},
        ]}

    def test_pad_add_and_chart(self):
        code, out, _ = self.run_cli("pad", "add", self.write_json("p.json", self.pad_points()))
        self.assertEqual(code, 0)
        self.assertEqual(len(out["added"]), 3)
        self.assertEqual(out["days"][self.today]["problems"], [])
        self.assertEqual(len(pad.points_for(self.data, self.today)), 3)

        code, out, _ = self.run_cli("pad", "add", self.write_json("p.json", self.pad_points()))
        self.assertEqual(out["added"], [])  # unchanged points are not stored again

        code, out, _ = self.run_cli("pad", "chart", self.today)
        self.assertEqual(code, 0)
        with open(out["chart"], encoding="utf-8") as fh:
            self.assertEqual(fh.read().count('class="pad-line"'), 3)
        with open(self.note(), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(f"../charts/{self.today}-pad.svg", body)

        code, out, _ = self.run_cli("pad", "chart", self.today, "--week")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.basename(out["chart"]).endswith("-pad.svg"))

    def test_pad_bad_input(self):
        pts = self.pad_points()
        pts["points"][1]["P"] = 9
        del pts["points"][2]["confidence"]
        code, _, err = self.run_cli("pad", "add", self.write_json("p.json", pts))
        self.assertEqual(code, 2)
        self.assertIn("points[1].P", err)
        self.assertIn("points[2].confidence", err)
        self.assertEqual(pad.points_for(self.data, self.today), [])
        code, _, err = self.run_cli("pad", "chart", "2026-01-01")
        self.assertEqual(code, 2)
        self.assertIn("no PAD points", err)

    def test_pad_later_point_replaces_slot(self):
        pts = self.pad_points()
        self.run_cli("pad", "add", self.write_json("p.json", pts))
        pts["points"][0]["P"] = 1
        code, out, _ = self.run_cli("pad", "add", self.write_json("p.json", pts))
        self.assertEqual(len(out["added"]), 1)
        current = pad.points_for(self.data, self.today)
        self.assertEqual([pt.P for pt in current if pt.slot == "morning"], [1])

    # --- G --------------------------------------------------------------------------------

    def g_items(self, codes=g.CODES, value=6):
        return [{"code": c, "value": value, "provenance": "self_report", "source": "note", "confidence": "high"}
                for c in codes]

    def test_g_predict_feedback_report(self):
        code, out, _ = self.run_cli("g", "predict", self.write_json("g.json", {"date": self.today,
                                                                             "items": self.g_items()}))
        self.assertEqual(code, 0)
        self.assertAlmostEqual(out["G"], 6.0)
        self.assertTrue(out["training_eligible"])
        pid = out["prediction"]
        self.assertEqual(g.predictions(self.data, self.today)[0]["id"], pid)

        future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)).isoformat()
        code, _, err = self.run_cli("g", "feedback", self.write_json("gf.json", {
            "prediction_id": pid, "targets": {"B1": 7}, "expressed_at": future}))
        self.assertEqual(code, 2)
        self.assertIn("expressed_at", err)
        code, out, _ = self.run_cli("g", "feedback", self.write_json("gf.json", {
            "prediction_id": pid, "targets": {"B1": 7}}))
        self.assertEqual(code, 0)
        self.assertEqual(out["feedback"]["targets"], {"B1": 7})

        code, out, _ = self.run_cli("g", "report")
        self.assertEqual(code, 0)
        self.assertFalse(out["calibration"]["enabled"])
        self.assertIn("G 6.0", out["text"])
        with open(self.note(), encoding="utf-8") as fh:
            self.assertIn("G 6.0", fh.read())

    def test_g_gap_fill_and_errors(self):
        code, out, _ = self.run_cli("g", "predict", self.write_json("g.json", {
            "date": self.today, "items": self.g_items(g.CODES[:-1])}))
        self.assertEqual(code, 0)
        self.assertEqual(out["filled"], ["E4"])
        self.assertFalse(out["training_eligible"])

        bad = self.g_items(("B1",))
        bad[0]["value"] = 11
        code, _, err = self.run_cli("g", "predict", self.write_json("g2.json", {"date": self.today, "items": bad}))
        self.assertEqual(code, 2)
        self.assertIn("items[0].value", err)
        code, _, err = self.run_cli("g", "report", "--date", "2026-01-01")
        self.assertEqual(code, 2)
        self.assertIn("no G prediction", err)

    # --- XP -------------------------------------------------------------------------------

    def test_xp_score(self):
        levels = {"rhythm": 4, "learning": 4, "boundaries": 4, "action": 4, "reflection": 4, "connection": 4}
        code, out, _ = self.run_cli("xp", "score", self.write_json("x.json", {
            "date": self.today, "levels": levels, "ordinary": 10}))
        self.assertEqual(code, 0)
        self.assertEqual(out["total"], 100)
        self.assertEqual(out["week"]["total"], 100)
        self.assertEqual(xp.latest(self.data, self.today)["total"], 100)

        del levels["connection"]
        code, _, err = self.run_cli("xp", "score", self.write_json("x.json", {
            "date": self.today, "levels": levels, "ordinary": 10}))
        self.assertEqual(code, 2)
        self.assertIn("connection", err)

    # --- checkpoint -------------------------------------------------------------------------

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_checkpoint(self):
        empty = os.path.join(self.tmp, "gitconfig")
        open(empty, "w").close()
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": empty, "GIT_CONFIG_NOSYSTEM": "1",
                                          "HOME": self.tmp}):
            code, out, _ = self.run_cli("checkpoint", "before daily")
            self.assertEqual(code, 0)
            self.assertTrue(out["committed"])
            self.assertTrue(os.path.isdir(os.path.join(self.root, ".git")))
            code, out, _ = self.run_cli("checkpoint", "after daily")
            self.assertEqual((code, out["committed"]), (0, False))


if __name__ == "__main__":
    unittest.main()
