import copy
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pptx import Presentation

SKILL = Path(__file__).resolve().parents[1]
try:
    importlib.metadata.version("playwright")
    HAS_PLAYWRIGHT = True
except importlib.metadata.PackageNotFoundError:
    HAS_PLAYWRIGHT = False


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_PLAYWRIGHT, "Playwright と Chromium が必要です")
class CaptureIntegrationTests(unittest.TestCase):
    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SKILL / "scripts" / name), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_partial_recapture_and_build_with_real_browser(self):
        site = SKILL / "evals" / "files" / "mock-shop" / "site"
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                source = SKILL / "evals" / "files" / "mock-shop" / "scenario.json"
                base_url = f"http://127.0.0.1:{server.server_port}/"
                scenario = json.loads(source.read_text(encoding="utf-8").replace("http://localhost:8765/", base_url))
                scenario["shots"][0]["steps"][0]["action"] = {"type": "ワイヤレスイヤホン"}
                scenario_path = work / "scenario.json"
                scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
                captures = work / "captures"
                first = self.run_script("capture.py", scenario_path, "--out", captures)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                original = json.loads((captures / "deck.json").read_text(encoding="utf-8"))
                original_images = {
                    shot["id"]: (
                        hashlib.sha256((captures / shot["image"]).read_bytes()).hexdigest(),
                        (captures / shot["image"]).stat().st_mtime_ns,
                    )
                    for shot in original["shots"][:3]
                }
                price_step = next(step for step in scenario["shots"][-1]["steps"] if step.get("item") == 4)
                price_step["target"] = "[class*='Price_price']"
                scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
                result = self.run_script("capture.py", scenario_path, "--out", captures, "--only", "item")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                updated = json.loads((captures / "deck.json").read_text(encoding="utf-8"))
                self.assertEqual(original["shots"][:3], updated["shots"][:3])
                for shot in updated["shots"][:3]:
                    image = captures / shot["image"]
                    self.assertEqual(original_images[shot["id"]], (
                        hashlib.sha256(image.read_bytes()).hexdigest(), image.stat().st_mtime_ns,
                    ))
                old_item, new_item = original["shots"][-1], updated["shots"][-1]
                self.assertNotEqual(old_item["captured_at"], new_item["captured_at"])
                old_price = next(step for step in old_item["steps"] if step.get("item") == 4)
                new_price = next(step for step in new_item["steps"] if step.get("item") == 4)
                self.assertEqual(new_price["target"], price_step["target"])
                self.assertNotEqual(old_price["box"], new_price["box"])
                self.assertIn("item.html?id=1001", new_item["captured_url"])
                output = work / "deck.pptx"
                built = self.run_script("build_deck.py", scenario_path, "--captures", captures, "-o", output)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                self.assertGreaterEqual(len(Presentation(output).slides), 5)
                reordered = copy.deepcopy(scenario)
                reordered["shots"][-1]["peeks"].reverse()
                scenario_path.write_text(json.dumps(reordered, ensure_ascii=False), encoding="utf-8")
                rejected_output = work / "mismatched.pptx"
                rejected = self.run_script("build_deck.py", scenario_path, "--captures", captures, "-o", rejected_output)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse(rejected_output.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
