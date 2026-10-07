import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from etrace import cli, journal, prompts
from etrace.adapters import notion
from etrace.config import Config
from etrace.store import Store

NOW = datetime.datetime(2026, 10, 7, 21, 0, tzinfo=datetime.timezone.utc)
DAY = "2026-10-07"
TOKEN = "secret-token-example"
PARENT = "parent-page-example"


class FakeNotion:
    """In-memory Notion API: pages with top-level block counts. No network."""

    def __init__(self, fail_on=None, drop_blocks=0):
        self.calls = []
        self.pages = {}  # page id -> {"children": int, "archived": bool, "title": str}
        self.fail_on = fail_on  # (method, path prefix) that raises NotionError
        self.drop_blocks = drop_blocks

    def __call__(self, method, url, headers, body):
        path = url[len("https://api.notion.com/v1"):]
        self.calls.append((method, path, headers, body))
        if self.fail_on and method == self.fail_on[0] and path.startswith(self.fail_on[1]):
            raise notion.NotionError(f"HTTP 400: rejected (token {TOKEN})")
        if method == "POST" and path == "/pages":
            pid = f"page-{len(self.pages) + 1}"
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            self.pages[pid] = {"children": len(body["children"]), "archived": False, "title": title}
            return {"object": "page", "id": pid, "url": f"https://www.notion.so/{pid}"}
        if method == "PATCH" and path.startswith("/blocks/"):
            pid = path.split("/")[2]
            self.pages[pid]["children"] += len(body["children"])
            return {"object": "list", "results": []}
        if method == "PATCH" and path.startswith("/pages/"):
            self.pages[path.split("/")[2]]["archived"] = body["archived"]
            return {"object": "page"}
        if method == "GET" and path.startswith("/blocks/"):
            pid = path.split("/")[2]
            total = self.pages[pid]["children"] - self.drop_blocks
            start = int(path.split("start_cursor=")[1]) if "start_cursor=" in path else 0
            end = min(start + notion.MAX_CHILDREN, total)
            # Content the adapter must ignore: Notion is never read back into notes.
            results = [{"type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": "edited in Notion"}}]}}] * (end - start)
            return {"results": results, "has_more": end < total,
                    "next_cursor": str(end) if end < total else None}
        raise AssertionError(f"unexpected call {method} {path}")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.journal = os.path.join(self.dir, "journal")
        self.store = Store(os.path.join(self.dir, "data"))
        self.creds_path = os.path.join(self.dir, "notion.toml")
        with open(self.creds_path, "w", encoding="utf-8") as fh:
            fh.write(f'token = "{TOKEN}"\nparent_page_id = "{PARENT}"\n')
        self.creds = notion.load_credentials(self.creds_path)
        self.note = journal.create_note(self.journal, DAY, "default", "en")

    def write_ai(self, name, text):
        doc = journal.load(self.note)
        journal.replace_ai_block(doc, name, text)
        journal.save(doc, self.note)


