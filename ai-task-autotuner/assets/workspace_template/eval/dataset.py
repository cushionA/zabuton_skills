"""Units of work for the task. Frozen once eval validation passes (edits change the eval hash).

load_units(split) -> list of {"id": str, ...}. Splits live in eval/splits.json:
  {"dev": [...ids], "holdout": [...ids], "full": [...ids]}  (+ any named subsets)
group_of(unit) -> key; units sharing a key must sit in the same split (near-duplicates,
same source page, same parent product). validate_eval.py checks it.

Make splits once (random, stratified, group-aware - never by baseline score):
  python eval/dataset.py --make-splits --dev 10 --holdout 10 --seed 0
"""
import argparse
import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
CHARTER = json.loads((WS / "charter.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(Path(CHARTER["skill_dir"]) / "scripts"))
from common import make_splits, read_json, write_json  # noqa: E402

ID_FIELD = "id"  # TODO: field that identifies a unit
_CACHE = {}


def _records():
    if "recs" not in _CACHE:
        path = CHARTER["data"]["inputs"][0]
        with open(path, encoding="utf-8") as f:
            if str(path).endswith(".jsonl"):
                recs = [json.loads(line) for line in f if line.strip()]
            else:
                recs = json.load(f)
        _CACHE["recs"] = {str(r[ID_FIELD]): r for r in recs}
    return _CACHE["recs"]


def load_units(split):
    recs = _records()
    splits = read_json(WS / "eval" / "splits.json", {})
    ids = list(recs) if split == "all" else splits[split]
    return [{"id": i, "input": recs[i]} for i in ids]


def group_of(unit):
    return unit["id"]  # TODO: e.g. normalized title or source URL when near-duplicates exist


def stratum_of(unit):
    return "_"  # TODO: e.g. category or size bucket


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-splits", action="store_true")
    ap.add_argument("--dev", type=int, required="--make-splits" in sys.argv)
    ap.add_argument("--holdout", type=int, required="--make-splits" in sys.argv)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    if a.make_splits:
        units = load_units("all")
        sp = make_splits([u["id"] for u in units], a.dev, a.holdout, a.seed,
                         groups={u["id"]: group_of(u) for u in units},
                         strata={u["id"]: stratum_of(u) for u in units})
        sp["full"] = [u["id"] for u in units]
        write_json(WS / "eval" / "splits.json", sp)
        print({k: len(v) for k, v in sp.items()})
