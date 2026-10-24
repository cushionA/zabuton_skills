"""Malformed model output cannot crash the scorer or become a successful reference."""
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import common
import workspace


class OutputContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        (self.ws / "eval" / "reference").mkdir(parents=True)
        common.write_json(self.ws / "charter.json", {"skill_dir": str(SKILL)})
        shutil.copy2(SKILL / "assets/workspace_template/eval/score.py", self.ws / "eval/score.py")
        self.scorer = workspace.load_module(self.ws / "eval/score.py", "output_contract_scorer")
        self.unit = {"id": "u", "input": {"item_keys": ["a", "b"]}}
        self.write_reference([{"key": "u|a", "label": "pos"}, {"key": "u|b", "label": "neg"}])

    def write_reference(self, rows, split="dev"):
        (self.ws / f"eval/reference/{split}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def valid(self):
        return {"items": [{"key": "a", "label": "pos"}, {"key": "b", "label": "neg"}]}

    def score(self, output):
        return self.scorer.score([self.unit], {"u": output}, "dev")

    def assert_invalid(self, output):
        result = self.score(output)
        self.assertEqual(result["primary"], 0.0)
        self.assertEqual(result["metrics"]["n_items"], 2)
        self.assertEqual({item["id"] for item in result["items"]}, {"u|a", "u|b"})
        self.assertTrue(result["gate_failures"])
        self.assertTrue(all(item["score"] == 0 for item in result["items"]))
        return result

    def test_exactly_one_valid_answer_per_item_succeeds(self):
        result = self.score(self.valid())
        self.assertEqual(result["primary"], 1.0)
        self.assertEqual(result["metrics"]["answered"], 1.0)
        self.assertEqual(result["gate_failures"], [])
        self.assertEqual(self.scorer.flatten(self.unit, self.valid()), {"u|a": "pos", "u|b": "neg"})

    def test_malformed_output_or_items_never_crash_or_shrink_denominator(self):
        for output in (None, [], "text", 3, False, {}, {"items": None}, {"items": "rows"},
                       {"items": {}}, {"items": 2}, {"items": False}):
            with self.subTest(output=output):
                self.assert_invalid(output)
                with self.assertRaises(ValueError):
                    self.scorer.flatten(self.unit, output)

    def test_malformed_item_key_and_label_are_not_silently_skipped(self):
        invalid_items = [None, "row", 1, [], {}, {"label": "pos"},
                         {"key": None, "label": "pos"}, {"key": 1, "label": "pos"},
                         {"key": False, "label": "pos"}, {"key": ["a"], "label": "pos"},
                         {"key": "a"}, {"key": "a", "label": {1, 2}},
                         {"key": "a", "label": float("nan")},
                         {"key": "a", "label": float("inf")}]
        for item in invalid_items:
            with self.subTest(item=item):
                output = {"items": [*self.valid()["items"], item]}
                self.assert_invalid(output)
                with self.assertRaises(ValueError):
                    self.scorer.flatten(self.unit, output)

    def test_conflicting_and_identical_duplicates_cannot_win_by_last_value(self):
        for label in ("wrong", "pos"):
            output = self.valid()
            output["items"].insert(0, {"key": "a", "label": label})
            with self.subTest(label=label):
                result = self.assert_invalid(output)
                self.assertTrue(any("duplicate key" in gate for gate in result["gate_failures"]))
                with self.assertRaisesRegex(ValueError, "duplicate key"):
                    self.scorer.flatten(self.unit, output)

    def test_unknown_key_invalidates_an_otherwise_correct_unit(self):
        output = self.valid()
        output["items"].append({"key": "invented", "label": "pos"})
        result = self.assert_invalid(output)
        self.assertTrue(any("unknown key" in gate for gate in result["gate_failures"]))
        with self.assertRaisesRegex(ValueError, "unknown key"):
            self.scorer.flatten(self.unit, output)

    def test_missing_expected_row_is_zero_and_a_gate_failure(self):
        output = {"items": [{"key": "a", "label": "pos"}]}
        result = self.score(output)
        self.assertEqual(result["primary"], 0.5)
        self.assertEqual(result["metrics"]["n_items"], 2)
        missing = next(item for item in result["items"] if item["id"] == "u|b")
        self.assertEqual(missing["score"], 0)
        self.assertIn("missing", missing["tags"])
        self.assertTrue(any("missing output for u|b" in gate for gate in result["gate_failures"]))
        # Reference merging can still adjudicate a well-formed partial response.
        self.assertEqual(self.scorer.flatten(self.unit, output), {"u|a": "pos"})
        self.assert_invalid({"items": []})
        absent = self.scorer.score([self.unit], {}, "dev")
        self.assertEqual(absent["primary"], 0)
        self.assertEqual(len(absent["items"]), 2)
        self.assertTrue(absent["gate_failures"])

    def test_present_null_false_zero_and_custom_json_labels_are_valid(self):
        for label in (None, False, 0, "", ["nested", None], {"class": "pos", "extras": []}):
            with self.subTest(label=label):
                self.write_reference([{"key": "u|a", "label": label}, {"key": "u|b", "acceptable": [label]}])
                output = {"items": [{"key": "a", "label": label}, {"key": "b", "label": label}]}
                result = self.score(output)
                self.assertEqual(result["primary"], 1.0)
                self.assertEqual(result["gate_failures"], [])
                self.assertEqual(self.scorer.flatten(self.unit, output), {"u|a": label, "u|b": label})
                del output["items"][0]["label"]
                self.assert_invalid(output)

    def test_json_label_equality_separates_booleans_from_numbers_recursively(self):
        unequal = [(False, 0), (True, 1), (False, 0.0), (True, 1.0),
                   ({"x": False}, {"x": 0}), ([False, {"x": True}], [0, {"x": 1}]),
                   (None, "null"), (1, "1"), ([], {}), ([1, 2], [2, 1])]
        equal = [(None, None), (False, False), (1, 1.0), (0, -0.0), ("label", "label"),
                 ({"x": [None, False, 1], "y": "a"}, {"y": "a", "x": [None, False, 1.0]})]
        for left, right in unequal:
            with self.subTest(left=left, right=right):
                self.assertFalse(common.json_label_equal(left, right))
                self.assertFalse(common.json_label_equal(right, left))
                self.assertFalse(common.label_matches(left, {"label": right}))
                self.assertFalse(common.label_matches(left, {"acceptable": [right]}))
        for left, right in equal:
            with self.subTest(left=left, right=right):
                self.assertTrue(common.json_label_equal(left, right))
                self.assertTrue(common.label_matches(left, {"label": right}))
                self.assertTrue(common.label_matches(left, {"acceptable": [right]}))

    def test_invalid_json_labels_and_sets_never_compare_equal_or_resolve(self):
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        invalid = [float("nan"), float("inf"), float("-inf"), ("tuple",), {"set"},
                   {1: "nonstring key"}, {"x": [float("nan")]}, object(), cyclic_list, cyclic_dict]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(common.json_label_equal(value, value))
                self.assertFalse(common.label_matches(value, {"label": value}))
                self.assertFalse(common.label_matches("valid", {"acceptable": ["valid", value]}))
                self.assertFalse(self.scorer._resolved_reference({"label": value}))
                self.assertFalse(self.scorer._resolved_reference({"acceptable": ["valid", value]}))
                self.assert_invalid({"items": [{"key": "a", "label": value}, {"key": "b", "label": "neg"}]})
        for entry in ({}, {"acceptable": None}, {"acceptable": []}, {"acceptable": "label"}):
            with self.subTest(entry=entry):
                self.assertFalse(common.label_matches(None, entry))

    def test_type_distinct_labels_are_wrong_answers_without_schema_errors(self):
        self.write_reference([{"key": "u|a", "label": 0}, {"key": "u|b", "acceptable": [{"x": [1]}]}])
        output = {"items": [{"key": "a", "label": False}, {"key": "b", "label": {"x": [True]}}]}
        scored = self.score(output)
        self.assertEqual(scored["primary"], 0)
        self.assertEqual(scored["gate_failures"], [])  # Valid labels, but a different value/type.
        self.assertTrue(all("wrong" in item["tags"] for item in scored["items"]))
        output["items"][0]["label"] = 0.0
        output["items"][1]["label"] = {"x": [1.0]}
        self.assertEqual(self.score(output)["primary"], 1)

    def test_nonfinite_holdout_reference_labels_are_not_counted_as_resolved(self):
        for value in (float("nan"), float("inf"), float("-inf"), {"x": [float("nan")]}):
            with self.subTest(value=value):
                self.write_reference([{"key": "u|a", "label": value},
                                      {"key": "u|b", "acceptable": ["neg", value]}], split="holdout")
                coverage = self.scorer.reference_coverage([self.unit], "holdout")
                self.assertEqual((coverage["covered"], coverage["total"]), (0, 2))
                self.assertEqual(coverage["invalid_sample"], ["u|a", "u|b"])
                scored = self.scorer.score([self.unit], {"u": self.valid()}, "holdout")
                self.assertEqual(scored["primary"], 0)
                self.assertEqual(len(scored["gate_failures"]), 2)

    def test_excluded_rows_may_be_omitted_but_do_not_hide_invalid_output(self):
        self.write_reference([{"key": "u|a", "label": "pos"}, {"key": "u|b", "exclude": True}])
        output = {"items": [{"key": "a", "label": "pos"}]}
        result = self.score(output)
        self.assertEqual(result["primary"], 1)
        self.assertEqual(result["metrics"]["n_items"], 1)
        self.assertEqual(result["gate_failures"], [])
        output["items"].extend([{"key": "b", "label": "ignored"}] * 2)
        result = self.score(output)
        self.assertEqual(result["primary"], 0)
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(any("duplicate" in failure for failure in result["gate_failures"]))

    def test_same_key_in_different_units_is_not_a_duplicate(self):
        unit_b = {"id": "v", "input": {"item_keys": ["a", "b"]}}
        self.write_reference([{"key": f"{uid}|{key}", "label": label}
                              for uid in ("u", "v") for key, label in (("a", "pos"), ("b", "neg"))])
        result = self.scorer.score([self.unit, unit_b], {"u": self.valid(), "v": self.valid()}, "dev")
        self.assertEqual(result["primary"], 1)
        self.assertEqual(len(result["items"]), 4)
        self.assertEqual(result["gate_failures"], [])

    def test_unknown_output_unit_id_cannot_receive_full_success(self):
        result = self.scorer.score([self.unit], {"u": self.valid(), "invented": self.valid()}, "dev")
        self.assertEqual(result["primary"], 0)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(any("unknown unit ids" in failure for failure in result["gate_failures"]))

    def test_invalid_output_mapping_and_hooks_remain_safe(self):
        for outputs in (None, [], "not a mapping"):
            with self.subTest(outputs=outputs):
                result = self.scorer.score([self.unit], outputs, "dev")
                self.assertEqual(result["primary"], 0)
                self.assertEqual(len(result["items"]), 2)
                self.assertTrue(result["gate_failures"])
        oracle = self.scorer.oracle([self.unit], "dev")
        self.assertEqual(self.scorer.score([self.unit], oracle, "dev")["primary"], 1)
        bad = self.scorer.corrupt(oracle["u"], random.Random(1))
        self.assertLess(self.score(bad)["primary"], 1)


if __name__ == "__main__":
    unittest.main()
