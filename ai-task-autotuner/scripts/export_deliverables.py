"""Assemble the user-facing deliverables and recommend production settings from measurements.

  python export_deliverables.py WS --candidate c007-x [--runs RUN_DIR ...] [--out WS/deliverables]

Writes:
  deliverables/pipeline/            final candidate (pipeline.py, prompts/, config.json)
  deliverables/production_config.json   recommended settings, each with the measurement behind it
  deliverables/eval/ + charter.json  dataset.py, score.py, splits.json, reference/ - the same layout as a
                                    workspace, so run_experiment/validate_eval work on the deliverables folder
                                    (fix machine-specific paths with relocate_workspace.py)
  deliverables/data_built/          constructed task inputs, if any
  deliverables/MANIFEST.md          what is where (task_design.md, report.md and non_ai_findings.md are
                                    written by you; this script copies them if present in WS)

Settings come from the given runs (default: every non-smoke run of the candidate):
  max_tokens  ~ 1.5 x the largest observed output (incl. thinking), at least p99 x 2, rounded up
  timeout_s   ~ 3 x p99 per-call latency (api_ms) + process start-up allowance on the cli backend
  concurrency the level measured without throttling errors
  effort / thinking as measured; cost and latency per unit with their basis
"""
import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import now_iso, percentile, read_json, read_jsonl, write_json  # noqa: E402
from workspace import list_runs  # noqa: E402


def _round_up(x, step):
    return int(math.ceil(x / step) * step)


