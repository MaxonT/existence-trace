import copy
import json
import os
import shutil
import tempfile
import unittest

from etrace import presets

EXPECTED_WORDS_ZH = [
    "无助", "沮丧", "轻蔑", "担忧", "烦躁", "不满", "恐慌", "紧张", "愤怒",
    "茫然", "平静", "淡定", "困惑", "专注", "笃定", "震惊", "惊讶", "警觉",
    "依恋", "满足", "惬意", "感动", "开心", "自豪", "惊喜", "兴奋", "豪迈",
]
XP_NAMES_ZH = {
    "default": ["节律与稳定", "学习与吸收", "边界与休息", "行动与尝试", "反思与发现", "联结与探索"],
    "tiger-method": ["日程与稳定今天", "真正吸收与学习", "停下优化与边界", "在不确定中行动",
                     "个人发现与知行整合", "真实联结与自主探索"],
}


class BundledPresetsTest(unittest.TestCase):
    def test_both_presets_have_all_files_and_validate(self):
        for name in presets.NAMES:
            for filename in presets.FILES:
                self.assertTrue(os.path.exists(os.path.join(presets.PRESETS_DIR, name, filename)), filename)
            p = presets.load(name)
            self.assertEqual(p["name"], name)

    def test_pad_table_matches_design(self):
        for name in presets.NAMES:
            pad = presets.load(name)["pad"]
            # rows P,A (low/mid/high), columns D low|mid|high
            order = [(p, a, d) for p in presets.PAD_BANDS for a in presets.PAD_BANDS for d in presets.PAD_BANDS]
            by_key = {(c["P"], c["A"], c["D"]): c["zh"] for c in pad["words"]}
            self.assertEqual([by_key[k] for k in order], EXPECTED_WORDS_ZH)
            for axis in presets.PAD_AXES:
                self.assertEqual(sorted(pad["axes"][axis]["levels"]), ["1", "2", "3", "4", "5"])

    def test_pad_projection(self):
        p = presets.load("default")
        self.assertEqual([presets.band(x) for x in (1, 2, 3, 4, 5)], ["low", "low", "mid", "high", "high"])
        self.assertEqual(presets.band(2.4), "low")
        self.assertEqual(presets.band(2.5), "mid")
        self.assertEqual(presets.pad_word(p, 3, 3, 3), "专注")
        self.assertEqual(presets.pad_word(p, 5, 5, 5, "en"), "exhilarated")
        self.assertEqual(presets.pad_word(p, 1, 2, 4), "轻蔑")
        with self.assertRaises(ValueError):
            presets.band(0)

    def test_g_weights_and_anchors(self):
        for name in presets.NAMES:
            p = presets.load(name)
            weights = {code: w for code, w, _ in presets.g_items(p)}
            self.assertEqual([weights[c] for c in ("B1", "B2", "B3", "B4")], [0.28, 0.18, 0.36, 0.18])
            self.assertEqual([weights[c] for c in ("E1", "E2", "E3", "E4")], [0.22, 0.14, 0.36, 0.28])
            self.assertEqual(sorted(p["g"]["anchors"], key=int), ["0", "2", "5", "8", "10"])
            self.assertIn("G = (B + E) / 2", p["g"]["formula"]["en"])

    def test_xp_dimensions_and_no_events(self):
        for name in presets.NAMES:
            xp = presets.load(name)["xp"]
            self.assertEqual([d["cap"] for d in xp["dimensions"]], [18, 16, 16, 14, 14, 12])
            self.assertEqual([d["label"]["zh"] for d in xp["dimensions"]], XP_NAMES_ZH[name])
            self.assertEqual(sorted(xp["levels"]), ["0", "1", "2", "3", "4"])
            self.assertEqual((xp["ordinary"]["min"], xp["ordinary"]["max"]), (0, 10))
            self.assertIs(xp["events"]["capped"], False)
            self.assertEqual(xp["booked_events"], [])
        default = [d["label"]["zh"] for d in presets.load("default")["xp"]["dimensions"]]
        tiger = [d["label"]["zh"] for d in presets.load("tiger-method")["xp"]["dimensions"]]
        self.assertNotEqual(default, tiger)

    def test_rules_cover_nine_principles(self):
        for name in presets.NAMES:
            rules = presets.load(name)["rules"]["rules"]
            base = [r for r in rules if r["scope"].startswith("principle:")]
            self.assertEqual(len(base), 9)
        self.assertGreater(len(presets.load("tiger-method")["rules"]["rules"]), 9)

    def test_question_templates_are_bilingual(self):
        for name in presets.NAMES:
            for tpl in presets.load(name)["question_templates"]["templates"]:
                self.assertTrue(tpl["label"]["zh"] and tpl["label"]["en"])
                self.assertTrue(tpl["prompt"]["zh"] and tpl["prompt"]["en"])

    def test_unknown_preset(self):
        with self.assertRaisesRegex(ValueError, "preset"):
            presets.load("nope")

    def test_label_rejects_unknown_language(self):
        with self.assertRaises(ValueError):
            presets.label({"zh": "a", "en": "b"}, "fr")


class InvalidPresetTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        shutil.copytree(os.path.join(presets.PRESETS_DIR, "default"), os.path.join(self.dir, "custom"))

    def _edit(self, filename, change):
        path = os.path.join(self.dir, "custom", filename)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        change(data)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    def assertInvalid(self, field_pattern):
        with self.assertRaisesRegex(ValueError, field_pattern):
            presets.load("custom", self.dir)

    def test_copy_is_valid(self):
        self.assertEqual(presets.load("custom", self.dir)["name"], "custom")

    def test_g_weights_must_sum_to_one(self):
        self._edit("g.json", lambda g: g["groups"]["E"]["items"]["E1"].update(weight=0.3))
        self.assertInvalid(r"g\.json\.groups\.E\.items: weights must sum to 1\.0")

    def test_g_missing_item(self):
        self._edit("g.json", lambda g: g["groups"]["B"]["items"].pop("B4"))
        self.assertInvalid(r"groups\.B\.items")

    def test_pad_needs_27_cells(self):
        self._edit("pad.json", lambda p: p["words"].pop())
        self.assertInvalid(r"pad\.json\.words: must have exactly 27")

    def test_pad_duplicate_cell(self):
        def dup(p):
            p["words"][1] = copy.deepcopy(p["words"][0])
        self._edit("pad.json", dup)
        self.assertInvalid(r"words\[1\]: duplicate")

    def test_pad_missing_en_word(self):
        self._edit("pad.json", lambda p: p["words"][4].pop("en"))
        self.assertInvalid(r"words\[4\]\.en")

    def test_xp_caps_positive(self):
        self._edit("xp.json", lambda x: x["dimensions"][2].update(cap=0))
        self.assertInvalid(r"xp\.json\.dimensions\[2\]\.cap: must be positive")

    def test_xp_no_shipped_events(self):
        self._edit("xp.json", lambda x: x.update(booked_events=[{"event_id": "e1"}]))
        self.assertInvalid(r"booked_events")

    def test_rule_needs_scope(self):
        self._edit("rules.json", lambda r: r["rules"][0].pop("scope"))
        self.assertInvalid(r"rules\[0\]\.scope")

    def test_rule_needs_text(self):
        self._edit("rules.json", lambda r: r["rules"][3].pop("text"))
        self.assertInvalid(r"rules\[3\]\.text")

    def test_template_needs_bilingual_label(self):
        self._edit("question_templates.json", lambda q: q["templates"][0]["label"].pop("zh"))
        self.assertInvalid(r"templates\[0\]\.label\.zh")

    def test_missing_file(self):
        os.remove(os.path.join(self.dir, "custom", "xp.json"))
        self.assertInvalid(r"custom/xp\.json: file is missing")

    def test_bad_json(self):
        with open(os.path.join(self.dir, "custom", "g.json"), "w") as fh:
            fh.write("{")
        self.assertInvalid(r"custom/g\.json: invalid JSON")


if __name__ == "__main__":
    unittest.main()
