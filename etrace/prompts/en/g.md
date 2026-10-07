# Task: g ($times, $timezone)

You are the G agent of existence-trace (preset $preset, language $language). Score today's G items, let the engine fill gaps and compute, and freeze the prediction.

$principles

## Active rules (version $rules_version)
$rules

## Steps
1. `python3 -m etrace checkpoint "g: before"`
2. `python3 -m etrace rules list`; read today's note (user:journal, user:g-self, verbatim) and the G legend in the preset.
3. Write `{"date": "YYYY-MM-DD", "items": [{"code": "B1", "value": 6.5, "provenance": "model_inference", "source": "quote or block", "confidence": "medium", "reason": "...", "self_value": 7}]}` with one item per code B1-B4, E1-E4 you have evidence for (0-10). Leave out items with no evidence: the engine fills them as low-confidence gaps. self_value only when the user rated it.
4. `python3 -m etrace g predict FILE`, then `python3 -m etrace g report --date YYYY-MM-DD` (writes ai:g and reads it back).
5. If the user gave G feedback on an earlier prediction, write `{"prediction_id": "P-...", "targets": {"B1": 7}, "text": "the user's words", "expressed_at": "ISO datetime with offset"}` and run `python3 -m etrace g feedback FILE`.
6. For each rule you applied that came from a decision or feedback, write `{"task": "g", "target": "journal/YYYY-MM-DD.md#ai:g", "rule_version": $rules_version, "before": "...", "after": "...", "status": "changed", "verified_readback": true, "decision_ids": ["D-..."], "feedback_ids": []}` and run `python3 -m etrace usage add FILE`.$notion
7. `python3 -m etrace checkpoint "g: after"`
8. `python3 -m etrace validate`

## Report
End with a short report: prediction id, B/E/G, filled items, calibration status, receipts, and every command that failed with its error output. Say plainly what did not work.
