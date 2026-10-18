"""Data quality gate: PASS / REVIEW / STOP before any experiment runs.

Runs the workspace's task-specific checks (WS/audit/checks.py) plus built-in checks,
records input-data hashes, and writes WS/audit/quality_gate.json.

  python quality_gate.py WS                     # evaluate; exit 0 PASS, 2 REVIEW, 3 STOP
  python quality_gate.py WS --accept-review "user approved on <date>: <reason>"
  python quality_gate.py WS --verify            # only re-check data hashes (exit 4 if changed)

checks.py must define `run_checks(ctx) -> list[dict]`; each finding is
  {"id", "severity": critical|high|medium|low|info, "title", "evidence", "affected",
   "known": bool, "recommendation"}
`known=True` marks an issue the task brief already states (e.g. "no labels are
included"); it is reported as a known limitation and does not block. Unknown
critical -> STOP, unknown high -> REVIEW. Only the user may accept a REVIEW.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import hash_paths, now_iso, read_json, sha256_file, write_json  # noqa: E402

SEVERITIES = ("critical", "high", "medium", "low", "info")


class Ctx:
    def __init__(self, ws, charter):
        self.ws = Path(ws)
        self.charter = charter
        self.inputs = [Path(p) for p in charter.get("data", {}).get("inputs", [])]

    def finding(self, id, severity, title, evidence="", affected=None, known=False, recommendation=""):
        assert severity in SEVERITIES, severity
        return {"id": id, "severity": severity, "title": title, "evidence": evidence,
                "affected": affected, "known": known, "recommendation": recommendation}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def builtin_checks(ctx):
    out = []
    if not ctx.inputs:
        out.append(ctx.finding("inputs-missing", "critical", "charter.data.inputs is empty",
                               recommendation="list the data files the pipeline reads"))
    for p in ctx.inputs:
        if not p.exists():
            out.append(ctx.finding("input-not-found", "critical", f"input not found: {p}"))
    if not (ctx.ws / "audit" / "profile.md").exists():
        out.append(ctx.finding("profile-missing", "high", "audit/profile.md missing",
                               recommendation="run profile_data.py --out WS/audit/profile"))
    if not (ctx.ws / "audit" / "data_audit.md").exists():
        out.append(ctx.finding("audit-notes-missing", "high", "audit/data_audit.md missing",
                               recommendation="write the audit narrative (what was checked, what was found)"))
    return out


def evaluate(ws):
    ws = Path(ws)
    charter = read_json(ws / "charter.json", {})
    ctx = Ctx(ws, charter)
    findings = builtin_checks(ctx)
    checks_path = ws / "audit" / "checks.py"
    if checks_path.exists():
        try:
            findings += list(load_module(checks_path, "audit_checks").run_checks(ctx) or [])
        except Exception as e:  # a crashing check is itself a blocking finding
            findings.append(ctx.finding("checks-crashed", "critical", f"checks.py raised {type(e).__name__}: {e}"))
    else:
        findings.append(ctx.finding("checks-missing", "high", "audit/checks.py missing"))
    blocking = [f for f in findings if not f.get("known")]
    if any(f["severity"] == "critical" for f in blocking):
        status = "STOP"
    elif any(f["severity"] == "high" for f in blocking):
        status = "REVIEW"
    else:
        status = "PASS"
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    existing = [p for p in ctx.inputs if p.exists()]
    gate = {"status": status, "created": now_iso(), "counts": counts, "findings": findings,
            "data_hashes": hash_paths(existing), "checks_sha": sha256_file(checks_path) if checks_path.exists() else None,
            "waiver": None}
    return gate


def verify_hashes(ws):
    """Return list of changed/missing data files versus the recorded gate."""
    gate = read_json(Path(ws) / "audit" / "quality_gate.json")
    if not gate:
        return ["quality_gate.json missing"]
    changed = []
    for path, h in gate.get("data_hashes", {}).items():
        if not os.path.exists(path):
            changed.append(f"missing: {path}")
        elif sha256_file(path) != h:
            changed.append(f"changed: {path}")
    return changed


def gate_allows_runs(ws):
    """(allowed, reason) - used by run_experiment.py."""
    gate = read_json(Path(ws) / "audit" / "quality_gate.json")
    if not gate:
        return False, "no audit/quality_gate.json - run the data audit and quality_gate.py first"
    if gate["status"] == "STOP":
        return False, "quality gate is STOP - report the findings to the user; do not run experiments"
    if gate["status"] == "REVIEW" and not gate.get("waiver"):
        return False, "quality gate is REVIEW - report to the user; only they can accept it (--accept-review)"
    changed = verify_hashes(ws)
    if changed:
        return False, "input data changed since the gate: " + "; ".join(changed[:5])
    return True, gate["status"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--accept-review", metavar="REASON", help="record the USER's acceptance of a REVIEW gate")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    path = Path(a.ws) / "audit" / "quality_gate.json"
    if a.verify:
        changed = verify_hashes(a.ws)
        print("data unchanged" if not changed else "\n".join(changed))
        sys.exit(4 if changed else 0)
    if a.accept_review:
        gate = read_json(path)
        if not gate or gate["status"] != "REVIEW":
            sys.exit("no REVIEW gate to accept")
        gate["waiver"] = {"reason": a.accept_review, "at": now_iso()}
        write_json(path, gate)
        print("REVIEW accepted and recorded")
        return
    gate = evaluate(a.ws)
    write_json(path, gate)
    print(f"GATE {gate['status']}  counts={json.dumps(gate['counts'])}")
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for f in sorted(gate["findings"], key=lambda f: (f.get("known", False), order[f["severity"]])):
        flag = "known" if f.get("known") else f["severity"].upper()
        print(f"- [{flag}] {f['id']}: {f['title']}"[:200])
    sys.exit({"PASS": 0, "REVIEW": 2, "STOP": 3}[gate["status"]])


if __name__ == "__main__":
    main()
