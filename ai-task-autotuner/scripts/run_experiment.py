"""Run one candidate pipeline over a split with the frozen evaluation and record everything.

  python run_experiment.py WS --candidate c003-slug --split dev --purpose baseline
  python run_experiment.py WS --exp E004 --split dev            # candidate from the ledger proposal
  python run_experiment.py WS --exp E004 --split dev --tag r2   # repeat run (noise / confirmation)
  python run_experiment.py WS --rescore WS/runs/<run_id>        # after an eval change

Purposes: smoke | baseline | reference | experiment | holdout | full | confirm.
experiment/holdout/full/confirm need a passing eval/validation.json for the current
eval hash; every LLM run needs a non-blocking audit/quality_gate.json with unchanged
input data. Holdout runs print aggregates only.

Writes WS/runs/<run_id>/{meta.json, outputs.jsonl, calls.jsonl, score.json, summary.json}
and prints a compact summary (keep the conversation small; open files for detail).
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_call  # noqa: E402
from common import append_jsonl, estimate_cost, now_iso, percentile, read_json, read_jsonl, write_json  # noqa: E402
from quality_gate import gate_allows_runs  # noqa: E402
from workspace import candidate_hash, charter as load_charter, eval_hash, ledger_rows, list_runs, load_eval, load_module  # noqa: E402

NEEDS_VALIDATION = {"experiment", "holdout", "full", "confirm"}
PURPOSES = {"smoke", "baseline", "reference"} | NEEDS_VALIDATION


class RunLog:
    def __init__(self, run_dir):
        self.lock = threading.Lock()
        self.calls_path = run_dir / "calls.jsonl"
        self.seq = 0
        # Circuit breaker: once the subscription/API quota is exhausted, every further call
        # would fail too - stop spending wall time and mark the rest as harness errors.
        self.quota_hit = threading.Event()

    def call(self, rec):
        with self.lock:
            self.seq += 1
            rec["seq"] = self.seq
            append_jsonl(self.calls_path, rec)


class Ctx:
    """Handed to pipeline.run(unit, ctx). All model calls must go through ctx.llm."""

    def __init__(self, cand_dir, config, log, sem, unit_id=None):
        self.cand_dir = Path(cand_dir)
        self.config = config
        self.params = config.get("params", {})
        self.unit_id = unit_id
        self._log = log
        self._sem = sem
        self._prompts = {}
        self.state = {}   # filled by pipeline.setup(ctx); shared read-only by all units
        self.notes = {}
        self.counts = {}
        self.n_calls = 0
        self.harness_failed = False

    def prompt(self, name):
        if name not in self._prompts:
            self._prompts[name] = (self.cand_dir / "prompts" / name).read_text(encoding="utf-8")
        return self._prompts[name]

    def llm(self, prompt, system=None, *, tag=None, unit_id=None, model=None, effort=None,
            max_tokens=None, json_schema=None, expect_json=False, backend=None):
        c = self.config
        if self._log.quota_hit.is_set():
            self.harness_failed = True
            return {"ok": False, "error_kind": "quota", "error": "skipped: quota exhausted earlier in this run",
                    "text": None, "json": None, "usage": None, "cost_usd": None}
        with self._sem:
            rec = llm_call.call(
                prompt, system, backend=backend or c.get("backend", "cli"), model=model or c.get("model", "haiku"),
                effort=effort if effort is not None else c.get("effort"),
                max_tokens=max_tokens or c.get("max_tokens"), json_schema=json_schema, expect_json=expect_json,
                timeout=c.get("timeout_s", 300), retries=c.get("retries", 2), thinking=c.get("thinking", "off"))
        if rec.get("error_kind") == "quota":
            self._log.quota_hit.set()
        self.n_calls += 1
        if llm_call.is_harness_failure(rec):
            self.harness_failed = True
        row = dict(rec)
        row.update(unit_id=unit_id or self.unit_id, tag=tag, prompt=prompt, system=system)
        self._log.call(row)
        return rec

    def map(self, fn, items, workers=None):
        """Parallel map inside one unit (bounded by the global call semaphore)."""
        items = list(items)
        with ThreadPoolExecutor(max_workers=workers or min(8, max(1, len(items)))) as ex:
            return list(ex.map(fn, items))

    def count(self, name, n=1):
        self.counts[name] = self.counts.get(name, 0) + n

    def note(self, key, value):
        self.notes[key] = value


def _claude_version():
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _validation_ok(ws):
    v = read_json(Path(ws) / "eval" / "validation.json")
    if not v:
        return False, "eval/validation.json missing - run validate_eval.py"
    if not v.get("passed"):
        return False, "eval validation did not pass - fix the eval first"
    if v.get("eval_hash") != eval_hash(ws):
        return False, "eval changed since validation - re-run validate_eval.py (and rescore old runs)"
    return True, ""


def _summarize(units, rows, calls, score, wall_s, concurrency):
    n = len(units)
    status = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    usage_keys = ("input", "output", "cache_read", "cache_write", "thinking")
    tok = {k: 0 for k in usage_keys}
    thinking_reported = False
    cost, cost_complete = 0.0, True
    for c in calls:
        u = c.get("usage")
        if u is None:
            cost_complete = False
            continue
        for k in usage_keys:
            if u.get(k) is not None:
                tok[k] += u[k]
                thinking_reported = thinking_reported or k == "thinking"
        if c.get("cost_usd") is None:
            cost_complete = False
        else:
            cost += c["cost_usd"]
    if not thinking_reported:
        tok["thinking"] = None
    # Backend-neutral comparison point: every input-side token at the base input price,
    # no caching. claude -p books most input as cache writes/reads, a bare API call may not.
    uncached = 0.0
    for c in calls:
        u = c.get("usage") or {}
        model = (c.get("model_served") or c.get("model_requested") or "").split(",")[0]
        est = estimate_cost(model, {"input": (u.get("input") or 0) + (u.get("cache_read") or 0)
                                    + (u.get("cache_write") or 0), "output": u.get("output") or 0})
        uncached = None if (est is None or uncached is None) else uncached + est
    in_side = tok["input"] + tok["cache_read"] + tok["cache_write"]
    unit_wall = [r.get("wall_ms") for r in rows if r.get("wall_ms") is not None]
    api_ms = [c.get("api_ms") for c in calls if c.get("api_ms") is not None]
    errors = {}
    for c in calls:
        if c.get("error_kind"):
            errors[c["error_kind"]] = errors.get(c["error_kind"], 0) + 1
    harness = status.get("harness_error", 0)
    return {
        "units": n, "status": status, "harness_error_rate": harness / n if n else 0.0,
        "primary": score.get("primary"), "metrics": score.get("metrics", {}),
        "gate_failures": len(score.get("gate_failures", [])), "items": len(score.get("items", [])),
        "calls": len(calls), "calls_per_unit": len(calls) / n if n else None,
        "tokens": tok, "tokens_per_unit": {k: (v / n if (v is not None and n) else None) for k, v in tok.items()},
        "cache_hit_ratio": (tok["cache_read"] / in_side) if in_side else None,
        "cost_usd": cost if calls else 0.0, "cost_complete": cost_complete,
        "cost_usd_per_unit": (cost / n) if n else None,
        "cost_uncached_list_usd": uncached if calls else 0.0,
        "api_ms": {"p50": percentile(api_ms, .5), "p90": percentile(api_ms, .9), "max": max(api_ms) if api_ms else None},
        "api_ms_per_unit": (sum(api_ms) / n) if (api_ms and n) else None,
        "unit_wall_ms": {"p50": percentile(unit_wall, .5), "p90": percentile(unit_wall, .9),
                         "max": max(unit_wall) if unit_wall else None, "sum": sum(unit_wall) if unit_wall else None},
        "run_wall_s": wall_s, "concurrency": concurrency,
        "retries": sum(max(0, (c.get("attempts") or 1) - 1) for c in calls),
        "call_errors": errors,
        "models_served": sorted({c.get("model_served") for c in calls if c.get("model_served")}),
        "ai_free": len(calls) == 0,
        "per_call": _per_call_dist(calls),
    }


def _per_call_dist(calls):
    """Distributions used to recommend production settings (max_tokens, timeout)."""
    outs = [(c.get("usage") or {}).get("output") for c in calls]
    think = [(c.get("usage") or {}).get("thinking") for c in calls]
    ins = [((c.get("usage") or {}).get("input") or 0) + ((c.get("usage") or {}).get("cache_read") or 0)
           + ((c.get("usage") or {}).get("cache_write") or 0) for c in calls if c.get("usage")]
    api = [c.get("api_ms") for c in calls]
    stops = {}
    for c in calls:
        stops[str(c.get("stop_reason"))] = stops.get(str(c.get("stop_reason")), 0) + 1

    def d(v):
        v = [x for x in v if x is not None]
        return {"p50": percentile(v, .5), "p95": percentile(v, .95), "p99": percentile(v, .99),
                "max": max(v) if v else None} if v else None
    return {"output_tokens": d(outs), "thinking_tokens": d(think), "input_side_tokens": d(ins),
            "api_ms": d(api), "stop_reasons": stops}


def _print_summary(run_id, s, holdout):
    p = s["primary"]
    print(f"RUN {run_id}")
    print(f"  primary={p:.4f}" if isinstance(p, (int, float)) else f"  primary={p}",
          "metrics=" + json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s["metrics"].items()},
                                  ensure_ascii=False)[:300])
    print(f"  units={s['units']} status={s['status']} gate_failures={s['gate_failures']}")
    if holdout:
        print("  (holdout: item-level details intentionally not printed)")
    t = s["tokens"]
    cost = f"${s['cost_usd']:.4f}" + ("" if s["cost_complete"] else " (incomplete)")
    print(f"  calls={s['calls']} in={t['input']} cache_r={t['cache_read']} cache_w={t['cache_write']} "
          f"out={t['output']} think={t['thinking']} cost={cost} per_unit=${(s['cost_usd_per_unit'] or 0):.5f}")
    print(f"  api_ms p50/p90/max={s['api_ms']['p50']}/{s['api_ms']['p90']}/{s['api_ms']['max']} "
          f"unit_wall_ms p50/p90/max={s['unit_wall_ms']['p50']}/{s['unit_wall_ms']['p90']}/{s['unit_wall_ms']['max']} "
          f"run_wall_s={s['run_wall_s']:.1f} conc={s['concurrency']} retries={s['retries']} errors={s['call_errors']}")


def score_run(ws, run_dir, units, outputs_by_id, split):
    _, score_mod = load_eval(ws)
    sc = score_mod.score(units, outputs_by_id, split)
    sc.setdefault("metrics", {})
    sc.setdefault("items", [])
    sc.setdefault("gate_failures", [])
    sc["eval_hash"] = eval_hash(ws)
    write_json(Path(run_dir) / "score.json", sc)
    return sc


def run(ws, candidate, split, purpose, exp=None, limit=None, concurrency=None, tag=None):
    ws = Path(ws)
    ch = load_charter(ws)
    ok, reason = gate_allows_runs(ws)
    if not ok:
        sys.exit(f"REFUSED: {reason}")
    if purpose in NEEDS_VALIDATION:
        ok, reason = _validation_ok(ws)
        if not ok:
            sys.exit(f"REFUSED: {reason}")
    cand_dir = ws / "candidates" / candidate
    if not (cand_dir / "pipeline.py").exists():
        sys.exit(f"REFUSED: {cand_dir}/pipeline.py not found")
    c_hash = candidate_hash(cand_dir)
    for prior in list_runs(ws):
        # smoke runs are for debugging a candidate; any other run freezes it
        if (prior.get("candidate") == candidate and prior.get("purpose") != "smoke"
                and prior.get("candidate_hash") != c_hash):
            sys.exit(f"REFUSED: {candidate} changed after run {prior['run_id']}; copy it to a new candidate "
                     "(candidates are immutable once measured so every result stays reproducible)")
    if purpose == "holdout":
        n_hold = sum(1 for r in list_runs(ws) if r.get("purpose") == "holdout")
        cap = ch.get("loop", {}).get("max_holdout_evals", 3)
        if n_hold >= cap:
            sys.exit(f"REFUSED: holdout already evaluated {n_hold} times (cap {cap}); more peeks turn it into a dev set")

    config = dict(ch.get("backend", {}))
    config.update(read_json(cand_dir / "config.json", {}))
    conc = concurrency or config.get("concurrency", 8)
    dataset, _ = load_eval(ws)
    units = dataset.load_units(split)
    if limit:
        units = units[:limit]
    pipeline = load_module(cand_dir / "pipeline.py", f"cand_{candidate.replace('-', '_')}")

    stamp = time.strftime("%Y%m%d-%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
    run_id = "__".join(x for x in (exp or candidate, split, purpose, tag, stamp) if x)
    run_dir = ws / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    log = RunLog(run_dir)
    sem = threading.BoundedSemaphore(config.get("max_parallel_calls", conc))
    meta = {"run_id": run_id, "candidate": candidate, "candidate_hash": c_hash, "exp": exp, "purpose": purpose,
            "split": split, "tag": tag, "limit": limit, "eval_hash": eval_hash(ws), "config": config,
            "unit_ids": [u["id"] for u in units], "started": now_iso(), "python": sys.version.split()[0],
            "platform": platform.platform(),
            "claude_version": _claude_version() if config.get("backend", "cli") == "cli" else None}
    write_json(run_dir / "meta.json", meta)

    shared = Ctx(cand_dir, config, log, sem)
    if hasattr(pipeline, "setup"):
        pipeline.setup(shared)
    rows = []
    t0 = time.time()
    if hasattr(pipeline, "run_all"):
        try:
            outs = pipeline.run_all(units, shared) or {}
            err = None
        except Exception as e:  # whole-batch failure
            outs, err = {}, f"{type(e).__name__}: {e}"
        for u in units:
            o = outs.get(u["id"])
            st = "ok" if o is not None else ("harness_error" if shared.harness_failed else "system_error")
            rows.append({"id": u["id"], "status": st, "error": err if o is None else None, "output": o,
                         "wall_ms": None, "n_calls": None, "notes": {}, "counts": {}})
    else:
        def work(u):
            if log.quota_hit.is_set():
                return {"id": u["id"], "status": "harness_error", "error": "skipped: quota exhausted",
                        "output": None, "wall_ms": None, "n_calls": 0, "notes": {}, "counts": {}}
            ctx = Ctx(cand_dir, config, log, sem, unit_id=u["id"])
            ctx.state = shared.state
            ts = time.time()
            try:
                out, st, err = pipeline.run(u, ctx), "ok", None
            except Exception as e:
                out, err = None, f"{type(e).__name__}: {str(e)[:300]}"
                st = "harness_error" if ctx.harness_failed else "system_error"
            return {"id": u["id"], "status": st, "error": err, "output": out,
                    "wall_ms": int((time.time() - ts) * 1000), "n_calls": ctx.n_calls,
                    "notes": ctx.notes, "counts": ctx.counts}
        with ThreadPoolExecutor(max_workers=conc) as ex:
            rows = list(ex.map(work, units))
    wall_s = time.time() - t0
    for r in rows:
        append_jsonl(run_dir / "outputs.jsonl", r)
    outputs = {r["id"]: (r["output"] if r["status"] == "ok" else None) for r in rows}
    sc = score_run(ws, run_dir, units, outputs, split)
    calls = read_jsonl(run_dir / "calls.jsonl")
    summary = _summarize(units, rows, calls, sc, wall_s, conc)
    counts = {}
    for r in rows:
        for k, v in (r.get("counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
    summary["pipeline_counts"] = counts
    summary["quota_exhausted"] = log.quota_hit.is_set()
    write_json(run_dir / "summary.json", summary)
    meta["ended"] = now_iso()
    write_json(run_dir / "meta.json", meta)
    _print_summary(run_id, summary, purpose == "holdout")
    print(f"  dir={run_dir}")
    if summary["quota_exhausted"]:
        clock = [r for r in read_jsonl(ws / "ledger.jsonl") if r.get("event") == "clock"]
        if not clock or clock[-1].get("action") != "pause":
            append_jsonl(ws / "ledger.jsonl", {"event": "clock", "action": "pause", "at": now_iso(),
                                               "epoch": time.time(), "reason": "quota exhausted (runner)"})
        print("  QUOTA EXHAUSTED: this run is incomplete (it will be void); the time-limit clock is paused. "
              "Stop launching runs and resume after the quota resets (`ledger.py clock WS resume`).")
    return run_dir


def rescore(ws, run_dir):
    run_dir = Path(run_dir)
    meta = read_json(run_dir / "meta.json")
    old = read_json(run_dir / "score.json")
    if old:
        write_json(run_dir / f"score.{old.get('eval_hash', 'old')}.json", old)
    dataset, _ = load_eval(ws)
    by_id = {u["id"]: u for u in dataset.load_units(meta["split"])}
    units = [by_id[i] for i in meta["unit_ids"] if i in by_id]
    rows = read_jsonl(run_dir / "outputs.jsonl")
    outputs = {r["id"]: (r["output"] if r["status"] == "ok" else None) for r in rows}
    sc = score_run(ws, run_dir, units, outputs, meta["split"])
    summary = read_json(run_dir / "summary.json", {})
    summary.update(primary=sc.get("primary"), metrics=sc.get("metrics", {}),
                   gate_failures=len(sc.get("gate_failures", [])), items=len(sc.get("items", [])))
    write_json(run_dir / "summary.json", summary)
    meta["eval_hash"] = sc["eval_hash"]
    meta["rescored"] = now_iso()
    write_json(run_dir / "meta.json", meta)
    print(f"RESCORED {meta['run_id']} primary={sc.get('primary')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--candidate")
    ap.add_argument("--exp", help="ledger experiment id (candidate and purpose come from the proposal)")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--purpose", choices=sorted(PURPOSES))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--tag", help="label for repeats, e.g. r2")
    ap.add_argument("--rescore", metavar="RUN_DIR")
    a = ap.parse_args()
    if a.rescore:
        rescore(a.ws, a.rescore)
        return
    candidate, purpose = a.candidate, a.purpose
    if a.exp:
        prop = [r for r in ledger_rows(a.ws) if r.get("exp") == a.exp and r.get("event") == "propose"]
        if not prop:
            sys.exit(f"unknown experiment {a.exp}; record it with ledger.py propose first")
        candidate = candidate or prop[-1]["candidate"]
        purpose = purpose or ("baseline" if prop[-1].get("lever") == "baseline" else "experiment")
    if not candidate or not purpose:
        sys.exit("need --candidate and --purpose (or --exp)")
    run(a.ws, candidate, a.split, purpose, exp=a.exp, limit=a.limit, concurrency=a.concurrency, tag=a.tag)


if __name__ == "__main__":
    main()
