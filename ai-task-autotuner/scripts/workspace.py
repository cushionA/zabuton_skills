"""Workspace layout helpers shared by the runner, validator and ledger."""
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import digest, hash_paths, read_json, read_jsonl  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent


def load_module(path, name):
    """Import a workspace .py file; scripts/ is importable from it (e.g. `from common import ...`)."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def charter(ws):
    return read_json(Path(ws) / "charter.json", {})


def eval_paths(ws):
    e = Path(ws) / "eval"
    return [p for p in (e / "dataset.py", e / "score.py", e / "splits.json", e / "reference") if p.exists()]


def eval_hash(ws):
    """Fingerprint of everything that defines the measurement (dataset, scorer, splits, references)."""
    return digest(hash_paths(eval_paths(ws), base=ws))


def candidate_hash(cand_dir):
    return digest(hash_paths([cand_dir], base=cand_dir))


def load_eval(ws):
    ws = Path(ws)
    dataset = load_module(ws / "eval" / "dataset.py", "ws_dataset")
    score = load_module(ws / "eval" / "score.py", "ws_score")
    return dataset, score


def list_runs(ws):
    runs = []
    rdir = Path(ws) / "runs"
    if not rdir.exists():
        return runs
    for d in sorted(rdir.iterdir()):
        meta = read_json(d / "meta.json")
        if meta:
            meta["_dir"] = str(d)
            meta["_summary"] = read_json(d / "summary.json", {})
            runs.append(meta)
    return runs


def load_items(run_dir):
    sc = read_json(Path(run_dir) / "score.json", {})
    return sc.get("items", [])


def ledger_rows(ws):
    return read_jsonl(Path(ws) / "ledger.jsonl")
