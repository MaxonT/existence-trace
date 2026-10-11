import unittest

from etrace import presets
from etrace.models import g


def item(code, value, **kw):
    data = {"code": code, "value": value, "provenance": "model_inference",
            "source": "note 2026-01-05", "confidence": "medium"}
    data.update(kw)
    return data


FULL = {"B1": 6, "B2": 7, "B3": 5, "B4": 4, "E1": 8, "E2": 6, "E3": 5, "E4": 7}


def full_items(**overrides):
    values = dict(FULL, **overrides)
    return [item(code, value) for code, value in values.items()]


class ComputeTest(unittest.TestCase):
    def setUp(self):
        self.preset = presets.load("default")

    def test_weighted_groups_and_g(self):
        r = g.compute(full_items(), self.preset)
        b = .28 * 6 + .18 * 7 + .36 * 5 + .18 * 4
        e = .22 * 8 + .14 * 6 + .36 * 5 + .28 * 7
        self.assertAlmostEqual(r.B, b, places=12)
        self.assertAlmostEqual(r.E, e, places=12)
        self.assertAlmostEqual(r.G, (b + e) / 2, places=12)
        self.assertEqual(r.missing, [])

    def test_no_early_rounding(self):
        r = g.compute(full_items(B1=6.04, E1=8.04), self.preset)
        b = .28 * 6.04 + .18 * 7 + .36 * 5 + .18 * 4
        self.assertAlmostEqual(r.B, b, places=12)
        self.assertNotEqual(r.B, round(r.B, 1))

    def test_display_rounds_one_decimal_half_up(self):
        self.assertEqual(g.display(6.25), "6.3")
        self.assertEqual(g.display(6.24), "6.2")
        self.assertEqual(g.display(5), "5.0")
        self.assertEqual(g.display(None, "zh"), "缺")
        self.assertEqual(g.display(None, "en"), "n/a")

    def test_missing_item_gives_none_not_zero(self):
        items = [i for i in full_items() if i["code"] != "E3"]
        r = g.compute(items, self.preset)
        self.assertIsNotNone(r.B)
        self.assertIsNone(r.E)
        self.assertIsNone(r.G)
        self.assertEqual(r.missing, ["E3"])

    def test_empty(self):
        r = g.compute([], self.preset)
        self.assertEqual((r.B, r.E, r.G), (None, None, None))

    def test_zero_values_are_real(self):
        r = g.compute(full_items(**{c: 0 for c in FULL}), self.preset)
        self.assertEqual((r.B, r.E, r.G), (0, 0, 0))

    def test_default_preset_loaded(self):
        self.assertAlmostEqual(g.compute(full_items()).G, g.compute(full_items(), self.preset).G)

    def test_duplicate_code_rejected(self):
        with self.assertRaises(g.GError) as cm:
            g.compute(full_items() + [item("B1", 3)], self.preset)
        self.assertIn("duplicate B1", str(cm.exception))

    def test_accepts_gitem_objects(self):
        objs = [g.GItem.from_dict(i) for i in full_items()]
        self.assertAlmostEqual(g.compute(objs).G, g.compute(full_items()).G)


class ItemTest(unittest.TestCase):
    def test_requires_provenance_source_confidence(self):
        for field in ("provenance", "source", "confidence"):
            data = item("B1", 5)
            del data[field]
            with self.assertRaises(g.GError) as cm:
                g.GItem.from_dict(data)
            self.assertIn(f"{field}: required", cm.exception.errors)

    def test_bad_fields(self):
        with self.assertRaises(g.GError) as cm:
            g.GItem.from_dict(item("B9", 11, provenance="guess", source=" ", confidence="sure"))
        text = str(cm.exception)
        for field in ("code", "value", "provenance", "source", "confidence"):
            self.assertIn(f"{field}:", text)

    def test_unknown_field(self):
        with self.assertRaises(g.GError):
            g.GItem.from_dict(item("B1", 5, mood="ok"))

    def test_bool_not_number(self):
        with self.assertRaises(g.GError):
            g.GItem.from_dict(item("B1", True))

    def test_self_value_preserved_and_needs_reason_when_different(self):
        with self.assertRaises(g.GError) as cm:
            g.GItem.from_dict(item("B1", 6, self_value=4))
        self.assertIn("reason", str(cm.exception))
        it = g.GItem.from_dict(item("B1", 6, self_value=4, reason="slept well per note"))
        self.assertEqual(it.self_value, 4)
        self.assertEqual(it.value, 6)
        self.assertEqual(it.to_dict()["self_value"], 4)
        same = g.GItem.from_dict(item("B1", 4, self_value=4, provenance="self_report"))
        self.assertIsNone(same.reason)

    def test_self_value_does_not_change_score(self):
        items = full_items()
        items[0] = item("B1", 6, self_value=2, reason="evidence says otherwise")
        self.assertAlmostEqual(g.compute(items).B, g.compute(full_items()).B)
        self.assertEqual(g.compute(items).items["B1"].self_value, 2)

    def test_round_trip(self):
        data = item("E4", 7.5, self_value=7.5)
        self.assertEqual(g.GItem.from_dict(data).to_dict(), data)

    def test_errors_carry_item_index(self):
        bad = full_items()
        bad[2] = item("B3", 12)
        with self.assertRaises(g.GError) as cm:
            g.compute(bad)
        self.assertTrue(any(e.startswith("items[2].value") for e in cm.exception.errors))


class LegendTest(unittest.TestCase):
    def test_zh(self):
        text = g.legend(presets.load("default"), "zh")
        self.assertIn("G = (B + E) / 2", text)
        self.assertIn("B 稳定", text)
        self.assertIn("E 探索", text)
        self.assertIn("B1 睡眠与恢复 (0.28)：0–2 明确失眠", text)
        self.assertIn("E3 期待与意义 (0.36)", text)
        self.assertIn("8–10 对明天有本人认可的期待与方向", text)
        self.assertIn("10 明确而充分", text)
        self.assertIn("不是临床量表", text)

    def test_en_both_presets(self):
        for name in presets.NAMES:
            p = presets.load(name)
            text = g.legend(p, "en")
            self.assertIn("G = (B + E) / 2", text)
            for code, weight, label in presets.g_items(p):
                self.assertIn(f"{code} {label['en']} ({weight:g})", text)
            self.assertIn("not a clinical scale", text)
            scale = text[text.index("Scale 0-10"):]
            self.assertLess(scale.index(" 0 "), scale.index(" 10 "))
            for key in presets.G_ITEM_ANCHORS:
                self.assertIn(f"{key} ", text)


if __name__ == "__main__":
    unittest.main()
