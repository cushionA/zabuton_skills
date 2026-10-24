"""Approval scope regressions using real audits and CLI calls, without a model."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common  # noqa: E402
import quality_gate  # noqa: E402


CHECKS = '''
import json
def run_checks(ctx):
    return json.loads((ctx.ws / "audit" / "findings.json").read_text(encoding="utf-8"))
'''


class ReviewApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        self.audit = self.ws / "audit"
        self.audit.mkdir(parents=True)
        self.data = Path(self.tmp.name) / "data.jsonl"
        self.data.write_text('{"id":"A"}\n', encoding="utf-8")
        common.write_json(self.ws / "charter.json", {"data": {"inputs": [str(self.data)]}})
        (self.audit / "profile.md").write_text("profile\n", encoding="utf-8")
        (self.audit / "data_audit.md").write_text("audit\n", encoding="utf-8")
        (self.audit / "checks.py").write_text(CHECKS, encoding="utf-8")
        common.write_json(self.audit / "findings.json", [])
        self.write_close_read([self.defect("A")])

    @staticmethod
    def defect(id, evidence="label contradicts input", severity="high"):
        return {"id": id, "verdict": "defect", "evidence": evidence, "severity": severity}

    def write_close_read(self, rows):
        (self.audit / "close_read.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def gate(self):
        return common.read_json(self.audit / "quality_gate.json")

    def run_cli(self, *args, expected=2):
        result = subprocess.run([sys.executable, str(SCRIPTS / "quality_gate.py"), str(self.ws), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def approve(self):
        self.run_cli()
        self.run_cli("--accept-review", "test fixture: user accepts the audited defects", expected=0)
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def assert_requires_review(self):
        self.run_cli()
        self.assertIsNone(self.gate()["waiver"])
        self.assertFalse(quality_gate.gate_allows_runs(self.ws)[0])
        clocks = [row for row in common.read_jsonl(self.ws / "ledger.jsonl") if row.get("event") == "clock"]
        self.assertEqual(clocks[-1]["action"], "pause")

    def test_unchanged_review_retains_approval_and_clock_state(self):
        self.approve()
        approved = self.gate()["waiver"]
        self.run_cli()
        self.assertEqual(self.gate()["waiver"]["reason"], approved["reason"])
        self.assertEqual(self.gate()["waiver"]["scope_sha256"], approved["scope_sha256"])
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])
        clocks = [row["action"] for row in common.read_jsonl(self.ws / "ledger.jsonl")]
        self.assertEqual(clocks, ["pause", "resume"])

    def test_new_defect_under_same_finding_id_requires_new_approval(self):
        self.approve()
        self.write_close_read([self.defect("A"), self.defect("B")])
        self.assert_requires_review()
        self.assertEqual(next(f for f in self.gate()["findings"] if f["id"] == "close-read-defects")["affected"],
                         ["A", "B"])

    def test_changed_evidence_beyond_display_truncation_requires_review(self):
        rows = [self.defect(str(i), "x" * 100) for i in range(6)]
        self.write_close_read(rows)
        self.approve()
        displayed_before = self.gate()["findings"]
        # Both changes are invisible in the abbreviated finding: after char 80,
        # and in the sixth defect. They still change what was approved.
        rows[0]["evidence"] += " new contradiction"
        rows[5]["evidence"] = "new evidence for the sixth unit"
        self.write_close_read(rows)
        self.assert_requires_review()
        self.assertEqual(self.gate()["findings"], displayed_before)

    def test_changed_audited_data_requires_new_approval(self):
        self.approve()
        self.data.write_text('{"id":"A","label":"changed"}\n', encoding="utf-8")
        self.assert_requires_review()

    def test_changed_source_roles_require_new_approval(self):
        self.approve()
        # Same files and hashes, but an auxiliary table has become an input.
        charter = {"data": {"inputs": [str(self.data)], "aux": [str(self.data)]}}
        common.write_json(self.ws / "charter.json", charter)
        self.assert_requires_review()

    def test_nonblocking_audit_progress_does_not_revoke_approval(self):
        self.approve()
        self.write_close_read([self.defect("A"), {"id": "B", "verdict": "ok", "evidence": "reviewed"}])
        self.run_cli()
        self.assertIsNotNone(self.gate()["waiver"])
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def test_cannot_accept_stale_review_after_a_new_defect(self):
        self.run_cli()
        self.write_close_read([self.defect("A"), self.defect("B")])
        result = self.run_cli("--accept-review", "test fixture acceptance", expected=1)
        self.assertIn("re-run quality_gate.py", result.stderr)
        self.assertIsNone(self.gate()["waiver"])

    def set_custom_finding(self, **updates):
        finding = {"id": "bad-labels", "severity": "high", "title": "labels contradict input",
                   "evidence": "A is incorrectly labeled", "affected": ["A"], "known": False,
                   "recommendation": "exclude or adjudicate"}
        finding.update(updates)
        common.write_json(self.audit / "findings.json", [finding])
        self.write_close_read([{"id": "A", "verdict": "ok"}])

    def test_custom_finding_content_is_part_of_approval(self):
        self.set_custom_finding()
        self.approve()
        self.set_custom_finding(affected=["B"], evidence="B is incorrectly labeled")
        self.assert_requires_review()

    def test_severity_escalation_stops_previously_approved_review(self):
        self.set_custom_finding()
        self.approve()
        self.set_custom_finding(severity="critical")
        self.run_cli(expected=3)
        self.assertIsNone(self.gate()["waiver"])
        self.assertFalse(quality_gate.gate_allows_runs(self.ws)[0])

    def test_equivalent_legacy_snapshot_can_retain_approval(self):
        self.set_custom_finding()
        self.approve()
        gate = self.gate()
        del gate["review_scope"]
        del gate["waiver"]["scope_sha256"]
        common.write_json(self.audit / "quality_gate.json", gate)
        self.run_cli()
        self.assertIsNotNone(self.gate()["waiver"])
        self.assertIn("scope_sha256", self.gate()["waiver"])
        self.set_custom_finding(evidence="additional contradiction")
        self.assert_requires_review()

    def test_legacy_truncated_close_read_snapshot_requires_fresh_approval(self):
        self.approve()
        gate = self.gate()
        del gate["review_scope"]
        del gate["waiver"]["scope_sha256"]
        common.write_json(self.audit / "quality_gate.json", gate)
        self.assert_requires_review()
        # Re-evaluation records full scope, after which explicit approval works.
        self.run_cli("--accept-review", "test fixture: approve full current evidence", expected=0)
        self.assertTrue(quality_gate.gate_allows_runs(self.ws)[0])

    def assert_stops(self):
        self.run_cli(expected=3)
        self.assertEqual(self.gate()["status"], "STOP")
        self.assertFalse(quality_gate.gate_allows_runs(self.ws)[0])

    def test_malformed_custom_findings_stop_instead_of_passing(self):
        for updates in ({"severity": "High"}, {"severity": "error"}, {"severity": "critical "},
                        {"severity": None}, {"id": ""}, {"title": None}, {"known": "false"}):
            with self.subTest(updates=updates):
                self.set_custom_finding(**updates)
                self.assert_stops()
                self.assertIn("checks-invalid-finding", [f["id"] for f in self.gate()["findings"]])
        common.write_json(self.audit / "findings.json", {"id": "not-a-list"})
        self.assert_stops()

    def test_close_read_defect_with_unlisted_severity_or_string_known_blocks(self):
        self.write_close_read([self.defect("A", severity="High")])
        self.assert_stops()
        self.write_close_read([dict(self.defect("A"), known="false")])
        self.assert_requires_review()


if __name__ == "__main__":
    unittest.main()