class CredentialsTest(Base):
    def test_defaults_and_repr_hides_token(self):
        self.assertEqual(self.creds.token, TOKEN)
        self.assertEqual(self.creds.parent_page_id, PARENT)
        self.assertEqual(self.creds.api_version, "2022-06-28")
        self.assertEqual(self.creds.api_url, "https://api.notion.com/v1")
        self.assertNotIn(TOKEN, repr(self.creds))

    def test_errors(self):
        with self.assertRaisesRegex(notion.NotionError, "not set"):
            notion.load_credentials(None)
        with self.assertRaisesRegex(notion.NotionError, "cannot read"):
            notion.load_credentials(os.path.join(self.dir, "missing.toml"))
        cases = {
            'token = "x"\n': "parent_page_id",
            f'token = ""\nparent_page_id = "{PARENT}"\n': "token",
            f'token = "{TOKEN}"\nparent_page_id = "p"\nextra = 1\n': "unknown keys.*extra",
            f'token = "{TOKEN}\n': "not valid TOML",
        }
        for text, pattern in cases.items():
            with self.subTest(pattern=pattern):
                with open(self.creds_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                with self.assertRaisesRegex(notion.NotionError, pattern) as ctx:
                    notion.load_credentials(self.creds_path)
                self.assertNotIn(TOKEN, str(ctx.exception))


class BlocksTest(unittest.TestCase):
    def test_markdown_kinds(self):
        text = (
            "# 2026-10-07\n\n<!-- trace:ai:headline -->\n## Thread\n\n"
            "<!-- hint: never shown -->\nFirst line\nsecond line\n\n"
            "- bullet\n1. numbered\n> quote\n---\n"
            "![PAD](../charts/2026-10-07-pad.svg)\n![web](https://example.com/a.png)\n"
            "```python\ncode here\n```\n"
            "| Slot | P |\n| --- | --- |\n| morning | 3 |\n| noon |\n"
            "#### deep\n"
        )
        blocks = notion.to_blocks(text)
        kinds = [b["type"] for b in blocks]
        self.assertEqual(kinds, ["heading_1", "heading_2", "paragraph", "bulleted_list_item",
                                 "numbered_list_item", "quote", "divider", "paragraph", "image",
                                 "code", "table", "heading_3"])
        plain = json.dumps(blocks, ensure_ascii=False)
        self.assertNotIn("trace:", plain)
        self.assertNotIn("hint", plain)
        self.assertEqual(blocks[2]["paragraph"]["rich_text"][0]["text"]["content"], "First line\nsecond line")
        self.assertEqual(blocks[7]["paragraph"]["rich_text"][0]["text"]["content"],
                         "PAD (../charts/2026-10-07-pad.svg)")
        self.assertEqual(blocks[8]["image"]["external"]["url"], "https://example.com/a.png")
        self.assertEqual(blocks[9]["code"]["rich_text"][0]["text"]["content"], "code here")
        table = blocks[10]["table"]
        self.assertEqual(table["table_width"], 2)
        self.assertTrue(table["has_column_header"])
        rows = [[c[0]["text"]["content"] if c else "" for c in r["table_row"]["cells"]]
                for r in table["children"]]
        self.assertEqual(rows, [["Slot", "P"], ["morning", "3"], ["noon", ""]])

    def test_long_text_is_split(self):
        rich = notion.to_blocks("x" * 4500)[0]["paragraph"]["rich_text"]
        self.assertEqual([len(r["text"]["content"]) for r in rich], [2000, 2000, 500])
        self.assertEqual(notion.rich_text(""), [])

    def test_empty_template_note(self):
        doc = journal.new_note(DAY, "default", "zh")
        blocks = notion.to_blocks(doc.render())
        self.assertEqual(blocks[0]["type"], "heading_1")
        self.assertIn("table", [b["type"] for b in blocks])


class PushTest(Base):
    def test_push_creates_page_and_records(self):
        self.write_ai("headline", "A calm day.")
        fake = FakeNotion()
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertTrue(res.recorded)
        self.assertEqual(res.status, notion.PUSHED)
        rec = res.record
        self.assertEqual(rec["id"], "NO-20261007-1")
        self.assertEqual(rec["page_id"], "page-1")
        self.assertEqual(rec["blocks"], fake.pages["page-1"]["children"])
        self.assertIsNone(rec["replaces"])
        self.assertEqual(fake.pages["page-1"]["title"], DAY)
        method, path, headers, body = fake.calls[0]
        self.assertEqual((method, path), ("POST", "/pages"))
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["Notion-Version"], "2022-06-28")
        self.assertEqual(body["parent"], {"page_id": PARENT})
        self.assertIn("A calm day.", json.dumps(body, ensure_ascii=False))
        stored = json.dumps(notion.records(self.store), ensure_ascii=False)
        self.assertNotIn(TOKEN, stored)
        self.assertNotIn("A calm day.", stored)

    def test_unchanged_note_is_not_pushed_again(self):
        fake = FakeNotion()
        first = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        calls = len(fake.calls)
        again = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertFalse(again.recorded)
        self.assertEqual(again.record["id"], first.record["id"])
        self.assertEqual(len(fake.calls), calls)

    def test_changed_note_replaces_and_archives_old_page(self):
        fake = FakeNotion()
        notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.write_ai("narrative", "Later that evening.")
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertEqual(res.record["id"], "NO-20261007-2")
        self.assertEqual(res.record["page_id"], "page-2")
        self.assertEqual(res.record["replaces"], "page-1")
        self.assertTrue(fake.pages["page-1"]["archived"])
        self.assertFalse(fake.pages["page-2"]["archived"])
        # the new page was complete before the old one was archived
        order = [(m, p) for m, p, _, _ in fake.calls]
        self.assertLess(order.index(("GET", "/blocks/page-2/children?page_size=100")),
                        order.index(("PATCH", "/pages/page-1")))

    def test_more_than_100_blocks_are_appended_in_batches(self):
        self.write_ai("narrative", "\n\n".join(f"Paragraph {i}" for i in range(250)))
        fake = FakeNotion()
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertEqual(res.status, notion.PUSHED)
        appends = [b for m, p, _, b in fake.calls if m == "PATCH" and p.startswith("/blocks/")]
        self.assertTrue(appends)
        self.assertTrue(all(len(b["children"]) <= notion.MAX_CHILDREN for b in appends))
        self.assertLessEqual(len(fake.calls[0][3]["children"]), notion.MAX_CHILDREN)
        self.assertEqual(fake.pages["page-1"]["children"], res.record["blocks"])
        self.assertGreater(res.record["blocks"], 250)

    def test_api_failure_is_recorded_without_token(self):
        fake = FakeNotion(fail_on=("POST", "/pages"))
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertTrue(res.recorded)
        self.assertEqual(res.status, notion.FAILED)
        self.assertIn("HTTP 400", res.record["reason"])
        self.assertNotIn(TOKEN, json.dumps(notion.records(self.store)))
        # a failed push is retried next time
        ok = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=FakeNotion())
        self.assertEqual(ok.status, notion.PUSHED)
        self.assertEqual(ok.record["id"], "NO-20261007-2")

    def test_half_written_page_is_archived(self):
        self.write_ai("narrative", "\n\n".join(f"P{i}" for i in range(150)))
        fake = FakeNotion(fail_on=("PATCH", "/blocks/"))
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertEqual(res.status, notion.FAILED)
        self.assertEqual(res.record["page_id"], "page-1")
        self.assertTrue(fake.pages["page-1"]["archived"])

    def test_readback_mismatch_is_unknown(self):
        fake = FakeNotion(drop_blocks=1)
        res = notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.assertEqual(res.status, notion.UNKNOWN)
        self.assertIn("expected", res.record["reason"])

    def test_notion_is_never_read_back_into_the_note(self):
        self.write_ai("headline", "Local text.")
        with open(self.note, "rb") as fh:
            before = fh.read()
        fake = FakeNotion()
        notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        self.write_ai("headline", "Local text, revised.")
        with open(self.note, "rb") as fh:
            revised = fh.read()
        notion.push(self.store, self.creds, self.journal, DAY, now=NOW, transport=fake)
        with open(self.note, "rb") as fh:
            after = fh.read()
        self.assertNotEqual(before, revised)
        self.assertEqual(revised, after)
        self.assertNotIn(b"edited in Notion", after)

    def test_missing_note(self):
        with self.assertRaises(FileNotFoundError):
            notion.push(self.store, self.creds, self.journal, "2026-10-08", now=NOW, transport=FakeNotion())


