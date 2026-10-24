"""Directional evaluator checks use real max/error scorers without model calls."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import validate_eval  # noqa: E402
import workspace  # noqa: E402
import run_experiment  # noqa: E402
import test_flow  # noqa: E402


DATASET = '''
def load_units(split):
    return [{"id": "d" + str(i), "input": {}} for i in range(8)] if split == "dev" else [{"id": "h0", "input": {}}]
def group_of(unit):
    return unit["id"]
'''

SCORER = '''
NULL_ERROR = {null_error!r}
ORACLE = {oracle!r}
CORRUPT = {corrupt!r}
SCALE = {scale!r}
OFFSET = {offset!r}
MAXIMIZE = {maximize!r}
def score(units, outputs, split):
    errors = [NULL_ERROR if outputs.get(u["id"]) is None else float(outputs[u["id"]] != "yes") for u in units]
    raw = sum(errors) / len(errors)
    primary = OFFSET + SCALE * (1 - raw if MAXIMIZE else raw)
    return {{"primary": primary, "metrics": {{}}, "gate_failures": [],
            "items": [{{"id": u["id"], "unit": u["id"], "score": 1 - error}} for u, error in zip(units, errors)]}}
def oracle(units, split):
    return {{u["id"]: ORACLE for u in units}}
def corrupt(output, rng):
    return output if CORRUPT == "unchanged" else "wrong"
'''


class EvalDirectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name)
        (self.ws / "eval").mkdir()
        (self.ws / "eval" / "dataset.py").write_text(DATASET, encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["d" + str(i) for i in range(8)], "holdout": ["h0"]})
        common.write_json(self.ws / "charter.json", {"data": {"inputs": [], "aux": []}, "primary": {"direction": "min"}})
        self.configure()

    def configure(self, *, primary=None, null_error=1.0, oracle="yes", corrupt="wrong", scale=1.0, offset=0.0,
                  maximize=False):
        if primary is not None:
            ch = workspace.charter(self.ws)
            ch["primary"] = primary
            common.write_json(self.ws / "charter.json", ch)
        (self.ws / "eval" / "score.py").write_text(SCORER.format(
            null_error=null_error, oracle=oracle, corrupt=corrupt, scale=scale, offset=offset, maximize=maximize),
            encoding="utf-8")

    def runs(self):
        dataset, scorer = workspace.load_eval(self.ws)
        units = dataset.load_units("dev")
        outputs = {u["id"]: "yes" if i < 4 else "wrong" for i, u in enumerate(units)}
        paths = []
        for i in range(2):
            path = self.ws / "runs" / f"baseline-{i}"
            common.write_json(path / "meta.json", {
                "unit_ids": [u["id"] for u in units], "split": "dev", "purpose": "baseline",
                "data_hash": workspace.data_fingerprint(self.ws), "eval_hash": workspace.eval_hash(self.ws)})
            common.write_json(path / "score.json", scorer.score(units, outputs, "dev"))
            common.write_json(path / "summary.json", {"harness_error_rate": 0})
            (path / "outputs.jsonl").unlink(missing_ok=True)
            for uid, output in outputs.items():
                common.append_jsonl(path / "outputs.jsonl", {"id": uid, "status": "ok", "output": output})
            paths.append(str(path))
        return paths

    def validate(self, *, expected=0, renoise=False, runs=None):
        command = ["validate_eval.py", str(self.ws), "--renoise" if renoise else "--runs", *(runs or self.runs())]
        if not renoise:
            command += ["--spotcheck", "All eight baseline outputs and their labels were checked against the task."]
        with mock.patch.object(sys, "argv", command), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                validate_eval.main()
            except SystemExit as result:
                code = result.code if isinstance(result.code, int) else 1
            else:
                code = 0
        self.assertEqual(code, expected)
        return common.read_json(self.ws / "eval" / "validation.json", {})

    def check(self, result, name):
        return next(c for c in result["checks"] if c["check"] == name)

    def test_min_error_rate_passes_null_oracle_corrupt_and_positive_headroom(self):
        result = self.validate()
        self.assertTrue(result["passed"])
        self.assertEqual(self.check(result, "null")["detail"]["primary"], 1.0)
        self.assertEqual(self.check(result, "oracle")["detail"]["primary"], 0.0)
        self.assertEqual(self.check(result, "corrupt")["detail"]["corrupted"], 1.0)
        self.assertEqual(result["noise"]["headroom"], 0.5)
        self.assertEqual(result["noise"]["optimum"], 0.0)

    def test_max_correctness_behavior_is_preserved(self):
        self.configure(primary={"direction": "max", "ceiling": 1.0}, maximize=True)
        result = self.validate()
        self.assertTrue(result["passed"])
        self.assertEqual(self.check(result, "null")["detail"]["primary"], 0)
        self.assertEqual(self.check(result, "oracle")["detail"]["primary"], 1)
        self.assertEqual(result["noise"]["headroom"], 0.5)

    def test_min_grader_cannot_reward_all_missing_answers(self):
        self.configure(null_error=0)
        result = self.validate(expected=1)
        self.assertFalse(self.check(result, "null")["ok"])

    def test_min_oracle_must_reach_the_lower_optimum(self):
        self.configure(oracle="wrong")
        result = self.validate(expected=1)
        self.assertFalse(self.check(result, "oracle")["ok"])

    def test_defined_corruption_must_worsen_the_primary(self):
        self.configure(corrupt="unchanged")
        result = self.validate(expected=1)
        check = self.check(result, "corrupt")
        self.assertFalse(check["ok"])
        self.assertTrue(check["required"])

    def test_custom_minimum_scale_uses_explicit_optimum_and_bounds(self):
        self.configure(primary={"direction": "min", "optimum": 10, "null_min": 100, "oracle_max": 10.1},
                       scale=100, offset=10)
        result = self.validate()
        self.assertTrue(result["passed"])
        self.assertEqual(result["noise"]["headroom"], 50)
        self.assertEqual(self.check(result, "oracle")["detail"]["optimum"], 10)

    def test_wrong_direction_cannot_validate_an_error_rate_as_correctness(self):
        self.configure(primary={"direction": "max"})
        result = self.validate(expected=1)
        self.assertFalse(self.check(result, "null")["ok"])
        self.assertFalse(self.check(result, "oracle")["ok"])

    def test_invalid_direction_or_thresholds_fail_before_validation(self):
        cases = [
            {"direction": "typo"}, {"direction": "min", "item_direction": "typo"},
            {"direction": "min", "null_min": True}, {"direction": "min", "oracle_max": float("nan")},
            {"direction": "min", "optimum": float("inf")}, {"direction": "min", "oracle_max": "0"},
            {"direction": "min", "optimum": 1, "oracle_max": 0},
            {"direction": "min", "null_min": 0, "oracle_max": 0.1},
        ]
        for primary in cases:
            with self.subTest(primary=primary):
                self.configure(primary=primary)
                self.validate(expected=2)
                self.assertFalse((self.ws / "eval" / "validation.json").exists())

    def test_renoise_rejects_invalid_direction_before_changing_charter(self):
        self.configure(primary={"direction": "min", "item_direction": "wrong"})
        self.validate(expected=2, renoise=True)
        self.assertNotIn("noise", workspace.charter(self.ws))

    def seed_noise(self):
        charter = workspace.charter(self.ws)
        charter["noise"] = {"sentinel": "previous trustworthy measurement", "min_delta": 0.2}
        common.write_json(self.ws / "charter.json", charter)
        return charter

    @staticmethod
    def write_outputs(path, rows):
        (path / "outputs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_incomplete_duplicate_and_foreign_units_cannot_establish_noise(self):
        cases = ("subset", "extra", "duplicate_meta", "duplicate_output", "missing_output",
                 "extra_output", "foreign_meta", "limited", "malformed_output", "missing_payload")
        for renoise in (False, True):
            for case in cases:
                with self.subTest(renoise=renoise, case=case):
                    before = self.seed_noise()
                    runs = self.runs()
                    path = Path(runs[0])
                    meta = common.read_json(path / "meta.json")
                    rows = common.read_jsonl(path / "outputs.jsonl")
                    foreign = {"id": "foreign", "status": "ok", "output": "wrong"}
                    if case == "subset":
                        meta["unit_ids"], rows = meta["unit_ids"][:4], rows[:4]
                    elif case == "extra":
                        meta["unit_ids"].append("foreign")
                        rows.append(foreign)
                    elif case == "duplicate_meta":
                        meta["unit_ids"].append(meta["unit_ids"][0])
                    elif case == "duplicate_output":
                        rows.append(rows[0])
                    elif case == "missing_output":
                        rows.pop()
                    elif case == "extra_output":
                        rows.append(foreign)
                    elif case == "foreign_meta":
                        meta["unit_ids"][-1] = "foreign"
                    elif case == "limited":
                        meta["limit"] = 4
                    elif case == "malformed_output":
                        rows.append("an unexpected diagnostic")
                    elif case == "missing_payload":
                        rows[0].pop("output")
                    common.write_json(path / "meta.json", meta)
                    self.write_outputs(path, rows)
                    result = self.validate(expected=1, renoise=renoise, runs=runs)
                    self.assertEqual(workspace.charter(self.ws), before)
                    if not renoise:
                        self.assertFalse(result["passed"])
                        self.assertFalse(self.check(result, "identity")["ok"])
                        self.assertIsNone(result["noise"])

    def test_the_same_generation_cannot_count_as_two_independent_runs(self):
        for renoise in (False, True):
            for aliases in (False, True):
                with self.subTest(renoise=renoise, aliases=aliases):
                    before = self.seed_noise()
                    runs = self.runs()
                    repeated = str(Path(runs[0]) / ".." / Path(runs[0]).name) if aliases else runs[0]
                    result = self.validate(expected=1, renoise=renoise, runs=[runs[0], repeated])
                    self.assertEqual(workspace.charter(self.ws), before)
                    if not renoise:
                        self.assertIn("independent runs", str(self.check(result, "identity")["detail"]))

    def test_copied_generation_ids_cannot_prove_independent_measurements(self):
        before = self.seed_noise()
        runs = self.runs()
        for run in runs:
            path = Path(run) / "meta.json"
            meta = common.read_json(path)
            meta["run_id"] = "same-copied-generation"
            common.write_json(path, meta)
        result = self.validate(expected=1, runs=runs)
        self.assertFalse(result["passed"])
        self.assertIn("duplicate generation run_id", str(self.check(result, "identity")["detail"]))
        self.assertEqual(workspace.charter(self.ws), before)

    def test_noise_cannot_use_only_the_shared_subset_of_scored_items(self):
        for renoise in (False, True):
            for case in ("missing", "duplicate", "empty", "unit_change", "boolean_score"):
                with self.subTest(renoise=renoise, case=case):
                    before = self.seed_noise()
                    runs = self.runs()
                    path = Path(runs[1]) / "score.json"
                    score = common.read_json(path)
                    if case == "missing":
                        score["items"].pop()
                    elif case == "duplicate":
                        score["items"].append(score["items"][0])
                    elif case == "unit_change":
                        score["items"][0]["unit"] = "different product group"
                    elif case == "boolean_score":
                        score["items"][0]["score"] = True
                    else:
                        score["items"] = []
                    common.write_json(path, score)
                    result = self.validate(expected=1, renoise=renoise, runs=runs)
                    self.assertEqual(workspace.charter(self.ws), before)
                    if not renoise:
                        self.assertFalse(self.check(result, "noise_items")["ok"])

    def test_complete_independent_renoise_can_replace_old_noise(self):
        self.seed_noise()
        self.validate(renoise=True)
        charter = workspace.charter(self.ws)
        self.assertEqual(charter["noise"]["n_items"], 8)
        self.assertEqual(charter["noise_history"][-1]["sentinel"], "previous trustworthy measurement")

    def test_shared_stored_item_subset_or_changed_score_cannot_hide_from_rescoring(self):
        for renoise in (False, True):
            for case in ("same_subset", "changed_score"):
                with self.subTest(renoise=renoise, case=case):
                    before = self.seed_noise()
                    runs = self.runs()
                    for run in runs:
                        path = Path(run) / "score.json"
                        score = common.read_json(path)
                        if case == "same_subset":
                            score["items"] = score["items"][:4]
                        else:
                            score["items"][0]["score"] = 0.25
                        common.write_json(path, score)
                    result = self.validate(expected=1, renoise=renoise, runs=runs)
                    self.assertEqual(workspace.charter(self.ws), before)
                    if not renoise:
                        self.assertFalse(self.check(result, "rescore")["ok"])
                        self.assertIsNone(result["noise"])

    def test_baseline_hard_gate_and_harness_failures_do_not_rewrite_noise(self):
        cases = ("reported_gate", "stored_gate", "reported_harness", "recorded_harness", "interrupted")
        for renoise in (False, True):
            for case in cases:
                with self.subTest(renoise=renoise, case=case):
                    before = self.seed_noise()
                    runs = self.runs()
                    for run in runs:
                        path = Path(run)
                        summary = common.read_json(path / "summary.json")
                        score = common.read_json(path / "score.json")
                        rows = common.read_jsonl(path / "outputs.jsonl")
                        if case == "reported_gate":
                            summary["gate_failures"] = 1
                        elif case == "stored_gate":
                            score["gate_failures"] = ["invalid output shape"]
                        elif case == "reported_harness":
                            summary["harness_error_rate"] = 0.5
                        elif case == "recorded_harness":
                            for row in rows:
                                row["status"] = "harness_error"
                        else:
                            summary["quota_exhausted"] = True
                        common.write_json(path / "summary.json", summary)
                        common.write_json(path / "score.json", score)
                        self.write_outputs(path, rows)
                    result = self.validate(expected=1, renoise=renoise, runs=runs)
                    self.assertEqual(workspace.charter(self.ws), before)
                    if not renoise:
                        check = "baseline_gates" if case.endswith("gate") else "harness_health"
                        self.assertFalse(self.check(result, check)["ok"])
                        self.assertIsNone(result["noise"])

    def test_real_malformed_template_baseline_is_not_a_trustworthy_noise_source(self):
        fixture = test_flow.FlowTest("test_full_flow")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.ws = fixture.ws
        fixture.run_py("quality_gate.py", str(self.ws))
        fixture.review_task()
        candidate = self.ws / "candidates" / "c003-malformed"
        candidate.mkdir()
        (candidate / "pipeline.py").write_text('def run(unit, ctx): return {"items": None}\n', encoding="utf-8")
        before = self.seed_noise()
        with contextlib.redirect_stdout(io.StringIO()):
            runs = [str(run_experiment.run(self.ws, "c003-malformed", "dev", "baseline", tag=str(i))) for i in range(2)]
        self.assertTrue(common.read_json(Path(runs[0]) / "score.json")["gate_failures"])
        result = self.validate(expected=1, runs=runs)
        self.assertFalse(result["passed"])
        self.assertFalse(self.check(result, "baseline_gates")["ok"])
        self.assertEqual(workspace.charter(self.ws), before)
        self.validate(expected=1, renoise=True, runs=runs)
        self.assertEqual(workspace.charter(self.ws), before)


if __name__ == "__main__":
    unittest.main()
