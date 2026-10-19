"""Relocate a workspace without approving changed data or task design.

  python relocate_workspace.py WS --data-map OLD_PREFIX=NEW_PREFIX [--dry-run]

Exported workspaces automatically remap bundled paths from replay.json's original
workspace. External inputs and briefs need explicit maps. Every recorded data and
reviewed-file hash must match before any JSON is changed. Existing REVIEW approval
and task review survive only this verified, content-preserving path relocation.
"""
import argparse
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import append_jsonl, digest, now_iso, read_json, read_jsonl, sha256_file, write_json  # noqa: E402
from quality_gate import Ctx, _scope_sha, close_read_findings  # noqa: E402
from task_review import _snapshot  # noqa: E402


def remap(path, pairs):
    """Match complete path prefixes, not similarly named sibling directories."""
    p = str(path).replace("\\", "/")
    for old, new in pairs:
        old, new = old.rstrip("/"), new.rstrip("/")
        if p == old or p.startswith(old + "/"):
            return new + p[len(old):]
    return path


def _mapped_hashes(hashes, pairs):
    out = {}
    for path, sha in hashes.items():
        target = str(remap(path, pairs))
        if target in out and out[target] != sha:
            raise ValueError(f"relocation merges distinct audited files: {target}")
        if not Path(target).is_file() or sha256_file(target) != sha:
            raise ValueError(f"audited/reviewed file missing or bytes changed: {target}; re-audit/re-review changes")
        out[target] = sha
    return out


def _mapped_charter(ch, pairs):
    ch = copy.deepcopy(ch)
    ch["skill_dir"] = str(Path(__file__).resolve().parents[1])
    for key in ("inputs", "aux"):
        ch.setdefault("data", {})[key] = [remap(p, pairs) for p in ch.get("data", {}).get(key, [])]
    if ch.get("brief"):
        ch["brief"] = remap(ch["brief"], pairs)
    return ch


def _mapped_snapshot(snapshot, ws, original_ws, pairs):
    snapshot = copy.deepcopy(snapshot)
    ch = _mapped_charter(snapshot["charter"], pairs)
    ch.pop("skill_dir", None)
    snapshot["charter"] = ch
    snapshot["audited_data_hashes"] = _mapped_hashes(snapshot["audited_data_hashes"], pairs)
    files = {}
    for key, sha in snapshot["files"].items():
        original = Path(key) if Path(key).is_absolute() else original_ws / key
        target = Path(remap(str(original.resolve()), pairs))
        if not target.is_file() or sha256_file(target) != sha:
            raise ValueError(f"reviewed design file missing or bytes changed: {target}")
        mapped_key = os.path.relpath(target, ws).replace("\\", "/")
        if mapped_key in files and files[mapped_key] != sha:
            raise ValueError(f"relocation merges distinct reviewed files: {target}")
        files[mapped_key] = sha
    snapshot["files"] = files
    return snapshot


def _verify_audit_evidence(ws, gate, charter):
    for name in ("checks.py", "profile.md", "data_audit.md", "close_read.jsonl"):
        if not (ws / "audit" / name).is_file():
            raise ValueError(f"original audit evidence missing: audit/{name}")
    if gate.get("checks_sha") != sha256_file(ws / "audit" / "checks.py"):
        raise ValueError("audit checks changed since the recorded gate; re-run quality_gate.py")
    if gate.get("waiver") and gate.get("review_scope"):
        # The scope stores full close-read evidence, while the displayed finding
        # truncates it. A newly discovered defect must never inherit the waiver.
        findings = close_read_findings(Ctx(ws, charter))
        defects = [r for r in read_jsonl(ws / "audit" / "close_read.jsonl") if r.get("verdict") == "defect"]
        blocking = any(f.get("id") == "close-read-defects" and not f.get("known")
                       and f.get("severity") in ("high", "critical") for f in findings)
        defects = sorted(defects, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False)) if blocking else []
        if defects != gate["review_scope"].get("close_read_defects", []):
            raise ValueError("close-read defects changed since REVIEW acceptance; re-run quality_gate.py")