class UrllibTransportTest(unittest.TestCase):
    def test_request_shape(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"id": "page-1"}'
        with mock.patch("etrace.adapters.notion.urllib.request.urlopen", return_value=resp) as urlopen:
            out = notion.urllib_transport("POST", "https://api.notion.com/v1/pages",
                                          {"Authorization": "Bearer t"}, {"a": "中"})
        self.assertEqual(out, {"id": "page-1"})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer t")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"a": "中"})

    def test_http_and_network_errors(self):
        err = urllib.error.HTTPError("https://api.notion.com/v1/pages", 401, "Unauthorized", {},
                                     io.BytesIO(b'{"message": "API token is invalid."}'))
        with mock.patch("etrace.adapters.notion.urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(notion.NotionError, "HTTP 401: API token is invalid"):
                notion.urllib_transport("GET", "https://api.notion.com/v1/x", {}, None)
        with mock.patch("etrace.adapters.notion.urllib.request.urlopen",
                        side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(notion.NotionError, "network error: offline"):
                notion.urllib_transport("GET", "https://api.notion.com/v1/x", {}, None)


class CliAndPromptTest(Base):
    def setUp(self):
        super().setUp()
        self.config = os.path.join(self.dir, "trace.toml")
        self.write_config(notion=True)

    def write_config(self, notion):
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write('data_dir = "."\nlanguage = "en"\n')
            if notion:
                fh.write('[credentials]\nnotion = "notion.toml"\n')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", self.config] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_push_ok_then_unchanged(self):
        fake = FakeNotion()
        with mock.patch("etrace.adapters.notion.urllib_transport", fake):
            code, out, _ = self.run_cli("notion", "push", "--date", DAY)
            self.assertEqual(code, 0)
            obj = json.loads(out)
            self.assertEqual((obj["status"], obj["recorded"], obj["page_id"]), ("pushed", True, "page-1"))
            code, out, _ = self.run_cli("notion", "push", "--date", DAY)
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(out)["recorded"])

    def test_push_failure_exits_1(self):
        with mock.patch("etrace.adapters.notion.urllib_transport", FakeNotion(fail_on=("POST", "/pages"))):
            code, out, err = self.run_cli("notion", "push", "--date", DAY)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "failed")
        self.assertNotIn(TOKEN, out + err)

    def test_push_without_credentials_exits_2(self):
        self.write_config(notion=False)
        code, _, err = self.run_cli("notion", "push", "--date", DAY)
        self.assertEqual(code, 2)
        self.assertIn("credentials.notion is not set", err)

    def test_prompts_mention_push_only_when_configured(self):
        plain = Config()
        with_notion = Config(credentials={"email": None, "notion": self.creds_path})
        for lang in prompts.LANGUAGES:
            for task in ("daily", "g"):
                with self.subTest(lang=lang, task=task):
                    self.assertNotIn("notion push", prompts.render(task, plain, self.store, lang))
                    text = prompts.render(task, with_notion, self.store, lang)
                    self.assertIn("`python3 -m etrace notion push --date YYYY-MM-DD`", text)
                    self.assertNotIn(self.creds_path, text)


if __name__ == "__main__":
    unittest.main()
