"""Human-friendly views of command results (the pretty side of `etrace`).

render(style, key, result) -> str | None. KEY is "command" or "command action"
(e.g. "validate", "g report"). A command without its own view gets the generic
one. The caller falls back to JSON when this returns None, so a drawing problem
never costs the user a result. Machine use is unchanged: JSON stays the default
whenever stdout is not a terminal.
"""

import os
import textwrap

from etrace import __version__, ui

BAR = 24


def _val(style, value):
    if isinstance(value, bool):
        return style.paint(style.icon("ok"), ui.GREEN, True) if value else style.paint(style.icon("fail"), ui.RED, True)
    if value is None:
        return style.muted("—" if style.unicode else "-")
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _head(style, key, *more):
    return ui.header(style, key, *[style.muted(m) for m in more if m])


def _msg(style, kind, title, detail=""):
    """'<icon> Title  detail' on one line; if it would not fit the terminal the detail wraps below."""
    first = getattr(style, kind)(style.head(title) if kind == "ok" else title)
    if not detail:
        return first
    one = first + "  " + style.muted(detail)
    if ui.visible_len(one) <= style.width:
        return one
    rest = textwrap.wrap(detail, max(20, style.width - 4))
    return "\n".join([first] + ["    " + style.muted(line) for line in rest])


def _count(value):
    return len(value) if isinstance(value, (list, tuple, set)) else (value or 0)


# --- per-command views ---------------------------------------------------------------

def init(style, key, r):
    arrow = lambda rgb: style.paint(style.icon("arrow"), rgb, True)
    facts = ui.kv(style, [
        ("folder", style.paint(r["root"], ui.INK)), ("preset", style.paint(r["preset"], ui.PINK, True)),
        ("rules", f"v{r['rules_version']}"), ("today's note", r["note"])])
    # people copy these lines, so they are printed whole (no box that could cut a long path)
    steps = []
    if os.path.realpath(r["root"]) != os.path.realpath(os.path.expanduser("~/existence-trace-data")):
        steps.append("  " + arrow(ui.PINK) + " export ETRACE_CONFIG=" + r["config"])
    steps += ["  " + arrow(ui.PINK) + " etrace ai set       " + style.muted("connect an AI (key stays outside this folder)"),
              "  " + arrow(ui.AMBER) + " etrace note         " + style.muted("write today's journal"),
              "  " + arrow(ui.BLUE) + " etrace run daily    " + style.muted("let the AI read it")]
    return "\n".join([
        ui.banner(style, __version__), "",
        style.ok(style.head("Your data folder is ready")), "", facts, "",
        "  " + style.head("Next")] + steps)


def validate(style, key, r):
    out = [_head(style, key), ""]
    if r["ok"]:
        out.append(_msg(style, "ok", "All clear", "the data chain and every note check out"))
    else:
        out.append(_msg(style, "fail", f"{len(r['problems'])} problem(s) found"))
        out += ["  " + style.paint(style.icon("bullet"), ui.RED) + " " + p for p in r["problems"]]
    return "\n".join(out)


def xp_score(style, key, r):
    week = r.get("week", {})
    missing = _count(week.get("missing"))
    rows = [("today", f"{r['total']:>4}  " + ui.bar(style, r["total"], 100, BAR, stops=(ui.AMBER, ui.PINK))
             + style.muted("  / 100")),
            ("week", f"{_val(style, week.get('total')):>4}  " + style.muted(f"from {week.get('start')}"
                                                                            + (f", {missing} day(s) missing" if missing else "")))]
    return "\n".join([_head(style, key, r["date"]), "", ui.kv(style, rows), "",
                      style.muted("  record " + str(r["xp"]))])


