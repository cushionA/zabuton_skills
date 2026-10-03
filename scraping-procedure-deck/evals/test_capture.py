import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture.py"
spec = importlib.util.spec_from_file_location("capture_under_test", SCRIPT)
capture = importlib.util.module_from_spec(spec)
api = types.ModuleType("playwright.sync_api")
api.Error = RuntimeError
api.sync_playwright = None
with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": api}):
    spec.loader.exec_module(capture)


SCREENS = {
    "https://test/start": {
        "#query": {"rect": [10, 20, 100, 30]},
        "#next": {"rect": [120, 20, 50, 30], "next": "https://test/detail"},
    },
    "https://test/detail": {
        "#price": {"rect": [20, 100, 80, 40]},
        "#new-price": {"rect": [120, 200, 90, 50]},
    },
    "https://test/other": {
        "#summary": {"rect": [30, 60, 110, 20]},
        "#new-summary": {"rect": [80, 90, 140, 40]},
    },
    "https://test/peek-a": {"#value": {"rect": [40, 80, 100, 40]}},
    "https://test/peek-b": {"#value": {"rect": [20, 60, 130, 50]}},
}


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    def wait_for(self, **kwargs):
        assert self.selector in SCREENS[self.page.url], (self.page.url, self.selector)

    def evaluate(self, script, fit):
        return SCREENS[self.page.url][self.selector]["rect"]

    def press_sequentially(self, value, **kwargs):
        self.page.values[self.selector] = value
        self.page.runtime.events.append(("type", self.selector, value))

    def click(self, **kwargs):
        assert self.page.values["#query"] == "abc"
        self.page.runtime.events.append(("click", self.selector))
        self.page.url = SCREENS[self.page.url][self.selector]["next"]


class FakePage:
    def __init__(self, context):
        self.context = context
        self.runtime = context.runtime
        self.url = "about:blank"
        self.values = {}

    def goto(self, url, **kwargs):
        assert url in SCREENS, url
        self.url = url
        self.runtime.events.append(("goto", url))

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, milliseconds):
        pass

    def evaluate(self, script, *args):
        if "scrollHeight" in script:
            return 800
        if "blur" in script or "scrollTo" in script:
            return None
        raise AssertionError(script)

    def screenshot(self, path, **kwargs):
        self.runtime.events.append(("screenshot", Path(path).name, self.url))
        Path(path).write_text(json.dumps({"generation": self.runtime.generation,
                                        "url": self.url, "values": self.values}), encoding="utf-8")

    def close(self):
        self.context.pages.remove(self)


class FakeContext:
    def __init__(self, runtime):
        self.runtime = runtime
        self.pages = []

    def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, runtime):
        self.runtime = runtime

    def new_context(self, **kwargs):
        return FakeContext(self.runtime)

    def close(self):
        pass


class FakePlaywright:
    def __init__(self, generation):
        self.generation = generation
        self.events = []
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def launch(self, **kwargs):
        return FakeBrowser(self)


