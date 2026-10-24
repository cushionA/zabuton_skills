"""End-to-end flow on a tiny synthetic task with a fake model (no network)."""
import contextlib
import io
import json
import shutil
import subprocess
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
import run_experiment  # noqa: E402
import task_review  # noqa: E402

PY = sys.executable

CHECKS = '''
import json
def run_checks(ctx):
    with open(ctx.inputs[0], encoding="utf-8") as handle:
        recs = [json.loads(l) for l in handle if l.strip()]
    return [ctx.finding("size", "info", f"{len(recs)} records"),
            ctx.finding("no-labels", "high", "labels not included", known=True)]
'''

RULE_PIPELINE = '''
def run(unit, ctx):
    items = []
    for k in unit["input"]["item_keys"]:
        items.append({"key": k, "label": "pos" if "good" in k else "neg"})
    ctx.count("rule_decided", len(items))
    return {"items": items}
'''

LLM_PIPELINE = '''
import json
def run(unit, ctx):
    r = ctx.llm(json.dumps(unit["input"]), "Label items.", tag="main", expect_json=True)
    if not r["ok"]:
        raise RuntimeError(r["error"])
    return r["json"]
'''


def fake_call(prompt, system=None, **kw):
    data = json.loads(prompt)
    items = [{"key": k, "label": "pos" if ("good" in k or "fine" in k) else "neg"} for k in data["item_keys"]]
    text = json.dumps({"items": items})
    return {"ok": True, "error_kind": None, "error": None, "text": text, "json": json.loads(text),
            "structured": None, "stop_reason": "end_turn", "model_served": "claude-haiku-5-5",
            "usage": {"input": 100, "output": 20, "cache_read": 50, "cache_write": 0, "thinking": None},
            "cost_usd": 0.0001, "api_ms": 900, "wall_ms": 1500, "attempts": 1, "attempt_errors": []}


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        data = self.tmp / "data.jsonl"
        rows = []
        for i in range(12):
            keys = [f"good-{i}", f"bad-{i}", f"fine-{i}"]
            rows.append({"id": f"u{i:02d}", "item_keys": keys})
        data.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.run_py("init_workspace.py", str(self.ws), "--task", "toy", "--data", str(data), "--backend", "claude")
        (self.ws / "audit" / "checks.py").write_text(CHECKS, encoding="utf-8")
        self.run_py("profile_data.py", str(data), "--out", str(self.ws / "audit" / "profile"))
        (self.ws / "audit" / "data_audit.md").write_text("toy audit\n", encoding="utf-8")
        # without a close read the audit is INCOMPLETE (exit 5), not a user-facing stop
        r = self.run_py("quality_gate.py", str(self.ws), ok=(5,))
        self.assertIn("GATE INCOMPLETE", r.stdout)
        common.append_jsonl(self.ws / "audit" / "close_read.jsonl", {"id": "u00", "verdict": "ok", "evidence": "fine"})
        # reference: fine-* is positive
        ref = self.ws / "eval" / "reference"
        for split, ids in (("dev", range(0, 6)), ("holdout", range(6, 12))):
            with open(ref / f"{split}.jsonl", "w", encoding="utf-8") as f:
                for i in ids:
                    for k, lab in ((f"good-{i}", "pos"), (f"bad-{i}", "neg"), (f"fine-{i}", "pos")):
                        f.write(json.dumps({"key": f"u{i:02d}|{k}", "label": lab, "source": "test"}) + "\n")
        r = self.run_py_ws("eval/dataset.py", "--make-splits", "--dev", "6", "--holdout", "6")
        self.assertIn("'dev': 6", r.stdout)
        common.write_json(self.ws / "eval" / "splits.json",
                          {"dev": [f"u{i:02d}" for i in range(6)], "holdout": [f"u{i:02d}" for i in range(6, 12)]})
        ds = (self.ws / "eval" / "dataset.py").read_text(encoding="utf-8")
        (self.ws / "eval" / "dataset.py").write_text(ds, encoding="utf-8")
        sc = (self.ws / "eval" / "score.py").read_text(encoding="utf-8")
        (self.ws / "eval" / "score.py").write_text(sc, encoding="utf-8")
        for name, code in (("c001-rule", RULE_PIPELINE), ("c002-llm", LLM_PIPELINE)):
            d = self.ws / "candidates" / name
            d.mkdir(parents=True)
            (d / "pipeline.py").write_text(code, encoding="utf-8")
            (d / "config.json").write_text('{"concurrency": 3}', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script, *args, ok=(0,)):
        r = subprocess.run([PY, str(SCRIPTS / script), *args], capture_output=True, text=True)
        if r.returncode not in ok:
            raise AssertionError(f"{script} rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def run_py_ws(self, rel, *args):
        r = subprocess.run([PY, str(self.ws / rel), *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"{rel} rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def quiet(self, fn, *a, **k):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = fn(*a, **k)
        return res, buf.getvalue()

    def review_task(self):
        (self.ws / "experiment_plan.md").write_text("Label all expected toy items.\n", encoding="utf-8")
        (self.ws / "eval" / "eval_spec.md").write_text("Item label agreement, with every item counted.\n", encoding="utf-8")
        request = task_review.prepare_review(self.ws, "c001-rule")
        (self.ws / "review" / "task_review.md").write_text("Synthetic fixture: six checks verified.\n", encoding="utf-8")
        result = self.ws / "review" / "task_review_result.json"
        common.write_json(result, {
            "design_hash": request["design_hash"],
            "reviewer": {"id": "test-fixture-reviewer", "kind": "subagent"},
            "checks": [{"id": check, "status": "PASS", "evidence": "Synthetic test fixture: explicit toy item contract."}
                       for check in task_review.CHECKS],
            "report": "review/task_review.md",
        })
        task_review.record_review(self.ws, result)

    def test_full_flow(self):
        # gate blocks runs until it exists
        with self.assertRaises(SystemExit):
            self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline")
        r = self.run_py("quality_gate.py", str(self.ws))
        self.assertIn("GATE PASS", r.stdout)  # known high finding does not block
        with self.assertRaisesRegex(SystemExit, "task review PENDING"):
            self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline")
        self.review_task()
        # experiment purpose needs eval validation
        with self.assertRaises(SystemExit):
            self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "experiment")
        self.run_py("ledger.py", "propose", str(self.ws), "--candidate", "c001-rule", "--lever", "baseline",
                    "--hypothesis", "rule baseline")
        r1, _ = self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline", exp="E001")
        r2, _ = self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "baseline", exp="E001", tag="r2")
        s = common.read_json(r1 / "summary.json")
        self.assertAlmostEqual(s["primary"], 2 / 3)
        self.assertEqual(s["pipeline_counts"]["rule_decided"], 18)
        # validation: oracle, null, noise, spotcheck
        v = self.run_py("validate_eval.py", str(self.ws), "--runs", str(r1), str(r2),
                        "--spotcheck", "read all 6 failing items; grader correct (fine-* is pos)")
        self.assertIn("PASSED", v.stdout)
        _, out = self.quiet(ledger.decide, self.ws, "E001")
        self.assertIn("BASELINE", out)
        # LLM candidate with fake model -> keep
        self.run_py("ledger.py", "propose", str(self.ws), "--candidate", "c002-llm", "--parent", "c001-rule",
                    "--lever", "model.choice", "--hypothesis", "let the model label items instead of the rule")
        with mock.patch.object(llm_call, "call", side_effect=fake_call):
            r3, out = self.quiet(run_experiment.run, self.ws, "c002-llm", "dev", "experiment", exp="E002")
        s3 = common.read_json(r3 / "summary.json")
        self.assertEqual(s3["primary"], 1.0)
        self.assertEqual(s3["calls"], 6)
        self.assertAlmostEqual(s3["cost_usd"], 0.0006)
        self.assertAlmostEqual(s3["cache_hit_ratio"], 50 / 150)
        _, out = self.quiet(ledger.decide, self.ws, "E002")
        self.assertIn("KEEP", out)
        # candidates are frozen after a measured run
        (self.ws / "candidates" / "c002-llm" / "pipeline.py").write_text(LLM_PIPELINE + "\n# edit\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.quiet(run_experiment.run, self.ws, "c002-llm", "dev", "experiment")
        (self.ws / "candidates" / "c002-llm" / "pipeline.py").write_text(LLM_PIPELINE, encoding="utf-8")
        # novelty check: a near-duplicate of a discarded idea is refused
        self.run_py("ledger.py", "propose", str(self.ws), "--candidate", "c001-rule", "--lever", "prompt.instructions",
                    "--hypothesis", "tighten the instructions about fine items")
        common.append_jsonl(self.ws / "ledger.jsonl", {"event": "decide", "exp": "E003", "decision": "discard",
                                                       "phase": "quality", "at": common.now_iso()})
        r = self.run_py("ledger.py", "propose", str(self.ws), "--candidate", "c001-rule", "--lever", "prompt.instructions",
                        "--hypothesis", "tighten the instructions about fine items again", ok=(2,))
        self.assertIn("NOVELTY CHECK FAILED", r.stdout)
        st = self.run_py("ledger.py", "status", str(self.ws), "--write")
        self.assertIn("RECOMMEND", st.stdout)
        self.assertIn("NO-LLM best E001", st.stdout)
        h = self.run_py("ledger.py", "history", str(self.ws))
        self.assertIn("| E002 | model.choice |", h.stdout)
        ex = self.run_py("export_deliverables.py", str(self.ws), "--candidate", "c002-llm")
        cfg = common.read_json(self.ws / "deliverables" / "production_config.json")
        self.assertEqual(cfg["settings"]["max_tokens"]["value"], 512)
        self.assertTrue((self.ws / "deliverables" / "candidates" / "c002-llm" / "pipeline.py").exists())
        self.assertTrue((self.ws / "deliverables" / "review" / "task_review.json").exists())
        self.assertIn("missing", ex.stdout)  # task_design.md / report.md not written yet
        # a defect found on close reading turns the gate into REVIEW, pauses the clock, blocks runs
        common.append_jsonl(self.ws / "audit" / "close_read.jsonl",
                            {"id": "u03", "verdict": "defect", "evidence": "label contradicts text"})
        self.run_py("quality_gate.py", str(self.ws), ok=(2,))
        st = self.run_py("ledger.py", "status", str(self.ws))
        self.assertIn("RECOMMEND WAIT_USER", st.stdout)
        self.assertIn("clock paused", st.stdout)
        with self.assertRaises(SystemExit):
            self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "smoke")
        self.run_py("quality_gate.py", str(self.ws), "--accept-review", "user: score u03 as-is")
        self.assertNotIn("clock paused", self.run_py("ledger.py", "status", str(self.ws)).stdout)
        self.assertTrue((self.ws / "STATE.md").exists())
        a = self.run_py("analyze_run.py", str(r3), "--compare", str(r1))
        self.assertIn("improved", a.stdout)
        # moving the data elsewhere: relocate rewrites charter + gate keys, hashes still verify
        moved = self.tmp / "moved"
        moved.mkdir()
        old = common.read_json(self.ws / "charter.json")["data"]["inputs"][0]
        shutil.copy2(old, moved / "data.jsonl")
        self.run_py("relocate_workspace.py", str(self.ws), "--data-map", f"{old}={moved / 'data.jsonl'}")
        self.assertIn("data unchanged", self.run_py("quality_gate.py", str(self.ws), "--verify").stdout)
        # input data change invalidates the gate
        with open(common.read_json(self.ws / "charter.json")["data"]["inputs"][0], "a", encoding="utf-8") as f:
            f.write('{"id": "u99", "item_keys": []}\n')
        with self.assertRaises(SystemExit):
            self.quiet(run_experiment.run, self.ws, "c001-rule", "dev", "smoke")


def quota_call(prompt, system=None, **kw):
    return {"ok": False, "error_kind": "quota", "error": "quota exhausted: You've hit your session limit",
            "text": None, "json": None, "usage": None, "cost_usd": None, "attempts": 1}


class QuotaTest(FlowTest):
    def test_quota_stops_run(self):
        self.run_py("quality_gate.py", str(self.ws))
        with mock.patch.object(llm_call, "call", side_effect=quota_call) as m:
            r, out = self.quiet(run_experiment.run, self.ws, "c002-llm", "dev", "smoke", concurrency=1)
        s = common.read_json(r / "summary.json")
        self.assertTrue(s["quota_exhausted"])
        self.assertEqual(m.call_count, 1)  # circuit breaker: no further calls after the first quota error
        self.assertEqual(s["status"], {"harness_error": 6})
        self.assertIn("QUOTA EXHAUSTED", out)

    def test_full_flow(self):
        pass


class DiversifyTest(unittest.TestCase):
    def test_recommendations(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ws = tmp / "ws"
            ws.mkdir()
            common.write_json(ws / "charter.json", {"phase": "quality", "loop": {"patience": 5}})
            common.write_json(ws / "audit" / "quality_gate.json", {"status": "PASS", "findings": []})
            rows = [{"event": "propose", "exp": "E001", "lever": "baseline", "candidate": "c0"},
                    {"event": "decide", "exp": "E001", "decision": "baseline", "phase": "quality", "at": "1",
                     "primary": 0.5, "candidate": "c0"}]
            for i in range(2, 5):
                rows.append({"event": "propose", "exp": f"E{i:03d}", "lever": "prompt.instructions", "candidate": "c0"})
                rows.append({"event": "decide", "exp": f"E{i:03d}", "decision": "discard", "phase": "quality", "at": str(i)})
            for r in rows:
                common.append_jsonl(ws / "ledger.jsonl", r)
            with mock.patch.object(ledger, "task_review_allows_runs", return_value=(True, "fixture")), \
                    mock.patch.object(ledger, "gate_allows_runs", return_value=(True, "fixture")):
                rec = ledger.recommend(ws)
            self.assertEqual(rec["rec"][0], "FRESH_EYES")
            common.append_jsonl(ws / "ledger.jsonl", {"event": "mark", "kind": "fresh-eyes", "text": "x", "at": "9"})
            with mock.patch.object(ledger, "task_review_allows_runs", return_value=(True, "fixture")), \
                    mock.patch.object(ledger, "gate_allows_runs", return_value=(True, "fixture")):
                rec = ledger.recommend(ws)
            self.assertEqual(rec["rec"][0], "DIVERSIFY")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
