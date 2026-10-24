"""Prove the evaluation can be trusted before the improvement loop starts.

  python validate_eval.py WS --runs WS/runs/<baseline_r1> WS/runs/<baseline_r2> \
      --spotcheck "read 8 lowest-scoring baseline items; grader correct on all; 1 ref fixed (key ...)"

Checks (all must pass; results go to WS/eval/validation.json, pinned to the eval hash):
  splits     dev/holdout non-empty, disjoint, and no shared group (dataset.group_of) across them
  null       all-missing outputs are at least as bad as the configured null bound
  determinism scoring the same outputs twice gives identical results
  oracle     score.oracle(units, split) outputs reach the configured optimum
             (skip only with --no-oracle REASON)
  corrupt    score.corrupt(output, rng) on oracle outputs worsens the primary (if defined)
  reference  complete dev/holdout item coverage with no unresolved disagreements
             (score.reference_coverage, if defined; explicit exclusions count as covered)
  rescore    stored baseline scores reproduce from stored outputs
  identity   baseline outputs were generated from the current source data and scored with the current eval
  noise      two baseline runs give the noise band; must be smaller than the headroom
  spotcheck  a human-style read of the worst baseline items was done (--spotcheck note)
Optional score.trivial(units, split) reports a trivial baseline (e.g. majority class).
"""
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import mean, now_iso, paired_items, read_json, read_jsonl, write_json  # noqa: E402
from workspace import charter as load_charter, data_fingerprint, eval_hash, load_eval  # noqa: E402


def _score(score_mod, units, outputs, split):
    sc = score_mod.score(units, outputs, split)
    if not isinstance(sc, dict) or not _finite(sc.get("primary")):
        raise ValueError("score.primary must be a finite number (not a boolean)")
    return sc


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _primary_contract(primary):
    """Directional checks; defaults describe normalized correctness/error rates."""
    if not isinstance(primary, dict):
        raise ValueError("primary must be an object")
    direction = primary.get("direction", "max")
    item_direction = primary.get("item_direction", "max")
    if direction not in ("max", "min") or item_direction not in ("max", "min"):
        raise ValueError("primary.direction and primary.item_direction must be max or min")
    sign = 1 if direction == "max" else -1
    null_key, oracle_key = ("null_max", "oracle_min") if sign == 1 else ("null_min", "oracle_max")
    null_bound = primary.get(null_key, 0.0 if sign == 1 else 1.0)
    oracle_bound = primary.get(oracle_key, 0.999 if sign == 1 else 0.001)
    optimum = primary.get("optimum", primary.get("ceiling", 1.0) if sign == 1 else 0.0)
    for name, value in ((null_key, null_bound), (oracle_key, oracle_bound), ("optimum", optimum)):
        if not _finite(value):
            raise ValueError(f"primary.{name} must be a finite number (not a boolean)")
    if sign * (optimum - oracle_bound) < -1e-9:
        raise ValueError("primary.optimum must be at least as good as the oracle threshold")
    if sign * (oracle_bound - null_bound) <= 0:
        raise ValueError("the oracle threshold must be strictly better than the null bound")
    return {"direction": direction, "item_direction": item_direction, "sign": sign,
            "null_key": null_key, "null_bound": null_bound,
            "oracle_key": oracle_key, "oracle_bound": oracle_bound, "optimum": optimum}


def _reference_coverage_check(score_mod, units, split):
    """Fail closed on incomplete or malformed reference coverage reports."""
    try:
        cov = score_mod.reference_coverage(units, split)
    except Exception as e:
        return False, {"split": split, "error": f"{type(e).__name__}: {e}"}
    if not isinstance(cov, dict):
        return False, {"split": split, "error": "reference_coverage must return a dict"}

    # Existing custom scorers may use the more explicit *_items names.
    fields = ("covered_items", "total_items") if "covered_items" in cov or "total_items" in cov else ("covered", "total")
    covered, total, unresolved = (cov.get(k) for k in (*fields, "unresolved"))

    def count(value):
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value >= 0
        return isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer()

    detail = {"split": split, **cov}
    if not all(count(v) for v in (covered, total, unresolved)):
        detail["error"] = "coverage counts must be finite non-negative integers (covered, total, unresolved)"
        return False, detail
    if total == 0:
        detail["error"] = "reference coverage has no expected items"
        return False, detail
    if covered != total or unresolved != 0:
        detail["error"] = "every expected item must have a resolved reference or explicit exclusion"
        return False, detail
    return True, detail


