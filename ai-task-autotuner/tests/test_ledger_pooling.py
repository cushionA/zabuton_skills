"""Pooling repeated measurements must preserve the scorer's sampling units."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import paired_items  # noqa: E402
from ledger import _pool  # noqa: E402


def measured_run(run_id, items):
    return {"run_id": run_id, "items": items, "summary": {}}


class LedgerPoolingTest(unittest.TestCase):
    def test_pooled_comparison_bootstraps_whole_units(self):
        baseline = [{"id": f"{unit}-{i}", "unit": unit, "score": 0.0}
                    for unit in ("product-a", "product-b") for i in range(10)]
        candidate = [dict(item, score=float(item["unit"] == "product-a")) for item in baseline]
        a = _pool([measured_run("a1", baseline), measured_run("a2", baseline)], "cost_usd")
        b = _pool([measured_run("b1", candidate), measured_run("b2", candidate)], "cost_usd")

        comparison = paired_items(a["items"], b["items"])
        self.assertEqual((comparison["n"], comparison["units"]), (20, 2))
        self.assertEqual(comparison["ci"], (0.0, 1.0))
        self.assertEqual(comparison["band"], 1.0)

    def test_repeats_average_scores_without_replicating_items(self):
        pooled = _pool([
            measured_run("r1", [{"id": "row-a", "unit": "product", "score": 1.0}]),
            measured_run("r2", [{"id": "row-a", "unit": "product", "score": 0.0}]),
        ], "cost_usd")
        self.assertEqual(pooled["items"], [{"id": "row-a", "unit": "product", "score": 0.5}])
        self.assertEqual(pooled["n_runs"], 2)

    def test_disagreeing_unit_assignments_are_rejected(self):
        for other in ({"unit": "different-product"}, {}):
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, "conflicting units.*row-a"):
                _pool([
                    measured_run("r1", [{"id": "row-a", "unit": "product", "score": 1.0}]),
                    measured_run("r2", [{"id": "row-a", "score": 0.0, **other}]),
                ], "cost_usd")

    def test_legacy_items_remain_independent_sampling_units(self):
        items = [{"id": "row-a", "score": 0.0}, {"id": "row-b", "score": 1.0}]
        pooled = _pool([measured_run("r1", items), measured_run("r2", items)], "cost_usd")
        self.assertEqual([item["unit"] for item in pooled["items"]], ["row-a", "row-b"])
        comparison = paired_items(pooled["items"], pooled["items"])
        self.assertEqual((comparison["n"], comparison["units"]), (2, 2))


if __name__ == "__main__":
    unittest.main()
