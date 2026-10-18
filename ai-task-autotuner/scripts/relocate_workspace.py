"""Point a moved workspace at the skill and data on this machine.

  python relocate_workspace.py WS --data-map OLD_PREFIX=NEW_PREFIX [--data-map ...] [--dry-run]

Rewrites charter.json (skill_dir -> this skill, data.inputs / data.aux by prefix) and the keys
of audit/quality_gate.json data_hashes, so the recorded hashes still guard the same files.
Run `quality_gate.py WS --verify` afterwards: it must say "data unchanged" before any run.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import read_json, write_json  # noqa: E402


def remap(path, pairs):
    p = str(path).replace("\\", "/")
    for old, new in pairs:
        if p.startswith(old):
            return new + p[len(old):]
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--data-map", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pairs = []
    for m in a.data_map:
        old, _, new = m.partition("=")
        pairs.append((old.replace("\\", "/"), new.replace("\\", "/")))
    ws = Path(a.ws)
    ch = read_json(ws / "charter.json")
    if ch is None:
        sys.exit(f"no charter.json in {ws}")
    skill_dir = str(Path(__file__).resolve().parents[1])
    print(f"skill_dir: {ch.get('skill_dir')} -> {skill_dir}")
    ch["skill_dir"] = skill_dir
    data = ch.setdefault("data", {})
    for key in ("inputs", "aux"):
        new = [remap(p, pairs) for p in data.get(key, [])]
        for old_p, new_p in zip(data.get(key, []), new):
            if old_p != new_p:
                print(f"{key}: {old_p} -> {new_p}")
            if not Path(new_p).exists():
                print(f"  WARNING: {new_p} does not exist")
        data[key] = new
    gate_path = ws / "audit" / "quality_gate.json"
    gate = read_json(gate_path)
    if gate and gate.get("data_hashes"):
        gate["data_hashes"] = {remap(k, pairs): v for k, v in gate["data_hashes"].items()}
    if a.dry_run:
        print("dry run: nothing written")
        return
    write_json(ws / "charter.json", ch)
    if gate:
        write_json(gate_path, gate)
    print("done - now run: quality_gate.py WS --verify")


if __name__ == "__main__":
    main()
