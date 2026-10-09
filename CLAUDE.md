# existence-trace — instructions for every agent run

- Ralph files: PRD is `scripts/ralph/prd.json`, progress log is `scripts/ralph/progress.txt`. Read the Codebase Patterns and Hard boundary sections of the progress log first.
- Design source of truth: `docs/SYSTEM_DESIGN.md`.
- Python 3 standard library only. Package: `etrace`. Tests: `python3 -m unittest discover -s tests -v`.

## Hard boundary (non-negotiable)
- Work only inside this repository.
- NEVER read, write, or delete anything in the directories listed in `scripts/ralph/protected-paths.local` (not in git; the sandbox enforces it), and never touch any Notion workspace or other connector. They belong to the owner's personal system. If unsure whether a path is protected, ask.
- Never edit shell config files, `~/.gitconfig`, `~/.ssh`, LaunchAgents, or system directories.
- Never add real personal data, real emails, Notion IDs, or absolute `/Users/` paths to the repo.
- Do not build any tool that imports or migrates data from the owner's own personal system.

## Starting Ralph (owner's local machine, 3 steps)
1. Pick a login in the terminal tab you will use (redo this when you switch tabs).
   - API credit: `export ANTHROPIC_API_KEY="sk-ant-<your-key>"` (the key must be created inside the workspace).
   - Subscription: `unset ANTHROPIC_API_KEY`.
2. `cd ~/Desktop/existence-trace`
3. `./scripts/ralph/safe-ralph.sh 5` (the number is the maximum number of rounds).

- Always use `safe-ralph.sh`. It turns on the sandbox that blocks `.codex` and Notion. Running `ralph.sh` directly is refused.
- Each round implements one story and takes a few minutes. A quiet terminal is normal.
- `Ctrl+C` stops the run; commits already made are kept.
- Check progress with `git log --oneline` or `scripts/ralph/progress.txt`.
- Never put a real API key in the repo; keep placeholders only.
