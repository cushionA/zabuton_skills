"""Shared execution limits, independent of the ledger's decision machinery.

USD caps stop new work at the observed cost. Calls already in flight may finish
above the cap; a backend or historical run without priced usage fails closed.
"""
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from common import read_json, read_jsonl
from workspace import charter as load_charter, effective_config_fingerprint, ledger_rows, list_runs


class RunPolicyError(RuntimeError):
    pass


def _epoch(row, now):
    if row.get("epoch") is not None:
        return float(row["epoch"])
    try:
        value = datetime.fromisoformat(str(row.get("at", "")).replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    except ValueError:
        return now


def active_hours(ws, ch, now=None):
    now = time.time() if now is None else now
    created = ch.get("created_epoch") or now
    paused, since = 0.0, None
    rows = [r for r in ledger_rows(ws) if r.get("event") == "clock"]
    for row in sorted(rows, key=lambda r: _epoch(r, now)):
        when = min(now, max(created, _epoch(row, now)))
        if row.get("action") == "pause" and since is None:
            since = when
        elif row.get("action") == "resume" and since is not None:
            paused += max(0.0, when - since)
            since = None
    if since is not None:
        paused += now - since
    return max(0.0, now - created - paused) / 3600, since is not None


def _nonnegative_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def observed_cost(runs, current_run=None):
    spent, unknown = 0.0, []
    for run in runs:
        summary = run.get("_summary", {})
        calls = read_jsonl(Path(run["_dir"]) / "calls.jsonl")
        if calls:
            costs = [row.get("cost_usd") for row in calls]
            if not all(_nonnegative_number(cost) for cost in costs):
                unknown.append(run["run_id"])
            spent += sum(cost for cost in costs if _nonnegative_number(cost))
        elif _nonnegative_number(summary.get("cost_usd")):
            spent += summary["cost_usd"]
            if summary.get("cost_complete") is False and not (summary.get("ai_free") is True or summary.get("calls") == 0):
                unknown.append(run["run_id"])
        elif summary and not (summary.get("ai_free") is True or summary.get("calls") == 0):
            unknown.append(run["run_id"])
        elif not summary and run.get("run_id") != current_run:
            # An abandoned or another process's run can have a dispatched call
            # whose usage was never persisted. Only this runner's fresh meta is
            # known to have no such call yet (pending attempts are added below).
            unknown.append(run["run_id"])
    return spent, unknown


def _experiment_slots(rows, runs):
    slots, candidates = [], set()
    for row in rows:
        if row.get("event") == "propose" and row.get("exp") not in [s[0] for s in slots]:
            slots.append((row["exp"], row.get("candidate")))
            candidates.add(row.get("candidate"))
    for run in runs:
        if run.get("purpose") == "reference" or run.get("candidate") in candidates:
            continue
        candidates.add(run.get("candidate"))
        slots.append((None, run.get("candidate")))
    return slots


def limit_state(ws, ch=None, now=None, extra_cost=0.0, unknown_cost=False, current_run=None):
    """State for status displays; execution also checks its own experiment slot."""
    ch = load_charter(ws) if ch is None else ch
    now = time.time() if now is None else now
    rows, runs = ledger_rows(ws), list_runs(ws)
    lim = ch.get("limits", {})
    spent, unknown = observed_cost(runs, current_run=current_run)
    spent += extra_cost
    if unknown_cost:
        unknown.append("in-flight attempt")
    hours, paused = active_hours(ws, ch, now)
    hard_hit = []
    remaining = []
    if lim.get("max_cost_usd") is not None:
        if unknown:
            hard_hit.append("USD cost unreported for run(s): " + ", ".join(unknown))
        if spent >= lim["max_cost_usd"]:
            hard_hit.append(f"cost ${spent:.2f} >= ${lim['max_cost_usd']}")
    if lim.get("max_hours") is not None and hours >= lim["max_hours"]:
        hard_hit.append(f"hours {hours:.1f} >= {lim['max_hours']}")
    if lim.get("max_hours") is not None:
        remaining.append(max(0.0, (lim["max_hours"] - hours) * 3600))
    if lim.get("deadline"):
        try:
            deadline = datetime.fromisoformat(str(lim["deadline"]).replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if now >= deadline.timestamp():
                hard_hit.append(f"deadline {lim['deadline']} passed")
            remaining.append(max(0.0, deadline.timestamp() - now))
        except ValueError:
            hard_hit.append(f"invalid deadline {lim['deadline']!r}")
    slots = _experiment_slots(rows, runs)
    hit = list(hard_hit)
    if lim.get("max_experiments") is not None and len(slots) >= lim["max_experiments"]:
        hit.append(f"experiments {len(slots)} >= {lim['max_experiments']}")
    holdout_ids = set(read_json(Path(ws) / "eval" / "splits.json", {}).get("holdout", []))
    return {"spent": spent, "cost_complete": not unknown, "unknown_cost_runs": unknown,
            "hours": hours, "paused": paused, "n_exp": len(slots),
            "holdout_evals": len({(m.get("candidate"), m.get("candidate_hash"), effective_config_fingerprint(m.get("config"))) for m in runs
                                  if (m.get("holdout_bearing") or m.get("split") == "holdout"
                                      or bool(set(m.get("unit_ids", [])) & holdout_ids))
                                  and m.get("purpose") != "reference"}),
            "hit": hit, "hard_hit": hard_hit, "limits": lim, "slots": slots,
            "remaining_s": min(remaining) if remaining else None}


def require_run_allowed(ws, candidate, purpose, exp=None, extra_cost=0.0, unknown_cost=False, current_run=None):
    rows = ledger_rows(ws)
    stops = [row for row in rows if row.get("event") == "stop"]
    if stops:
        raise RunPolicyError("ledger stopped: " + str(stops[-1].get("reason", "explicit stop")))
    state = limit_state(ws, extra_cost=extra_cost, unknown_cost=unknown_cost, current_run=current_run)
    if state["paused"]:
        raise RunPolicyError("clock paused; resume the ledger clock before launching work")
    if state["hard_hit"]:
        raise RunPolicyError("limit reached: " + "; ".join(state["hard_hit"]))
    cap = state["limits"].get("max_experiments")
    if cap is None or purpose == "reference":
        return state
    # Proposals reserve slots. Their allowed repeats remain executable after the
    # final slot was proposed. Finalization may reuse an already measured slot,
    # but never bypass an explicit stop, a paused clock, money or time limits.
    indices = [i + 1 for i, (slot_exp, slot_candidate) in enumerate(state["slots"])
               if (slot_exp == exp if exp is not None else slot_candidate == candidate)]
    slot = min(indices) if indices else len(state["slots"]) + 1
    if slot > cap:
        raise RunPolicyError(f"experiment slot {slot} exceeds max_experiments {cap}")
    return state


def bind_experiment(ws, candidate, purpose, exp):
    """Validate direct callers too; the CLI is not the trust boundary."""
    if exp is None:
        return
    props = [r for r in ledger_rows(ws) if r.get("event") == "propose" and r.get("exp") == exp]
    if not props:
        raise RunPolicyError(f"unknown experiment {exp}; record a proposal first")
    prop = props[-1]
    if prop.get("candidate") != candidate:
        raise RunPolicyError(f"experiment {exp} belongs to {prop.get('candidate')}, not {candidate}")
    expected = "baseline" if prop.get("lever") == "baseline" else "experiment"
    if purpose in ("baseline", "experiment", "confirm") and purpose != "confirm" and purpose != expected:
        raise RunPolicyError(f"experiment {exp} expects purpose {expected}, not {purpose}")


def holdout_membership(ws, dataset, units, split):
    declared = read_json(Path(ws) / "eval" / "splits.json", {}).get("holdout", [])
    holdout_ids = set(declared) | {u["id"] for u in dataset.load_units("holdout")}
    return split == "holdout" or bool(holdout_ids & {u["id"] for u in units}), holdout_ids


def require_holdout_allowed(ws, candidate, candidate_hash, purpose, holdout_bearing, holdout_ids, config=None):
    if not holdout_bearing:
        return
    if purpose not in {"holdout", "full", "reference"}:
        raise RunPolicyError(f"purpose {purpose} cannot evaluate held-out units; use holdout or full")
    if purpose == "reference":
        return  # reference preparation is separate from candidate evaluation
    cap = load_charter(ws).get("loop", {}).get("max_holdout_evals", 3)
    if cap <= 0:
        raise RunPolicyError(f"holdout evaluations disabled (cap {cap})")
    peeked = set()
    for run in list_runs(ws):
        bearing = (run.get("holdout_bearing") or run.get("split") == "holdout"
                   or bool(set(run.get("unit_ids", [])) & holdout_ids))
        if bearing and run.get("purpose") != "reference":
            peeked.add((run.get("candidate"), run.get("candidate_hash"), effective_config_fingerprint(run.get("config"))))
    if (candidate, candidate_hash, effective_config_fingerprint(config)) not in peeked and len(peeked) >= cap:
        raise RunPolicyError(f"holdout already evaluated {len(peeked)} candidates (cap {cap}); no new peeks")
