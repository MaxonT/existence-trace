"""Optional Notion output adapter: mirror a day's journal note into Notion.

Local Markdown stays the source of truth. This module only reads the note
file and pushes a copy; it never writes the note and never reads Notion page
content back into it. The only things read from Notion are the ids it returns
and the number of top-level blocks on a pushed page (the read-back check).

The token lives in the TOML file at credentials.notion, never in trace.toml;
it is never logged and never written into a record:

    token            integration token (sent as 'Authorization: Bearer ...')
    parent_page_id   the page that holds one child page per day
    api_version      Notion-Version header (default "2022-06-28")
    api_url          API base URL (default "https://api.notion.com/v1")

Pushing (notion.jsonl, id 'NO-YYYYMMDD-N'): a date whose note is unchanged
since its last 'pushed' record is not pushed again. A changed note becomes a
new page; the previous page is archived only after the new one is complete,
so the mirror is never empty. A record says 'pushed' only after the page is
read back with the expected block count; otherwise 'failed' (an API call
failed) or 'unknown' (pushed, but the count did not match), with a reason.

HTTP goes through a transport(method, url, headers, body) -> dict callable;
the default uses urllib. Tests pass a fake one, so nothing touches the network.
Every function takes data_dir as a path or an etrace.store.Store.
"""

import datetime
import hashlib
import json
import re
import tomllib
import urllib.error
import urllib.request

from etrace import journal
from etrace.store import Store

NAME = "notion"
PUSHED, FAILED, UNKNOWN = "pushed", "failed", "unknown"
MAX_TEXT = 2000  # Notion's limit per rich text object
MAX_CHILDREN = 100  # Notion's limit per children array
TIMEOUT = 30

_REQUIRED = ("token", "parent_page_id")
_DEFAULTS = {"api_version": "2022-06-28", "api_url": "https://api.notion.com/v1"}

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")
_DIVIDER = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")


class NotionError(Exception):
    """Bad or missing credentials file, or a failed Notion API call."""


class Credentials:
    """Notion settings from the credentials file. repr never shows the token."""

    def __init__(self, values):
        self.__dict__.update(values)

    def __repr__(self):
        return f"Credentials(parent_page_id={self.parent_page_id!r}, api_version={self.api_version!r})"


class PushResult:
    def __init__(self, record, recorded):
        self.record = record  # the notion.jsonl record (new or existing)
        self.recorded = recorded  # False when the note was already pushed unchanged

    @property
    def status(self):
        return self.record["status"]

    def __repr__(self):
        return f"PushResult(status={self.status!r}, recorded={self.recorded})"


def load_credentials(path):
    """Read and validate the credentials TOML file. Errors never include values."""
    if not path:
        raise NotionError("credentials.notion is not set in trace.toml")
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except OSError as exc:
        raise NotionError(f"cannot read notion credentials file: {exc.strerror}") from None
    except tomllib.TOMLDecodeError:
        raise NotionError("notion credentials file is not valid TOML") from None
    unknown = [k for k in raw if k not in _REQUIRED and k not in _DEFAULTS]
    if unknown:
        raise NotionError(f"unknown keys in notion credentials file: {', '.join(sorted(unknown))}")
    values = {}
    for key in _REQUIRED + tuple(_DEFAULTS):
        value = raw.get(key, _DEFAULTS.get(key))
        if not isinstance(value, str) or not value:
            raise NotionError(f"notion credentials file needs {key} (non-empty string)")
        values[key] = value
    values["api_url"] = values["api_url"].rstrip("/")
    return Credentials(values)


# --- Markdown -> Notion blocks ----------------------------------------------------

def rich_text(text):
    """Plain text as Notion rich text objects of at most MAX_TEXT characters."""
    return [{"type": "text", "text": {"content": text[i:i + MAX_TEXT]}}
            for i in range(0, len(text), MAX_TEXT)]


def _block(kind, text, **extra):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text), **extra}}


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _table(lines):
    rows = [_cells(line) for line in lines]
    header = len(rows) > 1 and all(_SEPARATOR_CELL.match(c) for c in rows[1] if c) and any(rows[1])
    if header:
        rows = rows[:1] + rows[2:]
    width = max(len(r) for r in rows)
    children = [{"object": "block", "type": "table_row",
                 "table_row": {"cells": [rich_text(c) for c in r + [""] * (width - len(r))]}}
                for r in rows]
    return {"object": "block", "type": "table",
            "table": {"table_width": width, "has_column_header": header,
                      "has_row_header": False, "children": children}}


def _image(alt, src):
    if src.startswith(("https://", "http://")):
        return {"object": "block", "type": "image",
                "image": {"type": "external", "external": {"url": src}}}
    # Local files (charts) cannot be uploaded through the API: name them instead.
    return _block("paragraph", f"{alt} ({src})" if alt else src)


