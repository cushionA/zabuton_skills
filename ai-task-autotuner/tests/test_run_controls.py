"""Runtime boundaries: exercise runner entry, queues and real retry wrapper offline."""
import contextlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common
import ledger
import llm_call
import run_experiment as runner
import run_policy
import workspace


DATASET = '''
def load_units(split):
    dev = [{"id": "d1", "input": {}}, {"id": "d2", "input": {}}, {"id": "d3", "input": {}}]
    hold = [{"id": "h1", "input": {}}]
    return dev if split == "dev" else (dev + hold if split == "full" else hold)
'''
SCORER = '''
def score(units, outputs, split):
    return {"primary": sum(outputs.get(u["id"]) is not None for u in units) / max(1, len(units)),
            "items": [{"id": u["id"], "unit": u["id"], "score": int(outputs.get(u["id"]) is not None)} for u in units]}
'''
IMPORT_MARKER = '''
from pathlib import Path
Path(__file__).parents[2].joinpath("imported").write_text("imported")
'''
CODE = IMPORT_MARKER + '''
def run(unit, ctx):
    return {"choice": "ok"}
'''
MODEL = IMPORT_MARKER + '''
def run(unit, ctx):
    result = ctx.llm("test", tag="main")
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return {"choice": "ok"}
'''


def attempt(cost=0.6, ok=True, error_kind=None, retryable=False):
    return {"ok": ok, "text": "ok" if ok else None, "error": None if ok else "temporary failure",
            "error_kind": error_kind, "retryable": retryable, "cost_usd": cost,
            "usage": {"input": 10, "output": 2, "cache_read": 0, "cache_write": 0, "thinking": 0},
            "stop_reason": "end_turn", "model_served": "claude-haiku-5-5"}


class RunControlsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name)
        (self.ws / "eval").mkdir()
        (self.ws / "runs").mkdir()
        (self.ws / "source.json").write_text("initial")
        (self.ws / "eval" / "dataset.py").write_text(DATASET)
        (self.ws / "eval" / "score.py").write_text(SCORER)
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["d1", "d2", "d3"], "holdout": ["h1"]})
        common.write_json(self.ws / "charter.json", {
            "created_epoch": time.time(), "data": {"inputs": [str(self.ws / "source.json")], "aux": []},
            "backend": {"backend": "claude", "model": "haiku", "concurrency": 3,
                        "max_parallel_calls": 1, "retries": 0, "timeout_s": 30},
            "limits": {}, "loop": {"max_holdout_evals": 1}})
        for name in ("c1", "c2", "c3"):
            self.candidate(name)
        for attribute in ("gate_allows_runs", "task_review_allows_runs", "_validation_ok"):
            patcher = mock.patch.object(runner, attribute, return_value=(True, ""))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(runner, "_cli_version", return_value="offline test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidate(self, name, code=CODE):
        path = self.ws / "candidates" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "pipeline.py").write_text(code)

    def charter(self, **updates):
        ch = common.read_json(self.ws / "charter.json")
        for key, value in updates.items():
            ch[key] = value
        common.write_json(self.ws / "charter.json", ch)
        return ch

    def run_candidate(self, candidate="c1", split="dev", purpose="baseline", **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run(self.ws, candidate, split, purpose, **kwargs)

    def assert_refused_before_import(self, pattern, **kwargs):
        marker = self.ws / "imported"
        marker.unlink(missing_ok=True)
        with self.assertRaisesRegex(SystemExit, pattern):
            self.run_candidate(**kwargs)
        self.assertFalse(marker.exists())

    def test_holdout_aliases_reject_tuning_purposes(self):
        for purpose in ("smoke", "baseline", "probe", "experiment", "confirm"):
            with self.subTest(purpose=purpose):
                self.assert_refused_before_import("held-out", split="alias", purpose=purpose)

    def test_code_only_peeks_count_and_repeats_share_one(self):
        first = self.run_candidate(split="holdout", purpose="holdout")
        self.assertTrue(common.read_json(first / "summary.json")["ai_free"])
        self.run_candidate(split="alias", purpose="full", tag="repeat")
        self.run_candidate(candidate="c2", purpose="smoke")
        self.assert_refused_before_import("cap 1", candidate="c2", split="full", purpose="full")

    def test_zero_holdout_cap_cannot_be_bypassed(self):
        self.run_candidate(split="holdout", purpose="holdout")
        self.charter(loop={"max_holdout_evals": 0})
        for split, purpose in (("holdout", "holdout"), ("full", "full"), ("alias", "full")):
            with self.subTest(split=split):
                self.assert_refused_before_import("cap 0", split=split, purpose=purpose)

    def test_effective_model_effort_and_concurrency_are_frozen_after_measurement(self):
        first = self.run_candidate(split="holdout", purpose="holdout", concurrency=2)
        meta = common.read_json(first / "meta.json")
        self.assertEqual(meta["config"]["concurrency"], 2)
        self.run_candidate(split="holdout", purpose="holdout", concurrency=2, tag="repeat")
        original = common.read_json(self.ws / "charter.json")["backend"]
        for key, value in (("model", "sonnet"), ("effort", "medium")):
            with self.subTest(key=key):
                self.charter(backend=dict(original, **{key: value}))
                self.assert_refused_before_import("effective config changed", split="holdout", purpose="holdout", concurrency=2)
        self.charter(backend=original)
        self.assert_refused_before_import("effective config changed", split="holdout", purpose="holdout", concurrency=3)

    def test_historical_config_changes_count_as_distinct_holdout_peeks(self):
        first = self.run_candidate(split="holdout", purpose="holdout")
        old = common.read_json(first / "meta.json")
        changed = dict(old, run_id="historical-config-change", config=dict(old["config"], effort="high"))
        legacy = self.ws / "runs" / changed["run_id"]
        legacy.mkdir()
        common.write_json(legacy / "meta.json", changed)
        common.write_json(legacy / "summary.json", {"ai_free": True, "calls": 0, "cost_usd": 0})
        self.assertEqual(run_policy.limit_state(self.ws)["holdout_evals"], 2)
        self.charter(loop={"max_holdout_evals": 2})
        self.assert_refused_before_import("cap 2", candidate="c2", split="holdout", purpose="holdout")

    def test_holdout_requires_validation_reference_does_not_score(self):
        with mock.patch.object(runner, "_validation_ok", return_value=(False, "not validated")):
            self.assert_refused_before_import("not validated", split="alias", purpose="full")
            ref = self.run_candidate(split="full", purpose="reference")
        score = common.read_json(ref / "score.json")
        self.assertIsNone(score["primary"])
        self.assertEqual(score["items"], [])
        self.assertEqual(run_policy.limit_state(self.ws)["holdout_evals"], 0)
        with self.assertRaisesRegex(SystemExit, "reference preparation"):
            runner.rescore(self.ws, ref)

    def test_experiment_binding_is_checked_for_direct_calls(self):
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E001", "candidate": "c1", "lever": "baseline"})
        for purpose in ("baseline", "smoke", "reference", "full"):
            with self.subTest(purpose=purpose):
                self.assert_refused_before_import("belongs to c1", candidate="c2", purpose=purpose, exp="E001")
        self.assert_refused_before_import("unknown experiment", exp="E999")
        self.assert_refused_before_import("expects purpose baseline", purpose="experiment", exp="E001")

    def test_hard_limits_and_paused_stop_before_candidate_import(self):
        scenarios = [({"max_cost_usd": 0}, "cost"), ({"max_hours": 0}, "hours"),
                     ({"deadline": "2000-01-01T00:00:00+09:00"}, "deadline")]
        for limits, message in scenarios:
            with self.subTest(limits=limits):
                self.charter(limits=limits)
                self.assert_refused_before_import(message)
        self.charter(limits={})
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "clock", "action": "pause", "epoch": time.time()})
        self.assert_refused_before_import("clock paused")
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "clock", "action": "resume", "epoch": time.time()})
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "stop", "reason": "explicit test stop"})
        self.assert_refused_before_import("explicit test stop", split="full", purpose="full")

    def test_unknown_historical_cost_does_not_become_zero(self):
        self.charter(limits={"max_cost_usd": 10})
        old = self.ws / "runs" / "unknown"
        old.mkdir()
        common.write_json(old / "meta.json", {"run_id": "unknown", "candidate": "legacy"})
        common.write_json(old / "summary.json", {"cost_usd": None, "calls": 1, "ai_free": False})
        self.assert_refused_before_import("USD cost unreported")
        for cost in (0, True, -1, float("nan"), float("inf")):
            with self.subTest(cost=cost):
                common.write_json(old / "summary.json", {"cost_usd": cost, "cost_complete": False,
                                                          "calls": 1, "ai_free": False})
                self.assert_refused_before_import("USD cost unreported")
        (old / "summary.json").unlink()
        self.assert_refused_before_import("USD cost unreported")

    def test_experiment_cap_allows_registered_slot_repeats_and_finalization(self):
        self.charter(limits={"max_experiments": 1})
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E001", "candidate": "c1", "lever": "baseline"})
        self.run_candidate(exp="E001")
        self.run_candidate(exp="E001", tag="r2")
        self.run_candidate(split="holdout", purpose="holdout")
        self.assert_refused_before_import("max_experiments 1", candidate="c2")
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E002", "candidate": "c2", "lever": "prompt.instructions"})
        self.assert_refused_before_import("max_experiments 1", candidate="c2", purpose="experiment", exp="E002")

    def test_observed_cost_stops_calls_waiting_in_semaphore(self):
        self.candidate("c1", MODEL)
        self.charter(limits={"max_cost_usd": 0.5})
        with mock.patch.object(llm_call, "_cli_attempt", return_value=attempt()) as adapter:
            run = self.run_candidate()
        self.assertEqual(adapter.call_count, 1)
        summary = common.read_json(run / "summary.json")
        self.assertTrue(summary["policy_exhausted"])
        self.assertGreater(summary["harness_error_rate"], 0)
        self.assertAlmostEqual(summary["cost_usd"], 0.6)

    def test_retry_rechecks_budget_without_extra_dispatch(self):
        self.candidate("c1", MODEL)
        ch = self.charter(limits={"max_cost_usd": 0.5})
        ch["backend"]["retries"] = 2
        self.charter(backend=ch["backend"])
        with mock.patch.object(llm_call, "_cli_attempt", return_value=attempt(ok=False, error_kind="api", retryable=True)) as adapter:
            with mock.patch.object(llm_call.time, "sleep"):
                run = self.run_candidate()
        self.assertEqual(adapter.call_count, 1)
        calls = common.read_jsonl(run / "calls.jsonl")
        self.assertEqual(calls[0]["attempts"], 1)
        self.assertTrue(calls[0]["policy_refused"])
        self.assertAlmostEqual(calls[0]["cost_usd"], 0.6)

    def test_quota_is_rechecked_inside_semaphore(self):
        self.candidate("c1", MODEL)
        with mock.patch.object(llm_call, "_cli_attempt", return_value=attempt(cost=None, ok=False, error_kind="quota")) as adapter:
            run = self.run_candidate()
        self.assertEqual(adapter.call_count, 1)
        self.assertTrue(common.read_json(run / "summary.json")["quota_exhausted"])

    def test_explicit_stop_during_call_blocks_waiting_dispatches(self):
        self.candidate("c1", MODEL)

        def stop_after_first(*args):
            common.append_jsonl(self.ws / "ledger.jsonl", {"event": "stop", "reason": "stop during run"})
            return attempt(cost=0)

        with mock.patch.object(llm_call, "_cli_attempt", side_effect=stop_after_first) as adapter:
            run = self.run_candidate()
        self.assertEqual(adapter.call_count, 1)
        self.assertTrue(common.read_json(run / "summary.json")["policy_exhausted"])

    def test_code_only_units_obey_stop_between_units(self):
        self.candidate("c1", IMPORT_MARKER + '''
import json
def run(unit, ctx):
    with Path(__file__).parents[2].joinpath("ledger.jsonl").open("a") as ledger:
        ledger.write(json.dumps({"event": "stop", "reason": "code requested stop"}) + "\\n")
    return {"choice": "ok"}
''')
        run = self.run_candidate(concurrency=1)
        summary = common.read_json(run / "summary.json")
        self.assertEqual(summary["status"].get("ok"), 1)
        self.assertEqual(summary["status"].get("harness_error"), 2)
        self.assertTrue(summary["policy_exhausted"])

    def test_deadline_bounds_adapter_timeout(self):
        self.candidate("c1", MODEL)
        self.charter(limits={"max_hours": 0.001})
        with mock.patch.object(llm_call, "_cli_attempt", return_value=attempt(cost=0)) as adapter:
            self.run_candidate()
        self.assertLessEqual(adapter.call_args.args[5], 3.6)
        self.assertGreater(adapter.call_args.args[5], 0)

    def test_data_change_during_run_invalidates_result_and_rescore(self):
        code = IMPORT_MARKER + '''
def run(unit, ctx):
    Path(__file__).parents[2].joinpath("source.json").write_text("changed")
    return {"choice": "ok"}
'''
        self.candidate("c1", code)
        run = self.run_candidate(concurrency=1)
        original = common.read_json(run / "meta.json")["data_hash"]
        self.assertNotEqual(original, workspace.data_fingerprint(self.ws))
        self.assertTrue(common.read_json(run / "summary.json")["data_changed"])
        score_before = (run / "score.json").read_bytes()
        with self.assertRaisesRegex(SystemExit, "rerun the candidate"):
            runner.rescore(self.ws, run)
        self.assertEqual(score_before, (run / "score.json").read_bytes())
        self.assertEqual(original, common.read_json(run / "meta.json")["data_hash"])

    def test_scorer_only_change_can_rescore_without_changing_generation_identity(self):
        run = self.run_candidate()
        original = common.read_json(run / "meta.json")["data_hash"]
        (self.ws / "eval" / "score.py").write_text(SCORER + "\n# changed scoring\n")
        with contextlib.redirect_stdout(io.StringIO()):
            runner.rescore(self.ws, run)
        self.assertEqual(original, common.read_json(run / "meta.json")["data_hash"])

    def test_unknown_usage_never_becomes_free_uncached_cost(self):
        units = [{"id": "d1"}]
        rows = [{"id": "d1", "status": "ok", "wall_ms": 1}]
        score = {"primary": 1, "metrics": {}, "items": []}
        full = {"input": 10, "output": 2, "cache_read": 0, "cache_write": 0, "thinking": 0}
        cases = [None, "unavailable", {}, {"input": 10, "output": 2}, dict(full, cache_read=None),
                 dict(full, input=-1), dict(full, input=True), dict(full, input="10"),
                 dict(full, output=float("nan"))]
        for usage in cases:
            with self.subTest(usage=usage):
                summary = runner._summarize(units, rows, [{"usage": usage, "cost_usd": None,
                                                          "model_served": "claude-haiku-5-5"}], score, 1, 1)
                self.assertIsNone(summary["cost_uncached_list_usd"])
                self.assertIsNone(summary["cost_uncached_list_usd_per_unit"])
        summary = runner._summarize(units, rows, [], score, 1, 1)
        self.assertEqual(summary["cost_uncached_list_usd"], 0)
        self.assertEqual(summary["cost_uncached_list_usd_per_unit"], 0)

    def test_fallback_after_unknown_usage_is_not_kept_as_free(self):
        self.charter(efficiency_metric="cost_uncached_list_usd_per_unit", phase="efficiency")
        self.candidate("c1", MODEL)
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E001", "candidate": "c1", "lever": "baseline"})
        with mock.patch.object(llm_call, "_cli_attempt", return_value=attempt(cost=0.1)):
            self.run_candidate(exp="E001")
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ledger, "print_status"):
            ledger.decide(self.ws, "E001")
        self.candidate("c2", IMPORT_MARKER + '''
def run(unit, ctx):
    ctx.llm("test")
    return {"choice": "ok"}
''')
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E002", "candidate": "c2", "lever": "deterministic.post"})
        unknown = attempt(cost=None, ok=False, error_kind="timeout")
        unknown["usage"] = None
        with mock.patch.object(llm_call, "_cli_attempt", return_value=unknown):
            run = self.run_candidate(candidate="c2", purpose="experiment", exp="E002")
        summary = common.read_json(run / "summary.json")
        self.assertEqual(summary["primary"], 1)
        self.assertIsNone(summary["cost_uncached_list_usd_per_unit"])
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ledger, "print_status"):
            ledger.decide(self.ws, "E002")
        decision = [row for row in common.read_jsonl(self.ws / "ledger.jsonl") if row.get("event") == "decide"][-1]
        self.assertEqual(decision["decision"], "discard")
        self.assertIsNone(decision["eff_ratio"])


if __name__ == "__main__":
    unittest.main()
