"""Build and track a reference (expected answers) when the data has no labels.

  python reference.py merge WS RUN_A RUN_B [--split dev]
      Compare two configured reference runs (purpose=reference) item by item using
      score.flatten(unit, output) -> {key: label}. Agreements go to
      eval/reference/<split>.jsonl (source "agree:<model>"); disagreements and items
      missing from either run go to <split>.disagreements.jsonl for adjudication.
  python reference.py status WS [--split dev]
  python reference.py export-csv WS --split dev --out review.csv [--sample 40]
      Sheet for a human check (disagreements first, then a random sample of agreements).
  python reference.py import-csv WS review.csv --split dev
      Rows with a non-empty `verdict` (ok | fix | exclude) become adjudicated entries (source "human").

Adjudicate by appending lines to eval/reference/<split>.adjudicated.jsonl:
  {"key": ..., "label": ..., "source": "adjudicated:<who>", "note": "..."}
  {"key": ..., "acceptable": [labelA, labelB], "source": ..., "note": "genuinely ambiguous"}
  {"key": ..., "exclude": true, "source": ..., "note": "input data defect; reported"}
A model-made reference measures agreement with that model, not accuracy; say so in reports.
"""
import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import append_jsonl, json_label_equal, load_reference, read_json, read_jsonl  # noqa: E402
from workspace import load_eval  # noqa: E402


def _flatten_run(ws, run_dir, units_by_id, score_mod):
    rows = read_jsonl(Path(run_dir) / "outputs.jsonl")
    flat, seen_units = {}, set()
    for r in rows:
        if not isinstance(r, dict):
            sys.exit(f"REFUSED: invalid output record in {Path(run_dir).name}")
        if r.get("status") != "ok":
            continue
        unit_id = r.get("id")
        if not isinstance(unit_id, str) or unit_id not in units_by_id:
            sys.exit(f"REFUSED: unknown reference unit {unit_id!r} in {Path(run_dir).name}")
        if unit_id in seen_units:
            sys.exit(f"REFUSED: duplicate reference unit {unit_id!r} in {Path(run_dir).name}")
        seen_units.add(unit_id)
        try:
            values = score_mod.flatten(units_by_id[unit_id], r.get("output"))
        except (ValueError, TypeError) as e:
            sys.exit(f"REFUSED: invalid reference output in {Path(run_dir).name}, unit {unit_id}: {e}")
        if not isinstance(values, dict):
            sys.exit(f"REFUSED: flatten() must return a mapping in {Path(run_dir).name}, unit {unit_id}")
        for k, v in values.items():
            if not isinstance(k, str):
                sys.exit("REFUSED: flatten() keys must be strings, e.g. f\"{unit_id}|{item_key}\"")
            if k in flat:
                sys.exit(f"REFUSED: duplicate flattened reference key {k!r} in {Path(run_dir).name}")
            flat[k] = v
    meta = read_json(Path(run_dir) / "meta.json", {})
    return flat, meta.get("config", {}).get("model", "?")


def merge(ws, run_a, run_b, split):
    dataset, score_mod = load_eval(ws)
    if not hasattr(score_mod, "flatten"):
        sys.exit("score.py must define flatten(unit, output) -> {key: label} to merge references")
    units = {u["id"]: u for u in dataset.load_units(split)}
    fa, ma = _flatten_run(ws, run_a, units, score_mod)
    fb, mb = _flatten_run(ws, run_b, units, score_mod)
    base = Path(ws) / "eval" / "reference"
    base.mkdir(parents=True, exist_ok=True)
    agree_p, dis_p = base / f"{split}.jsonl", base / f"{split}.disagreements.jsonl"
    for p in (agree_p, dis_p):
        if p.exists():
            p.unlink()
    n_agree = n_dis = 0
    for k in sorted(set(fa) | set(fb), key=str):
        a, b = fa.get(k), fb.get(k)
        if k in fa and k in fb and json_label_equal(a, b):
            append_jsonl(agree_p, {"key": k, "label": a, "source": f"agree:{ma}|{mb}"})
            n_agree += 1
        else:
            append_jsonl(dis_p, {"key": k, "a": a, "b": b, "missing": [x for x, f in (("a", fa), ("b", fb)) if k not in f]})
            n_dis += 1
    total = n_agree + n_dis
    print(f"merged: {n_agree} agreed, {n_dis} to adjudicate ({n_dis / total:.1%} disagreement)" if total else "nothing to merge")


def status(ws, split):
    ref = load_reference(ws, split)
    unresolved = ref.pop("__unresolved__")
    src = {}
    for e in ref.values():
        key = "exclude" if e.get("exclude") else ("acceptable" if "acceptable" in e else (e.get("source") or "?").split(":")[0])
        src[key] = src.get(key, 0) + 1
    print(f"reference[{split}]: {len(ref)} resolved {src}; unresolved={len(unresolved)}")


def export_csv(ws, split, out, sample, seed):
    base = Path(ws) / "eval" / "reference"
    dis = read_jsonl(base / f"{split}.disagreements.jsonl")
    ref = load_reference(ws, split)
    ref.pop("__unresolved__")
    agreed = [e for e in ref.values() if str(e.get("source", "")).startswith("agree")]
    random.Random(seed).shuffle(agreed)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "kind", "label_or_a", "b", "verdict", "corrected_label_json", "note"])
        for d in dis:
            w.writerow([json.dumps(d["key"], ensure_ascii=False), "disagreement", json.dumps(d.get("a"), ensure_ascii=False),
                        json.dumps(d.get("b"), ensure_ascii=False), "", "", ""])
        for e in agreed[:sample]:
            w.writerow([json.dumps(e["key"], ensure_ascii=False), "agreed-sample",
                        json.dumps(e.get("label"), ensure_ascii=False), "", "", "", ""])
    print(f"wrote {len(dis)} disagreements + {min(sample, len(agreed))} sampled agreements to {out}")


def import_csv(ws, path, split):
    base = Path(ws) / "eval" / "reference"
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            verdict = (row.get("verdict") or "").strip().lower()
            if not verdict:
                continue
            key = json.loads(row["key"])
            entry = {"key": key, "source": "human", "note": row.get("note", "")}
            if verdict == "exclude":
                entry["exclude"] = True
            elif verdict == "fix":
                entry["label"] = json.loads(row["corrected_label_json"])
            elif verdict == "ok":
                entry["label"] = json.loads(row["label_or_a"])
            else:
                continue
            append_jsonl(base / f"{split}.adjudicated.jsonl", entry)
            n += 1
    print(f"imported {n} human verdicts")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("ws")
    m.add_argument("run_a")
    m.add_argument("run_b")
    m.add_argument("--split", default="dev")
    s = sub.add_parser("status")
    s.add_argument("ws")
    s.add_argument("--split", default="dev")
    e = sub.add_parser("export-csv")
    e.add_argument("ws")
    e.add_argument("--split", default="dev")
    e.add_argument("--out", required=True)
    e.add_argument("--sample", type=int, default=40)
    e.add_argument("--seed", type=int, default=0)
    i = sub.add_parser("import-csv")
    i.add_argument("ws")
    i.add_argument("csv")
    i.add_argument("--split", default="dev")
    a = ap.parse_args()
    if a.cmd == "merge":
        merge(a.ws, a.run_a, a.run_b, a.split)
    elif a.cmd == "status":
        status(a.ws, a.split)
    elif a.cmd == "export-csv":
        export_csv(a.ws, a.split, a.out, a.sample, a.seed)
    else:
        import_csv(a.ws, a.csv, a.split)


if __name__ == "__main__":
    main()
