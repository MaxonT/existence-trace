import datetime
import os
import tempfile
import unittest

from etrace import journal
from etrace.journal import (
    JournalError,
    JournalFormatError,
    ProtectedBlockChanged,
)

SAMPLE = (
    "# 2026-10-07\n"
    "\n"
    "<!-- trace:ai:headline -->\n"
    "## 今日主线\n"
    "\n"
    "旧的主线  \n"
    "\n"
    "<!-- trace:user:journal -->\n"
    "## 这里更接近真实的我\n"
    "\n"
    "今天早上很累，  \n"
    "下午好一点。\t\n"
    "\n"
    "<!-- trace:user:feedback -->\n"
    "## 给 AI 的反馈\r\n"
    "\r\n"
    "[Q-1] 不是焦虑，是困。   \r\n"
    "<!-- trace:verbatim -->\n"
    "## 原版本\n"
    "\n"
    "今天早上很累，  "
)


class JournalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "2026-10-07.md")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def _read(self):
        return self._read_path(self.path)

    def _read_path(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    # --- parsing ---------------------------------------------------------

    def test_parse_blocks_and_kinds(self):
        doc = journal.parse(SAMPLE)
        self.assertEqual(doc.preamble, "# 2026-10-07\n\n")
        self.assertEqual(
            [b.key for b in doc.blocks],
            ["ai:headline", "user:journal", "user:feedback", "verbatim"],
        )
        self.assertEqual([b.protected for b in doc.blocks], [False, True, True, True])
        self.assertEqual(doc.block("ai:headline").title(), "## 今日主线\n")

    def test_render_round_trip_in_memory(self):
        self.assertEqual(journal.parse(SAMPLE).render(), SAMPLE)

    def test_round_trip_file_is_byte_identical(self):
        self._write(SAMPLE)
        before = self._read()
        journal.save(journal.load(self.path), self.path)
        self.assertEqual(self._read(), before)
        self.assertIn("   \r\n".encode(), before)

    def test_duplicate_marker_rejected(self):
        with self.assertRaises(JournalFormatError):
            journal.parse("<!-- trace:user:journal -->\na\n<!-- trace:user:journal -->\nb\n")

    def test_marker_must_be_whole_line(self):
        doc = journal.parse("<!-- trace:ai:x -->\ntext <!-- trace:user:y --> inline\n")
        self.assertEqual([b.key for b in doc.blocks], ["ai:x"])

    # --- replace_ai_block ------------------------------------------------

    def test_replace_ai_block_keeps_title_and_user_blocks(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        journal.replace_ai_block(doc, "headline", "新的主线：休息")
        journal.save(doc, self.path)
        out = journal.load(self.path)
        self.assertEqual(out.block("ai:headline").body, "## 今日主线\n\n新的主线：休息\n\n")
        for key in ("user:journal", "user:feedback", "verbatim"):
            self.assertEqual(out.block(key).body, journal.parse(SAMPLE).block(key).body)

    def test_replace_ai_block_rejects_user_and_missing_blocks(self):
        doc = journal.parse(SAMPLE)
        with self.assertRaises(JournalError):
            journal.replace_ai_block(doc, "journal", "x")  # user block, not ai
        with self.assertRaises(JournalError):
            journal.replace_ai_block(doc, "nope", "x")

    def test_replace_ai_block_rejects_markers_in_text(self):
        doc = journal.parse(SAMPLE)
        with self.assertRaises(JournalFormatError):
            journal.replace_ai_block(doc, "headline", "x\n<!-- trace:user:journal -->\ny")

    # --- protection ------------------------------------------------------

    def test_save_rejects_user_block_change(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        doc.block("user:journal").body += "AI 加的一句\n"
        with self.assertRaises(ProtectedBlockChanged) as ctx:
            journal.save(doc, self.path)
        self.assertIn("user:journal", str(ctx.exception))
        self.assertEqual(self._read().decode("utf-8"), SAMPLE)

    def test_save_rejects_whitespace_only_change(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        b = doc.block("user:feedback")
        b.body = b.body.replace("   \r\n", "\r\n")
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)

    def test_save_rejects_removed_or_added_protected_block(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        doc.blocks = [b for b in doc.blocks if b.key != "user:feedback"]
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)
        doc = journal.load(self.path)
        doc.blocks.append(journal.Block("user", "extra", "<!-- trace:user:extra -->\n", "x\n"))
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)

    def test_save_rejects_verbatim_change(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        doc.block("verbatim").body += "x"
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)

    def test_save_protects_user_edits_made_after_load(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        edited = SAMPLE.replace("下午好一点。", "下午好多了。")
        self._write(edited)  # the user edits the file while the AI works
        journal.replace_ai_block(doc, "headline", "x")
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)
        self.assertEqual(self._read().decode("utf-8"), edited)

    def test_append_verbatim_first_fill_has_no_header(self):
        path = journal.create_note(self.dir, "2026-10-08")
        empty = journal.load(path).block("verbatim").body
        doc = journal.load(path)
        journal.append_verbatim(doc, "今天的原话  ")
        journal.save(doc, path)
        body = journal.load(path).block("verbatim").body
        self.assertTrue(body.startswith(empty))
        self.assertEqual(body, "## 原版本\n\n今天的原话  \n")
        self.assertNotIn("补写于", body)

    def test_append_verbatim_late_addition_keeps_prefix(self):
        path = journal.create_note(self.dir, "2026-10-08")
        doc = journal.load(path)
        journal.append_verbatim(doc, "原话")
        journal.save(doc, path)
        before = journal.load(path).block("verbatim").body
        doc = journal.load(path)
        journal.append_verbatim(doc, "后来想起的事", "2026-10-10", datetime.date(2026, 10, 8))
        journal.save(doc, path)
        after = journal.load(path).block("verbatim").body
        self.assertTrue(after.startswith(before))
        self.assertEqual(after[len(before):], "\n补写于 2026-10-10｜实际发生于 2026-10-08\n\n后来想起的事\n")
        # several additions in one session, then one save
        doc = journal.load(path)
        journal.append_verbatim(doc, "第二段", "2026-10-11", "2026-10-08")
        journal.append_verbatim(doc, "第三段", "2026-10-11", "2026-10-09")
        journal.save(doc, path)
        final = journal.load(path).block("verbatim").body
        self.assertTrue(final.startswith(after))
        self.assertEqual(final.count("补写于"), 3)

    def test_append_verbatim_english_header(self):
        path = journal.create_note(self.dir, "2026-10-08", language="en")
        doc = journal.load(path)
        journal.append_verbatim(doc, "original")
        journal.append_verbatim(doc, "later", "2026-10-09", "2026-10-08", language="en")
        journal.save(doc, path)
        body = journal.load(path).block("verbatim").body
        self.assertIn("\nAdded on 2026-10-09 | happened on 2026-10-08\n\nlater\n", body)

    def test_append_verbatim_to_body_without_trailing_newline(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        journal.append_verbatim(doc, "补充", "2026-10-08", "2026-10-07")
        journal.save(doc, self.path)
        text = self._read().decode("utf-8")
        self.assertTrue(text.startswith(SAMPLE))
        self.assertTrue(text.endswith("今天早上很累，  \n\n补写于 2026-10-08｜实际发生于 2026-10-07\n\n补充\n"))

    def test_append_verbatim_dedupes_same_text_and_day(self):
        path = journal.create_note(self.dir, "2026-10-08")
        doc = journal.load(path)
        journal.append_verbatim(doc, "原话")
        journal.append_verbatim(doc, "补充", "2026-10-09", "2026-10-08")
        journal.save(doc, path)
        saved = self._read_path(path)
        doc = journal.load(path)
        journal.append_verbatim(doc, "补充\n", "2026-10-12", "2026-10-08")  # same text, same day
        journal.append_verbatim(doc, "原话", "2026-10-12", "2026-10-08")  # already the original
        journal.save(doc, path)
        self.assertEqual(self._read_path(path), saved)
        doc = journal.load(path)
        journal.append_verbatim(doc, "补充", "2026-10-12", "2026-10-07")  # other day: kept
        journal.save(doc, path)
        self.assertEqual(journal.load(path).block("verbatim").body.count("补充"), 2)

    def test_save_rejects_changed_removed_or_reordered_verbatim_bytes(self):
        path = journal.create_note(self.dir, "2026-10-08")
        doc = journal.load(path)
        journal.append_verbatim(doc, "第一句\n第二句")
        journal.save(doc, path)
        saved = self._read_path(path)
        edits = (
            lambda b: b.replace("第一句", "第一局"),  # changed
            lambda b: b.replace("第一句\n", ""),  # removed
            lambda b: b.replace("第一句\n第二句", "第二句\n第一句"),  # reordered
        )
        for edit in edits:
            doc = journal.load(path)
            journal.append_verbatim(doc, "补充", "2026-10-09", "2026-10-08")
            block = doc.block("verbatim")
            block.body = edit(block.body)
            with self.assertRaises(ProtectedBlockChanged):
                journal.save(doc, path)
            self.assertEqual(self._read_path(path), saved)

    def test_append_verbatim_authorizes_only_verbatim(self):
        path = journal.create_note(self.dir, "2026-10-08")
        doc = journal.load(path)
        journal.append_verbatim(doc, "原话")
        doc.block("user:journal").body += "sneaky\n"
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, path)

    def test_append_verbatim_rejects_verbatim_edited_on_disk_after_load(self):
        self._write(SAMPLE)
        doc = journal.load(self.path)
        edited = SAMPLE + "x"
        self._write(edited)
        journal.append_verbatim(doc, "补充", "2026-10-08", "2026-10-07")
        with self.assertRaises(ProtectedBlockChanged):
            journal.save(doc, self.path)
        self.assertEqual(self._read().decode("utf-8"), edited)

    def test_append_verbatim_rejects_bad_input(self):
        doc = journal.parse(SAMPLE)
        with self.assertRaises(JournalFormatError):
            journal.append_verbatim(doc, "  \n", "2026-10-08", "2026-10-07")
        with self.assertRaises(JournalFormatError):
            journal.append_verbatim(doc, "<!-- trace:ai:x -->", "2026-10-08", "2026-10-07")
        with self.assertRaises(JournalError):
            journal.append_verbatim(doc, "late")  # dates required once filled
        with self.assertRaises(JournalError):
            journal.append_verbatim(doc, "late", "2026-13-01", "2026-10-07")
        with self.assertRaises(JournalError):
            journal.append_verbatim(doc, "late", "2026-10-08", "2026-10-07", language="fr")
        self.assertEqual(doc.render(), SAMPLE)

    # --- new note --------------------------------------------------------

    def test_new_note_has_all_blocks_in_design_order(self):
        doc = journal.new_note(datetime.date(2026, 10, 8), "default", "zh")
        self.assertEqual(
            [b.key for b in doc.blocks],
            [
                "ai:headline", "ai:questions", "ai:pad", "user:pad-self", "ai:xp", "ai:g",
                "user:g-self", "user:journal", "user:feedback", "ai:narrative", "verbatim",
            ],
        )
        self.assertTrue(doc.preamble.startswith("# 2026-10-08"))
        self.assertEqual(doc.block("ai:questions").content().strip(), "")

    def _table_rows(self, body):
        return [l for l in body.splitlines() if l.startswith("|")][2:]

    def test_new_note_tables_have_empty_cells(self):
        for lang in ("zh", "en"):
            doc = journal.new_note("2026-10-08", "tiger-method", lang)
            g_rows = self._table_rows(doc.block("user:g-self").body)
            self.assertEqual([r.split("|")[1].strip() for r in g_rows],
                             ["B1", "B2", "B3", "B4", "E1", "E2", "E3", "E4"])
            pad_rows = self._table_rows(doc.block("user:pad-self").body)
            self.assertEqual(len(pad_rows), 4)
            for row in g_rows + pad_rows:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                self.assertTrue(cells[0])
                self.assertEqual(cells[1:], [""] * (len(cells) - 1), row)

    def test_new_note_labels_by_language(self):
        zh = journal.new_note("2026-10-08", language="zh").render()
        en = journal.new_note("2026-10-08", language="en").render()
        self.assertIn("早上", zh)
        self.assertIn("给 AI 的反馈", zh)
        self.assertIn("Morning", en)
        self.assertIn("Feedback for the AI", en)

    def test_new_note_hints_are_strippable(self):
        doc = journal.new_note("2026-10-08")
        self.assertEqual(journal.strip_hints(doc.block("user:feedback").content()).strip(), "")
        self.assertEqual(journal.strip_hints(doc.block("user:journal").content()).strip(), "")

    def test_new_note_rejects_bad_input(self):
        with self.assertRaises(JournalError):
            journal.new_note("2026-13-01")
        with self.assertRaises(JournalError):
            journal.new_note("2026-10-08", preset="nope")
        with self.assertRaises(JournalError):
            journal.new_note("2026-10-08", language="fr")

    def test_create_note_round_trips_and_never_overwrites(self):
        path = journal.create_note(self.dir, "2026-10-08", "default", "en")
        self.assertEqual(os.path.basename(path), "2026-10-08.md")
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        self.assertEqual(journal.parse(text).render(), text)
        with self.assertRaises(JournalError):
            journal.create_note(self.dir, "2026-10-08")


if __name__ == "__main__":
    unittest.main()
