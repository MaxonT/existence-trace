"""Journal notes: Markdown files split into AI, user and verbatim blocks.

A block starts at a marker line and runs until the next marker (or end of file):

    <!-- trace:ai:NAME -->      AI-owned; replace_ai_block may rewrite it
    <!-- trace:user:NAME -->    the user's own text; protected
    <!-- trace:verbatim -->     the original version; protected

Text before the first marker is the preamble (the note title). A block body that
starts with a Markdown heading keeps that heading as its title: replace_ai_block
rewrites only what follows it.

save() hashes every user and verbatim block of the file on disk and of the new
text, and refuses to write (ProtectedBlockChanged) on any difference. The only
exception is append_verbatim(): the verbatim block is append-only. Its first fill
adds the original text; later additions are new sections that start with a
'补写于 ...｜实际发生于 ...' header. The bytes already on disk must stay an exact
prefix of the new block.

Files are read and written with newline='' so parse -> render is byte-identical.
"""

import datetime
import hashlib
import os
import re

from etrace.store import write_text_atomic

PRESETS = ("default", "tiger-method")
LANGUAGES = ("zh", "en")

_MARKER = re.compile(
    r"^<!-- trace:(?:(?P<kind>ai|user):(?P<name>[a-z0-9][a-z0-9-]*)|(?P<verbatim>verbatim)) -->[ \t]*\r?\n?$"
)
_ANY_MARKER = re.compile(r"<!--\s*trace:", re.IGNORECASE)
_HINT = re.compile(r"<!-- hint:.*?-->[ \t]*(?:\r?\n)?", re.DOTALL)
_HEADING = re.compile(r"^#{1,6} ")
_ADDED_HEADER = {
    "zh": "补写于 {written}｜实际发生于 {happened}",
    "en": "Added on {written} | happened on {happened}",
}
_ADDED_LINE = re.compile(
    r"^(?:补写于 \d{4}-\d{2}-\d{2}｜实际发生于 (?P<zh>\d{4}-\d{2}-\d{2})"
    r"|Added on \d{4}-\d{2}-\d{2} \| happened on (?P<en>\d{4}-\d{2}-\d{2}))[ \t]*\r?$"
)


class JournalError(Exception):
    """Base error for journal problems."""


class JournalFormatError(JournalError):
    """The note cannot be split into blocks (duplicate marker, bad input)."""


class ProtectedBlockChanged(JournalError):
    """A user or verbatim block would change on save."""


class Block:
    """One marker line plus its body, kept as raw text."""

    def __init__(self, kind, name, marker, body):
        self.kind = kind  # 'ai' | 'user' | 'verbatim'
        self.name = name
        self.marker = marker
        self.body = body

    @property
    def key(self):
        return "verbatim" if self.kind == "verbatim" else f"{self.kind}:{self.name}"

    @property
    def protected(self):
        return self.kind in ("user", "verbatim")

    def title(self):
        """The leading heading line of the body ('' if none)."""
        first = _first_line(self.body)
        return first if _HEADING.match(first) else ""

    def content(self):
        """Body without the title line; this is what replace_ai_block rewrites."""
        return self.body[len(self.title()):]


class Document:
    def __init__(self, preamble, blocks):
        self.preamble = preamble
        self.blocks = blocks
        self._verbatim_append = None  # (hash before first append, hash now)

    def block(self, key):
        for b in self.blocks:
            if b.key == key:
                return b
        raise JournalError(f"no block {key!r} in note")

    def render(self):
        return self.preamble + "".join(b.marker + b.body for b in self.blocks)

    def protected_hashes(self):
        return protected_hashes(self)


def _first_line(text):
    end = text.find("\n")
    return text if end < 0 else text[: end + 1]


def parse(text):
    """Split note text into a Document. Inverse of Document.render()."""
    if not isinstance(text, str):
        raise JournalFormatError("note text must be str")
    preamble = []
    blocks = []
    seen = set()
    for line in text.splitlines(keepends=True):
        m = _MARKER.match(line)
        if m:
            kind = "verbatim" if m.group("verbatim") else m.group("kind")
            block = Block(kind, m.group("name") or "verbatim", line, "")
            if block.key in seen:
                raise JournalFormatError(f"duplicate block marker {block.key!r}")
            seen.add(block.key)
            blocks.append(block)
        elif blocks:
            blocks[-1].body += line
        else:
            preamble.append(line)
    return Document("".join(preamble), blocks)