def to_blocks(text):
    """Notion blocks for a note's Markdown.

    HTML comments (block markers, hints) are dropped. Handles headings,
    bullet/numbered lists, quotes, fenced code, pipe tables, images, dividers
    and paragraphs; inline Markdown stays as plain text.
    """
    lines = _COMMENT.sub("", text).splitlines()
    blocks, para, i = [], [], 0

    def flush():
        if para:
            blocks.append(_block("paragraph", "\n".join(para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(_block("code", "\n".join(code), language="plain text"))
        elif not stripped:
            flush()
        elif stripped.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            blocks.append(_table(rows))
            continue
        elif m := _HEADING.match(stripped):
            flush()
            blocks.append(_block(f"heading_{min(len(m.group(1)), 3)}", m.group(2).strip()))
        elif _DIVIDER.match(stripped):
            flush()
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif m := _IMAGE.match(stripped):
            flush()
            blocks.append(_image(m.group(1), m.group(2)))
        elif m := _BULLET.match(line):
            flush()
            blocks.append(_block("bulleted_list_item", m.group(1)))
        elif m := _NUMBERED.match(line):
            flush()
            blocks.append(_block("numbered_list_item", m.group(1)))
        elif m := _QUOTE.match(stripped):
            flush()
            blocks.append(_block("quote", m.group(1)))
        else:
            para.append(stripped)
        i += 1
    flush()
    return blocks


# --- HTTP ------------------------------------------------------------------------

def urllib_transport(method, url, headers, body):
    """Send one JSON request with urllib; the parsed JSON response or NotionError."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read() or b"{}").get("message", "")
        except (ValueError, AttributeError):
            message = ""
        raise NotionError(f"HTTP {exc.code}: {message or exc.reason}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise NotionError(f"network error: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise NotionError("Notion returned a response that is not JSON") from None


class _Client:
    def __init__(self, creds, transport):
        self.creds = creds
        self.transport = transport or urllib_transport

    def call(self, method, path, body=None):
        headers = {
            "Authorization": f"Bearer {self.creds.token}",
            "Notion-Version": self.creds.api_version,
            "Content-Type": "application/json",
        }
        try:
            result = self.transport(method, f"{self.creds.api_url}{path}", headers, body)
        except NotionError as exc:
            raise NotionError(_scrub(str(exc), self.creds)) from None
        if not isinstance(result, dict):
            raise NotionError(f"{method} {path}: response is not a JSON object")
        return result

    def count_children(self, page_id):
        count, cursor = 0, None
        while True:
            query = f"?page_size={MAX_CHILDREN}" + (f"&start_cursor={cursor}" if cursor else "")
            page = self.call("GET", f"/blocks/{page_id}/children{query}")
            count += len(page.get("results") or [])
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return count


def _scrub(text, creds):
    return text.replace(creds.token, "***") if creds.token else text


# --- push ------------------------------------------------------------------------

def _store(data_dir):
    return data_dir if isinstance(data_dir, Store) else Store(data_dir)


def _now(now):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        raise NotionError("now must be timezone-aware")
    return now


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def records(data_dir, date=None):
    """notion.jsonl records, oldest first, optionally for one date."""
    return [r for r in _store(data_dir).read_all(NAME) if date is None or r["date"] == date]


def _last_pushed(recs):
    pushed = [r for r in recs if r["status"] == PUSHED]
    return pushed[-1] if pushed else None


def _title(doc, date):
    first = doc.preamble.strip().splitlines()[0] if doc.preamble.strip() else ""
    return first.lstrip("#").strip() or date


def push(data_dir, creds, journal_dir, date, now=None, transport=None):
    """Mirror journal/DATE.md into a Notion page under creds.parent_page_id."""
    at = _now(now)
    store = _store(data_dir)
    path = journal.note_path(journal_dir, date)
    doc = journal.load(path)
    text = doc.render()
    digest = _hash(text)
    previous = _last_pushed(records(store, date))
    if previous and previous["content_hash"] == digest:
        return PushResult(previous, False)

    blocks = to_blocks(text)
    client = _Client(creds, transport)
    record = {
        "date": date,
        "status": FAILED,
        "page_id": None,
        "url": None,
        "blocks": len(blocks),
        "content_hash": digest,
        "replaces": previous["page_id"] if previous else None,
        "pushed_at": at.isoformat(),
    }
    try:
        page = client.call("POST", "/pages", {
            "parent": {"page_id": creds.parent_page_id},
            "properties": {"title": {"title": rich_text(_title(doc, date))}},
            "children": blocks[:MAX_CHILDREN],
        })
        record["page_id"], record["url"] = page.get("id"), page.get("url")
        if not record["page_id"]:
            raise NotionError("POST /pages: response has no page id")
        for start in range(MAX_CHILDREN, len(blocks), MAX_CHILDREN):
            client.call("PATCH", f"/blocks/{record['page_id']}/children",
                        {"children": blocks[start:start + MAX_CHILDREN]})
        found = client.count_children(record["page_id"])
        if found == len(blocks):
            record["status"] = PUSHED
        else:
            record["status"] = UNKNOWN
            record["reason"] = f"read back {found} blocks, expected {len(blocks)}"
    except NotionError as exc:
        record["reason"] = str(exc)
        if record["page_id"]:
            _archive(client, record["page_id"])  # do not leave a half-written page

    if record["status"] == PUSHED and previous:
        error = _archive(client, previous["page_id"])
        if error:
            record["archive_error"] = error
    return PushResult(_append(store, record, date), True)


def _archive(client, page_id):
    """Archive a page; the error text, or None when it worked."""
    try:
        client.call("PATCH", f"/pages/{page_id}", {"archived": True})
    except NotionError as exc:
        return str(exc)
    return None


def _append(store, record, date):
    stamp = date.replace("-", "")
    with store.lock():
        existing = store.read_all(NAME)
        record = {"id": f"NO-{stamp}-{sum(1 for r in existing if r['date'] == date) + 1}", **record}
        store.append_locked(NAME, record)
    return record
