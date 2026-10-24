"""A requested API thinking mode is not evidence that the adapter applied it."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common
import export_deliverables


class ThinkingExportTest(unittest.TestCase):
    def recommendation(self, model, effort, thinking_tokens, call_overrides=None):
        with tempfile.TemporaryDirectory() as temp:
            ws = Path(temp)
            run = ws / "runs" / "one"
            common.write_json(run / "meta.json", {
                "config": {"backend": "api", "model": model, "effort": effort, "thinking": "off"},
            })
            common.write_json(run / "summary.json", {"units": 1})
            common.append_jsonl(run / "calls.jsonl", {
                "backend": "api", "model_requested": model, "effort": effort, "thinking_requested": "off",
                "usage": {"input": 2, "output": 10, "thinking": thinking_tokens},
                **(call_overrides or {}),
            })
            return export_deliverables.recommend(ws, "candidate", [run])["settings"]["thinking"]

    def test_unsupported_api_off_remains_a_request(self):
        for model, effort in (("opus", "low"), ("haiku", "xhigh"), ("sonnet", "max"),
                              ("custom-model", "low")):
            with self.subTest(model=model, effort=effort):
                setting = self.recommendation(model, effort, None)
                self.assertEqual(setting["requested"], "off")
                self.assertEqual(setting["value"], "backend managed")

    def test_observed_thinking_cannot_be_overridden_by_off_config(self):
        setting = self.recommendation("haiku", "low", 5)
        self.assertEqual(setting["requested"], "off")
        self.assertEqual(setting["value"], "unverified")
        self.assertEqual(setting["measured_thinking_tokens_per_call"]["max"], 5)

    def test_supported_request_does_not_invent_missing_usage(self):
        setting = self.recommendation("haiku", "low", None)
        self.assertEqual(setting["value"], "off")
        self.assertIsNone(setting["measured_thinking_tokens_per_call"]["max"])

    def test_per_call_overrides_take_precedence_over_workspace_defaults(self):
        for override, expected in (({"model_requested": "opus"}, "backend managed"),
                                   ({"effort": "max"}, "backend managed"),
                                   ({"backend": "claude"}, "backend managed"),
                                   ({"thinking_requested": "adaptive"}, "unverified"),
                                   ({"model_requested": None}, "unverified")):
            with self.subTest(override=override):
                setting = self.recommendation("haiku", "low", None, override)
                self.assertEqual(setting["requested"], "off")
                self.assertEqual(setting["value"], expected)


if __name__ == "__main__":
    unittest.main()