def g_report(style, key, r):
    def row(name, value, rgb=None, stops=None):
        if value is None:
            return (name, style.muted("not enough items yet"))
        return (name, f"{value:>5.1f}  " + ui.bar(style, value, 10, BAR, rgb or ui.GREEN, stops)
                + style.muted("  / 10"))

    cal = r.get("calibration", {})
    reason = str(cal.get("reason", ""))
    if cal.get("enabled"):
        cal_line = _msg(style, "ok", "Calibration on", reason)
    else:
        cal_line = _msg(style, "off", "Calibration off", reason)
    out = [_head(style, key, r["date"]), "",
           ui.kv(style, [row("B stability", r["B"], ui.GREEN), row("E exploration", r["E"], ui.VIOLET),
                         row("G", r["G"], stops=ui.BRAND)]), "", "  " + cal_line, "",
           style.muted("  prediction " + str(r["prediction"]))]
    if r.get("note"):
        out.append(style.muted("  note       " + r["note"]))
    return "\n".join(out)


def _trend(style, r):
    trend, rows = r.get("trend") or {}, []
    for name, rgb, label in (("B", ui.GREEN, "B stability"), ("E", ui.VIOLET, "E exploration"), ("G", ui.BLUE, "G")):
        series = trend.get(name)
        if series:
            real = [v for v in series if v is not None]
            rows.append((label, ui.sparkline(style, series, None, None, rgb) + style.muted(
                f"  {len(real)} day(s), last {real[-1]:.1f}" if real else "")))
    return rows


def g_chart(style, key, r):
    pts = r.get("points", {})
    drawn = [k for k in ("B", "E", "G") if pts.get(k, 0) >= 3]
    out = [_head(style, key), "", _msg(style, "ok", "Trend chart written",
                                     f"{r.get('days')} day(s) with a record; lines drawn: {', '.join(drawn) or 'none'}"), ""]
    rows = _trend(style, r)
    if rows:
        out += [ui.kv(style, rows), ""]
    out.append("  " + style.paint(style.icon("file"), ui.BLUE) + " " + r["chart"])
    return "\n".join(out)


def pad_chart(style, key, r):
    out = [_head(style, key), "", _msg(style, "ok", "PAD chart written", f"from {r.get('points')} reading(s)"), "",
           "  " + style.paint(style.icon("file"), ui.PINK) + " " + r["chart"]]
    if r.get("note"):
        out.append("  " + style.paint(style.icon("file"), ui.AMBER) + " " + r["note"])
    return "\n".join(out)


def rules_list(style, key, r):
    rules = r.get("rules", [])
    rows = [[x.get("scope", ""), x.get("id", ""), x.get("text", "")] for x in rules]
    out = [_head(style, key, f"version {r.get('version')}", f"{len(rules)} rule(s)"), ""]
    out.append(ui.table(style, rows, headers=("scope", "id", "rule")) if rows else style.muted("  no rules"))
    return "\n".join(out)


def audit_weekly(style, key, r):
    out = [_head(style, key, r["week"]), ""]
    if r["passed"]:
        out.append(_msg(style, "ok", "Audit passed", "every feedback chain was followed through"))
    else:
        out.append(_msg(style, "fail", "Audit failed: a decision was never applied"))
        out += ["  " + style.paint(style.icon("bullet"), ui.RED) + " " + str(b) for b in r.get("broken", [])]
    out += ["", "  " + style.paint(style.icon("file"), ui.BLUE) + " " + r["report"]]
    return "\n".join(out)


def checkpoint(style, key, r):
    out = [_head(style, key), ""]
    if r.get("committed"):
        out.append(style.ok(style.head("Committed") + "  " + style.paint(str(r.get("commit") or "")[:10], ui.AMBER)))
    else:
        out.append(style.info(style.muted("Nothing new to commit")))
    out.append("  " + style.muted("pushed ") + _val(style, bool(r.get("pushed")))
               + (style.muted("   new repository initialised") if r.get("initialized") else ""))
    if r.get("push_error"):
        out.append(style.fail(str(r["push_error"])))
    return "\n".join(out)


# --- generic view ----------------------------------------------------------------------

