import datetime
import tempfile
import unittest

from etrace import presets
from etrace.models import g
from etrace.store import Store

UTC = datetime.timezone.utc
START = datetime.date(2026, 1, 1)
FULL = {"B1": 6, "B2": 7, "B3": 5, "B4": 4, "E1": 8, "E2": 6, "E3": 5, "E4": 7}


def items(skip=(), **overrides):
    values = dict(FULL, **overrides)
    return [{"code": c, "value": v, "provenance": "model_inference", "source": "note",
             "confidence": "medium"} for c, v in values.items() if c not in skip]


def at(day, hour=20, minute=30):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def predict_day(self, day, preset=None, **overrides):
        g.record(self.store, day, items(**overrides), now=at(day))
        return g.predict(self.store, day, preset=preset, now=at(day, 20, 31))


class PredictTest(Base):
    def test_snapshot_fields_and_immutability(self):
        snap = self.predict_day(START)
        self.assertEqual(snap["id"], "P-20260101-1")
        for key in ("id", "date", "revision", "rubric_version", "items", "created_at"):
            self.assertIn(key, snap)
        self.assertEqual(snap["rubric_version"], presets.load("default")["g"]["version"])
        self.assertEqual(snap["revision"], 1)
        again = g.predict(self.store, START, now=at(START, 21))
        self.assertEqual(again, snap)
        self.assertEqual(len(g.predictions(self.store)), 1)
        # a new revision gets a new snapshot; the old one stays unchanged
        g.record(self.store, START, items(B1=9), now=at(START, 22))
        new = g.predict(self.store, START, now=at(START, 22, 1))
        self.assertEqual((new["id"], new["revision"]), ("P-20260101-2", 2))
        self.assertEqual(g.predictions(self.store, START)[0], snap)

    def test_requires_g_record(self):
        with self.assertRaises(g.GError):
            g.predict(self.store, START, now=at(START))

    def test_gap_filled_snapshot_not_eligible(self):
        g.record(self.store, START, items(skip=("B1",)), now=at(START))
        snap = g.predict(self.store, START, now=at(START, 21))
        self.assertFalse(snap["training_eligible"])


class FeedbackTest(Base):
    def test_requires_prediction_before_feedback(self):
        snap = self.predict_day(START)
        with self.assertRaises(g.GError):
            g.feedback(self.store, "P-20990101-1", targets={"B1": 5}, now=at(START, 23))
        with self.assertRaises(g.GError):  # expressed before the snapshot existed
            g.feedback(self.store, snap["id"], targets={"B1": 5},
                       expressed_at=at(START, 20, 0), now=at(START, 23))
        with self.assertRaises(g.GError):  # same instant is not "before"
            g.feedback(self.store, snap["id"], targets={"B1": 5},
                       expressed_at=snap["created_at"], now=at(START, 23))
        rec = g.feedback(self.store, snap["id"], targets={"B1": 5}, now=at(START, 23))
        self.assertEqual(rec["targets"], {"B1": 5})
        self.assertEqual(rec["rubric_version"], snap["rubric_version"])

    def test_text_feedback_has_no_targets(self):
        snap = self.predict_day(START)
        rec = g.feedback(self.store, snap["id"], text="B1 feels more like 3 today",
                         now=at(START, 23))
        self.assertEqual(rec["targets"], {})
        self.assertEqual(rec["text"], "B1 feels more like 3 today")

    def test_rejects_bad_targets(self):
        snap = self.predict_day(START)
        for targets in ({"X9": 3}, {"B1": 11}, {"B1": "3"}, {"B1": True}):
            with self.assertRaises(g.GError):
                g.feedback(self.store, snap["id"], targets=targets, now=at(START, 23))
        with self.assertRaises(g.GError):
            g.feedback(self.store, snap["id"], now=at(START, 23))


class CalibrationTest(Base):
    def run_days(self, n, label_offset, start=START, preset=None, predicted=6, skip=()):
        for i in range(n):
            day = start + datetime.timedelta(days=i)
            g.record(self.store, day, items(skip=skip, B1=predicted), now=at(day))
            snap = g.predict(self.store, day, preset=preset, now=at(day, 20, 31))
            g.feedback(self.store, snap["id"], targets={"B1": predicted + label_offset},
                       now=at(day, 23))

    def test_needs_ten_plus_ten_days(self):
        self.run_days(19, 2)
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertEqual((len(cal.train_days), len(cal.test_days)), (10, 9))
        self.assertEqual(cal.apply("B1", 6), 6)

    def test_enabled_when_test_mae_lower(self):
        self.run_days(20, 2)
        cal = g.calibration(self.store)
        self.assertTrue(cal.enabled, cal.reason)
        self.assertAlmostEqual(cal.bias["B1"], 2.0)
        self.assertAlmostEqual(cal.mae_raw, 2.0)
        self.assertAlmostEqual(cal.mae_corrected, 0.0)
        self.assertEqual(cal.apply("B1", 6), 8.0)
        self.assertEqual(cal.apply("B1", 9.5), 10.0)
        # test days come after all training days
        self.assertLess(max(cal.train_days), min(cal.test_days))

    def test_disabled_when_correction_does_not_help(self):
        self.run_days(10, 2)
        self.run_days(10, -2, start=START + datetime.timedelta(days=10))
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertGreaterEqual(cal.mae_corrected, cal.mae_raw)

    def test_text_only_feedback_is_not_a_label(self):
        for i in range(20):
            day = START + datetime.timedelta(days=i)
            snap = self.predict_day(day)
            g.feedback(self.store, snap["id"], text="B1 should be 8", now=at(day, 23))
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertEqual(cal.train_days, [])

    def test_rubric_versions_not_mixed(self):
        old = presets.load("default")
        old = dict(old, g=dict(old["g"], version="0.9.0"))
        self.run_days(10, 2, preset=old)
        self.run_days(10, 2, start=START + datetime.timedelta(days=10))
        current = g.calibration(self.store)
        self.assertFalse(current.enabled)
        self.assertEqual(len(current.train_days), 10)
        self.assertEqual(current.test_days, [])
        self.assertFalse(g.calibration(self.store, version="0.9.0").enabled)

    def test_gap_filled_days_are_not_labels(self):
        self.run_days(20, 2, skip=("E1",))
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertEqual(cal.train_days, [])


if __name__ == "__main__":
    unittest.main()
