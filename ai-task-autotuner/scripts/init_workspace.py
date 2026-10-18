"""Create an experiment workspace from the template (never overwrites existing files).

  python init_workspace.py WS --task "short name" --data PATH [PATH...] [--brief PATH]
      [--model haiku] [--backend cli|api] [--effort low]
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import now_iso, read_json, write_json  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "workspace_template"

JOURNAL = """# Journal

Concise English. One block per experiment; rewrite Learnings as they change.

## Learnings (max ~20 bullets; what is true about this task and model)

## Idea backlog (group: idea - evidence - status)

## Log
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ws")
    ap.add_argument("--task", required=True)
    ap.add_argument("--data", nargs="+", required=True, help="input files the pipeline will read")
    ap.add_argument("--brief", default="", help="path to the user's task brief, if any")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--backend", default="cli", choices=["cli", "api"])
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    ws = Path(a.ws)
    ws.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE.rglob("*"):
        if "__pycache__" in src.parts:
            continue
        dst = ws / src.relative_to(TEMPLATE)
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    ch = read_json(ws / "charter.json")
    if not ch.get("created"):
        ch.update(task=a.task, brief=a.brief, created=now_iso(), created_epoch=time.time(),
                  skill_dir=str(TEMPLATE.parents[1]))
        ch["data"]["inputs"] = [str(Path(p).resolve()) for p in a.data]
        ch["backend"].update(backend=a.backend, model=a.model, effort=a.effort)
        write_json(ws / "charter.json", ch)
    for name, text in (("journal.md", JOURNAL),):
        if not (ws / name).exists():
            (ws / name).write_text(text, encoding="utf-8")
    (ws / "ledger.jsonl").touch()
    (ws / "runs").mkdir(exist_ok=True)
    print(f"workspace ready: {ws}")
    print("next: profile_data.py -> audit/checks.py + data_audit.md -> quality_gate.py "
          "-> experiment_plan.md + eval/ -> baseline x2 -> validate_eval.py -> loop")


if __name__ == "__main__":
    main()
