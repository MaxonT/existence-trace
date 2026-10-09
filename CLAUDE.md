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

## Evaluating a token-saving plugin (before and after checklist)
Run on the owner's local machine. A plugin that cannot show a measured saving stays off.
1. Read the source first. Check for network calls, `eval`, which paths it writes, and whether it rewrites or blocks tool results (for example Read). Run its own tests if it has any.
2. Baseline: with the plugin disabled, run `./scripts/ralph/safe-ralph.sh 2` and record the cost of the two rounds (use ccusage or the plan usage page).
3. Install the plugin, run the same `safe-ralph.sh 2` again from a clean working tree and record the cost.
4. Compare. Keep the plugin only if all of these hold:
   - cost per round drops in a clear way, not by noise (repeat once if the gap is small)
   - both runs finish the same stories with tests passing
   - no round stalls on a blocked or rewritten read, and the sandbox does not break any hook
5. If a hook misbehaves under the sandbox, or Ralph needs the full content of a file such as `prd.json`, disable the plugin for Ralph. Keep it for interactive use only.
6. Install one hook-based plugin at a time. Never stack several that change context.
7. Never paste real keys into prompts while a plugin that logs prompts is on.
