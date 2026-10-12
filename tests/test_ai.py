import json
import os
import stat
import tempfile
import unittest

from etrace import ai, config
from tests.fake_ai import FakeAPI, forbid_real_network

KEY = "sk-test-secret-0123"


def cfg(api, provider="openai", model="alpha-1", **kw):
    return config.AIConfig(provider=provider, model=model, base_url=api.url, **kw)


class KeyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def test_mask_never_shows_much(self):
        self.assertEqual(ai.mask_key(KEY), "…0123")
        self.assertEqual(ai.mask_key("short"), "…")
        self.assertEqual(ai.mask_key(""), "")
        self.assertEqual(ai.mask_key(None), "")

    def test_key_file_is_private_and_roundtrips(self):
        path = os.path.join(self.dir, "sub", "ai.toml")
        ai.write_key_file(path, "  " + KEY + " \n")
        self.assertEqual(ai.read_key_file(path), KEY)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertFalse(ai.key_file_exposed(path))
        os.chmod(path, 0o644)
        self.assertTrue(ai.key_file_exposed(path))
        ai.write_key_file(path, "sk-second-key-4567")  # overwriting also fixes the mode
        self.assertEqual(ai.read_key_file(path), "sk-second-key-4567")
        self.assertFalse(ai.key_file_exposed(path))

    def test_key_with_odd_characters_survives(self):
        path = os.path.join(self.dir, "ai.toml")
        odd = 'sk-"quoted"\\back-12345'
        ai.write_key_file(path, odd)
        self.assertEqual(ai.read_key_file(path), odd)

    def test_bad_key_files_never_leak_their_content(self):
        path = os.path.join(self.dir, "ai.toml")
        with open(path, "w") as fh:
            fh.write("key = sk-not-valid-toml-SECRET\n")
        with self.assertRaises(ai.AIError) as cm:
            ai.read_key_file(path)
        self.assertNotIn("SECRET", str(cm.exception))
        with open(path, "w") as fh:
            fh.write("other = 1\n")
        with self.assertRaises(ai.AIError):
            ai.read_key_file(path)
        with self.assertRaises(ai.AIError):
            ai.read_key_file(os.path.join(self.dir, "missing.toml"))
        with self.assertRaises(ai.AIError):
            ai.write_key_file(path, "   ")

    def test_resolve_order(self):
        path = os.path.join(self.dir, "ai.toml")
        ai.write_key_file(path, "file-key-000000")
        c = config.AIConfig(provider="openai", model="m", key_env="MY_KEY", key_file=path)
        env = {"ETRACE_AI_KEY": "override-key-1", "MY_KEY": "env-key-000000"}
        self.assertEqual(ai.resolve_key(c, env), ("override-key-1", "env ETRACE_AI_KEY"))
        env.pop("ETRACE_AI_KEY")
        key, source = ai.resolve_key(c, env)
        self.assertEqual(key, "file-key-000000")
        self.assertTrue(source.startswith("file "))
        self.assertEqual(ai.resolve_key(config.AIConfig(provider="openai", key_env="MY_KEY"), env),
                         ("env-key-000000", "env MY_KEY"))
        self.assertEqual(ai.resolve_key(config.AIConfig(provider="openai", key_env="NOPE"), {}), (None, ""))
        self.assertEqual(ai.resolve_key(config.AIConfig(), {}), (None, ""))

    def test_default_key_file_follows_xdg(self):
        self.assertEqual(ai.default_key_file({"XDG_CONFIG_HOME": "/x"}), "/x/etrace/ai.toml")
        self.assertTrue(ai.default_key_file({}).endswith(os.path.join(".config", "etrace", "ai.toml")))

    def test_local_services_need_no_key(self):
        for url in ("http://localhost:11434/v1", "http://127.0.0.1:8000", "http://[::1]:1234/v1"):
            self.assertFalse(ai.needs_key(config.AIConfig(provider="openai", base_url=url)), url)
        for url in ("https://api.openai.com/v1", "http://example.com:11434", "http://localhost.evil.com"):
            self.assertTrue(ai.needs_key(config.AIConfig(provider="openai", base_url=url)), url)
        self.assertTrue(ai.needs_key(config.AIConfig(provider="anthropic")))

    def test_presets_are_complete(self):
        for name, (provider, url, env) in ai.PRESETS.items():
            self.assertIn(provider, config.AI_PROVIDERS, name)
            self.assertTrue(url is None or url.startswith("http"), name)
        self.assertIsNone(ai.PRESETS["ollama"][2])  # local: no key variable
        self.assertEqual(ai.PRESETS["deepseek"][0], "openai")


