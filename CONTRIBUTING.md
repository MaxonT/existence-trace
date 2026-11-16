# Contributing to existence-trace

existence-trace is an open-source CLI (`etrace`) for an AI that reads your journal, asks where it is unsure, turns your answers into rules, and has to prove it used them. Issues, questions and pull requests are welcome.

## Before you start

- Open an issue first for anything bigger than a small fix, so we can agree on the shape.
- Python 3.11 or newer, standard library only. Please do not add runtime dependencies.
- Commands stay short: a common action is one word plus at most one argument, with sensible defaults. Please do not add a required flag where a default will do.
- PAD, G and XP are personal reflection aids, not clinical scales. Please do not describe them as medical or psychological measurements.

## Run the tests

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_privacy.py` fails if an email address, a Notion-style id or a home directory path lands anywhere in the repository. Use `example.com` addresses and synthetic data in tests and examples, never real records.

## Pull requests

- Keep a change small. Say what it changes and how you checked it.
- Add or update a unit test for every command you change.
- English is the default language everywhere. Chinese appears only where a file is explicitly the Chinese variant, such as `README.zh.md`.
- If an AI tool helped write the change, say so in the pull request.

## Reporting a bug

Use the bug report template. Include your Python version and the command that failed. Please remove any private journal text from what you paste.
