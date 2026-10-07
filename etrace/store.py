"""Append-only JSONL storage for the preference container.

History is never rewritten: records are only appended, every record needs a
unique 'id' within its file, and whole-file JSON (rules.json) is replaced
atomically via a temp file + os.replace. A lock file in the data dir keeps two
writers apart. A lock older than the stale timeout is reported as StaleLockError
and left in place; removing it is a human decision.
"""

import json
import os
import re
import tempfile
import time

LOCK_NAME = ".etrace.lock"
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class StoreError(Exception):
    """Base error for store problems."""


class InvalidRecord(StoreError):
    """Record is not a dict, has no 'id', or reuses an existing id."""


class CorruptFile(StoreError):
    """A JSONL line cannot be parsed."""


class LockTimeout(StoreError):
    """Another writer holds a fresh lock and did not release it in time."""


class StaleLockError(StoreError):
    """A lock older than the stale timeout exists. It is never removed silently."""


def write_json_atomic(path, obj):
    """Write obj as JSON to path through a temp file in the same dir + os.replace."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    """JSONL files under one data dir. name 'feedback' maps to feedback.jsonl."""

    def __init__(self, data_dir, lock_timeout=10.0, stale_after=300.0):
        self.data_dir = os.path.abspath(data_dir)
        self.lock_timeout = lock_timeout
        self.stale_after = stale_after
        self.lock_path = os.path.join(self.data_dir, LOCK_NAME)

    def path(self, name):
        if name.endswith(".jsonl"):
            name = name[: -len(".jsonl")]
        if not _NAME.match(name):
            raise StoreError(f"invalid store name: {name!r}")
        return os.path.join(self.data_dir, name + ".jsonl")

    def read_all(self, name):
        """Return every record in order; [] if the file does not exist yet."""
        path = self.path(name)
        if not os.path.exists(path):
            return []
        records = []
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise CorruptFile(f"{os.path.basename(path)}:{lineno}: {exc.msg}") from None
        return records

    def append(self, name, record):
        """Append one record. Rejects records without 'id' and duplicate ids."""
        if not isinstance(record, dict):
            raise InvalidRecord("record must be a JSON object")
        rid = record.get("id")
        if not isinstance(rid, str) or not rid.strip():
            raise InvalidRecord("record needs a non-empty string 'id'")
        line = json.dumps(record, ensure_ascii=False)
        path = self.path(name)
        with self.lock():
            if any(r.get("id") == rid for r in self.read_all(name)):
                raise InvalidRecord(f"duplicate id {rid!r} in {os.path.basename(path)}")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return record

    def write_json(self, filename, obj):
        """Atomically replace a whole JSON file (e.g. rules.json) under the lock."""
        with self.lock():
            write_json_atomic(os.path.join(self.data_dir, filename), obj)

    def lock(self):
        return _Lock(self)


class _Lock:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        s = self.store
        os.makedirs(s.data_dir, exist_ok=True)
        deadline = time.monotonic() + s.lock_timeout
        while True:
            try:
                fd = os.open(s.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(s.lock_path)
                except FileNotFoundError:
                    continue  # released between our open and stat; retry
                if age > s.stale_after:
                    raise StaleLockError(
                        f"lock {s.lock_path} is {age:.0f}s old (stale after {s.stale_after:.0f}s); "
                        "check no writer is running, then remove it by hand"
                    ) from None
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"lock {s.lock_path} held by another writer") from None
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(f"pid={os.getpid()} at={time.time():.0f}\n")
            return self

    def __exit__(self, *exc):
        try:
            os.unlink(self.store.lock_path)
        except FileNotFoundError:
            pass
        return False
