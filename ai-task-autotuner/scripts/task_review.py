"""Record an independent review of the task design before measured runs.

  python task_review.py prepare WS --candidate c000-baseline
  python task_review.py record WS WS/review/task_review.result.json
  python task_review.py status WS

The reviewer writes the result and a report under WS/review/. This script checks
completeness and freshness; it does not call a model or prove reviewer independence.
Routine optimization candidates do not change the reviewed baseline fingerprint.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import append_jsonl, digest, hash_paths, now_iso, read_json, sha256_file, write_json  # noqa: E402
from quality_gate import gate_allows_runs  # noqa: E402
from design_contracts import CONTRACT_PATH, load as load_contracts  # noqa: E402

CHECKS = (
    "goal_alignment", "input_sufficiency", "deterministic_safety",
    "output_contract", "evaluation_coverage", "leakage_and_difficulty",
    "assumptions_and_stress", "conditional_test_contracts", "rewrite_and_prompt_design",
)
DESIGN_FILES = (
    "audit/data_audit.md", "experiment_plan.md", "eval/eval_spec.md",
    "eval/dataset.py", "eval/score.py", "eval/splits.json",
    CONTRACT_PATH,
)
CHARTER_FIELDS = ("task", "goal", "brief", "user_request", "may_change", "off_limits", "data", "primary", "guards",
                  "efficiency_metric")


def _snapshot(ws, candidate, *, charter=None, gate=None):
    ws = Path(ws)
    if not isinstance(candidate, str) or not candidate or candidate in (".", "..") or any(
            c in candidate for c in ("/", "\\")):
        raise ValueError("baseline candidate must be a name under candidates/")
    cand = ws / "candidates" / candidate
    required = [ws / rel for rel in DESIGN_FILES] + [cand / "pipeline.py", ws / "charter.json"]
    missing = [str(p.relative_to(ws)) for p in required if not p.is_file()]
    if missing:
        raise ValueError("finish the draft design first: " + ", ".join(missing))
    load_contracts(ws)
    ch = read_json(ws / "charter.json") if charter is None else charter
    gate = read_json(ws / "audit" / "quality_gate.json", {}) if gate is None else gate
    # Definitions and synthetic/dev fixtures are reviewed; append-only outcomes
    # live outside probes/ so execution does not change the approved test plan.
    probes = ws / "review" / "probes"
    if probes.exists() and not probes.is_dir():
        raise ValueError("review/probes must be a directory of probe definitions/fixtures")
    files = [ws / rel for rel in DESIGN_FILES] + [cand, probes]
    if ch.get("brief"):
        brief = Path(ch["brief"])
        brief = brief if brief.is_absolute() else ws / brief
        if brief.is_file():
            files.append(brief)
    return {
        "candidate": candidate,
        "charter": {k: ch.get(k) for k in CHARTER_FIELDS},
        "audited_data_hashes": gate.get("data_hashes", {}),
        "files": hash_paths(files, base=ws),
    }


def prepare_review(ws, candidate="c000-baseline"):
    ws = Path(ws)
    allowed, reason = gate_allows_runs(ws)
    if not allowed:
        raise ValueError("complete the data audit first: " + reason)
    snapshot = _snapshot(ws, candidate)
    request = {
        "version": 2, "created": now_iso(), "design_hash": digest(snapshot),
        "snapshot": snapshot, "required_checks": list(CHECKS),
        "report": "review/task_review.md",
    }
    write_json(ws / "review" / "task_review_request.json", request)
    return request


def _report_path(ws, relative):
    if not isinstance(relative, str) or not relative.strip() or Path(relative).is_absolute():
        raise ValueError("report must be a relative path under review/")
    path = (Path(ws) / relative).resolve()
    if not path.is_relative_to((Path(ws) / "review").resolve()):
        raise ValueError("report must stay under review/")
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        raise ValueError("the reviewer report is missing or empty")
    return path


def _validate_result(result):
    if not isinstance(result, dict):
        raise ValueError("review result must be an object")
    reviewer = result.get("reviewer")
    if (not isinstance(reviewer, dict) or not isinstance(reviewer.get("id"), str)
            or not reviewer["id"].strip()
            or reviewer.get("kind") not in ("subagent", "isolated_model", "human")):
        raise ValueError("record the actual reviewer id and kind")
    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECKS):
        raise ValueError(f"review must cover all {len(CHECKS)} checks exactly once; update legacy reviews")
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("id") not in CHECKS or check["id"] in seen:
            raise ValueError("unknown or duplicate review check")
        seen.add(check["id"])
        if check.get("status") not in ("PASS", "REVISE"):
            raise ValueError("each check needs PASS or REVISE")
        if not isinstance(check.get("evidence"), str) or not check["evidence"].strip():
            raise ValueError("each check needs concrete evidence")
    return "PASS" if all(c["status"] == "PASS" for c in checks) else "REVISE"


def record_review(ws, result_path):
    ws = Path(ws)
    request = read_json(ws / "review" / "task_review_request.json")
    if not isinstance(request, dict) or not isinstance(request.get("snapshot"), dict):
        raise ValueError("prepare a task review request first")
    snapshot = _snapshot(ws, request["snapshot"].get("candidate"))
    current = digest(snapshot)
    if request.get("design_hash") != current:
        raise ValueError("design changed during review; prepare a fresh request")
    result = read_json(result_path)
    status = _validate_result(result)
    if result.get("design_hash") != current:
        raise ValueError("review result is for a different design")
    report = _report_path(ws, result.get("report"))
    stored = {
        "version": 2, "created": now_iso(), "status": status,
        "design_hash": current, "snapshot": snapshot,
        "reviewer": result["reviewer"], "checks": result["checks"],
        "report": result["report"], "report_sha256": sha256_file(report),
    }
    write_json(ws / "review" / "task_review.json", stored)
    append_jsonl(ws / "review" / "task_review_history.jsonl", {
        k: stored[k] for k in ("created", "status", "design_hash", "reviewer", "report", "report_sha256")
    })
    return stored


def review_state(ws):
    ws = Path(ws)
    try:
        review = read_json(ws / "review" / "task_review.json")
        if not review:
            return "PENDING", "prepare and complete an independent task review before measured runs"
        status = _validate_result(review)
        if review.get("status") != status:
            return "PENDING", "review status disagrees with its checks; record the review again"
        snapshot = review.get("snapshot")
        if not isinstance(snapshot, dict):
            return "PENDING", "review snapshot is missing; prepare a new task review"
        current = digest(_snapshot(ws, snapshot.get("candidate")))
        if current != review.get("design_hash") or digest(snapshot) != current:
            return "STALE", "task design changed; review the changes and affected scope before measured runs"
        report = _report_path(ws, review.get("report"))
        if sha256_file(report) != review.get("report_sha256"):
            return "STALE", "review report changed; record the updated review"
        if status == "REVISE":
            failed = [c["id"] for c in review["checks"] if c["status"] == "REVISE"]
            return "REVISE", "fix the design and re-review: " + ", ".join(failed)
        return "PASS", "current task design has a complete recorded review"
    except (ValueError, OSError, TypeError, KeyError) as e:
        return "PENDING", f"invalid task review: {e}"


def task_review_allows_runs(ws):
    status, reason = review_state(ws)
    return status == "PASS", f"task review {status}: {reason}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("ws")
    prep.add_argument("--candidate", default="c000-baseline")
    record = sub.add_parser("record")
    record.add_argument("ws")
    record.add_argument("result")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("ws")
    a = ap.parse_args()
    try:
        if a.command == "prepare":
            request = prepare_review(a.ws, a.candidate)
            print(f"TASK REVIEW REQUEST {request['design_hash']}: {Path(a.ws) / 'review/task_review_request.json'}")
            return
        if a.command == "record":
            review = record_review(a.ws, a.result)
            print(f"TASK REVIEW {review['status']}: {review['report']}")
            sys.exit(0 if review["status"] == "PASS" else 2)
        state, reason = review_state(a.ws)
        print(f"TASK REVIEW {state}: {reason}")
        sys.exit({"PASS": 0, "REVISE": 2, "STALE": 4, "PENDING": 5}[state])
    except (ValueError, OSError, TypeError, KeyError) as e:
        ap.exit(2, f"TASK REVIEW REFUSED: {e}\n")


if __name__ == "__main__":
    main()
