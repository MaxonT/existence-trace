"""A tiny local AI API for tests: scripted replies in OpenAI or Anthropic shape, every request recorded."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def forbid_real_network(testcase):
    """For the length of TESTCASE, any request to a host other than this machine is an error."""
    import urllib.request
    real = urllib.request.urlopen

    def guarded(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            raise AssertionError(f"a test tried to reach the real network: {host}")
        return real(req, *a, **kw)

    patcher = __import__("unittest.mock", fromlist=["patch"]).patch("urllib.request.urlopen", guarded)
    patcher.start()
    testcase.addCleanup(patcher.stop)


class FakeAPI:
    """kind: 'openai' or 'anthropic'. Queue replies with .reply(...); read .requests afterwards."""

    def __init__(self, kind="openai", models=("alpha-1", "beta-2"), required_key="sk-test-secret-0123"):
        self.kind, self.models, self.required_key = kind, list(models), required_key
        self.requests, self.script, self.fail_next = [], [], []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sent_key(self):
                return (self.headers.get("x-api-key") if api.kind == "anthropic"
                        else (self.headers.get("Authorization") or "").removeprefix("Bearer ")) or ""

            def _authorized(self):
                return api.required_key is None or self._sent_key() == api.required_key

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                api.requests.append({"method": method, "path": self.path, "body": body,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                if not self._authorized():
                    # real services echo (part of) the key they were sent; the client must scrub it
                    return self._send(401, {"error": {"message": "Incorrect API key provided: " + self._sent_key()}})
                if api.fail_next:
                    return self._send(api.fail_next.pop(0), {"error": {"message": "temporarily broken"}})
                if method == "GET" and self.path.endswith("/models"):
                    return self._send(200, {"data": [{"id": m} for m in api.models]})
                if method == "POST" and (self.path.endswith("/chat/completions") or self.path.endswith("/v1/messages")):
                    if not api.script:
                        return self._send(500, {"error": {"message": "the fake API has no more replies"}})
                    return self._send(200, api.script.pop(0))
                return self._send(404, {"error": {"message": "not found"}})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def chats(self):
        return [r for r in self.requests if r["method"] == "POST"]

    # scripted replies -------------------------------------------------------------------

    def say(self, text, tokens=(10, 5)):
        if self.kind == "anthropic":
            self.script.append({"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}})
        else:
            self.script.append({"choices": [{"message": {"role": "assistant", "content": text},
                                             "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}})

    def call(self, *calls, text="", tokens=(10, 5)):
        """Reply with tool calls: each is (name, args-dict)."""
        if self.kind == "anthropic":
            blocks = ([{"type": "text", "text": text}] if text else []) + [
                {"type": "tool_use", "id": f"tu_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
            self.script.append({"content": blocks, "stop_reason": "tool_use",
                                "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}})
        else:
            self.script.append({"choices": [{"message": {"role": "assistant", "content": text or None, "tool_calls": [
                {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}})
