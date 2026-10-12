import io
import unittest

from etrace import ui

FORBIDDEN = "●○◯◌◍◎⬤⚫⚪•∙◦⊙⏺⭘"  # the owner does not like circle glyphs


class Stream(io.StringIO):
    def __init__(self, tty=False, encoding="utf-8"):
        super().__init__()
        self._tty, self._encoding = tty, encoding

    def isatty(self):
        return self._tty

    @property
    def encoding(self):
        return self._encoding


def styled(**kw):
    kw.setdefault("color", True)
    kw.setdefault("truecolor", True)
    return ui.Style(**kw)


PLAIN = ui.Style(color=False, unicode=True, width=100)
ASCII = ui.Style(color=False, unicode=False, width=100)


class MeasureTest(unittest.TestCase):
    def test_visible_len(self):
        self.assertEqual(ui.visible_len("\x1b[31mab\x1b[0m"), 2)
        self.assertEqual(ui.visible_len("规则"), 4)
        self.assertEqual(ui.visible_len("a规"), 3)
        self.assertEqual(ui.visible_len(""), 0)

    def test_pad_counts_columns_not_characters(self):
        self.assertEqual(ui.visible_len(ui.pad("规则", 6)), 6)
        self.assertEqual(ui.visible_len(ui.pad(styled().paint("ab", ui.PINK), 5)), 5)
        self.assertEqual(ui.pad("toolong", 3), "toolong")

    def test_fit(self):
        self.assertEqual(ui.fit("short", 10), "short")
        cut = ui.fit("规则很长的文字内容", 7)
        self.assertLessEqual(ui.visible_len(cut), 7)
        self.assertTrue(cut.endswith("…"))
        self.assertEqual(ui.visible_len(ui.fit("x" * 50, 12)), 12)


class StyleTest(unittest.TestCase):
    def test_paint_is_identity_without_colour(self):
        self.assertEqual(PLAIN.paint("hi", ui.PINK, bold=True), "hi")
        self.assertEqual(PLAIN.gradient("hello"), "hello")

    def test_paint_with_colour(self):
        text = styled().paint("hi", ui.PINK, bold=True)
        self.assertTrue(text.startswith("\x1b[1;38;2;232;51;109m"))
        self.assertTrue(text.endswith("\x1b[0m"))
        self.assertEqual(ui.strip_ansi(text), "hi")

    def test_256_colour_fallback(self):
        text = styled(truecolor=False).paint("hi", ui.PINK)
        self.assertIn("38;5;", text)
        self.assertNotIn("38;2;", text)

    def test_gradient_endpoints(self):
        self.assertEqual(ui.color_at(ui.BRAND, 0), ui.PINK)
        self.assertEqual(ui.color_at(ui.BRAND, 0.5), ui.AMBER)
        self.assertEqual(ui.color_at(ui.BRAND, 1), ui.BLUE)
        self.assertEqual(ui.color_at(ui.BRAND, 7), ui.BLUE)  # clamped
        text = styled().gradient("abc")
        self.assertEqual(ui.strip_ansi(text), "abc")
        self.assertIn("38;2;232;51;109", text.split("m")[0] + "m")

    def test_icons_are_never_circles(self):
        for name, pair in ui.ICONS.items():
            for glyph in pair:
                self.assertTrue(glyph and not any(c in FORBIDDEN for c in glyph), (name, glyph))
        for glyph in ui.BLOCKS + ui.SPARKS + "".join(ui.LOGO):
            self.assertNotIn(glyph, FORBIDDEN)

    def test_ascii_icons_are_ascii(self):
        for name in ui.ICONS:
            self.assertTrue(ASCII.icon(name).isascii(), name)

    def test_style_for_colour_rules(self):
        tty, pipe = Stream(tty=True), Stream(tty=False)
        self.assertTrue(ui.style_for(tty, {}).color)
        self.assertFalse(ui.style_for(pipe, {}).color)
        self.assertFalse(ui.style_for(tty, {"NO_COLOR": "1"}).color)
        self.assertFalse(ui.style_for(tty, {"TERM": "dumb"}).color)
        self.assertTrue(ui.style_for(pipe, {"FORCE_COLOR": "1"}).color)
        self.assertFalse(ui.style_for(pipe, {"FORCE_COLOR": "0"}).color)
        self.assertTrue(ui.style_for(pipe, {"FORCE_COLOR": "1", "NO_COLOR": "1"}).color)  # explicit wins

    def test_style_for_encoding_and_truecolor(self):
        self.assertTrue(ui.style_for(Stream(encoding="utf-8"), {}).unicode)
        self.assertTrue(ui.style_for(Stream(encoding="UTF8"), {}).unicode)
        self.assertTrue(ui.style_for(Stream(encoding=None), {}).unicode)
        self.assertFalse(ui.style_for(Stream(encoding="ascii"), {}).unicode)
        self.assertFalse(ui.style_for(Stream(encoding="cp1252"), {}).unicode)
        self.assertTrue(ui.style_for(Stream(), {"COLORTERM": "truecolor"}).truecolor)
        self.assertFalse(ui.style_for(Stream(), {}).truecolor)

    def test_width_is_floored(self):
        self.assertEqual(ui.Style(width=5).width, 30)
        self.assertEqual(ui.style_for(Stream(), {}, width=64).width, 64)


