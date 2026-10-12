# existence-trace — instructions for every agent run

- Ralph files: PRD is `scripts/ralph/prd.json`, progress log is `scripts/ralph/progress.txt`. Read the Codebase Patterns and Hard boundary sections of the progress log first.
- Design source of truth: `docs/SYSTEM_DESIGN.md`.
- Python 3 standard library only. Package: `etrace`. Tests: `python3 -m unittest discover -s tests -v`.
- CLI look: `etrace/ui.py` (colour, banner, bars, panels) and `etrace/render.py` (per-command views). Output is JSON when piped and a coloured view in a terminal; agents always pass `--json`. The logo is only `█` and spaces (box-drawing/figlet lettering breaks apart in many fonts; `tests/test_ui.py` enforces it), and a coloured bar's empty rail is a flat dark block, never the dotted `░`. Never use circle glyphs in the UI (the owner dislikes them; `tests/test_ui.py` enforces it). A copyable line (command, path) must never be cut by a box. New command results get a view in `render.py` or fall back to the generic one.
- AI API: `etrace ai` (set/test/models) configures a service, `etrace run TASK` runs a task through it. Code in `etrace/ai.py` (two wire formats: openai-compatible and anthropic; key lookup `$ETRACE_AI_KEY` → `[ai].key_file` → `[ai].key_env`) and `etrace/agent.py` (tools: `etrace` + read-only `read`; no shell, no network; init/schedule/ai/run blocked). The key is never written to `trace.toml`, never taken as an argument, never printed; keep it that way. Do not hard-code model names. Tests use `tests/fake_ai.py` (a local server) and must never reach the real network.
- Importing a diary: `etrace import PATH` (`etrace/importer.py` reads .md/.txt/Day One files; `cli.cmd_import` writes into each day's `verbatim` block, append-only and idempotent). A file whose date cannot be told is skipped and listed, never guessed. `import` and `ai set` make the data folder if none exists. The AI agent must never be able to run `import` (it reads arbitrary host paths): keep it in `agent.BLOCKED`. `prompt`/`run` take `--date` for daily and g so imported days can be processed.
- `feedback ingest` reports `pending` (stored feedback with no decision yet) so a feedback run that stopped halfway is picked up next time; the feedback/calibrate prompts say so. Keep that recovery in the engine, never rely on the model remembering.
- Commands must stay short: a common action is one word plus at most one argument, with sensible defaults (`etrace init`, `etrace note`, `etrace run daily`). Do not add a required flag where a default will do.
- Packaging: `pyproject.toml` ships the `etrace` console script. Presets live in `etrace/preset_data/` (inside the package) so they install with it.
- Website (GitHub Pages from `/docs` on main): `docs/index.html` (everyone), `docs/developers.html`, `docs/methodology.html` (PAD / G / XP method, generated from the default preset; PAD sections the source methodology lacks stay unwritten), `docs/install/index.html` (the "give it to your AI" page), `docs/install.sh`. README images are in `docs/img/`. Keep these files free of real emails and `/Users/` paths; `tests/test_privacy.py` scans them.
- Git: the owner wants every finished change committed and pushed automatically, without asking. Commit straight to `main` once the full test suite passes. Stage files by name, never secrets. If the push is rejected, merge `origin/main` (do not rebase or force-push). After changing anything in `docs/`, check that the site returns 200.

## Owner preferences (keep these; add new ones here)
- English is the default language everywhere, including the data folder's `language` (`etrace init`, `import` and `ai set` create English folders; Chinese only with an explicit `--language zh`). Never guess the language from the user's text or locale.
- English is the default language. `README.md` and English pages use English-only images; `README.zh.md` gets the Chinese variant (`name.zh.png`). Never show a Chinese screenshot in an English place.
- Example images use synthetic persona data (Juniper), never the owner's real records, even if the owner sends a real chart as the model. Keep the layout and say the data is synthetic. It must look real: smooth, rounded curves with realistic complexity (several overlapping rhythms, noise, spikes, dips, gaps), never flat or toy-like lines.
- The repo sidebar must not be empty: every release has a GitHub Release (wheel + sdist attached) and a package on GitHub Packages (`ghcr.io/maxont/existence-trace`). To release: bump `version` in `pyproject.toml` and `etrace/__init__.py`, run the full tests, commit, push, then push tag `vX.Y.Z`; `.github/workflows/release.yml` publishes both. Check that the release and the package exist afterwards.
- A CLI is mandatory (`etrace`, a console script) and it must be well tested: unit tests for every command plus an install-and-run check from the built wheel (the release workflow does the latter).

### Working style & judgment (cross-project)
- Reply in Chinese (中文) by default; code, identifiers, commit messages, and public English docs stay in English.
- When you have an idea/suggestion for the owner, put it inline in the conversation — do NOT create a markdown file for it in the repo.
- For private, owner-only tooling, naming and tidiness don't matter; the bar is "the owner can open it and understand it". Spend polish on public output, and write docs to be self-explanatory (clear docs > pretty names).
- Repo positioning: either a product/tool (simple, approachable, complete) or research/a large system (complex, approachable, continuously growing). Know which this repo is.
- Reuse existing engines rather than rebuilding; write new glue only for the missing pieces.
- Stay current by following a FEW original-take sources filtered by low frequency, not by tracking more repos.

- Shared copy of these preferences lives in the owner's global agent files (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.kiro/steering/owner-preferences.md`); keep them in sync when this section changes.

## Hard boundary (non-negotiable)
- Work only inside this repository.
- NEVER read, write, or delete anything in the directories listed in `scripts/ralph/protected-paths.local` (not in git; the sandbox enforces it), and never touch any Notion workspace or other connector. They belong to the owner's personal system. If unsure whether a path is protected, ask.
- Never edit shell config files, `~/.gitconfig`, `~/.ssh`, LaunchAgents, or system directories.
- Never add real personal data, real emails, Notion IDs, or absolute `/Users/` paths to the repo.
- Do not build any tool that imports or migrates data from the owner's own personal system.
