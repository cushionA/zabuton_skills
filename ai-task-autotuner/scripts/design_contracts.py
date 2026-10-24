"""Validate a task's conditional test and rewrite plan; never execute its checks.

  python design_contracts.py WS

Completeness is not correctness or test execution. An independent reviewer must
challenge the conditions, oracles and coverage. Store subsequent observations in
review/contract_results.jsonl, separate from these frozen design definitions.
"""
import argparse
import json
from pathlib import Path

CONTRACT_PATH = "review/design_contracts.json"
INPUT_FIELDS = (
    "business_unit", "processing_unit", "call_unit", "valid_domain",
    "invalid_input_policy", "unsupported_input_policy", "size_model",
)
PATTERNS = ("boundary", "growth", "relations", "skew", "interaction", "prompt_context")
KINDS = ("example", "property", "metamorphic", "scale", "fault")
INPUT_CLASSES = ("valid", "invalid", "unsupported")
CONTRACT_FIELDS = (
    "precondition", "transformation", "expected_relation", "oracle", "tolerance",
    "failure_action", "check",
)
REWRITE_FIELDS = ("change", "applicability", "preserved_semantics", "risks")


def _object(value, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _text(value, location):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be nonempty text")
    return value


def _records(value, location, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{location} must be {'a nonempty' if nonempty else 'a'} list")
    seen = set()
    for index, record in enumerate(value):
        where = f"{location}[{index}]"
        _object(record, where)
        key = _text(record.get("id"), f"{where}.id")
        if key != key.strip() or key in seen:
            raise ValueError(f"{where}.id is padded or duplicate: {key!r}")
        seen.add(key)
    return seen


def _references(value, known, location, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{location} must be {'a nonempty' if nonempty else 'a'} list of ids")
    seen = set()
    for key in value:
        _text(key, location)
        if key not in known or key in seen:
            raise ValueError(f"{location} has an unknown or duplicate id: {key!r}")
        seen.add(key)


def validate(plan):
    """Return counts for a structurally complete plan or raise ValueError.

    Do not infer semantic truth, successful execution, or reviewer independence
    from a well-formed plan. Text describes task-specific requirements, not code
    to evaluate inside this validator.
    """
    _object(plan, "design contracts")
    if type(plan.get("version")) is not int or plan["version"] != 1:
        raise ValueError("design contracts version must be 1")
    model = _object(plan.get("input_model"), "input_model")
    for field in INPUT_FIELDS:
        _text(model.get(field), f"input_model.{field}")
    assumptions = _records(plan.get("assumptions"), "assumptions")
    for row in plan["assumptions"]:
        for field in ("statement", "basis"):
            _text(row.get(field), f"assumption {row['id']}.{field}")
    contracts = _records(plan.get("contracts"), "contracts")
    for row in plan["contracts"]:
        where = f"contract {row['id']}"
        if row.get("kind") not in KINDS:
            raise ValueError(f"{where}.kind must be one of {', '.join(KINDS)}")
        if row.get("input_class") not in INPUT_CLASSES:
            raise ValueError(f"{where}.input_class must be valid, invalid or unsupported")
        _references(row.get("assumptions"), assumptions, f"{where}.assumptions", nonempty=False)
        for field in CONTRACT_FIELDS:
            _text(row.get(field), f"{where}.{field}")
        # Execution results belong elsewhere: otherwise running a planned test
        # would change the reviewed design, or a plan might masquerade as proof.
        if "status" in row or "result" in row:
            raise ValueError(f"{where}: put execution status/results in review/contract_results.jsonl")
    if not any(row["input_class"] == "valid" for row in plan["contracts"]):
        raise ValueError("contracts must include a valid-input case, not only rejection tests")
    coverage = _object(plan.get("coverage"), "coverage")
    if set(coverage) != set(PATTERNS):
        raise ValueError(f"coverage must address exactly: {', '.join(PATTERNS)}")
    covered = set()
    for pattern in PATTERNS:
        row = _object(coverage[pattern], f"coverage.{pattern}")
        if set(row) == {"contracts"}:
            _references(row["contracts"], contracts, f"coverage.{pattern}.contracts")
            covered.update(row["contracts"])
        elif set(row) == {"not_applicable"}:
            _text(row["not_applicable"], f"coverage.{pattern}.not_applicable")
        else:
            raise ValueError(f"coverage.{pattern} needs contracts OR a not_applicable reason")
    if not covered:
        raise ValueError("coverage cannot mark every pattern not_applicable")
    rewrites = _records(plan.get("rewrites"), "rewrites", nonempty=False)
    if not rewrites:
        _text(plan.get("no_rewrite_reason"), "no_rewrite_reason")
    elif plan.get("no_rewrite_reason") not in (None, ""):
        raise ValueError("use rewrite alternatives OR no_rewrite_reason, not both")
    for row in plan["rewrites"]:
        for field in REWRITE_FIELDS:
            _text(row.get(field), f"rewrite {row['id']}.{field}")
        _references(row.get("validation_contracts"), contracts, f"rewrite {row['id']}.validation_contracts")
    return {"assumptions": len(assumptions), "contracts": len(contracts), "rewrites": len(rewrites),
            "patterns_with_checks": sum("contracts" in row for row in coverage.values())}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key in design contracts: {key}")
        result[key] = value
    return result


def load(ws):
    path = Path(ws) / CONTRACT_PATH
    try:
        plan = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except FileNotFoundError as exc:
        raise ValueError(f"finish {CONTRACT_PATH}; init_workspace.py WS --migrate seeds a blank template") from exc
    validate(plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ws")
    args = parser.parse_args()
    try:
        counts = validate(load(args.ws))
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"DESIGN CONTRACTS INCOMPLETE: {exc}\n")
    print("DESIGN CONTRACTS COMPLETE: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    print("Structure checked only; tests were not run and semantic validity needs independent review.")


if __name__ == "__main__":
    main()