class PieceTest(unittest.TestCase):
    def test_bar_width_is_exact(self):
        for style in (PLAIN, styled(), ASCII):
            for value in (-3, 0, 1, 3.3, 5, 9.99, 10, 40):
                text = ui.bar(style, value, 10, width=20)
                self.assertEqual(ui.visible_len(text), 20, (value, style.unicode, style.color))

    def test_bar_fills_proportionally(self):
        self.assertEqual(ui.strip_ansi(ui.bar(PLAIN, 10, 10, 8)), "█" * 8)
        self.assertEqual(ui.strip_ansi(ui.bar(PLAIN, 0, 10, 8)), "░" * 8)
        half = ui.strip_ansi(ui.bar(PLAIN, 5, 10, 8))
        self.assertEqual(half, "████" + "░" * 4)
        self.assertIn("▌", ui.bar(PLAIN, 4.375, 10, 8))  # exactly 3.5 cells: a half block, not rounded up
        self.assertIn("▋", ui.bar(PLAIN, 4.5, 10, 8))    # 3.6 cells: five eighths
        self.assertEqual(ui.bar(ASCII, 5, 10, 8), "####----")
        self.assertEqual(ui.visible_len(ui.bar(PLAIN, 3, 0, 8)), 8)  # vmax 0 is safe

    def test_bar_gradient(self):
        text = ui.bar(styled(), 10, 10, 10, stops=ui.BRAND)
        self.assertEqual(ui.strip_ansi(text), "█" * 10)
        self.assertGreater(text.count("\x1b["), 10)

    def test_sparkline(self):
        text = ui.sparkline(PLAIN, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(text, "▁▂▃▄▅▆▇█")
        self.assertEqual(ui.sparkline(PLAIN, [1, None, 3]), "▁ █")
        self.assertEqual(len(ui.sparkline(PLAIN, [5, 5, 5])), 3)  # flat series is fine
        self.assertEqual(ui.sparkline(PLAIN, [None, None]), "  ")
        self.assertEqual(ui.sparkline(PLAIN, []), "")
        self.assertEqual(ui.sparkline(PLAIN, [0, 10], vmin=0, vmax=20), "▁▅")
        self.assertTrue(ui.sparkline(ASCII, [1, 5, 9]).isascii())

    def test_wave(self):
        for style in (PLAIN, styled(), ASCII):
            self.assertEqual(ui.visible_len(ui.wave(style, 49)), 49)
        self.assertEqual(ui.wave(PLAIN, 30), ui.wave(PLAIN, 30))  # deterministic
        self.assertGreater(len(set(ui.wave(PLAIN, 49))), 4)        # actually varies

    def test_panel_rows_have_equal_width(self):
        lines = ["plain", "规则 with 中文", styled().paint("coloured", ui.PINK, True), ""]
        for style in (PLAIN, styled(), ASCII):
            rows = ui.panel(style, lines, title="Title", width=40).split("\n")
            self.assertEqual(len(rows), len(lines) + 2)
            self.assertEqual({ui.visible_len(r) for r in rows}, {40}, style.unicode)
            self.assertIn("Title", rows[0])

    def test_panel_cuts_long_lines_and_clamps_width(self):
        rows = ui.panel(PLAIN, ["x" * 200], width=50).split("\n")
        self.assertEqual({ui.visible_len(r) for r in rows}, {50})
        narrow = ui.Style(width=40)
        rows = ui.panel(narrow, ["a"], title="T", width=999).split("\n")
        self.assertEqual({ui.visible_len(r) for r in rows}, {40})

    def test_panel_box_characters(self):
        self.assertTrue(ui.panel(PLAIN, ["a"]).startswith("╭"))
        self.assertTrue(ui.panel(ASCII, ["a"]).isascii())

    def test_table(self):
        text = ui.table(PLAIN, [["a", "id-1", "first rule"], ["bb", "id-22", "规则 two"]],
                        headers=("scope", "id", "rule"))
        rows = text.split("\n")
        self.assertEqual(len(rows), 4)  # header, rule line, two rows
        self.assertTrue(rows[0].lstrip().startswith("scope"))
        self.assertEqual(rows[2].index("id-1"), rows[3].index("id-22"))  # aligned
        self.assertIn("规则 two", rows[3])

    def test_table_truncates_the_last_column(self):
        text = ui.table(ui.Style(width=40), [["a", "b", "x" * 100]], headers=("p", "q", "r"))
        self.assertTrue(all(ui.visible_len(r) <= 40 for r in text.split("\n")))
        self.assertIn("…", text)

    def test_kv_aligns_values(self):
        rows = ui.kv(PLAIN, [("a", "1"), ("longer", "2")]).split("\n")
        self.assertEqual(rows[0].index("1"), rows[1].index("2"))


class BannerTest(unittest.TestCase):
    def test_logo_rows_are_equal_width(self):
        self.assertEqual({ui.visible_len(r) for r in ui.LOGO}, {49})
        self.assertEqual(len(ui.LOGO), 6)

    def test_big_banner(self):
        lines = ui.banner(PLAIN, "9.9.9").split("\n")
        self.assertEqual(len(lines), 8)  # six logo rows, the wave, the tagline
        self.assertEqual(lines[:6], list(ui.LOGO))
        self.assertIn("9.9.9", lines[-1])
        self.assertIn(ui.TAGLINE, lines[-1])
        self.assertNotIn("\x1b", "\n".join(lines))

    def test_big_banner_in_colour_keeps_its_shape(self):
        lines = ui.banner(styled(width=100), "1.0").split("\n")
        self.assertIn("\x1b[", lines[0])
        self.assertEqual({ui.visible_len(r) for r in lines[:7]}, {49})

    def test_compact_banner(self):
        narrow = ui.banner(ui.Style(width=40), "1.0")
        self.assertNotIn("\n", narrow)
        self.assertIn("etrace", narrow)
        compact = ui.banner(PLAIN, "1.0", compact=True)
        self.assertNotIn("\n", compact)
        self.assertIn(ui.TAGLINE, compact)

    def test_ascii_banner(self):
        text = ui.banner(ASCII, "1.0")
        self.assertTrue(text.isascii())
        self.assertIn("etrace v1.0", text)

    def test_no_circle_glyphs_in_any_banner(self):
        for style in (PLAIN, styled(), ui.Style(width=40)):
            text = ui.banner(style, "1.0") + ui.header(style, "x", "y")
            self.assertFalse(any(c in text for c in FORBIDDEN))

    def test_header(self):
        text = ui.strip_ansi(ui.header(styled(), "g report", "2026-10-12"))
        self.assertIn("etrace", text)
        self.assertIn("g report", text)
        self.assertTrue(ui.header(ASCII, "x").isascii())


if __name__ == "__main__":
    unittest.main()
