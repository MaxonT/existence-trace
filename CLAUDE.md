# existence-trace — instructions for every agent run

- Ralph files: PRD is `scripts/ralph/prd.json`, progress log is `scripts/ralph/progress.txt`. Read the Codebase Patterns and Hard boundary sections of the progress log first.
- Design source of truth: `docs/SYSTEM_DESIGN.md`.
- Python 3 standard library only. Package: `etrace`. Tests: `python3 -m unittest discover -s tests -v`.
- Packaging: `pyproject.toml` ships the `etrace` console script. Presets live in `etrace/preset_data/` (inside the package) so they install with it.
- Website (GitHub Pages from `/docs` on main): `docs/index.html` (everyone), `docs/developers.html`, `docs/methodology.html` (PAD / G / XP method, generated from the default preset; PAD sections the source methodology lacks stay unwritten), `docs/install/index.html` (the "give it to your AI" page), `docs/install.sh`. README images are in `docs/img/`. Keep these files free of real emails and `/Users/` paths; `tests/test_privacy.py` scans them.
- Git: the owner wants every finished change committed and pushed automatically, without asking. Commit straight to `main` once the full test suite passes. Stage files by name, never secrets. If the push is rejected, merge `origin/main` (do not rebase or force-push). After changing anything in `docs/`, check that the site returns 200.

## Hard boundary (non-negotiable)
- Work only inside this repository.
- NEVER read, write, or delete anything in the directories listed in `scripts/ralph/protected-paths.local` (not in git; the sandbox enforces it), and never touch any Notion workspace or other connector. They belong to the owner's personal system. If unsure whether a path is protected, ask.
- Never edit shell config files, `~/.gitconfig`, `~/.ssh`, LaunchAgents, or system directories.
- Never add real personal data, real emails, Notion IDs, or absolute `/Users/` paths to the repo.
- Do not build any tool that imports or migrates data from the owner's own personal system.
