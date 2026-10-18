"""Compact, context-friendly analysis of one run (optionally versus another).

  python analyze_run.py RUN_DIR [--compare OTHER_RUN] [--top 8]
  python analyze_run.py RUN_DIR --show UNIT_OR_ITEM_ID [--max-chars 1200]
  python analyze_run.py RUN_DIR --dump-failures OUT.jsonl [--limit 60]   # hand-off file for an analyzer subagent
  python analyze_run.py RUN_DIR --consistency RUN2 [RUN3 ...]           # pass^k / pass@k over repeats

The default view stays under ~40 lines: quality, failure tags, worst units, and
efficiency (tokens, cost, latency percentiles, retries, truncation, slow calls).
Raw prompts and responses are printed only with --show, truncated.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import paired_items, percentile, read_json, read_jsonl  # noqa: E402


def _load(run_dir):
    d = Path(run_dir)
    return {"dir": d, "meta": read_json(d / "meta.json", {}), "summary": read_json(d / "summary.json", {}),
            "score": read_json(d / "score.json", {}), "calls": read_jsonl(d / "calls.jsonl"),
            "outputs": read_jsonl(d / "outputs.jsonl")}


def _clip(text, n):
    text = "" if text is None else str(text)
    if len(text) <= n:
        return text
    half = n // 2
    return text[:half] + f"\n...[{len(text) - n} chars omitted]...\n" + text[-half:]


def overview(R, top):
    m, s, sc = R["meta"], R["summary"], R["score"]
    holdout = m.get("purpose") == "holdout"
    print(f"RUN {m.get('run_id')} cand={m.get('candidate')} split={m.get('split')} purpose={m.get('purpose')}")
    print(f"QUALITY primary={s.get('primary')} metrics={json.dumps(s.get('metrics', {}), ensure_ascii=False)[:260]}")
    print(f"  units={s.get('units')} status={s.get('status')} gate_failures={len(sc.get('gate_failures', []))} "
          f"items={len(sc.get('items', []))}")
    for g in sc.get("gate_failures", [])[:3]:
        print(f"  gate: {str(g)[:160]}")
    if holdout:
        print("  (holdout run: failure details withheld to keep it held out)")
    else:
        items = sc.get("items", [])
        fails = [i for i in items if i.get("score", 0) < 1]
        tags = Counter(t for i in fails for t in (i.get("tags") or ["untagged"]))
        print(f"FAILURES {len(fails)}/{len(items)} items; tags: " + ", ".join(f"{t}:{c}" for t, c in tags.most_common(top)))
        per_unit = Counter(i.get("unit") for i in fails)
        print("  worst units: " + ", ".join(f"{u}:{c}" for u, c in per_unit.most_common(top)))
        errs = [o for o in R["outputs"] if o.get("status") != "ok"]
        for o in errs[:3]:
            print(f"  unit {o['id']} {o['status']}: {str(o.get('error'))[:140]}")
    t = s.get("tokens", {})
    calls = R["calls"]
    outs = [(c.get("usage") or {}).get("output") for c in calls]
    trunc = sum(1 for c in calls if c.get("error_kind") == "truncated")
    parse = sum(1 for c in calls if c.get("error_kind") == "parse")
    print(f"EFFICIENCY calls={s.get('calls')} ({s.get('calls_per_unit')}/unit) cost=${s.get('cost_usd')} "
          f"(${s.get('cost_usd_per_unit')}/unit, complete={s.get('cost_complete')}) cache_hit={s.get('cache_hit_ratio')}")
    print(f"  tokens in={t.get('input')} cache_r={t.get('cache_read')} cache_w={t.get('cache_write')} "
          f"out={t.get('output')} think={t.get('thinking')} | out/call p50={percentile(outs, .5)} p90={percentile(outs, .9)} max={max([o for o in outs if o is not None], default=None)}")
    a, u = s.get("api_ms", {}), s.get("unit_wall_ms", {})
    print(f"  api_ms/call p50={a.get('p50')} p90={a.get('p90')} max={a.get('max')} | unit wall p50={u.get('p50')} "
          f"p90={u.get('p90')} max={u.get('max')} sum={u.get('sum')} | run wall={s.get('run_wall_s')}s conc={s.get('concurrency')}")
    print(f"  retries={s.get('retries')} call_errors={s.get('call_errors')} truncated={trunc} parse_fail={parse} "
          f"served={s.get('models_served')} pipeline_counts={s.get('pipeline_counts')}")
    slow = sorted((c for c in calls if c.get("api_ms") is not None), key=lambda c: -c["api_ms"])[:3]
    for c in slow:
        uu = c.get("usage") or {}
        print(f"  slow: unit={c.get('unit_id')} tag={c.get('tag')} api_ms={c['api_ms']} in={uu.get('input')}+{uu.get('cache_read')}r out={uu.get('output')}")


def compare(R, O, top):
    a, b = O["score"].get("items", []), R["score"].get("items", [])
    pi = paired_items(a, b)
    sa, sb = O["summary"], R["summary"]
    print(f"COMPARE {O['meta'].get('run_id')} -> {R['meta'].get('run_id')}")
    print(f"  primary {sa.get('primary')} -> {sb.get('primary')} | items +{pi['improved']}/-{pi['regressed']} of {pi['n']} "
          f"(sign p={pi['p']:.3f}, paired band={pi['band']})")
    for k in sorted(set(sa.get("metrics", {})) | set(sb.get("metrics", {}))):
        va, vb = sa.get("metrics", {}).get(k), sb.get("metrics", {}).get(k)
        if va != vb:
            print(f"  {k}: {va} -> {vb}")
    for key in ("cost_usd_per_unit", "calls_per_unit", "api_ms_per_unit"):
        va, vb = sa.get(key), sb.get(key)
        ratio = f"x{vb / va:.2f}" if va and vb else ""
        print(f"  {key}: {va} -> {vb} {ratio}")
    if R["meta"].get("purpose") != "holdout" and O["meta"].get("purpose") != "holdout":
        tags_a = {i["id"]: i.get("tags") for i in a}
        tags_b = {i["id"]: i.get("tags") for i in b}
        print("  improved: " + ", ".join(f"{i}{tags_a.get(i) or ''}" for i in pi["improved_ids"][:top]))
        print("  regressed: " + ", ".join(f"{i}{tags_b.get(i) or ''}" for i in pi["regressed_ids"][:top]))


def show(R, key, max_chars):
    items = [i for i in R["score"].get("items", []) if i.get("id") == key or i.get("unit") == key]
    unit = items[0].get("unit") if items else key
    for i in items[:20]:
        print(f"ITEM {i['id']} score={i.get('score')} tags={i.get('tags')}")
        print(f"  pred={json.dumps(i.get('pred'), ensure_ascii=False)[:300]}")
        print(f"  ref ={json.dumps(i.get('ref'), ensure_ascii=False)[:300]}")
    for c in R["calls"]:
        if c.get("unit_id") != unit:
            continue
        u = c.get("usage") or {}
        print(f"--- CALL seq={c.get('seq')} tag={c.get('tag')} ok={c.get('ok')} err={c.get('error_kind')} "
              f"stop={c.get('stop_reason')} api_ms={c.get('api_ms')} in={u.get('input')} cr={u.get('cache_read')} "
              f"out={u.get('output')} think={u.get('thinking')}")
        print("[system sha]", c.get("system_sha"))
        print("[prompt]\n" + _clip(c.get("prompt"), max_chars))
        print("[response]\n" + _clip(c.get("text"), max_chars))
    for o in R["outputs"]:
        if o.get("id") == unit:
            print(f"--- OUTPUT status={o.get('status')} notes={o.get('notes')} counts={o.get('counts')}")
            print(_clip(json.dumps(o.get("output"), ensure_ascii=False), max_chars))


def dump_failures(R, out, limit):
    if R["meta"].get("purpose") == "holdout":
        sys.exit("refusing to dump holdout failures")
    resp = {}
    for c in R["calls"]:
        resp.setdefault(c.get("unit_id"), []).append(_clip(c.get("text"), 3000))
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for i in R["score"].get("items", []):
            if i.get("score", 0) >= 1:
                continue
            f.write(json.dumps({"id": i["id"], "unit": i.get("unit"), "tags": i.get("tags"), "pred": i.get("pred"),
                                "ref": i.get("ref"), "responses": resp.get(i.get("unit"), [])[:3]},
                               ensure_ascii=False) + "\n")
            n += 1
            if n >= limit:
                break
    print(f"wrote {n} failed items to {out}")


def consistency(runs):
    per = {}
    for R in runs:
        for i in R["score"].get("items", []):
            per.setdefault(i["id"], []).append(i.get("score", 0) >= 1)
    k = len(runs)
    full = [v for v in per.values() if len(v) == k]
    if not full:
        print("no shared items")
        return
    pass_all = sum(all(v) for v in full) / len(full)
    pass_any = sum(any(v) for v in full) / len(full)
    flaky = sum(1 for v in full if any(v) and not all(v))
    print(f"CONSISTENCY k={k} items={len(full)} pass^k={pass_all:.4f} pass@k={pass_any:.4f} flaky_items={flaky}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--compare")
    ap.add_argument("--show")
    ap.add_argument("--dump-failures")
    ap.add_argument("--consistency", nargs="+")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    R = _load(a.run)
    if a.show:
        if R["meta"].get("purpose") == "holdout":
            sys.exit("refusing to show holdout items")
        show(R, a.show, a.max_chars)
    elif a.dump_failures:
        dump_failures(R, a.dump_failures, a.limit)
    elif a.consistency:
        consistency([R] + [_load(x) for x in a.consistency])
    else:
        overview(R, a.top)
        if a.compare:
            compare(R, _load(a.compare), a.top)


if __name__ == "__main__":
    main()
