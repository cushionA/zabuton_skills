"""Reference merging refuses ambiguous output before touching existing labels."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import common
import reference
import workspace


DATASET = '''
import json
from pathlib import Path
WS = Path(__file__).resolve().parents[1]
def load_units(split):
    return json.loads((WS / "units.json").read_text(encoding="utf-8"))[split]
'''


class ReferenceContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name)
        (self.ws / "eval/reference").mkdir(parents=True)
        common.write_json(self.ws / "charter.json", {"skill_dir": str(SKILL)})
        shutil.copy2(SKILL / "assets/workspace_template/eval/score.py", self.ws / "eval/score.py")
        (self.ws / "eval/dataset.py").write_text(DATASET, encoding="utf-8")
        self.units = [{"id": "u", "input": {"item_keys": ["a", "b"]}}]
        common.write_json(self.ws / "units.json", {"dev": self.units})
        self.agreed = self.ws / "eval/reference/dev.jsonl"
        self.disagreements = self.ws / "eval/reference/dev.disagreements.jsonl"
        self.agreed.write_text('{"key":"u|a","label":"previous-agreement"}\n', encoding="utf-8")
        self.disagreements.write_text('{"key":"u|b","a":"previous-a","b":"previous-b"}\n', encoding="utf-8")
        self.run_a = self.ws / "reference-a"
        self.run_b = self.ws / "reference-b"
        for run in (self.run_a, self.run_b):
            run.mkdir()
            self.write_meta(run)
            self.write_records(run, [self.record()])

    def write_meta(self, run, **updates):
        meta = {"run_id": run.name, "purpose": "reference", "split": "dev", "candidate": f"ref-{run.name}",
                "candidate_hash": f"hash-{run.name}", "config_hash": "config", "data_hash": workspace.data_fingerprint(self.ws),
                "config": {"backend": "claude", "model": "test-model"}}
        meta.update(updates)
        common.write_json(run / "meta.json", meta)

    def record(self, output=None, unit="u"):
        if output is None:
            output = {"items": [{"key": "a", "label": "pos"}, {"key": "b", "label": "neg"}]}
        return {"id": unit, "status": "ok", "output": output}

    def write_records(self, run, records):
        (run / "outputs.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    def merge(self):
        with contextlib.redirect_stdout(io.StringIO()):
            reference.merge(self.ws, self.run_a, self.run_b, "dev")

    def assert_refused_without_replacing_reference(self, phrase, run=None, unit=None):
        before = {path: path.read_bytes() for path in (self.agreed, self.disagreements)}
        with self.assertRaises(SystemExit) as ctx:
            self.merge()
        message = str(ctx.exception)
        self.assertIn("REFUSED", message)
        self.assertIn(phrase, message)
        if run:
            self.assertIn(run.name, message)
        if unit:
            self.assertIn(unit, message)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_malformed_duplicate_or_null_rows_in_either_run_preserve_old_reference(self):
        bad_outputs = [
            {"items": None},
            {"items": [{"key": "a", "label": "wrong"}, {"key": "a", "label": "pos"},
                       {"key": "b", "label": "neg"}]},
            {"items": [{"key": "a"}, {"key": "b", "label": "neg"}]},
        ]
        for run in (self.run_a, self.run_b):
            for output in bad_outputs:
                with self.subTest(run=run.name, output=output):
                    for current in (self.run_a, self.run_b):
                        self.write_records(current, [self.record()])
                    self.write_records(run, [self.record(output)])
                    self.assert_refused_without_replacing_reference("invalid reference output", run, "u")

    def test_duplicate_unit_records_are_not_silently_overwritten(self):
        for run in (self.run_a, self.run_b):
            with self.subTest(run=run.name):
                for current in (self.run_a, self.run_b):
                    self.write_records(current, [self.record()])
                self.write_records(run, [self.record(), self.record()])
                self.assert_refused_without_replacing_reference("duplicate reference unit", run, "u")

    def test_distinct_units_cannot_create_the_same_flattened_reference_key(self):
        # The template joins unit/key with '|'; do not silently overwrite a
        # collision when distinct input ids or item keys contain that delimiter.
        units = [{"id": "u", "input": {"item_keys": ["a|x"]}},
                 {"id": "u|a", "input": {"item_keys": ["x"]}}]
        common.write_json(self.ws / "units.json", {"dev": units})
        rows = [self.record({"items": [{"key": "a|x", "label": "first"}]}),
                self.record({"items": [{"key": "x", "label": "second"}]}, unit="u|a")]
        for run in (self.run_a, self.run_b):
            self.write_records(run, rows)
        self.assert_refused_without_replacing_reference("duplicate flattened reference key", self.run_a, "u|a|x")

    def test_invalid_record_or_unknown_unit_is_refused_before_replacing_files(self):
        cases = [([None], "invalid output record"),
                 ([self.record(unit="invented")], "unknown reference unit"),
                 ([self.record(unit=None)], "unknown reference unit")]
        for records, phrase in cases:
            with self.subTest(records=records):
                self.write_records(self.run_a, records)
                self.assert_refused_without_replacing_reference(phrase, self.run_a)

    def test_invalid_custom_flatten_contract_is_refused_not_salvaged(self):
        dataset, scorer = workspace.load_eval(self.ws)
        for values in (None, [], "not a mapping", {1: "not a string key"}):
            with self.subTest(values=values), \
                    mock.patch.object(reference, "load_eval", return_value=(dataset, scorer)), \
                    mock.patch.object(scorer, "flatten", return_value=values):
                self.assert_refused_without_replacing_reference("flatten()")
        for error in (ValueError("ambiguous label"), TypeError("bad label type")):
            with self.subTest(error=error), \
                    mock.patch.object(reference, "load_eval", return_value=(dataset, scorer)), \
                    mock.patch.object(scorer, "flatten", side_effect=error):
                self.assert_refused_without_replacing_reference("invalid reference output", self.run_a, "u")

    def test_partial_well_formed_rows_merge_then_coverage_reports_missing_key(self):
        partial = {"items": [{"key": "a", "label": "pos"}]}
        for run in (self.run_a, self.run_b):
            self.write_records(run, [self.record(partial)])
        self.merge()
        agreed = common.read_jsonl(self.agreed)
        self.assertEqual([(row["key"], row["label"]) for row in agreed], [("u|a", "pos")])
        self.assertEqual(common.read_jsonl(self.disagreements), [])
        _, scorer = workspace.load_eval(self.ws)
        coverage = scorer.reference_coverage(self.units, "dev")
        self.assertEqual((coverage["covered"], coverage["total"], coverage["unresolved"]), (1, 2, 0))
        self.assertEqual(coverage["missing_sample"], ["u|b"])

    def test_explicit_null_and_false_labels_are_answers_not_missing_rows(self):
        output = {"items": [{"key": "a", "label": None}, {"key": "b", "label": False}]}
        for run in (self.run_a, self.run_b):
            self.write_records(run, [self.record(output)])
        self.merge()
        rows = common.read_jsonl(self.agreed)
        self.assertEqual({row["key"]: row["label"] for row in rows}, {"u|a": None, "u|b": False})
        _, scorer = workspace.load_eval(self.ws)
        scored = scorer.score(self.units, {"u": output}, "dev")
        self.assertEqual(scored["primary"], 1)
        self.assertEqual(scored["gate_failures"], [])

    def test_boolean_number_disagreement_cannot_become_reference_agreement(self):
        for left, right in ((False, 0), (True, 1), ({"x": False}, {"x": 0}),
                            ([None, True], [None, 1])):
            with self.subTest(left=left, right=right):
                for run, label in ((self.run_a, left), (self.run_b, right)):
                    output = {"items": [{"key": "a", "label": label}, {"key": "b", "label": "neg"}]}
                    self.write_records(run, [self.record(output)])
                self.merge()
                self.assertEqual([row["key"] for row in common.read_jsonl(self.agreed)], ["u|b"])
                disagreements = common.read_jsonl(self.disagreements)
                self.assertEqual(len(disagreements), 1)
                self.assertEqual(disagreements[0]["key"], "u|a")
                self.assertIs(type(disagreements[0]["a"]), type(left))
                _, scorer = workspace.load_eval(self.ws)
                coverage = scorer.reference_coverage(self.units, "dev")
                self.assertEqual((coverage["covered"], coverage["total"], coverage["unresolved"]), (1, 2, 1))

    def test_equal_json_numbers_and_object_key_order_can_agree(self):
        for left, right in ((1, 1.0), ({"a": 1, "b": [False]}, {"b": [False], "a": 1.0})):
            with self.subTest(left=left, right=right):
                for run, label in ((self.run_a, left), (self.run_b, right)):
                    output = {"items": [{"key": "a", "label": label}, {"key": "b", "label": "neg"}]}
                    self.write_records(run, [self.record(output)])
                self.merge()
                self.assertEqual(len(common.read_jsonl(self.agreed)), 2)
                self.assertEqual(common.read_jsonl(self.disagreements), [])

    def test_same_run_twice_cannot_become_a_reference(self):
        self.run_b = self.run_a
        self.assert_refused_without_replacing_reference("same run")
        copy = self.ws / "reference-copy"
        shutil.copytree(self.run_a, copy)
        self.run_b = copy
        self.assert_refused_without_replacing_reference("cannot verify itself")

    def test_only_complete_current_reference_runs_of_the_split_merge(self):
        cases = ((dict(purpose="smoke"), "--purpose reference"), (dict(purpose=None), "--purpose reference"),
                 (dict(split="holdout"), "not 'dev'"), (dict(data_hash="old"), "different source data"))
        for updates, phrase in cases:
            with self.subTest(updates=updates):
                self.write_meta(self.run_b, **updates)
                self.assert_refused_without_replacing_reference(phrase, run=self.run_b)
        self.write_meta(self.run_b)
        (self.run_b / "meta.json").unlink()
        self.assert_refused_without_replacing_reference("no meta.json", run=self.run_b)
        self.write_meta(self.run_b)
        common.write_json(self.run_b / "summary.json", {"quota_exhausted": True})
        self.assert_refused_without_replacing_reference("incomplete", run=self.run_b)

    def test_candidate_in_the_experiment_ledger_cannot_label_itself(self):
        self.write_meta(self.run_b, candidate="c003-tuned")
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "propose", "exp": "E003", "candidate": "c003-tuned"})
        self.assert_refused_without_replacing_reference("experiment ledger", run=self.run_b)

    def test_merge_records_the_runs_it_used(self):
        self.merge()
        record = common.read_json(self.ws / "eval/reference/dev.merge.json")
        self.assertEqual([run["run_id"] for run in record["runs"]], ["reference-a", "reference-b"])
        self.assertEqual((record["agreed"], record["disagreements"]), (2, 0))
        self.assertFalse(record["same_candidate_and_config"])
        self.write_meta(self.run_b, candidate_hash="hash-reference-a")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            reference.merge(self.ws, self.run_a, self.run_b, "dev")
        self.assertTrue(common.read_json(self.ws / "eval/reference/dev.merge.json")["same_candidate_and_config"])
        self.assertIn("self-consistency", out.getvalue())


if __name__ == "__main__":
    unittest.main()
