import re
import unittest
import xml.etree.ElementTree as ET

from etrace import chart
from etrace.models.pad import PadError, PadPoint

NS = {"svg": "http://www.w3.org/2000/svg"}


def point(date="2026-10-07", slot="morning", P=3, A=3, D=3):
    return PadPoint(date=date, slot=slot, P=P, A=A, D=D, provenance="self_report")


def day_points(slots=("morning", "noon", "afternoon")):
    return [point(slot=s, P=2 + i, A=4 - i, D=3) for i, s in enumerate(slots)]


def parse(svg):
    return ET.fromstring(svg)


def path_coords(path):
    return [tuple(map(float, xy.split(","))) for xy in re.findall(r"-?[\d.]+,-?[\d.]+", path.get("d"))]


class PadSvgTest(unittest.TestCase):
    def test_canvas_background_and_three_lines(self):
        root = parse(chart.pad_svg(day_points()))
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual((root.get("width"), root.get("height")), ("1240", "590"))
        rect = root.find("svg:rect", NS)
        self.assertEqual(rect.get("fill"), "#F3F5F7")
        paths = root.findall(".//svg:path", NS)
        self.assertEqual(len(paths), 3)
        self.assertEqual([p.get("stroke") for p in paths], ["#D6336C", "#F08C00", "#1C7ED6"])
        for p in paths:
            self.assertEqual(p.get("stroke-width"), "5")
            self.assertEqual(p.get("fill"), "none")

    def test_no_dots_or_dashes(self):
        svg = chart.pad_svg(day_points(), kind="week")
        root = parse(svg)
        self.assertEqual(root.findall(".//svg:circle", NS), [])
        self.assertEqual(root.findall(".//svg:ellipse", NS), [])
        self.assertNotIn("dasharray", svg)
        self.assertNotIn("marker", svg)

    def test_y_ticks_1_to_5_and_fixed_scale(self):
        root = parse(chart.pad_svg(day_points()))
        labels = [t.text for t in root.findall("svg:text", NS)]
        for level in "12345":
            self.assertIn(level, labels)
        self.assertNotIn("0", labels)
        for p in root.findall(".//svg:path", NS):
            for _, y in path_coords(p):
                self.assertGreaterEqual(y, chart._sy(5) - 0.01)
                self.assertLessEqual(y, chart._sy(0) + 0.01)

    def test_slot_only_caption(self):
        root = parse(chart.pad_svg(day_points(), language="en"))
        caption = root.find(".//svg:text[@class='caption']", NS)
        self.assertIn("not exact clock times", caption.text)
        exact = [point(slot="09:00"), point(slot="12:30", P=4), point(slot="16:00", P=2)]
        root = parse(chart.pad_svg(exact))
        self.assertIsNone(root.find(".//svg:text[@class='caption']", NS))
        mixed = exact + [point(slot="evening")]
        self.assertIn("不代表精确的钟点时间", chart.pad_svg(mixed))

    def test_slot_positions_are_fixed(self):
        root = parse(chart.pad_svg(day_points()))
        coords = path_coords(root.find(".//svg:path", NS))
        x_min, x_max = chart.DAY_START, chart.DAY_END
        self.assertAlmostEqual(coords[0][0], chart._sx(9.5, x_min, x_max), places=1)
        self.assertAlmostEqual(coords[-1][0], chart._sx(16.0, x_min, x_max), places=1)

    def test_late_night_time_after_evening(self):
        pts = [point(slot="20:00"), point(slot="01:30", P=1)]
        coords = path_coords(parse(chart.pad_svg(pts)).find(".//svg:path", NS))
        self.assertAlmostEqual(coords[-1][0], chart._sx(25.5, chart.DAY_START, chart.DAY_END), places=1)

    def test_day_chart_rejects_several_dates(self):
        with self.assertRaises(PadError):
            chart.pad_svg([point(), point(date="2026-10-08")])

    def test_week_chart(self):
        pts = [point(date=f"2026-10-0{d}", slot=s, P=d % 5, A=2, D=4)
               for d in range(1, 8) for s in ("morning", "evening")]
        root = parse(chart.pad_svg(pts, kind="week", language="en"))
        self.assertEqual(len(root.findall(".//svg:path", NS)), 3)
        labels = [t.text for t in root.findall("svg:text", NS)]
        self.assertIn("10-01", labels)
        self.assertIn("10-07", labels)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            chart.pad_svg([])
        with self.assertRaises(ValueError):
            chart.pad_svg(day_points(), kind="month")
        with self.assertRaises(TypeError):
            chart.pad_svg([{"P": 3}])

    def test_single_point_draws_visible_lines(self):
        root = parse(chart.pad_svg([point(slot="10:00", P=4)]))
        for p in root.findall(".//svg:path", NS):
            coords = path_coords(p)
            self.assertGreater(coords[-1][0] - coords[0][0], 1)


class SmoothingTest(unittest.TestCase):
    def test_linear_data_is_reproduced(self):
        xs = [6, 9, 12, 15, 18]
        ys = [1 + 0.2 * x for x in xs]
        for x0 in (6, 10.3, 18):
            self.assertAlmostEqual(chart.local_linear(xs, ys, x0, 2.5), 1 + 0.2 * x0, places=9)

    def test_constant_and_single_x(self):
        self.assertAlmostEqual(chart.local_linear([9, 9], [2, 4], 12, 2.5), 3)
        self.assertAlmostEqual(chart.local_linear([9, 12, 15], [3, 3, 3], 10, 2.5), 3)

    def test_bandwidth_controls_smoothness(self):
        xs = [6, 9, 12, 15, 18]
        ys = [1, 5, 1, 5, 1]
        narrow = chart.local_linear(xs, ys, 9, 0.5)
        wide = chart.local_linear(xs, ys, 9, 2.5)
        self.assertGreater(narrow, wide)
        self.assertEqual(chart.BANDWIDTH, {"day": 2.5, "week": 0.75})

    def test_smooth_clamps_to_0_5(self):
        curve = chart.smooth([0, 1, 2], [0, 0, 5], 2.5)
        self.assertTrue(all(0 <= y <= 5 for _, y in curve))
        self.assertEqual(len(curve), chart.SAMPLES)


if __name__ == "__main__":
    unittest.main()
