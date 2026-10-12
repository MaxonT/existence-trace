"""The built-in agent behind `etrace run TASK`: the task prompt, an AI API and two tight tools.

The model works only through:

    etrace  runs ONE etrace command (the same commands an external agent would run). Blocked:
            init, schedule, ai, run, and any --config / --json / --pretty / '-' argument. Agent
            JSON or Markdown goes in `files`, which are written to a private temp folder (never
            the data folder) and referenced as "@name" in the arguments.
    read    reads a file, or lists a folder, inside the data folder: journal/, data/, audits/,
            presets/. Read-only, paths must stay inside, trace.toml is not readable.

There is no shell and no network tool, so the worst the model can do is what an etrace command
can do to the data folder, and every one of those commands keeps its own checks (protected
zones, read-back, validate). Each step is returned in the result so the run can be audited.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

from etrace import ai, prompts

BLOCKED = ("init", "schedule", "ai", "run")
READABLE = ("journal", "data", "audits", "presets")
MAX_OUTPUT = 12000          # characters of command output / file text handed back to the model
MAX_LIST = 200
DEFAULT_STEPS = 40
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

TOOLS = [
    {"name": "etrace",
     "description": ("Run one etrace command. `args` is the argument list after `etrace`, for example "
                     "[\"rules\", \"list\"] or [\"pad\", \"add\", \"@points.json\"]. Where the task text says "
                     "`python3 -m etrace X`, call this tool with args X. Do not add --config, --json or --pretty. "
                     "For a FILE argument put its text in `files` ({\"points.json\": \"...\"}) and pass "
                     "\"@points.json\". Returns {exit_code, stdout, stderr}."),
     "parameters": {"type": "object", "properties": {
         "args": {"type": "array", "items": {"type": "string"}},
         "files": {"type": "object", "additionalProperties": {"type": "string"}}},
         "required": ["args"]}},
    {"name": "read",
     "description": ("Read a text file, or list a folder, inside the data folder. Paths are relative, for "
                     "example \"journal/2026-10-12.md\" or \"data\". Only journal/, data/, audits/ and presets/."),
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]

PREFACE = """You are the existence-trace agent. You work only through two tools: `etrace` (run one etrace command) and `read` (read a file or list a folder in the data folder). You have no shell.
Where the task text below says `python3 -m etrace X`, call the etrace tool with args X (a list). Never add --config, --json or --pretty. Put any FILE content in `files` and pass it as "@name".
Follow the task exactly. When it is finished, reply with a short summary and make no further tool call.

---

