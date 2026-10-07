"""PAD trajectory chart as a standalone SVG.

pad_svg(points, kind='day'|'week') draws one consistent chart: a 1240x590
canvas on #F3F5F7, y fixed 0-5 with ticks 1-5, and exactly three smooth solid
lines (P, A, D). There are no dots and no dashed lines.

Each line is a Gaussian-kernel local linear fit of that axis's readings,
evaluated on an even grid between the first and last reading (bandwidth 2.5
hours for a day, 0.75 days for a week) and clamped to 0-5.

x positions: an exact 'HH:MM' reading sits at its clock time (hours before
05:00 belong to the previous evening); a slot-only reading sits at the fixed
pad.SLOT_HOURS position and the caption says so.
"""

import datetime
import math
import xml.etree.ElementTree as ET

from etrace import presets
from etrace.models import pad

WIDTH, HEIGHT = 1240, 590
BACKGROUND = "#F3F5F7"
COLORS = {"P": "#D6336C", "A": "#F08C00", "D": "#1C7ED6"}
LINE_WIDTH = 5
BANDWIDTH = {"day": 2.5, "week": 0.75}  # hours / days
KINDS = tuple(BANDWIDTH)
Y_MIN, Y_MAX = 0.0, 5.0
SAMPLES = 240

# Plot area inside the canvas.
LEFT, RIGHT, TOP, BOTTOM = 80, 1200, 80, 490
DAY_START, DAY_END = 5.0, 29.0  # 05:00 to 05:00 next day
WEEK_DAYS = 7

FONT = "-apple-system, 'PingFang SC', 'Noto Sans CJK SC', 'Helvetica Neue', Arial, sans-serif"
TEXT_COLOR = "#343A40"
MUTED_COLOR = "#868E96"
GRID_COLOR = "#DEE2E6"
SVG_NS = "http://www.w3.org/2000/svg"

CAPTION_SLOTS = {
    "zh": "注：只标了时段的读数画在固定的时段位置，不代表精确的钟点时间。",
    "en": "Note: readings with only a time slot are drawn at fixed slot positions, not exact clock times.",
}
TITLES = {
    "day": {"zh": "PAD 一日轨迹 {start}", "en": "PAD day trajectory {start}"},
    "week": {"zh": "PAD 一周轨迹 {start} – {end}", "en": "PAD week trajectory {start} – {end}"},
}


def _hour(point):
    """Chart hour of a reading (05:00-29:00)."""
    if not point.exact_time:
        return pad.SLOT_HOURS[point.slot]
    hh, mm = point.slot.split(":")
    hour = int(hh) + int(mm) / 60
    return hour + 24 if hour < DAY_START else hour


def local_linear(xs, ys, x0, bandwidth):
    """Gaussian-kernel local linear estimate at x0."""
    s0 = s1 = s2 = t0 = t1 = 0.0
    for x, y in zip(xs, ys):
        d = x - x0
        w = math.exp(-0.5 * (d / bandwidth) ** 2)
        s0 += w
        s1 += w * d
        s2 += w * d * d
        t0 += w * y
        t1 += w * d * y
    if s0 <= 0.0:  # far from every reading: use the nearest one
        return min(zip(xs, ys), key=lambda xy: abs(xy[0] - x0))[1]
    det = s0 * s2 - s1 * s1
    if det <= 1e-12 * s0 * s0 * bandwidth * bandwidth:  # one x value: local mean
        return t0 / s0
    return (s2 * t0 - s1 * t1) / det


def smooth(xs, ys, bandwidth, samples=SAMPLES):
    """[(x, y)] on an even grid over [min xs, max xs], y clamped to 0-5."""
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-9:  # a single position: draw a short flat stroke
        lo, hi = lo - bandwidth / 5, hi + bandwidth / 5
    grid = [lo + (hi - lo) * i / (samples - 1) for i in range(samples)]
    return [(x, min(Y_MAX, max(Y_MIN, local_linear(xs, ys, x, bandwidth)))) for x in grid]


def _sx(x, x_min, x_max):
    return LEFT + (x - x_min) / (x_max - x_min) * (RIGHT - LEFT)


