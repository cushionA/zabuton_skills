"""Repeated measurements reconsider adoption without self-comparison or stale KEEP."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common
import ledger
import workspace


class LedgerRedecisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": [], "aux": []}, "phase": "quality",
            "primary": {"direction": "max"},
            "guards": [{"metric": "false_accept", "max": 0.1}],
            "loop": {"min_delta": 0.1, "min_efficiency_gain": 0.1},
        })
        for name in ("dataset.py", "score.py"):
            path = self.ws / "eval" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("# synthetic measurement fixture\n", encoding="utf-8")
        common.write_json(self.ws / "eval/splits.json", {"dev": ["a", "b"], "holdout": []})
        for exp in ("E001", "E002", "E003"):
            path = self.ws / "candidates" / exp / "pipeline.py"
            path.parent.mkdir(parents=True)
            path.write_text(f"# frozen candidate {exp}\n", encoding="utf-8")
            common.append_jsonl(self.ws / "ledger.jsonl", {
                "event": "propose", "exp": exp, "candidate": exp,
                "lever": "baseline" if exp == "E001" else "prompt.instructions",
            })
        self.run_count = 0

    def measured(self, exp, primary, *, false_accept=0, gate_failures=0, harness=0):
        self.run_count += 1
        run_id = f"{exp}-r{self.run_count}"
        run = self.ws / "runs" / run_id
        common.write_json(run / "meta.json", {
            "run_id": run_id, "exp": exp, "candidate": exp,
            "candidate_hash": workspace.candidate_hash(self.ws / "candidates" / exp),
            "purpose": "baseline" if exp == "E001" else "experiment", "split": "dev",
            "eval_hash": workspace.eval_hash(self.ws), "data_hash": workspace.data_fingerprint(self.ws),
            "config": {"backend": "claude", "model": "haiku", "effort": "low", "concurrency": 1},
        })
        # Correctness item scores retain max direction for an error-rate primary.
        score = 1 - primary if workspace.charter(self.ws)["primary"]["direction"] == "min" else primary
        common.write_json(run / "score.json", {
            "eval_hash": workspace.eval_hash(self.ws),
            "items": [{"id": uid, "unit": uid, "score": score} for uid in ("a", "b")],
        })
        common.write_json(run / "summary.json", {
            "primary": primary, "cost_usd_per_unit": 1.0, "units": 2,
            "harness_error_rate": harness, "gate_failures": gate_failures,
            "metrics": {"false_accept": false_accept}, "ai_free": False,
        })

    def decide(self, exp, **options):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ledger, "print_status"):
            ledger.decide(self.ws, exp, **options)
        return ledger._state(self.ws)["decisions"][exp]

    def baseline(self, primary=0.5):
        self.measured("E001", primary)
        self.assertEqual(self.decide("E001")["decision"], "baseline")

    def adopt(self, primary=0.9):
        self.baseline()
        self.measured("E002", primary)
        self.assertEqual(self.decide("E002")["decision"], "keep")

    def assert_best(self, exp):
        best = ledger._state(self.ws)["best"]
        self.assertEqual(best["exp"] if best else None, exp)
        return best

    def test_identical_repeat_stays_kept_against_baseline_and_preserves_history(self):
        self.adopt()
        previous = (self.ws / "ledger.jsonl").read_bytes()
        self.measured("E002", 0.9)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(result["best_exp"], "E001")
        self.assertEqual(result["comparison_policy"], "latest_accepted_other_experiment")
        self.assertAlmostEqual(result["delta"], 0.4)
        self.assertEqual(len(result["runs"]), 2)
        self.assert_best("E002")
        self.assertTrue((self.ws / "ledger.jsonl").read_bytes().startswith(previous))

    def test_degraded_repeat_rolls_back_and_next_candidate_uses_restored_incumbent(self):
        self.adopt()
        self.measured("E002", 0)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "discard")
        self.assertAlmostEqual(result["primary"], 0.45)
        self.assertAlmostEqual(result["delta"], -0.05)
        self.assertEqual(result["best_exp"], "E001")
        self.assert_best("E001")
        self.measured("E003", 0.8)
        next_result = self.decide("E003")
        self.assertEqual(next_result["decision"], "keep")
        self.assertEqual(next_result["best_exp"], "E001")
        self.assertAlmostEqual(next_result["delta"], 0.3)

    def test_later_output_gate_failure_revokes_adoption(self):
        self.adopt()
        self.measured("E002", 0.9, gate_failures=1)
        self.assertEqual(self.decide("E002")["decision"], "invalid")
        self.assert_best("E001")

    def test_later_guard_failure_revokes_adoption(self):
        self.adopt()
        self.measured("E002", 0.9, false_accept=0.3)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "discard")
        self.assertIn("guard violated", result["reason"])
        self.assert_best("E001")

    def test_later_harness_failure_revokes_adoption(self):
        self.adopt()
        self.measured("E002", 0.9, harness=0.2)
        self.assertEqual(self.decide("E002")["decision"], "void")
        self.assert_best("E001")

    def test_baseline_repeat_refreshes_baseline_without_self_comparison(self):
        self.baseline()
        self.measured("E001", 0.7)
        result = self.decide("E001")
        self.assertEqual(result["decision"], "baseline")
        self.assertIsNone(result["best_exp"])
        self.assertNotIn("paired", result)
        self.assertAlmostEqual(result["primary"], 0.6)
        self.assert_best("E001")
        self.measured("E001", 0.7, gate_failures=1)
        self.assertEqual(self.decide("E001")["decision"], "invalid")
        self.assert_best(None)

    def test_marginal_rerun_then_confirmation_uses_same_distinct_incumbent(self):
        self.baseline()
        self.measured("E002", 0.65)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "rerun")
        self.assert_best("E001")
        self.measured("E002", 0.65)
        self.assertEqual(self.decide("E002")["decision"], "keep")
        self.measured("E002", 0.65)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(result["best_exp"], "E001")
        self.assertAlmostEqual(result["delta"], 0.15)

    def test_minimizing_primary_repeats_and_rollback_keep_the_correct_direction(self):
        charter = workspace.charter(self.ws)
        charter["primary"]["direction"] = "min"
        common.write_json(self.ws / "charter.json", charter)
        self.adopt(primary=0.1)
        self.measured("E002", 0.99)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "discard")
        self.assertAlmostEqual(result["primary"], 0.545)
        self.assertAlmostEqual(result["delta"], -0.045)
        self.assertEqual(result["best_exp"], "E001")
        self.assert_best("E001")

    def test_redecision_skips_a_previously_kept_but_now_discarded_comparator(self):
        self.baseline(primary=0.1)
        self.measured("E002", 0.5)
        self.assertEqual(self.decide("E002")["decision"], "keep")
        self.measured("E003", 0.9)
        self.assertEqual(self.decide("E003")["decision"], "keep")
        self.assertEqual(self.decide("E002")["decision"], "discard")
        self.assert_best("E003")
        self.measured("E003", 0.9)
        result = self.decide("E003")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(result["best_exp"], "E001")
        self.measured("E003", 0.9, gate_failures=1)
        self.assertEqual(self.decide("E003")["decision"], "invalid")
        self.assert_best("E001")

    def test_legacy_latest_rejections_cannot_restore_historical_keep(self):
        self.baseline()
        for decision in ("discard", "invalid", "void", "rerun"):
            with self.subTest(decision=decision):
                for status in ("keep", decision):
                    common.append_jsonl(self.ws / "ledger.jsonl", {
                        "event": "decide", "exp": "E002", "decision": status,
                        "candidate": "E002", "primary": 0.9,
                    })
                self.assert_best("E001")

    def test_rejected_candidate_can_be_adopted_again_after_new_evidence(self):
        self.adopt()
        self.measured("E002", 0)
        self.assertEqual(self.decide("E002")["decision"], "discard")
        self.measured("E002", 0.99)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(result["best_exp"], "E001")
        self.assertAlmostEqual(result["primary"], 0.63)
        self.assert_best("E002")


if __name__ == "__main__":
    unittest.main()