def scenario():
    return {
        "browser": {"user_agent": "test", "device_scale_factor": 2},
        "shots": [
            {"id": "entry", "goto": "https://test/start", "steps": [
                {"kind": "op", "target": "#query", "action": {"type": "abc"}},
                {"kind": "op", "target": "#next", "action": "click"},
            ]},
            {"id": "detail", "steps": [{"kind": "data", "item": 1, "target": "#price"}]},
            {"id": "other", "goto": "https://test/other", "steps": [
                {"kind": "data", "item": 2, "target": "#summary"}], "peeks": [
                {"goto": "https://test/peek-a", "target": "#value", "text": "A"},
                {"goto": "https://test/peek-b", "target": "#value", "text": "B"},
            ]},
        ],
    }


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def run_capture(self, source, only=None, generation="first"):
        runtime = FakePlaywright(generation)
        with patch.object(capture, "sync_playwright", return_value=runtime):
            self.assertEqual(capture.run(source, self.output, False, only), 0)
        saved = json.loads((self.output / "deck.json").read_text(encoding="utf-8"))
        return saved, runtime.events

    def test_first_capture_types_text_before_screenshot_and_follows_click(self):
        source = scenario()
        saved, events = self.run_capture(source)
        self.assertNotIn("image", source["shots"][0])
        self.assertEqual(saved["shots"][0]["steps"][0]["box"], [20, 40, 200, 60])
        self.assertEqual(saved["shots"][1]["captured_url"], "https://test/detail")
        self.assertEqual(saved["shots"][1]["steps"][0]["box"], [40, 200, 160, 80])
        self.assertEqual(saved["shots"][0]["image_size"], [2560, 1600])
        entry = json.loads((self.output / saved["shots"][0]["image"]).read_text(encoding="utf-8"))
        self.assertEqual(entry["values"], {"#query": "abc"})
        self.assertLess(events.index(("type", "#query", "abc")),
                        events.index(("screenshot", "01_entry.png", "https://test/start")))
        self.assertEqual([peek["captured_url"] for peek in saved["shots"][2]["peeks"]],
                         ["https://test/peek-a", "https://test/peek-b"])

    def test_only_replays_dependencies_and_recaptures_changed_target(self):
        source = scenario()
        old, _ = self.run_capture(source)
        entry_image = (self.output / "01_entry.png").read_bytes()
        old_detail_image = (self.output / "02_detail.png").read_bytes()
        source["shots"][1]["steps"][0]["target"] = "#new-price"
        saved, events = self.run_capture(source, {"detail"}, "second")
        self.assertEqual([event for event in events if event[0] == "screenshot"],
                         [("screenshot", "02_detail.png", "https://test/detail")])
        self.assertIn(("goto", "https://test/start"), events)
        self.assertIn(("click", "#next"), events)
        self.assertEqual(saved["shots"][1]["steps"][0]["target"], "#new-price")
        self.assertEqual(saved["shots"][1]["steps"][0]["box"], [240, 400, 180, 100])
        self.assertNotEqual((self.output / "02_detail.png").read_bytes(), old_detail_image)
        self.assertEqual((self.output / "01_entry.png").read_bytes(), entry_image)
        self.assertEqual(saved["shots"][0], old["shots"][0])
        self.assertEqual(saved["shots"][2], old["shots"][2])

    def test_only_keeps_capture_metadata_for_changed_nonselected_shots(self):
        source = scenario()
        old, _ = self.run_capture(source)
        old_peek_images = [(self.output / pk["image"]).read_bytes() for pk in old["shots"][2]["peeks"]]
        source["shots"][2]["steps"][0]["target"] = "#new-summary"
        source["shots"][2]["peeks"].reverse()
        source["shots"][2]["peeks"][0]["target"] = "#new-value"
        saved, _ = self.run_capture(source, {"detail"}, "second")
        self.assertEqual(saved["shots"][2], old["shots"][2])
        self.assertEqual([(self.output / pk["image"]).read_bytes() for pk in saved["shots"][2]["peeks"]],
                         old_peek_images)

    def test_only_without_previous_capture_still_replays_dependencies(self):
        saved, events = self.run_capture(scenario(), {"detail"})
        self.assertNotIn("image", saved["shots"][0])
        self.assertNotIn("image", saved["shots"][2])
        self.assertEqual([event[1] for event in events if event[0] == "screenshot"], ["02_detail.png"])
        self.assertEqual(saved["shots"][1]["captured_url"], "https://test/detail")

    def test_manual_image_is_preserved(self):
        source = scenario()
        manual = {"id": "manual", "image": "supplied.png", "steps": [
            {"kind": "data", "item": 3, "box": [1, 2, 3, 4]}]}
        source["shots"].insert(0, copy.deepcopy(manual))
        (self.output / "supplied.png").write_bytes(b"supplied")
        saved, events = self.run_capture(source)
        self.assertEqual(saved["shots"][0], manual)
        self.assertEqual((self.output / "supplied.png").read_bytes(), b"supplied")
        self.assertFalse(any(event[0] == "screenshot" and "manual" in event[1] for event in events))

    def test_public_type_shorthand_and_internal_action_are_distinct(self):
        self.assertEqual(capture.norm_action("click"), {"type": "click"})
        self.assertEqual(capture.norm_action({"type": "abc"}), {"type": "type", "value": "abc"})
        self.assertEqual(capture.norm_action({"type": "click"}), {"type": "type", "value": "click"})
        self.assertEqual(capture.norm_action({"type": "type", "value": "abc"}),
                         {"type": "type", "value": "abc"})
        self.assertEqual(capture.norm_action({"select": "price"}), {"type": "select", "value": "price"})

    def test_unknown_only_id_fails_before_opening_browser(self):
        with patch.object(capture, "sync_playwright") as browser:
            with self.assertRaisesRegex(SystemExit, "missing"):
                capture.run(scenario(), self.output, False, {"missing"})
        browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