def _sy(y):
    return BOTTOM - (y - Y_MIN) / (Y_MAX - Y_MIN) * (BOTTOM - TOP)


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def _domain(points, kind):
    """(x positions, x_min, x_max, ticks [(x, label)], start date, end date)."""
    dates = sorted({datetime.date.fromisoformat(pt.date) for pt in points})
    start = dates[0]
    if kind == "day":
        if len(dates) > 1:
            raise pad.PadError([f"points: a day chart needs one date, got {len(dates)}"])
        xs = [_hour(pt) for pt in points]
        ticks = [(h, f"{h % 24:02d}:00") for h in range(6, 29, 3)]
        return xs, DAY_START, DAY_END, ticks, start, start
    days = max(WEEK_DAYS, (dates[-1] - start).days + 1)
    xs = [(datetime.date.fromisoformat(pt.date) - start).days + _hour(pt) / 24 for pt in points]
    ticks = [(i + 0.5, (start + datetime.timedelta(days=i)).strftime("%m-%d")) for i in range(days)]
    end = start + datetime.timedelta(days=days - 1)
    return xs, 0.0, float(days), ticks, start, end


def pad_svg(points, kind="day", language="zh", preset=None):
    """The PAD chart for POINTS (PadPoint list) as an SVG string."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    points = list(points)
    if not points:
        raise ValueError("pad_svg needs at least one point")
    if not all(isinstance(pt, pad.PadPoint) for pt in points):
        raise TypeError("points must be PadPoint instances")
    if preset is None:
        preset = presets.load("default")
    lang = language if language in ("zh", "en") else "en"

    xs, x_min, x_max, ticks, start, end = _domain(points, kind)
    xs = [min(x_max, max(x_min, x)) for x in xs]

    svg = ET.Element("svg", {
        "xmlns": SVG_NS, "width": str(WIDTH), "height": str(HEIGHT),
        "viewBox": f"0 0 {WIDTH} {HEIGHT}", "font-family": FONT,
    })
    _sub(svg, "rect", x=0, y=0, width=WIDTH, height=HEIGHT, fill=BACKGROUND)

    title = TITLES[kind][lang].format(start=start.isoformat(), end=end.isoformat())
    _sub(svg, "text", title, x=LEFT, y=44, font_size=24, font_weight="600", fill=TEXT_COLOR)

    # Legend: colored axis names, top right.
    legend_x = RIGHT
    for axis in reversed(pad.AXES):
        name = presets.label(preset["pad"]["axes"][axis]["label"], lang)
        _sub(svg, "text", name, x=legend_x, y=44, font_size=18, font_weight="600",
             fill=COLORS[axis], text_anchor="end")
        legend_x -= 30 + 14 * len(name)

    # Grid and y ticks 1-5 (solid hairlines, not data lines).
    grid = _sub(svg, "g", stroke=GRID_COLOR, stroke_width=1)
    for level in range(0, 6):
        y = round(_sy(level), 2)
        _sub(grid, "line", x1=LEFT, y1=y, x2=RIGHT, y2=y)
        if level:
            _sub(svg, "text", str(level), x=LEFT - 14, y=y + 6, font_size=16,
                 fill=MUTED_COLOR, text_anchor="end")
    for x, label in ticks:
        sx = round(_sx(x, x_min, x_max), 2)
        _sub(svg, "text", label, x=sx, y=BOTTOM + 28, font_size=15,
             fill=MUTED_COLOR, text_anchor="middle")

    # The three lines.
    bandwidth = BANDWIDTH[kind]
    for i, axis in enumerate(pad.AXES):
        curve = smooth(xs, [pt.values[i] for pt in points], bandwidth)
        coords = [f"{_sx(x, x_min, x_max):.2f},{_sy(y):.2f}" for x, y in curve]
        _sub(svg, "path", d="M" + " L".join(coords), fill="none", stroke=COLORS[axis],
             stroke_width=LINE_WIDTH, stroke_linecap="round", stroke_linejoin="round",
             **{"class": "pad-line", "data-axis": axis})

    if any(not pt.exact_time for pt in points):
        _sub(svg, "text", CAPTION_SLOTS[lang], x=LEFT, y=HEIGHT - 30, font_size=15,
             fill=MUTED_COLOR, **{"class": "caption"})

    return ET.tostring(svg, encoding="unicode")
