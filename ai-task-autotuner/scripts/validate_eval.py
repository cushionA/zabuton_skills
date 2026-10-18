"""Prove the evaluation can be trusted before the improvement loop starts.

  python validate_eval.py WS --runs WS/runs/<baseline_r1> WS/runs/<baseline_r2> \
      --spotcheck "read 8 lowest-scoring baseline items; grader correct on all; 1 ref fixed (key ...)"

Checks (all must pass; results go to WS/eval/validation.json, pinned to the eval hash):
  splits     dev/holdout non-empty, disjoint, and no shared group (dataset.group_of) across them
  null       all-missing outputs score at the floor without crashing
  determinism scoring the same outputs twice gives identical results
  oracle     score.oracle(units, split) outputs reach the ceiling (skip only with --no-oracle REASON)
  corrupt    score.corrupt(output, rng) on oracle outputs lowers the score (if defined)
  reference  no unresolved reference disagreements (score.reference_coverage, if defined)
  rescore    stored baseline scores reproduce from stored outputs
  noise      two baseline runs give the noise band; must be smaller than the headroom
  spotcheck  a human-style read of the worst baseline items was done (--spotcheck note)
Optional score.trivial(units, split) reports a trivial baseline (e.g. majority class).
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import mean, now_iso, paired_items, read_json, read_jsonl, write_json  # noqa: E402
from workspace import charter as load_charter, eval_hash, load_eval  # noqa: E402


def _score(score_mod, units, outputs, split):
    sc = score_mod.score(units, outputs, split)
    return sc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--runs", nargs="*", default=[], help="two (or more) baseline runs on dev")
    ap.add_argument("--spotcheck", default="", help="what you read and what you concluded")
    ap.add_argument("--no-oracle", metavar="REASON", help="only when an oracle output cannot be built")
    ap.add_argument("--dev", default="dev")
    ap.add_argument("--holdout", default="holdout")
    a = ap.parse_args()
    ws = Path(a.ws)
    ch = load_charter(ws)
    prim = ch.get("primary", {})
    null_max = prim.get("null_max", 0.0)
    oracle_min = prim.get("oracle_min", 0.999)
    dataset, score_mod = load_eval(ws)
    checks = []

    def add(name, ok, detail, required=True):
        checks.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})

    dev = dataset.load_units(a.dev)
    hold = dataset.load_units(a.holdout)
    dev_ids, hold_ids = {u["id"] for u in dev}, {u["id"] for u in hold}
    overlap = dev_ids & hold_ids
    detail = {"dev": len(dev), "holdout": len(hold), "overlap": len(overlap)}
    ok = bool(dev) and bool(hold) and not overlap
    if hasattr(dataset, "group_of"):
        gd = {dataset.group_of(u) for u in dev}
        gh = {dataset.group_of(u) for u in hold}
        shared = gd & gh
        detail["shared_groups"] = sorted(map(str, shared))[:10]
        ok = ok and not shared
    else:
        detail["group_of"] = "not defined (near-duplicate units may leak across splits)"
    add("splits", ok, detail)

    try:
        sc_null = _score(score_mod, dev, {u["id"]: None for u in dev}, a.dev)
        add("null", sc_null.get("primary") is not None and sc_null["primary"] <= null_max + 1e-9,
            {"primary": sc_null.get("primary"), "null_max": null_max})
    except Exception as e:
        add("null", False, f"scorer crashed on missing outputs: {type(e).__name__}: {e}")

    oracle_out = None
    if hasattr(score_mod, "oracle"):
        try:
            oracle_out = score_mod.oracle(dev, a.dev)
            s1 = _score(score_mod, dev, oracle_out, a.dev)
            s2 = _score(score_mod, dev, json.loads(json.dumps(oracle_out)), a.dev)
            add("oracle", s1.get("primary", 0) >= oracle_min and not s1.get("gate_failures"),
                {"primary": s1.get("primary"), "oracle_min": oracle_min, "gate_failures": s1.get("gate_failures", [])[:3]})
            add("determinism", s1.get("primary") == s2.get("primary") and s1.get("metrics") == s2.get("metrics"),
                {"primary": [s1.get("primary"), s2.get("primary")]})
        except Exception as e:
            add("oracle", False, f"{type(e).__name__}: {e}")
    elif a.no_oracle:
        add("oracle", True, f"skipped: {a.no_oracle}", required=False)
    else:
        add("oracle", False, "score.oracle not defined; implement it or pass --no-oracle REASON")

    if oracle_out is not None and hasattr(score_mod, "corrupt"):
        rng = random.Random(0)
        corrupted = {k: (score_mod.corrupt(v, rng) if v is not None else None) for k, v in oracle_out.items()}
        s3 = _score(score_mod, dev, corrupted, a.dev)
        base = _score(score_mod, dev, oracle_out, a.dev).get("primary")
        add("corrupt", s3.get("primary") is not None and s3["primary"] < base,
            {"oracle": base, "corrupted": s3.get("primary")}, required=False)

    if hasattr(score_mod, "reference_coverage"):
        cov = score_mod.reference_coverage(dev, a.dev)
        add("reference", not cov.get("unresolved"), cov)

    if hasattr(score_mod, "trivial"):
        st = _score(score_mod, dev, score_mod.trivial(dev, a.dev), a.dev)
        add("trivial_baseline", True, {"primary": st.get("primary"), "metrics": st.get("metrics")}, required=False)

    noise = None
    if len(a.runs) >= 2:
        prims, harness = [], []
        rescore_ok = True
        by_id = {u["id"]: u for u in dev}
        for r in a.runs:
            meta = read_json(Path(r) / "meta.json")
            stored = read_json(Path(r) / "score.json", {})
            rows = read_jsonl(Path(r) / "outputs.jsonl")
            units = [by_id[i] for i in meta["unit_ids"] if i in by_id]
            outs = {row["id"]: (row["output"] if row["status"] == "ok" else None) for row in rows}
            again = _score(score_mod, units, outs, a.dev)
            if again.get("primary") != stored.get("primary"):
                rescore_ok = False
            prims.append(again.get("primary"))
            summ = read_json(Path(r) / "summary.json", {})
            harness.append(summ.get("harness_error_rate", 0))
        add("rescore", rescore_ok, {"note": "stored primary reproduces from outputs" if rescore_ok else
                                    "stored score differs (eval changed? run --rescore on these runs first)"})
        items = [read_json(Path(r) / "score.json", {}).get("items", []) for r in a.runs[:2]]
        pi = paired_items(items[0], items[1])
        n_items = pi["n"] or 1
        floor = max(pi["band"] or 0.0, 1.0 / n_items)
        headroom = (prim.get("ceiling", 1.0) - mean(prims)) if prims and None not in prims else None
        noise = {"band": pi["band"], "flip_items": pi["improved"] + pi["regressed"], "n_items": pi["n"],
                 "primary_runs": prims, "min_delta": floor, "headroom": headroom,
                 "harness_error_rates": harness}
        add("noise", headroom is not None and floor < headroom, noise)
        add("harness_health", max(harness) <= 0.05, {"harness_error_rates": harness})
    else:
        add("noise", False, "pass two baseline runs with --runs to measure the noise floor")

    add("spotcheck", len(a.spotcheck.strip()) >= 20, a.spotcheck.strip() or "missing --spotcheck note")

    passed = all(c["ok"] for c in checks if c["required"])
    out = {"passed": passed, "eval_hash": eval_hash(ws), "created": now_iso(), "checks": checks, "noise": noise}
    write_json(ws / "eval" / "validation.json", out)
    if noise:
        ch["noise"] = noise
        write_json(ws / "charter.json", ch)
    print(f"EVAL VALIDATION {'PASSED' if passed else 'FAILED'}  eval_hash={out['eval_hash']}")
    for c in checks:
        mark = "ok " if c["ok"] else ("FAIL" if c["required"] else "warn")
        print(f"  [{mark}] {c['check']}: {json.dumps(c['detail'], ensure_ascii=False)[:220]}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