def recommend(ws, candidate, run_dirs):
    calls, summaries, metas = [], [], []
    for d in run_dirs:
        calls += read_jsonl(Path(d) / "calls.jsonl")
        summaries.append(read_json(Path(d) / "summary.json", {}))
        metas.append(read_json(Path(d) / "meta.json", {}))
    cfg = dict(metas[-1].get("config", {})) if metas else {}
    outs = [(c.get("usage") or {}).get("output") for c in calls]
    outs = [o for o in outs if o is not None]
    api = [c.get("api_ms") for c in calls if c.get("api_ms") is not None]
    wall = [c.get("last_attempt_wall_ms") or c.get("wall_ms") for c in calls]
    wall = [w for w in wall if w is not None]
    thinking = [(c.get("usage") or {}).get("thinking") for c in calls]
    thinking = [t for t in thinking if t is not None]
    truncated = sum(1 for c in calls if c.get("error_kind") == "truncated" or c.get("stop_reason") == "max_tokens")
    errors = {}
    for c in calls:
        if c.get("error_kind") and c.get("error_kind") not in ("parse",):
            errors[c["error_kind"]] = errors.get(c["error_kind"], 0) + 1
    rec = {"generated": now_iso(), "candidate": candidate, "runs": [str(d) for d in run_dirs],
           "n_calls_measured": len(calls), "settings": {}, "expectations": {}, "caveats": []}
    S = rec["settings"]
    S["backend_measured"] = cfg.get("backend", "cli")
    S["model"] = {"value": cfg.get("model"), "served": sorted({c.get("model_served") for c in calls if c.get("model_served")})}
    tried = sorted({str(r.get("config", {}).get("effort")) for r in list_runs(ws) if r.get("purpose") != "smoke"})
    S["effort"] = {"value": cfg.get("effort"),
                   "why": f"as measured; efforts measured in this workspace: {tried} ('low' is the lowest level)"}
    S["thinking"] = {"value": cfg.get("thinking", "off"),
                     "measured_thinking_tokens_per_call": {"p50": percentile(thinking, .5), "max": max(thinking) if thinking else None}}
    if outs:
        mx, p99 = max(outs), percentile(outs, .99)
        mt = max(_round_up(mx * 1.5, 256), _round_up(p99 * 2, 256), 512)
        S["max_tokens"] = {"value": mt, "why": f"observed output (incl. thinking) p50={percentile(outs, .5)} "
                           f"p99={p99} max={mx}; truncated calls={truncated}"}
        if truncated:
            rec["caveats"].append(f"{truncated} calls hit max_tokens during measurement: the recommendation is a floor; "
                                  "re-measure with a higher limit")
    if api:
        p99a = percentile(api, .99)
        overhead = (percentile(wall, .99) - p99a) if (wall and S["backend_measured"] == "cli") else 0
        S["timeout_s"] = {"value": max(30, _round_up((3 * p99a + max(0, overhead)) / 1000, 10)),
                          "why": f"api_ms p50={percentile(api, .5)} p99={p99a} max={max(api)}"
                                 + (f"; cli start-up ~{overhead:.0f} ms at p99" if overhead else "")}
    conc = [m.get("config", {}).get("concurrency") for m in metas]
    S["concurrency"] = {"value": max([c for c in conc if c] or [1]),
                        "why": f"highest measured; throttling/infra errors seen: {errors or 'none'}"}
    S["retries"] = {"value": cfg.get("retries", 2), "why": "retry with jittered backoff on 429/5xx/timeouts only"}
    S["prompt_caching"] = {"value": "static prefix first; cache the system prompt",
                           "measured_cache_hit_ratio": [s.get("cache_hit_ratio") for s in summaries]}
    E = rec["expectations"]
    units = sum(s.get("units", 0) for s in summaries)
    unc = [s.get("cost_uncached_list_usd") for s in summaries]
    E["calls_per_unit"] = (len(calls) / units) if units else None
    if None not in unc and units:
        E["cost_usd_per_unit_uncached_list"] = sum(unc) / units
        E["cost_usd_per_1000_units_uncached_list"] = 1000 * sum(unc) / units
    # cli-reported cost depends on cache state (claude -p caches whole prompts): list runs separately
    E["cost_per_unit_by_run"] = [{"run": Path(d).name, "cli_reported": s.get("cost_usd_per_unit"),
                                  "cache_hit_ratio": s.get("cache_hit_ratio")} for d, s in zip(run_dirs, summaries)]
    E["cost_basis"] = ("uncached list price of every input-side token (backend-neutral); cli-reported costs per run "
                       "are shown with their cache hit ratio - a warm rerun can be several times cheaper")
    E["api_ms_per_call_p50"] = percentile(api, .5) if api else None
    E["unit_wall_ms_p50"] = percentile([s.get("unit_wall_ms", {}).get("p50") for s in summaries], .5)
    if S["backend_measured"] == "cli":
        rec["caveats"].append("measured through claude -p: ~800 extra input tokens per call and process start-up "
                              "time that a direct API call would not have; the cli cannot turn thinking off, so the "
                              "api backend with thinking off may produce fewer output tokens")
    if any(s.get("ai_free") for s in summaries):
        rec["caveats"].append("this candidate makes no model calls: model settings do not apply")
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--runs", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args()
    ws = Path(a.ws)
    out = Path(a.out) if a.out else ws / "deliverables"
    out.mkdir(parents=True, exist_ok=True)
    cand = ws / "candidates" / a.candidate
    if not cand.exists():
        sys.exit(f"no candidate {cand}")
    runs = a.runs or [m["_dir"] for m in list_runs(ws) if m.get("candidate") == a.candidate and m.get("purpose") != "smoke"]
    if not runs:
        sys.exit("no measured runs of this candidate")
    shutil.copytree(cand, out / "pipeline", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("dataset.py", "score.py", "splits.json", "eval_spec.md", "validation.json"):
        src = ws / "eval" / name
        if src.exists():
            (out / "eval").mkdir(exist_ok=True)
            shutil.copy2(src, out / "eval" / name)
    ref = ws / "eval" / "reference"
    if ref.exists() and any(f.is_file() for f in ref.rglob("*")):
        shutil.copytree(ref, out / "eval" / "reference", dirs_exist_ok=True)
    shutil.copy2(ws / "charter.json", out / "charter.json")
    built = ws / "data_built"
    if built.exists():
        shutil.copytree(built, out / "data_built", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    rec = recommend(ws, a.candidate, runs)
    write_json(out / "production_config.json", rec)
    copied = []
    for name in ("task_design.md", "report.md", "non_ai_findings.md", "experiment_plan.md"):
        if (ws / name).exists():
            shutil.copy2(ws / name, out / name)
            copied.append(name)
    manifest = ["# Deliverables", "", f"generated {rec['generated']} from candidate `{a.candidate}`", "",
                "| path | content |", "|---|---|",
                "| pipeline/ | final pipeline code, prompts and config |",
                "| production_config.json | recommended settings with the measurements behind them |",
                "| eval/, charter.json | dataset, splits, scorer, reference (provenance in eval/eval_spec.md) - "
                "re-measure later with run_experiment.py on this folder after relocate_workspace.py |",
                "| data_built/ | constructed task inputs (if the task was derived from raw data) |"]
    manifest += [f"| {n} | |" for n in copied]
    missing = [n for n in ("task_design.md", "report.md") if n not in copied]
    if missing:
        manifest += ["", f"Missing (write them in WS and re-run): {', '.join(missing)}"]
    (out / "MANIFEST.md").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    s = rec["settings"]
    print(f"deliverables -> {out}")
    print(f"  max_tokens={s.get('max_tokens', {}).get('value')} timeout_s={s.get('timeout_s', {}).get('value')} "
          f"concurrency={s['concurrency']['value']} effort={s['effort']['value']} thinking={s['thinking']['value']}")
    print(f"  cost/unit uncached list={rec['expectations'].get('cost_usd_per_unit_uncached_list')}")
    for c in rec["caveats"]:
        print(f"  caveat: {c}")
    if missing:
        print(f"  missing: {missing}")


if __name__ == "__main__":
    main()
