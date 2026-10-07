"""The `python3 -m etrace` command line: one entry point for users and agents.

Every command prints one JSON object on stdout. Exit codes:

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
import json
import os
import shutil
import sys
from zoneinfo import ZoneInfo

from etrace import chart, checkpoint, config, journal, presets, prompts
from etrace.adapters import scheduler
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
    raise config.ConfigError("no trace.toml found; run `python3 -m etrace init --preset NAME` "
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
    text = prompts.render(args.task, ctx.cfg, ctx.store, args.language)
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


def cmd_schedule(ctx, args):
    files = scheduler.write(args.target, ctx.cfg, args.out)
    tasks = {task: ctx.cfg.tasks[task] for task in config.TASKS}
    return {"target": args.target, "timezone": ctx.cfg.timezone, "tasks": tasks,
            "files": files, "installed": False}


# --- parser ----------------------------------------------------------------------

def build_parser():
    p = _Parser(prog="python3 -m etrace", description="existence-trace: a self-improving preference loop.")
    p.add_argument("--config", help="path to trace.toml")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def group(name, help_text):
        sp = sub.add_parser(name, help=help_text).add_subparsers(dest="action", required=True,
                                                                 parser_class=_Parser)
        return sp

    s = sub.add_parser("init", help="create a fresh data dir from a preset")
    s.add_argument("--preset", required=True, choices=presets.NAMES)
    s.add_argument("--dir", help=f"data root (default {DEFAULT_ROOT})")
    s.add_argument("--language", default=config.Config.language, choices=config.LANGUAGES)
    s.add_argument("--timezone", default=config.Config.timezone)
    s.set_defaults(func=cmd_init, needs_ctx=False)

    n = group("note", "journal notes")
    s = n.add_parser("new")
    s.add_argument("date")
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
    s.add_argument("date")
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
    s.set_defaults(func=cmd_prompt)

    s = sub.add_parser("schedule", help="write scheduler files for an agent runner (never installs them)")
    s.add_argument("--target", required=True, choices=scheduler.TARGETS)
    s.add_argument("--out", required=True, help="directory to write the files into")
    s.set_defaults(func=cmd_schedule)

    s = sub.add_parser("validate", help="check the data chain and notes; exit 1 on any failure")
    s.set_defaults(func=cmd_validate)
    return p


# Module exceptions that mean "the input was wrong" (exit 2).
_INPUT_ERRORS = (questions.QuestionError, decisions.DecisionError, usage.UsageError,
                 rules.RuleError, g.GError, pad.PadError, xp.XPError, journal.JournalError,
                 note.OutreachError, mail.EmailError, audit.AuditError, config.ConfigError,
                 prompts.PromptError, scheduler.SchedulerError, ValueError)


def _print(obj, stream):
    if isinstance(obj, RawText):
        stream.write(obj if obj.endswith("\n") else obj + "\n")
        return
    stream.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    out, err = sys.stdout, sys.stderr
    try:
        args = build_parser().parse_args(argv)
    except InputError as exc:
        for line in exc.errors:
            err.write(f"error: {line}\n")
        return 2
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else 0

    is_validate = args.func is cmd_validate
    try:
        if getattr(args, "needs_ctx", True):
            result = args.func(Context(config.load(find_config(args.config))), args)
        else:
            result = args.func(args)
    except InputError as exc:
        for line in exc.errors:
            err.write(f"error: {line}\n")
        return 1 if is_validate else 2
    except Failure as exc:
        if exc.result is not None:
            _print(exc.result, out)
        err.write(f"failed: {exc}\n")
        return 1
    except _INPUT_ERRORS as exc:
        for line in _errors(exc):
            err.write(f"error: {line}\n")
        return 1 if is_validate else 2
    except (StoreError, checkpoint.CheckpointError, OSError) as exc:
        err.write(f"failed: {exc}\n")
        return 1
    _print(result, out)
    return 0
