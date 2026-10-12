"""Terminal look of the etrace CLI: colour, icons, banner, bars, sparklines, panels.

Standard library only. Everything degrades on purpose:

* colour is on when stdout/stderr is a terminal; NO_COLOR turns it off and
  FORCE_COLOR (not "0") turns it on; truecolor when COLORTERM says so, else
  the 256-colour palette;
* a stream whose encoding is not UTF-8 gets an ASCII look (no box drawing,
  no block characters), so a piped or legacy terminal never shows mojibake;
* widths are measured as the terminal draws them: ANSI codes count 0 and
  East Asian wide characters (Chinese text in rules, notes) count 2.

No circle glyphs anywhere (the owner does not like them): icons are checks,
diamonds, triangles, arrows and squares.
"""

import math
import os
import re
import shutil
import unicodedata

PINK, AMBER, BLUE = (232, 51, 109), (240, 140, 30), (31, 120, 209)  # P, A, D
GREEN, VIOLET = (43, 154, 102), (112, 72, 232)                      # B, E
RED, GRAY, INK = (224, 49, 49), (134, 142, 150), (233, 236, 239)
BRAND = (PINK, AMBER, BLUE)

ICONS = {  # name -> (unicode, ascii)
    "ok": ("✔", "+"), "fail": ("✘", "x"), "warn": ("▲", "!"), "info": ("◆", "*"),
    "arrow": ("❯", ">"), "step": ("✦", "*"), "bullet": ("▪", "-"), "wave": ("∿", "~"),
    "file": ("▤", "#"), "key": ("◇", "o"),
}
BLOCKS = " ▏▎▍▌▋▊▉█"          # eighths, for smooth bars
SPARKS = "▁▂▃▄▅▆▇█"
ASCII_SPARKS = "_.-=+*#@"

# ANSI Shadow lettering for "ETRACE": six rows, 49 columns.
LOGO = (
    "███████╗████████╗██████╗  █████╗  ██████╗███████╗",
    "██╔════╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██╔════╝",
    "█████╗     ██║   ██████╔╝███████║██║     █████╗  ",
    "██╔══╝     ██║   ██╔══██╗██╔══██║██║     ██╔══╝  ",
    "███████╗   ██║   ██║  ██║██║  ██║╚██████╗███████╗",
    "╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝",
)
TAGLINE = "record your traces of existence"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


# --- measuring ------------------------------------------------------------------

def strip_ansi(text):
    return _ANSI.sub("", text)


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_len(text):
    """Columns the terminal uses for TEXT (ANSI codes free, wide characters double)."""
    return sum(char_width(ch) for ch in strip_ansi(text))


def pad(text, width):
    return text + " " * max(0, width - visible_len(text))


