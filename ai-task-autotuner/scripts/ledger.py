"""Experiment ledger: pre-registered hypotheses, keep/discard decisions, phase and stop rules,
and anti-dead-end steering. State lives in WS/ledger.jsonl; `status` is the resume card.

  python ledger.py propose WS --candidate c004-x --parent c002-y --lever prompt.instructions \
      --hypothesis "..." --expect "+0.03 primary; fixes unit-conflict tag" --falsifier "no change on unit-conflict items"
  python ledger.py decide WS E004 [--split dev] [--override keep|discard --reason "..."]
  python ledger.py status WS [--write]          # <=30 lines; --write also saves WS/STATE.md
  python ledger.py compare WS RUN_A RUN_B       # paired item comparison
  python ledger.py mark WS fresh-eyes "what the fresh look found"   # or: categorize, headroom
  python ledger.py phase WS efficiency [--floor 0.82] --reason "..."
  python ledger.py clock WS pause|resume --reason "waiting for the user" [--at ISO]  # limits count active time
  python ledger.py history WS                  # markdown table of all experiments for the report
  python ledger.py void WS RUN_ID --reason "..."   # exclude an infra-broken run from decisions
  python ledger.py stop WS --reason "..."

Levers (use the group.detail form; groups drive the diversity rules):
  prompt.instructions prompt.examples prompt.reasoning input.representation input.selection
  output.format decomposition.split decomposition.batch deterministic.pre deterministic.post
  model.choice model.effort ensemble.vote ensemble.verify ensemble.cascade context.knowledge
  harness.parsing simplify.ablation baseline
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import append_jsonl, now_iso, paired_items, read_json, text_similarity, write_json  # noqa: E402
from workspace import (candidate_hash, charter as load_charter, data_fingerprint, effective_config_fingerprint,
                       eval_hash, ledger_rows, list_runs)  # noqa: E402
from task_review import task_review_allows_runs  # noqa: E402
from run_policy import active_hours as policy_active_hours, limit_state  # noqa: E402

LEVER_GROUPS = ["prompt", "input", "output", "decomposition", "deterministic", "model", "ensemble",
                "context", "simplify", "harness"]
KEEPS = ("keep", "baseline")


def _get(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if cur is None and dotted.endswith("_per_unit") and isinstance(d, dict):
        total = d.get(dotted[: -len("_per_unit")])  # runs measured before the metric existed
        units = d.get("units")
        cur = total / units if _finite(total) and _finite(units) and units > 0 else None
    return cur


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _complete_mean(values, *, nonnegative=False):
    """An unknown repeat cannot become a measured zero or disappear from the average."""
    if not values or any(not _finite(v) or (nonnegative and v < 0) for v in values):
        return None
    return sum(values) / len(values)


def _efficiency_ratio(candidate, baseline):
    if not _finite(candidate) or not _finite(baseline) or candidate < 0 or baseline < 0:
        return None
    if baseline == 0:
        return 1.0 if candidate == 0 else None  # a positive cost cannot improve on free
    return candidate / baseline


def _state(ws):
    rows = ledger_rows(ws)
    props, decisions, marks, phases, stops = {}, {}, [], [], []
    for r in rows:
        ev = r.get("event")
        if ev == "propose":
            props[r["exp"]] = r
        elif ev == "decide":
            decisions[r["exp"]] = r
        elif ev == "mark":
            marks.append(r)
        elif ev == "phase":
            phases.append(r)
        elif ev == "stop":
            stops.append(r)
    best = None
    for r in rows:
        if r.get("event") == "decide" and r.get("decision") in KEEPS:
            best = r
    return {"rows": rows, "props": props, "decisions": decisions, "marks": marks, "phases": phases,
            "stops": stops, "best": best}


def _next_exp_id(props):
    n = 0
    for k in props:
        try:
            n = max(n, int(k.lstrip("E")))
        except ValueError:
            pass
    return f"E{n + 1:03d}"


def _exp_runs(ws, exp, split):
    ws = Path(ws)
    eh = eval_hash(ws)
    dh = data_fingerprint(ws)
    rows = ledger_rows(ws)
    prop = next((r for r in reversed(rows) if r.get("event") == "propose" and r.get("exp") == exp), None)
    if not prop:
        return []
    candidate = prop.get("candidate")
    cand_dir = ws / "candidates" / str(candidate)
    valid_path = isinstance(candidate, str) and candidate not in ("", ".", "..") and "/" not in candidate and "\\" not in candidate
    current_hash = candidate_hash(cand_dir) if valid_path and (cand_dir / "pipeline.py").is_file() else None
    voided = {r["run"] for r in rows if r.get("event") == "void_run"}
    out = []
    for m in list_runs(ws):
        if m["run_id"] in voided or any(m["_summary"].get(flag) for flag in
                                       ("quota_exhausted", "policy_exhausted", "data_changed")):
            continue  # infra-broken runs never enter a decision
        if m.get("exp") == exp and m.get("split") == split and m.get("purpose") in ("experiment", "baseline", "confirm"):
            if m.get("limit"):
                continue  # partial runs never count
            score = read_json(Path(m["_dir"]) / "score.json", {})
            invalid = []
            if m.get("candidate") != candidate:
                invalid.append(f"candidate {m.get('candidate')!r} does not match proposal {candidate!r}")
            if current_hash is None or m.get("candidate_hash") != current_hash:
                invalid.append("candidate content differs from the measured candidate hash")
            if score.get("eval_hash") != m.get("eval_hash"):
                invalid.append("score and run metadata have different eval hashes")
            config_hash = effective_config_fingerprint(m.get("config"))
            if config_hash is None:
                invalid.append("effective execution config is missing or unresolved")
            elif m.get("config_hash") not in (None, config_hash):
                invalid.append("effective execution config differs from its recorded hash")
            out.append({"run_id": m["run_id"], "summary": m["_summary"], "items": score.get("items", []),
                        "invalid": invalid, "config_hash": config_hash,
                        "stale": bool(invalid) or m.get("eval_hash") != eh or m.get("data_hash") != dh})
    if len({r["config_hash"] for r in out if r["config_hash"] is not None}) > 1:
        for run in out:
            run["invalid"].append("experiment repeats have different effective execution configs")
            run["stale"] = True
    return out


def _pool(runs, eff_metric):
    """Average several runs of one experiment (repeats tighten the estimate)."""
    acc, units = {}, {}
    for r in runs:
        for it in r["items"]:
            key = it["id"]
            unit = it.get("unit") or key  # legacy scorers: each item is its own unit
            if key in units and units[key] != unit:
                raise ValueError(f"conflicting units for item {key!r}: {units[key]!r} != {unit!r}")
            units[key] = unit
            acc.setdefault(key, []).append(it.get("score", 0.0))
    items = [{"id": k, "unit": units[k], "score": sum(v) / len(v)} for k, v in acc.items()]
    metric_names = {k for r in runs for k in (r["summary"].get("metrics") or {})}
    return {
        "primary": _complete_mean([r["summary"].get("primary") for r in runs]),
        "metrics": {k: _complete_mean([(r["summary"].get("metrics") or {}).get(k) for r in runs])
                    for k in metric_names},
        "eff": _complete_mean([_get(r["summary"], eff_metric) for r in runs], nonnegative=True),
        "harness": max(r["summary"].get("harness_error_rate", 0) for r in runs),
        "gate_failures": sum(r["summary"].get("gate_failures", 0) for r in runs),
        "items": items, "n_runs": len(runs), "runs": [r["run_id"] for r in runs],
        "ai_free": all(r["summary"].get("ai_free") for r in runs),
    }


def current_value(ws, decision, split="dev", eff_metric=None):
    """Re-pool a decided experiment's runs under the current eval (decide rows keep decision-time
    numbers; after a rescore those are stale). Returns (primary, eff, stale)."""
    if not decision:
        return None, None, True
    runs = _exp_runs(ws, decision["exp"], decision.get("split", split))
    if not runs or any(r["stale"] for r in runs):
        return decision.get("primary"), decision.get("eff"), True
    p = _pool(runs, eff_metric or decision.get("eff_metric") or "cost_usd_per_unit")
    if p["primary"] is None:
        return decision.get("primary"), decision.get("eff"), True
    return p["primary"], p["eff"], False


def _guard_violations(guards, cand, best):
    out = []
    for g in guards or []:
        m = g["metric"]
        v = cand["metrics"].get(m)
        if v is None:
            out.append(f"{m}: missing")
            continue
        bv = best["metrics"].get(m) if best else None
        if "max" in g and v > g["max"]:
            out.append(f"{m}={v:.4f} > max {g['max']}")
        if "min" in g and v < g["min"]:
            out.append(f"{m}={v:.4f} < min {g['min']}")
        if "max_increase" in g and bv is not None and v - bv > g["max_increase"]:
            out.append(f"{m} rose {v - bv:+.4f} (> {g['max_increase']})")
        if "max_decrease" in g and bv is not None and bv - v > g["max_decrease"]:
            out.append(f"{m} fell {v - bv:+.4f} (> {g['max_decrease']})")
    return out


def cmd_propose(a):
    ws = Path(a.ws)
    st = _state(ws)
    ch = load_charter(ws)
    limits = _limits(ws, ch)
    cap = limits["limits"].get("max_experiments")
    if cap is not None and limits["n_exp"] >= cap:
        sys.exit(f"REFUSED: max_experiments {cap} already reserved; finish the registered experiments and finalize")
    if not (ws / "candidates" / a.candidate / "pipeline.py").exists():
        sys.exit(f"candidate {a.candidate} not found under candidates/")
    group = a.lever.split(".")[0]
    if a.lever != "baseline" and group not in LEVER_GROUPS:
        sys.exit(f"unknown lever group '{group}'; use one of {LEVER_GROUPS} (group.detail)")
    warnings = []
    for p in st["props"].values():
        d = st["decisions"].get(p["exp"], {}).get("decision")
        if d in KEEPS or d is None:
            continue
        sim = text_similarity(a.hypothesis, p.get("hypothesis", ""))
        if sim >= 0.55 or (p.get("lever") == a.lever and a.family and p.get("family") == a.family):
            warnings.append(f"close to {p['exp']} ({d}, lever {p.get('lever')}, sim {sim:.2f}): {p.get('hypothesis', '')[:90]}")
    if warnings and not a.force_similar:
        print("NOVELTY CHECK FAILED - this repeats a direction that already failed:")
        for w in warnings:
            print("  - " + w)
        print("State what new evidence makes it worth retrying with --force-similar \"...\", or pick a different lever.")
        sys.exit(2)
    exp = _next_exp_id(st["props"])
    row = {"event": "propose", "exp": exp, "at": now_iso(), "candidate": a.candidate, "parent": a.parent,
           "lever": a.lever, "family": a.family, "hypothesis": a.hypothesis, "expect": a.expect,
           "falsifier": a.falsifier, "phase": ch.get("phase", "quality"), "force_similar": a.force_similar,
           "similar_warnings": warnings}
    append_jsonl(ws / "ledger.jsonl", row)
    print(f"{exp} proposed ({a.lever}) -> run: run_experiment.py {a.ws} --exp {exp} --split dev")


def decide(ws, exp, split="dev", override=None, reason=None):
    ws = Path(ws)
    st = _state(ws)
    ch = load_charter(ws)
    prop = st["props"].get(exp)
    if not prop:
        sys.exit(f"unknown experiment {exp}")
    loop = ch.get("loop", {})
    eff_metric = ch.get("efficiency_metric", "cost_usd_per_unit")
    direction = 1 if ch.get("primary", {}).get("direction", "max") == "max" else -1
    runs = _exp_runs(ws, exp, split)
    if not runs:
        sys.exit(f"no complete runs for {exp} on {split}")
    invalid = [f"{r['run_id']}: {'; '.join(r['invalid'])}" for r in runs if r.get("invalid")]
    if invalid:
        sys.exit("run attribution invalid - restore the measured candidate or void and rerun: " + " | ".join(invalid))
    if any(r["stale"] for r in runs):
        sys.exit("some runs were scored with an older eval - rescore them (run_experiment.py --rescore)")
    cand = _pool(runs, eff_metric)
    if cand["primary"] is None:
        sys.exit("primary is missing or nonfinite in one or more runs; repair the measurements first")
    best = st["best"]
    out = {"event": "decide", "exp": exp, "at": now_iso(), "split": split, "phase": ch.get("phase", "quality"),
           "candidate": prop["candidate"], "lever": prop.get("lever"), "runs": cand["runs"],
           "primary": cand["primary"], "metrics": cand["metrics"], "eff_metric": eff_metric, "eff": cand["eff"],
           "ai_free": cand["ai_free"]}
    best_pool = None
    if best:
        best_runs = _exp_runs(ws, best["exp"], split)
        if any(r.get("invalid") for r in best_runs):
            sys.exit(f"best {best['exp']} has invalid run attribution; restore the measured candidate or void and rerun")
        if not best_runs or any(r["stale"] for r in best_runs):
            sys.exit(f"best {best['exp']} has no current-eval runs on {split}; rescore it first")
        best_pool = _pool(best_runs, eff_metric)
        if best_pool["primary"] is None:
            sys.exit(f"best {best['exp']} has missing or nonfinite primary measurements; repair them first")
    n_items = len(cand["items"]) or 1
    noise = ch.get("noise") or {}
    min_delta = loop.get("min_delta") or noise.get("min_delta") or 1.0 / n_items
    min_eff = loop.get("min_efficiency_gain", 0.10)
    if prop.get("lever") == "baseline" and best is None:
        decision, why = "baseline", "first measured point"
    elif best_pool is None:
        sys.exit("no baseline yet - propose and decide a baseline (lever=baseline) first")
    else:
        pi = paired_items(best_pool["items"], cand["items"])
        delta = direction * (cand["primary"] - best_pool["primary"])
        eff_ratio = _efficiency_ratio(cand["eff"], best_pool["eff"])
        out.update(delta=delta, min_delta=min_delta,
                   paired={k: pi[k] for k in ("n", "units", "improved", "regressed", "units_improved",
                                              "units_regressed", "band", "ci", "p")},
                   eff_ratio=eff_ratio, best_exp=best["exp"])
        guards = _guard_violations(ch.get("guards"), cand, best_pool)
        harness_cap = max(0.05, 2 * best_pool["harness"])
        marginal = (delta < 2 * min_delta) and cand["n_runs"] < 2
        if cand["harness"] > harness_cap:
            decision, why = "void", f"harness error rate {cand['harness']:.2%} > {harness_cap:.2%}: fix infra and rerun"
        elif cand["gate_failures"]:
            decision, why = "invalid", f"{cand['gate_failures']} hard-gate failures (schema/coverage)"
        elif guards:
            decision, why = "discard", "guard violated: " + "; ".join(guards)
        elif out["phase"] == "efficiency":
            floor = ch.get("quality_floor")
            q_ok = floor is None or direction * (cand["primary"] - floor) >= 0
            gain = (1 - eff_ratio) if eff_ratio is not None else None
            if not q_ok:
                decision, why = "discard", f"primary {cand['primary']:.4f} below floor {floor}"
            elif gain is not None and gain >= min_eff:
                near_floor = floor is not None and direction * (cand["primary"] - floor) < min_delta / 2
                decision = "rerun" if (near_floor and cand["n_runs"] < 2) else "keep"
                why = f"{eff_metric} -{gain:.0%} with primary held" + (" (near floor: confirm with a repeat)" if decision == "rerun" else "")
            elif delta >= min_delta and gain is not None and gain > -0.05 and pi["improved"] > pi["regressed"]:
                decision = "rerun" if marginal else "keep"
                why = f"primary +{delta:.4f} at equal cost"
            else:
                decision, why = "discard", f"no efficiency gain >= {min_eff:.0%} (ratio {eff_ratio})"
        else:
            if delta >= min_delta and pi["improved"] > pi["regressed"]:
                decision = "rerun" if marginal else "keep"
                why = f"primary {delta:+.4f} >= min_delta {min_delta:.4f}; items +{pi['improved']}/-{pi['regressed']}"
                if decision == "rerun":
                    why += " (marginal: repeat once to confirm)"
            elif abs(delta) < min_delta and eff_ratio is not None and eff_ratio <= 1 - min_eff \
                    and pi["regressed"] <= pi["improved"] + max(1, round(0.02 * pi["n"])):
                decision, why = "keep", f"quality tie, {eff_metric} x{eff_ratio:.2f} (simpler/cheaper wins ties)"
            else:
                decision, why = "discard", f"primary {delta:+.4f} vs min_delta {min_delta:.4f}; items +{pi['improved']}/-{pi['regressed']}"
    if out.get("paired") and out["paired"].get("ci") and out["paired"]["ci"][0] is not None:
        lo, hi = out["paired"]["ci"]
        why += f"; unit-bootstrap 95% CI [{direction * lo:+.4f}, {direction * hi:+.4f}]"
    if override:
        why = f"OVERRIDE {decision}->{override}: {reason or 'no reason given'} | {why}"
        decision = override
    out.update(decision=decision, reason=why)
    append_jsonl(ws / "ledger.jsonl", out)
    print(f"{exp} {decision.upper()}: {why}")
    if decision in KEEPS:
        print(f"  new best: {prop['candidate']} primary={cand['primary']:.4f} {eff_metric}={cand['eff']}")
    print_status(ws, compact=True)


def _epoch(row):
    if row.get("epoch") is not None:
        return float(row["epoch"])
    at = str(row.get("at", ""))
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ"):
        try:
            return datetime.strptime(at, fmt).timestamp()
        except ValueError:
            continue
    return time.time()


def active_hours(ws, ch):
    """Hours since the workspace was created, minus time paused (waiting for the user, quota outages)."""
    return policy_active_hours(ws, ch)


def _limits(ws, ch):
    return limit_state(ws, ch)


def _gate(ws):
    gate = read_json(Path(ws) / "audit" / "quality_gate.json")
    if not gate:
        return "AUDIT", "no quality gate yet: profile, checks.py, close read, data_audit.md, quality_gate.py"
    if gate["status"] == "INCOMPLETE":
        return "AUDIT", "audit incomplete: " + "; ".join(f["title"] for f in gate["findings"] if f["severity"] == "procedure")
    if gate["status"] == "STOP" or (gate["status"] == "REVIEW" and not gate.get("waiver")):
        return "WAIT_USER", f"quality gate {gate['status']}: report findings to the user and wait (clock paused)"
    allowed, reason = task_review_allows_runs(ws)
    if not allowed:
        return "TASK_REVIEW", reason
    return None


def recommend(ws):
    ws = Path(ws)
    st = _state(ws)
    ch = load_charter(ws)
    loop = ch.get("loop", {})
    patience = loop.get("patience", 5)
    phase = ch.get("phase", "quality")
    lim = _limits(ws, ch)
    order = sorted(st["props"], key=lambda e: int(e.lstrip("E")) if e.lstrip("E").isdigit() else 0)
    decided = [e for e in order if e in st["decisions"]]
    pending = [e for e in order if e not in st["decisions"]]
    last_keep_idx = max((i for i, e in enumerate(decided) if st["decisions"][e]["decision"] in KEEPS), default=-1)
    since = [e for e in decided[last_keep_idx + 1:] if st["decisions"][e].get("phase", "quality") == phase
             and st["decisions"][e]["decision"] not in ("void", "rerun")]
    stall = len(since)
    groups_since = [st["props"][e].get("lever", "?").split(".")[0] for e in since]
    last_keep_at = st["decisions"][decided[last_keep_idx]]["at"] if last_keep_idx >= 0 else ""
    fresh = any(m.get("kind") in ("fresh-eyes", "categorize") and m["at"] >= last_keep_at for m in st["marks"])
    recent_groups = [st["props"][e].get("lever", "?").split(".")[0] for e in order[-3:]]
    tried = {}
    for e in decided:
        g = st["props"][e].get("lever", "?").split(".")[0]
        t = tried.setdefault(g, [0, 0])
        t[1] += 1
        t[0] += st["decisions"][e]["decision"] == "keep"
    untried = [g for g in LEVER_GROUPS if g not in tried and g != "harness"]
    targets = ch.get("targets", {})
    best = st["best"]
    rec = ("CONTINUE", "exploit around the best; one hypothesis per experiment")
    gate_rec = _gate(ws)
    cap = lim["limits"].get("max_experiments")

    def reserved_slot_allowed(exp):
        return cap is None or any(slot_exp == exp and i <= cap
                                  for i, (slot_exp, _) in enumerate(lim["slots"], 1))

    runnable_pending = [exp for exp in pending if reserved_slot_allowed(exp)]
    rerun = decided[-1] if decided and st["decisions"][decided[-1]]["decision"] == "rerun" else None
    if st["stops"]:
        rec = ("STOPPED", st["stops"][-1].get("reason", ""))
    elif gate_rec:
        rec = gate_rec
    elif lim["hard_hit"]:
        rec = ("STOP", "limit reached: " + "; ".join(lim["hard_hit"]) + " -> report the measured results")
    elif lim["paused"]:
        rec = ("PAUSED", "clock paused: resume it before launching work")
    elif runnable_pending:
        p = runnable_pending[0]
        has_runs = any(m.get("exp") == p for m in list_runs(ws))
        rec = ("DECIDE" if has_runs else "RUN", f"{p}: " + ("ledger.py decide" if has_runs else "run_experiment.py --exp " + p))
    elif rerun and reserved_slot_allowed(rerun):
        rec = ("RERUN", f"{rerun} is marginal: run_experiment.py --exp {rerun} --tag r2, then decide again")
    elif lim["hit"]:
        rec = ("STOP", "limit reached: " + "; ".join(lim["hit"]) + " -> finalize (holdout, full run, report)")
    elif best is None:
        rec = ("BASELINE", "propose lever=baseline, run it twice (--tag r2), validate_eval.py, decide")
    else:
        q_target = targets.get("quality")
        reached = q_target is not None and best.get("primary") is not None and best["primary"] >= q_target
        e_target = targets.get("efficiency")
        e_reached = e_target is not None and best.get("eff") is not None and best["eff"] <= e_target
        plateau = stall >= patience and len(set(groups_since)) >= 3 and fresh
        if phase == "quality" and (reached or plateau):
            rec = ("SWITCH_PHASE", ("quality target reached" if reached else f"quality plateau ({stall} non-keeps, "
                   f"{len(set(groups_since))} lever groups, fresh-eyes done)") + " -> ledger.py phase WS efficiency")
        elif phase == "efficiency" and (e_reached or plateau):
            rec = ("FINALIZE", "efficiency target reached" if e_reached else "efficiency plateau -> holdout + full run + report")
        elif stall >= 3 and not fresh:
            rec = ("FRESH_EYES", "categorize remaining failures, run a headroom probe, and get proposals from a "
                   "fresh subagent that has not seen the ledger; then ledger.py mark WS fresh-eyes \"...\"")
        elif stall >= 2 and len(set(recent_groups)) == 1:
            rec = ("DIVERSIFY", f"last 3 used '{recent_groups[0]}'; next must use another group, untried first: "
                   f"{untried or sorted(tried, key=lambda g: tried[g][1])}")
        elif stall >= patience and len(set(groups_since)) < 3:
            rec = ("DIVERSIFY", f"{stall} non-keeps within {sorted(set(groups_since))}; try {untried or 'a different group'}")
        elif len(order) >= 3 and len(set(recent_groups)) == 1 and stall == 0:
            rec = ("EXPLORE", f"3 in a row on '{recent_groups[0]}'; spend the next one on an untried group: {untried}")
    return {"rec": rec, "stall": stall, "groups_since": groups_since, "tried": tried, "untried": untried,
            "limits": lim, "phase": phase, "best": best, "pending": pending, "st": st, "order": order}


def print_status(ws, compact=False, write=False):
    ws = Path(ws)
    ch = load_charter(ws)
    r = recommend(ws)
    st, best, lim = r["st"], r["best"], r["limits"]
    lines = []
    counts = {}
    for d in st["decisions"].values():
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    lines.append(f"PHASE {r['phase']} | experiments {len(st['props'])} {counts}")
    eff_metric = ch.get("efficiency_metric", "cost_usd_per_unit")
    if best:
        cur_p, cur_e, stale = current_value(ws, best, eff_metric=eff_metric)
        lines.append(f"BEST {best['exp']} {best['candidate']} primary={cur_p:.4f} {eff_metric}={cur_e} "
                     f"lever={best.get('lever')}" + ("  (decision-time numbers: rescore its runs)" if stale else ""))
    noise = ch.get("noise") or {}
    if noise:
        lines.append(f"NOISE min_delta={noise.get('min_delta'):.4f} band={noise.get('band')} n_items={noise.get('n_items')} "
                     f"headroom={noise.get('headroom')}")
    if ch.get("quality_floor") is not None:
        lines.append(f"FLOOR primary >= {ch['quality_floor']}")
        switch = [r for r in st["rows"] if r.get("event") == "phase" and r.get("to") == "efficiency"]
        if switch and best:
            at_switch = [d for d in st["decisions"].values() if d.get("decision") in KEEPS and d["at"] <= switch[-1]["at"]]
            if at_switch:
                top = current_value(ws, at_switch[-1], eff_metric=eff_metric)[0]
                now = current_value(ws, best, eff_metric=eff_metric)[0]
                drop = top - now
                md = (ch.get("noise") or {}).get("min_delta") or 0.0
                lines.append(f"DRIFT since phase switch: {top:.4f} -> {now:.4f} ({-drop:+.4f})"
                             + ("  <- cumulative drop exceeds min_delta: check holdout before cutting further"
                                if drop > md else ""))
    lines.append(f"STALL {r['stall']} non-keeps since last keep; groups since: {r['groups_since']}")
    lines.append("LEVERS kept/tried: " + ", ".join(f"{g} {k}/{t}" for g, (k, t) in sorted(r["tried"].items()))
                 + f" | untried: {r['untried']}")
    L = lim["limits"]
    ai_free = [d for d in st["decisions"].values() if d.get("ai_free") and d.get("primary") is not None]
    if ai_free:
        bf = max(ai_free, key=lambda d: d["primary"])
        md = (ch.get("noise") or {}).get("min_delta") or 0.0
        gap = (best["primary"] - bf["primary"]) if best else None
        note = " <- within noise of the best: report that the model may be unnecessary" if (
            gap is not None and gap <= md) else ""
        lines.append(f"NO-LLM best {bf['exp']} {bf['candidate']} primary={bf['primary']:.4f} (gap {gap}){note}")
    cost_note = " (known subtotal; some costs unreported)" if not lim["cost_complete"] else ""
    lines.append(f"LIMITS cost ${lim['spent']:.3f}/{L.get('max_cost_usd')}{cost_note} hours {lim['hours']:.1f}/{L.get('max_hours')}"
                 f"{' (clock paused)' if lim['paused'] else ''} "
                 f"exps {lim['n_exp']}/{L.get('max_experiments')} holdout {lim['holdout_evals']}/"
                 f"{ch.get('loop', {}).get('max_holdout_evals', 3)}")
    lines.append(f"RECOMMEND {r['rec'][0]}: {r['rec'][1]}")
    if not compact:
        keeps = [d for d in st["decisions"].values() if d["decision"] in KEEPS]
        if keeps:
            lines.append("BRANCH POINTS (kept): " + " | ".join(
                f"{d['exp']} {d['candidate']} {d['primary']:.3f} eff={d.get('eff') if d.get('eff') is None else round(d['eff'], 6)}"
                for d in keeps[-4:]))
        lines.append("RECENT:")
        for e in r["order"][-6:]:
            p = st["props"][e]
            d = st["decisions"].get(e, {})
            delta = d.get("delta")
            lines.append(f"  {e} {d.get('decision', 'pending'):8} {p.get('lever', ''):22} "
                         f"{'' if delta is None else f'{delta:+.4f}'} {p.get('hypothesis', '')[:70]}")
    text = "\n".join(lines)
    print(text)
    if write:
        card = ("# STATE (resume card)\n\nRegenerated by `ledger.py status --write`. To resume: read this card, "
                "the last ~40 lines of journal.md, then act on RECOMMEND.\n\n```\n" + text + "\n```\n")
        (ws / "STATE.md").write_text(card, encoding="utf-8")


def cmd_history(ws):
    st = _state(ws)
    print("| exp | lever | candidate | hypothesis | primary | delta | eff | decision | reason |")
    print("|---|---|---|---|---|---|---|---|---|")
    for e in sorted(st["props"], key=lambda x: int(x.lstrip("E")) if x.lstrip("E").isdigit() else 0):
        p, d = st["props"][e], st["decisions"].get(e, {})
        prim = d.get("primary")
        delta = d.get("delta")
        eff = d.get("eff")
        effs = '' if eff is None else f"{eff:.6g} ({d.get('eff_metric')})"
        print(f"| {e} | {p.get('lever')} | {p.get('candidate')} | {p.get('hypothesis', '')[:80]} | "
              f"{'' if prim is None else f'{prim:.4f}'} | {'' if delta is None else f'{delta:+.4f}'} | "
              f"{effs} | {d.get('decision', 'pending')} | {str(d.get('reason', ''))[:80]} |")
    for r in st["rows"]:
        if r.get("event") in ("phase", "stop", "mark", "clock"):
            print(f"<!-- {r['event']} {r.get('at')} {r.get('to') or r.get('kind') or r.get('action') or ''}: "
                  f"{r.get('reason') or r.get('text') or ''} -->")


def cmd_compare(a):
    def items(x):
        p = Path(x)
        return read_json(p / "score.json", {}).get("items", [])
    pi = paired_items(items(a.run_a), items(a.run_b))
    print(json.dumps({k: pi[k] for k in ("n", "improved", "regressed", "delta_mean", "band", "p")}))
    print("improved:", pi["improved_ids"][:a.top])
    print("regressed:", pi["regressed_ids"][:a.top])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("ws")
    p.add_argument("--candidate", required=True)
    p.add_argument("--parent")
    p.add_argument("--lever", required=True)
    p.add_argument("--family", default="", help="short slug for the idea family, e.g. 'unit-normalization'")
    p.add_argument("--hypothesis", required=True)
    p.add_argument("--expect", default="", help="predicted effect, e.g. '+0.02..0.05 primary'")
    p.add_argument("--falsifier", default="", help="what result would show the hypothesis is wrong")
    p.add_argument("--force-similar", default="", help="why retrying a failed direction is justified")
    d = sub.add_parser("decide")
    d.add_argument("ws")
    d.add_argument("exp")
    d.add_argument("--split", default="dev")
    d.add_argument("--override", choices=["keep", "discard"])
    d.add_argument("--reason")
    s = sub.add_parser("status")
    s.add_argument("ws")
    s.add_argument("--write", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("ws")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.add_argument("--top", type=int, default=15)
    m = sub.add_parser("mark")
    m.add_argument("ws")
    m.add_argument("kind", choices=["fresh-eyes", "categorize", "headroom", "note"])
    m.add_argument("text")
    ph = sub.add_parser("phase")
    ph.add_argument("ws")
    ph.add_argument("to", choices=["quality", "efficiency"])
    ph.add_argument("--floor", type=float)
    ph.add_argument("--reason", default="")
    cl = sub.add_parser("clock")
    cl.add_argument("ws")
    cl.add_argument("action", choices=["pause", "resume"])
    cl.add_argument("--reason", default="")
    cl.add_argument("--at", help="backdate, ISO time with zone (e.g. 2026-10-18T10:52:00+0000) for outages noticed later")
    cl.add_argument("--gap", action="store_true",
                    help="with resume: treat the time since the last file change in WS as an outage (session died)")
    hi = sub.add_parser("history")
    hi.add_argument("ws")
    vr = sub.add_parser("void")
    vr.add_argument("ws")
    vr.add_argument("run", help="run id (directory name under runs/)")
    vr.add_argument("--reason", required=True)
    sp = sub.add_parser("stop")
    sp.add_argument("ws")
    sp.add_argument("--reason", required=True)
    a = ap.parse_args()
    if a.cmd == "propose":
        cmd_propose(a)
    elif a.cmd == "decide":
        decide(a.ws, a.exp, a.split, a.override, a.reason)
    elif a.cmd == "status":
        print_status(a.ws, write=a.write)
    elif a.cmd == "compare":
        cmd_compare(a)
    elif a.cmd == "mark":
        append_jsonl(Path(a.ws) / "ledger.jsonl", {"event": "mark", "kind": a.kind, "text": a.text, "at": now_iso()})
        print(f"marked {a.kind}")
    elif a.cmd == "phase":
        ws = Path(a.ws)
        ch = load_charter(ws)
        st = _state(ws)
        floor = a.floor
        if floor is None and a.to == "efficiency" and st["best"]:
            md = (ch.get("loop", {}).get("min_delta") or (ch.get("noise") or {}).get("min_delta") or 0.0)
            cur, _, stale = current_value(ws, st["best"], eff_metric=ch.get("efficiency_metric"))
            if stale:
                sys.exit("the best experiment's runs are stale (eval changed): rescore them before setting a floor")
            floor = cur - md
        append_jsonl(ws / "ledger.jsonl", {"event": "phase", "from": ch.get("phase", "quality"), "to": a.to,
                                           "floor": floor, "reason": a.reason, "at": now_iso()})
        ch["phase"] = a.to
        ch["quality_floor"] = floor
        write_json(ws / "charter.json", ch)
        print(f"phase -> {a.to}; quality floor = {floor}")
    elif a.cmd == "clock":
        rows = [r for r in ledger_rows(a.ws) if r.get("event") == "clock"]
        state = rows[-1]["action"] if rows else "resume"
        if a.action == "resume" and a.gap and state != "pause":
            last = max((f.stat().st_mtime for f in Path(a.ws).rglob("*")
                        if f.is_file() and f.name != "STATE.md"), default=time.time())
            if time.time() - last > 600:
                append_jsonl(Path(a.ws) / "ledger.jsonl", {
                    "event": "clock", "action": "pause", "reason": "outage: no activity (session interrupted)",
                    "at": datetime.fromtimestamp(last).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"), "epoch": last})
                state = "pause"
                print(f"recorded outage since {datetime.fromtimestamp(last).astimezone().isoformat(timespec='minutes')}")
        if state == a.action:
            print(f"clock already {a.action}d")
        else:
            row = {"event": "clock", "action": a.action, "reason": a.reason, "at": a.at or now_iso()}
            row["epoch"] = _epoch(row) if a.at else time.time()
            append_jsonl(Path(a.ws) / "ledger.jsonl", row)
            print(f"clock {a.action}d")
    elif a.cmd == "void":
        append_jsonl(Path(a.ws) / "ledger.jsonl", {"event": "void_run", "run": a.run, "reason": a.reason, "at": now_iso()})
        print(f"run {a.run} voided: excluded from decisions")
    elif a.cmd == "history":
        cmd_history(a.ws)
    elif a.cmd == "stop":
        append_jsonl(Path(a.ws) / "ledger.jsonl", {"event": "stop", "reason": a.reason, "at": now_iso()})
        print("stopped")


if __name__ == "__main__":
    main()
