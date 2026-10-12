import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import smtplib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import send_email
import state


class SendEmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / "state"
        self.report_path = self.root / "report.json"
        self.body_path = self.root / "body.md"
        self.body_path.write_text("確認済みの研究報告\n", encoding="utf-8")
        self.today = dt.date(2026, 10, 13)
        state.save_queue(self.state_dir, [
            {"id": "recommended", "title": "Recommended"},
            {"id": "checked", "title": "Checked"},
            {"id": "pending", "title": "Pending"},
        ], self.today)
        self.report = {
            "run_id": "run:1",
            "recommended": [{
                "id": "recommended", "title": "Recommended",
                "category": "deep_dive_value", "published_at": "2026-10-12",
            }],
            "digest": [{"id": "pending", "title": "Pending"}],
            "completed_ids": ["checked"],
        }
        self.smtp = self.enterContext(mock.patch.object(send_email.smtplib, "SMTP"))
        self.smtp_ssl = self.enterContext(mock.patch.object(send_email.smtplib, "SMTP_SSL"))
        self.enterContext(mock.patch.dict(os.environ, {
            "RESEARCH_RADAR_SMTP_HOST": "smtp.example.invalid",
            "RESEARCH_RADAR_SMTP_PORT": "587",
            "RESEARCH_RADAR_SMTP_USERNAME": "sender@example.invalid",
            "RESEARCH_RADAR_SMTP_PASSWORD": "test-password",
            "RESEARCH_RADAR_EMAIL_TO": "recipient@example.invalid",
            "RESEARCH_RADAR_EMAIL_FROM": "   ",
        }, clear=True))

    def run_mail(self, *extra):
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")
        argv = [
            "send_email.py", str(self.report_path), str(self.body_path),
            "--state-dir", str(self.state_dir), "--today", self.today.isoformat(),
            "--expected-run-id", "run:1", *extra,
        ]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            send_email.main()
        return json.loads(output.getvalue())

    def queued_ids(self):
        data = json.loads((self.state_dir / "queue.json").read_text(encoding="utf-8"))
        return [item["id"] for item in data["candidates"]]

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.state_dir.iterdir()}

    def assert_no_smtp(self):
        self.smtp.assert_not_called()
        self.smtp_ssl.assert_not_called()

    def test_dry_run_preserves_all_state(self):
        before = self.snapshot()
        result = self.run_mail("--dry-run")
        self.assertEqual(result["reason"], "dry_run")
        self.assertEqual(self.snapshot(), before)
        self.assert_no_smtp()

    def test_stale_run_is_rejected_before_smtp_or_state_changes(self):
        self.report["run_id"] = "old-run:1"
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.run_mail()
        self.assertEqual(self.snapshot(), before)
        self.assert_no_smtp()

    def test_missing_expected_run_id_fails_closed(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "run_id"):
            self.run_mail("--expected-run-id", "")
        self.assertEqual(self.snapshot(), before)
        self.assert_no_smtp()

    def test_success_acknowledges_only_completed_and_uses_sender_fallback(self):
        result = self.run_mail()
        self.assertTrue(result["sent"])
        self.assertEqual(self.queued_ids(), ["pending"])
        self.assertEqual(set(state.load_seen(self.state_dir)), {"recommended"})
        message = self.smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(str(message["From"]), "sender@example.invalid")
        self.assertEqual(str(message["To"]), "recipient@example.invalid")
        self.smtp_ssl.assert_not_called()

    def test_smtp_failure_does_not_acknowledge_pending_work(self):
        self.smtp.return_value.__enter__.return_value.send_message.side_effect = smtplib.SMTPException("failed")
        before = self.snapshot()
        with self.assertRaises(smtplib.SMTPException):
            self.run_mail()
        self.assertEqual(self.snapshot(), before)

    def test_quiet_run_records_completed_work_without_sending(self):
        self.report.update(recommended=[], digest=[])
        result = self.run_mail()
        self.assertEqual(result["reason"], "nothing_to_report")
        self.assertEqual(self.queued_ids(), ["recommended", "pending"])
        self.assert_no_smtp()
        self.assertIsNone(state.freshness_ratio(self.state_dir, 1))

    def test_quiet_dry_run_does_not_record_completed_work(self):
        self.report.update(recommended=[], digest=[])
        before = self.snapshot()
        self.run_mail("--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.assert_no_smtp()

    def test_missing_body_fails_even_when_nothing_is_sent(self):
        self.report.update(recommended=[], digest=[])
        self.body_path.unlink()
        before = self.snapshot()
        with self.assertRaises(FileNotFoundError):
            self.run_mail("--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.assert_no_smtp()

    def test_duplicate_recovers_completion_after_state_update_interruption(self):
        fingerprint = self.run_mail("--dry-run")["fingerprint"]
        state.save_mail(self.state_dir, {"sent": [fingerprint]})
        result = self.run_mail()
        self.assertEqual(result["reason"], "duplicate")
        self.assertEqual(self.queued_ids(), ["pending"])
        self.assertEqual(set(state.load_seen(self.state_dir)), {"recommended"})
        self.assert_no_smtp()
        history_path = self.state_dir / "freshness.json"
        history = json.loads(history_path.read_text(encoding="utf-8"))["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["recommended_fresh"], 1)
        self.run_mail()
        self.assertEqual(json.loads(history_path.read_text(encoding="utf-8"))["history"], history)


if __name__ == "__main__":
    unittest.main()
