"""Conditional design plans cannot silently omit scope, oracles or rewrite checks."""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common
import design_contracts
import init_workspace
from contract_fixtures import item_contracts


class DesignContractsTest(unittest.TestCase):
    def test_complete_conditional_plan_is_not_execution_evidence(self):
        plan = item_contracts()
        self.assertEqual(design_contracts.validate(plan), {
            "assumptions": 2, "contracts": 3, "rewrites": 1, "patterns_with_checks": 6,
        })
        with tempfile.TemporaryDirectory() as temp:
            common.write_json(Path(temp) / design_contracts.CONTRACT_PATH, plan)
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "design_contracts.py"), temp],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("tests were not run", result.stdout)
            self.assertFalse((Path(temp) / "review/task_review.json").exists())

    def test_preconditions_oracles_and_failure_policy_cannot_be_blank(self):
        for field in design_contracts.CONTRACT_FIELDS:
            for value in (None, "", " \n", False, []):
                with self.subTest(field=field, value=value):
                    plan = item_contracts()
                    plan["contracts"][1][field] = value
                    with self.assertRaisesRegex(ValueError, field):
                        design_contracts.validate(plan)

    def test_input_units_domain_and_size_model_are_explicit(self):
        for field in design_contracts.INPUT_FIELDS:
            with self.subTest(field=field):
                plan = item_contracts()
                del plan["input_model"][field]
                with self.assertRaisesRegex(ValueError, field):
                    design_contracts.validate(plan)

    def test_unknown_or_duplicate_assumptions_contracts_and_rewrites_are_rejected(self):
        for collection in ("assumptions", "contracts", "rewrites"):
            with self.subTest(collection=collection):
                plan = item_contracts()
                plan[collection].append(copy.deepcopy(plan[collection][0]))
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    design_contracts.validate(plan)
        for field, values in (("assumptions", ["missing"]), ("assumptions", ["A1", "A1"])):
            plan = item_contracts()
            plan["contracts"][0][field] = values
            with self.assertRaisesRegex(ValueError, "unknown or duplicate"):
                design_contracts.validate(plan)
        plan = item_contracts()
        plan["rewrites"][0]["validation_contracts"] = ["not-a-contract"]
        with self.assertRaisesRegex(ValueError, "validation_contracts"):
            design_contracts.validate(plan)

    def test_rejection_only_tests_cannot_stand_in_for_valid_difficult_inputs(self):
        plan = item_contracts()
        for contract in plan["contracts"]:
            contract["input_class"] = "invalid"
        with self.assertRaisesRegex(ValueError, "valid-input"):
            design_contracts.validate(plan)

    def test_not_applicable_requires_a_reason_and_is_not_an_executed_pass(self):
        plan = item_contracts()
        plan["coverage"]["prompt_context"] = {"not_applicable": "Code-only design; no model sees input text."}
        self.assertEqual(design_contracts.validate(plan)["patterns_with_checks"], 5)
        for invalid in ({}, {"not_applicable": ""}, {"contracts": []},
                        {"contracts": ["missing"]}, {"contracts": ["C1", "C1"]},
                        {"contracts": ["C1"], "not_applicable": "No need"}):
            with self.subTest(invalid=invalid):
                plan["coverage"]["prompt_context"] = invalid
                with self.assertRaises(ValueError):
                    design_contracts.validate(plan)
        plan["coverage"] = {key: {"not_applicable": "Everything is assumed safe."}
                            for key in design_contracts.PATTERNS}
        with self.assertRaisesRegex(ValueError, "every pattern"):
            design_contracts.validate(plan)

    def test_dropped_coverage_patterns_and_invented_enums_do_not_silently_pass(self):
        plan = item_contracts()
        del plan["coverage"]["growth"]
        with self.assertRaisesRegex(ValueError, "coverage must address"):
            design_contracts.validate(plan)
        for field in ("kind", "input_class"):
            plan = item_contracts()
            plan["contracts"][0][field] = "unconditional-pass"
            with self.assertRaisesRegex(ValueError, field):
                design_contracts.validate(plan)

    def test_simple_tasks_can_explain_why_no_rewrite_is_justified(self):
        plan = item_contracts()
        plan["rewrites"] = []
        with self.assertRaisesRegex(ValueError, "no_rewrite_reason"):
            design_contracts.validate(plan)
        plan["no_rewrite_reason"] = "Identity copy in code; no model and no duplicated decisions to remove."
        self.assertEqual(design_contracts.validate(plan)["rewrites"], 0)
        plan["rewrites"] = item_contracts()["rewrites"]
        with self.assertRaisesRegex(ValueError, "not both"):
            design_contracts.validate(plan)

    def test_changed_measurement_results_cannot_be_embedded_in_frozen_contracts(self):
        for field in ("status", "result"):
            plan = item_contracts()
            plan["contracts"][0][field] = "passed"
            with self.assertRaisesRegex(ValueError, "contract_results.jsonl"):
                design_contracts.validate(plan)

    def test_malformed_types_and_duplicate_json_keys_fail_closed(self):
        for bad in (None, [], True, {"version": True}, {"version": 2}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                design_contracts.validate(bad)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / design_contracts.CONTRACT_PATH
            target.parent.mkdir()
            for text in ('{"version":1,"version":1}', '{"version":1,"input_model":{"x":1,"x":2}}'):
                target.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                    design_contracts.load(temp)

    def test_migration_seeds_an_incomplete_plan_without_resetting_user_state(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = Path(temp)
            common.write_json(ws / "charter.json", {
                "task": "existing task", "backend": {"backend": "codex", "model": "custom"},
                "limits": {"max_cost_usd": 3}, "efficiency_metric": "tokens_per_unit.output",
            })
            common.append_jsonl(ws / "ledger.jsonl", {"event": "stop", "reason": "user stopped"})
            common.write_json(ws / "review/task_review.json", {"version": 1, "status": "PASS"})
            ledger_before = (ws / "ledger.jsonl").read_bytes()
            review_before = (ws / "review/task_review.json").read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                init_workspace.migrate(ws)
            self.assertEqual((ws / "ledger.jsonl").read_bytes(), ledger_before)
            self.assertEqual((ws / "review/task_review.json").read_bytes(), review_before)
            ch = common.read_json(ws / "charter.json")
            self.assertEqual(ch["limits"]["max_cost_usd"], 3)
            self.assertEqual(ch["backend"]["model"], "custom")
            self.assertEqual(ch["efficiency_metric"], "tokens_per_unit.output")
            with self.assertRaises(ValueError):
                design_contracts.load(ws)
            common.write_json(ws / design_contracts.CONTRACT_PATH, item_contracts())
            before = (ws / design_contracts.CONTRACT_PATH).read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                init_workspace.migrate(ws)
            self.assertEqual((ws / design_contracts.CONTRACT_PATH).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
