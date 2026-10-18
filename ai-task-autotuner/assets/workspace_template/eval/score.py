"""Frozen scorer. Deterministic checks first; an LLM judge only for what code cannot decide.

score(units, outputs, split) -> {
    "primary": float,                 # headline metric (charter.primary)
    "metrics": {name: float},         # guards and diagnostics (precision, false-accept rate, coverage...)
    "items": [{"id", "unit", "score": 0..1, "tags": [...], "pred", "ref"}],
    "gate_failures": [str],           # hard-gate violations: schema, missing/extra/fabricated keys
}
outputs maps unit id -> pipeline output, or None when the unit failed (scored, never dropped).
Item ids must be stable strings across runs (paired comparisons use them).

Optional hooks used by validate_eval.py / reference.py:
  flatten(unit, output) -> {item_key: label}     (reference merging)
  oracle(units, split) -> outputs that must reach the ceiling
  corrupt(output, rng) -> output with injected errors (score must drop)
  reference_coverage(units, split) -> {"covered", "total", "unresolved"}
  trivial(units, split) -> outputs of a trivial baseline (majority class, first candidate...)

This template scores item-level labels against eval/reference/<split>*.jsonl, where the
pipeline output is {"items": [{"key": ..., "label": ...}]}. Adapt it to the task.
"""
import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(json.loads((WS / "charter.json").read_text(encoding="utf-8"))["skill_dir"]) / "scripts"))
from common import label_matches, load_reference  # noqa: E402


def expected_keys(unit):
    # TODO: the items every unit must answer (taken from the unit itself, not from the reference)
    return [str(k) for k in unit["input"].get("item_keys", [])]


def flatten(unit, output):
    if not isinstance(output, dict):
        return {}
    return {f"{unit['id']}|{it.get('key')}": it.get("label") for it in output.get("items", []) if isinstance(it, dict)}


def score(units, outputs, split):
    ref = load_reference(WS, split)
    ref.pop("__unresolved__", None)
    items, gate = [], []
    for u in units:
        out = outputs.get(u["id"])
        pred = flatten(u, out) if out is not None else {}
        keys = [f"{u['id']}|{k}" for k in expected_keys(u)] or [k for k in ref if k.startswith(f"{u['id']}|")]
        extra = set(pred) - set(keys)
        if out is not None and extra:
            gate.append(f"{u['id']}: {len(extra)} unknown keys")
        for k in keys:
            entry = ref.get(k)
            if entry is None or entry.get("exclude"):
                continue
            p = pred.get(k)
            ok = label_matches(p, entry) if k in pred else False
            tags = [] if ok else (["missing"] if k not in pred else ["wrong"])
            if not ok:
                tags.append(f"ref:{entry.get('label', 'acceptable-set')}")
            items.append({"id": k, "unit": u["id"], "score": 1.0 if ok else 0.0, "tags": tags,
                          "pred": p, "ref": entry.get("label", entry.get("acceptable"))})
    n = len(items)
    acc = sum(i["score"] for i in items) / n if n else 0.0
    answered = sum(1 for i in items if "missing" not in i["tags"]) / n if n else 0.0
    return {"primary": acc, "metrics": {"accuracy": acc, "answered": answered, "n_items": n},
            "items": items, "gate_failures": gate}


def oracle(units, split):
    ref = load_reference(WS, split)
    ref.pop("__unresolved__", None)
    out = {}
    for u in units:
        its = []
        for k, e in ref.items():
            if k.startswith(f"{u['id']}|") and not e.get("exclude"):
                label = e["label"] if "label" in e else e["acceptable"][0]
                its.append({"key": k.split("|", 1)[1], "label": label})
        out[u["id"]] = {"items": its}
    return out


def corrupt(output, rng):
    its = [dict(i) for i in output.get("items", [])]
    labels = [i["label"] for i in its]
    rng.shuffle(labels)
    for i, lab in zip(its, labels):
        i["label"] = lab
    if its:
        its[0]["label"] = "__corrupted__"
    return {"items": its}


def reference_coverage(units, split):
    ref = load_reference(WS, split)
    unresolved = ref.pop("__unresolved__", [])
    covered = sum(1 for u in units if any(k.startswith(f"{u['id']}|") for k in ref))
    return {"covered": covered, "total": len(units), "unresolved": len(unresolved)}