def relocate(ws, pairs=(), dry_run=False):
    ws = Path(ws).resolve()
    ch = read_json(ws / "charter.json")
    if not ch:
        raise ValueError(f"no charter.json in {ws}")
    replay = read_json(ws / "replay.json", {})
    original_ws = Path(replay.get("source_workspace", ws)).resolve()
    pairs = [(str(old).replace("\\", "/"), str(new).replace("\\", "/")) for old, new in pairs]
    if any(not old or not new for old, new in pairs):
        raise ValueError("--data-map requires nonempty OLD=NEW paths")
    # Explicit external maps take precedence over automatic bundled-data maps.
    if original_ws != ws:
        pairs.append((str(original_ws), str(ws)))
    new_ch = _mapped_charter(ch, pairs)
    for key in ("inputs", "aux"):
        for path in new_ch.get("data", {}).get(key, []):
            if not Path(path).exists():
                raise ValueError(f"missing {key} path: {path}; supply --data-map OLD=NEW")
    gate_path = ws / "audit" / "quality_gate.json"
    gate = read_json(gate_path)
    new_gate = copy.deepcopy(gate)
    if gate:
        _verify_audit_evidence(ws, gate, new_ch)
        new_gate["data_hashes"] = _mapped_hashes(gate.get("data_hashes", {}), pairs)
        scope = new_gate.get("review_scope")
        if scope:
            waiver = gate.get("waiver")
            if waiver and waiver.get("scope_sha256") not in (None, _scope_sha(gate["review_scope"])):
                raise ValueError("recorded REVIEW acceptance does not match its scope")
            scope["data_hashes"] = _mapped_hashes(scope.get("data_hashes", {}), pairs)
            if "data_sources" in scope:
                scope["data_sources"] = {kind: sorted(str(remap(p, pairs)) for p in paths)
                                         for kind, paths in scope["data_sources"].items()}
            if waiver:
                new_gate["waiver"]["scope_sha256"] = _scope_sha(scope)
        elif gate.get("waiver") and new_gate["data_hashes"] != gate.get("data_hashes", {}):
            raise ValueError("legacy REVIEW acceptance has no auditable scope; re-run quality_gate.py")
    review_path = ws / "review" / "task_review.json"
    review = read_json(review_path)
    updated_review = None
    updated_request = None
    if review:
        if digest(review.get("snapshot", {})) != review.get("design_hash"):
            raise ValueError("recorded task review snapshot/hash disagree")
        report = (ws / review["report"]).resolve()
        if (not report.is_relative_to(ws / "review") or not report.is_file()
                or sha256_file(report) != review.get("report_sha256")):
            raise ValueError("recorded task review report changed or is unavailable")
        expected = _mapped_snapshot(review["snapshot"], ws, original_ws, pairs)
        current = _snapshot(ws, expected["candidate"], charter=new_ch, gate=new_gate or {})
        if expected != current:
            raise ValueError("task design differs beyond file relocation; prepare a new task review")
        updated_review = dict(review, snapshot=current, design_hash=digest(current))
        request = read_json(ws / "review" / "task_review_request.json")
        if request and request.get("design_hash") == review["design_hash"]:
            updated_request = dict(request, snapshot=current, design_hash=digest(current))
    event = {"event": "relocate", "at": now_iso(), "source_workspace": str(original_ws),
             "workspace": str(ws), "path_maps": pairs, "content_verified": True}
    if review:
        event.update(previous_design_hash=review["design_hash"], design_hash=updated_review["design_hash"])
    if dry_run:
        return event
    write_json(ws / "charter.json", new_ch)
    if gate:
        write_json(gate_path, new_gate)
    if updated_review:
        write_json(review_path, updated_review)
        if updated_request:
            write_json(ws / "review" / "task_review_request.json", updated_request)
        append_jsonl(ws / "review" / "task_review_history.jsonl", event)
    append_jsonl(ws / "ledger.jsonl", event)
    if replay:
        replay["source_workspace"] = str(ws)
        replay["relocated_at"] = event["at"]
        replay["external_dependencies"] = [remap(p, pairs) for p in replay.get("external_dependencies", [])]
        write_json(ws / "replay.json", replay)
    return event


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--data-map", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pairs = []
    for value in a.data_map:
        old, sep, new = value.partition("=")
        if not sep:
            ap.error("--data-map requires OLD=NEW")
        pairs.append((old, new))
    try:
        event = relocate(a.ws, pairs, a.dry_run)
    except (ValueError, OSError, KeyError, TypeError) as e:
        ap.exit(2, f"RELOCATION REFUSED: {e}\n")
    for old, new in event["path_maps"]:
        print(f"{old} -> {new}")
    print("dry run: content verified; nothing written" if a.dry_run else
          "relocated; data and reviewed design bytes verified, existing controls retained")


if __name__ == "__main__":
    main()
