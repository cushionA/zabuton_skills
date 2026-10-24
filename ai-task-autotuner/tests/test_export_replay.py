"""Exports replay with original source unavailable and preserve approval/control history."""
import contextlib
import io
import shutil
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import export_deliverables  # noqa: E402
import ledger  # noqa: E402
import quality_gate  # noqa: E402
import relocate_workspace  # noqa: E402
import run_experiment  # noqa: E402
import run_policy  # noqa: E402
import task_review  # noqa: E402
import workspace  # noqa: E402
import test_flow  # noqa: E402


class ExportReplayTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_flow.FlowTest("test_full_flow")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.ws = self.fixture.ws
        self.tmp = self.fixture.tmp
        self.external = self.tmp / "data.jsonl"
        built = self.ws / "data_built" / "units.jsonl"
        built.parent.mkdir()
        shutil.copy2(self.external, built)
        ch = common.read_json(self.ws / "charter.json")
        ch["data"]["inputs"] = [str(built)]
        ch["data"]["aux"] = [str(self.external)]
        ch["brief"] = str(self.ws / "brief.md")
        (self.ws / "brief.md").write_text("Label all expected items.\n", encoding="utf-8")
        common.write_json(self.ws / "review/probes/known_answers.json", {"good": "pos", "bad": "neg"})
        common.write_json(self.ws / "charter.json", ch)
        self.fixture.run_py("quality_gate.py", str(self.ws))
        self.fixture.review_task()

    def quiet(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def measured(self, exp=None):
        r1 = self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline", tag="one", exp=exp)
        r2 = self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline", tag="two", exp=exp)
        self.fixture.run_py("validate_eval.py", str(self.ws), "--runs", str(r1), str(r2),
                            "--spotcheck", "Read all toy errors and verified every reference label.")
        return r1, r2

    def test_export_preserves_accepted_comparators_for_repeat_and_rollback_after_source_removed(self):
        self.fixture.run_py("ledger.py", "propose", str(self.ws), "--candidate", "c001-rule",
                            "--lever", "baseline", "--hypothesis", "initial rule")
        self.measured(exp="E001")
        self.quiet(ledger.decide, self.ws, "E001")
        intermediate = self.ws / "candidates/c002-llm"
        intermediate.joinpath("pipeline.py").write_text(test_flow.RULE_PIPELINE.replace(
            '"good" in k', '"good" in k or ("fine" in k and int(unit["id"][1:]) % 2 == 0)'),
            encoding="utf-8")
        final = self.ws / "candidates/c003-final"
        shutil.copytree(self.ws / "candidates/c001-rule", final)
        final.joinpath("pipeline.py").write_text(test_flow.RULE_PIPELINE.replace(
            '"good" in k', '"good" in k or "fine" in k'), encoding="utf-8")
        for exp, name, hypothesis in (("E002", "c002-llm", "handle half the fine items"),
                                       ("E003", "c003-final", "handle all fine items")):
            self.fixture.run_py("ledger.py", "propose", str(self.ws), "--candidate", name,
                                "--lever", "deterministic.pre", "--hypothesis", hypothesis)
            final_run = self.quiet(run_experiment.run, self.ws, name, "dev", "experiment", exp=exp)
            self.quiet(ledger.decide, self.ws, exp)
            self.assertEqual(ledger._state(self.ws)["decisions"][exp]["decision"], "keep")
        before = ledger._state(self.ws)
        self.assertAlmostEqual(before["decisions"]["E001"]["primary"], 2 / 3)
        self.assertAlmostEqual(before["decisions"]["E002"]["primary"], 5 / 6)
        self.assertEqual(before["decisions"]["E003"]["primary"], 1)
        hashes = {name: workspace.candidate_hash(self.ws / "candidates" / name)
                  for name in ("c001-rule", "c002-llm", "c003-final")}
        # Rejected candidate code is not a dependency merely because its old KEEP
        # remains in append-only history.
        rejected = self.ws / "candidates/c099-rejected"
        shutil.copytree(final, rejected)
        for decision in ("keep", "discard"):
            common.append_jsonl(self.ws / "ledger.jsonl", {
                "event": "decide", "exp": "E099", "candidate": rejected.name, "decision": decision})
        out = self.tmp / "chain-export"
        self.quiet(export_deliverables.export_workspace, self.ws, "c003-final", [final_run], out)
        self.assertEqual(common.read_json(out / "replay.json")["retained_candidates"], sorted(hashes))
        self.assertFalse((out / "candidates" / rejected.name).exists())
        self.assertEqual({name: workspace.candidate_hash(out / "candidates" / name) for name in hashes}, hashes)
        moved_data = self.tmp / "chain-data.jsonl"
        shutil.copy2(self.external, moved_data)
        self.external.unlink()
        shutil.rmtree(self.ws)
        relocate_workspace.relocate(out, [(str(self.external), str(moved_data))])
        self.quiet(run_experiment.run, out, "c003-final", "dev", "experiment", exp="E003", tag="repeat")
        self.quiet(ledger.decide, out, "E003")
        repeated = ledger._state(out)
        self.assertEqual(repeated["decisions"]["E003"]["decision"], "keep")
        self.assertEqual(repeated["decisions"]["E003"]["best_exp"], "E002")
        self.quiet(ledger.decide, out, "E003", override="discard", reason="test rollback after replay")
        self.assertEqual(ledger._state(out)["best"]["exp"], "E002")
        self.quiet(run_experiment.run, out, "c002-llm", "dev", "experiment", exp="E002", tag="repeat")
        self.quiet(ledger.decide, out, "E002")
        rolled_back = ledger._state(out)
        self.assertEqual(rolled_back["best"]["exp"], "E002")
        self.assertEqual(rolled_back["decisions"]["E002"]["best_exp"], "E001")

    def test_export_replays_after_source_removed(self):
        r1, r2 = self.measured()
        # The final candidate differs from the independently reviewed baseline.
        final = self.ws / "candidates" / "c003-final"
        shutil.copytree(self.ws / "candidates" / "c001-rule", final)
        final_run = self.quiet(run_experiment.run, self.ws, "c003-final", "dev", "experiment")
        original_eval = workspace.eval_hash(self.ws)
        # Changing current defaults must not change what the export claims to replay.
        ch = common.read_json(self.ws / "charter.json")
        measured_cfg = common.read_json(final_run / "meta.json")["config"]
        ch["backend"]["effort"] = "medium"
        common.write_json(self.ws / "charter.json", ch)
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "mark", "kind": "fixture", "text": "retain me"})
        out = self.tmp / "exported"
        self.quiet(export_deliverables.export_workspace, self.ws, "c003-final", [final_run], out)
        self.assertEqual(common.read_json(out / "charter.json")["backend"], measured_cfg)
        self.assertTrue((out / "candidates" / "c001-rule" / "pipeline.py").is_file())
        self.assertTrue((out / "runs" / r1.name / "outputs.jsonl").is_file())
        self.assertTrue((out / "audit" / "quality_gate.json").is_file())
        self.assertEqual(common.read_json(out / "review/design_contracts.json"),
                         common.read_json(self.ws / "review/design_contracts.json"))
        self.assertTrue((out / "review/probes/known_answers.json").is_file())
        moved_data = self.tmp / "moved-data.jsonl"
        shutil.copy2(self.external, moved_data)
        self.external.unlink()
        shutil.rmtree(self.ws)
        relocate_workspace.relocate(out, [(str(self.external), str(moved_data))])
        self.assertTrue(quality_gate.gate_allows_runs(out)[0])
        self.assertEqual(task_review.review_state(out)[0], "PASS")
        self.assertEqual(workspace.eval_hash(out), original_eval)
        self.assertTrue(run_experiment._validation_ok(out)[0])
        self.assertTrue(any(r.get("text") == "retain me" for r in common.read_jsonl(out / "ledger.jsonl")))
        for purpose, split in (("smoke", "dev"), ("baseline", "dev"), ("experiment", "dev"),
                               ("holdout", "holdout")):
            run = self.quiet(run_experiment.run, out, "c003-final", split, purpose, tag="replay")
            self.assertEqual(common.read_json(run / "summary.json")["calls"], 0)
        # Stored baseline outputs still support a real validator rerun.
        self.fixture.run_py("validate_eval.py", str(out), "--runs", str(out / "runs" / r1.name),
                            str(out / "runs" / r2.name), "--spotcheck",
                            "Replayed all toy errors and verified every reference label.")

    def test_accepted_review_survives_same_bytes_not_changed_bytes(self):
        common.append_jsonl(self.ws / "audit" / "close_read.jsonl",
                            {"id": "u03", "verdict": "defect", "evidence": "fixture defect"})
        self.fixture.run_py("quality_gate.py", str(self.ws), ok=(2,))
        self.fixture.run_py("quality_gate.py", str(self.ws), "--accept-review", "test user accepted this fixture defect")
        old_gate = common.read_json(self.ws / "audit" / "quality_gate.json")
        old_review = common.read_json(self.ws / "review" / "task_review.json")
        changed = self.tmp / "changed.jsonl"
        changed.write_text(self.external.read_text() + '{"id":"extra"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            relocate_workspace.relocate(self.ws, [(str(self.external), str(changed))])
        self.assertEqual(common.read_json(self.ws / "audit" / "quality_gate.json"), old_gate)
        self.assertEqual(common.read_json(self.ws / "review" / "task_review.json"), old_review)
        same = self.tmp / "same.jsonl"
        shutil.copy2(self.external, same)
        relocate_workspace.relocate(self.ws, [(str(self.external), str(same))])
        new_gate = common.read_json(self.ws / "audit" / "quality_gate.json")
        self.assertEqual(new_gate["waiver"]["reason"], old_gate["waiver"]["reason"])
        self.assertEqual(new_gate["waiver"]["scope_sha256"], quality_gate._scope_sha(new_gate["review_scope"]))
        self.assertTrue(quality_gate._same_review(new_gate, quality_gate.evaluate(self.ws)))
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])
        self.assertEqual(task_review.review_state(self.ws)[0], "PASS")

    def test_relocation_rejects_new_unapproved_defect(self):
        common.append_jsonl(self.ws / "audit" / "close_read.jsonl",
                            {"id": "u03", "verdict": "defect", "evidence": "accepted fixture defect"})
        self.fixture.run_py("quality_gate.py", str(self.ws), ok=(2,))
        self.fixture.run_py("quality_gate.py", str(self.ws), "--accept-review", "test user accepted u03 only")
        before = common.read_json(self.ws / "audit" / "quality_gate.json")
        common.append_jsonl(self.ws / "audit" / "close_read.jsonl",
                            {"id": "u04", "verdict": "defect", "evidence": "new unapproved defect"})
        same = self.tmp / "same.jsonl"
        shutil.copy2(self.external, same)
        with self.assertRaisesRegex(ValueError, "close-read defects changed"):
            relocate_workspace.relocate(self.ws, [(str(self.external), str(same))])
        self.assertEqual(common.read_json(self.ws / "audit" / "quality_gate.json"), before)

    def test_control_history_preserved_and_unrelated_large_artifacts_omitted(self):
        self.measured()
        final = self.ws / "candidates" / "c003-final"
        shutil.copytree(self.ws / "candidates" / "c001-rule", final)
        measured = self.quiet(run_experiment.run, self.ws, "c003-final", "dev", "experiment")
        historic = self.ws / "runs" / "historic-holdout"
        historic.mkdir()
        common.write_json(historic / "meta.json", {"run_id": historic.name, "candidate": "c999-other",
                                                   "purpose": "holdout", "split": "holdout"})
        common.write_json(historic / "summary.json", {"ai_free": False, "cost_usd": 7.5})
        common.write_json(historic / "score.json", {"items": []})
        (historic / "calls.jsonl").write_text('{"prompt":"huge unrelated prompt","cost_usd":7.5}\n')
        (historic / "outputs.jsonl").write_text('{"output":"huge unrelated result"}\n')
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "stop", "reason": "fixture explicit STOP"})
        out = self.tmp / "history-export"
        self.quiet(export_deliverables.export_workspace, self.ws, "c003-final", [measured], out)
        self.assertEqual(common.read_jsonl(out / "ledger.jsonl")[-1]["event"], "stop")
        self.assertEqual(common.read_json(out / "runs" / historic.name / "summary.json")["cost_usd"], 7.5)
        controls = common.read_jsonl(out / "runs" / historic.name / "calls.jsonl")
        self.assertEqual(controls, [{"cost_usd": 7.5}])
        self.assertFalse((out / "runs" / historic.name / "outputs.jsonl").exists())
        relocate_workspace.relocate(out)
        with self.assertRaisesRegex(SystemExit, "STOP|stopped"):
            self.quiet(run_experiment.run, out, "c003-final", "dev", "experiment")

    def test_unfinished_priced_and_unpriced_cost_history_survives_export(self):
        r1, _ = self.measured()
        unfinished = self.ws / "runs" / "unfinished-priced"
        unfinished.mkdir()
        common.write_json(unfinished / "meta.json", {"run_id": unfinished.name, "candidate": "c999-other",
                                                     "purpose": "probe", "split": "dev"})
        common.append_jsonl(unfinished / "calls.jsonl", {"cost_usd": 3.5, "attempts": 2,
                                                        "prompt": "omit this private input"})
        ch = common.read_json(self.ws / "charter.json")
        ch["limits"]["max_cost_usd"] = 3
        common.write_json(self.ws / "charter.json", ch)
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                if unknown:
                    unpriced = self.ws / "runs" / "unfinished-unpriced"
                    unpriced.mkdir()
                    common.write_json(unpriced / "meta.json", {"run_id": unpriced.name, "candidate": "c998-other",
                                                               "purpose": "probe", "split": "dev"})
                    common.append_jsonl(unpriced / "calls.jsonl", {"cost_usd": None, "text": "omit result"})
                before = run_policy.limit_state(self.ws)
                out = self.tmp / f"unfinished-export-{unknown}"
                self.quiet(export_deliverables.export_workspace, self.ws, "c001-rule", [r1], out)
                relocate_workspace.relocate(out)
                after = run_policy.limit_state(out)
                self.assertEqual(after["spent"], before["spent"])
                self.assertEqual(after["cost_complete"], before["cost_complete"])
                self.assertEqual(after["unknown_cost_runs"], before["unknown_cost_runs"])
                controls = common.read_jsonl(out / "runs" / unfinished.name / "calls.jsonl")
                self.assertEqual(controls, [{"cost_usd": 3.5, "attempts": 2}])
                with self.assertRaisesRegex(SystemExit, "limit reached"):
                    self.quiet(run_experiment.run, out, "c001-rule", "dev", "baseline")

    def test_prefix_mapping_does_not_remap_siblings(self):
        self.assertEqual(relocate_workspace.remap("/data-old/file", [("/data", "/new")]), "/data-old/file")
        self.assertEqual(relocate_workspace.remap("/data/file", [("/data/", "/new/")]), "/new/file")


if __name__ == "__main__":
    unittest.main()