def generic(style, key, r, depth=0):
    if not isinstance(r, dict):
        return "\n".join([_head(style, key), "", "  " + str(r)])
    pairs, extra = [], []
    for k, v in r.items():
        if isinstance(v, dict) and depth < 1:
            extra.append((k, v))
        elif isinstance(v, (list, tuple)):
            if v and all(isinstance(i, (str, int, float)) for i in v):
                pairs.append((k, ", ".join(str(i) for i in v)))
            else:
                pairs.append((k, style.muted(f"{len(v)} item(s)")))
        elif isinstance(v, dict):
            pairs.append((k, style.muted(f"{len(v)} field(s)")))
        else:
            pairs.append((k, _val(style, v)))
    out = [_head(style, key), ""] if depth == 0 else []
    if pairs:
        out.append(ui.kv(style, pairs))
    for k, v in extra:
        out += ["", "  " + style.head(k), ui.kv(style, [("  " + a, _val(style, b)) for a, b in v.items()
                                                       if not isinstance(b, (dict, list, tuple))])]
    return "\n".join(out)


def note(style, key, r):
    out = [_head(style, key, r["date"]), "",
           _msg(style, "ok", "Created today's note" if r["created"] else "Today's note is ready"), "",
           "  " + style.paint(style.icon("file"), ui.AMBER) + " " + r["note"], "",
           style.muted("  write in the marked zones; `etrace note -e` opens it")]
    return "\n".join(out)


def _ai_lines(style, r):
    if not r.get("provider"):
        steps = ["  " + style.paint(style.icon("arrow"), ui.PINK, True) + " etrace ai set openai",
                 "  " + style.paint(style.icon("arrow"), ui.AMBER, True) + " etrace ai set anthropic",
                 "  " + style.paint(style.icon("arrow"), ui.BLUE, True) + " etrace ai set deepseek"
                 + style.muted("   (also openrouter, ollama, custom --url URL)")]
        return [style.info(style.muted("No AI is connected yet. Pick a service:"))] + steps
    k = r["key"]
    if not k["needed"]:
        key_text = style.ok(style.muted("none needed (local service)"))
    elif k["found"]:
        key_text = style.ok(style.paint(k["masked"], ui.INK) + style.muted(f"  ({k['source']})"))
    else:
        key_text = style.fail(k["problem"] or "no key found: etrace ai set (or export ETRACE_AI_KEY)")
    model = r["model"] and style.paint(r["model"], ui.PINK, True) or style.paint(
        style.icon("warn"), ui.AMBER, True) + " " + style.muted("not chosen: etrace ai models")
    out = [ui.kv(style, [("service", r["service"]), ("address", r["base_url"]), ("model", model),
                         ("key", key_text)])]
    if k.get("exposed"):
        out += ["", style.warn("the key file is readable by other users: chmod 600 it")]
    return out


def _test_lines(style, t):
    if not t:
        return []
    if not t.get("ok"):
        return [_msg(style, "fail", "The API call failed", t.get("error") or "")]
    out = [_msg(style, "ok", "API reachable", f"{t['ms']} ms, the model answered {t['reply']!r}")]
    if t.get("tools"):
        out.append(_msg(style, "ok", "Tool calling works", "etrace run can use this model"))
    else:
        out.append(_msg(style, "warn", "The test tool was not called",
                        "`etrace run` needs tool calling: choose another model"))
    return out


def ai_status(style, key, r):
    out = [_head(style, key), ""] + _ai_lines(style, r)
    if r.get("provider") and r.get("model") and not r["key"]["problem"]:
        out += ["", style.muted("  etrace ai test   check it"), style.muted("  etrace run daily   use it")]
    return "\n".join(out)


def ai_set(style, key, r):
    out = [_head(style, key), "", _msg(style, "ok", "Saved", "the key is not in trace.toml"), ""]
    out += _ai_lines(style, r)
    tests = _test_lines(style, r.get("test"))
    if tests:
        out += [""] + tests
    if r.get("next"):
        out += ["", "  " + style.muted(r["next"])]
    return "\n".join(out)


def ai_test(style, key, r):
    head = [_head(style, key, f"{r.get('provider')} {r.get('model')}"), ""]
    return "\n".join(head + _test_lines(style, r))