def fit(text, width, ellipsis="…"):
    """Plain TEXT cut to at most WIDTH columns (never cuts through a wide character)."""
    if visible_len(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - visible_len(ellipsis):
            break
        out, used = out + ch, used + w
    return out + ellipsis


# --- style ----------------------------------------------------------------------

def _to256(rgb):
    r, g, b = (round(c / 255 * 5) for c in rgb)
    return 16 + 36 * r + 6 * g + b


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def color_at(stops, t):
    """Colour at position T (0..1) along the gradient STOPS."""
    if len(stops) == 1:
        return stops[0]
    t = min(1.0, max(0.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return _lerp(stops[i], stops[i + 1], t - i)


class Style:
    """How to draw: colour on/off, unicode or ascii, truecolor, terminal width."""

    def __init__(self, color=False, unicode=True, width=80, truecolor=False):
        self.color, self.unicode, self.truecolor = color, unicode, truecolor
        self.width = max(30, int(width))

    def icon(self, name):
        return ICONS[name][0 if self.unicode else 1]

    def fg(self, rgb):
        if self.truecolor:
            return "38;2;%d;%d;%d" % rgb
        return "38;5;%d" % _to256(rgb)

    def paint(self, text, rgb=None, bold=False, dim=False):
        if not self.color or not text:
            return text
        codes = (["1"] if bold else []) + (["2"] if dim else []) + ([self.fg(rgb)] if rgb else [])
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text

    def gradient(self, text, stops=BRAND, bold=False):
        """TEXT coloured left to right along STOPS (spaces stay uncoloured)."""
        if not self.color:
            return text
        n = max(1, len(text) - 1)
        return "".join(ch if ch == " " else self.paint(ch, color_at(stops, i / n), bold)
                       for i, ch in enumerate(text))

    def ok(self, text):
        return self.paint(self.icon("ok"), GREEN, True) + " " + text

    def fail(self, text):
        return self.paint(self.icon("fail"), RED, True) + " " + self.paint(text, RED)

    def warn(self, text):
        return self.paint(self.icon("warn"), AMBER, True) + " " + text

    def info(self, text):
        return self.paint(self.icon("info"), BLUE, True) + " " + text

    def off(self, text):
        return self.paint(self.icon("key"), GRAY) + " " + self.muted(text)

    def muted(self, text):
        return self.paint(text, GRAY)

    def head(self, text):
        return self.paint(text, INK, bold=True)


def _env_on(env, name):
    value = env.get(name, "")
    return value != "" and value != "0"


def style_for(stream, env=None, width=None):
    """The Style for STREAM under ENV (default os.environ)."""
    env = os.environ if env is None else env
    isatty = bool(getattr(stream, "isatty", lambda: False)())
    if _env_on(env, "FORCE_COLOR"):
        color = True
    elif env.get("NO_COLOR", "") != "":
        color = False
    else:
        color = isatty and env.get("TERM", "") != "dumb"
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    unicode_ok = not encoding or encoding.startswith("utf")  # unknown encoding: assume UTF-8
    truecolor = env.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    if width is None:
        width = shutil.get_terminal_size((100, 24)).columns if isatty else 100
    return Style(color, unicode_ok, width, truecolor)


# --- pieces ---------------------------------------------------------------------

def bar(style, value, vmax, width=24, rgb=GREEN, stops=None):
    """A smooth horizontal bar of exactly WIDTH columns (value clamped to 0..VMAX)."""
    frac = 0.0 if vmax <= 0 else min(1.0, max(0.0, value / vmax))
    if not style.unicode:
        n = round(frac * width)
        return "#" * n + "-" * (width - n)
    eighths = round(frac * width * 8)
    full, part = divmod(eighths, 8)
    cells = "█" * full + (BLOCKS[part] if part and full < width else "")
    track = "░" * (width - len(cells))
    if stops:
        head = style.gradient(cells, stops) if cells else ""
    else:
        head = style.paint(cells, rgb)
    return head + style.paint(track, GRAY, dim=True)


def sparkline(style, values, vmin=None, vmax=None, rgb=BLUE):
    """One character per value; None is a gap."""
    real = [v for v in values if v is not None]
    if not real:
        return " " * len(values)
    lo = min(real) if vmin is None else vmin
    hi = max(real) if vmax is None else vmax
    chars = SPARKS if style.unicode else ASCII_SPARKS
    out = []
    for v in values:
        if v is None:
            out.append(" ")
            continue
        t = 0.5 if hi <= lo else min(1.0, max(0.0, (v - lo) / (hi - lo)))
        out.append(chars[round(t * (len(chars) - 1))])
    return style.paint("".join(out), rgb)


def wave(style, width, phase=0.0):
    """A deterministic 'trace': layered sines drawn with block characters."""
    chars = SPARKS if style.unicode else ASCII_SPARKS
    vals = []
    for x in range(width):
        v = (0.55 * math.sin(x * 0.32 + phase) + 0.30 * math.sin(x * 0.11 + 1.3 + phase)
             + 0.15 * math.sin(x * 0.9 + 0.4))
        vals.append(min(1.0, max(0.0, 0.5 + 0.62 * v)))
    text = "".join(chars[min(len(chars) - 1, max(0, round(v * (len(chars) - 1))))] for v in vals)
    return style.gradient(text)


def panel(style, lines, title=None, width=None):
    """LINES in a rounded box (ascii box without unicode); every row has the same width."""
    width = min(style.width, width or style.width)
    inner = width - 4
    tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") if style.unicode else ("+", "+", "+", "+", "-", "|")
    rows = []
    for line in lines:
        rows.append(line if visible_len(line) <= inner else fit(strip_ansi(line), inner))
    if title:
        label = f" {title} "
        top = style.paint(tl + h, GRAY) + style.head(label) + style.paint(
            h * max(0, width - 3 - visible_len(label)) + tr, GRAY)
    else:
        top = style.paint(tl + h * (width - 2) + tr, GRAY)
    side = style.paint(v, GRAY)
    body = [f"{side} {pad(r, inner)} {side}" for r in rows]
    bottom = style.paint(bl + h * (width - 2) + br, GRAY)
    return "\n".join([top] + body + [bottom])


def table(style, rows, headers=None, max_width=None):
    """ROWS (lists of plain strings) as aligned columns; the last column takes what is left."""
    max_width = min(style.width, max_width or style.width)
    cols = len(headers) if headers else (len(rows[0]) if rows else 0)
    cells = ([list(headers)] if headers else []) + [list(r) for r in rows]
    widths = [max(visible_len(str(c[i])) for c in cells) for i in range(cols)]
    gap = 2
    fixed = sum(widths[:-1]) + gap * (cols - 1)
    widths[-1] = max(8, min(widths[-1], max_width - fixed - 2))
    out = []
    for n, row in enumerate(cells):
        text = (" " * gap).join(
            pad(fit(str(c), widths[i]), widths[i]) if i < cols - 1 else fit(str(c), widths[i])
            for i, c in enumerate(row))
        if headers and n == 0:
            text = style.muted(text)
        out.append("  " + text.rstrip())
        if headers and n == 0:
            out.append("  " + style.paint(("─" if style.unicode else "-") * min(sum(widths) + gap * (cols - 1), max_width - 4), GRAY, dim=True))
    return "\n".join(out)


def kv(style, pairs, key_width=None):
    """Aligned 'key  value' lines (values may already be painted)."""
    pairs = [(k, v) for k, v in pairs]
    key_width = key_width or max((visible_len(k) for k, _ in pairs), default=0)
    return "\n".join("  " + style.muted(pad(k, key_width)) + "  " + str(v) for k, v in pairs)


def banner(style, version, compact=False):
    """The logo block for `etrace`, `--help` and `init`; one line when COMPACT or the terminal is narrow."""
    ver = style.muted("v" + version)
    if style.unicode and style.width >= 56 and not compact:
        lines = [style.gradient(row, bold=True) for row in LOGO]
        lines.append(wave(style, len(LOGO[0])))
        lines.append(style.head(TAGLINE) + "  " + style.paint(style.icon("bullet"), GRAY) + "  " + ver
                     + "  " + style.paint(style.icon("bullet"), GRAY) + "  " + style.muted("PAD  G  XP"))
        return "\n".join(lines)
    if style.unicode:
        return (style.gradient(style.icon("wave") * 3) + " " + style.head("etrace") + " " + ver + "  "
                + style.muted(TAGLINE))
    return "etrace v" + version + " - " + TAGLINE


def header(style, *parts):
    """The one-line brand header above a command result."""
    sep = " " + style.paint(style.icon("arrow"), GRAY) + " "
    return (style.gradient(style.icon("wave") * 2 if style.unicode else "~~") + " "
            + style.head("etrace") + sep + sep.join(parts))
