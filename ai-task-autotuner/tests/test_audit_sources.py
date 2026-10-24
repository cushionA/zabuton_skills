"""An audit covers declared source roles, full trees, and current audit evidence."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common
import quality_gate
import run_experiment


class AuditSourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ws = self.base / "ws"
        self.audit = self.ws / "audit"
        self.audit.mkdir(parents=True)
        self.source = self.ws / "inputs" / "a.jsonl"
        self.source.parent.mkdir()
        self.source.write_text('{"id":"u1","value":"one"}\n', encoding="utf-8")
        self.other = self.base / "b.jsonl"
        self.other.write_text('{"id":"u2","value":"two"}\n', encoding="utf-8")
        self.set_sources([str(self.source)])
        for name in ("profile.md", "data_audit.md"):
            (self.audit / name).write_text("audited source\n", encoding="utf-8")
        (self.audit / "close_read.jsonl").write_text('{"id":"u1","verdict":"ok"}\n', encoding="utf-8")
        (self.audit / "checks.py").write_text("def run_checks(ctx): return []\n", encoding="utf-8")
        (self.ws / "eval").mkdir()
        (self.ws / "eval" / "dataset.py").write_text(
            'def load_units(split):\n    return [] if split == "holdout" else [{"id":"u1","input":{}}]\n',
            encoding="utf-8")
        (self.ws / "eval" / "score.py").write_text(
            'def score(units, outputs, split):\n    return {"primary":1,"items":[{"id":"u1","score":1}]}\n',
            encoding="utf-8")
        common.write_json(self.ws / "eval" / "splits.json", {"dev": ["u1"], "holdout": []})
        candidate = self.ws / "candidates" / "code"
        candidate.mkdir(parents=True)
        (candidate / "pipeline.py").write_text(
            'from pathlib import Path\nPath(__file__).parents[2].joinpath("imported").write_text("bad")\n'
            'def run(unit, ctx): return {"ok":True}\n', encoding="utf-8")
        self.record_audit()

    def set_sources(self, inputs, aux=None):
        ch = common.read_json(self.ws / "charter.json", {})
        ch.update(data={"inputs": inputs, "aux": [] if aux is None else aux},
                  backend={"backend": "api", "model": "haiku", "concurrency": 1}, limits={})
        common.write_json(self.ws / "charter.json", ch)

    def record_audit(self, status="PASS"):
        gate = quality_gate.evaluate(self.ws)
        self.assertEqual(gate["status"], status, gate["findings"])
        common.write_json(self.audit / "quality_gate.json", gate)
        return gate

    def verify_cli(self, exit_code):
        result = subprocess.run([sys.executable, str(SCRIPTS / "quality_gate.py"), str(self.ws), "--verify"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        return result

    def assert_blocked(self):
        self.assertTrue(quality_gate.verify_hashes(self.ws))
        self.assertFalse(quality_gate.gate_allows_runs(self.ws)[0])
        self.verify_cli(4)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "REFUSED"):
            run_experiment.run(self.ws, "code", "dev", "smoke")
        self.assertFalse((self.ws / "imported").exists(), "audit must stop candidate import too")

    def test_unchanged_sources_and_ws_relative_alias_verify(self):
        original = quality_gate.source_manifest(self.ws)
        self.set_sources(["inputs/a.jsonl"])
        self.assertEqual(quality_gate.source_manifest(self.ws), original)
        self.assertEqual(quality_gate.verify_hashes(self.ws), [])
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])
        self.assertIn("data unchanged", self.verify_cli(0).stdout)

    def test_source_replacement_and_addition_fail_before_smoke(self):
        for inputs in ([str(self.other)], [str(self.source), str(self.other)]):
            with self.subTest(inputs=inputs):
                self.set_sources(inputs)
                self.assert_blocked()

    def test_source_removal_role_order_and_duplicate_changes_are_detected(self):
        self.set_sources([str(self.source), str(self.other)])
        self.record_audit()
        cases = [([str(self.source)], []),
                 ([str(self.source)], [str(self.other)]),
                 ([str(self.other), str(self.source)], []),
                 ([str(self.source), str(self.other), str(self.other)], [])]
        for inputs, aux in cases:
            with self.subTest(inputs=inputs, aux=aux):
                self.set_sources(inputs, aux)
                self.assert_blocked()

    def test_directory_add_delete_rename_and_empty_subdirectory_are_detected(self):
        self.set_sources([str(self.source.parent)])
        for operation in ("add", "delete", "rename", "empty-directory", "cache-file"):
            with self.subTest(operation=operation):
                self.record_audit()
                if operation == "add":
                    target = self.source.parent / "new.jsonl"
                    target.write_text("{}\n")
                elif operation == "delete":
                    self.source.unlink()
                elif operation == "rename":
                    target = self.source.with_name("renamed.jsonl")
                    self.source.rename(target)
                elif operation == "empty-directory":
                    target = self.source.parent / "empty"
                    target.mkdir()
                else:
                    target = self.source.parent / "__pycache__" / "data.bin"
                    target.parent.mkdir()
                    target.write_bytes(b"also part of declared source tree")
                self.assert_blocked()
                if operation == "delete":
                    self.source.write_text('{"id":"u1","value":"one"}\n')
                elif operation == "rename":
                    target.rename(self.source)
                elif operation in ("add", "empty-directory"):
                    target.rmdir() if target.is_dir() else target.unlink()
                else:
                    shutil.rmtree(target.parent)

    def test_missing_or_empty_replacement_is_not_equivalent_to_audited_tree(self):
        self.set_sources([str(self.source.parent)])
        self.record_audit()
        self.set_sources([str(self.base / "missing")])
        self.assert_blocked()
        self.record_audit("STOP")
        self.assert_blocked()
        empty = self.base / "empty"
        empty.mkdir()
        self.set_sources([str(empty)])
        self.assert_blocked()

    def test_fresh_audit_accepts_new_source_scope_without_inventing_approval(self):
        self.set_sources([str(self.other)])
        self.assert_blocked()
        self.record_audit()
        self.assertEqual(quality_gate.verify_hashes(self.ws), [])
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def test_changed_check_or_audit_evidence_requires_local_reaudit(self):
        for relative in quality_gate.AUDIT_EVIDENCE:
            with self.subTest(relative=relative):
                path = self.ws / relative
                original = path.read_text()
                path.write_text(original + ("\n" if path.suffix == ".jsonl" else "\n# updated\n"))
                self.assert_blocked()
                path.write_text(original)
        self.assertEqual(quality_gate.verify_hashes(self.ws), [])

    def test_legacy_gate_needs_reaudit_instead_of_claiming_current_scope(self):
        gate = common.read_json(self.audit / "quality_gate.json")
        gate.pop("source_manifest")
        common.write_json(self.audit / "quality_gate.json", gate)
        self.assertIn("manifest missing", self.verify_cli(4).stdout)
        self.assertFalse(quality_gate.gate_allows_runs(self.ws)[0])
        self.record_audit()
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def test_audit_detects_sources_changing_inside_check(self):
        (self.audit / "checks.py").write_text(
            'def run_checks(ctx):\n    ctx.inputs[0].write_text("changed during checks")\n    return []\n')
        gate = self.record_audit("STOP")
        self.assertIn("sources-changed-during-audit", [f["id"] for f in gate["findings"]])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_nested_directory_symlink_contents_are_audited_and_cycles_block(self):
        tree = self.source.parent
        linked = self.base / "linked"
        linked.mkdir()
        (linked / "x.json").write_text("one")
        try:
            (tree / "link").symlink_to(linked, target_is_directory=True)
        except OSError as e:
            self.skipTest(str(e))
        self.set_sources([str(tree)])
        gate = self.record_audit()
        self.assertIn("link/x.json", gate["source_manifest"]["inputs"][0]["files"])
        (linked / "new.json").write_text("two")
        self.assert_blocked()
        self.record_audit()
        (linked / "cycle").symlink_to(tree, target_is_directory=True)
        self.assert_blocked()
        gate = self.record_audit("STOP")
        self.assertIn("source-unreadable", [f["id"] for f in gate["findings"]])


if __name__ == "__main__":
    unittest.main()