def load(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return parse(fh.read())


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def protected_hashes(doc):
    """{block key: sha256 of body} for every user and verbatim block."""
    return {b.key: _hash(b.body) for b in doc.blocks if b.protected}


def strip_hints(text):
    """Remove template hint comments (<!-- hint: ... -->) from block text."""
    return _HINT.sub("", text)


def replace_ai_block(doc, name, text):
    """Rewrite the content of ai block NAME, keeping its title line. Returns doc."""
    block = doc.block(f"ai:{name}")
    if _ANY_MARKER.search(text):
        raise JournalFormatError("ai block text must not contain trace markers")
    text = text.strip("\n")
    title = block.title()
    if not title and block.marker and not block.marker.endswith("\n"):
        block.marker += "\n"  # marker was the last line without a newline
    block.body = title + ("\n" + text + "\n\n" if text else "\n")
    return doc


def _verbatim_sections(content):
    """[(happened_on or None, sha256 of text)] for each section of verbatim content.

    The first section (before any '补写于' header) is the original version and
    has happened_on None.
    """
    sections = [[None, []]]
    for line in content.splitlines():
        m = _ADDED_LINE.match(line)
        if m:
            sections.append([m.group("zh") or m.group("en"), []])
        else:
            sections[-1][1].append(line)
    out = []
    for happened, lines in sections:
        text = "\n".join(lines).strip("\n")
        if happened or text.strip():
            out.append((happened, _hash(text)))
    return out


def append_verbatim(doc, text, written_on=None, happened_on=None, language="zh"):
    """Append TEXT to the verbatim block; existing bytes never change. Returns doc.

    An empty block gets TEXT as the original version, without a header. A filled
    block gets a new section headed '补写于 WRITTEN_ON｜实际发生于 HAPPENED_ON'
    (en: 'Added on ... | happened on ...'). Appending text that is already there
    for the same happened_on (or as the original version) is a no-op.
    """
    block = doc.block("verbatim")
    if not isinstance(text, str) or not text.strip():
        raise JournalFormatError("verbatim text must be a non-empty string")
    if _ANY_MARKER.search(text):
        raise JournalFormatError("verbatim text must not contain trace markers")
    if language not in LANGUAGES:
        raise JournalError(f"unknown language {language!r}; expected one of {', '.join(LANGUAGES)}")
    text = text.strip("\n")
    filled = bool(block.content().strip())
    if filled:
        if written_on is None or happened_on is None:
            raise JournalError("written_on and happened_on are required for a late addition")
        happened_on = _iso(happened_on)
        header = _ADDED_HEADER[language].format(written=_iso(written_on), happened=happened_on)
        digest = _hash(text)
        for happened, h in _verbatim_sections(block.content()):
            if h == digest and happened in (None, happened_on):
                return doc
        addition = header + "\n\n" + text + "\n"
    else:
        addition = text + "\n"
    if not block.body and not block.marker.endswith("\n"):
        block.marker += "\n"  # marker was the last line without a newline
    # keep one blank line between the existing text and the addition
    if block.body.endswith("\n\n") or not block.body:
        pass
    elif block.body.endswith("\n"):
        addition = "\n" + addition
    else:
        addition = "\n\n" + addition
    if doc._verbatim_append is None:
        doc._verbatim_append = (_hash(block.body), None)
    block.body += addition
    doc._verbatim_append = (doc._verbatim_append[0], _hash(block.body))
    return doc


def save(doc, path):
    """Write doc to path, refusing any change to user or verbatim blocks.

    'Before' is the file currently on disk (so edits the user made since load()
    are never overwritten). A new file has nothing to protect yet. The file is
    read back after writing and checked again.
    """
    text = doc.render()
    after = protected_hashes(parse(text))
    if os.path.exists(path):
        before = protected_hashes(load(path))
        _check(before, after, doc._verbatim_append)
    write_text_atomic(path, text)
    with open(path, encoding="utf-8", newline="") as fh:
        if fh.read() != text:
            raise JournalError(f"read-back mismatch after writing {path}")
    doc._verbatim_append = None
    return path


def _check(before, after, verbatim_append):
    changed = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        if key == "verbatim" and verbatim_append and (before.get(key), after.get(key)) == verbatim_append:
            continue
        if key not in after:
            changed.append(f"{key} (removed)")
        elif key not in before:
            changed.append(f"{key} (added)")
        else:
            changed.append(key)
    if changed:
        raise ProtectedBlockChanged("protected blocks would change: " + ", ".join(changed))


# --- new note template ------------------------------------------------------

G_ITEMS = ("B1", "B2", "B3", "B4", "E1", "E2", "E3", "E4")
PAD_SLOTS = ("morning", "noon", "afternoon", "other")

_LABELS = {
    "zh": {
        "headline": "今日主线",
        "questions": "AI 想问你",
        "pad": "PAD",
        "pad-self": "我来填写 PAD",
        "xp": "XP",
        "g": "G 估值",
        "g-self": "我的 G 自评",
        "journal": "这里更接近真实的我",
        "feedback": "给 AI 的反馈",
        "narrative": "我今天经历了什么",
        "verbatim": "原版本",
        "slots": {"morning": "早上", "noon": "中午", "afternoon": "下午", "other": "其他"},
        "pad_head": ("时段", "P 愉悦", "A 激活", "D 掌控", "词", "备注"),
        "g_head": ("项目", "分数 0–10", "备注"),
        "hint_pad": "每格 1–5，不确定就留空。可以在「其他」行补充具体时间。",
        "hint_g": "每项 0–10，不想填就留空，留空不会被当成 0。",
        "hint_journal": "随便写。这里的文字 AI 不会改动。",
        "hint_feedback": "写给 AI 的话，空行分隔多条。回答问题时以 [Q-编号] 开头。",
    },
    "en": {
        "headline": "Today's thread",
        "questions": "What the AI wants to ask you",
        "pad": "PAD",
        "pad-self": "My PAD",
        "xp": "XP",
        "g": "G estimate",
        "g-self": "My G self-rating",
        "journal": "Closer to the real me",
        "feedback": "Feedback for the AI",
        "narrative": "What I went through today",
        "verbatim": "Original version",
        "slots": {"morning": "Morning", "noon": "Noon", "afternoon": "Afternoon", "other": "Other"},
        "pad_head": ("Slot", "P pleasure", "A arousal", "D dominance", "Word", "Note"),
        "g_head": ("Item", "Score 0-10", "Note"),
        "hint_pad": "Each cell 1-5; leave it empty if unsure. Use the Other row for exact times.",
        "hint_g": "Each item 0-10; empty is fine and is never read as 0.",
        "hint_journal": "Write anything. The AI never changes text here.",
        "hint_feedback": "Notes for the AI, separate entries with a blank line. Start with [Q-id] to answer a question.",
    },
}

# (kind, name) in the order of docs/SYSTEM_DESIGN.md 4.4
NOTE_BLOCKS = (
    ("ai", "headline"),
    ("ai", "questions"),
    ("ai", "pad"),
    ("user", "pad-self"),
    ("ai", "xp"),
    ("ai", "g"),
    ("user", "g-self"),
    ("user", "journal"),
    ("user", "feedback"),
    ("ai", "narrative"),
    ("verbatim", "verbatim"),
)


def _row(cells):
    return "| " + " | ".join(cells) + " |"


def _table(head, rows):
    lines = [_row(head), _row(["---"] * len(head))]
    lines += [_row([first] + [""] * (len(head) - 1)) for first in rows]
    return "\n".join(lines)


def _iso(date):
    if isinstance(date, datetime.date):
        return date.isoformat()
    try:
        return datetime.date.fromisoformat(date).isoformat()
    except (TypeError, ValueError):
        raise JournalError(f"date must be YYYY-MM-DD, got {date!r}") from None


def note_path(journal_dir, date):
    return os.path.join(journal_dir, _iso(date) + ".md")


def new_note(date, preset="default", language="zh"):
    """Build an empty note for DATE. Every self-report cell starts empty."""
    if preset not in PRESETS:
        raise JournalError(f"unknown preset {preset!r}; expected one of {', '.join(PRESETS)}")
    if language not in LANGUAGES:
        raise JournalError(f"unknown language {language!r}; expected one of {', '.join(LANGUAGES)}")
    L = _LABELS[language]
    bodies = {
        "pad-self": _table(L["pad_head"], [L["slots"][s] for s in PAD_SLOTS])
        + f"\n\n<!-- hint: {L['hint_pad']} -->",
        "g-self": _table(L["g_head"], G_ITEMS) + f"\n\n<!-- hint: {L['hint_g']} -->",
        "journal": f"<!-- hint: {L['hint_journal']} -->",
        "feedback": f"<!-- hint: {L['hint_feedback']} -->",
    }
    parts = [f"# {_iso(date)}\n\n"]
    for kind, name in NOTE_BLOCKS:
        marker = "<!-- trace:verbatim -->" if kind == "verbatim" else f"<!-- trace:{kind}:{name} -->"
        body = bodies.get(name)
        parts.append(f"{marker}\n## {L[name]}\n" + (f"\n{body}\n\n" if body else "\n"))
    return parse("".join(parts))


def create_note(journal_dir, date, preset="default", language="zh"):
    """Write a new note for DATE; never overwrites an existing one. Returns the path."""
    path = note_path(journal_dir, date)
    if os.path.exists(path):
        raise JournalError(f"note already exists: {os.path.basename(path)}")
    return save(new_note(date, preset, language), path)
