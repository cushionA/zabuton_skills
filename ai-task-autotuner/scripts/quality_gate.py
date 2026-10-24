"""Data quality gate: PASS / REVIEW / STOP before any experiment runs.

Runs the workspace's task-specific checks (WS/audit/checks.py) plus built-in checks,
records input-data hashes, and writes WS/audit/quality_gate.json.

  python quality_gate.py WS                     # evaluate; exit 0 PASS, 2 REVIEW, 3 STOP, 5 INCOMPLETE
  python quality_gate.py WS --accept-review "user approved on <date>: <reason>"
  python quality_gate.py WS --verify            # verify source scope, bytes and audit evidence (exit 4 if changed)

checks.py must define `run_checks(ctx) -> list[dict]`; each finding is
  {"id", "severity": critical|high|medium|low|info, "title", "evidence", "affected",
   "known": bool, "recommendation"}
`known=True` marks an issue the task brief already states (e.g. "no labels are
included"); it is reported as a known limitation and does not block. A user's general
acceptance of a limitation does not cover specific defects found later - those are new
findings. Unknown critical -> STOP, unknown high -> REVIEW. Only the user may accept a REVIEW.
A malformed finding (unlisted severity such as "High", missing id/title, non-boolean known)
becomes a critical `checks-invalid-finding`: a typo must never let the gate pass.

Close-read verdicts go to WS/audit/close_read.jsonl, one line per unit read:
  {"id": "<unit id>", "verdict": "ok|hard|ambiguous|defect", "evidence": "...", "severity": "high"}
`defect` lines become a finding (severity from the lines, default high; an unlisted severity
counts as critical); `ambiguous` lines medium.
Missing procedure artifacts (profile, data_audit.md, checks.py, close_read.jsonl) give
INCOMPLETE: finish the audit, do not report to the user.
REVIEW/STOP pause the time-limit clock (ledger.jsonl); --accept-review resumes it.
"""
import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import append_jsonl, now_iso, read_json, read_jsonl, sha256_file, sha256_text, write_json  # noqa: E402

SEVERITIES = ("critical", "high", "medium", "low", "info")
STATUS_EXIT = {"PASS": 0, "REVIEW": 2, "STOP": 3, "INCOMPLETE": 5}
AUDIT_EVIDENCE = ("audit/profile.md", "audit/data_audit.md", "audit/close_read.jsonl", "audit/checks.py")


