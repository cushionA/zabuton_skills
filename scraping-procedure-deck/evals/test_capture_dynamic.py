import importlib.util
import json
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from time import monotonic

from PIL import Image
from pptx import Presentation

import test_capture_integration as integration

SKILL = Path(__file__).resolve().parents[1]
FIXTURES = SKILL / "evals" / "mock-dynamic"


class DynamicHandler(integration.QuietHandler):
    def do_GET(self):
        if self.path == "/pending":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.flush()
            self.server.closed.wait(20)
            return
        super().do_GET()


@unittest.skipUnless(integration.HAS_PLAYWRIGHT, "Playwright と Chromium が必要です")
class DynamicCaptureTests(unittest.TestCase):
    run_script = integration.CaptureIntegrationTests.run_script
    assert_green_box = integration.CaptureIntegrationTests.assert_green_box

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("capture_dynamic", SKILL / "scripts" / "capture.py")
        cls.capture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.capture)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(DynamicHandler, directory=str(FIXTURES)))
        self.server.closed = threading.Event()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.server.closed.set()
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def capture_shots(self, shots):
        scenario = {"title": "動的画面", "site": "検証", "start_url": self.base_url,
                    "data_layout": [{"no": 1, "name": "値", "type": "文字列"}],
                    "browser": {"viewport": [901, 601], "device_scale_factor": 1.5,
                                "settle_ms": 0, "timeout_ms": 6000}, "shots": shots}
        source = self.work / "scenario.json"
        source.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
        out = self.work / "captures"
        result = self.run_script("capture.py", source, "--out", out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        deck = json.loads((out / "deck.json").read_text(encoding="utf-8"))
        for shot in deck["shots"]:
            for info, box in [(shot, shot["steps"][-1]["box"])] + [
                    (peek, peek["mark_box"]) for peek in shot.get("peeks", [])]:
                with Image.open(out / info["image"]) as image:
                    self.assertEqual(info["image_size"], list(image.size))
                    self.assert_green_box(image, box)
            self.assertEqual(shot["image_size"], [1352, 902])
        built = self.run_script("build_deck.py", source, "--captures", out, "-o", self.work / "deck.pptx")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertGreaterEqual(len(Presentation(self.work / "deck.pptx").slides), 4)
        return deck

    def test_virtual_container_keeps_current_rows_in_shot_and_peek(self):
        url = self.base_url + "/virtual.html"
        scroll = {"target": "#row24", "container": "#results", "max_steps": 15, "wait_ms": 180}
        setup = [{"action": "scroll", "target": "#results"}]
        deck = self.capture_shots([{"id": "virtual", "screen": "一覧", "goto": url, "setup": setup,
                                    "scroll_until": scroll,
                                    "wait_for": {"css": "#row24", "has_text": "商品 24"},
                                    "steps": [{"kind": "data", "item": 1, "target": "#row24", "fit": "box"}],
                                    "peeks": [{"goto": url, "setup": setup, "scroll_until": scroll,
                                               "target": "#row24", "mark": "#row24", "pad": 8.5,
                                               "text": "表示例"}]}])
        self.assertFalse(deck["shots"][0]["page_top"])
        self.assertEqual(deck["shots"][0]["steps"][0]["box"][2:], [272.2, 60.8])

    def test_delayed_append_until_missing_row_appears(self):
        self.capture_shots([{"id": "infinite", "screen": "一覧", "goto": self.base_url + "/infinite.html",
                             "scroll_until": {"target": "#row12", "max_steps": 18, "wait_ms": 250},
                             "wait_for": [{"target": "#loading", "state": "hidden"}, "#row12"],
                             "steps": [{"kind": "data", "item": 1, "target": "#row12", "fit": "box"}]}])

    def test_spa_route_and_input_wait_for_new_data_while_request_is_open(self):
        started = monotonic()
        deck = self.capture_shots([
            {"id": "route", "screen": "詳細", "goto": self.base_url + "/spa.html", "capture_mode": "viewport",
             "setup": [{"target": "#details", "action": {"type": "click", "wait_url": "**/details"}}],
             "wait_url": "**/details",
             "wait_for": [{"target": "#loading", "state": "hidden"},
                          {"css": "#value", "has_text": "詳細の新データ"}],
             "steps": [{"kind": "data", "item": 1, "target": "#value", "fit": "box"}]},
            {"id": "input", "screen": "検索", "goto": self.base_url + "/spa.html", "capture_mode": "viewport",
             "wait_for": [{"target": "#loading", "state": "hidden"},
                          {"css": "#value", "has_text": "検索結果: shoes"}],
             "steps": [{"kind": "op", "text": "検索語を入力", "target": "#query", "action": {"fill": "shoes"}},
                       {"kind": "data", "item": 1, "target": "#value", "fit": "box"}]},
        ])
        self.assertLess(monotonic() - started, 15, "常時接続で10秒のnetworkidle待機を繰り返してはいけない")
        self.assertTrue(deck["shots"][0]["captured_url"].endswith("/details"))

    def test_missing_target_stops_at_step_limit_and_timeout(self):
        from playwright.sync_api import Error, sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(self.base_url + "/virtual.html")
            for config, budget in [({"target": "#absent", "container": "#results", "max_steps": 2, "wait_ms": 30}, 1),
                                   ({"target": "#absent", "max_steps": 1000, "wait_ms": 50, "timeout_ms": 180}, 1)]:
                with self.subTest(config=config):
                    started = monotonic()
                    with self.assertRaisesRegex(Error, "scroll_until"):
                        self.capture.scroll_until(page, config, 3000)
                    self.assertLess(monotonic() - started, budget)
            browser.close()

    def test_viewport_rejects_target_outside_current_screen(self):
        from playwright.sync_api import Error, sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 901, "height": 601})
            page.goto(self.base_url + "/virtual.html")
            rect = self.capture.measure(page, "#row0", 1000, "box")
            with self.assertRaisesRegex(Error, "画面外"):
                self.capture.viewport_clip(page, [rect])
            page.locator("#results").scroll_into_view_if_needed()
            rect = self.capture.measure(page, "#row4", 1000, "box")
            with self.assertRaisesRegex(Error, "切れています"):
                self.capture.viewport_clip(page, [rect], [{"target": "#row4"}])
            browser.close()


if __name__ == "__main__":
    unittest.main()