def _id_key(value):
    if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
        raise ValueError("unit/item ids must be finite JSON scalars")
    return type(value).__name__, json.dumps(value, ensure_ascii=False)


def _exact_ids(values, expected, label):
    if not isinstance(values, list):
        return [f"{label} must be a list"]
    try:
        keys = [_id_key(value) for value in values]
        expected_keys = [_id_key(value) for value in expected]
    except ValueError as e:
        return [f"{label}: {e}"]
    errors = []
    if len(set(keys)) != len(keys):
        errors.append(f"{label} contains duplicate ids")
    if set(keys) != set(expected_keys):
        errors.append(f"{label} must contain every dev unit exactly once (missing={len(set(expected_keys) - set(keys))}, "
                      f"extra={len(set(keys) - set(expected_keys))})")
    return errors


def _run_identity_check(ws, runs, units=None, split="dev"):
    """Require distinct, complete dev generations on the current inputs/eval."""
    current_data, current_eval = data_fingerprint(ws), eval_hash(ws)
    problems = []
    if units is None:
        dataset, _ = load_eval(ws)
        units = dataset.load_units(split)
    expected = [unit["id"] for unit in units]
    expected_error = _exact_ids(expected, expected, "dev dataset")
    if not expected:
        expected_error.append("dev dataset has no units")
    if expected_error:
        problems.append({"run": "dev dataset", "reasons": expected_error})
    seen_paths, seen_run_ids = set(), set()
    for run in runs:
        reasons = []
        path = Path(run).resolve()
        if path in seen_paths:
            reasons.append("the same run directory was supplied more than once; use independent runs")
        seen_paths.add(path)
        try:
            meta = read_json(path / "meta.json", {})
            rows = read_jsonl(path / "outputs.jsonl")
            if not isinstance(meta, dict):
                raise ValueError("run metadata must be an object")
            if not (path / "outputs.jsonl").is_file():
                raise ValueError("outputs.jsonl is missing")
        except (OSError, ValueError, TypeError) as e:
            problems.append({"run": str(run), "reasons": reasons + [f"invalid run evidence: {e}"]})
            continue
        if meta.get("run_id") is not None:
            try:
                run_key = _id_key(meta["run_id"])
                if run_key in seen_run_ids:
                    reasons.append("duplicate generation run_id; copied evidence is not an independent run")
                seen_run_ids.add(run_key)
            except ValueError as e:
                reasons.append(str(e))
        if meta.get("limit") is not None:
            reasons.append("limited/partial runs cannot establish full-dev noise")
        if meta.get("split") not in (None, split):
            reasons.append(f"run split does not match requested dev split {split!r}")
        reasons += _exact_ids(meta.get("unit_ids"), expected, "meta.unit_ids")
        if not all(isinstance(row, dict) and "id" in row for row in rows):
            reasons.append("every output row must be an object with a unit id")
        else:
            reasons += _exact_ids([row["id"] for row in rows], expected, "outputs.jsonl")
            if any(row.get("status") not in ("ok", "system_error", "harness_error")
                   or row.get("status") == "ok" and "output" not in row for row in rows):
                reasons.append("output rows need a recorded status and successful output payload")
        if not meta.get("data_hash"):
            reasons.append("generation data snapshot missing; rerun on current data (legacy outputs cannot prove equivalence)")
        elif meta["data_hash"] != current_data:
            reasons.append("source data or input-unit definition changed; rerun (rescoring old outputs is insufficient)")
        if meta.get("eval_hash") != current_eval:
            reasons.append("eval changed; rescore only after generation data identity passes")
        if reasons:
            problems.append({"run": str(run), "reasons": reasons})
    return not problems, {"data_hash": current_data, "eval_hash": current_eval, "problems": problems}


