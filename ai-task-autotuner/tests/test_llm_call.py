"""llm_call parsing, retries and error classes with a mocked claude CLI."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common  # noqa: E402
import llm_call  # noqa: E402


def envelope(result='{"a": 1}', is_error=False, status=None, thinking=5):
    return json.dumps({
        "type": "result", "is_error": is_error, "api_error_status": status, "result": result,
        "duration_ms": 900, "duration_api_ms": 700, "ttft_ms": 300, "num_turns": 1, "stop_reason": "end_turn",
        "total_cost_usd": 0.0002,
        "usage": {"input_tokens": 2, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 800},
        "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 10, "cacheReadInputTokens": 0,
                                            "cacheCreationInputTokens": 800, "thinkingTokens": thinking}},
    })


def proc(stdout, rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


class LlmCallTest(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch.object(llm_call.shutil, "which", return_value="/bin/claude")
        self.which.start()
        self.sleep = mock.patch.object(llm_call.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.which.stop()
        self.sleep.stop()

    def test_success_parses_usage_and_json(self):
        with mock.patch.object(llm_call.subprocess, "run", return_value=proc(envelope())) as run:
            r = llm_call.call("hi", "sys", model="haiku", effort="low", expect_json=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["json"], {"a": 1})
        self.assertEqual(r["usage"], {"input": 2, "output": 10, "cache_read": 0, "cache_write": 800, "thinking": 5})
        self.assertEqual(r["api_ms"], 700)
        self.assertEqual(r["model_served"], "claude-haiku-5-5")
        args = run.call_args[0][0]
        self.assertIn("--system-prompt-file", args)
        self.assertIn("--effort", args)
        self.assertNotIn("hi", args)  # prompt goes through stdin, never argv
        self.assertEqual(run.call_args[1]["input"], "hi")
        self.assertNotIn("CLAUDECODE", run.call_args[1]["env"])

    def test_retryable_error_then_success_sums_usage(self):
        seq = [proc(envelope("Overloaded", is_error=True, status=529), rc=1), proc(envelope())]
        with mock.patch.object(llm_call.subprocess, "run", side_effect=seq):
            r = llm_call.call("hi", retries=2)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(r["usage"]["cache_write"], 1600)  # failed attempts are still spend
        self.assertAlmostEqual(r["cost_usd"], 0.0004)

    def test_non_retryable_error(self):
        with mock.patch.object(llm_call.subprocess, "run",
                               return_value=proc(envelope("bad model", is_error=True, status=404), rc=1)) as run:
            r = llm_call.call("hi", retries=3)
        self.assertFalse(r["ok"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(r["error_kind"], "api")
        self.assertTrue(llm_call.is_harness_failure(r))

    def test_timeout_is_harness_failure(self):
        with mock.patch.object(llm_call.subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 1)):
            r = llm_call.call("hi", retries=1, timeout=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_kind"], "timeout")
        self.assertEqual(r["attempts"], 2)
        self.assertIsNone(r["usage"])

    def test_parse_failure_is_flagged_not_harness(self):
        with mock.patch.object(llm_call.subprocess, "run", return_value=proc(envelope("no json here"))):
            r = llm_call.call("hi", expect_json=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["error_kind"], "parse")
        self.assertFalse(llm_call.is_harness_failure(r))


class CommonTest(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(common.extract_json('text ```json\n{"a": [1, 2]}\n``` more'), {"a": [1, 2]})
        self.assertEqual(common.extract_json('pre {"a": "}"} post {"b": 2}'), {"a": "}"})
        self.assertIsNone(common.extract_json("nothing"))

    def test_stats(self):
        self.assertAlmostEqual(common.sign_test(8, 0), 2 / 256)
        pi = common.paired_items([{"id": "a", "score": 0}, {"id": "b", "score": 1}],
                                 [{"id": "a", "score": 1}, {"id": "b", "score": 1}])
        self.assertEqual((pi["improved"], pi["regressed"]), (1, 0))
        self.assertAlmostEqual(common.cohen_kappa([("x", "x"), ("y", "y")]), 1.0)

    def test_estimate_cost_haiku_long_context(self):
        short = common.estimate_cost("haiku", {"input": 50_000, "output": 0})
        self.assertAlmostEqual(short, 0.005)
        long = common.estimate_cost("claude-haiku-5-5", {"input": 150_000, "output": 0})
        self.assertAlmostEqual(long, 0.075)

    def test_make_splits_keeps_groups_together(self):
        ids = [f"u{i}" for i in range(20)]
        groups = {i: ("g" + str(int(i[1:]) // 2)) for i in ids}
        sp = common.make_splits(ids, 6, 6, seed=1, groups=groups)
        for a in sp["dev"]:
            for b in sp["holdout"]:
                self.assertNotEqual(groups[a], groups[b])


if __name__ == "__main__":
    unittest.main()
