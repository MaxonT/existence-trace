"""Feedback intake from the user:feedback block of journal notes (feedback.jsonl).

Each note's feedback block is split into entries by blank lines. Template hints
(<!-- hint: ... -->) and the block title are never ingested. Every entry is
stored verbatim, once:

    id              NOTE_DATE:feedback:HASH12   (HASH = sha256 of the entry text)
    text            the entry exactly as written (outer blank lines removed)
    observed_at     when intake first saw it (timezone-aware ISO datetime)
    refers_to_date  the note's date (the day the feedback is about)
    question_id     'Q-...' if the entry starts with [Q-...], else None
    supersedes      id of the earlier version this entry replaces, else None
    version         1 for a first version, previous version + 1 after an edit

Re-running intake adds nothing for unchanged entries. When an entry is edited
its text hash changes: the new text is stored as a new record whose supersedes
points at the version that disappeared from the note. History is never
rewritten; current() returns the latest version of every entry.
"""

import datetime
import difflib
import hashlib
import os
import re

from etrace import journal
from etrace.store import InvalidRecord, Store

NAME = "feedback"
BLOCK = "user:feedback"
_NOTE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_QUESTION = re.compile(r"^\[(Q-[A-Za-z0-9_.:-]+)\]")
_SIMILAR = 0.5  # minimum difflib ratio to treat an unmatched entry as an edit


class FeedbackError(Exception):
    """Invalid intake input."""


class IngestResult:
    """What one intake run did. errors lists (note file name, message)."""

    def __init__(self):
        self.added = []
        self.unchanged = 0
        self.errors = []

    @property
    def ok(self):
        return not self.errors


def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    if now is None:
        return datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise FeedbackError("now must be a timezone-aware datetime")
    return now


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_entries(block_content):
    """Split feedback block content (title already removed) into entry texts."""
    entries, current = [], []
    for line in journal.strip_hints(block_content).splitlines():
        if line.strip():
            current.append(line)
        elif current:
            entries.append("\n".join(current))
            current = []
    if current:
        entries.append("\n".join(current))
    return entries


def question_id(text):
    m = _QUESTION.match(text.lstrip())
    return m.group(1) if m else None


def entry_id(note_date, text):
    return f"{note_date}:feedback:{_hash(text)[:12]}"


def read_all(data_dir):
    return _store(data_dir).read_all(NAME)


def current(data_dir, note_date=None):
    """Latest version of every feedback entry (records no later record supersedes)."""
    records = read_all(data_dir)
    replaced = {r.get("supersedes") for r in records}
    return [
        r for r in records
        if r["id"] not in replaced and (note_date is None or r.get("refers_to_date") == note_date)
    ]


def _pick_previous(text, qid, index, candidates):
    """The vanished earlier entry that TEXT most likely edits, or None."""
    if not candidates:
        return None
    if qid:
        same_q = [c for c in candidates if c.get("question_id") == qid]
        if same_q:
            return same_q[0]
    same_index = [c for c in candidates if c.get("entry_index") == index]
    if same_index:
        return same_index[0]
    best = max(candidates, key=lambda c: difflib.SequenceMatcher(None, c["text"], text).ratio())
    if difflib.SequenceMatcher(None, best["text"], text).ratio() >= _SIMILAR:
        return best
    return None


def ingest_note(data_dir, note_date, entries, now=None, result=None):
    """Store new feedback entries of one note. Returns an IngestResult."""
    store = _store(data_dir)
    result = result or IngestResult()
    observed_at = _now(now).isoformat()
    known = {r["id"] for r in store.read_all(NAME)}
    present = {entry_id(note_date, t) for t in entries}
    vanished = [r for r in current(store, note_date) if r["id"] not in present]
    seen = set()
    for index, text in enumerate(entries):
        rid = entry_id(note_date, text)
        if rid in known or rid in seen:
            result.unchanged += 1
            continue
        seen.add(rid)
        qid = question_id(text)
        previous = _pick_previous(text, qid, index, vanished)
        if previous is not None:
            vanished.remove(previous)
        record = {
            "id": rid,
            "source": "note",
            "block": BLOCK,
            "note_date": note_date,
            "refers_to_date": note_date,
            "observed_at": observed_at,
            "text": text,
            "content_hash": _hash(text),
            "question_id": qid,
            "entry_index": index,
            "supersedes": previous["id"] if previous else None,
            "version": previous.get("version", 1) + 1 if previous else 1,
        }
        try:
            store.append(NAME, record)
        except InvalidRecord:
            result.unchanged += 1  # another intake run stored it first
            continue
        result.added.append(record)
    return result


def ingest_notes(journal_dir, data_dir, now=None):
    """Read the feedback block of every YYYY-MM-DD.md note in journal_dir.

    A note that cannot be read or parsed is reported in result.errors and the
    other notes are still ingested. Notes without a feedback block are skipped.
    """
    if not os.path.isdir(journal_dir):
        raise FeedbackError(f"journal dir does not exist: {journal_dir}")
    now = _now(now)
    result = IngestResult()
    for name in sorted(os.listdir(journal_dir)):
        m = _NOTE_FILE.match(name)
        if not m:
            continue
        try:
            doc = journal.load(os.path.join(journal_dir, name))
            block = doc.block(BLOCK)
        except journal.JournalFormatError as exc:
            result.errors.append((name, str(exc)))
            continue
        except journal.JournalError:
            continue  # no feedback block in this note
        except (OSError, UnicodeDecodeError) as exc:
            result.errors.append((name, str(exc)))
            continue
        ingest_note(data_dir, m.group(1), split_entries(block.content()), now, result)
    return result
