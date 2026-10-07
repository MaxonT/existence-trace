# Example persona: Juniper

Juniper is **fictional**: a baker who is learning to code and just took her first paid cake order.
Nothing here is real data.

- `journal/` holds three days of notes (2026-03-02 to 2026-03-04) as Juniper left them: her PAD
  and G self-ratings, her journal text, and her feedback for the AI. The AI blocks are still empty.
- `agent/` holds what an agent would produce for those days: one question, one decision with
  three rule changes, PAD points, G items, XP levels, headlines and narratives, and the usage
  receipt that proves the decision was used.
- `expected.json` lists the results the engine must produce from them (question and feedback
  ids, new rules, G and XP values, PAD problems).

`tests/test_example.py` runs the whole loop on a temporary copy. To run it by hand:

```sh
python3 -m etrace init --preset default --language en --timezone UTC --dir /tmp/juniper
export ETRACE_CONFIG=/tmp/juniper/trace.toml
cp examples/persona/journal/*.md /tmp/juniper/journal/
A=examples/persona/agent

python3 -m etrace question add $A/question.json     # Q-20260303-1: commute or work?
python3 -m etrace feedback ingest                   # her [Q-20260303-1] reply answers it
python3 -m etrace decide $A/decision.json           # 1 decision, 3 new rules
for d in 2026-03-02 2026-03-03 2026-03-04; do
  python3 -m etrace pad add $A/$d-pad.json && python3 -m etrace pad chart $d
  python3 -m etrace g predict $A/$d-g.json && python3 -m etrace g report --date $d
  python3 -m etrace xp score $A/$d-xp.json
  python3 -m etrace note write $d headline $A/$d-headline.md
  python3 -m etrace note write $d narrative $A/$d-narrative.md
done
python3 -m etrace outreach send --scan ok --date 2026-03-04   # decision report in the note
```

Then copy `agent/receipt.json`, fill in `rule_version` (from `rules list`) and `decision_ids`
(from `decide`), and finish:

```sh
python3 -m etrace usage add receipt.json
python3 -m etrace validate
python3 -m etrace audit weekly
```

Things worth noticing:

- Day two's `B1` is a `model_inference` of 4 next to her self-rating of 5 (principle 9), and the
  21:00 PAD point is a low-confidence inference, never a self-report (principle 2).
- Day three has no noon PAD reading; the engine reports it instead of inventing one (principle 3).
- The first paid cake order is an XP event booked once, on the day it happened (principle 4).