def _noise_items(runs):
    """Never estimate repeat noise from a silently intersected item subset."""
    items, expected, expected_units = [], None, None
    for run in runs:
        values = read_json(Path(run) / "score.json", {}).get("items")
        if not isinstance(values, list) or not values:
            raise ValueError(f"{run}: noise needs a nonempty scored-item list")
        if not all(isinstance(item, dict) and "id" in item and _finite(item.get("score")) for item in values):
            raise ValueError(f"{run}: scored items need ids and finite scores")
        keys = [_id_key(item["id"]) for item in values]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{run}: duplicate scored item ids cannot establish noise")
        if expected is None:
            expected = set(keys)
        elif set(keys) != expected:
            raise ValueError(f"{run}: repeat scored items differ; cannot estimate noise on their intersection")
        assignments = {_id_key(item["id"]): _id_key(item.get("unit") or item["id"]) for item in values}
        if expected_units is None:
            expected_units = assignments
        elif assignments != expected_units:
            raise ValueError(f"{run}: repeat item unit assignments differ; cannot estimate clustered noise")
        items.append(values)
    return items


def _score_evidence(score):
    if not isinstance(score, dict) or not _finite(score.get("primary")) or not isinstance(score.get("items"), list):
        raise ValueError("scored evidence must include a finite primary and item list")
    items = []
    for item in score["items"]:
        if not isinstance(item, dict) or "id" not in item or not _finite(item.get("score")):
            raise ValueError("scored items need ids and finite numeric scores")
        items.append((_id_key(item["id"]), _id_key(item.get("unit") or item["id"]), item["score"]))
    return {"primary": score.get("primary"), "metrics": score.get("metrics", {}),
            "gate_failures": score.get("gate_failures", []),
            "items": sorted(items)}


