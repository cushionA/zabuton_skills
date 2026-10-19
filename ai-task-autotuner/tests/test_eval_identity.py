"""Measurement identity follows input bytes, roles and scope rather than locations."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import quality_gate  # noqa: E402
import validate_eval  # noqa: E402
import workspace  # noqa: E402


DATASET = '''
import json
from pathlib import Path
WS = Path(__file__).resolve().parents[1]
def load_units(split):
    ch = json.loads((WS / "charter.json").read_text())
    records = json.loads(Path(ch["data"]["inputs"][0]).read_text())
    ids = json.loads((WS / "eval" / "splits.json").read_text())[split]
    return [{"id": r["id"], "input": r} for r in records if r["id"] in ids]
def group_of(unit):
    return unit["id"]
'''

SCORER = '''
def score(units, outputs, split):
    items = [{"id": u["id"], "unit": u["id"],
              "score": float(outputs.get(u["id"]) == u["input"]["target"])} for u in units]
    return {"primary": sum(i["score"] for i in items) / len(items) if items else 0,
            "items": items, "metrics": {}, "gate_failures": []}
def oracle(units, split):
    return {u["id"]: u["input"]["target"] for u in units}
'''


class EvalIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        (self.ws / "eval" / "reference").mkdir(parents=True)
        self.data = self.tmp / "inputs.json"
        common.write_json(self.data, [{"id": f"u{i}", "target": 1} for i in range(5)])
        common.write_json(self.ws / "charter.json", {
            "data": {"inputs": [str(self.data)], "aux": [], "scope_notes": "all five records"},
            "primary": {"ceiling": 1.0}})
        (self.ws / "eval" / "dataset.py").write_text(DATASET)
        (self.ws / "eval" / "score.py").write_text(SCORER)
        common.write_json(self.ws / "eval" / "splits.json", {"dev": [f"u{i}" for i in range(4)], "holdout": ["u4"]})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def data_config(self, **fields):
        ch = common.read_json(self.ws / "charter.json")
        ch["data"].update(fields)
        common.write_json(self.ws / "charter.json", ch)

    def change_input(self):
        records = common.read_json(self.data)
        records[0]["target"] = 2
        common.write_json(self.data, records)

    def make_runs(self, suffix=""):
        dataset, scorer = workspace.load_eval(self.ws)
        units = dataset.load_units("dev")
        runs = []
        for i in range(2):
            run = self.ws / "runs" / f"baseline{i}{suffix}"
            run.mkdir(parents=True)
            common.write_json(run / "meta.json", {
                "unit_ids": [u["id"] for u in units], "split": "dev", "purpose": "baseline",
                "data_hash": workspace.data_fingerprint(self.ws), "eval_hash": workspace.eval_hash(self.ws)})
            common.write_json(run / "score.json", scorer.score(units, {u["id"]: 0 for u in units}, "dev"))
            common.write_json(run / "summary.json", {"harness_error_rate": 0})
            for u in units:
                common.append_jsonl(run / "outputs.jsonl", {"id": u["id"], "status": "ok", "output": 0})
            runs.append(run)
        return runs

    def validate(self, runs, expected=0, renoise=False):
        cmd = [sys.executable, "-B", str(SCRIPTS / "validate_eval.py"), str(self.ws),
               "--renoise" if renoise else "--runs", *map(str, runs)]
        if not renoise:
            cmd += ["--spotcheck", "Read all four baseline outputs; all targets and scores verified."]
        result = subprocess.run(cmd, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_current_bytes_change_identity_without_relying_on_audit_cache(self):
        before = workspace.data_fingerprint(self.ws), workspace.eval_hash(self.ws)
        common.write_json(self.ws / "audit" / "quality_gate.json", {"status": "PASS", "data_hashes": {}})
        self.change_input()
        self.assertNotEqual(before[0], workspace.data_fingerprint(self.ws))
        self.assertNotEqual(before[1], workspace.eval_hash(self.ws))

    def test_auxiliary_roles_order_subsets_and_scope_are_part_of_identity(self):
        aux = self.tmp / "lookup.json"
        aux.write_text('{"lookup": 1}')
        self.data_config(aux=[str(aux)])
        first = workspace.data_fingerprint(self.ws)
        aux.write_text('{"lookup": 2}')
        self.assertNotEqual(first, workspace.data_fingerprint(self.ws))
        first = workspace.data_fingerprint(self.ws)
        self.data_config(inputs=[str(aux)], aux=[str(self.data)])
        self.assertNotEqual(first, workspace.data_fingerprint(self.ws))
        self.data_config(inputs=[str(self.data), str(aux)], aux=[])
        first = workspace.data_fingerprint(self.ws)
        self.data_config(inputs=[str(aux), str(self.data)])
        self.assertNotEqual(first, workspace.data_fingerprint(self.ws))
        self.data_config(inputs=[str(self.data)])
        self.assertNotEqual(first, workspace.data_fingerprint(self.ws))
        first = workspace.data_fingerprint(self.ws)
        self.data_config(scope_notes="first four only")
        self.assertNotEqual(first, workspace.data_fingerprint(self.ws))

    def test_content_preserving_relocation_and_workspace_copy_keep_identity(self):
        original = workspace.data_fingerprint(self.ws), workspace.eval_hash(self.ws)
        copied = self.tmp / "copied_workspace"
        shutil.copytree(self.ws, copied)
        moved = self.tmp / "renamed-input.json"
        shutil.copy2(self.data, moved)
        ch = common.read_json(copied / "charter.json")
        ch["data"]["inputs"] = [str(moved)]
        common.write_json(copied / "charter.json", ch)
        self.assertEqual(original, (workspace.data_fingerprint(copied), workspace.eval_hash(copied)))

    def test_source_directory_tree_and_missing_sources_change_identity(self):
        tree = self.tmp / "records"
        tree.mkdir()
        (tree / "part.json").write_text("[]")
        self.data_config(inputs=[str(tree)])
        before = workspace.data_fingerprint(self.ws)
        (tree / "part.json").rename(tree / "other.json")
        self.assertNotEqual(before, workspace.data_fingerprint(self.ws))
        before = workspace.data_fingerprint(self.ws)
        shutil.rmtree(tree)
        self.assertNotEqual(before, workspace.data_fingerprint(self.ws))

    def test_only_scorer_and_reference_changes_can_reuse_generation_identity(self):
        data_before, eval_before = workspace.data_fingerprint(self.ws), workspace.eval_hash(self.ws)
        with (self.ws / "eval" / "score.py").open("a") as f:
            f.write("\n# revised scorer\n")
        (self.ws / "eval" / "reference" / "dev.json").write_text('{"u0": 1}')
        self.assertEqual(data_before, workspace.data_fingerprint(self.ws))
        self.assertNotEqual(eval_before, workspace.eval_hash(self.ws))
        with (self.ws / "eval" / "dataset.py").open("a") as f:
            f.write("\n# revised input construction\n")
        self.assertNotEqual(data_before, workspace.data_fingerprint(self.ws))
        before = workspace.data_fingerprint(self.ws)
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["u0"], "holdout": ["u1"]})
        self.assertNotEqual(before, workspace.data_fingerprint(self.ws))

    def test_validator_rejects_old_data_even_after_reaudit_and_eval_refresh(self):
        runs = self.make_runs()
        self.validate(runs)
        self.change_input()
        # A new audit can pass, and rescoring can refresh eval_hash; neither
        # proves old model outputs were generated from the newly supplied data.
        audit = self.ws / "audit"
        audit.mkdir(exist_ok=True)
        (audit / "checks.py").write_text("def run_checks(ctx): return []\n")
        (audit / "profile.md").write_text("Five records profiled.\n")
        (audit / "data_audit.md").write_text("Changed target inspected.\n")
        common.append_jsonl(audit / "close_read.jsonl", {"id": "u0", "verdict": "ok"})
        gate = quality_gate.evaluate(self.ws)
        self.assertEqual(gate["status"], "PASS")
        common.write_json(audit / "quality_gate.json", gate)
        for run in runs:
            meta = common.read_json(run / "meta.json")
            meta["eval_hash"] = workspace.eval_hash(self.ws)
            common.write_json(run / "meta.json", meta)
        self.validate(runs, expected=1)
        result = common.read_json(self.ws / "eval" / "validation.json")
        identity = next(c for c in result["checks"] if c["check"] == "identity")
        self.assertFalse(identity["ok"])
        self.assertIn("rerun", str(identity["detail"]))
        self.validate(self.make_runs("-new"))

    def test_legacy_runs_without_generation_snapshot_require_rerun(self):
        runs = self.make_runs()
        for run in runs:
            meta = common.read_json(run / "meta.json")
            del meta["data_hash"]
            common.write_json(run / "meta.json", meta)
        self.validate(runs, expected=1)
        ok, detail = validate_eval._run_identity_check(self.ws, runs)
        self.assertFalse(ok)
        self.assertIn("legacy", str(detail))

    def test_noise_cannot_be_refreshed_from_old_data(self):
        runs = self.make_runs()
        self.validate(runs)
        before = common.read_json(self.ws / "charter.json")["noise"]
        self.change_input()
        self.validate(runs, expected=1, renoise=True)
        self.assertEqual(before, common.read_json(self.ws / "charter.json")["noise"])


if __name__ == "__main__":
    unittest.main()
