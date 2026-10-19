"""Execution policy is rechecked between physical attempts of one logical call."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_call


class AttemptPolicyTest(unittest.TestCase):
    def test_stop_before_first_attempt_has_no_spend_or_provider_dispatch(self):
        def stopped():
            raise RuntimeError("user stopped the run")

        with mock.patch.object(llm_call, "_cli_attempt") as provider:
            result = llm_call.call("task", backend="claude", before_attempt=stopped)
        provider.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(result["policy_refused"])
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["cost_usd"], 0)

    def test_retry_cannot_spend_after_first_attempt_reaches_budget(self):
        observed = []

        def guard():
            if observed:
                raise RuntimeError("observed USD limit reached")
            return 2.5

        transient = {"ok": False, "error_kind": "api", "error": "temporarily unavailable",
                     "retryable": True, "cost_usd": 0.1, "cost_basis": "provider",
                     "usage": {"input": 4, "output": 1, "cache_read": 0, "cache_write": 0, "thinking": None}}
        with mock.patch.object(llm_call, "_cli_attempt", return_value=transient) as provider, \
                mock.patch.object(llm_call.time, "sleep"):
            result = llm_call.call("task", backend="claude", timeout=30, retries=2,
                                   before_attempt=guard, on_attempt=observed.append)
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args[5], 2.5)
        self.assertEqual(len(observed), 1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["cost_usd"], 0.1)
        self.assertEqual(result["usage"]["input"], 4)
        self.assertFalse(result["ok"])
        self.assertTrue(result["policy_refused"])

    def test_expired_remaining_time_prevents_dispatch(self):
        with mock.patch.object(llm_call, "_cli_attempt") as provider:
            result = llm_call.call("task", backend="claude", before_attempt=lambda: 0)
        provider.assert_not_called()
        self.assertTrue(result["policy_refused"])
        self.assertEqual(result["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
