import json
import os
import tempfile
import threading
import time
import unittest

from etrace import store
from etrace.store import (
    CorruptFile,
    InvalidRecord,
    LockTimeout,
    StaleLockError,
    Store,
    StoreError,
)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.s = Store(self.dir, lock_timeout=0.3, stale_after=60)

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_and_read_all_in_order(self):
        self.s.append("feedback", {"id": "a", "text": "早上很累 "})
        self.s.append("feedback", {"id": "b", "text": "second"})
        self.assertEqual(
            self.s.read_all("feedback"),
            [{"id": "a", "text": "早上很累 "}, {"id": "b", "text": "second"}],
        )
        with open(os.path.join(self.dir, "feedback.jsonl"), encoding="utf-8") as fh:
            self.assertIn("早上很累", fh.read())

    def test_read_missing_file_is_empty(self):
        self.assertEqual(self.s.read_all("questions"), [])

    def test_jsonl_suffix_accepted(self):
        self.s.append("usage.jsonl", {"id": "u1"})
        self.assertEqual(self.s.read_all("usage"), [{"id": "u1"}])

    def test_rejects_missing_or_empty_id(self):
        for rec in ({}, {"id": ""}, {"id": 3}, {"text": "x"}):
            with self.assertRaises(InvalidRecord):
                self.s.append("feedback", rec)
        with self.assertRaises(InvalidRecord):
            self.s.append("feedback", ["id", "x"])
        self.assertEqual(self.s.read_all("feedback"), [])

    def test_rejects_duplicate_id_same_file_only(self):
        self.s.append("feedback", {"id": "a", "v": 1})
        with self.assertRaises(InvalidRecord):
            self.s.append("feedback", {"id": "a", "v": 2})
        self.s.append("decisions", {"id": "a"})  # other file is fine
        self.assertEqual(self.s.read_all("feedback"), [{"id": "a", "v": 1}])

    def test_bad_names_rejected(self):
        for name in ("../x", "a/b", "", "x.json"):
            with self.assertRaises(StoreError):
                self.s.append(name, {"id": "a"})

    def test_corrupt_line_reported_with_line_number(self):
        with open(os.path.join(self.dir, "xp.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"id": "a"}\n{broken\n')
        with self.assertRaisesRegex(CorruptFile, "xp.jsonl:2"):
            self.s.read_all("xp")

    def test_write_json_atomic_replaces_whole_file(self):
        path = os.path.join(self.dir, "rules.json")
        store.write_json_atomic(path, {"version": 1})
        store.write_json_atomic(path, {"version": 2, "label": "规则"})
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"version": 2, "label": "规则"})
        self.assertEqual(sorted(os.listdir(self.dir)), ["rules.json"])

    def test_write_json_atomic_failure_keeps_old_file(self):
        path = os.path.join(self.dir, "rules.json")
        store.write_json_atomic(path, {"version": 1})
        with self.assertRaises(TypeError):
            store.write_json_atomic(path, {"bad": object()})
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"version": 1})
        self.assertEqual(sorted(os.listdir(self.dir)), ["rules.json"])

    def test_store_write_json_uses_lock(self):
        self.s.write_json("rules.json", {"version": 1})
        self.assertFalse(os.path.exists(self.s.lock_path))
        with open(os.path.join(self.dir, "rules.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], 1)

    def test_lock_released_after_append_and_after_error(self):
        self.s.append("feedback", {"id": "a"})
        self.assertFalse(os.path.exists(self.s.lock_path))
        with self.assertRaises(InvalidRecord):
            self.s.append("feedback", {"id": "a"})
        self.assertFalse(os.path.exists(self.s.lock_path))

    def test_fresh_lock_blocks_writer(self):
        with self.s.lock():
            with self.assertRaises(LockTimeout):
                Store(self.dir, lock_timeout=0.2).append("feedback", {"id": "a"})
        self.assertEqual(self.s.read_all("feedback"), [])

    def test_writer_waits_for_lock_release(self):
        holder = self.s.lock()
        holder.__enter__()
        t = threading.Timer(0.1, holder.__exit__, (None, None, None))
        t.start()
        Store(self.dir, lock_timeout=2).append("feedback", {"id": "a"})
        t.join()
        self.assertEqual(self.s.read_all("feedback"), [{"id": "a"}])

    def test_stale_lock_reported_and_not_removed(self):
        with open(self.s.lock_path, "w") as fh:
            fh.write("pid=1\n")
        old = time.time() - 3600
        os.utime(self.s.lock_path, (old, old))
        with self.assertRaisesRegex(StaleLockError, "stale"):
            self.s.append("feedback", {"id": "a"})
        self.assertTrue(os.path.exists(self.s.lock_path))
        self.assertEqual(self.s.read_all("feedback"), [])

    def test_concurrent_appends_lose_nothing(self):
        s = Store(self.dir, lock_timeout=10)

        def worker(n):
            for i in range(20):
                s.append("usage", {"id": f"{n}-{i}"})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = [r["id"] for r in s.read_all("usage")]
        self.assertEqual(len(ids), 80)
        self.assertEqual(len(set(ids)), 80)


if __name__ == "__main__":
    unittest.main()
