"""Workspace layout helpers shared by the runner, validator and ledger."""
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MODEL_ALIASES, digest, hash_paths, read_json, read_jsonl, sha256_file  # noqa: E402

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
    """Fingerprint of current source data and the files defining its measurement."""
    return digest({"eval": hash_paths(eval_paths(ws), base=ws), "data_hash": data_fingerprint(ws)})


def data_snapshot(ws):
    """Path-neutral identity of the inputs used to generate model outputs.

    Preserve source roles and order, including duplicate sources. A file's
    location is deliberately absent so moving identical data does not invalidate
    its measurements; relative names within a directory still define its tree.
    Dataset/split changes require new outputs, while scorer/reference changes
    can reuse and rescore existing outputs.
    """
    ws = Path(ws)
    data = charter(ws).get("data", {})

    def source(path):
        p = Path(path)
        if p.is_file():
            return {"kind": "file", "sha256": sha256_file(p)}
        if p.is_dir():
            return {"kind": "directory", "files": hash_paths([p], base=p)}
        # Missing sources must differ from the previous readable snapshot.
        return {"kind": "missing"}

    return {"version": 1,
            "inputs": [source(p) for p in data.get("inputs", [])],
            "aux": [source(p) for p in data.get("aux", [])],
            "scope": {k: v for k, v in data.items() if k not in ("inputs", "aux")},
            "units": hash_paths([ws / "eval" / "dataset.py", ws / "eval" / "splits.json"], base=ws)}


def data_fingerprint(ws):
    """Identity pinned at generation; never refresh it when rescoring outputs."""
    return digest(data_snapshot(ws))


def candidate_hash(cand_dir):
    return digest(hash_paths([cand_dir], base=cand_dir))


def effective_config_fingerprint(config):
    """Identity of resolved execution settings; unknown legacy settings stay unknown."""
    if not isinstance(config, dict) or config.get("backend") not in ("cli", "claude", "codex", "api"):
        return None
    model = config.get("model")
    if not isinstance(model, str) or not model.strip() or model == "auto":
        return None
    normalized = dict(config)
    normalized["backend"] = "claude" if config["backend"] == "cli" else config["backend"]
    if normalized["backend"] in ("claude", "api"):
        normalized["model"] = MODEL_ALIASES.get(model, model)
    elif model == "luna":
        normalized["model"] = "gpt-6-luna"
    return digest(normalized)


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
