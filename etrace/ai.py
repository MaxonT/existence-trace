"""AI API access for `etrace ai` and `etrace run`: key handling, HTTP, two provider adapters.

Standard library only (urllib). Two wire formats cover almost every service:

    openai      POST {base}/chat/completions, GET {base}/models   (OpenAI, DeepSeek, OpenRouter,
                Ollama, Azure-style gateways, any "OpenAI-compatible" address)
    anthropic   POST {base}/v1/messages,      GET {base}/v1/models (Claude)

The key is looked up, in order, in $ETRACE_AI_KEY, in the key file named by [ai].key_file,
then in the environment variable named by [ai].key_env. It is never written to trace.toml
(the data folder is a git repository), never accepted as a command-line argument (shell
history) and never printed: errors and results only carry mask_key(). The key file is created
with mode 600 in a 700 folder.

Conversations use one internal shape, the OpenAI chat shape (system / user / assistant with
tool_calls / tool). The anthropic adapter converts to and from it, so the agent loop does not
care which service it talks to. No model name is hard-coded: `etrace ai models` asks the service.
"""

import dataclasses
import json
import os
import stat
import time
import tomllib
import urllib.error
import urllib.request

PRESETS = {  # short names for `etrace ai set NAME`: (provider, base_url, key_env)
    "openai": ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    "deepseek": ("openai", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("openai", "http://localhost:11434/v1", None),  # local, no key
    "custom": ("openai", None, None),                           # your own address: --url
}
DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
LABELS = {"openai": "OpenAI-compatible", "anthropic": "Anthropic"}
ANTHROPIC_VERSION = "2023-06-01"
ENV_KEY = "ETRACE_AI_KEY"
MAX_ERROR_CHARS = 300


class AIError(Exception):
    """An AI API problem. The message never contains the key."""


# --- the key ------------------------------------------------------------------------------

def default_key_file(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "etrace", "ai.toml")


def mask_key(key):
    """'…abcd' (last four characters only; nothing for a short key)."""
    return "…" + key[-4:] if key and len(key) >= 12 else ("…" if key else "")


def read_key_file(path):
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise AIError("the AI key file does not exist; run `etrace ai set`") from None
    except (OSError, tomllib.TOMLDecodeError):
        raise AIError("the AI key file cannot be read (it must be TOML with: key = \"...\")") from None
    key = data.get("key")
    if not isinstance(key, str) or not key.strip():
        raise AIError("the AI key file has no `key = \"...\"` line")
    return key.strip()


def write_key_file(path, key):
    """Store KEY in PATH (mode 600, folder mode 700). Returns PATH."""
    if not isinstance(key, str) or not key.strip():
        raise AIError("the key is empty")
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# etrace AI API key. Keep this file private (mode 600); never commit it.\n")
        fh.write(f"key = {json.dumps(key.strip())}\n")
    os.chmod(path, 0o600)
    return path


def key_file_exposed(path):
    """True if group or others can read the key file."""
    try:
        return bool(os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO))
    except OSError:
        return False


def resolve_key(ai_cfg, env=None):
    """(key or None, source label). The label says where it came from, never what it is."""
    env = os.environ if env is None else env
    if env.get(ENV_KEY):
        return env[ENV_KEY].strip(), f"env {ENV_KEY}"
    if ai_cfg.key_file and os.path.exists(ai_cfg.key_file):
        return read_key_file(ai_cfg.key_file), f"file {_tilde(ai_cfg.key_file)}"
    if ai_cfg.key_env and env.get(ai_cfg.key_env):
        return env[ai_cfg.key_env].strip(), f"env {ai_cfg.key_env}"
    return None, ""


def _tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def base_url(ai_cfg):
    return (ai_cfg.base_url or DEFAULT_BASE[ai_cfg.provider]).rstrip("/")


