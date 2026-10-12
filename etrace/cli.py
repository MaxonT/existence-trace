"""The `python3 -m etrace` command line: one entry point for users and agents.

Every command prints one JSON object on stdout when the output is piped (agents,
scripts). In a terminal it prints a pretty, coloured view instead; --json and
--pretty force one or the other, --no-color turns colour off (NO_COLOR works,
FORCE_COLOR turns it on). The three flags may sit anywhere on the line. Exit codes:

    0  done
    1  the command ran but something failed (validate problems, a failed
       email, an unreadable note, a git error...)
    2  bad input: unknown command or option, bad config, or agent JSON that
       does not pass the field checks (one 'field: message' line per error
       on stderr)

FILE arguments hold agent JSON; '-' reads stdin. The config is found via
--config, then $ETRACE_CONFIG, then ./trace.toml, then
~/existence-trace-data/trace.toml. Its data_dir is the data root:

    ROOT/trace.toml  journal/  data/  audits/  charts/  presets/NAME/
"""

import argparse
import datetime
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from etrace import __version__, agent, ai, chart, checkpoint, config, importer, journal, presets, prompts, render, ui
from etrace.adapters import notion, scheduler
from etrace.container import audit, decisions, feedback, questions, rules, usage, validate
from etrace.models import g, pad, xp
from etrace.outreach import email as mail
from etrace.outreach import note
from etrace.store import Store, StoreError, write_text_atomic

ENV_CONFIG = "ETRACE_CONFIG"
DEFAULT_ROOT = "~/existence-trace-data"
DIRS = ("journal", "data", "audits", "charts", "presets")
# ai blocks an agent writes as free text; questions/pad/g are written by their commands.
TEXT_BLOCKS = ("headline", "xp", "narrative")


class InputError(Exception):
    """Bad agent input or arguments. .errors lists 'field: message' strings."""

    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class Failure(Exception):
    """The command ran but failed; exit 1. .result is still printed."""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class RawText(str):
    """A command result printed as plain text instead of JSON (prompt --raw)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(f"usage: {message}")

    def format_help(self):
        return render.command_help(ui.style_for(sys.stdout), *_help_parts(self)) + "\n"


# How `etrace --help` groups the commands; test_cli_pretty checks that every command is listed.
HELP_GROUPS = (
    ("Start", ("init", "ai", "run", "validate")),
    ("Journal", ("note", "import")),
    ("Lenses", ("pad", "g", "xp")),
    ("The loop", ("question", "feedback", "decide", "usage", "outreach", "rules", "audit")),
    ("Tools", ("prompt", "schedule", "checkpoint", "notion")),
)


# One line for each subcommand that argparse was given no help text for.
COMMAND_HELP = {
    "note new": "create the note for a day (default today)",
    "question add": "file an evidence-backed question for the user",
    "feedback ingest": "take new feedback from the notes and close answered questions",
    "usage add": "store a usage receipt (the write must be read back first)",
    "outreach send": "send the day's questions or a report to the user",
    "pad add": "record PAD readings",
    "pad chart": "draw the PAD chart and write it into the note",
    "g predict": "freeze a G prediction for a day",
    "g feedback": "record feedback on a frozen G prediction",
    "g report": "write the G report block, with the calibration status",
    "g chart": "draw the B / E / G trend chart",
    "xp score": "score a day's XP and record it",
    "audit weekly": "audit every feedback chain of a week",
    "rules list": "list the preference rules",
}
DEFAULT_ARG_HELP = {  # arguments that had no help text
    "file": "agent JSON file; '-' reads stdin",
    "date": "YYYY-MM-DD, 'today' or 'yesterday'",
    "block": "which ai block of the note to write",
    "task": "which agent task",
    "kind": "what to send: the day's open questions, or a report",
    "summary": "one or two sentences for the report",
    "as_of": "any date inside the week to audit (default today)",
    "message": "commit message for the checkpoint",
    "scope": "only rules of this scope",
    "target": "which agent runner to write files for",
    "preset": "which bundled preset to start from",
    "language": "language of the notes and prompts",
    "timezone": "IANA timezone that decides what 'today' is",
}


def _walk(parser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _walk(child)


def _describe(root):
    """Fill in the summaries and argument help that --help shows."""
    for parser in _walk(root):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                helped = {c.dest: c.help for c in action._choices_actions}
                for name, child in action.choices.items():
                    path = child.prog.removeprefix("etrace").strip()
                    child.description = child.description or helped.get(name) or COMMAND_HELP.get(path)
            elif action.help is None and action.dest in DEFAULT_ARG_HELP:
                action.help = DEFAULT_ARG_HELP[action.dest]


def _invocation(action):
    if not action.option_strings:
        return action.metavar or (f"{{{','.join(action.choices)}}}" if action.choices else action.dest)
    names = ", ".join(action.option_strings)
    if action.nargs == 0:
        return names
    value = action.metavar or (f"{{{','.join(action.choices)}}}" if action.choices else action.dest.upper())
    return f"{names} {value}"


def _arg_text(action):
    text = action.help or ""
    skip = (None, False, argparse.SUPPRESS)
    if (action.option_strings and action.nargs != 0 and action.default not in skip
            and "default" not in text.lower()):
        text = f"{text} (default {action.default})".strip()
    return text


def _help_parts(parser):
    """(path, usage, summary, sections) for render.command_help."""
    # Python 3.14's argparse colours its own usage line in a terminal: strip that first.
    usage = " ".join(ui.strip_ansi(parser.format_usage()).split()).removeprefix("usage:").strip().replace("[-h] ", "")
    commands, arguments, options = [], [], []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands = [(name, child.description or "") for name, child in action.choices.items()]
        elif isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS:
            continue
        elif action.option_strings:
            options.append((_invocation(action), _arg_text(action)))
        else:
            arguments.append((_invocation(action), _arg_text(action)))
    options.append(("-h, --help", "this screen"))
    path = parser.prog.removeprefix("etrace").strip()
    return path, usage, parser.description or "", [("Commands", commands), ("Arguments", arguments),
                                                    ("Options", options)]


class _Root(_Parser):
    """The top-level parser: its --help is the banner screen, not argparse's default."""

    def format_help(self):
        commands = {}
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                commands = {a.dest: a.help or "" for a in action._choices_actions}
        return render.help_screen(ui.style_for(sys.stdout), HELP_GROUPS, commands) + "\n"


# --- context -------------------------------------------------------------------

