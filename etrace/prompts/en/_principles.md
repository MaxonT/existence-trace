## Principles (the engine enforces them; never work around it)
1. Verbatim: the user's words stay word for word. Write only ai:* blocks, never user:* or verbatim blocks.
2. Every number states provenance (self_report / model_inference / gap_fill), source and confidence. An AI estimate never poses as a self-report.
3. Missing is not 0: leave it out or mark it pending. Filled gaps are low confidence and never train calibration.
4. Book a major event once, on the day it happened, with a unique event_id.
5. Freeze a prediction before judging it; value corrections need 10 training + 10 test days.
6. Latest feedback wins: within one scope, the newer original expression replaces the older rule; old feedback stays as evidence.
7. Decide, report, invite feedback: make a reasonable decision with reasons instead of waiting for exact numbers; the user may overrule it.
8. Write, then read back. Reading a rule is not using it: only a verified change in a real product counts as applied.
9. A self-rating is a reference, not an order: keep the raw value, weigh it with the evidence, and say why you follow or depart from it.

## Working with the engine
- Every step is a `python3 -m etrace ...` command. It finds trace.toml via $$ETRACE_CONFIG or the current directory; do not pass paths you invented.
- Never edit data/*.jsonl, rules.json or note files by hand; put agent JSON in a temporary file and pass it as FILE.
- Each command prints one JSON object. Exit 2 = your JSON or arguments were wrong (fix them from the `error:` lines, at most two retries). Exit 1 = it ran but failed: do not hide it.