class OpenAIClientTest(unittest.TestCase):
    def setUp(self):
        forbid_real_network(self)
        self.api = FakeAPI("openai")
        self.addCleanup(self.api.close)
        self.client = ai.Client(cfg(self.api), KEY, retries=2, sleep=lambda s: None)

    def test_models_are_listed_sorted_with_the_key_in_the_header(self):
        self.api.models = ["zeta", "alpha"]
        self.assertEqual(self.client.models(), ["alpha", "zeta"])
        req = self.api.requests[0]
        self.assertEqual((req["method"], req["path"]), ("GET", "/models"))
        self.assertEqual(req["headers"]["authorization"], "Bearer " + KEY)

    def test_chat_text_and_usage(self):
        self.api.say("hello", tokens=(7, 3))
        reply = self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual((reply.text, reply.tool_calls, reply.usage), ("hello", [], {"input": 7, "output": 3}))
        body = self.api.chats[0]["body"]
        self.assertEqual(self.api.chats[0]["path"], "/chat/completions")
        self.assertEqual((body["model"], body["messages"]), ("alpha-1", [{"role": "user", "content": "hi"}]))
        self.assertNotIn("tools", body)

    def test_tool_calls_are_parsed(self):
        self.api.call(("etrace", {"args": ["rules", "list"]}), ("read", {"path": "data"}), text="working")
        reply = self.client.chat([{"role": "user", "content": "go"}], tools=[ai.PING_TOOL])
        self.assertEqual([(c.name, c.args) for c in reply.tool_calls],
                         [("etrace", {"args": ["rules", "list"]}), ("read", {"path": "data"})])
        self.assertEqual(reply.text, "working")
        self.assertEqual(self.api.chats[0]["body"]["tools"][0]["function"]["name"], "ping")

    def test_invalid_tool_arguments_do_not_crash(self):
        self.api.script.append({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "etrace", "arguments": "{not json"}}]}}]})
        reply = self.client.chat([{"role": "user", "content": "go"}])
        self.assertIsNone(reply.tool_calls[0].args)

    def test_wrong_key_is_a_clean_error_and_the_echoed_key_is_scrubbed(self):
        bad = ai.Client(cfg(self.api), "sk-wrong-key-99999", retries=0)
        with self.assertRaises(ai.AIError) as cm:
            bad.models()
        self.assertIn("401", str(cm.exception))
        self.assertIn("Incorrect API key provided", str(cm.exception))  # the server's words are kept
        self.assertNotIn("sk-wrong-key-99999", str(cm.exception))        # but the key it echoed is not
        self.assertIn("***", str(cm.exception))

    def test_server_errors_are_retried_then_succeed(self):
        waits = []
        client = ai.Client(cfg(self.api), KEY, retries=2, sleep=waits.append)
        self.api.fail_next = [503, 429]
        self.api.say("recovered")
        self.assertEqual(client.chat([{"role": "user", "content": "x"}]).text, "recovered")
        self.assertEqual(waits, [1, 2])
        self.assertEqual(len(self.api.chats), 3)

    def test_retries_run_out(self):
        self.api.fail_next = [503, 503, 503]
        with self.assertRaises(ai.AIError) as cm:
            self.client.chat([{"role": "user", "content": "x"}])
        self.assertIn("503", str(cm.exception))

    def test_client_errors_are_not_retried(self):
        self.api.fail_next = [400]
        with self.assertRaises(ai.AIError):
            self.client.chat([{"role": "user", "content": "x"}])
        self.assertEqual(len(self.api.requests), 1)

    def test_unreachable_host(self):
        c = ai.Client(config.AIConfig(provider="openai", model="m", base_url="http://127.0.0.1:1"), KEY,
                      retries=0, timeout=2)
        with self.assertRaises(ai.AIError) as cm:
            c.models()
        self.assertIn("cannot reach 127.0.0.1:1", str(cm.exception))

    def test_not_an_openai_server(self):
        self.api.script.append({"unexpected": True})
        with self.assertRaises(ai.AIError) as cm:
            self.client.chat([{"role": "user", "content": "x"}])
        self.assertIn("unexpected shape", str(cm.exception))

    def test_missing_pieces_are_reported_early(self):
        with self.assertRaises(ai.AIError):
            ai.Client(config.AIConfig(), KEY)
        with self.assertRaises(ai.AIError):
            ai.Client(config.AIConfig(provider="openai", model="m", base_url="https://api.openai.com/v1"), None)
        ai.Client(config.AIConfig(provider="openai", model="m", base_url="http://localhost:11434/v1"), None)

    def test_check_reports_latency_and_tool_support(self):
        self.api.say("ok")
        self.api.call(("ping", {"message": "hello"}))
        result = ai.check(self.client)
        self.assertTrue(result["ok"])
        self.assertEqual((result["reply"], result["tools"]), ("ok", True))
        self.assertIsInstance(result["ms"], int)

    def test_check_flags_a_model_without_tools(self):
        self.api.say("ok")
        self.api.say("I will not call tools")
        result = ai.check(self.client)
        self.assertTrue(result["ok"])
        self.assertIs(result["tools"], False)

    def test_check_when_the_first_call_fails(self):
        bad = ai.Client(cfg(self.api), "sk-wrong-key-99999", retries=0)
        result = ai.check(bad)
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])
        self.assertIsNone(result["tools"])


