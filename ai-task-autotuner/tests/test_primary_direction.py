"""Minimized headline metrics retain an explicit, independent item-score contract."""
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


class PrimaryDirectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": [], "aux": []}, "phase": "quality",
            "primary": {"direction": "min"},
            "efficiency_metric": "cost_usd_per_unit",
            "loop": {"min_delta": 0.05, "min_efficiency_gain": 0.1},
        })
        for candidate in ("c000-base", "c001-test", "c002-worse"):
            path = self.ws / "candidates" / candidate / "pipeline.py"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {candidate}\ndef run(unit, ctx): return {{}}\n", encoding="utf-8")
        for name in ("dataset.py", "score.py"):
            path = self.ws / "eval" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("# synthetic task\n", encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["a", "b", "c", "d"], "holdout": []})

    def charter(self, **updates):
        ch = workspace.charter(self.ws)
        ch.update(updates)
        common.write_json(self.ws / "charter.json", ch)
        return ch

    def propose(self, exp, candidate, lever="deterministic.pre"):
        common.append_jsonl(self.ws / "ledger.jsonl", {
            "event": "propose", "exp": exp, "candidate": candidate, "lever": lever,
        })

    def measured(self, exp, candidate, primary, *, cost=1.0, tag="r1"):
        run_id = f"{exp}__dev__{tag}"
        run = self.ws / "runs" / run_id
        ch = workspace.charter(self.ws)
        item_min = ch["primary"].get("item_direction") == "min"
        item_score = primary if item_min or ch["primary"]["direction"] == "max" else 1 - primary
        common.write_json(run / "meta.json", {
            "run_id": run_id, "exp": exp, "candidate": candidate,
            "candidate_hash": workspace.candidate_hash(self.ws / "candidates" / candidate),
            "purpose": "baseline" if exp == "E001" else "experiment", "split": "dev",
            "eval_hash": workspace.eval_hash(self.ws), "data_hash": workspace.data_fingerprint(self.ws),
            "config": {"backend": "claude", "model": "haiku", "effort": "low", "concurrency": 1},
        })
        common.write_json(run / "score.json", {
            "eval_hash": workspace.eval_hash(self.ws),
            "items": [{"id": uid, "unit": uid, "score": item_score} for uid in ("a", "b", "c", "d")],
        })
        common.write_json(run / "summary.json", {
            "primary": primary, "cost_usd_per_unit": cost, "units": 4,
            "harness_error_rate": 0, "gate_failures": 0, "metrics": {}, "ai_free": cost == 0,
        })
        return run

    def decide(self, exp):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ledger, "print_status"):
            ledger.decide(self.ws, exp)
        return [r for r in workspace.ledger_rows(self.ws) if r.get("event") == "decide" and r["exp"] == exp][-1]

    def baseline(self, primary=0.3, *, cost=1.0):
        self.propose("E001", "c000-base", "baseline")
        run = self.measured("E001", "c000-base", primary, cost=cost)
        self.assertEqual(self.decide("E001")["decision"], "baseline")
        return run

    def recommend(self):
        with mock.patch.object(ledger, "_gate", return_value=None):
            return ledger.recommend(self.ws)["rec"]

    def phase(self, *options):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["ledger.py", "phase", str(self.ws), "efficiency", *options]), \
                contextlib.redirect_stdout(output):
            ledger.main()
        return workspace.charter(self.ws)["quality_floor"], output.getvalue()

    def status(self):
        output = io.StringIO()
        with mock.patch.object(ledger, "_gate", return_value=None), contextlib.redirect_stdout(output):
            ledger.print_status(self.ws)
        return output.getvalue()

    def test_minimum_target_requires_at_most_target_and_accepts_zero(self):
        run = self.baseline()
        self.charter(targets={"quality": 0.1})
        self.assertEqual(self.recommend()[0], "CONTINUE")
        summary = common.read_json(run / "summary.json")
        summary["primary"] = 0.1
        common.write_json(run / "summary.json", summary)
        self.assertEqual(self.recommend()[0], "SWITCH_PHASE")
        self.charter(targets={"quality": 0})
        self.assertEqual(self.recommend()[0], "CONTINUE")
        summary["primary"] = 0
        common.write_json(run / "summary.json", summary)
        self.assertEqual(self.recommend()[0], "SWITCH_PHASE")

    def test_target_uses_current_eval_results_and_not_stale_decision_numbers(self):
        self.baseline()
        self.charter(targets={"quality": 0.1})
        (self.ws / "eval" / "score.py").write_text("# corrected scorer\n", encoding="utf-8")
        self.assertNotEqual(self.recommend()[0], "SWITCH_PHASE")
        self.measured("E001", "c000-base", 0.05)
        # E001's stored decision is still 0.3; current re-pooled results are 0.05.
        self.assertEqual(self.recommend()[0], "SWITCH_PHASE")

    def test_minimum_phase_sets_upper_bound_and_honors_explicit_zero(self):
        self.baseline()
        bound, output = self.phase()
        self.assertAlmostEqual(bound, 0.35)
        self.assertIn("quality ceiling", output)
        self.assertIn("CEILING primary <= 0.35", self.status())
        bound, _ = self.phase("--floor", "0")
        self.assertEqual(bound, 0)
        self.assertIn("CEILING primary <= 0.0", self.status())

    def test_minimum_efficiency_accepts_within_upper_bound_and_rejects_excess(self):
        self.baseline()
        self.phase()
        self.propose("E002", "c001-test")
        self.measured("E002", "c001-test", 0.32, cost=0.5)
        self.assertEqual(self.decide("E002")["decision"], "keep")
        self.propose("E003", "c002-worse")
        self.measured("E003", "c002-worse", 0.36, cost=0.1)
        result = self.decide("E003")
        self.assertEqual(result["decision"], "discard")
        self.assertIn("above ceiling", result["reason"])

    def test_minimum_primary_keeps_correctness_item_scores_higher_is_better(self):
        self.baseline()
        self.propose("E002", "c001-test")
        self.measured("E002", "c001-test", 0.1)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertAlmostEqual(result["delta"], 0.2)
        self.assertEqual(result["paired"]["improved"], 4)
        self.assertGreater(result["paired"]["ci"][0], 0)
        self.assertIn("item-score improvement CI [+0.2000, +0.2000]", result["reason"])

    def test_explicit_minimum_item_contributions_reverse_pair_counts_and_interval(self):
        self.charter(primary={"direction": "min", "item_direction": "min"})
        self.baseline()
        self.propose("E002", "c001-test")
        self.measured("E002", "c001-test", 0.1)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(result["paired"]["improved"], 4)
        self.assertEqual(result["paired"]["regressed"], 0)
        self.assertGreater(result["paired"]["ci"][0], 0)
        self.propose("E003", "c002-worse")
        self.measured("E003", "c002-worse", 0.4)
        result = self.decide("E003")
        self.assertEqual(result["decision"], "discard")
        self.assertEqual(result["paired"]["regressed"], 4)
        self.assertLess(result["paired"]["ci"][1], 0)

    def test_minimum_status_selects_lowest_code_only_value(self):
        self.baseline(cost=0)
        self.propose("E002", "c001-test")
        self.measured("E002", "c001-test", 0.1, cost=0)
        self.decide("E002")
        self.assertIn("NO-LLM best E002", self.status())

    def test_minimum_degradation_and_history_labels_are_directional(self):
        self.baseline(primary=0.1)
        rows = workspace.ledger_rows(self.ws)
        rows[-1]["at"] = "2026-01-01T00:00:00+0000"
        (self.ws / "ledger.jsonl").write_text("", encoding="utf-8")
        for row in rows:
            common.append_jsonl(self.ws / "ledger.jsonl", row)
        self.charter(phase="efficiency", quality_floor=0.2)
        common.append_jsonl(self.ws / "ledger.jsonl", {
            "event": "phase", "to": "efficiency", "floor": 0.2, "at": "2026-01-01T00:00:01+0000",
        })
        self.propose("E002", "c001-test")
        for tag in ("r1", "r2"):
            self.measured("E002", "c001-test", 0.18, cost=0.5, tag=tag)
        self.assertEqual(self.decide("E002")["decision"], "keep")
        output = self.status()
        self.assertIn("quality -0.0800", output)
        self.assertIn("cumulative degradation exceeds min_delta", output)
        history = io.StringIO()
        with contextlib.redirect_stdout(history):
            ledger.cmd_history(self.ws)
        self.assertIn("min (lower is better)", history.getvalue())

    def test_maximum_targets_bounds_and_improvement_remain_unchanged(self):
        self.charter(primary={"direction": "max"})
        self.baseline(primary=0.6)
        self.charter(targets={"quality": 0.8})
        self.assertEqual(self.recommend()[0], "CONTINUE")
        self.propose("E002", "c001-test")
        self.measured("E002", "c001-test", 0.9)
        result = self.decide("E002")
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(self.recommend()[0], "SWITCH_PHASE")
        bound, _ = self.phase()
        self.assertAlmostEqual(bound, 0.85)
        self.assertIn("FLOOR primary >=", self.status())

    def test_invalid_primary_or_item_direction_refuses_before_phase_mutation(self):
        for field in ("direction", "item_direction"):
            with self.subTest(field=field):
                self.charter(primary={"direction": "min", field: "sideways"})
                before = (self.ws / "charter.json").read_bytes()
                with self.assertRaisesRegex(SystemExit, f"primary.{field} must be max or min"):
                    self.recommend()
                with self.assertRaisesRegex(SystemExit, f"primary.{field} must be max or min"):
                    self.phase("--floor", "0")
                self.assertEqual((self.ws / "charter.json").read_bytes(), before)
                self.assertFalse((self.ws / "ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