class Context:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = cfg.data_dir
        self.data = os.path.join(self.root, "data")
        self.journal = os.path.join(self.root, "journal")
        self.audits = os.path.join(self.root, "audits")
        self.charts = os.path.join(self.root, "charts")
        self.lang = cfg.language
        self.tz = ZoneInfo(cfg.timezone)
        self._preset = None

    @property
    def store(self):
        return Store(self.data)

    @property
    def preset(self):
        if self._preset is None:
            self._preset = load_preset(self.root, self.cfg.preset)
        return self._preset

    def now(self):
        return datetime.datetime.now(self.tz)

    def today(self):
        return self.now().date()

    def day(self, value):
        """'today', 'yesterday' or YYYY-MM-DD -> ISO date string."""
        if value in (None, "today"):
            return self.today().isoformat()
        if value == "yesterday":
            return (self.today() - datetime.timedelta(days=1)).isoformat()
        try:
            return datetime.date.fromisoformat(value).isoformat()
        except (TypeError, ValueError):
            raise InputError(f"date: must be YYYY-MM-DD, 'today' or 'yesterday', got {value!r}") from None


def load_preset(root, name):
    """The data root's copy of preset NAME if init made one, else the bundled preset."""
    local = os.path.join(root, "presets")
    if os.path.isdir(os.path.join(local, name)):
        return presets.load(name, local)
    return presets.load(name)


def find_config(path=None):
    candidates = [path] if path else [
        os.environ.get(ENV_CONFIG),
        os.path.join(os.getcwd(), "trace.toml"),
        os.path.join(os.path.expanduser(DEFAULT_ROOT), "trace.toml"),
    ]
    for candidate in candidates:
        if candidate and (path or os.path.exists(candidate)):
            return candidate
    raise config.ConfigError("no trace.toml found; run `etrace init` (or `etrace import PATH`) first, "
                             f"or pass --config / set ${ENV_CONFIG}")


# --- agent JSON ------------------------------------------------------------------