class AnthropicClientTest(unittest.TestCase):
    def setUp(self):
        forbid_real_network(self)
        self.api = FakeAPI("anthropic")
        self.addCleanup(self.api.close)
        self.client = ai.Client(cfg(self.api, "anthropic", "claude-x"), KEY, retries=0)

    def test_headers_and_models_path(self):
        self.assertEqual(self.client.models(), ["alpha-1", "beta-2"])
        req = self.api.requests[0]
        self.assertEqual(req["path"], "/v1/models")
        self.assertEqual(req["headers"]["x-api-key"], KEY)
        self.assertEqual(req["headers"]["anthropic-version"], ai.ANTHROPIC_VERSION)
        self.assertNotIn("authorization", req["headers"])

    def test_system_prompt_and_tools_are_converted(self):
        self.api.say("done", tokens=(4, 2))
        reply = self.client.chat([{"role": "system", "content": "be brief"},
                                  {"role": "user", "content": "hi"}], tools=[ai.PING_TOOL])
        self.assertEqual((reply.text, reply.usage), ("done", {"input": 4, "output": 2}))
        body = self.api.chats[0]["body"]
        self.assertEqual(self.api.chats[0]["path"], "/v1/messages")
        self.assertEqual(body["system"], "be brief")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["tools"][0]["name"], "ping")
        self.assertEqual(body["tools"][0]["input_schema"]["type"], "object")

    def test_tool_use_blocks_become_tool_calls(self):
        self.api.call(("etrace", {"args": ["validate"]}), text="checking")
        reply = self.client.chat([{"role": "user", "content": "go"}])
        self.assertEqual(reply.text, "checking")
        self.assertEqual([(c.id, c.name, c.args) for c in reply.tool_calls],
                         [("tu_0", "etrace", {"args": ["validate"]})])

    def test_conversation_with_tool_results_is_converted(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "etrace", "arguments": '{"args": ["x"]}'}},
                {"id": "b", "type": "function", "function": {"name": "read", "arguments": '{"path": "data"}'}}]},
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"},
        ]
        system, converted = ai.to_anthropic(messages)
        self.assertEqual(system, "sys")
        self.assertEqual(converted[1]["content"][0], {"type": "text", "text": "ok"})
        self.assertEqual(converted[1]["content"][1], {"type": "tool_use", "id": "a", "name": "etrace",
                                                      "input": {"args": ["x"]}})
        # both results of one turn travel in ONE user message, as the API requires
        self.assertEqual(len(converted), 3)
        self.assertEqual([b["tool_use_id"] for b in converted[2]["content"]], ["a", "b"])

    def test_an_assistant_turn_with_no_text_and_no_calls_is_still_valid(self):
        _, converted = ai.to_anthropic([{"role": "assistant", "content": None}])
        self.assertTrue(converted[0]["content"])

    def test_not_the_anthropic_api(self):
        self.api.script.append({"choices": []})
        with self.assertRaises(ai.AIError):
            self.client.chat([{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
