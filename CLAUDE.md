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