def read_json(path):
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
    except OSError as exc:
        raise InputError(f"file: cannot read {path}: {exc.strerror}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"json: line {exc.lineno} column {exc.colno}: {exc.msg}") from None


def check_fields(obj, required, optional=(), where=""):
    """Field-level shape check: obj must be an object with the required keys and no others."""
    if not isinstance(obj, dict):
        raise InputError(f"{where or 'input'}: must be a JSON object")
    errors = [f"{where}{k}: required" for k in required if k not in obj]
    errors += [f"{where}{k}: unknown field" for k in obj if k not in required and k not in optional]
    if errors:
        raise InputError(errors)
    return obj


def _errors(exc):
    """'field: message' lines from a module exception."""
    errors = getattr(exc, "errors", None)
    if isinstance(errors, list) and errors and all(isinstance(e, str) for e in errors):
        return errors
    return [str(exc)]


def _aware(value, field):
    if not isinstance(value, str):
        raise InputError(f"{field}: must be an ISO datetime string with a UTC offset")
    try:
        at = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise InputError(f"{field}: not an ISO datetime: {value!r}") from None
    if at.tzinfo is None or at.utcoffset() is None:
        raise InputError(f"{field}: needs a UTC offset (e.g. 2026-10-07T20:30:00-05:00)")
    return at


# --- commands ----------------------------------------------------------------------

def cmd_init(args):
    if args.preset not in presets.NAMES:
        raise InputError(f"preset: unknown preset {args.preset!r}; expected one of {', '.join(presets.NAMES)}")
    root = os.path.abspath(os.path.expanduser(args.dir or DEFAULT_ROOT))
    taken = [n for n in ("trace.toml",) + DIRS if os.path.exists(os.path.join(root, n))]
    if taken:
        raise InputError(f"dir: {root} is not a fresh data dir (already has {', '.join(taken)})")
    try:
        config.from_dict({"timezone": args.timezone, "language": args.language, "preset": args.preset})
    except config.ConfigError as exc:
        raise InputError(str(exc)) from None

    for name in DIRS:
        os.makedirs(os.path.join(root, name), exist_ok=True)
    shutil.copytree(presets.preset_dir(args.preset), os.path.join(root, "presets", args.preset))
    toml = (
        "# existence-trace settings; see examples/trace.toml in the repository for every key.\n"
        f"timezone = {json.dumps(args.timezone)}\n"
        'data_dir = "."\n'
        f"preset = {json.dumps(args.preset)}\n"
        f"language = {json.dumps(args.language)}\n"
        'outreach = "note"\n'
    )
    with open(os.path.join(root, "trace.toml"), "w", encoding="utf-8") as fh:
        fh.write(toml)

    ctx = Context(config.load(os.path.join(root, "trace.toml")))
    now = ctx.now()
    for rule in ctx.preset["rules"]["rules"]:
        rules.add_rule(ctx.store, rule["id"], rule["scope"], presets.label(rule["text"], ctx.lang),
                       now.isoformat())
    path = journal.create_note(ctx.journal, ctx.today(), args.preset, ctx.lang)
    return {"root": root, "config": os.path.join(root, "trace.toml"), "preset": args.preset,
            "rules_version": rules.version(ctx.store), "note": path}


def cmd_note_new(ctx, args):
    path = journal.create_note(ctx.journal, ctx.day(args.date), ctx.cfg.preset, ctx.lang)
    return {"note": path}


def _open_in_editor(path):
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        cmd = shlex.split(editor) + [path]
    elif sys.platform == "darwin":
        cmd = ["open", "-t", path]
    elif sys.platform.startswith("linux"):
        cmd = ["xdg-open", path]
    else:
        raise InputError("note: set $EDITOR to open the note from here")
    try:
        subprocess.run(cmd, check=False)
    except OSError as exc:
        raise Failure(f"cannot start {cmd[0]}: {exc.strerror}") from None


def cmd_note_today(ctx, args):
    """`etrace note`: make sure today's note exists, say where it is, optionally open it."""
    day = ctx.day(None)
    path = journal.note_path(ctx.journal, day)
    created = not os.path.exists(path)
    if created:
        journal.create_note(ctx.journal, day, ctx.cfg.preset, ctx.lang)
    if args.edit:
        _open_in_editor(path)
    return {"note": path, "date": day, "created": created}


def _read_text(path):
    try:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise InputError(f"file: cannot read {path}: {exc.strerror}") from None


def cmd_note_write(ctx, args):
    day = ctx.day(args.date)
    note_file = journal.note_path(ctx.journal, day)
    if not os.path.exists(note_file):
        raise InputError(f"date: no note for {day}; run `note new {day}` first")
    text = _read_text(args.file).strip("\n")
    doc = journal.load(note_file)
    journal.replace_ai_block(doc, args.block, text)
    journal.save(doc, note_file)
    out = {"note": note_file, "block": f"ai:{args.block}"}
    if text not in journal.load(note_file).block(f"ai:{args.block}").content():
        raise Failure(f"ai:{args.block} text not found in {note_file} after writing", out)
    return out


def cmd_prompt(ctx, args):
    day = ctx.day(args.date) if args.date else None
    text = prompts.render(args.task, ctx.cfg, ctx.store, args.language, day=day)
    if args.raw:
        return RawText(text)
    return {"task": args.task, "language": args.language or ctx.lang, "chars": len(text), "prompt": text}


def cmd_question_add(ctx, args):
    obj = check_fields(read_json(args.file), ("source", "date", "evidence", "candidates",
                                              "uncertainty", "dimensions", "dedupe_key"))
    rec = questions.add_question(ctx.store, **obj)
    return {"question": rec}


def _answer_questions(ctx, records):
    """Mark open questions answered by feedback that starts with [Q-id]."""
    answered = []
    for rec in records:
        qid = rec.get("question_id")
        if not qid:
            continue
        try:
            q = questions.get(ctx.store, qid)
        except KeyError:
            continue
        if q["status"] == questions.OPEN:
            questions.answer(ctx.store, qid, rec["id"])
            answered.append(qid)
    return answered


def cmd_feedback_ingest(ctx, args):
    result = feedback.ingest_notes(ctx.journal, ctx.store)
    out = {"added": [r["id"] for r in result.added], "unchanged": result.unchanged,
           "note_errors": [f"{name}: {msg}" for name, msg in result.errors]}
    new = list(result.added)
    problems = list(out["note_errors"])
    if ctx.cfg.email_enabled:
        replies = mail.read_replies(ctx.store, mail.load_credentials(ctx.cfg.credentials["email"]))
        out["email_added"] = [r["id"] for r in replies.added]
        new += replies.added
        if not replies.ok:
            out["email_error"] = replies.error
            problems.append(f"email: {replies.error}")
    out["answered"] = _answer_questions(ctx, new)
    decided = {fid for d in decisions.all_decisions(ctx.store) for fid in d.get("feedback_ids") or []}
    out["pending"] = [r["id"] for r in feedback.current(ctx.store) if r["id"] not in decided]
    out["scan_ok"] = not problems
    if problems:
        raise Failure("; ".join(problems), out)
    return out


def cmd_decide(ctx, args):
    obj = check_fields(read_json(args.file), ("feedback_ids", "decision_text", "value_source"),
                       ("rule_changes",))
    return {"decision": decisions.decide(ctx.store, **obj)}


def cmd_usage_add(ctx, args):
    obj = check_fields(read_json(args.file),
                       ("task", "target", "rule_version", "before", "after", "status", "verified_readback"),
                       ("feedback_ids", "decision_ids"))
    obj.setdefault("feedback_ids", [])
    obj.setdefault("decision_ids", [])
    return {"receipt": usage.record(ctx.store, **obj)}


def cmd_outreach_send(ctx, args):
    day = ctx.day(args.date)
    scan_ok = args.scan == "ok"
    dids = args.decision or None
    res = note.write(ctx.journal, ctx.store, day, scan_ok, ctx.lang, ctx.cfg.preset,
                     decision_ids=dids, summary=args.summary)
    out = {"note": {"path": res.path, "recorded": res.recorded, "notification": res.notification["id"]}}
    if ctx.cfg.email_enabled:
        creds = mail.load_credentials(ctx.cfg.credentials["email"])
        sent = mail.send(ctx.store, creds, day, scan_ok, ctx.lang, args.kind,
                         decision_ids=dids, summary=args.summary)
        out["email"] = {"status": sent.status, "recorded": sent.recorded,
                        "notification": sent.notification["id"],
                        "reason": sent.notification.get("reason")}
        if sent.status != "sent":
            raise Failure(f"email {sent.status}: {sent.notification.get('reason')}", out)
    return out


def _pad_points(obj):
    if isinstance(obj, dict):
        obj = check_fields(obj, ("points",))["points"]
    if not isinstance(obj, list) or not obj:
        raise InputError("points: must be a non-empty list of PAD points")
    points, errors = [], []
    for i, item in enumerate(obj):
        try:
            points.append(pad.PadPoint.from_dict(item))
        except pad.PadError as exc:
            errors += [f"points[{i}].{e}" for e in exc.errors]
    if errors:
        raise InputError(errors)
    return points


def cmd_pad_add(ctx, args):
    points = _pad_points(read_json(args.file))
    added = pad.record(ctx.store, points)
    days = {}
    for day in sorted({pt.date for pt in points}):
        current = pad.points_for(ctx.store, day)
        summary = pad.daily_summary(current)
        days[day] = {"points": len(current), "problems": pad.validate_day(current).problems,
                     "summary": {"P": summary.P, "A": summary.A, "D": summary.D,
                                 "unknown_durations": summary.unknown_durations}}
    return {"added": [r["id"] for r in added], "days": days}


def _pad_block(ctx, day, points, chart_file):
    head = {"zh": ("时段", "词", "来源"), "en": ("Slot", "Word", "Source")}[ctx.lang]
    lines = [f"![PAD](../charts/{chart_file})", "",
             f"| {head[0]} | P | A | D | {head[1]} | {head[2]} |", "| --- | --- | --- | --- | --- | --- |"]
    for pt in points:
        word = pad.project(pt.P, pt.A, pt.D, ctx.preset, ctx.lang)
        lines.append(f"| {pt.slot} | {pt.P:g} | {pt.A:g} | {pt.D:g} | {word} | {pt.provenance} |")
    return "\n".join(lines)


def cmd_pad_chart(ctx, args):
    day = ctx.day(args.date)
    if args.week:
        monday = datetime.date.fromisoformat(day)
        monday -= datetime.timedelta(days=monday.weekday())
        dates = [(monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]
        points = [pt for d in dates for pt in pad.points_for(ctx.store, d)]
        name = f"{audit.week_id(monday)}-pad.svg"
    else:
        points = pad.points_for(ctx.store, day)
        name = f"{day}-pad.svg"
    if not points:
        raise InputError(f"date: no PAD points stored for {'the week of ' if args.week else ''}{day}; "
                         "run `pad add FILE` first")
    svg = chart.pad_svg(points, "week" if args.week else "day", ctx.lang, ctx.preset)
    os.makedirs(ctx.charts, exist_ok=True)
    path = os.path.join(ctx.charts, name)
    write_text_atomic(path, svg)
    out = {"chart": path, "points": len(points)}
    note_file = journal.note_path(ctx.journal, day)
    if not args.week and os.path.exists(note_file):
        doc = journal.load(note_file)
        text = _pad_block(ctx, day, points, name)
        journal.replace_ai_block(doc, "pad", text)
        journal.save(doc, note_file)
        if text not in journal.load(note_file).block("ai:pad").content():
            raise Failure(f"PAD table not found in {note_file} after writing", out)
        out["note"] = note_file
    return out


def cmd_g_predict(ctx, args):
    obj = check_fields(read_json(args.file), ("date", "items"))
    if not isinstance(obj["items"], list):
        raise InputError("items: must be a list of G items")
    day = ctx.day(obj["date"])
    g.record(ctx.store, day, obj["items"])
    snap = g.predict(ctx.store, day, ctx.preset)
    res = g.compute(snap["items"], ctx.preset)
    return {"prediction": snap["id"], "date": day, "revision": snap["revision"],
            "filled": [i["code"] for i in snap["items"] if i["provenance"] == "gap_fill"],
            "training_eligible": snap["training_eligible"], "B": res.B, "E": res.E, "G": res.G}


def cmd_g_feedback(ctx, args):
    obj = check_fields(read_json(args.file), ("prediction_id",), ("targets", "text", "expressed_at"))
    if "expressed_at" in obj:
        obj["expressed_at"] = _aware(obj["expressed_at"], "expressed_at")
    return {"feedback": g.feedback(ctx.store, **obj)}


def _calibrated_g(preset, res, cal):
    """G from calibrated item values (None unless all eight items are present)."""
    if res.missing:
        return None
    groups = [sum(w["weight"] * cal.apply(code, res.items[code].value)
                  for code, w in preset["g"]["groups"][grp]["items"].items())
              for grp in g.GROUPS]
    return sum(groups) / len(groups)


def _g_text(ctx, day, res, cal):
    lang = ctx.lang
    names = {code: presets.label(name, lang) for code, _, name in presets.g_items(ctx.preset)}
    head = {"zh": ("项目", "分数", "来源"), "en": ("Item", "Score", "Source")}[lang]
    lines = [f"| {head[0]} | {head[1]} | {head[2]} |", "| --- | --- | --- |"]
    for code in g.CODES:
        item = res.items.get(code)
        value = g.display(item.value if item else None, lang)
        lines.append(f"| {code} {names[code]} | {value} | {item.provenance if item else ''} |")
    lines += ["", f"B {g.display(res.B, lang)} · E {g.display(res.E, lang)} · G {g.display(res.G, lang)}"]
    if cal.enabled:
        label = {"zh": "校准后 G", "en": "Calibrated G"}[lang]
        lines.append(f"{label} {g.display(_calibrated_g(ctx.preset, res, cal), lang)}")
    else:
        label = {"zh": "校准未启用", "en": "Calibration off"}[lang]
        lines.append(f"{label}: {cal.reason}")
    return "\n".join(lines + ["", g.legend(ctx.preset, lang)])


def cmd_g_chart(ctx, args):
    if args.days < 1:
        raise InputError("--days must be at least 1")
    end = datetime.date.fromisoformat(ctx.day(args.date))
    series = []
    for i in range(args.days - 1, -1, -1):
        day = (end - datetime.timedelta(days=i)).isoformat()
        rec = g.latest(ctx.store, day)
        if rec is not None:
            res = g.compute(rec["items"], ctx.preset)
            series.append((day, {"B": res.B, "E": res.E, "G": res.G}))
    counts = {k: sum(1 for _, v in series if v[k] is not None) for k in chart.G_LINES}
    if max(counts.values(), default=0) < chart.G_MIN_POINTS:
        raise InputError(f"date: need at least {chart.G_MIN_POINTS} days with B, E or G in the "
                         f"{args.days} days up to {end}; have {counts}")
    svg = chart.g_svg(series, ctx.lang)
    os.makedirs(ctx.charts, exist_ok=True)
    path = os.path.join(ctx.charts, f"{end}-g-{args.days}d.svg")
    write_text_atomic(path, svg)
    trend = {k: [v[k] for _, v in series] for k in chart.G_LINES}
    return {"chart": path, "days": len(series), "points": counts, "trend": trend}


# --- ai: `etrace ai ...` and `etrace run` ---------------------------------------------------

def _ai_status(cfg):
    a = cfg.ai
    found, source, problem, exposed = None, "", None, False
    if a.provider:
        try:
            found, source = ai.resolve_key(a)
        except ai.AIError as exc:
            problem = str(exc)
        exposed = bool(a.key_file and source.startswith("file") and ai.key_file_exposed(a.key_file))
    return {"configured": a.configured, "provider": a.provider, "service": ai.LABELS.get(a.provider),
            "model": a.model, "base_url": ai.base_url(a) if a.provider else None,
            "key": {"needed": ai.needs_key(a) if a.provider else None, "found": bool(found),
                    "source": source, "masked": ai.mask_key(found), "exposed": exposed, "problem": problem},
            "config": cfg.source}


def _client(cfg, need_model=True):
    a = cfg.ai
    if not a.provider:
        raise InputError("ai: no AI is set up yet; run `etrace ai set`")
    if need_model and not a.model:
        raise InputError("ai: no model chosen; run `etrace ai models`, then `etrace ai set --model NAME`")
    try:
        return ai.Client(a, ai.resolve_key(a)[0])
    except ai.AIError as exc:
        raise InputError(f"ai: {exc}") from None


def cmd_ai_status(ctx, args):
    return _ai_status(ctx.cfg)


def cmd_ai_models(ctx, args):
    client = _client(ctx.cfg, need_model=False)
    try:
        ids = client.models()
    except ai.AIError as exc:
        raise Failure(str(exc)) from None
    return {"models": ids, "count": len(ids), "current": ctx.cfg.ai.model}


def cmd_ai_test(ctx, args):
    result = ai.check(_client(ctx.cfg))
    if not result["ok"]:
        raise Failure(result["error"], result)
    return result


def _say(text):
    sys.stderr.write(text)
    sys.stderr.flush()


def _ask(prompt):
    _say(prompt)
    return sys.stdin.readline().strip()


def _choose(label, options, default=None):
    """Pick one of OPTIONS by number or name; Enter takes DEFAULT."""
    for i, name in enumerate(options, 1):
        _say(f"  {i:>2}  {name}\n")
    while True:
        answer = _ask(f"{label}{f' [{default}]' if default else ''}: ") or (default or "")
        if answer in options:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        _say("  not one of the choices, try again\n")


def cmd_ai_set(ctx, args):
    cur, interactive = ctx.cfg.ai, sys.stdin.isatty()
    name = args.service
    if name is None and not (args.url or args.provider or cur.provider) and interactive:
        _say("Which AI service?\n")
        name = _choose("service", list(ai.PRESETS), default="openai")
    provider, url, key_env = cur.provider, cur.base_url, cur.key_env
    if name:
        provider, url, key_env = ai.PRESETS[name]
    url = args.url or url
    provider = args.provider or provider or ("openai" if url else None)  # an address alone means OpenAI-compatible
    key_env = args.key_env or key_env
    if not provider:
        raise InputError("ai: say which service: etrace ai set openai | anthropic | deepseek | openrouter | "
                         "ollama | custom --url URL")
    if name == "custom" and not url:
        raise InputError("ai: `custom` needs --url (the address of an OpenAI-compatible API)")

    key_file = cur.key_file if not name else None  # a new service needs its own key
    candidate = config.AIConfig(provider, None, url, key_env, key_file)
    secret = None
    if args.key_stdin:
        secret = sys.stdin.read().strip()
        if not secret:
            raise InputError("ai: --key-stdin got no key on standard input")
    elif (interactive and name != "ollama" and not os.environ.get(key_env or "")
          and not (key_file and os.path.exists(key_file))):
        secret = getpass.getpass("API key (hidden; Enter to skip): ", stream=sys.stderr).strip() or None
    if secret:
        try:
            key_file = ai.write_key_file(os.path.expanduser(args.key_file or key_file or ai.default_key_file()),
                                         secret)
        except (ai.AIError, OSError) as exc:
            raise Failure(f"could not store the key: {exc}") from None

    model = args.model or (cur.model if not name else None)
    if not model and interactive:
        model = _pick_model(config.AIConfig(provider, None, url, key_env, key_file))
    stored_url = None if url == ai.DEFAULT_BASE.get(provider) else url
    try:
        new_cfg = config.write_ai(ctx.cfg.source, {
            "provider": provider, "model": model, "base_url": stored_url, "key_env": key_env,
            "key_file": ai._tilde(key_file) if key_file else None})
    except config.ConfigError as exc:
        raise InputError(f"ai: {exc}") from None

    out = dict(_ai_status(new_cfg), saved=True)
    if new_cfg.ai.configured and not args.no_test:
        try:
            out["test"] = ai.check(ai.Client(new_cfg.ai, ai.resolve_key(new_cfg.ai)[0]))
        except ai.AIError as exc:
            out["test"] = {"ok": False, "error": str(exc)}
        if not out["test"]["ok"]:
            raise Failure("saved, but the test call failed: " + str(out["test"]["error"]), out)
    elif not new_cfg.ai.configured:
        out["next"] = "etrace ai models   then   etrace ai set --model NAME"
    return out


def _pick_model(candidate):
    """Interactive: list the service's models and let the user pick one (or type a name)."""
    try:
        key = ai.resolve_key(candidate)[0]
        ids = ai.Client(candidate, key, retries=0).models()  # no waiting on a service that is not there
    except ai.AIError as exc:
        _say(f"(could not list models: {exc})\n")
        return _ask("model name: ") or None
    shown = ids[:15]
    _say(f"Models ({len(ids)} found; showing {len(shown)}):\n")
    for i, m in enumerate(shown, 1):
        _say(f"  {i:>2}  {m}\n")
    answer = _ask("model (number or name): ")
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    return answer or None


def cmd_run(ctx, args):
    client = _client(ctx.cfg)
    try:
        return agent.run_task(ctx.cfg, client, args.task, ctx.store, args.language, args.max_steps,
                              getattr(args, "progress", None), day=ctx.day(args.date) if args.date else None)
    except agent.AgentError as exc:
        raise Failure(str(exc), exc.result) from None


# --- import: `etrace import PATH` -------------------------------------------------------------

def _within(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def cmd_import(ctx, args):
    """Bring an existing diary in: each day's text goes into that day's note, in the protected
    'Original version' block (append-only), so the words stay word for word."""
    paths = [os.path.abspath(os.path.expanduser(p)) for p in args.paths]
    for p in paths:
        if not os.path.exists(p):
            raise InputError(f"paths: not found: {p}")
        if _within(p, ctx.journal) or _within(ctx.journal, p):
            raise InputError(f"paths: {p} is your own journal folder (or holds it); "
                             "point at the diary you want to bring in")
    if args.days < 1:
        raise InputError("days: must be at least 1")
    client = _client(ctx.cfg) if args.run and not args.dry_run else None  # fail before touching anything
    default = datetime.date.fromisoformat(ctx.day(args.date)) if args.date else None
    entries, skipped = importer.scan(paths, ctx.tz, default, args.mtime)
    today = ctx.today()
    imported, kept = [], []
    for e in entries:
        if e["date"] > today:
            skipped.append({"path": f"{e['source']} ({e['date'].isoformat()})", "reason": "a date in the future"})
        elif journal._ANY_MARKER.search(e["text"]):
            skipped.append({"path": f"{e['source']} ({e['date'].isoformat()})",
                            "reason": "contains an etrace marker comment (<!-- trace: ...); remove it first"})
        else:
            kept.append(e)
    for e in kept:
        day = e["date"].isoformat()
        path = journal.note_path(ctx.journal, day)
        created = not os.path.exists(path)
        row = {"date": day, "source": e["source"], "chars": len(e["text"]), "created": created,
               "status": "would add" if args.dry_run else "added"}
        if not args.dry_run:
            try:
                doc = (journal.new_note(day, ctx.cfg.preset, ctx.lang) if created else journal.load(path))
                before = doc.render()
                journal.append_verbatim(doc, e["text"], written_on=today, happened_on=day, language=ctx.lang)
                if doc.render() == before:
                    row["status"] = "already there"
                else:
                    journal.save(doc, path)
            except journal.JournalError as exc:
                skipped.append({"path": f"{e['source']} ({day})", "reason": str(exc)})
                continue
        imported.append(row)
    days = sorted({r["date"] for r in imported})
    out = {"dry_run": bool(args.dry_run), "entries": len(imported), "days": len(days),
           "first": days[0] if days else None, "last": days[-1] if days else None,
           "added": sum(r["status"] == "added" for r in imported),
           "unchanged": sum(r["status"] == "already there" for r in imported),
           "new_notes": sum(r["created"] and r["status"] != "already there" for r in imported),
           "imported": imported, "skipped": skipped, "ran": [],
           "paths": list(args.paths), "ai_ready": bool(ctx.cfg.ai.configured)}
    if client and days:
        progress = getattr(args, "progress", None)
        for day in days[-args.days:]:
            if progress:
                progress({"day": day})
            try:
                rec = agent.run_task(ctx.cfg, client, "daily", ctx.store, None, agent.DEFAULT_STEPS, progress, day=day)
            except agent.AgentError as exc:
                out["ran"].append({"date": day, "finished": False, "steps": len((exc.result or {}).get("steps", [])),
                                   "error": str(exc)})
                raise Failure(f"the AI stopped on {day}: {exc}", out) from None
            out["ran"].append({"date": day, "finished": rec["finished"], "steps": len(rec["steps"]),
                               "usage": rec["usage"]})
            if not rec["finished"]:
                raise Failure(f"the AI did not finish {day}", out)
    return out


def _system_timezone():
    """The machine's IANA timezone name, or the default if it cannot be told."""
    target = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in target:
        name = target.split("zoneinfo/", 1)[1]
        try:
            ZoneInfo(name)
            return name
        except (ValueError, OSError):
            pass
    return config.Config.timezone


def _guess_language(entries):
    """'zh' if most of the imported text is Chinese, else 'en'."""
    text = "".join(e["text"][:2000] for e in entries[:50])
    letters = [c for c in text if c.isalpha()]
    cjk = sum("\u4e00" <= c <= "\u9fff" for c in letters)
    return "zh" if letters and cjk / len(letters) > 0.3 else "en"


def _locale_language():
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if value:
            return "zh" if value.lower().startswith("zh") else "en"
    return "en"


def _config_or_new(args, entries=None):
    """(path to trace.toml, init result or None): the user's data folder, made first if there is none."""
    try:
        return find_config(args.config), None
    except config.ConfigError:
        pass
    made = cmd_init(argparse.Namespace(preset="default", dir=None, timezone=_system_timezone(),
                                       language=_guess_language(entries) if entries else _locale_language()))
    return made["config"], made


def cmd_import_start(args):
    """`etrace import`: finds your data folder, or makes one first, then imports."""
    made = None
    try:
        cfg_path = find_config(args.config)
    except config.ConfigError:
        paths = [os.path.abspath(os.path.expanduser(p)) for p in args.paths]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            raise InputError(f"paths: not found: {missing[0]}") from None
        entries, _ = importer.scan(paths, ZoneInfo(_system_timezone()))
        if args.dry_run:  # no folder yet: preview against a throwaway one, leave nothing behind
            with tempfile.TemporaryDirectory(prefix="etrace-preview-") as tmp:
                fresh = cmd_init(argparse.Namespace(preset="default", dir=os.path.join(tmp, "d"), timezone=_system_timezone(),
                                                    language=_guess_language(entries)))
                out = cmd_import(Context(config.load(fresh["config"])), args)
            out["no_folder_yet"] = True
            return out
        cfg_path, made = _config_or_new(args, entries)
    out = cmd_import(Context(config.load(cfg_path)), args)
    if made:
        out["created_folder"] = made["root"]
    return out


def cmd_ai_set_start(args):
    """`etrace ai set` works on a fresh machine too: it makes the data folder first."""
    cfg_path, made = _config_or_new(args)
    out = cmd_ai_set(Context(config.load(cfg_path)), args)
    if made:
        out["created_folder"] = made["root"]
    return out


def cmd_g_report(ctx, args):
    day = ctx.day(args.date)
    snaps = g.predictions(ctx.store, day)
    if not snaps:
        raise InputError(f"date: no G prediction for {day}; run `g predict FILE` first")
    snap = snaps[-1]
    res = g.compute(snap["items"], ctx.preset)
    cal = g.calibration(ctx.store, ctx.preset)
    text = _g_text(ctx, day, res, cal)
    out = {"date": day, "prediction": snap["id"], "B": res.B, "E": res.E, "G": res.G,
           "calibration": {"enabled": cal.enabled, "reason": cal.reason}, "text": text}
    note_file = journal.note_path(ctx.journal, day)
    if os.path.exists(note_file):
        doc = journal.load(note_file)
        journal.replace_ai_block(doc, "g", text)
        journal.save(doc, note_file)
        if text not in journal.load(note_file).block("ai:g").content():
            raise Failure(f"G report not found in {note_file} after writing", out)
        out["note"] = note_file
    return out


def cmd_xp_score(ctx, args):
    obj = check_fields(read_json(args.file), ("date", "levels", "ordinary"), ("events",))
    day = xp.score_day(ctx.day(obj["date"]), obj["levels"], obj["ordinary"],
                       obj.get("events", []), ctx.preset)
    rec = xp.record(ctx.store, day)
    monday = datetime.date.fromisoformat(day.date)
    monday -= datetime.timedelta(days=monday.weekday())
    week = xp.week_total(ctx.store, monday)
    return {"xp": rec["id"], "date": day.date, "total": day.total,
            "week": {"start": week.week_start, "total": week.total, "missing": week.missing}}


def cmd_audit_weekly(ctx, args):
    as_of = ctx.day(args.as_of)
    report = audit.weekly(ctx.store, as_of, ctx.audits, language=ctx.lang)
    week = audit.week_id(as_of)
    return {"week": week, "passed": report["passed"],
            "broken": [c["root"] for c in report["chains"] if c["problems"]],
            "report": os.path.join(ctx.audits, f"{week}.md")}


def cmd_checkpoint(ctx, args):
    res = checkpoint.checkpoint(ctx.root, args.message, ctx.cfg.git_remote)
    out = {"committed": res.committed, "commit": res.commit, "initialized": res.initialized,
           "pushed": res.pushed}
    if res.push_error:
        out["push_error"] = res.push_error
        raise Failure(f"push failed: {res.push_error}", out)
    return out


def cmd_rules_list(ctx, args):
    if args.all:
        found = rules.read(ctx.store)["rules"]
        if args.scope:
            found = [r for r in found if r["scope"] == args.scope]
    else:
        found = rules.active_rules(ctx.store, args.scope)
    return {"version": rules.version(ctx.store), "rules": found}


def cmd_validate(ctx, args):
    problems = []
    try:
        problems += validate.validate(ctx.store).problems
    except (StoreError, KeyError, ValueError) as exc:
        problems.append(f"data: {exc}")
    if os.path.isdir(ctx.journal):
        for name in sorted(os.listdir(ctx.journal)):
            if name.endswith(".md"):
                try:
                    journal.load(os.path.join(ctx.journal, name))
                except (journal.JournalError, OSError, UnicodeDecodeError) as exc:
                    problems.append(f"journal/{name}: {exc}")
    out = {"ok": not problems, "problems": problems}
    if problems:
        raise Failure(f"{len(problems)} problem(s)", out)
    return out


def cmd_notion_push(ctx, args):
    day = ctx.day(args.date)
    creds = notion.load_credentials(ctx.cfg.credentials["notion"])
    res = notion.push(ctx.store, creds, ctx.journal, day)
    rec = res.record
    out = {"date": day, "status": rec["status"], "recorded": res.recorded, "record": rec["id"],
           "page_id": rec["page_id"], "url": rec["url"], "blocks": rec["blocks"],
           "reason": rec.get("reason"), "archive_error": rec.get("archive_error")}
    if rec["status"] != notion.PUSHED:
        raise Failure(f"notion {rec['status']}: {rec.get('reason')}", out)
    return out


def cmd_schedule(ctx, args):
    files = scheduler.write(args.target, ctx.cfg, args.out)
    tasks = {task: ctx.cfg.tasks[task] for task in config.TASKS}
    return {"target": args.target, "timezone": ctx.cfg.timezone, "tasks": tasks,
            "files": files, "installed": False}


# --- parser ----------------------------------------------------------------------

def build_parser():
    p = _Root(prog="etrace", description="existence-trace: record your traces of existence, "
                                           "with a self-improving preference loop. "
                                           "(also runs as `python3 -m etrace`)")
    p.add_argument("--version", action="version", version=f"etrace {__version__}")
    p.add_argument("--config", help="path to trace.toml")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def group(name, help_text):
        sp = sub.add_parser(name, help=help_text).add_subparsers(dest="action", required=True,
                                                                 parser_class=_Parser)
        return sp

    s = sub.add_parser("init", help="create your data folder (no options needed)")
    s.add_argument("--preset", default="default", choices=presets.NAMES)
    s.add_argument("--dir", help=f"data root (default {DEFAULT_ROOT})")
    s.add_argument("--language", default=config.Config.language, choices=config.LANGUAGES)
    s.add_argument("--timezone", default=config.Config.timezone)
    s.set_defaults(func=cmd_init, needs_ctx=False)

    nt_parser = sub.add_parser("note", help="today's note: create it and show where it is (-e opens it)")
    nt_parser.add_argument("-e", "--edit", action="store_true", help="open it in $EDITOR (or the system editor)")
    nt_parser.set_defaults(func=cmd_note_today)
    n = nt_parser.add_subparsers(dest="action", parser_class=_Parser)
    s = n.add_parser("new")
    s.add_argument("date", nargs="?", default="today")
    s.set_defaults(func=cmd_note_new)
    s = n.add_parser("write", help="replace the text of an ai block and read it back")
    s.add_argument("date")
    s.add_argument("block", choices=TEXT_BLOCKS)
    s.add_argument("file", help="plain Markdown; '-' reads stdin")
    s.set_defaults(func=cmd_note_write)

    q = group("question", "questions for the user")
    s = q.add_parser("add")
    s.add_argument("file")
    s.set_defaults(func=cmd_question_add)

    f = group("feedback", "user feedback")
    s = f.add_parser("ingest")
    s.set_defaults(func=cmd_feedback_ingest)

    s = sub.add_parser("decide", help="store a decision and its rule changes")
    s.add_argument("file")
    s.set_defaults(func=cmd_decide)

    u = group("usage", "usage receipts")
    s = u.add_parser("add")
    s.add_argument("file")
    s.set_defaults(func=cmd_usage_add)

    o = group("outreach", "reach the user")
    s = o.add_parser("send")
    s.add_argument("--scan", required=True, choices=("ok", "failed"),
                   help="whether the scan for new questions completed")
    s.add_argument("--date")
    s.add_argument("--kind", default="questions", choices=("questions", "report"))
    s.add_argument("--decision", action="append", help="decision id to report (repeatable)")
    s.add_argument("--summary")
    s.set_defaults(func=cmd_outreach_send)

    pd = group("pad", "PAD points and charts")
    s = pd.add_parser("add")
    s.add_argument("file")
    s.set_defaults(func=cmd_pad_add)
    s = pd.add_parser("chart")
    s.add_argument("date", nargs="?", default="today")
    s.add_argument("--week", action="store_true", help="chart the Monday-Sunday week of DATE")
    s.set_defaults(func=cmd_pad_chart)

    gg = group("g", "G estimates")
    s = gg.add_parser("predict")
    s.add_argument("file")
    s.set_defaults(func=cmd_g_predict)
    s = gg.add_parser("feedback")
    s.add_argument("file")
    s.set_defaults(func=cmd_g_feedback)
    s = gg.add_parser("report")
    s.add_argument("--date")
    s.set_defaults(func=cmd_g_report)
    s = gg.add_parser("chart")
    s.add_argument("date", nargs="?", default="today")
    s.add_argument("--days", type=int, default=30, help="window ending on DATE (default 30)")
    s.set_defaults(func=cmd_g_chart)

    x = group("xp", "XP scoring")
    s = x.add_parser("score")
    s.add_argument("file")
    s.set_defaults(func=cmd_xp_score)

    a = group("audit", "implementation audit")
    s = a.add_parser("weekly")
    s.add_argument("--as-of", dest="as_of")
    s.set_defaults(func=cmd_audit_weekly)

    s = sub.add_parser("checkpoint", help="commit the data root to its local git repo")
    s.add_argument("message")
    s.set_defaults(func=cmd_checkpoint)

    r = group("rules", "preference rules")
    s = r.add_parser("list")
    s.add_argument("--scope")
    s.add_argument("--all", action="store_true", help="include superseded rules")
    s.set_defaults(func=cmd_rules_list)

    s = sub.add_parser("prompt", help="render the agent prompt for a task")
    s.add_argument("task", choices=prompts.TASKS)
    s.add_argument("--language", choices=prompts.LANGUAGES, help="default: language in trace.toml")
    s.add_argument("--raw", action="store_true", help="print the prompt text instead of JSON")
    s.add_argument("--date", help="for daily and g: a past day instead of today")
    s.set_defaults(func=cmd_prompt)

    nt = group("notion", "optional Notion mirror (local Markdown stays the source of truth)")
    s = nt.add_parser("push", help="mirror a day's note into Notion")
    s.add_argument("--date")
    s.set_defaults(func=cmd_notion_push)

    s = sub.add_parser("schedule", help="write scheduler files for an agent runner (never installs them)")
    s.add_argument("--target", required=True, choices=scheduler.TARGETS)
    s.add_argument("--out", required=True, help="directory to write the files into")
    s.set_defaults(func=cmd_schedule)

    s = sub.add_parser("validate", help="check the data chain and notes; exit 1 on any failure")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("ai", help="connect an AI API: show it, `set` it up, `test` it, list `models`")
    s.set_defaults(func=cmd_ai_status)
    a = s.add_subparsers(dest="action", parser_class=_Parser)
    x = a.add_parser("set", help="choose the service, key and model (asks you when run in a terminal)")
    x.add_argument("service", nargs="?", choices=tuple(ai.PRESETS), help="openai, anthropic, deepseek, openrouter, ollama or custom")
    x.add_argument("--model", help="model name (see `etrace ai models`)")
    x.add_argument("--url", help="API address, for `custom` or a proxy")
    x.add_argument("--provider", choices=config.AI_PROVIDERS, help="wire format for `custom`")
    x.add_argument("--key-env", help="NAME of the environment variable that holds the key")
    x.add_argument("--key-file", help="where to store the key (default ~/.config/etrace/ai.toml)")
    x.add_argument("--key-stdin", action="store_true", help="read the key from standard input")
    x.add_argument("--no-test", action="store_true", help="save without calling the API")
    x.set_defaults(func=cmd_ai_set_start, needs_ctx=False)
    x = a.add_parser("test", help="call the API once; also checks that the model supports tools")
    x.set_defaults(func=cmd_ai_test)
    x = a.add_parser("models", help="list the models the service offers")
    x.set_defaults(func=cmd_ai_models)

    s = sub.add_parser("run", help="let the AI do a task with the API from `etrace ai set`")
    s.add_argument("task", choices=prompts.TASKS, help="daily, g, feedback, calibrate or audit")
    s.add_argument("--language", choices=config.LANGUAGES, help="language of the prompt (default: trace.toml)")
    s.add_argument("--max-steps", type=int, default=agent.DEFAULT_STEPS, help="stop after this many AI turns")
    s.add_argument("--date", help="for daily and g: work on this past day instead of today")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("import", help="bring in your existing diary: folders, files or a Day One export")
    s.add_argument("paths", nargs="+", metavar="PATH", help="folders or files with the diary (.md, .txt, Day One .json)")
    s.add_argument("--run", action="store_true", help="then let the AI read the latest days (needs `etrace ai set`)")
    s.add_argument("--days", type=int, default=7, help="with --run: how many of the latest days (default 7)")
    s.add_argument("--date", help="the day for a single file that has no date in it")
    s.add_argument("--mtime", action="store_true", help="use a file's modified date when it has no other date")
    s.add_argument("--dry-run", action="store_true", help="show what would happen and change nothing")
    s.set_defaults(func=cmd_import_start, needs_ctx=False)
    _describe(p)
    return p


# Module exceptions that mean "the input was wrong" (exit 2).
_INPUT_ERRORS = (questions.QuestionError, decisions.DecisionError, usage.UsageError,
                 rules.RuleError, g.GError, pad.PadError, xp.XPError, journal.JournalError,
                 note.OutreachError, mail.EmailError, audit.AuditError, config.ConfigError,
                 prompts.PromptError, scheduler.SchedulerError, notion.NotionError, ValueError)


FORMAT_FLAGS = {"--json": "json", "--pretty": "pretty"}


def _take_flags(argv):
    """(argv without the output flags, 'json' | 'pretty' | None, no_color). Flags may sit anywhere."""
    rest, fmt, no_color = [], None, False
    for i, arg in enumerate(argv):
        if arg == "--":
            rest += argv[i:]
            break
        if arg in FORMAT_FLAGS:
            fmt = FORMAT_FLAGS[arg]
        elif arg == "--no-color":
            no_color = True
        else:
            rest.append(arg)
    return rest, fmt, no_color


def _is_tty(stream):
    return bool(getattr(stream, "isatty", lambda: False)())


def _emit(obj, stream, key, pretty, style, streamed=False):
    if isinstance(obj, RawText):
        stream.write(obj if obj.endswith("\n") else obj + "\n")
        return
    if pretty:
        text = render.render(style, key, obj, streamed=streamed)
        if text is not None:
            stream.write(text + "\n")
            return
    stream.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def _home_status():
    """Where the data lives, for bare `etrace`; None if there is no usable config."""
    try:
        ctx = Context(config.load(find_config(None)))
        note_file = journal.note_path(ctx.journal, ctx.today().isoformat())
        return {"root": ctx.root, "preset": ctx.cfg.preset, "language": ctx.lang,
                "timezone": ctx.cfg.timezone, "note": note_file, "note_exists": os.path.exists(note_file)}
    except (config.ConfigError, InputError, OSError, ValueError, KeyError):
        return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    argv, fmt, no_color = _take_flags(argv)
    out, err = sys.stdout, sys.stderr
    env = dict(os.environ)
    if no_color:
        env.pop("FORCE_COLOR", None)
        env["NO_COLOR"] = "1"
    out_style, err_style = ui.style_for(out, env), ui.style_for(err, env)

    def say(kind, line):
        if err_style.color:
            err.write(f"{err_style.paint(err_style.icon('fail'), ui.RED, True)} "
                      f"{err_style.paint(kind + ':', ui.RED, True)} {line}\n")
        else:
            err.write(f"{kind}: {line}\n")

    if not argv:
        out.write(render.home_screen(out_style, _home_status()) + "\n")
        return 0
    try:
        args = build_parser().parse_args(argv)
    except InputError as exc:
        for line in exc.errors:
            say("error", line)
        return 2
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else 0

    key = " ".join(x for x in (args.command, getattr(args, "action", None)) if x)
    pretty = fmt == "pretty" or (fmt is None and _is_tty(out))
    args.progress = (lambda step: err.write(render.progress_line(err_style, step) + "\n")) if pretty else None
    is_validate = args.func is cmd_validate
    try:
        if getattr(args, "needs_ctx", True):
            result = args.func(Context(config.load(find_config(args.config))), args)
        else:
            result = args.func(args)
    except InputError as exc:
        for line in exc.errors:
            say("error", line)
        return 1 if is_validate else 2
    except Failure as exc:
        if exc.result is not None:
            _emit(exc.result, out, key, pretty, out_style, streamed=args.progress is not None)
        say("failed", exc)
        return 1
    except _INPUT_ERRORS as exc:
        for line in _errors(exc):
            say("error", line)
        return 1 if is_validate else 2
    except (StoreError, checkpoint.CheckpointError, ai.AIError, OSError) as exc:
        say("failed", exc)
        return 1
    _emit(result, out, key, pretty, out_style, streamed=args.progress is not None)
    return 0
