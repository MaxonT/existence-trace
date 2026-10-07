import datetime
import tempfile
import unittest

from etrace.models import g
from etrace.store import Store

NOW = datetime.datetime(2026, 1, 10, 20, 30, tzinfo=datetime.timezone.utc)
FULL = {"B1": 6, "B2": 7, "B3": 5, "B4": 4, "E1": 8, "E2": 6, "E3": 5, "E4": 7}


def item(code, value):
    return {"code": code, "value": value, "provenance": "model_inference",
            "source": "note", "confidence": "medium"}


def items(skip=(), **overrides):
    values = dict(FULL, **overrides)
    return [item(c, v) for c, v in values.items() if c not in skip]


class GapFillTest(unittest.TestCase):
    def test_complete_day_is_untouched(self):
        day = g.gap_fill("2026-01-05", items(), {})
        self.assertFalse(day.explicit_gap_fill)
        self.assertEqual(day.filled, [])
        self.assertTrue(g.training_eligible(day))

    def test_bridge_interpolates_by_days(self):
        history = {"2026-01-01": items(B1=2), "2026-01-05": items(B1=10)}
        day = g.gap_fill("2026-01-02", items(skip=("B1",)), history)
        b1 = day.items["B1"]
        self.assertEqual(b1.method, "historical_bridge")
        self.assertAlmostEqual(b1.value, 4.0)
        self.assertEqual(b1.provenance, "gap_fill")
        self.assertEqual(b1.confidence, "low")
        self.assertEqual(b1.neighbors, [{"date": "2026-01-01", "value": 2},
                                        {"date": "2026-01-05", "value": 10}])
        self.assertTrue(day.explicit_gap_fill)
        self.assertEqual(day.filled, ["B1"])
        self.assertEqual(day.items["B2"].provenance, "model_inference")
        self.assertFalse(g.training_eligible(day))

    def test_nearest_neighbors_and_gap_fill_values_ignored(self):
        filled = g.gap_fill("2026-01-03", items(skip=("E2",)), {"2026-01-01": items(E2=1)})
        history = {"2025-12-01": items(E2=9), "2026-01-01": items(E2=1),
                   "2026-01-03": filled.item_dicts(), "2026-01-04": items(skip=("E2",))}
        day = g.gap_fill("2026-01-02", items(skip=("E2",)), history)
        self.assertEqual(day.items["E2"].method, "continuity_carry_forward")
        self.assertEqual(day.items["E2"].value, 1)
        self.assertEqual(day.items["E2"].neighbors, [{"date": "2026-01-01", "value": 1}])

    def test_scale_prior_without_left_value(self):
        day = g.gap_fill("2026-01-02", items(skip=("E4",)), {"2026-01-05": items(E4=9)})
        self.assertEqual(day.items["E4"].method, "scale_prior")
        self.assertEqual(day.items["E4"].value, 5.0)
        self.assertEqual(day.items["E4"].neighbors, [])

    def test_given_gap_fill_items_rejected(self):
        filled = g.gap_fill("2026-01-02", items(skip=("E4",)), {}).item_dicts()
        with self.assertRaises(g.GError):
            g.gap_fill("2026-01-02", filled, {})

    def test_gap_fill_item_validation(self):
        base = dict(code="B1", value=5.0, provenance="gap_fill", source="gap_fill:scale_prior",
                    method="scale_prior", neighbors=[])
        with self.assertRaises(g.GError) as ctx:
            g.GItem(confidence="medium", **base)
        self.assertIn("confidence: gap_fill items must have confidence low", ctx.exception.errors)
        with self.assertRaises(g.GError):
            g.GItem(**dict(base, confidence="low", method="guess"))
        with self.assertRaises(g.GError):
            g.GItem(code="B1", value=5, provenance="self_report", source="s", confidence="high",
                    method="scale_prior")
        restored = g.GItem.from_dict(g.GItem(confidence="low", **base).to_dict())
        self.assertEqual(restored.method, "scale_prior")

    def test_compute_accepts_filled_day(self):
        day = g.gap_fill("2026-01-02", items(skip=("E4",)), {})
        self.assertIsNotNone(g.compute(day.item_dicts()).G)

    def test_training_eligible_on_records(self):
        self.assertFalse(g.training_eligible({"explicit_gap_fill": True, "items": []}))
        self.assertTrue(g.training_eligible({"explicit_gap_fill": False, "items": items()}))


class RevisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_later_real_value_creates_bridge_revision(self):
        g.record(self.dir, "2026-01-01", items(B3=2), now=NOW)
        added = g.record(self.dir, "2026-01-02", items(skip=("B3",)), now=NOW)
        self.assertEqual([r["id"] for r in added], ["G-20260102-r1"])
        first = g.latest(self.dir, "2026-01-02")
        b3 = next(i for i in first["items"] if i["code"] == "B3")
        self.assertEqual(b3["method"], "continuity_carry_forward")
        self.assertTrue(first["explicit_gap_fill"])
        self.assertFalse(g.training_eligible(first))

        added = g.record(self.dir, "2026-01-03", items(B3=6), now=NOW)
        self.assertEqual([r["id"] for r in added], ["G-20260103-r1", "G-20260102-r2"])
        revs = g.revisions(self.dir, "2026-01-02")
        self.assertEqual([r["revision"] for r in revs], [1, 2])
        self.assertEqual(revs[0], first)
        self.assertEqual(revs[1]["supersedes"], "G-20260102-r1")
        b3 = next(i for i in revs[1]["items"] if i["code"] == "B3")
        self.assertEqual(b3["method"], "historical_bridge")
        self.assertAlmostEqual(b3["value"], 4.0)
        self.assertEqual(len(Store(self.dir).read_all("g")), 4)

    def test_rerun_without_change_adds_nothing(self):
        g.record(self.dir, "2026-01-02", items(skip=("B3",)), now=NOW)
        self.assertEqual(g.refill(self.dir, now=NOW), [])
        self.assertEqual(g.record(self.dir, "2026-01-02", items(skip=("B3",)), now=NOW), [])
        self.assertEqual(len(g.revisions(self.dir, "2026-01-02")), 1)

    def test_naive_now_rejected(self):
        with self.assertRaises(g.GError):
            g.record(self.dir, "2026-01-02", items(), now=datetime.datetime(2026, 1, 2))


if __name__ == "__main__":
    unittest.main()
