"""Assemble the user-facing deliverables and recommend production settings from measurements.

  python export_deliverables.py WS --candidate c007-x [--runs RUN_DIR ...] [--out WS/deliverables]

Writes:
  deliverables/candidates/<name>/   final candidate (pipeline.py, prompts/, config.json)
  deliverables/results/             outputs of the full-scope and holdout runs given with --runs
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
from common import append_jsonl, now_iso, percentile, read_json, read_jsonl, write_json  # noqa: E402
from workspace import candidate_hash, data_fingerprint, eval_hash, list_runs  # noqa: E402
from quality_gate import gate_allows_runs  # noqa: E402
from task_review import task_review_allows_runs  # noqa: E402
from relocate_workspace import relocate  # noqa: E402
from ledger import KEEPS, _state as ledger_state  # noqa: E402
from llm_call import api_thinking_off  # noqa: E402


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
                   "why": f"requested setting; efforts measured in this workspace: {tried}; supported levels depend on the model"}
    S["thinking"] = {"value": cfg.get("thinking", "off"), "requested": cfg.get("thinking", "off"),
                     "measured_thinking_tokens_per_call": {"p50": percentile(thinking, .5), "max": max(thinking) if thinking else None}}
    if S["backend_measured"] in ("cli", "claude", "codex"):
        S["thinking"]["value"] = "backend managed"
        S["thinking"]["why"] = "the adapter does not enforce CLI thinking on/off; low effort is not thinking off"
    elif S["backend_measured"] == "api" and S["thinking"]["requested"] == "off":
        # A pipeline can override the model, backend or effort for individual calls.
        # Workspace defaults alone cannot establish whether its off requests applied.
        off_controls = []
        for call in calls:
            requested = call.get("thinking_requested", call.get("thinking"))
            model = call.get("model_requested")
            effort = call.get("effort")
            if requested != "off":
                off_controls.append(None)
            elif call.get("backend") in ("cli", "claude", "codex"):
                off_controls.append(False)
            elif call.get("backend") != "api" or not model or effort is None:
                off_controls.append(None)
            else:
                off_controls.append(api_thinking_off(model, effort) is not None)
        if any(t > 0 for t in thinking):
            S["thinking"]["value"] = "unverified"
            S["thinking"]["why"] = "thinking tokens were reported despite the off request; investigate before claiming off"
        elif False in off_controls:
            S["thinking"]["value"] = "backend managed"
            S["thinking"]["why"] = "at least one measured call did not apply the requested off control"
        elif not off_controls or None in off_controls:
            S["thinking"]["value"] = "unverified"
            S["thinking"]["why"] = "call-level thinking settings are missing or mixed; defaults cannot establish off"
        else:
            S["thinking"]["why"] = "supported off request in this adapter; absent usage is not proof of zero thinking"
    if outs and S["backend_measured"] != "codex":
        mx, p99 = max(outs), percentile(outs, .99)
        mt = max(_round_up(mx * 1.5, 256), _round_up(p99 * 2, 256), 512)
        S["max_tokens"] = {"value": mt, "why": f"observed output (incl. thinking) p50={percentile(outs, .5)} "
                           f"p99={p99} max={mx}; truncated calls={truncated}"}
        if truncated:
            rec["caveats"].append(f"{truncated} calls hit max_tokens during measurement: the recommendation is a floor; "
                                  "re-measure with a higher limit")
    if S["backend_measured"] == "codex":
        S["max_tokens"] = {"value": None, "why": "Codex CLI has no supported max-output-token flag"}
        if wall:
            S["timeout_s"] = {"value": max(30, _round_up(3 * percentile(wall, .99) / 1000, 10)),
                              "why": "3 x p99 measured CLI wall time; API-only latency is not reported"}
        rec["caveats"].append("measured through codex exec: USD cost, API-only latency, thinking tokens and served model "
                              "may not be reported; max_tokens and thinking=off are not enforced")
    elif api:
        p99a = percentile(api, .99)
        overhead = (percentile(wall, .99) - p99a) if (wall and S["backend_measured"] in ("cli", "claude")) else 0
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
    if S["backend_measured"] in ("cli", "claude"):
        rec["caveats"].append("measured through claude -p: ~800 extra input tokens per call and process start-up "
                              "time were observed in the documented calibration; the adapter does not enforce "
                              "thinking=off. Compare with an API only where that model supports disabling thinking")
    if any(s.get("ai_free") for s in summaries):
        rec["caveats"].append("this candidate makes no model calls: model settings do not apply")
    return rec


def export_workspace(ws, candidate, run_dirs=None, out=None):
    """Export replay dependencies and control history, without unrelated raw calls/outputs."""
    ws = Path(ws).resolve()
    out = Path(out).resolve() if out else ws / "deliverables"
    if out == ws or ws.is_relative_to(out):
        raise ValueError("export destination must not contain the source workspace")
    if any(out.is_relative_to(ws / name) for name in ("audit", "review", "eval", "candidates", "data_built", "runs")):
        raise ValueError("export destination must not alter workspace evidence or data")
    if out.exists() and any(out.iterdir()):
        raise ValueError("export destination must be empty; use a new directory")
    cand = ws / "candidates" / candidate
    if not cand.is_dir() or cand.parent != ws / "candidates":
        raise ValueError(f"no candidate {cand}")
    for allowed, reason in (gate_allows_runs(ws), task_review_allows_runs(ws)):
        if not allowed:
            raise ValueError("cannot export a replayable workspace: " + reason)
    relocate(ws, dry_run=True)  # verify all replay evidence before creating the export
    runs = [Path(d).resolve() for d in run_dirs] if run_dirs is not None else [
        Path(m["_dir"]) for m in list_runs(ws)
        if m.get("candidate") == candidate and m.get("purpose") != "smoke"]
    if not runs:
        raise ValueError("no measured runs of this candidate")
    configs = []
    current_candidate_hash = candidate_hash(cand)
    current_data_hash = data_fingerprint(ws)
    current_eval_hash = eval_hash(ws)
    for d in runs:
        meta = read_json(d / "meta.json", {})
        if (not d.is_relative_to(ws / "runs") or meta.get("candidate") != candidate
                or meta.get("purpose") == "smoke"):
            raise ValueError("--runs must name measured runs of the exported candidate in this workspace")
        if not all((d / name).is_file() for name in ("meta.json", "summary.json", "score.json", "outputs.jsonl")):
            raise ValueError(f"incomplete measured run: {d}")
        score = read_json(d / "score.json")
        if (meta.get("candidate_hash") != current_candidate_hash
                or meta.get("data_hash") != current_data_hash
                or meta.get("eval_hash") != current_eval_hash
                or score.get("eval_hash") != meta.get("eval_hash")):
            raise ValueError(f"stale or mismatched measured run: {d}; repair/rescore before export")
        configs.append(meta.get("config", {}))
    if any(c != configs[0] for c in configs[1:]):
        raise ValueError("selected runs used different effective settings; select one measured configuration")
    baseline = read_json(ws / "review" / "task_review.json")["snapshot"]["candidate"]
    # The retained ledger can compare or roll back to every still-accepted candidate.
    # Scores alone cannot establish attribution: those candidates' frozen code must
    # survive removal of the source workspace as well as the final/reviewed baseline.
    retained_candidates = {candidate, baseline}
    retained_candidates.update(d["candidate"] for d in ledger_state(ws)["decisions"].values()
                               if d.get("decision") in KEEPS)
    for name in retained_candidates:
        if (not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name
                or not (ws / "candidates" / name / "pipeline.py").is_file()):
            raise ValueError(f"cannot preserve accepted candidate {name!r}: candidate files are missing or invalid")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cand, out / "candidates" / candidate, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    # the task's actual outputs: the user needs results, not only the method
    for d in runs:
        meta = read_json(Path(d) / "meta.json", {})
        if meta.get("purpose") in ("full", "holdout") and (Path(d) / "outputs.jsonl").exists():
            (out / "results").mkdir(exist_ok=True)
            shutil.copy2(Path(d) / "outputs.jsonl", out / "results" / f"{meta['run_id']}.outputs.jsonl")
            shutil.copy2(Path(d) / "summary.json", out / "results" / f"{meta['run_id']}.summary.json")
    # Task-specific helpers under eval/ are executable dependencies too.
    shutil.copytree(ws / "eval", out / "eval", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    ch = read_json(ws / "charter.json")
    # The root defaults may have changed after measurement. Preserve the selected
    # run's effective settings without mutating the frozen candidate bytes.
    ch["backend"] = configs[-1]
    write_json(out / "charter.json", ch)
    shutil.copytree(ws / "audit", out / "audit", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    if (ws / "ledger.jsonl").exists():
        shutil.copy2(ws / "ledger.jsonl", out / "ledger.jsonl")
    # Budget, STOP, void and holdout history must not disappear on export. Scores
    # retain ledger comparability; large calls/outputs are kept only when needed.
    for name in sorted(retained_candidates - {candidate}):
        shutil.copytree(ws / "candidates" / name, out / "candidates" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    for meta in list_runs(ws):
        src = Path(meta["_dir"])
        dest = out / "runs" / src.name
        dest.mkdir(parents=True, exist_ok=True)
        names = {"meta.json", "summary.json", "score.json"}
        if src in runs:
            names |= {"outputs.jsonl", "calls.jsonl"}
        if meta.get("purpose") == "baseline":
            names.add("outputs.jsonl")  # reproduce evaluation validation/noise
        for name in names:
            if (src / name).is_file():
                shutil.copy2(src / name, dest / name)
        if src not in runs:
            # Incomplete runs may have incurred costs before summary.json was
            # written. Keep each observed event, including unreported USD cost,
            # without copying prompts, generated text or unrelated model inputs.
            for call in read_jsonl(src / "calls.jsonl"):
                control = {k: v for k, v in call.items() if k in {
                    "seq", "unit_id", "tag", "cost_usd", "usage", "attempts", "attempt_errors",
                    "error_kind", "stop_reason", "model_requested", "model_served", "backend",
                    "wall_ms", "last_attempt_wall_ms", "api_ms"}}
                append_jsonl(dest / "calls.jsonl", control)
    # A brief stored inside WS is a reviewed design dependency too.
    if ch.get("brief"):
        brief = Path(ch["brief"])
        brief = brief if brief.is_absolute() else ws / brief
        if brief.is_file() and brief.resolve().is_relative_to(ws):
            target = out / brief.resolve().relative_to(ws)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(brief, target)
    external = [p for kind in ("inputs", "aux") for p in ch.get("data", {}).get(kind, [])
                if not Path(p).resolve().is_relative_to(ws)]
    if ch.get("brief") and not brief.resolve().is_relative_to(ws):
        external.append(str(brief))
    write_json(out / "replay.json", {
        "version": 1, "source_workspace": str(ws), "candidate": candidate,
        "reviewed_baseline": baseline, "external_dependencies": sorted(set(external)),
        "retained_candidates": sorted(retained_candidates),
        "retained_run_history": "all meta/summary/score and stripped call control events; selected runs include raw calls/outputs; baseline includes outputs",
        "source_data_hashes": read_json(ws / "audit" / "quality_gate.json", {}).get("data_hashes", {}),
    })
    built = ws / "data_built"
    if built.exists():
        shutil.copytree(built, out / "data_built", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for kind in ("inputs", "aux"):
        for source in ch.get("data", {}).get(kind, []):
            source = Path(source).resolve()
            if source.is_relative_to(ws):
                if source == ws or out.is_relative_to(source):
                    raise ValueError("an input directory contains the export destination; choose an external --out")
                target = out / source.relative_to(ws)
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    review_dir = ws / "review"
    if review_dir.exists():
        shutil.copytree(review_dir, out / "review", dirs_exist_ok=True)
    rec = recommend(ws, candidate, runs)
    write_json(out / "production_config.json", rec)
    copied = []
    for name in ("task_design.md", "report.md", "non_ai_findings.md", "experiment_plan.md"):
        if (ws / name).exists():
            shutil.copy2(ws / name, out / name)
            copied.append(name)
    manifest = ["# Deliverables", "", f"generated {rec['generated']} from candidate `{candidate}`", "",
                "| path | content |", "|---|---|",
                f"| candidates/{candidate}/ | final pipeline code, prompts and config (workspace layout: "
                "run_experiment.py works on this folder) |",
                "| candidates/ (other retained folders) | reviewed baseline and all currently accepted candidates; "
                "frozen code for ledger comparisons, repeats and rollback |",
                "| results/ | outputs and summaries of the full-scope / holdout runs passed to export |",
                "| production_config.json | recommended settings with the measurements behind them |",
                "| eval/, charter.json | dataset, splits, scorer, reference (provenance in eval/reference_provenance.json) - "
                "re-measure later with run_experiment.py on this folder after relocate_workspace.py |",
                "| data_built/ | constructed task inputs (if the task was derived from raw data) |",
                "| audit/ | original data audit, gate and any user acceptance; hashes still enforced |",
                "| ledger.jsonl, runs/ | original decisions, STOP, budget and holdout history plus baseline validation outputs |",
                "| replay.json | original path mapping, retained history and external data/brief dependencies |"]
    if review_dir.exists():
        manifest += ["| review/ | independent task-design review, findings and recorded checks |"]
    manifest += [f"| {n} | |" for n in copied]
    manifest += ["", "Run `relocate_workspace.py THIS_FOLDER --data-map OLD=NEW` before replay. Bundled workspace paths "
                "are remapped automatically; external data/brief paths require a map when unavailable. Relocation "
                "requires the same bytes and preserves review and audit decisions. Changed data requires a new audit "
                "and review. Original STOP, time/budget and holdout limits continue to apply; export does not reset them.",
                "", "Run metadata/summaries/scores and call control events (including unfinished costs) are retained "
                "for all history; raw prompts/results of unrelated candidates are omitted. Re-scoring those omitted runs requires the original workspace."]
    missing = [n for n in ("task_design.md", "report.md") if n not in copied]
    if missing:
        manifest += ["", f"Missing (write them in WS and export to a new destination): {', '.join(missing)}"]
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
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--runs", nargs="*")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        export_workspace(a.ws, a.candidate, a.runs or None, a.out)
    except (ValueError, OSError, KeyError) as e:
        ap.exit(2, f"EXPORT REFUSED: {e}\n")


if __name__ == "__main__":
    main()