def ai_models(style, key, r):
    models, cur = r["models"], r.get("current")
    shown = models[:40]
    out = [_head(style, key, f"{r['count']} model(s)"), ""]
    for m in shown:
        mark = style.paint(style.icon("arrow"), ui.PINK, True) if m == cur else " "
        out.append("  " + mark + " " + (style.head(m) if m == cur else m))
    if len(models) > len(shown):
        out.append(style.muted(f"    … and {len(models) - len(shown)} more (etrace --json ai models lists all)"))
    out += ["", style.muted("  etrace ai set --model NAME")]
    return "\n".join(out)


def progress_line(style, step):
    """One line per tool call while `etrace run` works (printed to stderr)."""
    call = ui.fit(step["call"], max(20, style.width - 14))
    if step.get("error"):
        return "  " + style.paint(style.icon("fail"), ui.RED, True) + " " + call + style.muted("  " + str(step["error"])[:60])
    code = step.get("exit_code")
    if code is None:
        return "  " + style.paint(style.icon("key"), ui.GRAY) + " " + call
    if code == 0:
        return "  " + style.paint(style.icon("ok"), ui.GREEN, True) + " " + call
    return "  " + style.paint(style.icon("fail"), ui.RED, True) + " " + call + style.muted(f"  exit {code}")


def run(style, key, r):
    steps = r["steps"]
    bad = sum(1 for x in steps if x.get("error") or x.get("exit_code") not in (0, None))
    out = [_head(style, key, r["task"], f"{r['provider']} {r['model']}"), ""]
    for step in steps[-12:]:
        out.append(progress_line(style, step))
    if len(steps) > 12:
        out.insert(2, style.muted(f"  … {len(steps) - 12} earlier step(s)"))
    out.append("")
    summary = f"{len(steps)} step(s)" + (f", {bad} failed" if bad else "")
    if r["finished"]:
        out.append(_msg(style, "ok", "The AI finished", summary))
    else:
        out.append(_msg(style, "fail", "The AI did not finish", summary))
    if r.get("final"):
        lines = []
        for para in r["final"].split("\n"):
            lines += textwrap.wrap(para, max(30, min(style.width, 96) - 4)) or [""]
        out += ["", ui.panel(style, lines, title="Summary", width=min(style.width, 96))]
    u = r.get("usage", {})
    out += ["", style.muted(f"  tokens  {u.get('input', 0):,} in, {u.get('output', 0):,} out")]
    return "\n".join(out)


RENDERERS = {
    "init": init, "validate": validate, "xp score": xp_score, "g report": g_report,
    "g chart": g_chart, "pad chart": pad_chart, "rules list": rules_list,
    "audit weekly": audit_weekly, "checkpoint": checkpoint, "note": note, "ai": ai_status, "ai set": ai_set,
    "ai test": ai_test, "ai models": ai_models, "run": run,
}

SITE = "https://maxont.github.io/existence-trace/"
OPTIONS = (
    ("--config PATH", "path to trace.toml (else $ETRACE_CONFIG, ./trace.toml, ~/existence-trace-data)"),
    ("--json", "print JSON even in a terminal (the default whenever output is piped)"),
    ("--pretty", "print the pretty view even when output is piped"),
    ("--no-color", "no colour (also: NO_COLOR; FORCE_COLOR forces it on)"),
    ("--version", "print the version"),
    ("-h, --help", "this screen; `etrace COMMAND --help` for one command"),
)


def help_screen(style, groups, commands):
    """`etrace --help`. GROUPS: [(title, [command names])], COMMANDS: {name: help text}."""
    colors = (ui.PINK, ui.AMBER, ui.BLUE, ui.GREEN, ui.VIOLET)
    width = max(len(n) for n in commands) + 2
    out = [ui.banner(style, __version__, compact=not style.color), "",
           "  " + style.head("usage") + "  etrace [options] " + style.paint("COMMAND", ui.AMBER, True) + " ...", ""]
    def rows(first, text, rgb, col):
        """FIRST (already painted) plus TEXT wrapped under a hanging indent."""
        wrapped = textwrap.wrap(text, max(20, style.width - 6 - col)) or [""]
        return (["      " + first + style.muted(wrapped[0])]
                + [" " * (6 + col) + style.muted(line) for line in wrapped[1:]])

    for i, (title, names) in enumerate(groups):
        rgb = colors[i % len(colors)]
        out.append("  " + style.paint(style.icon("arrow"), rgb, True) + " " + style.head(title))
        for name in names:
            out += rows(style.paint(ui.pad(name, width), rgb, True), commands[name], rgb, width)
        out.append("")
    out.append("  " + style.head("options"))
    opt_w = max(len(o) for o, _ in OPTIONS) + 2
    for o, d in OPTIONS:
        out += rows(ui.pad(style.paint(o, ui.INK), opt_w), d, ui.INK, opt_w)
    out += ["", "  " + style.muted("docs  ") + SITE, "  " + style.muted("PAD, G and XP are reflection aids, not clinical scales.")]
    return "\n".join(out)


