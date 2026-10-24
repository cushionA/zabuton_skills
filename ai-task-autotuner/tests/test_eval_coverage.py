"""Reference completeness must be checked against inputs, including holdout."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import reference  # noqa: E402
import validate_eval  # noqa: E402
import workspace  # noqa: E402


DATASET = '''
import json
from pathlib import Path
WS = Path(__file__).resolve().parents[1]
def load_units(split):
    return json.loads((WS / "units.json").read_text())[split]
def group_of(unit):
    return unit["id"]
'''

NO_REFERENCE_SCORER = '''
def score(units, outputs, split):
    items = [{"id": u["id"], "unit": u["id"], "score": float(outputs.get(u["id"]) == "yes")}
             for u in units]
    acc = sum(i["score"] for i in items) / len(items)
    return {"primary": acc, "metrics": {"accuracy": acc}, "items": items, "gate_failures": []}
def oracle(units, split):
    return {u["id"]: "yes" for u in units}
'''


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        (self.ws / "eval" / "reference").mkdir(parents=True)
        common.write_json(self.ws / "charter.json", {"skill_dir": str(SKILL), "primary": {}})
        self.units = {
            "dev": [{"id": f"d{i}", "input": {"item_keys": ["a", "b"]}} for i in range(2)],
            "holdout": [{"id": "h0", "input": {"item_keys": ["a", "b"]}}],
        }
        common.write_json(self.ws / "units.json", self.units)
        (self.ws / "eval" / "dataset.py").write_text(DATASET, encoding="utf-8")
        shutil.copy2(SKILL / "assets" / "workspace_template" / "eval" / "score.py", self.ws / "eval" / "score.py")
        for split, units in self.units.items():
            self.write_reference(split, [{"key": f"{u['id']}|{k}", "label": label}
                                         for u in units for k, label in (("a", "pos"), ("b", "neg"))])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_reference(self, split, rows):
        (self.ws / "eval" / "reference" / f"{split}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def scorer(self):
        return workspace.load_eval(self.ws)[1]

    def baselines(self, custom=False):
        runs = []
        score_mod = self.scorer()
        for n in range(2):
            run = self.ws / "runs" / f"r{n}"
            run.mkdir(parents=True)
            outputs = ({u["id"]: "yes" if i == 0 else "no" for i, u in enumerate(self.units["dev"])}
                       if custom else {u["id"]: {"items": [{"key": "a", "label": "pos"},
                                                                         {"key": "b", "label": "pos"}]}
                                       for u in self.units["dev"]})
            common.write_json(run / "meta.json", {"unit_ids": list(outputs),
                              "data_hash": workspace.data_fingerprint(self.ws),
                              "eval_hash": workspace.eval_hash(self.ws)})
            common.write_json(run / "score.json", score_mod.score(self.units["dev"], outputs, "dev"))
            common.write_json(run / "summary.json", {"harness_error_rate": 0})
            for uid, output in outputs.items():
                common.append_jsonl(run / "outputs.jsonl", {"id": uid, "status": "ok", "output": output})
            runs.append(str(run))
        return runs

    def validate(self, custom=False):
        args = ["validate_eval.py", str(self.ws), "--runs", *self.baselines(custom), "--spotcheck",
                "read baseline mistakes against the input and confirmed grading"]
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_context:
                validate_eval.main()
        return exit_context.exception.code, common.read_json(self.ws / "eval" / "validation.json")

    def reference_check(self, result):
        return next(c for c in result["checks"] if c["check"] == "reference")

    def test_complete_dev_and_holdout_pass(self):
        code, result = self.validate()
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])
        self.assertEqual([s["split"] for s in self.reference_check(result)["detail"]["splits"]], ["dev", "holdout"])

    def test_partial_reference_within_covered_unit_cannot_shrink_denominator(self):
        self.write_reference("dev", [{"key": "d0|a", "label": "pos"},
                                     {"key": "d1|a", "label": "pos"}, {"key": "d1|b", "label": "neg"}])
        scorer = self.scorer()
        units = self.units["dev"]
        cov = scorer.reference_coverage(units, "dev")
        self.assertEqual((cov["covered"], cov["total"]), (3, 4))
        scored = scorer.score(units, scorer.oracle(units, "dev"), "dev")
        self.assertEqual(scored["primary"], 0.75)
        self.assertEqual(len(scored["items"]), 4)
        self.assertTrue(any("d0|b" in failure for failure in scored["gate_failures"]))
        code, result = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse(self.reference_check(result)["ok"])

    def test_whole_missing_unit_is_not_removed(self):
        self.write_reference("dev", [{"key": "d0|a", "label": "pos"}, {"key": "d0|b", "label": "neg"}])
        scorer = self.scorer()
        scored = scorer.score(self.units["dev"], scorer.oracle(self.units["dev"], "dev"), "dev")
        self.assertEqual(scored["primary"], 0.5)
        self.assertEqual(len(scored["items"]), 4)
        self.assertEqual(len(scored["gate_failures"]), 2)
        code, result = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse(self.reference_check(result)["ok"])

    def test_holdout_missing_references_blocks_otherwise_valid_dev(self):
        self.write_reference("holdout", [])
        code, result = self.validate()
        self.assertEqual(code, 1)
        check = self.reference_check(result)
        self.assertFalse(check["ok"])
        self.assertEqual(check["detail"]["splits"][1]["covered"], 0)
        self.assertTrue(next(c for c in result["checks"] if c["check"] == "oracle")["ok"])

    def test_malformed_holdout_entries_cannot_pass_as_complete_references(self):
        self.write_reference("holdout", [{"key": "h0|a"}, {"key": "h0|b", "acceptable": []}])
        scorer = self.scorer()
        cov = scorer.reference_coverage(self.units["holdout"], "holdout")
        self.assertEqual((cov["covered"], cov["total"]), (0, 2))
        self.assertEqual(cov["invalid_sample"], ["h0|a", "h0|b"])
        outputs = {"h0": {"items": [{"key": "a", "label": None}, {"key": "b", "label": None}]}}
        scored = scorer.score(self.units["holdout"], outputs, "holdout")
        self.assertEqual(scored["primary"], 0.0)
        self.assertEqual(len(scored["items"]), 2)
        self.assertEqual(len(scored["gate_failures"]), 2)
        self.assertTrue(all("invalid-reference" in item["tags"] for item in scored["items"]))
        code, result = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse(self.reference_check(result)["ok"])
        self.assertTrue(next(c for c in result["checks"] if c["check"] == "oracle")["ok"])

    def test_reference_payload_requires_a_label_or_nonempty_acceptable_list(self):
        scorer = self.scorer()
        invalid = [{}, {"acceptable": []}, {"acceptable": None}, {"acceptable": "pos"},
                   {"acceptable": {"pos": True}}, {"label": "pos", "acceptable": []},
                   {"exclude": "true"}, {"exclude": 1}, {"exclude": "true", "label": "pos"}]
        for entry in invalid:
            with self.subTest(entry=entry):
                self.assertFalse(scorer._resolved_reference(entry))
        for label in (None, False, 0, "", "pos", ["nested"], {"custom": "label"}):
            with self.subTest(label=label):
                self.write_reference("holdout", [{"key": "h0|a", "label": label},
                                                 {"key": "h0|b", "acceptable": [label]}])
                scored = scorer.score(self.units["holdout"], scorer.oracle(self.units["holdout"], "holdout"), "holdout")
                self.assertEqual(scored["primary"], 1.0)
                self.assertEqual(scored["gate_failures"], [])
                self.assertEqual(scorer.reference_coverage(self.units["holdout"], "holdout")["covered"], 2)

    def test_acceptable_payload_precedes_label_as_in_label_matches(self):
        self.write_reference("holdout", [{"key": "h0|a", "label": "other", "acceptable": [None]},
                                         {"key": "h0|b", "label": "neg"}])
        scorer = self.scorer()
        scored = scorer.score(self.units["holdout"], scorer.oracle(self.units["holdout"], "holdout"), "holdout")
        self.assertEqual(scored["primary"], 1.0)
        self.assertEqual(scored["gate_failures"], [])

    def test_holdout_unresolved_disagreement_blocks_validation(self):
        # A custom scorer can report unresolved entries even when counts otherwise agree.
        source = (self.ws / "eval" / "score.py").read_text(encoding="utf-8")
        source = source.replace('"unresolved": len(unresolved)', '"unresolved": 1 if split == "holdout" else len(unresolved)')
        (self.ws / "eval" / "score.py").write_text(source, encoding="utf-8")
        code, result = self.validate()
        self.assertEqual(code, 1)
        self.assertEqual(self.reference_check(result)["detail"]["splits"][1]["unresolved"], 1)

    def test_explicit_exclusion_is_accounted_for_but_not_scored(self):
        rows = [{"key": "h0|a", "label": "pos"}, {"key": "h0|b", "exclude": True, "note": "known defect"}]
        self.write_reference("holdout", rows)
        scorer = self.scorer()
        cov = scorer.reference_coverage(self.units["holdout"], "holdout")
        self.assertEqual((cov["covered"], cov["total"], cov["excluded"]), (2, 2, 1))
        scored = scorer.score(self.units["holdout"], scorer.oracle(self.units["holdout"], "holdout"), "holdout")
        self.assertEqual(scored["primary"], 1.0)
        self.assertEqual(len(scored["items"]), 1)
        self.assertEqual(scored["gate_failures"], [])
        code, result = self.validate()
        self.assertEqual(code, 0)
        self.assertTrue(self.reference_check(result)["ok"])

    def test_key_omitted_by_both_reference_runs_is_still_missing(self):
        runs = []
        for n in range(2):
            run = self.ws / f"reference_r{n}"
            run.mkdir()
            common.write_json(run / "meta.json", {"run_id": run.name, "purpose": "reference", "split": "dev",
                                                  "candidate": "ref-a", "data_hash": workspace.data_fingerprint(self.ws),
                                                  "config": {"model": "test-model"}})
            for u in self.units["dev"]:
                common.append_jsonl(run / "outputs.jsonl", {"id": u["id"], "status": "ok",
                                                            "output": {"items": [{"key": "a", "label": "pos"}]}})
            runs.append(run)
        with contextlib.redirect_stdout(io.StringIO()):
            reference.merge(self.ws, *runs, "dev")
        cov = self.scorer().reference_coverage(self.units["dev"], "dev")
        self.assertEqual((cov["covered"], cov["total"], cov["unresolved"]), (2, 4, 0))
        code, result = self.validate()
        self.assertEqual(code, 1)
        self.assertFalse(self.reference_check(result)["ok"])

    def test_custom_scorer_legacy_item_count_names_are_supported(self):
        source = (self.ws / "eval" / "score.py").read_text(encoding="utf-8")
        source = source.replace('"covered": len(keys)', '"covered_items": len(keys)').replace('"total": len(keys)', '"total_items": len(keys)')
        (self.ws / "eval" / "score.py").write_text(source, encoding="utf-8")
        code, result = self.validate()
        self.assertEqual(code, 0)
        self.assertEqual(self.reference_check(result)["detail"]["splits"][0]["covered_items"], 4)

    def test_reference_free_scorer_without_hook_still_validates(self):
        (self.ws / "eval" / "score.py").write_text(NO_REFERENCE_SCORER, encoding="utf-8")
        self.units["dev"] = [{"id": f"d{i}"} for i in range(4)]
        common.write_json(self.ws / "units.json", self.units)
        shutil.rmtree(self.ws / "eval" / "reference")
        code, result = self.validate(custom=True)
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])
        self.assertFalse(any(c["check"] == "reference" for c in result["checks"]))

    def test_malformed_coverage_reports_fail_closed(self):
        reports = [None, {}, {"covered": 1, "total": 2, "unresolved": 0},
                   {"covered": 3, "total": 2, "unresolved": 0},
                   {"covered": True, "total": 1, "unresolved": 0},
                   {"covered": "2", "total": 2, "unresolved": 0},
                   {"covered": float("nan"), "total": 2, "unresolved": 0},
                   {"covered": 2, "total": float("inf"), "unresolved": 0},
                   {"covered": 1.5, "total": 1.5, "unresolved": 0},
                   {"covered": -1, "total": -1, "unresolved": 0},
                   {"covered": 0, "total": 0, "unresolved": 0},
                   {"covered": 2, "total": 2, "unresolved": []},
                   {"covered_items": 2, "unresolved": 0}]
        for report in reports:
            with self.subTest(report=report):
                scorer = types.SimpleNamespace(reference_coverage=lambda units, split: report)
                ok, detail = validate_eval._reference_coverage_check(scorer, self.units["dev"], "dev")
                self.assertFalse(ok, detail)
                self.assertIn("error", detail)
        for report in ({"covered": 2.0, "total": 2, "unresolved": 0},
                       {"covered_items": 2, "total_items": 2, "unresolved": 0}):
            scorer = types.SimpleNamespace(reference_coverage=lambda units, split: report)
            self.assertTrue(validate_eval._reference_coverage_check(scorer, self.units["dev"], "dev")[0])

    def test_crashing_coverage_hook_is_a_validation_failure(self):
        scorer = types.SimpleNamespace(reference_coverage=mock.Mock(side_effect=ValueError("bad reference file")))
        ok, detail = validate_eval._reference_coverage_check(scorer, self.units["dev"], "dev")
        self.assertFalse(ok)
        self.assertIn("bad reference file", detail["error"])


if __name__ == "__main__":
    unittest.main()