def _source_path(ws, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("data sources must be nonempty path strings")
    path = Path(value)
    try:
        return (path if path.is_absolute() else Path(ws) / path).resolve()
    except RuntimeError as e:
        raise ValueError(f"cannot resolve source path: {value}") from e


def _source_paths(ws, charter, role):
    data = charter.get("data", {})
    if not isinstance(data, dict) or not isinstance(data.get(role, []), list):
        raise ValueError(f"charter.data.{role} must be a list of paths")
    return [_source_path(ws, value) for value in data.get(role, [])]


def _directory_snapshot(root):
    """Include every file and empty directory, following links without cycles."""
    files, directories = {}, []

    def visit(path, prefix, ancestors):
        canonical = path.resolve()
        if canonical in ancestors:
            raise ValueError("cyclic source directory symlink")
        ancestors = ancestors | {canonical}
        for child in sorted(path.iterdir(), key=lambda p: p.name):
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                directories.append(relative)
                visit(child, relative, ancestors)
            elif child.is_file():
                files[relative] = sha256_file(child)
            else:
                # Broken links, sockets and pipes cannot be silently absent
                # from an audit of a declared directory.
                raise ValueError(f"unsupported or missing source entry: {relative}")

    visit(root, "", set())
    return {"files": files, "directories": directories}


def source_manifest(ws, charter=None):
    """Ordered, role-aware source roots and their complete current contents.

    Relative charter paths are relative to WS. Root aliases resolve to their
    canonical path; order and duplicate declarations are retained. Relative
    paths inside directory trees remain stable for verified relocation.
    """
    charter = read_json(Path(ws) / "charter.json", {}) if charter is None else charter
    if not isinstance(charter, dict):
        raise ValueError("charter must be an object")
    manifest = {"version": 1}
    for role in ("inputs", "aux"):
        entries = []
        for path in _source_paths(ws, charter, role):
            entry = {"path": path.as_posix()}
            try:
                if path.is_file():
                    entry.update(kind="file", sha256=sha256_file(path))
                elif path.is_dir():
                    entry.update(kind="directory", **_directory_snapshot(path))
                else:
                    entry["kind"] = "missing"
            except (OSError, ValueError) as e:
                entry.update(kind="unreadable", error=f"{type(e).__name__}: {e}")
            entries.append(entry)
        manifest[role] = entries
    return manifest


def _manifest_hashes(manifest):
    hashes = {}
    for role in ("inputs", "aux"):
        for source in manifest.get(role, []):
            if source.get("kind") == "file":
                hashes[source["path"]] = source["sha256"]
            elif source.get("kind") == "directory":
                hashes.update({(Path(source["path"]) / name).as_posix(): sha
                               for name, sha in source["files"].items()})
    return hashes


def _audit_evidence_hashes(ws):
    return {relative: sha256_file(Path(ws) / relative) for relative in AUDIT_EVIDENCE
            if (Path(ws) / relative).is_file()}


class Ctx:
    def __init__(self, ws, charter):
        self.ws = Path(ws)
        self.charter = charter
        self.source_errors = []
        try:
            self.inputs = _source_paths(ws, charter, "inputs")
        except ValueError as e:
            self.inputs = []
            self.source_errors.append(str(e))
        # auxiliary files the checks (or pipeline) read: provenance, side tables... hashed too
        try:
            self.aux = _source_paths(ws, charter, "aux")
        except ValueError as e:
            self.aux = []
            self.source_errors.append(str(e))

    def finding(self, id, severity, title, evidence="", affected=None, known=False, recommendation=""):
        if severity not in SEVERITIES + ("procedure",):
            raise ValueError(f"unknown severity {severity!r}; use one of {', '.join(SEVERITIES)}")
        return {"id": id, "severity": severity, "title": title, "evidence": evidence,
                "affected": affected, "known": known, "recommendation": recommendation}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # A re-audit must run the exact source being hashed, even after a same-size
    # edit within a filesystem timestamp tick (a stale .pyc is not evidence).
    exec(compile(Path(path).read_bytes(), str(path), "exec"), mod.__dict__)
    return mod


def builtin_checks(ctx):
    out = []
    out.extend(ctx.finding("source-config-invalid", "critical", message) for message in ctx.source_errors)
    if not ctx.inputs:
        out.append(ctx.finding("inputs-missing", "critical", "charter.data.inputs is empty",
                               recommendation="list the data files the pipeline reads"))
    for p in ctx.inputs + ctx.aux:
        if not p.exists():
            out.append(ctx.finding("input-not-found", "critical", f"input not found: {p}"))
    for rel, how in (("audit/profile.md", "run profile_data.py --out WS/audit/profile"),
                     ("audit/data_audit.md", "write the audit narrative"),
                     ("audit/close_read.jsonl", "close-read a stratified sample and record verdicts")):
        if not (ctx.ws / rel).exists():
            out.append(ctx.finding(f"missing:{rel}", "procedure", f"{rel} missing", recommendation=how))
    return out


def _checked_findings(ctx, raw):
    """Fail closed: a finding the gate cannot classify is a blocking finding, not a pass."""
    if raw is None:
        return []
    if isinstance(raw, (str, bytes, dict)) or not hasattr(raw, "__iter__"):
        return [ctx.finding("checks-invalid-finding", "critical", "run_checks must return a list of findings",
                            evidence=repr(raw)[:300])]
    out = []
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            problem = "not an object"
        elif not isinstance(f.get("id"), str) or not f["id"].strip():
            problem = "missing id"
        elif f.get("severity") not in SEVERITIES + ("procedure",):
            problem = f"unknown severity {f.get('severity')!r}; use one of {', '.join(SEVERITIES)}"
        elif not isinstance(f.get("title"), str) or not f["title"].strip():
            problem = "missing title"
        elif not isinstance(f.get("known", False), bool):
            problem = "known must be true or false"
        else:
            out.append(f)
            continue
        out.append(ctx.finding("checks-invalid-finding", "critical", f"checks.py finding {i}: {problem}",
                               evidence=json.dumps(f, ensure_ascii=False, default=repr)[:300],
                               recommendation="fix the finding in audit/checks.py and re-run quality_gate.py"))
    return out


def close_read_findings(ctx):
    rows = read_jsonl(ctx.ws / "audit" / "close_read.jsonl")
    if not rows:
        return []
    out = [ctx.finding("close-read", "info", f"{len(rows)} units close-read",
                       evidence=", ".join(f"{v}:{sum(1 for r in rows if r.get('verdict') == v)}"
                                          for v in ("ok", "hard", "ambiguous", "defect")))]
    defects = [r for r in rows if r.get("verdict") == "defect"]
    if defects:
        order = {s: i for i, s in enumerate(SEVERITIES)}
        severities = [r.get("severity", "high") for r in defects]
        sev = min((s if s in order else "critical" for s in severities), key=order.get)
        out.append(ctx.finding("close-read-defects", sev, f"{len(defects)} units look defective on close reading",
                               evidence="; ".join(f"{r['id']}: {str(r.get('evidence', ''))[:80]}" for r in defects[:5]),
                               affected=[r["id"] for r in defects], known=all(r.get("known") is True for r in defects),
                               recommendation="report ids and evidence; let the user decide how to score them"))
    amb = [r for r in rows if r.get("verdict") == "ambiguous"]
    if amb:
        out.append(ctx.finding("close-read-ambiguous", "medium", f"{len(amb)} units are ambiguous",
                               affected=[r["id"] for r in amb],
                               recommendation="use acceptable sets or exclude with a reason; report the count"))
    return out


def _clock(ws, action, reason):
    """Pause/resume the time-limit clock unless it is already in that state."""
    state = None
    for r in read_jsonl(Path(ws) / "ledger.jsonl"):
        if r.get("event") == "clock":
            state = r["action"]
    if (action == "pause" and state != "pause") or (action == "resume" and state == "pause"):
        append_jsonl(Path(ws) / "ledger.jsonl", {"event": "clock", "action": action, "reason": reason,
                                                 "at": now_iso(), "epoch": time.time()})


def _blocking_findings(gate):
    """Keep every finding field and duplicate; order alone is not a new defect."""
    return sorted((f for f in gate.get("findings", [])
                   if not f.get("known") and f.get("severity") in ("critical", "high")),
                  key=lambda f: json.dumps(f, sort_keys=True, ensure_ascii=False))


def _scope_sha(scope):
    return sha256_text(json.dumps(scope, sort_keys=True, ensure_ascii=False))


def _same_review(old, gate):
    """Whether the recorded acceptance describes exactly this audited REVIEW.

    A finding ID identifies a kind of problem, not the records/evidence the user
    accepted. Bind modern approvals to their full audit snapshot. Legacy gates
    can prove equivalence only from fields they actually stored; the abbreviated
    close-read summary cannot prove that its underlying evidence is unchanged.
    """
    if old.get("status") != "REVIEW" or gate.get("status") != "REVIEW":
        return False
    if old.get("review_scope") is not None:
        old_sha = _scope_sha(old["review_scope"])
        accepted_sha = (old.get("waiver") or {}).get("scope_sha256")
        if accepted_sha is not None and accepted_sha != old_sha:
            return False
        old_scope, new_scope = old["review_scope"], gate["review_scope"]
        if old_scope.get("source_manifest") is not None:
            return old_sha == _scope_sha(new_scope)
        # An old scope recorded roots but sorted away their order. A single
        # root per role remains provable; multiple roots require one fresh
        # acceptance instead of guessing the original ordering.
        sources = old_scope.get("data_sources")
        if (not isinstance(sources, dict) or any(len(sources.get(role, [])) > 1 for role in ("inputs", "aux"))
                or sources != new_scope.get("data_sources")):
            return False
        comparable = dict(new_scope)
        comparable.pop("source_manifest", None)
        comparable["version"] = old_scope.get("version", 1)
        return old_sha == _scope_sha(comparable)
    old_blocking = _blocking_findings(old)
    if any(f.get("id") == "close-read-defects" for f in old_blocking):
        return False
    return (old.get("source_manifest") == gate.get("source_manifest")
            and bool(old.get("data_hashes")) and bool(old.get("checks_sha"))
            and old_blocking == _blocking_findings(gate)
            and old["data_hashes"] == gate["data_hashes"]
            and old["checks_sha"] == gate["checks_sha"])


def evaluate(ws):
    ws = Path(ws)
    charter = read_json(ws / "charter.json", {})
    ctx = Ctx(ws, charter)
    try:
        before_sources = source_manifest(ws, charter)
    except ValueError:
        before_sources = None
    findings = builtin_checks(ctx) + close_read_findings(ctx)
    checks_path = ws / "audit" / "checks.py"
    if checks_path.exists():
        try:
            raw = load_module(checks_path, "audit_checks").run_checks(ctx)
            if raw is not None and not isinstance(raw, (str, bytes, dict)) and hasattr(raw, "__iter__"):
                raw = list(raw)  # a generator that fails part-way is a crash, not a partial audit
        except Exception as e:  # a crashing check is itself a blocking finding
            findings.append(ctx.finding("checks-crashed", "critical", f"checks.py raised {type(e).__name__}: {e}"))
        else:
            findings += _checked_findings(ctx, raw)
    else:
        findings.append(ctx.finding("checks-missing", "high", "audit/checks.py missing"))
    try:
        sources = source_manifest(ws, charter)
    except ValueError:
        sources = None  # malformed source declarations already have critical findings
    if sources is not None:
        for role in ("inputs", "aux"):
            for source in sources[role]:
                if source["kind"] not in ("file", "directory"):
                    findings.append(ctx.finding("source-unreadable", "critical",
                                                f"cannot audit {role} source: {source['path']}",
                                                evidence=source.get("error", source["kind"])))
    if before_sources != sources:
        findings.append(ctx.finding("sources-changed-during-audit", "critical",
                                    "source scope or contents changed while audit checks were running",
                                    recommendation="finish data changes and re-run the audit"))
    blocking = [f for f in findings if not f.get("known")]
    if any(f["severity"] == "procedure" for f in findings):
        status = "INCOMPLETE"
    elif any(f["severity"] == "critical" for f in blocking):
        status = "STOP"
    elif any(f["severity"] == "high" for f in blocking):
        status = "REVIEW"
    else:
        status = "PASS"
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES + ("procedure",)}
    gate = {"status": status, "created": now_iso(), "counts": counts, "findings": findings,
            "data_hashes": _manifest_hashes(sources or {}), "source_manifest": sources,
            "audit_evidence_hashes": _audit_evidence_hashes(ws),
            "checks_sha": sha256_file(checks_path) if checks_path.exists() else None,
            "waiver": None}
    # The displayed close-read finding abbreviates evidence to five records and
    # 80 characters each. Approval must cover the complete underlying defects.
    defects = [r for r in read_jsonl(ws / "audit" / "close_read.jsonl") if r.get("verdict") == "defect"]
    gate["review_scope"] = {
        "version": 2,
        "findings": _blocking_findings(gate),
        "data_hashes": gate["data_hashes"],
        "source_manifest": sources,
        "data_sources": {"inputs": sorted(p.as_posix() for p in ctx.inputs),
                         "aux": sorted(p.as_posix() for p in ctx.aux)},
        "checks_sha": gate["checks_sha"],
        "close_read_defects": sorted(defects, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
        if any(f.get("id") == "close-read-defects" for f in _blocking_findings(gate)) else [],
    }
    return gate


def verify_hashes(ws):
    """Verify current source declarations, expanded bytes and audit evidence."""
    ws = Path(ws)
    try:
        gate = read_json(ws / "audit" / "quality_gate.json")
        if not isinstance(gate, dict) or not gate:
            return ["quality_gate.json missing or invalid"]
        recorded = gate.get("source_manifest")
        if not isinstance(recorded, dict) or recorded.get("version") != 1:
            return ["source manifest missing; re-run quality_gate.py to audit the current source scope"]
        current = source_manifest(ws)
        changed = []
        if recorded != current:
            changed.append("source scope or contents changed; re-run quality_gate.py")
        if any(source.get("kind") not in ("file", "directory")
               for role in ("inputs", "aux") for source in current[role]):
            changed.append("one or more source roots are missing or unreadable")
        if gate.get("data_hashes") != _manifest_hashes(current):
            changed.append("expanded audited file set or hashes changed")
        scope = gate.get("review_scope")
        if isinstance(scope, dict) and scope.get("source_manifest") not in (None, recorded):
            changed.append("recorded REVIEW source scope disagrees with the audit manifest")
        evidence = _audit_evidence_hashes(ws)
        if set(evidence) != set(AUDIT_EVIDENCE) or gate.get("audit_evidence_hashes") != evidence:
            changed.append("audit evidence or check code changed; re-run quality_gate.py")
        if gate.get("checks_sha") != evidence.get("audit/checks.py"):
            changed.append("audit/checks.py differs from the audited version")
        return changed
    except (OSError, ValueError, TypeError, KeyError) as e:
        return [f"cannot verify audit sources: {e}; re-run quality_gate.py"]


def gate_allows_runs(ws):
    """(allowed, reason) - used by run_experiment.py."""
    try:
        gate = read_json(Path(ws) / "audit" / "quality_gate.json")
    except (OSError, ValueError) as e:
        return False, f"invalid quality gate: {e}; re-run quality_gate.py"
    if not isinstance(gate, dict) or not gate:
        return False, "no audit/quality_gate.json - run the data audit and quality_gate.py first"
    if gate.get("status") not in STATUS_EXIT:
        return False, "invalid quality gate status; re-run quality_gate.py"
    if gate["status"] == "INCOMPLETE":
        return False, "audit incomplete - finish the missing audit steps and re-run quality_gate.py"
    if gate["status"] == "STOP":
        return False, "quality gate is STOP - report the findings to the user; do not run experiments"
    if gate["status"] == "REVIEW" and not gate.get("waiver"):
        return False, "quality gate is REVIEW - report to the user; only they can accept it (--accept-review)"
    if gate["status"] == "REVIEW":
        waiver, scope = gate.get("waiver"), gate.get("review_scope")
        if (not isinstance(waiver, dict) or not isinstance(scope, dict)
                or waiver.get("scope_sha256") != _scope_sha(scope)):
            return False, "REVIEW acceptance does not match its scope; re-run quality_gate.py"
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
        current = evaluate(a.ws)
        if not _same_review(gate, current):
            sys.exit("audit findings or data changed since the REVIEW; re-run quality_gate.py before accepting")
        gate = current
        gate["waiver"] = {"reason": a.accept_review, "at": now_iso(),
                          "scope_sha256": _scope_sha(gate["review_scope"])}
        write_json(path, gate)
        _clock(a.ws, "resume", "user accepted REVIEW")
        print("REVIEW accepted and recorded; time-limit clock resumed")
        return
    old = read_json(path)
    gate = evaluate(a.ws)
    if old and old.get("waiver") and gate["status"] == "REVIEW":
        if _same_review(old, gate):
            gate["waiver"] = dict(old["waiver"], carried_over=now_iso(),
                                  scope_sha256=_scope_sha(gate["review_scope"]))
            print("REVIEW findings unchanged: the user's earlier acceptance still applies")
    write_json(path, gate)
    if gate["status"] == "REVIEW" and gate.get("waiver"):
        _clock(a.ws, "resume", "gate re-run; acceptance carried over")
    elif gate["status"] in ("REVIEW", "STOP"):
        _clock(a.ws, "pause", f"gate {gate['status']}: waiting for the user")
    elif gate["status"] == "PASS":
        _clock(a.ws, "resume", "gate PASS")
    print(f"GATE {gate['status']}  counts={json.dumps(gate['counts'])}")
    order = {s: i for i, s in enumerate(("procedure",) + SEVERITIES)}
    for f in sorted(gate["findings"], key=lambda f: (f.get("known", False), order[f["severity"]])):
        flag = "known" if f.get("known") else f["severity"].upper()
        print(f"- [{flag}] {f['id']}: {f['title']}"[:200])
    sys.exit(STATUS_EXIT[gate["status"]])


if __name__ == "__main__":
    main()
