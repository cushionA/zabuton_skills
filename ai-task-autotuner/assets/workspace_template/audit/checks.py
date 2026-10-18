"""Task-specific data checks for quality_gate.py.

run_checks(ctx) returns findings made with ctx.finding(id, severity, title, evidence,
affected, known, recommendation). Compute every number here; do not eyeball.

Severity guide (be strict - a measurement built on broken data is worse than none):
  critical  the task or its scoring cannot be defined on this data
            (unreadable input, missing required fields, contradictory labels,
             leakage between splits that cannot be removed)
  high      results would be misleading unless a human decides
            (labels of doubtful provenance, severe skew, duplicated units that
             would double count, many units that cannot be scored)
  medium    affects interpretation; record it and carry it into the report
  low/info  facts worth knowing (size, distributions)
known=True: the user's brief already states it (e.g. "labels are not included");
it is reported as a known limitation and does not block. Never mark something known
just to get past the gate.
"""
import json
from collections import Counter


def _records(path):
    with open(path, encoding="utf-8") as f:
        if str(path).endswith(".jsonl"):
            return [json.loads(line) for line in f if line.strip()]
        obj = json.load(f)
        return obj if isinstance(obj, list) else [obj]


def run_checks(ctx):
    out = []
    recs = _records(ctx.inputs[0])
    out.append(ctx.finding("size", "info", f"{len(recs)} records in {ctx.inputs[0].name}"))

    # Example: unique id field
    ids = Counter(str(r.get("id")) for r in recs)
    dups = [k for k, c in ids.items() if c > 1]
    if dups:
        out.append(ctx.finding("dup-ids", "critical", f"{len(dups)} duplicated ids", evidence=str(dups[:5]),
                               affected=len(dups), recommendation="dedupe or re-key before scoring"))

    # TODO: required fields present / types; label consistency (same input, different label);
    # near-duplicate units (same title/url) that must share a split; units that cannot be scored;
    # distribution of difficulty-relevant features (sizes, asymmetries, missing text); and how many
    # units a trivial rule would already solve (tells whether LLM optimisation is worth it).
    return out
