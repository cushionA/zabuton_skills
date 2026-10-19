"""Decision safety for candidate attribution and measured zero/unknown costs."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common  # noqa: E402
import ledger  # noqa: E402
import workspace  # noqa: E402


class LedgerIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": [], "aux": []}, "phase": "quality",
            "primary": {"direction": "max"}, "loop": {"min_delta": 0.1, "min_efficiency_gain": 0.1},
        })
        for candidate in ("c000-base", "c001-test", "c999-foreign"):
            p = self.ws / "candidates" / candidate / "pipeline.py"
            p.parent.mkdir(parents=True)
            p.write_text(f"# {candidate}\ndef run(unit, ctx): return {{}}\n", encoding="utf-8")
        for name in ("dataset.py", "score.py"):
            p = self.ws / "eval" / name
            p.parent.mkdir(exist_ok=True)
            p.write_text("# synthetic measurement\n", encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["a", "b"], "holdout": []})

    def propose(self, exp, candidate, lever="deterministic.pre"):
        common.append_jsonl(self.ws / "ledger.jsonl", {
            "event": "propose", "exp": exp, "candidate": candidate, "lever": lever,
        })

    def measured(self, exp, candidate, *, cost=1.0, primary=0.5, tag="r1", metrics=None):
        run_id = f"{exp}__dev__experiment__{tag}"
        run = self.ws / "runs" / run_id
        common.write_json(run / "meta.json", {
            "run_id": run_id, "exp": exp, "candidate": candidate,
            "candidate_hash": workspace.candidate_hash(self.ws / "candidates" / candidate),
            "purpose": "baseline" if exp == "E001" else "experiment", "split": "dev",
            "eval_hash": workspace.eval_hash(self.ws), "data_hash": workspace.data_fingerprint(self.ws),
            "config": {"backend": "claude", "model": "claude-haiku-5-5", "effort": "low", "concurrency": 2},
        })
        common.write_json(run / "score.json", {
            "eval_hash": workspace.eval_hash(self.ws),
            "items": [{"id": i, "unit": i, "score": primary} for i in ("a", "b")],
        })
        common.write_json(run / "summary.json", {
            "primary": primary, "cost_usd_per_unit": cost, "units": 2,
            "harness_error_rate": 0, "gate_failures": 0, "metrics": metrics or {}, "ai_free": cost == 0,
        })
        return run

    def decide(self, exp, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ledger, "print_status"):
            ledger.decide(self.ws, exp, **kwargs)
        return [r for r in workspace.ledger_rows(self.ws) if r.get("event") == "decide" and r["exp"] == exp][-1]

    def baseline(self, *, cost=1.0, primary=0.5, phase="quality", metrics=None):
        ch = workspace.charter(self.ws)
        ch["phase"] = phase
        common.write_json(self.ws / "charter.json", ch)
        self.propose("E001", "c000-base", "baseline")
        self.measured("E001", "c000-base", cost=cost, primary=primary, metrics=metrics)
        self.assertEqual(self.decide("E001")["decision"], "baseline")
        self.propose("E002", "c001-test")

    def assert_no_decision(self, exp="E002"):
        self.assertFalse(any(r.get("event") == "decide" and r["exp"] == exp
                             for r in workspace.ledger_rows(self.ws)))

    def test_foreign_candidate_result_cannot_be_kept_under_proposed_candidate(self):
        self.baseline()
        self.measured("E002", "c999-foreign", primary=1.0)
        with self.assertRaisesRegex(SystemExit, "attribution invalid.*does not match proposal"):
            self.decide("E002", override="keep", reason="must not bypass attribution")
        self.assert_no_decision()

    def test_edited_candidate_metadata_cannot_relabel_foreign_results(self):
        self.baseline()
        run = self.measured("E002", "c999-foreign", primary=1.0)
        meta = common.read_json(run / "meta.json")
        meta["candidate"] = "c001-test"  # the foreign content hash still identifies its actual generator
        common.write_json(run / "meta.json", meta)
        with self.assertRaisesRegex(SystemExit, "attribution invalid.*candidate hash"):
            self.decide("E002")
        self.assert_no_decision()

    def test_editing_measured_candidate_invalidates_old_results(self):
        self.baseline()
        self.measured("E002", "c001-test", primary=1.0)
        p = self.ws / "candidates" / "c001-test" / "pipeline.py"
        p.write_text(p.read_text(encoding="utf-8") + "# altered\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "attribution invalid.*candidate hash"):
            self.decide("E002")
        self.assert_no_decision()

    def test_editing_only_meta_eval_hash_cannot_freshen_old_scores(self):
        self.baseline()
        run = self.measured("E002", "c001-test", primary=1.0)
        meta = common.read_json(run / "meta.json")
        meta["eval_hash"] = "forged-current-eval"
        common.write_json(run / "meta.json", meta)
        with self.assertRaisesRegex(SystemExit, "attribution invalid.*different eval hashes"):
            self.decide("E002")
        self.assert_no_decision()

    def test_source_generation_hash_cannot_be_omitted(self):
        self.baseline()
        run = self.measured("E002", "c001-test", primary=1.0)
        meta = common.read_json(run / "meta.json")
        meta.pop("data_hash")
        common.write_json(run / "meta.json", meta)
        with self.assertRaisesRegex(SystemExit, "older eval"):
            self.decide("E002")
        self.assert_no_decision()

    def test_interrupted_or_source_mutating_runs_never_enter_a_decision(self):
        self.baseline()
        run = self.measured("E002", "c001-test", primary=1.0)
        original = common.read_json(run / "summary.json")
        for flag in ("quota_exhausted", "policy_exhausted", "data_changed"):
            with self.subTest(flag=flag):
                common.write_json(run / "summary.json", {**original, flag: True})
                with self.assertRaisesRegex(SystemExit, "no complete runs"):
                    self.decide("E002")
                self.assert_no_decision()

    def test_mixed_model_or_effort_repeats_cannot_be_pooled_even_with_override(self):
        self.baseline()
        self.measured("E002", "c001-test", primary=1.0)
        run = self.measured("E002", "c001-test", primary=1.0, tag="r2")
        original = common.read_json(run / "meta.json")
        for change in ({"model": "claude-sonnet-4-6"}, {"effort": "medium"}):
            with self.subTest(change=change):
                meta = {**original, "config": {**original["config"], **change}}
                meta["config_hash"] = workspace.effective_config_fingerprint(meta["config"])
                common.write_json(run / "meta.json", meta)
                with self.assertRaisesRegex(SystemExit, "attribution invalid.*different effective execution configs"):
                    self.decide("E002", override="keep", reason="cannot mix model variants")
                self.assert_no_decision()

    def test_registered_final_experiment_slot_can_finish_before_cap_stop(self):
        ch = workspace.charter(self.ws)
        ch["limits"] = {"max_experiments": 1}
        common.write_json(self.ws / "charter.json", ch)
        self.propose("E001", "c000-base", "baseline")
        with mock.patch.object(ledger, "_gate", return_value=None):
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "RUN")
            self.measured("E001", "c000-base")
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "DECIDE")
            self.decide("E001")
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "STOP")
        args = SimpleNamespace(ws=str(self.ws), candidate="c001-test", lever="deterministic.pre")
        before = (self.ws / "ledger.jsonl").read_text(encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "max_experiments 1 already reserved"):
            ledger.cmd_propose(args)
        self.assertEqual((self.ws / "ledger.jsonl").read_text(encoding="utf-8"), before)

    def test_hard_limits_and_paused_clock_take_precedence_over_reserved_pending_slot(self):
        self.propose("E001", "c000-base", "baseline")
        ch = workspace.charter(self.ws)
        ch["limits"] = {"max_experiments": 1, "max_cost_usd": 0}
        common.write_json(self.ws / "charter.json", ch)
        with mock.patch.object(ledger, "_gate", return_value=None):
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "STOP")
            ch["limits"].pop("max_cost_usd")
            common.write_json(self.ws / "charter.json", ch)
            common.append_jsonl(self.ws / "ledger.jsonl", {
                "event": "clock", "action": "pause", "at": common.now_iso(),
            })
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "PAUSED")

    def test_registered_marginal_result_can_be_repeated_at_experiment_cap(self):
        self.baseline()
        ch = workspace.charter(self.ws)
        ch["limits"] = {"max_experiments": 2}
        common.write_json(self.ws / "charter.json", ch)
        self.measured("E002", "c001-test", primary=0.61)
        self.assertEqual(self.decide("E002")["decision"], "rerun")
        with mock.patch.object(ledger, "_gate", return_value=None):
            self.assertEqual(ledger.recommend(self.ws)["rec"][0], "RERUN")

    def test_positive_cost_to_zero_is_kept_as_full_reduction_in_both_phases(self):
        for phase in ("quality", "efficiency"):
            with self.subTest(phase=phase):
                (self.ws / "ledger.jsonl").unlink(missing_ok=True)
                self.baseline(cost=1.0, phase=phase)
                self.measured("E002", "c001-test", cost=0.0)
                decision = self.decide("E002")
                self.assertEqual(decision["decision"], "keep")
                self.assertEqual(decision["eff"], 0.0)
                self.assertEqual(decision["eff_ratio"], 0.0)

    def test_zero_to_zero_is_a_tie_and_positive_cost_cannot_improve_on_zero(self):
        self.baseline(cost=0.0, phase="efficiency")
        self.measured("E002", "c001-test", cost=0.0)
        decision = self.decide("E002")
        self.assertEqual((decision["decision"], decision["eff_ratio"]), ("discard", 1.0))
        # Even higher quality cannot be called equal-cost efficiency when cost went from free to positive.
        self.propose("E003", "c999-foreign")
        self.measured("E003", "c999-foreign", cost=0.1, primary=1.0)
        decision = self.decide("E003")
        self.assertEqual(decision["decision"], "discard")
        self.assertIsNone(decision["eff_ratio"])

    def test_unknown_or_invalid_cost_repeat_does_not_disappear_from_pool(self):
        self.baseline(phase="efficiency")
        self.measured("E002", "c001-test", cost=0.0)
        for bad in (None, -1, float("nan"), float("inf"), True):
            with self.subTest(cost=bad):
                self.measured("E002", "c001-test", cost=bad, tag="r2")
                decision = self.decide("E002")
                self.assertIsNone(decision["eff"])
                self.assertIsNone(decision["eff_ratio"])
                self.assertEqual(decision["decision"], "discard")

    def test_guard_metric_missing_in_a_repeat_is_missing_for_whole_pool(self):
        self.baseline(metrics={"error_rate": 0.0})
        ch = workspace.charter(self.ws)
        ch["guards"] = [{"metric": "error_rate", "max": 0.1}]
        common.write_json(self.ws / "charter.json", ch)
        self.measured("E002", "c001-test", cost=0.0, metrics={"error_rate": 0.0})
        self.measured("E002", "c001-test", cost=0.0, tag="r2")
        decision = self.decide("E002")
        self.assertIsNone(decision["metrics"]["error_rate"])
        self.assertEqual(decision["decision"], "discard")
        self.assertIn("error_rate: missing", decision["reason"])


if __name__ == "__main__":
    unittest.main()