"""


class AgentError(Exception):
    """The run could not finish (the API failed, or it did not finish within the step limit)."""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


def _clip(text):
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n… (cut: {len(text) - MAX_OUTPUT} more characters)"


class Sandbox:
    """The two tools, bound to one data folder and one trace.toml."""

    def __init__(self, config_path, root):
        self.config_path, self.root = config_path, os.path.realpath(root)

    # etrace

    def run_etrace(self, args, files=None):
        """Run one command in-process. Returns {exit_code, stdout, stderr} (or {error})."""
        if (not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args)):
            return {"error": "args must be a non-empty list of strings"}
        if args[0] in BLOCKED or args[0].startswith("-"):
            return {"error": f"the command {args[0]!r} is not available to the agent"}
        for a in args:
            if a == "-" or a.split("=")[0] in ("--config", "--json", "--pretty", "--no-color"):
                return {"error": f"the argument {a!r} is not allowed (the harness sets the output mode and "
                                 f"the config; pass file content with `files`)"}
        files = files or {}
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            return {"error": "files must be an object of name -> text"}
        bad = [n for n in files if not _NAME.match(n)]
        if bad:
            return {"error": f"bad file name {bad[0]!r}: use letters, digits, . _ - only"}
        tmp = tempfile.mkdtemp(prefix="etrace-agent-")
        try:
            paths = {}
            for name, text in files.items():
                paths[name] = os.path.join(tmp, name)
                with open(paths[name], "w", encoding="utf-8") as fh:
                    fh.write(text)
            final = []
            for a in args:
                if a.startswith("@"):
                    if a[1:] not in paths:
                        return {"error": f"{a!r} is not in `files`"}
                    a = paths[a[1:]]
                final.append(a)
            from etrace import cli  # late: cli imports this module for `etrace run`
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--config", self.config_path, "--json", "--no-color"] + final)
            return {"exit_code": code, "stdout": _clip(out.getvalue()), "stderr": _clip(err.getvalue())}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # read

    def read(self, path):
        if not isinstance(path, str) or not path.strip():
            return {"error": "path must be a non-empty string"}
        rel = os.path.normpath(path.strip().lstrip("/"))
        top = rel.split(os.sep)[0]
        if rel.startswith("..") or top not in READABLE:
            return {"error": f"only {', '.join(READABLE)} inside the data folder can be read"}
        full = os.path.realpath(os.path.join(self.root, rel))
        if full != self.root and not full.startswith(self.root + os.sep):
            return {"error": "that path leaves the data folder"}
        if os.path.isdir(full):
            names = sorted(os.listdir(full))
            more = len(names) - MAX_LIST
            names = [n + ("/" if os.path.isdir(os.path.join(full, n)) else "") for n in names[:MAX_LIST]]
            return {"folder": rel, "entries": names, **({"more": more} if more > 0 else {})}
        if not os.path.exists(full):
            return {"error": f"{rel} does not exist"}
        try:
            with open(full, encoding="utf-8") as fh:
                return {"file": rel, "text": _clip(fh.read())}
        except UnicodeDecodeError:
            return {"error": f"{rel} is not a text file"}
        except OSError as exc:
            return {"error": f"cannot read {rel}: {exc.strerror}"}

    def execute(self, call):
        """The JSON-able result of one tool call from the model."""
        if call.args is None:
            return {"error": "the arguments were not valid JSON; send one JSON object"}
        if call.name == "etrace":
            return self.run_etrace(call.args.get("args"), call.args.get("files"))
        if call.name == "read":
            return self.read(call.args.get("path"))
        return {"error": f"unknown tool {call.name!r}; use etrace or read"}


def _describe(call):
    """A short human label for a tool call, for progress lines and the run record."""
    if call.args is None:
        return call.name
    if call.name == "etrace":
        args = call.args.get("args")
        return "etrace " + " ".join(str(a) for a in args) if isinstance(args, list) else "etrace"
    if call.name == "read":
        return f"read {call.args.get('path')}"
    return call.name


def run_task(cfg, client, task, store, language=None, max_steps=DEFAULT_STEPS, progress=None):
    """Run TASK to the end. Returns the run record; raises AgentError (with .result) if it cannot finish."""
    if task not in prompts.TASKS:
        raise AgentError(f"task: unknown task {task!r}; expected one of {', '.join(prompts.TASKS)}")
    system = PREFACE + prompts.render(task, cfg, store, language)
    sandbox = Sandbox(cfg.source or os.path.join(cfg.data_dir, "trace.toml"), cfg.data_dir)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"Run the {task} task now."}]
    record = {"task": task, "provider": cfg.ai.provider, "model": cfg.ai.model, "steps": [],
              "usage": {"input": 0, "output": 0}, "final": "", "finished": False}
    for _ in range(max_steps):
        try:
            reply = client.chat(messages, TOOLS)
        except ai.AIError as exc:
            raise AgentError(str(exc), record) from None
        for k in ("input", "output"):
            record["usage"][k] += reply.usage.get(k, 0)
        messages.append({"role": "assistant", "content": reply.text or None,
                         **({"tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.name, "arguments": c.raw or "{}"}}
                                            for c in reply.tool_calls]} if reply.tool_calls else {})})
        if not reply.tool_calls:
            record.update(final=reply.text.strip(), finished=True)
            return record
        for call in reply.tool_calls:
            result = sandbox.execute(call)
            step = {"tool": call.name, "call": _describe(call),
                    "exit_code": result.get("exit_code"), "error": result.get("error")}
            record["steps"].append(step)
            if progress:
                progress(step)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    raise AgentError(f"stopped after {max_steps} steps without finishing (raise it with --max-steps)", record)
