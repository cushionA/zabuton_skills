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
  reference_coverage(units, split) -> {"covered", "total", "unresolved"} item counts
      Every expected item needs a resolved reference or explicit exclusion, on dev and holdout.
  trivial(units, split) -> outputs of a trivial baseline (majority class, first candidate...)

This template scores item-level labels against eval/reference/<split>*.jsonl, where the
pipeline output is {"items": [{"key": ..., "label": ...}]}. Adapt it to the task.
"""
import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(json.loads((WS / "charter.json").read_text(encoding="utf-8"))["skill_dir"]) / "scripts"))
from common import json_label_equal, label_matches, load_reference  # noqa: E402


def expected_keys(unit):
    # TODO: the items every unit must answer (taken from the unit itself, not from the reference)
    return [str(k) for k in unit["input"].get("item_keys", [])]


def _item_keys(unit):
    # Expectations must come from the input, never from what reference runs happened to emit.
    return [f"{unit['id']}|{k}" for k in expected_keys(unit)]


def _resolved_reference(entry):
    """A key alone is not a resolved answer; null itself can be a valid label."""
    if not isinstance(entry, dict):
        return False
    if entry.get("exclude") is True:
        return True
    if entry.get("exclude"):
        return False  # Exclusions must be explicit JSON true, not a truthy string or number.
    if "acceptable" in entry:
        # label_matches gives acceptable precedence over label when both are present.
        return (isinstance(entry["acceptable"], list) and bool(entry["acceptable"])
                and all(json_label_equal(label, label) for label in entry["acceptable"]))
    return "label" in entry and json_label_equal(entry["label"], entry["label"])


def _parse_output(unit, output):
    """Validate before flattening, so duplicate or malformed rows cannot win by overwrite.

    Missing rows are handled by score() against the resolved reference. A partial,
    otherwise well-formed output remains usable for reference disagreement merging.
    """
    if not isinstance(output, dict):
        return {}, ["output must be an object"]
    if not isinstance(output.get("items"), list):
        return {}, ["items must be an array"]
    expected = set(expected_keys(unit))
    pred, seen, errors = {}, set(), []
    for index, item in enumerate(output["items"]):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be an object")
            continue
        if "key" not in item or not isinstance(item["key"], str):
            errors.append(f"items[{index}].key must be a string")
            continue
        key = item["key"]
        if key in seen:
            errors.append(f"duplicate key {key!r}")
        seen.add(key)
        if key not in expected:
            errors.append(f"unknown key {key!r}")
        if "label" not in item:
            errors.append(f"items[{index}].label is required")
            continue
        if not json_label_equal(item["label"], item["label"]):
            errors.append(f"items[{index}].label must be a JSON value")
            continue
        pred[f"{unit['id']}|{key}"] = item["label"]
    return pred, errors


def flatten(unit, output):
    # Reference generation must refuse invalid output rather than silently
    # agreeing on whichever duplicate happened to appear last.
    pred, errors = _parse_output(unit, output)
    if errors:
        raise ValueError(f"{unit['id']}: invalid output: " + "; ".join(errors))
    return pred


def score(units, outputs, split):
    ref = load_reference(WS, split)
    ref.pop("__unresolved__", None)
    items, gate = [], []
    if not isinstance(outputs, dict):
        gate.append("outputs must map unit ids to output objects")
        outputs = {}
    elif set(outputs) - {u["id"] for u in units}:
        gate.append("outputs contain unknown unit ids")
        outputs = {}
    for u in units:
        out = outputs.get(u["id"])
        pred, errors = _parse_output(u, out)
        if errors:
            gate.extend(f"{u['id']}: {error}" for error in errors)
            pred = {}  # An invalid unit must not receive partial or full success.
        keys = _item_keys(u)
        for k in keys:
            entry = ref.get(k)
            if _resolved_reference(entry) and entry.get("exclude") is True:
                continue
            p = pred.get(k)
            if not _resolved_reference(entry):
                kind = "missing" if entry is None else "invalid"
                gate.append(f"{u['id']}: {kind} reference for {k}")
                tags = [f"{kind}-reference"] + (["missing"] if k not in pred else [])
                items.append({"id": k, "unit": u["id"], "score": 0.0, "tags": tags, "pred": p, "ref": None})
                continue
            ok = label_matches(p, entry) if k in pred else False
            tags = [] if ok else (["missing"] if k not in pred else ["wrong"])
            if errors:
                tags.append("invalid-output")
            elif k not in pred:
                gate.append(f"{u['id']}: missing output for {k}")
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
            if k.startswith(f"{u['id']}|") and _resolved_reference(e) and e.get("exclude") is not True:
                label = e["acceptable"][0] if "acceptable" in e else e["label"]
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
    keys = [k for u in units for k in _item_keys(u)]
    missing = [k for k in keys if k not in ref]
    invalid = [k for k in keys if k in ref and not _resolved_reference(ref[k])]
    excluded = sum(1 for k in keys if k in ref and ref[k].get("exclude") is True)
    return {"covered": len(keys) - len(missing) - len(invalid), "total": len(keys), "unresolved": len(unresolved),
            "excluded": excluded, "missing_sample": missing[:10], "invalid_sample": invalid[:10]}
