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
        # ridge: delta = sum(w r) / (sum(w) + lambda), w = 2^(-age/60)
        ws = [2 ** (-age / 60) for age in range(20)]
        self.assertAlmostEqual(cal.bias["B1"], 2 * sum(ws) / (sum(ws) + 5))
        self.assertEqual(cal.bias["B2"], 0.0)
        self.assertAlmostEqual(cal.mae_raw, 2.0)
        self.assertLess(cal.mae_corrected, cal.mae_raw - 0.2)
        self.assertAlmostEqual(cal.apply("B1", 6), 6 + cal.bias["B1"])
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

    def label_days(self, n, targets_for, start=START, frozen_late=False):
        for i in range(n):
            day = start + datetime.timedelta(days=i)
            stamp = day + datetime.timedelta(days=3) if frozen_late else day
            g.record(self.store, day, items(), now=at(stamp))
            snap = g.predict(self.store, day, now=at(stamp, 20, 31))
            g.feedback(self.store, snap["id"], targets=targets_for(i), now=at(stamp, 23))

    def test_feedback_accepts_group_and_g_targets(self):
        snap = self.predict_day(START)
        rec = g.feedback(self.store, snap["id"], targets={"G": 7, "B": 6, "E3": 4},
                         now=at(START, 23))
        self.assertEqual(rec["targets"], {"E3": 4, "B": 6, "G": 7})

    def test_design_vectors(self):
        p = presets.load("default")
        self.assertEqual(g.design_vector("B3", p), [0, 0, 1, 0, 0, 0, 0, 0])
        self.assertEqual(g.design_vector("B", p), [.28, .18, .36, .18, 0, 0, 0, 0])
        self.assertEqual(g.design_vector("E", p), [0, 0, 0, 0, .22, .14, .36, .28])
        self.assertEqual(g.design_vector("G", p), [.14, .09, .18, .09, .11, .07, .18, .14])
        with self.assertRaises(g.GError):
            g.design_vector("X", p)

    def test_g_level_feedback_trains_all_items(self):
        raw_g = g.compute(items()).G
        self.label_days(20, lambda i: {"G": raw_g + 1.5})
        cal = g.calibration(self.store)
        self.assertTrue(cal.enabled, cal.reason)
        self.assertTrue(all(cal.bias[c] > 0 for c in g.CODES))
        self.assertGreater(cal.bias["B3"], cal.bias["B2"])  # heavier weight, larger share

    def test_same_day_labels_share_weight_one(self):
        # one day with eight item labels must not outweigh one day with a single label
        days = {"2026-01-01": {"prospective": True, "labels": [
                    (g.design_vector(c), [6.0] * 8, 8.0) for c in g.CODES]}}
        per_item = g.fit_offsets(days, "2026-01-01")["B1"]
        self.assertAlmostEqual(per_item, (1 / 8) * 2 / (1 / 8 + 5))

    def test_offsets_are_clipped(self):
        days = {f"2026-01-{d:02d}": {"prospective": True, "labels": [
                    (g.design_vector("B1"), [0.0] * 8, 10.0)]} for d in range(1, 31)}
        self.assertEqual(g.fit_offsets(days, "2026-01-30")["B1"], 2.0)

    def test_retrospective_days_train_but_do_not_test(self):
        self.label_days(10, lambda i: {"B1": 8})
        self.label_days(10, lambda i: {"B1": 8}, start=START + datetime.timedelta(days=10),
                        frozen_late=True)
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertEqual((len(cal.train_days), len(cal.test_days)), (10, 0))
        self.assertGreater(cal.bias["B1"], 0)

    def test_small_gain_stays_off(self):
        # predictions are 6; labels alternate 6.2 / 5.8, so any offset barely helps
        self.label_days(20, lambda i: {"B1": 6.2 if i % 2 else 5.8})
        cal = g.calibration(self.store)
        self.assertFalse(cal.enabled)
        self.assertIn("less than 0.2", cal.reason)

    def test_recent_deterioration_turns_off(self):
        # 20 days at +2 build a strong offset, then 10 days at +0 make it hurt
        self.label_days(20, lambda i: {"B1": 8})
        self.label_days(10, lambda i: {"B1": 6}, start=START + datetime.timedelta(days=20))
        cal = g.calibration(self.store)
        self.assertGreater(cal.mae_raw - cal.mae_corrected, -1)  # still computed
        self.assertGreaterEqual(cal.recent_mae_corrected - cal.recent_mae_raw, 0.3)
        self.assertFalse(cal.enabled)
        self.assertIn("last 10", cal.reason)

    def test_mae_is_averaged_per_day_first(self):
        days = [[(g.design_vector(c), [6.0] * 8, 6.0) for c in g.CODES],
                [(g.design_vector("B1"), [6.0] * 8, 8.0)]]
        self.assertEqual([g._day_mae(d, {}) for d in days], [0.0, 2.0])


if __name__ == "__main__":
    unittest.main()
