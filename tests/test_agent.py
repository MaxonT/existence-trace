import json
import os
import tempfile
import unittest
from unittest import mock

from etrace import agent, ai, cli, config, prompts
from tests.fake_ai import FakeAPI, forbid_real_network

TZ = "America/Chicago"
KEY = "sk-test-secret-0123"


def call(name, **args):
    return ai.ToolCall("c1", name, args, json.dumps(args))


class DataFolderTest(unittest.TestCase):
    """A real data folder made by `etrace init`, wired to a fake AI API."""

    kind = "openai"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "data-folder")
        self.config = os.path.join(self.root, "trace.toml")
        with mock.patch("sys.stdout"), mock.patch.dict(os.environ, {"ETRACE_CONFIG": ""}):
            code = cli.main(["init", "--preset", "default", "--dir", self.root, "--language", "en",
                             "--timezone", TZ])
        self.assertEqual(code, 0)
        forbid_real_network(self)
        self.api = FakeAPI(self.kind)
        self.addCleanup(self.api.close)
        config.write_ai(self.config, {"provider": self.kind, "model": "alpha-1", "base_url": self.api.url,
                                      "key_env": None, "key_file": None})
        self.cfg = config.load(self.config)
        self.sandbox = agent.Sandbox(self.config, self.root)
        self.secret_file = os.path.join(self.tmp, "outside-secret.txt")
        with open(self.secret_file, "w") as fh:
            fh.write("TOP-SECRET")


class SandboxReadTest(DataFolderTest):
    def test_lists_and_reads_the_allowed_folders(self):
        listing = self.sandbox.read("journal")
        self.assertTrue(listing["entries"] and listing["entries"][0].endswith(".md"))
        name = listing["entries"][0]
        self.assertTrue(self.sandbox.read(f"journal/{name}")["text"].strip())
        self.assertIn("rules.json", " ".join(self.sandbox.read("data")["entries"]))
        self.assertIn("default/", self.sandbox.read("presets")["entries"])

    def test_cannot_leave_the_allowed_folders(self):
        for path in ("trace.toml", "../outside-secret.txt", "journal/../trace.toml", "/etc/passwd",
                     "charts", "", "  ", "..", "journal/../../outside-secret.txt", "data/../.."):
            result = self.sandbox.read(path)
            self.assertIn("error", result, path)
            self.assertNotIn("text", result, path)
            self.assertNotIn("TOP-SECRET", json.dumps(result))

    def test_symlinks_cannot_escape(self):
        link = os.path.join(self.root, "journal", "escape.txt")
        os.symlink(self.secret_file, link)
        result = self.sandbox.read("journal/escape.txt")
        self.assertIn("leaves the data folder", result["error"])
        folder_link = os.path.join(self.root, "data", "out")
        os.symlink(self.tmp, folder_link)
        self.assertIn("error", self.sandbox.read("data/out"))

    def test_missing_binary_and_big_files(self):
        self.assertIn("does not exist", self.sandbox.read("journal/nope.md")["error"])
        with open(os.path.join(self.root, "data", "blob.bin"), "wb") as fh:
            fh.write(b"\xff\xfe\x00\x01")
        self.assertIn("not a text file", self.sandbox.read("data/blob.bin")["error"])
        with open(os.path.join(self.root, "data", "big.txt"), "w") as fh:
            fh.write("x" * (agent.MAX_OUTPUT + 500))
        big = self.sandbox.read("data/big.txt")["text"]
        self.assertIn("cut: 500 more characters", big)
        self.assertLess(len(big), agent.MAX_OUTPUT + 100)

    def test_bad_types(self):
        for bad in (None, 5, ["a"], {}):
            self.assertIn("error", self.sandbox.read(bad))


