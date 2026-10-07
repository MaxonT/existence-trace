import unittest

from etrace import presets
from etrace.models import pad
from etrace.models.pad import PadError, PadPoint


def point(**kw):
    base = {"date": "2026-10-07", "slot": "morning", "P": 3, "A": 3, "D": 3,
            "provenance": "self_report"}
    base.update(kw)
    return PadPoint.from_dict(base)


def inferred(**kw):
    kw.setdefault("provenance", "model_inference")
    kw.setdefault("source", "journal 09:10 entry")
    kw.setdefault("confidence", "medium")
    return point(**kw)


class PadPointTest(unittest.TestCase):
    def test_valid_self_report_and_exact_time(self):
        p = point(slot="13:45", P=4.5, A=0, D=5)
        self.assertEqual(p.named_slot, "noon")
        self.assertTrue(p.exact_time)
        self.assertFalse(point().exact_time)

    def test_values_must_be_within_0_5(self):
        for bad in (-0.1, 5.1, "3", True, None):
            with self.assertRaises(PadError) as cm:
                point(P=bad)
            self.assertTrue(any(e.startswith("P:") for e in cm.exception.errors), cm.exception.errors)

    def test_slot_names_and_times(self):
        self.assertEqual(pad.slot_of("07:00"), "early_morning")
        self.assertEqual(pad.slot_of("10:59"), "morning")
        self.assertEqual(pad.slot_of("17:30"), "afternoon")
        self.assertEqual(pad.slot_of("23:00"), "late_night")
        self.assertEqual(pad.slot_of("02:00"), "late_night")
        for bad in ("dawn", "25:00", "9:00", None):
            with self.assertRaises(PadError):
                point(slot=bad)

    def test_model_inference_needs_source_and_confidence(self):
        with self.assertRaises(PadError) as cm:
            point(provenance="model_inference")
        self.assertIn("source: required for model_inference", cm.exception.errors)
        self.assertIn("confidence: required for model_inference", cm.exception.errors)
        with self.assertRaises(PadError):
            inferred(confidence="sure")
        with self.assertRaises(PadError):
            inferred(source="  ")
        self.assertEqual(inferred().confidence, "medium")

    def test_bad_provenance_and_date(self):
        with self.assertRaises(PadError):
            point(provenance="gap_fill")
        with self.assertRaises(PadError):
            point(date="2026-13-01")

    def test_self_value_kept_and_reason_required_when_different(self):
        with self.assertRaises(PadError) as cm:
            inferred(P=2, self_value={"P": 4, "A": 3, "D": 3})
        self.assertIn("reason: required when the values differ from self_value", cm.exception.errors)
        p = inferred(P=2, self_value={"P": 4, "A": 3, "D": 3}, reason="journal says the meeting drained me")
        self.assertEqual(p.self_value, {"P": 4, "A": 3, "D": 3})
        self.assertEqual(p.P, 2)
        self.assertEqual(p.to_dict()["self_value"], {"P": 4, "A": 3, "D": 3})
        same = inferred(self_value={"P": 3, "A": 3, "D": 3})
        self.assertIsNone(same.reason)
        with self.assertRaises(PadError):
            inferred(self_value={"P": 3, "A": 3})
        with self.assertRaises(PadError):
            inferred(self_value={"P": 3, "A": 3, "D": 9}, reason="x")

    def test_from_dict_reports_unknown_and_missing_fields(self):
        with self.assertRaises(PadError) as cm:
            PadPoint.from_dict({"date": "2026-10-07", "mood": 1})
        errors = cm.exception.errors
        self.assertIn("mood: unknown field", errors)
        self.assertIn("slot: required", errors)
        self.assertIn("provenance: required", errors)

    def test_duration_must_be_positive(self):
        with self.assertRaises(PadError):
            point(duration_hours=0)
        self.assertEqual(point(duration_hours=2.5).duration_hours, 2.5)

    def test_to_dict_round_trip(self):
        p = inferred(duration_hours=2)
        self.assertEqual(PadPoint.from_dict(p.to_dict()), p)


class ProjectTest(unittest.TestCase):
    def test_project_words(self):
        self.assertEqual(pad.project(3, 3, 3), "专注")
        self.assertEqual(pad.project(1, 1, 1), "无助")
        self.assertEqual(pad.project(5, 5, 5), "豪迈")
        self.assertEqual(pad.project(0, 0, 0), "无助")
        self.assertEqual(pad.project(4.4, 2.6, 3.4), "开心")
        tiger = presets.load("tiger-method")
        self.assertEqual(pad.project(3, 3, 3, preset=tiger, language="en"),
                         presets.pad_word(tiger, 3, 3, 3, "en"))

    def test_project_rejects_out_of_range(self):
        with self.assertRaises(PadError):
            pad.project(6, 3, 3)


class DailySummaryTest(unittest.TestCase):
    def test_duration_weighted_mean(self):
        s = pad.daily_summary([point(P=2, A=1, D=4, duration_hours=1),
                               point(slot="noon", P=5, A=4, D=1, duration_hours=3)])
        self.assertAlmostEqual(s.P, (2 + 15) / 4)
        self.assertAlmostEqual(s.A, (1 + 12) / 4)
        self.assertAlmostEqual(s.D, (4 + 3) / 4)
        self.assertFalse(s.equal_weighted)
        self.assertEqual(s.points, 2)

    def test_unknown_durations_equal_weighted_and_flagged(self):
        s = pad.daily_summary([point(P=1), point(slot="noon", P=4)])
        self.assertAlmostEqual(s.P, 2.5)
        self.assertTrue(s.equal_weighted)
        self.assertEqual(s.unknown_durations, 2)
        mixed = pad.daily_summary([point(P=1, duration_hours=2), point(slot="noon", P=4, duration_hours=4),
                                   point(slot="afternoon", P=1)])
        self.assertAlmostEqual(mixed.P, (2 + 16 + 3) / 9)
        self.assertEqual(mixed.unknown_durations, 1)

    def test_empty_and_mixed_dates(self):
        self.assertIsNone(pad.daily_summary([]))
        with self.assertRaises(PadError):
            pad.daily_summary([point(), point(date="2026-10-08")])


class ValidateDayTest(unittest.TestCase):
    def test_requires_morning_noon_afternoon(self):
        full = [point(), point(slot="noon"), point(slot="15:30")]
        self.assertTrue(pad.validate_day(full).ok)
        result = pad.validate_day([point(), point(slot="evening")])
        self.assertFalse(result.ok)
        self.assertIn("missing noon reading", result.problems)
        self.assertIn("missing afternoon reading", result.problems)
        self.assertFalse(pad.validate_day([]).ok)

    def test_rejects_several_dates(self):
        pts = [point(), point(slot="noon"), point(slot="afternoon", date="2026-10-08")]
        self.assertFalse(pad.validate_day(pts).ok)


if __name__ == "__main__":
    unittest.main()