def _baseline_evidence(runs, units, score_mod, split):
    """Recompute evidence and reject invalid stable outputs before learning noise."""
    records, rescore_errors, gate_errors, harness_errors = [], [], [], []
    by_id = {unit["id"]: unit for unit in units}
    for run in runs:
        try:
            path = Path(run)
            meta = read_json(path / "meta.json")
            stored = read_json(path / "score.json", {})
            summary = read_json(path / "summary.json", {})
            rows = read_jsonl(path / "outputs.jsonl")
            ordered_units = [by_id[uid] for uid in meta["unit_ids"]]
            outputs = {row["id"]: row["output"] if row["status"] == "ok" else None for row in rows}
            fresh = _score(score_mod, ordered_units, outputs, split)
            if _score_evidence(fresh) != _score_evidence(stored):
                rescore_errors.append({"run": str(run), "error": "stored primary/metrics/item evidence does not reproduce"})
            gate_values = (fresh.get("gate_failures", []), stored.get("gate_failures", []))
            reported_gates = summary.get("gate_failures", 0)
            if (any(not isinstance(value, list) or value for value in gate_values)
                    or not _finite(reported_gates) or reported_gates != 0):
                gate_errors.append({"run": str(run), "error": "baseline output has hard-gate failures or invalid gate metadata"})
            reported_harness = summary.get("harness_error_rate", 0)
            observed_harness = sum(row["status"] == "harness_error" for row in rows) / len(rows)
            if (not _finite(reported_harness) or not 0 <= reported_harness <= 1
                    or max(reported_harness, observed_harness) > 0.05
                    or any(summary.get(flag) for flag in ("quota_exhausted", "policy_exhausted", "data_changed"))):
                harness_errors.append({"run": str(run), "error": "baseline contains interrupted or unhealthy execution"})
            records.append({"primary": fresh["primary"],
                            "harness": max(reported_harness, observed_harness) if _finite(reported_harness) else None})
        except Exception as e:
            rescore_errors.append({"run": str(run), "error": f"{type(e).__name__}: {e}"})
    return {"records": records, "rescore_errors": rescore_errors, "gate_errors": gate_errors,
            "harness_errors": harness_errors,
            "ok": not (rescore_errors or gate_errors or harness_errors) and len(records) == len(runs)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--runs", nargs="*", default=[], help="two (or more) baseline runs on dev")
    ap.add_argument("--spotcheck", default="", help="what you read and what you concluded")
    ap.add_argument("--no-oracle", metavar="REASON", help="only when an oracle output cannot be built")
    ap.add_argument("--dev", default="dev")
    ap.add_argument("--holdout", default="holdout")
    ap.add_argument("--renoise", nargs=2, metavar=("RUN_A", "RUN_B"),
                    help="re-measure the noise floor from two runs of the current best (e.g. at phase switch)")
    a = ap.parse_args()
    ws = Path(a.ws)
    ch = load_charter(ws)
    prim = ch.get("primary", {})
    try:
        contract = _primary_contract(prim)
    except ValueError as e:
        ap.error(str(e))
    direction = contract["sign"]
    dataset, score_mod = load_eval(ws)
    dev = dataset.load_units(a.dev)
    if a.renoise:
        identity_ok, detail = _run_identity_check(ws, a.renoise, dev, a.dev)
        if not identity_ok:
            sys.exit("REFUSED: noise runs do not describe the current inputs/eval: " + json.dumps(detail))
        try:
            items = _noise_items(a.renoise)
        except (OSError, ValueError, TypeError) as e:
            sys.exit(f"REFUSED: noise run items are incomplete: {e}")
        evidence = _baseline_evidence(a.renoise, dev, score_mod, a.dev)
        if not evidence["ok"]:
            sys.exit("REFUSED: noise runs contain invalid baseline evidence: " + json.dumps(evidence))
        pi = paired_items(items[0], items[1])
        floor = max(pi["band"] or 0.0, 1.0 / (pi["n"] or 1))
        old = ch.get("noise") or {}
        ch.setdefault("noise_history", []).append(old)
        ch["noise"] = dict(old, band=pi["band"], flip_items=pi["improved"] + pi["regressed"], n_items=pi["n"],
                           units=pi["units"], min_delta=floor, renoised_from=[str(r) for r in a.renoise],
                           renoised_at=now_iso())
        if old.get("min_delta") and floor < old["min_delta"] and pi["top5_unit_item_share"] > 0.3:
            print("  caution: few large units carry most items; two runs can agree by chance - "
                  "keep the larger min_delta unless more repeats confirm the lower one")
        write_json(ws / "charter.json", ch)
        print(f"noise re-measured on the incumbent: min_delta {old.get('min_delta')} -> {floor:.4f} "
              f"({ch['noise']['flip_items']} flips / {pi['n']} items)")
        return
    checks = []

    def add(name, ok, detail, required=True):
        checks.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})

    hold = dataset.load_units(a.holdout)
    dev_ids, hold_ids = {u["id"] for u in dev}, {u["id"] for u in hold}
    overlap = dev_ids & hold_ids
    detail = {"dev": len(dev), "holdout": len(hold), "overlap": len(overlap),
              "duplicate_dev_ids": len(dev) - len(dev_ids), "duplicate_holdout_ids": len(hold) - len(hold_ids)}
    ok = bool(dev) and bool(hold) and not overlap and len(dev) == len(dev_ids) and len(hold) == len(hold_ids)
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
        add("null", direction * (sc_null["primary"] - contract["null_bound"]) <= 1e-9,
            {"primary": sc_null["primary"], contract["null_key"]: contract["null_bound"],
             "direction": contract["direction"]})
    except Exception as e:
        add("null", False, f"scorer crashed on missing outputs: {type(e).__name__}: {e}")

    oracle_out = None
    if hasattr(score_mod, "oracle"):
        try:
            oracle_out = score_mod.oracle(dev, a.dev)
            s1 = _score(score_mod, dev, oracle_out, a.dev)
            s2 = _score(score_mod, dev, json.loads(json.dumps(oracle_out)), a.dev)
            add("oracle", direction * (s1["primary"] - contract["oracle_bound"]) >= -1e-9
                and direction * (contract["optimum"] - s1["primary"]) >= -1e-9
                and not s1.get("gate_failures"),
                {"primary": s1["primary"], contract["oracle_key"]: contract["oracle_bound"],
                 "optimum": contract["optimum"], "direction": contract["direction"],
                 "gate_failures": s1.get("gate_failures", [])[:3]})
            add("determinism", s1.get("primary") == s2.get("primary") and s1.get("metrics") == s2.get("metrics"),
                {"primary": [s1.get("primary"), s2.get("primary")]})
        except Exception as e:
            add("oracle", False, f"{type(e).__name__}: {e}")
    elif a.no_oracle:
        add("oracle", True, f"skipped: {a.no_oracle}", required=False)
    else:
        add("oracle", False, "score.oracle not defined; implement it or pass --no-oracle REASON")

    if oracle_out is not None and hasattr(score_mod, "corrupt"):
        try:
            rng = random.Random(0)
            corrupted = {k: (score_mod.corrupt(v, rng) if v is not None else None) for k, v in oracle_out.items()}
            s3 = _score(score_mod, dev, corrupted, a.dev)
            base = _score(score_mod, dev, oracle_out, a.dev)["primary"]
            add("corrupt", direction * (s3["primary"] - base) < 0,
                {"oracle": base, "corrupted": s3["primary"], "direction": contract["direction"]})
        except Exception as e:
            add("corrupt", False, f"{type(e).__name__}: {e}")

    if hasattr(score_mod, "reference_coverage"):
        coverage = [_reference_coverage_check(score_mod, units, split)
                    for units, split in ((dev, a.dev), (hold, a.holdout))]
        add("reference", all(ok for ok, _ in coverage), {"splits": [detail for _, detail in coverage]})

    if hasattr(score_mod, "trivial"):
        st = _score(score_mod, dev, score_mod.trivial(dev, a.dev), a.dev)
        add("trivial_baseline", True, {"primary": st.get("primary"), "metrics": st.get("metrics")}, required=False)

    noise = None
    if len(a.runs) >= 2:
        identity_ok, identity = _run_identity_check(ws, a.runs, dev, a.dev)
        add("identity", identity_ok, identity)
    else:
        identity_ok = False
    if len(a.runs) >= 2 and identity_ok:
        try:
            run_items = _noise_items(a.runs)
            add("noise_items", True, {"runs": len(run_items), "items_per_run": len(run_items[0])})
        except (OSError, ValueError, TypeError) as e:
            identity_ok = False
            add("noise_items", False, str(e))
    if len(a.runs) >= 2 and identity_ok:
        evidence = _baseline_evidence(a.runs, dev, score_mod, a.dev)
        add("rescore", not evidence["rescore_errors"], {"errors": evidence["rescore_errors"]})
        add("baseline_gates", not evidence["gate_errors"], {"errors": evidence["gate_errors"]})
        add("harness_health", not evidence["harness_errors"], {"errors": evidence["harness_errors"]})
        identity_ok = evidence["ok"]
    if len(a.runs) >= 2 and identity_ok:
        prims = [record["primary"] for record in evidence["records"]]
        harness = [record["harness"] for record in evidence["records"]]
        items = run_items[:2]
        pi = paired_items(items[0], items[1])
        n_items = pi["n"] or 1
        floor = max(pi["band"] or 0.0, 1.0 / n_items)
        headroom = direction * (contract["optimum"] - mean(prims)) if prims and None not in prims else None
        noise = {"band": pi["band"], "band_items_independent": pi["band_items_independent"],
                 "flip_items": pi["improved"] + pi["regressed"], "n_items": pi["n"], "units": pi["units"],
                 "top5_unit_item_share": pi["top5_unit_item_share"], "primary_runs": prims, "min_delta": floor,
                 "headroom": headroom, "direction": contract["direction"], "optimum": contract["optimum"],
                 "harness_error_rates": harness}
        if pi["top5_unit_item_share"] > 0.3:
            print(f"  note: 5 units carry {pi['top5_unit_item_share']:.0%} of the items - whole-unit flips dominate "
                  "the noise; consider more repeats per candidate or a per-unit metric")
        add("noise", headroom is not None and floor < headroom, noise)
    else:
        add("noise", False, "pass two baseline runs on the current data/eval with --runs to measure the noise floor")

    add("spotcheck", len(a.spotcheck.strip()) >= 20, a.spotcheck.strip() or "missing --spotcheck note")

    passed = all(c["ok"] for c in checks if c["required"])
    out = {"passed": passed, "eval_hash": eval_hash(ws), "data_hash": data_fingerprint(ws),
           "created": now_iso(), "checks": checks, "noise": noise}
    write_json(ws / "eval" / "validation.json", out)
    if noise and passed:
        ch["noise"] = noise
        write_json(ws / "charter.json", ch)
    print(f"EVAL VALIDATION {'PASSED' if passed else 'FAILED'}  eval_hash={out['eval_hash']}")
    for c in checks:
        mark = "ok " if c["ok"] else ("FAIL" if c["required"] else "warn")
        print(f"  [{mark}] {c['check']}: {json.dumps(c['detail'], ensure_ascii=False)[:220]}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
