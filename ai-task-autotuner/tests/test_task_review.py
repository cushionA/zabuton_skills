"""Task-review lifecycle and measured-run enforcement, without model calls."""
import contextlib
import copy
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
import ledger  # noqa: E402
import llm_call  # noqa: E402
import quality_gate  # noqa: E402
import run_experiment  # noqa: E402
import task_review  # noqa: E402


DATASET = '''
import json
from pathlib import Path
def load_units(split):
    ws = Path(__file__).resolve().parents[1]
    rows = json.loads((ws / "data.json").read_text())
    ids = json.loads((ws / "eval/splits.json").read_text())[split]
    return [{"id": row["id"], "input": row} for row in rows if row["id"] in ids]
'''

SCORER = '''
def score(units, outputs, split):
    items = [{"id": u["id"], "unit": u["id"],
              "score": float(outputs.get(u["id"]) == {"label": u["input"]["label"]})}
             for u in units]
    return {"primary": sum(i["score"] for i in items) / len(items),
            "items": items, "metrics": {}, "gate_failures": []}
'''

PIPELINE = '''
def run(unit, ctx):
    ctx.count("rule_decided")
    return {"label": unit["input"]["label"]}
'''

CHECK_IDS = (
    "goal_alignment", "input_sufficiency", "deterministic_safety",
    "output_contract", "evaluation_coverage", "leakage_and_difficulty",
)


class TaskReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Path(self.temp.name) / "ws"
        self.ws.mkdir()
        common.write_json(self.ws / "data.json", [
            {"id": "a", "label": "positive"}, {"id": "b", "label": "negative"},
        ])
        common.write_json(self.ws / "charter.json", {
            "task": "Return the label supplied in each record",
            "brief": "A small code-only task for exercising the review gate",
            "data": {"inputs": [str(self.ws / "data.json")], "aux": []},
            "primary": {"metric": "primary", "direction": "max"},
            "guards": [], "backend": {"backend": "api", "concurrency": 1},
        })
        for rel, contents in {
            "audit/profile.md": "Two complete records.\n",
            "audit/data_audit.md": "Both records inspected; no defects found.\n",
            "audit/checks.py": "def run_checks(ctx):\n    return []\n",
            "experiment_plan.md": "Review the draft, then measure the baseline twice.\n",
            "eval/eval_spec.md": "Exact label agreement, grouped by input record.\n",
            "eval/dataset.py": DATASET,
            "eval/score.py": SCORER,
            "candidates/c000-baseline/pipeline.py": PIPELINE,
            "candidates/c000-baseline/prompts/system.txt": "Return the supplied label.\n",
        }.items():
            path = self.ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        common.write_json(self.ws / "eval/splits.json", {"dev": ["a", "b"], "holdout": []})
        common.write_json(self.ws / "candidates/c000-baseline/config.json", {"concurrency": 1})
        common.append_jsonl(self.ws / "audit/close_read.jsonl", {
            "id": "a", "verdict": "ok", "evidence": "The label is explicit in the input.",
        })
        self.refresh_audit()

    def refresh_audit(self):
        gate = quality_gate.evaluate(self.ws)
        self.assertEqual(gate["status"], "PASS")
        common.write_json(self.ws / "audit/quality_gate.json", gate)
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def make_result(self, request, revise=None):
        (self.ws / "review/task_review.md").write_text(
            "Independent review: checked task purpose, inputs, code-only rules, "
            "output validation, scoring coverage and split leakage.\n",
            encoding="utf-8",
        )
        return {
            "design_hash": request["design_hash"],
            "reviewer": {"id": "independent-reviewer-1", "kind": "subagent"},
            "checks": [
                {"id": check, "status": "REVISE" if check == revise else "PASS",
                 "evidence": f"Reviewed {check} in the task specification and draft pipeline."}
                for check in CHECK_IDS
            ],
            "report": "review/task_review.md",
        }

    def record(self, result):
        path = self.ws / "review/task_review.result.json"
        common.write_json(path, result)
        return task_review.record_review(self.ws, path)

    def pass_review(self):
        request = task_review.prepare_review(self.ws)
        return self.record(self.make_result(request))

    def run_candidate(self, purpose="baseline"):
        # A code-only baseline must remain executable without any external process/model.
        with mock.patch.object(llm_call, "call", side_effect=AssertionError("unexpected model call")):
            with contextlib.redirect_stdout(io.StringIO()):
                return run_experiment.run(self.ws, "c000-baseline", "dev", purpose)

    def status_text(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            ledger.print_status(self.ws, write=True)
        return output.getvalue()

    def test_review_lifecycle_blocks_then_allows_a_real_baseline(self):
        self.assertEqual(task_review.review_state(self.ws)[0], "PENDING")
        with self.assertRaisesRegex(SystemExit, "task review PENDING"):
            self.run_candidate()
        self.assertIn("RECOMMEND TASK_REVIEW", self.status_text())

        request = task_review.prepare_review(self.ws)
        self.assertEqual(set(request["required_checks"]), set(CHECK_IDS))
        revise = self.record(self.make_result(request, revise="deterministic_safety"))
        self.assertEqual(revise["status"], "REVISE")
        with self.assertRaisesRegex(SystemExit, "task review REVISE"):
            self.run_candidate()
        status = self.status_text()
        self.assertIn("RECOMMEND TASK_REVIEW", status)
        self.assertNotIn("WAIT_USER", status)
        self.assertIn("TASK_REVIEW", (self.ws / "STATE.md").read_text())

        approved = self.record(self.make_result(request))
        self.assertEqual(approved["status"], "PASS")
        self.assertEqual(task_review.review_state(self.ws)[0], "PASS")
        self.assertEqual(approved["design_hash"], request["design_hash"])
        self.assertEqual(approved["report_sha256"], common.sha256_file(self.ws / approved["report"]))
        self.assertEqual(len(common.read_jsonl(self.ws / "review/task_review_history.jsonl")), 2)
        run_dir = self.run_candidate()
        summary = common.read_json(run_dir / "summary.json")
        self.assertEqual(summary["primary"], 1.0)
        self.assertEqual(summary["units"], 2)
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["pipeline_counts"], {"rule_decided": 2})
        self.assertIn("RECOMMEND BASELINE", self.status_text())

    def test_all_measured_purposes_require_review_before_creating_runs(self):
        for state in ("PENDING", "REVISE"):
            if state == "REVISE":
                request = task_review.prepare_review(self.ws)
                self.record(self.make_result(request, revise="deterministic_safety"))
            for purpose in sorted(run_experiment.PURPOSES - {"smoke"}):
                with self.subTest(state=state, purpose=purpose):
                    with self.assertRaisesRegex(SystemExit, f"task review {state}"):
                        self.run_candidate(purpose)
        self.assertFalse((self.ws / "runs").exists())

    def test_smoke_can_debug_an_unreviewed_draft_but_still_needs_data_audit(self):
        run_dir = self.run_candidate("smoke")
        self.assertEqual(common.read_json(run_dir / "summary.json")["primary"], 1.0)
        (self.ws / "audit/quality_gate.json").unlink()
        with self.assertRaisesRegex(SystemExit, "quality_gate.json"):
            self.run_candidate("smoke")
        with self.assertRaisesRegex(ValueError, "complete the data audit first"):
            task_review.prepare_review(self.ws)
        self.assertEqual(len(list((self.ws / "runs").iterdir())), 1)

    def test_incomplete_or_unattributed_reviews_cannot_be_recorded(self):
        request = task_review.prepare_review(self.ws)
        good = self.make_result(request)
        invalid = {}
        invalid["missing check"] = copy.deepcopy(good)
        invalid["missing check"]["checks"].pop()
        invalid["duplicate check"] = copy.deepcopy(good)
        invalid["duplicate check"]["checks"][-1] = copy.deepcopy(good["checks"][0])
        invalid["empty evidence"] = copy.deepcopy(good)
        invalid["empty evidence"]["checks"][0]["evidence"] = " \n"
        invalid["unattributed reviewer"] = copy.deepcopy(good)
        invalid["unattributed reviewer"]["reviewer"]["id"] = ""
        invalid["unsupported reviewer kind"] = copy.deepcopy(good)
        invalid["unsupported reviewer kind"]["reviewer"]["kind"] = "self"
        for name, result in invalid.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.record(result)
                self.assertFalse((self.ws / "review/task_review.json").exists())
                self.assertFalse(task_review.task_review_allows_runs(self.ws)[0])

    def test_a_real_report_inside_the_review_directory_is_required(self):
        request = task_review.prepare_review(self.ws)
        good = self.make_result(request)
        (self.ws / "outside.md").write_text("Not a reviewer report.", encoding="utf-8")
        for report in ("outside.md", "review/../outside.md", str(self.ws / "outside.md"),
                       "review/missing.md"):
            with self.subTest(report=report):
                result = dict(good, report=report)
                with self.assertRaises(ValueError):
                    self.record(result)
        (self.ws / "review/task_review.md").write_text(" \n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            self.record(good)
        self.assertFalse((self.ws / "review/task_review.json").exists())

    def test_result_hash_and_pending_request_must_match_the_current_design(self):
        request = task_review.prepare_review(self.ws)
        result = self.make_result(request)
        with self.assertRaisesRegex(ValueError, "different design"):
            self.record(dict(result, design_hash="unrelated-design"))
        path = self.ws / "experiment_plan.md"
        path.write_text(path.read_text() + "Add an unresolved design assumption.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "design changed during review"):
            self.record(result)
        self.assertFalse((self.ws / "review/task_review.json").exists())
        # Re-preparing alone does not make the old reviewer result current.
        newer = task_review.prepare_review(self.ws)
        self.assertNotEqual(newer["design_hash"], request["design_hash"])
        with self.assertRaisesRegex(ValueError, "different design"):
            self.record(result)

    def test_review_expires_when_task_eval_or_baseline_design_changes(self):
        self.pass_review()
        paths = (
            "experiment_plan.md", "eval/eval_spec.md", "eval/dataset.py", "eval/score.py",
            "eval/splits.json", "candidates/c000-baseline/pipeline.py",
            "candidates/c000-baseline/prompts/system.txt", "candidates/c000-baseline/config.json",
        )
        for rel in paths:
            with self.subTest(changed=rel):
                path = self.ws / rel
                original = path.read_bytes()
                path.write_bytes(original + b"\n ")
                self.assertEqual(task_review.review_state(self.ws)[0], "STALE")
                with self.assertRaisesRegex(SystemExit, "task review STALE"):
                    self.run_candidate()
                path.write_bytes(original)
                self.assertTrue(task_review.task_review_allows_runs(self.ws)[0])
        charter = common.read_json(self.ws / "charter.json")
        charter["task"] = "A different task with the same files"
        common.write_json(self.ws / "charter.json", charter)
        self.assertEqual(task_review.review_state(self.ws)[0], "STALE")

    def test_new_candidates_and_generated_references_do_not_restart_task_review(self):
        approved = self.pass_review()
        candidate = self.ws / "candidates/c001-next/pipeline.py"
        candidate.parent.mkdir()
        candidate.write_text(PIPELINE + "\n# ordinary optimization candidate\n", encoding="utf-8")
        common.append_jsonl(self.ws / "eval/reference/dev.jsonl", {
            "key": "a", "label": "positive", "source": "generated after task review",
        })
        common.write_json(self.ws / "eval/reference/provenance.json", {
            "model": "fake-reference-model", "created": "after-review",
        })
        charter = common.read_json(self.ws / "charter.json")
        charter["noise"] = {"min_delta": 0.01, "n_items": 2}
        common.write_json(self.ws / "charter.json", charter)
        self.assertEqual(task_review.review_state(self.ws)[0], "PASS")
        self.assertEqual(task_review.prepare_review(self.ws)["design_hash"], approved["design_hash"])

    def test_reauditing_changed_inputs_requires_a_fresh_design_review(self):
        self.pass_review()
        common.write_json(self.ws / "data.json", [
            {"id": "a", "label": "changed"}, {"id": "b", "label": "negative"},
        ])
        self.refresh_audit()
        self.assertEqual(task_review.review_state(self.ws)[0], "STALE")
        with self.assertRaisesRegex(SystemExit, "task review STALE"):
            self.run_candidate()

    def test_editing_an_external_brief_invalidates_review_without_changing_its_path(self):
        brief = self.ws.parent / "task-brief.md"
        for reference in (str(brief), "../task-brief.md"):
            with self.subTest(reference=reference):
                brief.write_text("Return the supplied label verbatim.\n", encoding="utf-8")
                charter = common.read_json(self.ws / "charter.json")
                charter["brief"] = reference
                common.write_json(self.ws / "charter.json", charter)
                self.pass_review()
                self.assertEqual(task_review.review_state(self.ws)[0], "PASS")

                brief.write_text("Return a normalized label instead.\n", encoding="utf-8")
                # The input-data audit remains valid; the design review must notice
                # the external source document changed even though its path did not.
                self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])
                self.assertEqual(task_review.review_state(self.ws)[0], "STALE")
                with self.assertRaisesRegex(SystemExit, "task review STALE"):
                    self.run_candidate()

    def test_malformed_records_or_changed_reports_cannot_open_the_run_gate(self):
        good = self.pass_review()
        path = self.ws / "review/task_review.json"
        malformed = ["{", "[]", "true", json.dumps(dict(good, status="PASS", checks=[]))]
        missing_snapshot = copy.deepcopy(good)
        del missing_snapshot["snapshot"]
        malformed.append(json.dumps(missing_snapshot))
        contradictory = copy.deepcopy(good)
        contradictory["checks"][0]["status"] = "REVISE"
        malformed.append(json.dumps(contradictory))
        for value in malformed:
            with self.subTest(record=value[:60]):
                path.write_text(value, encoding="utf-8")
                self.assertFalse(task_review.task_review_allows_runs(self.ws)[0])
                with self.assertRaisesRegex(SystemExit, "task review"):
                    self.run_candidate()
        common.write_json(path, good)
        report = self.ws / good["report"]
        report.write_text("A different report without re-recording.\n", encoding="utf-8")
        self.assertEqual(task_review.review_state(self.ws)[0], "STALE")
        with self.assertRaisesRegex(SystemExit, "task review STALE"):
            self.run_candidate()
        report.unlink()
        self.assertFalse(task_review.task_review_allows_runs(self.ws)[0])


if __name__ == "__main__":
    unittest.main()
