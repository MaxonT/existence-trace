<p align="center"><img src="docs/banner.svg" alt="existence-trace banner" width="100%"></p>

# existence-trace: AI agents that learn to read your journal

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/python-3-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies: stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen.svg)](#)
[![Last commit](https://img.shields.io/github/last-commit/MaxonT/existence-trace.svg)](https://github.com/MaxonT/existence-trace/commits)
[![GitHub stars](https://img.shields.io/github/stars/MaxonT/existence-trace.svg?style=social)](https://github.com/MaxonT/existence-trace/stargazers)

[中文](README.zh.md) · [Website](https://maxont.github.io/existence-trace/) · [For developers](https://maxont.github.io/existence-trace/developers.html) · [Install page](https://maxont.github.io/existence-trace/install/) · [Methodology](https://maxont.github.io/existence-trace/methodology.html)

**Record your traces of existence.**

You write a journal. An AI agent reads it through three lenses (PAD for emotion, G for stability
and exploration, XP for growth) and writes a daily report. The part that matters most is what happens
next: a **self-improving preference system** that asks you where it is unsure, turns your answer
into a rule, and proves every week that it actually used it. Python 3 standard library only, MIT.

> **Note.** Your words stay word for word; the AI only writes its own blocks. Every number says
> whether you said it or the AI inferred it. Your data lives in your own folder, not in this
> repository. PAD, G and XP are reflection aids, **not clinical scales**.

⭐ If this is the kind of memory you want an AI to keep for you, a star helps others find it.

## Install

**Give it to your AI.** Paste this into Claude Code, Codex or any agent that can run shell commands:

```
Read https://maxont.github.io/existence-trace/install and install existence-trace for me.
```

It checks Python, installs the `etrace` command, asks you four questions (preset, language,
timezone, data folder), and creates your journal. It never edits your shell config without asking.

**Or in a terminal** (needs Python 3.11+; uses `uv`, `pipx` or `pip --user`):

```sh
curl -fsSL https://maxont.github.io/existence-trace/install.sh | bash
```

Add `--dry-run` to see what it would do first. Then:

```sh
etrace init --language en  # creates ~/existence-trace-data, nothing else to configure
etrace ai set              # connect an AI (asks you, see below)
etrace note                # today's journal: write in the marked zones
etrace run daily           # the AI reads it and writes the report
```

<p align="center"><img src="docs/img/cli.png" alt="The etrace command in a terminal: gradient banner, G report with bars, trend sparklines and a help page (synthetic demo data)" width="720"></p>

## Connect an AI

`etrace run` uses any AI API you give it. Pick a service and answer two questions (key, model):

```sh
etrace ai set deepseek     # or: openai  anthropic  openrouter  ollama
etrace ai set custom --url https://my-proxy.example/v1    # any OpenAI-compatible address
etrace ai                  # show what is connected
etrace ai test             # one real call: reachable? does the model support tool calls?
etrace ai models           # the models your service offers
```

Run `etrace ai set` with no arguments for a menu. Anthropic uses its own format; every other
service speaks the OpenAI format. Pass `--model NAME` to skip the model question.

**Your key never touches your data folder.** It is looked up in this order: `$ETRACE_AI_KEY`,
then the file named by `[ai].key_file` (default `~/.config/etrace/ai.toml`, mode 600, which
`etrace ai set` writes for you), then the variable named by `[ai].key_env`. It is never accepted
as a command-line argument (shell history), never printed (only `…0123`), and never written
to `trace.toml`, which lives in a git repository. For a script: `echo $KEY | etrace ai set openai --key-stdin`.

`etrace run daily|g|feedback|calibrate|audit` hands the task to the model with two tools only:
run one `etrace` command, and read files in your data folder. There is no shell and no network
tool, and `init`, `schedule`, `ai` and `run` are blocked for the AI. It stops after 40 steps
(`--max-steps`). Prefer your own agent (Claude Code, Codex)? Skip this section:
`etrace prompt daily --raw` prints the same prompt.

## Commands

| Command | What it does |
|---|---|
| `etrace init` | create your data folder |
| `etrace ai [set\|test\|models]` | connect and check an AI API |
| `etrace run TASK` | let the AI do `daily`, `g`, `feedback`, `calibrate` or `audit` |
| `etrace note [-e]` | create today's note and show where it is; `-e` opens it |
| `etrace validate` | check the data chain and notes; exit 1 on any failure |
| `etrace pad` / `g` / `xp` | the three lenses: points, charts (`pad chart`, `g chart`), scores |
| `etrace question` / `feedback` / `decide` / `usage` / `outreach` | the preference loop |
| `etrace rules` / `audit` | read the rules; weekly audit |
| `etrace prompt TASK` | the prompt for your own agent |
| `etrace schedule` / `checkpoint` / `notion` | scheduler files, git commit, optional Notion mirror |

`etrace COMMAND --help` explains one command. Piped output is JSON; in a terminal you get the
pretty view (`--json` and `--pretty` switch it, `--no-color` or `NO_COLOR` turn colour off).

**Other ways:** `pip install` the wheel from the [latest release](https://github.com/MaxonT/existence-trace/releases/latest),
or run the container from GitHub Packages: `docker run --rm -v ~/existence-trace-data:/data ghcr.io/maxont/existence-trace --help`.

## The self-improving preference loop

<p align="center"><img src="docs/assets/how-it-works.gif" alt="How existence-trace works: journal, read through PAD/G/XP, ask, decide into a rule, audit weekly" width="100%"></p>

```
 notice doubt ──> ask you ──> your feedback ──> decide ──> change a rule
      ^                                                         │
      │                                                         v
 weekly audit <── usage receipt <── read back <── used in a real note
```

1. **Question.** The agent finds a real doubt in your notes, with evidence and two candidate readings.
2. **Feedback.** You answer in the protected zone of a note, or reply to the email.
3. **Decision.** It decides with reasons instead of waiting, and the newest feedback wins. You can overrule it.
4. **Receipt.** When a decision really changes a report, the agent reads the file back and records it. Reading a rule is not using it.
5. **Audit.** Every week `audit weekly` follows each chain and fails if a decision was never applied.

`etrace validate` checks the whole chain at any time and exits 1 on any problem.

## What a month looks like

<p align="center"><a href="https://maxont.github.io/existence-trace/methodology.html#pad"><img src="docs/img/pad-month.png" alt="Example monthly PAD trend: P, A and D smoothed with a 0.75-day Gaussian-kernel local linear fit; shaded days have no direct record (synthetic example data)" width="100%"></a></p>

| Default time | Task | What it does |
|---|---|---|
| 09:00 | `calibrate` | scan recent notes, ask real questions, send the daily status |
| 12:30, 20:30 | `feedback` | read feedback, decide, change rules, report decisions |
| 19:30 | `daily` | PAD, XP, headline and narrative for today, with receipts |
| 20:30 | `g` | G estimate, frozen prediction, calibration |
| Sun 22:15 | `audit` | weekly audit of every feedback chain |

`etrace schedule --target codex|claude-code|cron --out DIR` writes the schedule files and installs
nothing. A fictional walk-through lives in [`examples/persona`](examples/persona); every key of
`trace.toml` is in [`examples/trace.toml`](examples/trace.toml).

## The 9 principles

The engine enforces these in code and tests; prompts tell the agent never to work around them.

1. **Verbatim.** Your words stay word for word.
2. **An AI estimate never poses as a self-report.**
3. **Missing is not 0.**
4. **An event is booked once.**
5. **Freeze a prediction before judging it.**
6. **Latest feedback wins** within one scope.
7. **Decide, report, invite feedback.**
8. **Write, then read back. Reading is not using.**
9. **A self-rating is a reference, not an order.**

The full text, the architecture and how to add a preset are on the
[developer page](https://maxont.github.io/existence-trace/developers.html). Tests:
`python3 -m unittest discover -s tests -v`; `tests/test_privacy.py` fails the build if an email, a
Notion-style id or a home directory path lands in the repository.

## Not a clinical tool

PAD, G and XP are personal reflection aids. They are **not clinical scales**, make no medical or
psychological judgement, and are no substitute for professional care. If you are struggling,
please reach out to a qualified professional or a local crisis line.

## Privacy

Your data lives only in your data directory, outside this repository. Email carries only the
context a question needs, never the full journal. Credentials are read from files you name in
`trace.toml` and never written into config, logs or prompts.
