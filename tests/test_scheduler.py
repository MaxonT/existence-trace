import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

from etrace import cli, config
from etrace.adapters import scheduler

TOML = """\
timezone = "Asia/Shanghai"
data_dir = "."
language = "en"

[tasks]
calibrate = "08:05"
feedback = ["12:30", "21:30", "07:45"]
daily = "19:40"
g = "23:10"
audit = "Sat 06:20"
"""

# Expected (weekday or None, hour, minute) per task, from TOML above.
EXPECTED = {
    "calibrate": {(None, 8, 5)},
    "feedback": {(None, 12, 30), (None, 21, 30), (None, 7, 45)},
    "daily": {(None, 19, 40)},
    "g": {(None, 23, 10)},
    "audit": {("Sat", 6, 20)},
}
CRON_DOW = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat"}
RRULE_DOW = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


def rrule_times(rule):
    parts = dict(p.split("=") for p in rule.split(";"))
    day = RRULE_DOW[parts["BYDAY"]] if parts["FREQ"] == "WEEKLY" else None
    if parts["FREQ"] == "DAILY":
        assert "BYDAY" not in parts
    return {(day, int(h), int(m)) for h in parts["BYHOUR"].split(",") for m in parts["BYMINUTE"].split(",")}


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "trace data")
        os.makedirs(self.root)
        self.config_path = os.path.join(self.root, "trace.toml")
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write(TOML)
        self.cfg = config.load(self.config_path)
        self.out = os.path.join(self.tmp, "out")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", self.config_path, *argv])
        text = out.getvalue()
        return code, (json.loads(text) if text.strip() else None), err.getvalue()

    def test_codex_one_automation_per_task_matches_trace_toml(self):
        code, out, err = self.run_cli("schedule", "--target", "codex", "--out", self.out)
        self.assertEqual(code, 0, err)
        self.assertFalse(out["installed"])
        found = {}
        for task in config.TASKS:
            paths = sorted(p for p in out["files"] if os.path.basename(os.path.dirname(p)).startswith(f"etrace-{task}"))
            self.assertTrue(paths, task)
            times = set()
            for path in paths:
                self.assertEqual(os.path.basename(path), "automation.toml")
                with open(path, "rb") as fh:
                    auto = tomllib.load(fh)
                self.assertEqual(auto["kind"], "cron")
                self.assertEqual(auto["timezone"], "Asia/Shanghai")
                self.assertIn(f"prompt {task} --raw", auto["prompt"])
                times |= rrule_times(auto["rrule"])
            found[task] = times
            # 12:30 and 21:30 share a rule; 07:45 needs its own.
            self.assertEqual(len(paths), 2 if task == "feedback" else 1, task)
        self.assertEqual(found, EXPECTED)

    def test_cron_snippet_matches_trace_toml(self):
        code, out, err = self.run_cli("schedule", "--target", "cron", "--out", self.out)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["files"], [os.path.join(self.out, "etrace.crontab")])
        with open(out["files"][0], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("CRON_TZ=Asia/Shanghai\n", text)
        found = {task: set() for task in config.TASKS}
        for line in text.splitlines():
            if not line or line.startswith("#") or line.startswith("CRON_TZ="):
                continue
            minute, hour, dom, mon, dow, rest = line.split(" ", 5)
            self.assertEqual((dom, mon), ("*", "*"))
            task = re.search(r"# etrace (\w+)$", rest).group(1)
            self.assertIn(f"prompt {task} --raw", rest)
            self.assertIn("| claude -p", rest)
            found[task].add((None if dow == "*" else CRON_DOW[dow], int(hour), int(minute)))
        self.assertEqual(found, EXPECTED)

    def test_claude_code_scripts_match_trace_toml(self):
        code, out, err = self.run_cli("schedule", "--target", "claude-code", "--out", self.out)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out["files"]), len(config.TASKS))
        for task in config.TASKS:
            path = os.path.join(self.out, f"etrace-{task}.sh")
            self.assertIn(path, out["files"])
            self.assertTrue(os.access(path, os.X_OK))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertIn("# timezone: Asia/Shanghai\n", text)
            self.assertIn(f"prompt {task} --raw", text)
            self.assertIn('-p ${ETRACE_CLAUDE_FLAGS:-} "$PROMPT"', text)
            when = re.search(r"^# schedule: (.+)$", text, re.M).group(1)
            self.assertEqual({scheduler.parse_time(t) for t in when.split(", ")}, EXPECTED[task])

    def test_generated_prompt_command_runs(self):
        # The command embedded in every target prints the real prompt (quoted paths with spaces).
        cmd = scheduler.prompt_command(self.cfg, "daily")
        self.assertIn("'", cmd)  # the data root has a space, so it is quoted
        res = subprocess.run(["/bin/sh", "-c", f"cd / && {cmd}"], capture_output=True, text=True,
                             timeout=60, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), cli_prompt(self.config_path, "daily").strip())

    def test_agent_inherits_environment(self):
        # Commands the agent runs after the prompt must find the same config and package.
        cmd = scheduler.env_command(self.cfg) + " && sh -c 'python3 -m etrace rules list'"
        res = subprocess.run(["/bin/sh", "-c", f"cd / && {cmd}"], capture_output=True, text=True,
                             timeout=60, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("rules", json.loads(res.stdout))

    def test_never_installs(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen, \
                mock.patch("os.system") as system:
            for target in scheduler.TARGETS:
                code, out, err = self.run_cli("schedule", "--target", target, "--out", self.out)
                self.assertEqual(code, 0, err)
                for path in out["files"]:
                    self.assertTrue(path.startswith(self.out + os.sep))
        run.assert_not_called()
        popen.assert_not_called()
        system.assert_not_called()

    def test_bad_target_and_out(self):
        code, _, err = self.run_cli("schedule", "--target", "launchd", "--out", self.out)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        not_dir = os.path.join(self.tmp, "file")
        open(not_dir, "w").close()
        code, _, err = self.run_cli("schedule", "--target", "cron", "--out", not_dir)
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)
        with self.assertRaises(scheduler.SchedulerError):
            scheduler.render("launchd", self.cfg)

    def test_cron_escapes_percent(self):
        root = os.path.join(self.tmp, "50%")
        os.makedirs(root)
        with open(os.path.join(root, "trace.toml"), "w", encoding="utf-8") as fh:
            fh.write(TOML)
        cfg = config.load(os.path.join(root, "trace.toml"))
        for line in scheduler.cron_lines(cfg):
            self.assertNotRegex(line, r"(?<!\\)%")


def cli_prompt(config_path, task):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["--config", config_path, "prompt", task, "--raw"])
    return out.getvalue()


if __name__ == "__main__":
    unittest.main()
