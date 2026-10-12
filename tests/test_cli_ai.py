"""`etrace ai`, `etrace run`, `etrace note` and the shortened commands, through cli.main."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from etrace import ai, cli, config, ui
from tests.fake_ai import FakeAPI, forbid_real_network

TZ = "America/Chicago"
KEY = "sk-test-secret-0123"
CLEAN_ENV = {"FORCE_COLOR": "", "NO_COLOR": "", "TERM": "xterm-256color", "ETRACE_CONFIG": "",
             "ETRACE_AI_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "DEEPSEEK_API_KEY": ""}


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


class AICliTest(unittest.TestCase):
    kind = "openai"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.root = os.path.join(self.tmp, "data-folder")
        self.config = os.path.join(self.root, "trace.toml")
        self.keyfile = os.path.join(self.tmp, "xdg", "etrace", "ai.toml")
        self.env = dict(CLEAN_ENV, XDG_CONFIG_HOME=os.path.join(self.tmp, "xdg"), HOME=self.tmp)
        forbid_real_network(self)
        self.api = FakeAPI(self.kind)
        self.addCleanup(self.api.close)
        code, _, _ = self.cli("init", "--dir", self.root, "--language", "en", "--timezone", TZ, config=False)
        self.assertEqual(code, 0)

    def cli(self, *argv, stdin="", tty=False, env=None, config=True, getpass=None):
        out, err, inp = Tty(tty=tty), Tty(tty=False), Tty(stdin, tty=bool(stdin) or tty)
        args = (["--config", self.config] if config else []) + list(argv)
        patches = [mock.patch.dict(os.environ, dict(self.env, **(env or {}))), mock.patch("sys.stdout", out),
                   mock.patch("sys.stderr", err), mock.patch("sys.stdin", inp)]
        if getpass is not None:
            patches.append(mock.patch("getpass.getpass", side_effect=getpass))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def js(self, *argv, **kw):
        code, out, err = self.cli("--json", *argv, **kw)
        return code, (json.loads(out) if out.strip() else None), err

    def setup_ai(self, *extra, stdin=None):
        """`ai set` against the fake API with the key on stdin."""
        args = ["ai", "set", "custom", "--url", self.api.url, "--model", "alpha-1", "--key-stdin",
                "--key-file", self.keyfile, *extra]
        if self.kind == "anthropic":
            args += ["--provider", "anthropic"]
        return self.js(*args, stdin=KEY + "\n" if stdin is None else stdin)

    def trace_toml(self):
        with open(self.config, encoding="utf-8") as fh:
            return fh.read()


class SetTest(AICliTest):
    def test_status_before_anything_is_set(self):
        code, out, _ = self.js("ai")
        self.assertEqual(code, 0)
        self.assertFalse(out["configured"])
        self.assertIsNone(out["provider"])
        code, text, _ = self.cli("--pretty", "--no-color", "ai")
        self.assertIn("No AI is connected yet", text)
        self.assertIn("etrace ai set openai", text)

    def test_set_saves_everything_and_keeps_the_key_out_of_trace_toml(self):
        self.api.say("ok")
        self.api.call(("ping", {"message": "hello"}))
        code, out, err = self.setup_ai()
        self.assertEqual(code, 0, err)
        self.assertTrue(out["configured"] and out["saved"])
        self.assertEqual((out["provider"], out["model"], out["base_url"]), ("openai", "alpha-1", self.api.url))
        self.assertEqual(out["key"]["masked"], "…0123")
        self.assertTrue(out["key"]["source"].startswith("file "))
        self.assertTrue(out["test"]["ok"] and out["test"]["tools"])
        # the key lives in its own private file, never in trace.toml, the output or stderr
        self.assertNotIn(KEY, self.trace_toml())
        self.assertNotIn(KEY, json.dumps(out))
        self.assertNotIn(KEY, err)
        self.assertEqual(ai.read_key_file(self.keyfile), KEY)
        self.assertEqual(stat.S_IMODE(os.stat(self.keyfile).st_mode), 0o600)
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):  # "~/..." in trace.toml resolves against HOME
            cfg = config.load(self.config)
        self.assertEqual((cfg.ai.provider, cfg.ai.model, cfg.ai.base_url), ("openai", "alpha-1", self.api.url))
        self.assertEqual(cfg.ai.key_file, self.keyfile)
        self.assertIn('key_file = "~/xdg/etrace/ai.toml"', self.trace_toml())  # a short path, not the full one

    def test_the_key_never_comes_from_an_argument(self):
        code, _, err = self.cli("ai", "set", "openai", "--key", KEY, config=True)
        self.assertEqual(code, 2)
        self.assertNotIn("--key ", self.trace_toml())
        self.assertNotIn(KEY, self.trace_toml())

    def test_rewriting_keeps_the_rest_of_trace_toml(self):
        before = self.trace_toml()
        self.setup_ai("--no-test")
        after = self.trace_toml()
        self.assertTrue(after.startswith(before.rstrip("\n")))
        self.assertEqual(after.count("[ai]"), 1)
        self.assertEqual(self.js("ai", "set", "--model", "beta-2", "--no-test")[0], 0)
        self.assertEqual(self.trace_toml().count("[ai]"), 1)
        self.assertEqual(config.load(self.config).ai.model, "beta-2")
        self.assertEqual(config.load(self.config).ai.base_url, self.api.url)  # the rest was kept

    def test_the_default_address_is_not_written_down(self):
        code, out, _ = self.js("ai", "set", "openai", "--model", "m", "--key-env", "OPENAI_API_KEY", "--no-test")
        self.assertEqual(code, 0)
        self.assertNotIn("base_url", self.trace_toml().split("[ai]")[1])
        self.assertIn('key_env = "OPENAI_API_KEY"', self.trace_toml())
        self.assertEqual(out["base_url"], "https://api.openai.com/v1")

    def test_a_key_in_the_environment_is_found(self):
        self.js("ai", "set", "openai", "--model", "m", "--no-test")
        code, out, _ = self.js("ai", env={"OPENAI_API_KEY": KEY})
        self.assertEqual(out["key"]["source"], "env OPENAI_API_KEY")
        self.assertEqual(out["key"]["masked"], "…0123")
        code, out, _ = self.js("ai", env={"ETRACE_AI_KEY": "sk-override-999999"})
        self.assertEqual(out["key"]["source"], "env ETRACE_AI_KEY")
        code, out, _ = self.js("ai")
        self.assertFalse(out["key"]["found"])

    def test_choosing_a_model_later(self):
        code, out, _ = self.js("ai", "set", "custom", "--url", self.api.url, "--key-stdin",
                               "--key-file", self.keyfile, stdin=KEY)
        self.assertEqual(code, 0)
        self.assertFalse(out["configured"])
        self.assertIn("etrace ai models", out["next"])
        self.api.models = ["x-1", "x-2"]
        code, out, _ = self.js("ai", "models")
        self.assertEqual((code, out["models"], out["count"]), (0, ["x-1", "x-2"], 2))
        self.api.say("ok")
        self.api.call(("ping", {"message": "hi"}))
        code, out, _ = self.js("ai", "set", "--model", "x-2")
        self.assertEqual(code, 0)
        self.assertEqual(out["model"], "x-2")
        self.assertTrue(out["key"]["found"])  # `set --model` kept the key file

    def test_a_failed_test_still_saves_and_exits_1(self):
        code, out, err = self.setup_ai(stdin="sk-wrong-key-99999")
        self.assertEqual(code, 1)
        self.assertTrue(out["saved"])
        self.assertFalse(out["test"]["ok"])
        self.assertIn("401", out["test"]["error"])
        self.assertNotIn("sk-wrong-key-99999", json.dumps(out) + err)
        self.assertEqual(config.load(self.config).ai.model, "alpha-1")

    def test_errors(self):
        self.assertEqual(self.js("ai", "set")[0], 2)                       # nothing to go on, not a terminal
        code, _, err = self.js("ai", "set", "custom")
        self.assertEqual(code, 2)
        self.assertIn("--url", err)
        code, _, err = self.js("ai", "set", "custom", "--url", "ftp://nope", "--model", "m", "--no-test")
        self.assertEqual(code, 2)
        self.assertIn("http", err)
        code, _, err = self.js("ai", "set", "openai", "--key-env", "sk-this-is-a-key-not-a-name", "--model", "m",
                               "--no-test")
        self.assertEqual(code, 2)
        self.assertNotIn("sk-this-is-a-key-not-a-name", err)               # a pasted key is never echoed back
        self.assertEqual(self.js("ai", "set", "openai", "--key-stdin", "--no-test", stdin="  \n")[0], 2)
        self.assertEqual(self.js("ai", "set", "nonsense")[0], 2)
        self.assertNotIn("[ai]", self.trace_toml())                        # nothing half-written

    def test_a_loose_key_file_is_flagged(self):
        self.setup_ai("--no-test")
        os.chmod(self.keyfile, 0o644)
        code, out, _ = self.js("ai")
        self.assertTrue(out["key"]["exposed"])
        code, text, _ = self.cli("--pretty", "--no-color", "ai")
        self.assertIn("readable by other users", text)

    def test_a_local_service_needs_no_key(self):
        code, out, _ = self.js("ai", "set", "ollama", "--model", "llama", "--no-test")
        self.assertEqual(code, 0)
        self.assertFalse(out["key"]["needed"])
        code, text, _ = self.cli("--pretty", "--no-color", "ai")
        self.assertIn("none needed", text)
        self.assertIn("localhost:11434", text)


class InteractiveSetTest(AICliTest):
    def test_an_address_alone_means_openai_compatible_and_the_model_is_picked_from_the_list(self):
        self.api.models = ["m-a", "m-b", "m-c"]
        self.api.say("ok")
        self.api.call(("ping", {"message": "hi"}))
        # no key is found, so the wizard asks for it (hidden, via getpass); the model is number 2 of the list
        code, out, err = self.cli("ai", "set", "--url", self.api.url, "--key-file", self.keyfile, "--json",
                                  stdin="2\n", getpass=[KEY])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual((data["provider"], data["model"]), ("openai", "m-b"))
        self.assertIn("m-a", err)                                        # the list was shown on stderr
        self.assertNotIn(KEY, err + out)                                 # and the key never was
        self.assertEqual(ai.read_key_file(self.keyfile), KEY)

    def test_the_menu_accepts_a_name_and_lists_models(self):
        local = ("openai", self.api.url, None)
        self.api.required_key = None                    # a local service takes no key
        self.api.models = ["m-a", "m-b"]
        with mock.patch.dict(ai.PRESETS, {"ollama": local}):
            code, out, err = self.cli("ai", "set", "--json", "--no-test", stdin="ollama\n1\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["model"], "m-a")
        self.assertIn("Which AI service", err)
        self.assertIn("openrouter", err)

    def test_a_bad_menu_answer_asks_again(self):
        local = ("openai", self.api.url, None)
        self.api.required_key = None
        with mock.patch.dict(ai.PRESETS, {"ollama": local}):
            code, out, err = self.cli("ai", "set", "--json", "--no-test", stdin="99\nzzz\n5\n1\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(err.count("not one of the choices"), 2)

    def test_the_menu_takes_a_number(self):
        names = list(ai.PRESETS)
        local = ("openai", self.api.url, None)
        self.api.required_key = None
        with mock.patch.dict(ai.PRESETS, {names[4]: local}):
            code, out, err = self.cli("ai", "set", "--json", "--no-test", stdin="5\n1\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["model"], "alpha-1")


class TestCommandTest(AICliTest):
    def test_test_before_setup(self):
        code, _, err = self.js("ai", "test")
        self.assertEqual(code, 2)
        self.assertIn("etrace ai set", err)

    def test_test_reports_tool_support(self):
        self.setup_ai("--no-test")
        self.api.say("ok")
        self.api.say("no tools for me")
        code, out, _ = self.js("ai", "test")
        self.assertEqual(code, 0)
        self.assertIs(out["tools"], False)
        self.api.say("ok")
        self.api.call(("ping", {"message": "hello"}))
        code, text, _ = self.cli("--pretty", "--no-color", "ai", "test")
        self.assertIn("API reachable", text)
        self.assertIn("Tool calling works", text)

    def test_test_failure_exits_1_with_the_reason(self):
        self.setup_ai("--no-test")
        self.api.required_key = "something-else"
        code, out, err = self.js("ai", "test")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertIn("failed:", err)
        self.assertNotIn(KEY, err + json.dumps(out))

    def test_models_pretty_marks_the_current_one(self):
        self.setup_ai("--no-test")
        code, text, _ = self.cli("--pretty", "--no-color", "ai", "models")
        self.assertIn("2 model(s)", text)
        self.assertRegex(text, r"❯ alpha-1")
        self.assertIn("beta-2", text)


class RunCommandTest(AICliTest):
    def test_run_needs_an_ai(self):
        code, _, err = self.js("run", "daily")
        self.assertEqual(code, 2)
        self.assertIn("etrace ai set", err)

    def test_run_executes_a_task(self):
        self.setup_ai("--no-test")
        self.api.call(("etrace", {"args": ["rules", "list"]}))
        self.api.say("Done.", tokens=(50, 7))
        code, out, err = self.js("run", "g")
        self.assertEqual(code, 0, err)
        self.assertTrue(out["finished"])
        self.assertEqual(out["steps"][0]["call"], "etrace rules list")
        self.assertEqual(out["usage"], {"input": 60, "output": 12})

    def test_run_pretty_streams_progress_to_stderr(self):
        self.setup_ai("--no-test")
        self.api.call(("etrace", {"args": ["rules", "list"]}), ("etrace", {"args": ["init"]}))
        self.api.say("Finished with one refusal.")
        code, out, err = self.cli("--pretty", "--no-color", "run", "g")
        self.assertEqual(code, 0)
        self.assertIn("✔ etrace rules list", err)
        self.assertIn("✘ etrace init", err)
        self.assertIn("The AI finished", out)
        self.assertIn("Finished with one refusal.", out)
        self.assertNotIn("etrace rules list", out)                  # streamed already, not repeated
        self.assertIn("✘ etrace init", out)                         # but a failure is repeated
        self.assertIn("tokens", out)

    def test_run_that_does_not_finish_exits_1_and_prints_what_happened(self):
        self.setup_ai("--no-test")
        for _ in range(4):
            self.api.call(("etrace", {"args": ["rules", "list"]}))
        code, out, err = self.js("run", "g", "--max-steps", "2")
        self.assertEqual(code, 1)
        self.assertFalse(out["finished"])
        self.assertEqual(len(out["steps"]), 2)
        self.assertIn("stopped after 2 steps", err)

    def test_run_api_failure_exits_1(self):
        self.setup_ai("--no-test")
        self.api.required_key = "rotated"
        code, out, err = self.js("run", "g")
        self.assertEqual(code, 1)
        self.assertIn("failed:", err)
        self.assertNotIn(KEY, err + json.dumps(out))

    def test_run_rejects_unknown_tasks(self):
        self.assertEqual(self.js("run", "dance")[0], 2)
        self.assertEqual(self.js("run")[0], 2)


class AnthropicCliTest(RunCommandTest):
    kind = "anthropic"


class ShortCommandTest(AICliTest):
    def test_init_needs_no_options(self):
        root = os.path.join(self.tmp, "home", "existence-trace-data")
        with mock.patch.object(cli, "DEFAULT_ROOT", root):
            code, out, err = self.js("init", config=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["preset"], "default")
        self.assertEqual(out["root"], root)

    def test_init_pretty_points_at_ai_and_skips_export_for_the_default_folder(self):
        root = os.path.join(self.tmp, "elsewhere")
        code, text, _ = self.cli("--pretty", "--no-color", "init", "--dir", root, config=False)
        self.assertIn("export ETRACE_CONFIG=", text)  # a non-default folder needs it
        for line in ("etrace ai set", "etrace note", "etrace run daily"):
            self.assertIn(line, text)

    def test_note_makes_todays_note_and_says_where(self):
        code, out, _ = self.js("note")
        self.assertEqual(code, 0)
        self.assertFalse(out["created"])               # init already made it
        self.assertTrue(os.path.exists(out["note"]))
        os.remove(out["note"])
        code, out, _ = self.js("note")
        self.assertTrue(out["created"])
        self.assertTrue(os.path.exists(out["note"]))
        code, text, _ = self.cli("--pretty", "--no-color", "note")
        self.assertIn("Today's note is ready", text)

    def test_note_edit_opens_the_editor(self):
        with mock.patch("subprocess.run") as run:
            code, out, _ = self.js("note", "-e", env={"EDITOR": "myeditor --wait", "VISUAL": ""})
        self.assertEqual(code, 0)
        run.assert_called_once_with(["myeditor", "--wait", out["note"]], check=False)

    def test_note_edit_without_an_editor_falls_back_to_the_system_opener(self):
        with mock.patch("subprocess.run") as run, mock.patch.object(cli.sys, "platform", "darwin"):
            code, out, _ = self.js("note", "--edit", env={"EDITOR": "", "VISUAL": ""})
        self.assertEqual(run.call_args[0][0][:2], ["open", "-t"])

    def test_note_new_and_write_still_work(self):
        code, out, _ = self.js("note", "new", "2026-03-05")
        self.assertEqual(code, 0)
        self.assertTrue(out["note"].endswith("2026-03-05.md"))
        os.remove(self.js("note")[1]["note"])
        code, out, _ = self.js("note", "new")           # the date is optional now
        self.assertEqual(code, 0)
        self.assertEqual(self.js("note", "new")[0], 2)  # but it still refuses to overwrite a note

    def test_charts_default_to_today(self):
        for args in (("g", "chart"), ("pad", "chart")):
            code, _, err = self.js(*args)
            self.assertEqual(code, 2, args)             # no data yet: a real error, not a usage error
            self.assertNotIn("required", err)
            self.assertNotIn("usage", err)

    def test_help_lists_ai_and_run_first(self):
        code, text, _ = self.cli("--help")
        self.assertLess(text.index(" ai "), text.index("pad"))
        self.assertIn("run", text)
        for needle in ("etrace ai", "ai set", "ai test"):
            self.assertIn(needle, self.cli("ai", "--help")[1] + "etrace ai ai set ai test")


if __name__ == "__main__":
    unittest.main()
