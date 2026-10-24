"""Baseline adoption obeys the same output, infrastructure and guard constraints."""
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


class BaselineGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": [], "aux": []}, "primary": {"direction": "max"},
            "phase": "quality", "loop": {"min_delta": 0.1},
            "guards": [{"metric": "false_accept", "max": 0.1}],
        })
        for name in ("dataset.py", "score.py"):
            path = self.ws / "eval" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("# synthetic evaluator\n", encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["a"], "holdout": []})
        for candidate in ("base", "candidate"):
            path = self.ws / "candidates" / candidate / "pipeline.py"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {candidate}\n", encoding="utf-8")

    def measured(self, exp="E001", candidate="base", **changes):
        common.append_jsonl(self.ws / "ledger.jsonl", {
            "event": "propose", "exp": exp, "candidate": candidate,
            "lever": "baseline" if exp == "E001" else "prompt.instructions",
        })
        run = self.ws / "runs" / exp
        common.write_json(run / "meta.json", {
            "run_id": exp, "exp": exp, "candidate": candidate,
            "candidate_hash": workspace.candidate_hash(self.ws / "candidates" / candidate),
            "purpose": "baseline" if exp == "E001" else "experiment", "split": "dev",
            "eval_hash": workspace.eval_hash(self.ws), "data_hash": workspace.data_fingerprint(self.ws),
            "config": {"backend": "claude", "model": "haiku", "effort": "low"},
        })
        common.write_json(run / "score.json", {
            "eval_hash": workspace.eval_hash(self.ws), "items": [{"id": "a", "score": 1.0}],
        })
        common.write_json(run / "summary.json", {
            "primary": 1.0, "cost_usd_per_unit": 1.0, "units": 1,
            "gate_failures": 0, "harness_error_rate": 0.0, "metrics": {"false_accept": 0.0},
            **changes,
        })
        return run

    def decide(self, exp="E001", **options):
        with mock.patch.object(ledger, "print_status"), contextlib.redirect_stdout(io.StringIO()):
            ledger.decide(self.ws, exp, **options)
        return ledger._state(self.ws)["decisions"][exp]

    def test_valid_baseline_with_absolute_guard_and_harness_boundary_can_be_adopted(self):
        self.measured(harness_error_rate=0.05, metrics={"false_accept": 0.1})
        self.assertEqual(self.decide()["decision"], "baseline")
        self.assertEqual(ledger._state(self.ws)["best"]["exp"], "E001")

    def test_malformed_baseline_is_invalid_and_never_becomes_best(self):
        self.measured(gate_failures=6)
        result = self.decide()
        self.assertEqual(result["decision"], "invalid")
        self.assertIn("hard-gate failures", result["reason"])
        self.assertIsNone(ledger._state(self.ws)["best"])

    def test_broken_baseline_is_void_before_any_relative_harness_threshold_exists(self):
        self.measured(harness_error_rate=0.1)
        result = self.decide()
        self.assertEqual(result["decision"], "void")
        self.assertIn("10.00% > 5.00%", result["reason"])
        self.assertIsNone(ledger._state(self.ws)["best"])

    def test_baseline_guard_violation_or_missing_guard_metric_is_discarded(self):
        run = self.measured(metrics={"false_accept": 0.2})
        for metrics in ({"false_accept": 0.2}, {}):
            with self.subTest(metrics=metrics):
                summary = common.read_json(run / "summary.json")
                summary["metrics"] = metrics
                common.write_json(run / "summary.json", summary)
                self.assertEqual(self.decide()["decision"], "discard")
                self.assertIsNone(ledger._state(self.ws)["best"])

    def test_keep_override_cannot_bypass_output_harness_or_guard_constraints(self):
        run = self.measured()
        original = common.read_json(run / "summary.json")
        for bad in ({"gate_failures": 1}, {"harness_error_rate": 1.0}, {"metrics": {"false_accept": 0.2}}):
            with self.subTest(bad=bad):
                common.write_json(run / "summary.json", {**original, **bad})
                before = (self.ws / "ledger.jsonl").read_bytes()
                with self.assertRaisesRegex(SystemExit, "cannot override hard measurement constraints or configured guards"):
                    self.decide(override="keep", reason="invalid measurement cannot be promoted")
                self.assertEqual((self.ws / "ledger.jsonl").read_bytes(), before)
                self.assertIsNone(ledger._state(self.ws)["best"])
                self.assertEqual(self.decide(override="discard", reason="explicit rejection")["decision"], "discard")
                self.assertIsNone(ledger._state(self.ws)["best"])

    def test_later_invalid_candidate_cannot_replace_valid_baseline_via_override(self):
        self.measured()
        self.decide()
        self.measured("E002", "candidate", gate_failures=1)
        with self.assertRaisesRegex(SystemExit, "cannot override hard measurement constraints"):
            self.decide("E002", override="keep", reason="cannot bypass schema")
        self.assertEqual(ledger._state(self.ws)["best"]["exp"], "E001")

    def test_unknown_or_invalid_measurement_safety_counts_fail_closed(self):
        run = self.measured()
        original = common.read_json(run / "summary.json")
        for bad in ({"harness_error_rate": None}, {"harness_error_rate": float("nan")},
                    {"harness_error_rate": True}, {"gate_failures": None}, {"gate_failures": -1}):
            with self.subTest(bad=bad):
                common.write_json(run / "summary.json", {**original, **bad})
                with self.assertRaisesRegex(SystemExit, "missing or invalid measurement safety counts"):
                    self.decide(override="keep")
                self.assertIsNone(ledger._state(self.ws)["best"])

    def test_direct_api_override_cannot_inject_unlisted_baseline_decision(self):
        self.measured(gate_failures=1)
        with self.assertRaisesRegex(SystemExit, "override must be keep or discard"):
            self.decide(override="baseline")
        self.assertIsNone(ledger._state(self.ws)["best"])


if __name__ == "__main__":
    unittest.main()