class SandboxCommandTest(DataFolderTest):
    def run_args(self, *args, files=None):
        return self.sandbox.run_etrace(list(args), files)

    def test_runs_an_etrace_command_and_returns_its_output(self):
        result = self.run_args("rules", "list")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("principle:", result["stdout"])
        self.assertEqual(json.loads(result["stdout"])["version"], 9)  # JSON, whatever the terminal is

    def test_failures_come_back_as_data(self):
        result = self.run_args("note", "write", "2026-01-01", "headline", "@x.md", files={"x.md": "hi"})
        self.assertEqual(result["exit_code"], 2)
        self.assertIn("no note for 2026-01-01", result["stderr"])

    def test_blocked_commands(self):
        for cmd in ("init", "schedule", "ai", "run"):
            result = self.run_args(cmd, "--help")
            self.assertIn("not available", result["error"], cmd)
        self.assertIn("error", self.run_args("--version"))
        self.assertIn("error", self.run_args())

    def test_blocked_arguments(self):
        for bad in (["validate", "--config", self.config], ["validate", "--config=/x"],
                    ["validate", "--json"], ["validate", "--pretty"], ["validate", "--no-color"],
                    ["pad", "add", "-"]):
            self.assertIn("not allowed", self.sandbox.run_etrace(bad)["error"], bad)

    def test_bad_shapes(self):
        for args in ("rules list", [1, 2], [["a"]], None):
            self.assertIn("error", self.sandbox.run_etrace(args))
        self.assertIn("files must be", self.run_args("validate", files={"a": 1})["error"])

    def test_file_names_cannot_escape_the_temp_folder(self):
        for name in ("../x.json", "a/b.json", "/abs.json", ".hidden", "", "x" * 200, "a b.json"):
            self.assertIn("bad file name", self.run_args("validate", files={name: "{}"})["error"], name)
        self.assertIn("not in `files`", self.run_args("pad", "add", "@missing.json")["error"])

    def test_files_go_to_a_temp_folder_not_the_data_folder(self):
        before = _tree(self.root)
        points = [{"date": "2026-10-12", "slot": "morning", "P": 3, "A": 3, "D": 3, "provenance": "self_report"}]
        result = self.run_args("pad", "add", "@points.json", files={"points.json": json.dumps(points)})
        self.assertEqual(result["exit_code"], 0, result)
        added = _tree(self.root) - before
        self.assertTrue(any(p.startswith("data") for p in added))      # the command wrote its data
        self.assertFalse(any("points.json" in p for p in added))        # but the agent file did not land here
        leftovers = [d for d in os.listdir(tempfile.gettempdir()) if d.startswith("etrace-agent-")
                     and os.path.exists(os.path.join(tempfile.gettempdir(), d, "points.json"))]
        self.assertEqual(leftovers, [])                                 # and the temp folder was removed

    def test_output_is_clipped(self):
        result = self.run_args("rules", "list", "--all")
        self.assertLessEqual(len(result["stdout"]), agent.MAX_OUTPUT + 100)

    def test_the_outer_output_is_not_touched(self):
        import io
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.run_args("rules", "list")
        self.assertEqual(out.getvalue(), "")

    def test_execute_routes_and_rejects(self):
        self.assertEqual(self.sandbox.execute(call("etrace", args=["rules", "list"]))["exit_code"], 0)
        self.assertIn("entries", self.sandbox.execute(call("read", path="data")))
        self.assertIn("unknown tool", self.sandbox.execute(call("shell", cmd="rm -rf /"))["error"])
        bad = ai.ToolCall("c", "etrace", None, "{not json")
        self.assertIn("not valid JSON", self.sandbox.execute(bad)["error"])


def _tree(root):
    return {os.path.relpath(os.path.join(d, f), root) for d, _, files in os.walk(root) for f in files}