GLOBAL_FLAGS = "--json  --pretty  --no-color  --config PATH"


def command_help(style, path, usage, summary, sections):
    """`etrace COMMAND --help`. SECTIONS: [(title, [(name, text)])] with titles Commands / Arguments / Options."""
    colors = {"Commands": ui.PINK, "Arguments": ui.AMBER, "Options": ui.BLUE}
    longest = max((len(n) for _, items in sections for n, _ in items), default=0)
    col = min(longest, 26) + 2  # a longer name gets a line of its own
    out = [ui.header(style, path), ""]
    lines = textwrap.wrap(usage, max(30, style.width - 9), subsequent_indent="  ") or [usage]
    out.append("  " + style.head("usage") + "  " + lines[0])
    out += ["         " + line for line in lines[1:]]
    if summary:
        out += ["", "  " + style.paint(summary, ui.INK)]
    for title, items in sections:
        if not items:
            continue
        rgb = colors.get(title, ui.GRAY)
        out += ["", "  " + style.paint(style.icon("arrow"), rgb, True) + " " + style.head(title)]
        for name, text in items:
            wrapped = textwrap.wrap(text, max(20, style.width - 6 - col)) or [""]
            if len(name) > col - 2:
                out.append("      " + style.paint(name, rgb, True))
                out += [" " * (6 + col) + style.muted(line) for line in wrapped if text]
                continue
            out.append("      " + style.paint(ui.pad(name, col), rgb, True) + style.muted(wrapped[0]))
            out += [" " * (6 + col) + style.muted(line) for line in wrapped[1:]]
    foot = textwrap.wrap(GLOBAL_FLAGS + "   (see etrace --help)", max(20, style.width - 12))
    out += ["", "  " + style.muted("global  ") + foot[0]] + ["          " + line for line in foot[1:]]
    return "\n".join(out)


def home_screen(style, status=None):
    """Bare `etrace`: banner, where the data lives, the first three commands."""
    out = [ui.banner(style, __version__), ""]
    if status:
        out += [ui.kv(style, [
            ("data folder", style.paint(status["root"], ui.INK)),
            ("preset", f"{status['preset']}  {style.muted('·')}  {status['language']}  {style.muted('·')}  {status['timezone']}"),
            ("today's note", (style.ok(status["note"]) if status["note_exists"] else
                              style.paint(style.icon("warn"), ui.AMBER, True) + " " + style.muted("not created yet: etrace note new today")))]), ""]
    else:
        out += [style.info(style.muted("No data folder found yet.")), ""]
    steps = [
        style.paint(style.icon("arrow"), ui.PINK, True) + " etrace init --preset default --dir ~/existence-trace-data",
        style.paint(style.icon("arrow"), ui.AMBER, True) + " etrace validate",
        style.paint(style.icon("arrow"), ui.BLUE, True) + " etrace --help",
    ]
    if style.width >= 66:
        out.append(ui.panel(style, steps, title="Start here", width=min(style.width, 78)))
    else:  # too narrow for a box that would cut a command people copy
        out += ["  " + style.head("Start here")] + ["  " + line for line in steps]
    return "\n".join(out)


def render(style, key, result):
    """The pretty text for RESULT, or None if it cannot be drawn (caller prints JSON)."""
    try:
        text = RENDERERS.get(key, generic)(style, key, result)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None
    if text and not style.unicode:
        text = text.replace("…", "...")
    return text
