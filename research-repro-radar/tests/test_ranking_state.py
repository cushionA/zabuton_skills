import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import state


class RankingStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="radar_ranking_test_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state_dir = self.directory / "state"
        self.today = dt.date(2026, 10, 13)

    def candidate(self, key, **overrides):
        record = {
            "id": key,
            "title": f"{key} small model",
            "source": "arxiv",
            "url": f"https://example.invalid/{key}",
            "estimated_cpu_minutes": 20,
        }
        record.update(overrides)
        return record

    def run_script(self, name, value, today=None, check=True):
        input_path = self.directory / "input.json"
        input_path.write_text(json.dumps(value), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), str(input_path),
             "--state-dir", str(self.state_dir), "--today", (today or self.today).isoformat()],
            check=check, capture_output=True, text=True, encoding="utf-8",
        )

    def rank(self, records, today=None):
        return json.loads(self.run_script("rank_candidates.py", records, today).stdout)

    def queue(self):
        return state.load_queue(self.state_dir, self.today, 28)

    def test_interrupted_checks_remain_pending_until_acknowledged(self):
        first = self.rank([self.candidate("A"), self.candidate("B")])
        self.assertEqual([item["id"] for item in first["scheduled_checks"]], ["a"])
        self.assertEqual({state.candidate_key(item) for item in self.queue()}, {"a", "b"})

        # Simulate interruption: ranking completed, but no checks or run record did.
        resumed = self.rank([])
        self.assertEqual([item["id"] for item in resumed["scheduled_checks"]], ["a"])
        self.assertEqual({state.candidate_key(item) for item in self.queue()}, {"a", "b"})

        # Legacy recommended arrays implicitly acknowledge detailed reports.
        self.run_script("record_run.py", [{
            "id": "a", "title": "A small model", "category": "deep_dive_value",
            "published_at": self.today.isoformat(),
        }])
        self.assertEqual([state.candidate_key(item) for item in self.queue()], ["b"])
        self.assertIn("a", state.load_seen(self.state_dir))
        next_run = self.rank([])
        self.assertEqual([item["id"] for item in next_run["scheduled_checks"]], ["b"])
        self.assertEqual([state.candidate_key(item) for item in self.queue()], ["b"])

        # A checked/discarded candidate can complete without being recommended.
        result = self.run_script("record_run.py", {"recommended": [], "completed_ids": ["B"]})
        self.assertEqual(json.loads(result.stdout)["completed_queue_items"], 1)
        self.assertEqual(self.queue(), [])
        self.assertNotIn("b", state.load_seen(self.state_dir))
        self.assertEqual(self.rank([])["scheduled_checks"], [])

    def test_rediscovery_preserves_first_pending_date_and_updates_metadata(self):
        self.rank([self.candidate("A")])
        tomorrow = self.today + dt.timedelta(days=1)
        self.rank([self.candidate("A", title="Updated small model")], tomorrow)
        pending = self.queue()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["title"], "Updated small model")
        self.assertEqual(pending[0]["carried_over_since"], self.today.isoformat())
        self.assertEqual(pending[0]["carry_count"], 2)

    def test_resource_deferrals_remain_reportable_but_are_never_scheduled(self):
        result = self.rank([
            self.candidate("gpu", requires_gpu=True, estimated_gpu_minutes=5),
            self.candidate("ram", estimated_ram_gb=9),
            self.candidate("paid", estimated_cost_jpy=1),
            self.candidate("slow", estimated_cpu_minutes=21),
        ])
        expected = {"gpu": "gpu_disabled", "ram": "ram_limit",
                    "paid": "cash_budget_exceeded", "slow": "cpu_time_limit"}
        self.assertEqual({item["id"]: item["reason"] for item in result["ranked_candidates"]}, expected)
        self.assertEqual({item["id"] for item in result["review_pool"]}, set(expected))
        self.assertEqual({item["id"] for item in result["deferred"]}, set(expected))
        self.assertEqual(result["scheduled_checks"], [])
        self.assertEqual(result["rejected"], [])
        self.assertEqual(self.queue(), [])

    def test_time_deferred_reporting_keeps_reason_without_duplicate_candidates(self):
        result = self.rank([self.candidate("A"), self.candidate("B")])
        ranked = {item["id"]: item for item in result["ranked_candidates"]}
        self.assertEqual(len(result["ranked_candidates"]), 2)
        self.assertEqual(ranked["b"]["reason"], "run_time_budget")
        self.assertEqual(result["counts"]["queued"], 2)

    def test_invalid_completion_ids_cannot_mutate_recorded_state(self):
        self.rank([self.candidate("A")])
        before = (self.state_dir / "queue.json").read_bytes()
        for invalid in ("a", [None], [3], [""], ["  "]):
            with self.subTest(completed_ids=invalid):
                result = self.run_script("record_run.py", {
                    "recommended": [{"id": "a", "title": "A"}], "completed_ids": invalid,
                }, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.state_dir / "queue.json").read_bytes(), before)
                self.assertFalse((self.state_dir / "seen.json").exists())
                self.assertFalse((self.state_dir / "freshness.json").exists())

    def test_completion_ids_include_recommended_but_not_digest(self):
        report = {
            "recommended": [{"id": "A"}], "completed_ids": ["a", "B"],
            "digest": [{"id": "c"}],
        }
        self.assertEqual(state.get_completed_ids(report), ["a", "b"])

    def test_unknown_dates_do_not_activate_freshness_boost(self):
        for items in ([{"id": "unknown"}], [{"published_at": "invalid"}], []):
            state.record_freshness(self.state_dir, items, self.today, 30)
        self.assertIsNone(state.freshness_ratio(self.state_dir, 3))
        result = self.rank([self.candidate("A", published_at=self.today.isoformat())])
        self.assertFalse(result["freshness_boost"]["active"])
        self.assertIsNone(result["freshness_boost"]["recent_fresh_ratio"])

    def test_known_old_reports_still_activate_freshness_boost(self):
        for _ in range(3):
            state.record_freshness(self.state_dir, [{"published_at": "2020-01-01"}], self.today, 30)
        self.assertEqual(state.freshness_ratio(self.state_dir, 3), 0)
        self.assertTrue(self.rank([self.candidate("A")])["freshness_boost"]["active"])


if __name__ == "__main__":
    unittest.main()