class RunTaskTest(DataFolderTest):
    def client(self):
        return ai.Client(self.cfg.ai, KEY, retries=0)

    def tool_result_texts(self, body):
        """The tool results of the last turn of a request body, in either wire format."""
        last = body["messages"][-1]
        if self.kind == "anthropic":
            return [b["content"] for b in last["content"] if b["type"] == "tool_result"]
        return [m["content"] for m in body["messages"] if m["role"] == "tool"]

    def run_task(self, task="g", **kw):
        return agent.run_task(self.cfg, self.client(), task, self.root, **kw)

    def test_a_full_run_executes_the_models_tool_calls(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}), ("read", {"path": "journal"}), tokens=(100, 20))
        self.api.call(("etrace", {"args": ["validate"]}), tokens=(150, 10))
        self.api.say("All done: rules read, data valid.", tokens=(160, 12))
        seen = []
        result = self.run_task("g", progress=seen.append)
        self.assertTrue(result["finished"])
        self.assertEqual(result["final"], "All done: rules read, data valid.")
        self.assertEqual([s["call"] for s in result["steps"]], ["etrace rules list", "read journal", "etrace validate"])
        self.assertEqual([s["exit_code"] for s in result["steps"]], [0, None, 0])
        self.assertEqual(seen, result["steps"])
        self.assertEqual(result["usage"], {"input": 410, "output": 42})
        self.assertEqual((result["provider"], result["model"]), (self.kind, "alpha-1"))

    def test_the_prompt_and_tools_reach_the_model(self):
        self.api.say("nothing to do")
        self.run_task("g")
        body = self.api.chats[0]["body"]
        system = body["messages"][0]["content"]
        self.assertTrue(system.startswith("You are the existence-trace agent"))
        self.assertIn("g predict FILE", system)                       # the real task prompt follows
        self.assertEqual([t["function"]["name"] for t in body["tools"]], ["etrace", "read"])
        self.assertEqual(body["messages"][1], {"role": "user", "content": "Run the g task now."})

    def test_tool_results_are_sent_back_to_the_model(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}))
        self.api.say("ok")
        self.run_task("g")
        second = self.api.chats[1]["body"]["messages"]
        self.assertEqual(second[2]["role"], "assistant")
        self.assertEqual(second[2]["tool_calls"][0]["function"]["name"], "etrace")
        self.assertEqual((second[3]["role"], second[3]["tool_call_id"]), ("tool", "call_0"))
        self.assertEqual(json.loads(second[3]["content"])["exit_code"], 0)

    def test_a_blocked_command_is_refused_and_the_run_goes_on(self):
        self.api.call(("etrace", {"args": ["init", "--preset", "default"]}))
        self.api.say("I could not do that")
        result = self.run_task("g")
        self.assertTrue(result["finished"])
        self.assertIn("not available", result["steps"][0]["error"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "presets", "nope")))

    def test_the_model_cannot_read_outside(self):
        self.api.call(("read", {"path": "../outside-secret.txt"}))
        self.api.say("nope")
        self.run_task("g")
        tool_message = self.tool_result_texts(self.api.chats[1]["body"])[0]
        self.assertNotIn("TOP-SECRET", tool_message)
        self.assertIn("error", json.loads(tool_message))

    def test_step_limit(self):
        for _ in range(5):
            self.api.call(("etrace", {"args": ["rules", "list"]}))
        with self.assertRaises(agent.AgentError) as cm:
            self.run_task("g", max_steps=3)
        self.assertIn("stopped after 3 steps", str(cm.exception))
        self.assertEqual(len(cm.exception.result["steps"]), 3)
        self.assertFalse(cm.exception.result["finished"])

    def test_an_api_failure_keeps_the_partial_record(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}))  # the second request finds no scripted reply: 500
        with self.assertRaises(agent.AgentError) as cm:
            self.run_task("g")
        self.assertIn("500", str(cm.exception))
        self.assertEqual(len(cm.exception.result["steps"]), 1)

    def test_unknown_task(self):
        with self.assertRaises(agent.AgentError):
            self.run_task("nonsense")

    def test_every_task_prompt_renders_inside_the_agent(self):
        for task in prompts.TASKS:
            self.api.say("done")
            self.assertTrue(self.run_task(task)["finished"], task)

    def test_the_key_never_appears_in_anything_the_model_sees(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}), ("read", {"path": "data"}))
        self.api.say("done")
        self.run_task("g")
        for req in self.api.chats:
            self.assertNotIn(KEY, json.dumps(req["body"]))


class RunTaskAnthropicTest(RunTaskTest):
    kind = "anthropic"
    # the next two replace the OpenAI-shaped versions in the parent

    def test_system_prompt_is_a_top_level_field(self):
        self.api.say("nothing to do")
        self.run_task("g")
        body = self.api.chats[0]["body"]
        self.assertTrue(body["system"].startswith("You are the existence-trace agent"))
        self.assertEqual(body["messages"], [{"role": "user", "content": "Run the g task now."}])
        self.assertEqual([t["name"] for t in body["tools"]], ["etrace", "read"])

    def test_tool_results_are_sent_back_to_the_model(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}), ("read", {"path": "data"}))
        self.api.say("ok")
        self.run_task("g")
        second = self.api.chats[1]["body"]["messages"]
        results = second[-1]["content"]
        self.assertEqual([b["type"] for b in results], ["tool_result", "tool_result"])
        self.assertEqual([b["tool_use_id"] for b in results], ["tu_0", "tu_1"])

    def test_the_prompt_and_tools_reach_the_model(self):
        self.api.say("nothing to do")
        self.run_task("g")
        self.assertIn("g predict FILE", self.api.chats[0]["body"]["system"])


if __name__ == "__main__":
    unittest.main()
