"""Generic data profiler for audit (stdlib only).

Profiles .jsonl / .json / .csv / .tsv files and directories of .json files without
printing the data itself: record counts, field paths (nested lists as `[]`),
presence and null rates, types, distinct counts, length/size/numeric stats,
duplicate values across records, duplicate records, and file hashes.

  python profile_data.py DATA [DATA...] --out WS/audit/profile [--max-depth 4] [--max-rows 80]

Writes <out>.json (full detail) and <out>.md (compact) and prints the compact view.
Other file types are listed with size and hash only.
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import percentile, sha256_file, write_json  # noqa: E402

DISTINCT_CAP = 5000
TOP_VALUES_IF_DISTINCT_LE = 30


class FieldStats:
    __slots__ = ("count", "nulls", "types", "values", "capped", "lengths", "numbers", "records_with",
                 "per_record_max", "value_records")

    def __init__(self):
        self.count = 0
        self.nulls = 0
        self.types = Counter()
        self.values = Counter()
        self.capped = False
        self.lengths = []      # string length or list size
        self.numbers = []
        self.records_with = 0
        self.per_record_max = 0
        self.value_records = defaultdict(set)  # scalar value -> record indexes (for dup-across-records)


def _type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _walk(value, path, depth, max_depth, sink):
    sink.append((path, value))
    if depth >= max_depth:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}.{k}" if path else str(k), depth + 1, max_depth, sink)
    elif isinstance(value, list):
        for v in value:
            _walk(v, f"{path}[]", depth + 1, max_depth, sink)


def profile_records(records, max_depth):
    fields = defaultdict(FieldStats)
    record_hashes = Counter()
    for idx, rec in enumerate(records):
        record_hashes[json.dumps(rec, sort_keys=True, ensure_ascii=False)] += 1
        sink = []
        _walk(rec, "", 0, max_depth, sink)
        per_record = Counter()
        for path, v in sink:
            if path == "":
                continue
            fs = fields[path]
            fs.count += 1
            per_record[path] += 1
            t = _type(v)
            fs.types[t] += 1
            if v is None:
                fs.nulls += 1
                continue
            if t == "string":
                fs.lengths.append(len(v))
            elif t == "list":
                fs.lengths.append(len(v))
            elif t == "number":
                fs.numbers.append(v)
            if t in ("string", "number", "bool"):
                key = v if t != "string" or len(v) <= 300 else v[:300]
                if not fs.capped:
                    fs.values[key] += 1
                    if len(fs.values) > DISTINCT_CAP:
                        fs.capped = True
                if len(fs.value_records) <= DISTINCT_CAP:
                    fs.value_records[key].add(idx)
        for path, c in per_record.items():
            fields[path].records_with += 1
            fields[path].per_record_max = max(fields[path].per_record_max, c)
    n = len(records)
    out = {}
    for path, fs in fields.items():
        d = {
            "count": fs.count,
            "records_with_pct": round(100 * fs.records_with / n, 1) if n else None,
            "null_pct": round(100 * fs.nulls / fs.count, 1) if fs.count else None,
            "types": dict(fs.types),
            "distinct": None if fs.capped else len(fs.values),
            "distinct_capped": fs.capped,
        }
        if fs.lengths:
            d["len"] = {"min": min(fs.lengths), "p50": percentile(fs.lengths, 0.5),
                        "p90": percentile(fs.lengths, 0.9), "max": max(fs.lengths)}
        if fs.numbers:
            d["num"] = {"min": min(fs.numbers), "p50": percentile(fs.numbers, 0.5), "max": max(fs.numbers)}
        if fs.values and not fs.capped and len(fs.values) <= TOP_VALUES_IF_DISTINCT_LE:
            d["top"] = [[str(k)[:60], c] for k, c in fs.values.most_common(8)]
        # duplicates only matter for mostly-unique fields (ids, urls, titles), not enums
        if fs.per_record_max == 1 and fs.value_records and len(fs.value_records) > 0.5 * fs.records_with:
            shared = [recs for recs in fs.value_records.values() if len(recs) > 1]
            d["dup_values"] = len(shared)
            d["records_sharing_value"] = sum(len(r) for r in shared)
            if shared:
                d["dup_examples"] = [str(k)[:80] for k, recs in fs.value_records.items() if len(recs) > 1][:3]
        d["unique_key_candidate"] = bool(
            fs.per_record_max == 1 and fs.records_with == n and fs.nulls == 0 and not fs.capped
            and d.get("distinct") == n and set(fs.types) <= {"string", "number"})
        out[path] = d
    dup_records = sum(c - 1 for c in record_hashes.values() if c > 1)
    return {"records": n, "duplicate_records": dup_records, "fields": out}


def load_records(path):
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".jsonl":
        recs, bad = [], 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    bad += 1
        return recs, {"malformed_lines": bad}
    if suf == ".json":
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        return (obj if isinstance(obj, list) else [obj]), {"top_level": _type(obj)}
    if suf in (".csv", ".tsv"):
        with open(p, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f, delimiter="\t" if suf == ".tsv" else ","))
        return rows, {}
    return None, {}


def profile_path(path, max_depth):
    p = Path(path)
    results = []
    if p.is_dir():
        groups = defaultdict(list)
        for f in sorted(p.rglob("*")):
            if f.is_file():
                groups[f.suffix.lower()].append(f)
        for suf, files in sorted(groups.items()):
            size = sum(f.stat().st_size for f in files)
            entry = {"path": f"{p}/*{suf}", "files": len(files), "bytes": size}
            if suf == ".json" and len(files) > 1:
                recs, errors = [], 0
                for f in files:
                    try:
                        with open(f, encoding="utf-8") as fh:
                            recs.append(json.load(fh))
                    except ValueError:
                        errors += 1
                entry.update(kind="json-collection", malformed_files=errors, **profile_records(recs, max_depth))
            elif suf in (".jsonl", ".json", ".csv", ".tsv"):
                for f in files:
                    results.extend(profile_path(f, max_depth))
                continue
            results.append(entry)
        return results
    entry = {"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
    recs, extra = load_records(p)
    entry.update(extra)
    if recs is not None:
        entry.update(kind="records", **profile_records(recs, max_depth))
    else:
        entry["kind"] = "opaque"
    return [entry]


def _cell(v, n=60):
    return str(v).replace("\n", " ").replace("|", "/")[:n]


def _fmt_stats(d):
    parts = []
    if "len" in d:
        L = d["len"]
        parts.append(f"len {L['min']}/{L['p50']:.0f}/{L['p90']:.0f}/{L['max']}")
    if "num" in d:
        N = d["num"]
        parts.append(f"num {N['min']}/{N['p50']}/{N['max']}")
    return " ".join(parts)


def render_md(results, max_rows):
    lines = []
    for e in results:
        head = f"## {e['path']}  ({e.get('kind')}, {e.get('bytes', 0):,} bytes"
        if "files" in e:
            head += f", {e['files']} files"
        if "records" in e:
            head += f", {e['records']} records, dup_records={e.get('duplicate_records', 0)}"
        if e.get("malformed_lines") or e.get("malformed_files"):
            head += f", MALFORMED={e.get('malformed_lines') or e.get('malformed_files')}"
        lines.append(head + ")")
        fields = e.get("fields")
        if not fields:
            continue
        lines.append("| path | rec% | null% | types | distinct | len/num min/p50/p90/max | dups | notes |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, (path, d) in enumerate(fields.items()):
            if i >= max_rows:
                lines.append(f"| ... {len(fields) - max_rows} more paths in the .json |")
                break
            types = ",".join(f"{t}:{c}" for t, c in d["types"].items()) if len(d["types"]) > 1 else next(iter(d["types"]))
            distinct = f">{DISTINCT_CAP}" if d["distinct_capped"] else (d["distinct"] or "")
            dups = d.get("records_sharing_value", "")
            notes = []
            if d.get("unique_key_candidate"):
                notes.append("KEY?")
            if d.get("top"):
                notes.append("top: " + ", ".join(f"{_cell(v, 30)}({c})" for v, c in d["top"][:4]))
            if d.get("dup_examples"):
                notes.append("dup e.g. " + " ; ".join(_cell(x, 40) for x in d["dup_examples"][:2]))
            lines.append(f"| {path} | {d['records_with_pct']} | {d['null_pct']} | {types} | {distinct} | "
                         f"{_fmt_stats(d)} | {dups} | {'; '.join(notes)[:160]} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", help="output prefix, e.g. WS/audit/profile")
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=60, help="field rows per file in the compact view")
    a = ap.parse_args()
    results = []
    for p in a.paths:
        results.extend(profile_path(p, a.max_depth))
    md = render_md(results, a.max_rows)
    if a.out:
        write_json(a.out + ".json", results)
        Path(a.out + ".md").write_text(md + "\n", encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