def needs_key(ai_cfg):
    """A local service (loopback address) works without a key."""
    host = base_url(ai_cfg).split("//", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    return not (host.startswith("[::1]") or host.split(":")[0] in ("localhost", "127.0.0.1"))


# --- replies ------------------------------------------------------------------------------

@dataclasses.dataclass
class ToolCall:
    id: str
    name: str
    args: dict | None        # None: the model sent arguments that are not valid JSON
    raw: str = ""


@dataclasses.dataclass
class Reply:
    text: str
    tool_calls: list
    usage: dict
    stop: str = ""


# --- the client ---------------------------------------------------------------------------

class Client:
    """One AI API. chat() takes and returns the internal (OpenAI-shaped) messages."""

    def __init__(self, ai_cfg, key, timeout=120, retries=2, sleep=time.sleep):
        if ai_cfg.provider not in LABELS:
            raise AIError("no AI provider is set up; run `etrace ai set`")
        if not key and needs_key(ai_cfg):
            raise AIError("no API key found; run `etrace ai set` (or export ETRACE_AI_KEY)")
        self.cfg, self.key, self.timeout, self.retries, self.sleep = ai_cfg, key, timeout, retries, sleep

    # transport

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json",
             "User-Agent": "existence-trace"}
        if self.cfg.provider == "anthropic":
            h["anthropic-version"] = ANTHROPIC_VERSION
            if self.key:
                h["x-api-key"] = self.key
        elif self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def _scrub(self, text):
        return text.replace(self.key, "***") if self.key else text

    def _call(self, method, path, payload=None):
        url = base_url(self.cfg) + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace")
                finally:
                    exc.close()
                last = AIError(f"the API answered {exc.code}: {self._scrub(_error_text(body))}")
                if exc.code not in (429, 500, 502, 503, 504):
                    raise last from None
            except urllib.error.URLError as exc:
                last = AIError(f"cannot reach {_host(url)}: {self._scrub(str(exc.reason))}")
            except (TimeoutError, OSError) as exc:
                last = AIError(f"cannot reach {_host(url)}: {self._scrub(str(exc))}")
            except ValueError:
                raise AIError(f"{_host(url)} did not answer with JSON (is the address right?)") from None
            if attempt < self.retries:
                self.sleep(2 ** attempt)
        raise last

    # models

    def models(self):
        """Model ids the service offers, sorted."""
        path = "/v1/models" if self.cfg.provider == "anthropic" else "/models"
        data = self._call("GET", path)
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise AIError("the service returned no model list")
        ids = [m.get("id") or m.get("name") for m in items if isinstance(m, dict)]
        return sorted(i for i in ids if isinstance(i, str))

    # chat

    def chat(self, messages, tools=None, max_tokens=4096):
        if self.cfg.provider == "anthropic":
            return self._chat_anthropic(messages, tools, max_tokens)
        return self._chat_openai(messages, tools)

    def _chat_openai(self, messages, tools):
        body = {"model": self.cfg.model, "messages": messages}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        data = self._call("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise AIError("the service answered in an unexpected shape (not OpenAI-compatible?)") from None
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                args = args if isinstance(args, dict) else None
            except ValueError:
                args = None
            calls.append(ToolCall(c.get("id") or f"call_{len(calls)}", fn.get("name", ""), args,
                                  raw if isinstance(raw, str) else json.dumps(raw)))
        usage = data.get("usage") or {}
        return Reply(msg.get("content") or "", calls,
                     {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
                     choice.get("finish_reason") or "")

    def _chat_anthropic(self, messages, tools, max_tokens):
        system, converted = to_anthropic(messages)
        body = {"model": self.cfg.model, "max_tokens": max_tokens, "messages": converted}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t["parameters"]} for t in tools]
        data = self._call("POST", "/v1/messages", body)
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise AIError("the service answered in an unexpected shape (not the Anthropic API?)")
        text, calls = [], []
        for block in data["content"]:
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                args = block.get("input")
                calls.append(ToolCall(block.get("id", ""), block.get("name", ""),
                                      args if isinstance(args, dict) else None, json.dumps(args)))
        usage = data.get("usage") or {}
        return Reply("".join(text), calls,
                     {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)},
                     data.get("stop_reason") or "")


def to_anthropic(messages):
    """(system text, Anthropic messages) from the internal messages."""
    system, out = [], []
    for m in messages:
        role = m["role"]
        if role == "system":
            system.append(m["content"])
        elif role == "user":
            out.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": c["id"], "name": fn["name"], "input": args})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)  # results of one turn travel in one user message
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system), out


def _host(url):
    return url.split("//", 1)[-1].split("/", 1)[0]


def _error_text(body):
    try:
        data = json.loads(body)
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        return str(err)[:MAX_ERROR_CHARS]
    except ValueError:
        return body.strip().replace("\n", " ")[:MAX_ERROR_CHARS] or "no details"


# --- `etrace ai test` ---------------------------------------------------------------------

PING_TOOL = {"name": "ping", "description": "Reply to the user by calling this tool.",
             "parameters": {"type": "object", "properties": {"message": {"type": "string"}},
                            "required": ["message"]}}


def check(client):
    """Probe the API: a plain request, then a tool call (`etrace run` needs tool calling).

    Returns {"ok", "ms", "reply", "tools", "error"}: ok is False only when the plain request
    failed; tools is True/False/None (None: not tried).
    """
    out = {"ok": False, "ms": None, "reply": None, "tools": None, "error": None,
           "provider": client.cfg.provider, "model": client.cfg.model}
    start = time.monotonic()
    try:
        reply = client.chat([{"role": "user", "content": "Reply with exactly one word: ok"}], max_tokens=16)
    except AIError as exc:
        out["error"] = str(exc)
        return out
    out.update(ok=True, ms=round((time.monotonic() - start) * 1000), reply=reply.text.strip()[:60])
    try:
        probe = client.chat([{"role": "user", "content": "Call the ping tool with the message hello."}],
                            tools=[PING_TOOL], max_tokens=64)
        out["tools"] = any(c.name == "ping" for c in probe.tool_calls)
    except AIError as exc:
        out["tools"] = False
        out["tools_error"] = str(exc)
    return out
