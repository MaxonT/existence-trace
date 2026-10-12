"""Read an existing diary from disk, so `etrace import` can bring it in.

Understands three shapes, mixed freely in one folder:

  * one day per file (.md, .markdown, .txt): the day comes from the file name
    (2026-03-04.md, 20260304.txt, 2026年3月4日.md), the folder path (2026/03/04.md),
    a `date:` line in front matter, or a date heading on the first line;
  * many days in one file: every line that is just a date (`## 2026-03-04`,
    `**2026-03-04**`, `2026-03-04 Wednesday`) starts a new day;
  * a Day One JSON export (the entry's own time zone decides the day).

Nothing here writes anything: scan() returns entries and the files it had to skip.
"""

import datetime
import json
import os
import re
from zoneinfo import ZoneInfo

TEXT_EXT = (".md", ".markdown", ".txt")
MAX_FILES = 5000
MAX_BYTES = 2_000_000
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}

_YEAR = r"(?:19|20)\d{2}"
_DATES = (
    re.compile(rf"(?<!\d)({_YEAR})[-_.](\d{{1,2}})[-_.](\d{{1,2}})(?!\d)"),
    re.compile(rf"(?<!\d)({_YEAR})年\s*(\d{{1,2}})月\s*(\d{{1,2}})日?"),
    re.compile(rf"(?<!\d)({_YEAR})/(\d{{1,2}})/(\d{{1,2}})(?!\d)"),
    re.compile(rf"(?<!\d)({_YEAR})(\d{{2}})(\d{{2}})(?!\d)"),
)
_WEEKDAY = re.compile(
    r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?$|^(?:星期|周|礼拜)[一二三四五六日天]$", re.IGNORECASE)
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONT_DATE = re.compile(r"^(?:date|created|created_at|day)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_DAYONE_ESCAPE = re.compile(r"\\([.\-!#()\[\]{}+*_`>~|])")


def _valid(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d))
    except ValueError:
        return None


def _find(text):
    """(date, (start, end)) of the first real calendar date in TEXT, else (None, None)."""
    for pattern in _DATES:
        for m in pattern.finditer(text):
            day = _valid(*m.groups())
            if day:
                return day, m.span()
    return None, None


def find_date(text):
    return _find(text)[0]


def _date_line(line):
    """The date if LINE is nothing but a date (a heading, bold, or bare, maybe with a weekday)."""
    raw = line.strip()
    if not raw or len(raw) > 60:
        return None
    marked = raw.startswith(("#", "**", "__"))
    bare = raw.strip("#*_> \t")
    day, span = _find(bare)
    if not day:
        return None
    rest = (bare[:span[0]] + " " + bare[span[1]:]).strip(" \t-—–:：,，()（）|[]")
    if not rest or _WEEKDAY.match(rest):
        return day
    return day if marked and len(rest) <= 40 else None


def _front_matter(text):
    """(date or None, text without the front matter)."""
    m = _FRONT.match(text)
    if not m:
        return None, text
    found = _FRONT_DATE.search(m.group(1))
    return (find_date(found.group(1)) if found else None), text[m.end():]


def _split_days(text):
    """[(date, body)] if TEXT holds two or more date lines, else None."""
    lines = text.split("\n")
    marks = [(i, d) for i, d in ((i, _date_line(x)) for i, x in enumerate(lines)) if d]
    if len(marks) < 2:
        return None
    out = []
    for n, (i, day) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        out.append((day, "\n".join(lines[i + 1:end]).strip("\n")))
    return out


def _read(path):
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"bigger than {MAX_BYTES // 1_000_000} MB")
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ValueError("not UTF-8 text") from None


def _dayone(data, tz):
    """[(date, text)] from a parsed Day One export, or None if DATA is not one."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return None
    out = []
    for entry in data["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        try:
            when = datetime.datetime.fromisoformat(str(entry["creationDate"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        zone = tz
        if entry.get("timeZone"):
            try:
                zone = ZoneInfo(entry["timeZone"])
            except (KeyError, ValueError, OSError):
                zone = tz
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        out.append((when.astimezone(zone).date(), _DAYONE_ESCAPE.sub(r"\1", entry["text"]).strip()))
    return out


def _files(paths):
    """[(file, label)]: every file under PATHS in a stable order; hidden folders and junk are left out."""
    out = []
    for path in paths:
        if os.path.isfile(path):
            out.append((path, os.path.basename(path)))
            continue
        for folder, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                if not name.startswith("."):
                    full = os.path.join(folder, name)
                    out.append((full, os.path.relpath(full, path).replace(os.sep, "/")))
    return out


def scan(paths, tz, default_date=None, use_mtime=False):
    """Read PATHS. Returns (entries, skipped).

    entries: [{"date": datetime.date, "text": str, "source": str}], oldest first.
    skipped: [{"path": str, "reason": str}] for everything that was not usable.
    """
    entries, skipped = [], []
    direct = {p for p in paths if os.path.isfile(p)}
    files = _files(paths)
    if len(files) > MAX_FILES:
        skipped.append({"path": "(too many files)", "reason": f"only the first {MAX_FILES} of {len(files)} were read"})
        files = files[:MAX_FILES]

    def skip(label, reason):
        skipped.append({"path": label, "reason": reason})

    for path, label in files:
        ext = os.path.splitext(path)[1].lower()
        if ext not in TEXT_EXT + (".json",):
            if path in direct or ext in (".doc", ".docx", ".pdf", ".rtf", ".html"):
                skip(label, f"{ext or 'no extension'} files are not read (use .md, .txt or a Day One .json)")
            continue
        try:
            if ext == ".json":
                with open(path, "rb") as fh:
                    raw = fh.read(MAX_BYTES * 20 + 1)
                if len(raw) > MAX_BYTES * 20:
                    skip(label, "bigger than 40 MB")
                    continue
                found = _dayone(json.loads(raw.decode("utf-8-sig")), tz)
                if found is None:
                    skip(label, "not a Day One export")
                    continue
                for day, text in found:
                    if text:
                        entries.append({"date": day, "text": text, "source": label})
                    else:
                        skip(f"{label} ({day})", "empty entry")
                continue
            text = _read(path)
        except (OSError, ValueError) as exc:
            skip(label, str(exc) if isinstance(exc, ValueError) else (exc.strerror or "cannot read"))
            continue

        fm_date, body = _front_matter(text)
        days = _split_days(body)
        if days:
            before = body.split("\n")
            for day, chunk in days:
                if chunk.strip():
                    entries.append({"date": day, "text": chunk, "source": label})
                else:
                    skip(f"{label} ({day})", "empty day")
            first = next(i for i, x in enumerate(before) if _date_line(x))
            if "\n".join(before[:first]).strip():
                skip(label, "text above the first date heading was left out")
            continue
        name = os.path.splitext(os.path.basename(label))[0]
        first_line = next((x for x in body.split("\n") if x.strip()), "")
        day = (find_date(name) or fm_date or _date_line(first_line)
               or find_date(os.path.splitext(path)[0].replace(os.sep, "/")) or default_date)
        if day is None and use_mtime:
            day = datetime.datetime.fromtimestamp(os.path.getmtime(path), tz).date()
        if day is None:
            skip(label, "no date in the name, folder, front matter or first line "
                        "(--date DATE for one file, --mtime to use the file's own date)")
            continue
        if not body.strip():
            skip(label, "empty")
            continue
        entries.append({"date": day, "text": body.strip("\n"), "source": label})
    entries.sort(key=lambda e: e["date"])
    return entries, skipped
