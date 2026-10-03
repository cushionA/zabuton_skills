import importlib.util
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_review.py"
SPEC = importlib.util.spec_from_file_location("render_review", SCRIPT)
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class RenderReviewTests(unittest.TestCase):
    def parse(self, markdown):
        document = RENDERER.render_review(markdown)
        parsed = Elements()
        parsed.feed(document)
        return document, parsed

    def test_japanese_headings_have_unique_sections_and_toc(self):
        document, parsed = self.parse("# 登録計画\n## L0 概要\n### 要確認\n未決定\n### 要確認\n証拠なし")
        ids = [attrs["id"] for tag, attrs in parsed.tags if tag == "details"]
        links = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
        self.assertEqual(ids, ["登録計画", "l0-概要", "要確認", "要確認-1"])
        self.assertEqual(links, ["#" + value for value in ids])
        self.assertIn("<title>登録計画</title>", document)
        self.assertIn("未決定", parsed.text)
        self.assertEqual(document.count("<details"), document.count("</details>"))

    def test_l0_table_and_escaped_pipe(self):
        _, parsed = self.parse("### 全体作業マップ\n| 工程 | 主な作業 | 確認事項 |\n|---|---|---|\n| 要件 | 方針を決定 | A \\| B を確認 |")
        self.assertEqual(sum(tag == "th" for tag, _ in parsed.tags), 3)
        self.assertEqual(sum(tag == "td" for tag, _ in parsed.tags), 3)
        self.assertIn("A | B を確認", parsed.text)

    def test_table_pipe_uses_backslash_parity(self):
        self.assertEqual(RENDERER.cells(r"| A \| B | C |"), ["A | B", "C"])
        self.assertEqual(RENDERER.cells(r"| A \\| B | C |"), ["A \\", "B", "C"])
        self.assertEqual(RENDERER.cells(r"| A \\\| B | C |"), ["A \\| B", "C"])

    def test_existing_internal_links_resolve_and_other_links_stay_text(self):
        markdown = "# 計画\n| 工程 | 主な作業 | 確認事項 |\n|---|---|---|\n| [Phase 1](#phase-1) | 確認 | [Task](#task-41) |\n\n[不在](#missing) [外部](https://example.com) [危険](javascript:alert(1))\n# Phase 1\n## Task 4.1\n未確認"
        document, parsed = self.parse(markdown)
        links = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
        self.assertEqual(links.count("#phase-1"), 2)
        self.assertEqual(links.count("#task-41"), 2)
        self.assertNotIn("#missing", links)
        self.assertTrue(all(link.startswith("#") for link in links))
        self.assertIn('[不在](#missing)', document)

    def test_html_and_script_are_inert_in_every_content_context(self):
        markdown = '# <img src=x onerror=alert(1)>\n<script>alert(2)</script>\n\n| 工程 | 主な作業 | 確認事項 |\n|---|---|---|\n| <svg onload=alert(3)> | `<script>` | **<iframe>** |\n\n```html\n<script>alert(4)</script>\n```'
        document, parsed = self.parse(markdown)
        self.assertEqual(sum(tag == "script" for tag, _ in parsed.tags), 1)
        self.assertFalse(any(tag in {"img", "svg", "iframe"} for tag, _ in parsed.tags))
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", document)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", document)
        self.assertFalse(any(name.startswith("on") for _, attrs in parsed.tags for name in attrs))

    def test_fenced_headings_are_not_navigation_and_output_is_deterministic(self):
        markdown = "# 計画\n~~~text\n## 見出しではない\n~~~\n- **判断待ち**\n- `確認`"
        document, parsed = self.parse(markdown)
        self.assertEqual(sum(tag == "details" for tag, _ in parsed.tags), 1)
        self.assertIn("## 見出しではない", parsed.text)
        self.assertEqual(document, RENDERER.render_review(markdown))

    def test_overview_sections_open_and_phase_details_start_collapsed(self):
        markdown = "# 計画\n## 0. 全体概要\n### 0.1 目的\n目的\n### 0.2 全体作業マップ\n作業\n# Phase 1\n## Task 1.1\n手順\n### 補足\n詳細"
        _, parsed = self.parse(markdown)
        opened = {attrs["id"]: "open" in attrs for tag, attrs in parsed.tags if tag == "details"}
        self.assertEqual(opened, {"計画": True, "0-全体概要": True, "01-目的": True, "02-全体作業マップ": True,
                                  "phase-1": False, "task-11": True, "補足": False})

    def test_ordered_steps_keep_original_numbers(self):
        _, parsed = self.parse("# 手順\n3. 先行確認\n5. 結果確認\n- 証拠未添付")
        values = [attrs.get("value") for tag, attrs in parsed.tags if tag == "li" and "value" in attrs]
        self.assertEqual(values, ["3", "5"])
        self.assertIn("先行確認", parsed.text)

    def test_cli_utf8_input_and_self_contained_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "計画.md"
            output = Path(directory) / "確認.html"
            source.write_text("# 日本語計画\n未決定", encoding="utf-8-sig")
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), str(source), str(output)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            document = output.read_text(encoding="utf-8")
            self.assertIn("<title>日本語計画</title>", document)
            self.assertNotIn("window.openai", document)
            self.assertNotIn("https://", document)
            self.assertNotIn("<script src=", document)
            self.assertIn("未決定", source.read_text(encoding="utf-8-sig"))

    def test_cli_rejects_same_path_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plan.md"
            source.write_text("# 保持する計画", encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), str(source), str(source)], capture_output=True, text=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("異なるパス", result.stderr)
            self.assertEqual(source.read_text(encoding="utf-8"), "# 保持する計画")


if __name__ == "__main__":
    unittest.main()
