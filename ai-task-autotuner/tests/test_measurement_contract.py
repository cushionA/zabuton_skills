"""Measurement policies and source identity cannot inherit unrelated validation."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common
import ledger
import quality_gate
import run_experiment
import workspace


class MeasurementContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name) / "ws"
        (self.ws / "eval").mkdir(parents=True)
        (self.ws / "data.json").write_text('{"id":"a"}\n', encoding="utf-8")
        for name in ("dataset.py", "score.py"):
            (self.ws / "eval" / name).write_text("# measurement fixture\n", encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["a"], "holdout": ["b"]})
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": ["data.json"], "aux": []},
            "primary": {"direction": "max", "oracle_min": 0.999}, "guards": [],
        })

    def test_primary_and_guard_changes_invalidate_validation_without_regenerating_inputs(self):
        original = workspace.charter(self.ws)
        data_hash = workspace.data_fingerprint(self.ws)
        eval_hash = workspace.eval_hash(self.ws)
        common.write_json(self.ws / "eval" / "validation.json", {"passed": True, "eval_hash": eval_hash})
        self.assertTrue(run_experiment._validation_ok(self.ws)[0])
        for field, value in (("primary", {"direction": "min"}),
                             ("primary", {"direction": "max", "oracle_min": 0.95}),
                             ("primary", {"direction": "max", "item_direction": "min"}),
                             ("guards", [{"metric": "false_accept", "max": 0.01}])):
            with self.subTest(field=field, value=value):
                common.write_json(self.ws / "charter.json", {**original, field: value})
                self.assertEqual(workspace.data_fingerprint(self.ws), data_hash)
                self.assertNotEqual(workspace.eval_hash(self.ws), eval_hash)
                self.assertFalse(run_experiment._validation_ok(self.ws)[0])

    def test_relative_source_is_workspace_relative_and_relocates_by_contents(self):
        relative_hash = workspace.data_fingerprint(self.ws)
        ch = workspace.charter(self.ws)
        ch["data"]["inputs"] = [str(self.ws / "data.json")]
        common.write_json(self.ws / "charter.json", ch)
        self.assertEqual(workspace.data_fingerprint(self.ws), relative_hash)
        source = self.ws / "data.json"
        copy = Path(self.temp.name) / "same.json"
        copy.write_bytes(source.read_bytes())
        ch["data"]["inputs"] = [str(copy)]
        common.write_json(self.ws / "charter.json", ch)
        self.assertEqual(workspace.data_fingerprint(self.ws), relative_hash)

    def test_nested_linked_source_bytes_and_empty_directories_are_measured(self):
        root = self.ws / "inputs"
        root.mkdir()
        target = Path(self.temp.name) / "linked-data"
        target.mkdir()
        (target / "rows.json").write_text("old", encoding="utf-8")
        try:
            (root / "link").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(str(e))
        ch = workspace.charter(self.ws)
        ch["data"]["inputs"] = ["inputs"]
        common.write_json(self.ws / "charter.json", ch)
        before = workspace.data_fingerprint(self.ws)
        (target / "rows.json").write_text("new", encoding="utf-8")
        changed = workspace.data_fingerprint(self.ws)
        self.assertNotEqual(before, changed)
        (root / "empty").mkdir()
        self.assertNotEqual(changed, workspace.data_fingerprint(self.ws))

    def test_ledger_recommends_audit_when_source_or_evidence_is_stale(self):
        audit = self.ws / "audit"
        audit.mkdir()
        for name in ("profile.md", "data_audit.md"):
            (audit / name).write_text("audit fixture\n", encoding="utf-8")
        common.append_jsonl(audit / "close_read.jsonl", {"id": "a", "verdict": "ok"})
        (audit / "checks.py").write_text("def run_checks(ctx): return []\n", encoding="utf-8")
        gate = quality_gate.evaluate(self.ws)
        self.assertEqual(gate["status"], "PASS")
        common.write_json(audit / "quality_gate.json", gate)
        with mock.patch.object(ledger, "task_review_allows_runs", return_value=(True, "fixture")):
            self.assertIsNone(ledger._gate(self.ws))
            common.append_jsonl(audit / "close_read.jsonl", {"id": "b", "verdict": "defect"})
            self.assertEqual(ledger._gate(self.ws)[0], "AUDIT")


if __name__ == "__main__":
    unittest.main()
