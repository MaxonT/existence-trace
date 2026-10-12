"""Short task prompts for coding agents (zh/en).

A prompt only schedules work: it lists the steps and the exact CLI JSON shapes,
and defers every rule to the engine. Templates use string.Template ($name,
$$ for a literal dollar). render() fills them from trace.toml and the active
rules; it never writes the data root path into the text, so a rendered prompt
holds no absolute paths.
"""

import os
import string

from etrace.container import rules as rules_mod

TASKS = ("calibrate", "feedback", "daily", "g", "audit")
LANGUAGES = ("zh", "en")
MAX_CHARS = 6000
PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))

_MORE = {
    "zh": "……另有 {n} 条生效规则未列出：运行 `python3 -m etrace rules list` 查看全部。",
    "en": "...{n} more active rules not shown: run `python3 -m etrace rules list` for all of them.",
}
# Appended to the last writing step of daily/g when credentials.notion is set.
_NOTION = {
    "zh": " 然后把今天的笔记镜像到 Notion：`python3 -m etrace notion push --date YYYY-MM-DD`（今天的日期；失败不影响本地结果，照实汇报）。",
    "en": " Then mirror today's note to Notion: `python3 -m etrace notion push --date YYYY-MM-DD` (today's date; a failure there leaves local work intact, report it).",
}
_NO_RULES = {"zh": "（还没有生效规则）", "en": "(no active rules yet)"}
_OUTREACH = {
    "zh": {"note": "只写进当天笔记", "note+email": "写进当天笔记并发邮件"},
    "en": {"note": "today's note only", "note+email": "today's note and email"},
}
# Added to daily/g when they are pointed at one past day (an imported or late entry).
_FOCUS = {
    "zh": "**这次要处理的是 {day}，不是今天**：它是导入或补写的日记。所有写着“今天”或当天日期的步骤都用 {day}；"
          "不要创建明天的页面，也不要做 Notion 镜像。\n\n",
    "en": "**This run is for the day {day}, not today**: it is an imported or late entry. Use {day} wherever a step "
          "says today or the day's date; do not create tomorrow's page and skip the Notion mirror.\n\n",
}
TASKS_WITH_DAY = ("daily", "g")


class PromptError(ValueError):
    """Unknown task or language."""


def template(task, language):
    """Raw template text for TASK ('_principles' included) in LANGUAGE."""
    if task not in TASKS + ("_principles",):
        raise PromptError(f"task: unknown task {task!r}; expected one of {', '.join(TASKS)}")
    if language not in LANGUAGES:
        raise PromptError(f"language: must be one of {', '.join(LANGUAGES)}, got {language!r}")
    with open(os.path.join(PROMPTS_DIR, language, f"{task}.md"), encoding="utf-8") as fh:
        return fh.read()


def _rule_line(rule):
    return f"- `{rule['id']}` [{rule['scope']}] {' '.join(rule['text'].split())}"


def _fill(text, values, rules_text):
    return string.Template(text).substitute(values, rules=rules_text).strip() + "\n"


def render(task, cfg, data_dir, language=None, max_chars=MAX_CHARS, day=None):
    """The prompt for TASK with values from CFG and the active rules in DATA_DIR.

    Rules are listed while they fit under max_chars; the rest are counted with
    a pointer to `rules list`, so the prompt always stays under the limit.
    DAY (YYYY-MM-DD, only for daily and g) points the task at that day instead of today.
    """
    lang = language or cfg.language
    if day is not None and task not in TASKS_WITH_DAY:
        raise PromptError(f"date: only {' and '.join(TASKS_WITH_DAY)} can run for a chosen day, not {task!r}")
    body = template(task, lang)
    values = {
        "task": task,
        "focus": _FOCUS[lang].format(day=day) if day else "",
        "times": ", ".join(cfg.tasks[task]),
        "timezone": cfg.timezone,
        "preset": cfg.preset,
        "language": lang,
        "outreach": _OUTREACH[lang][cfg.outreach],
        "rules_version": rules_mod.version(data_dir),
        "notion": _NOTION[lang] if cfg.credentials.get("notion") else "",
    }
    values["principles"] = _fill(template("_principles", lang), values, "").strip()

    active = rules_mod.active_rules(data_dir)
    lines = [_rule_line(r) for r in active]
    if not lines:
        return _fill(body, values, _NO_RULES[lang])
    for shown in range(len(lines), -1, -1):
        rest = len(lines) - shown
        rules_text = "\n".join(lines[:shown] + ([_MORE[lang].format(n=rest)] if rest else []))
        text = _fill(body, values, rules_text)
        if len(text) < max_chars:
            return text
    raise PromptError(f"prompt {task}/{lang} is over {max_chars} characters even without rules")
