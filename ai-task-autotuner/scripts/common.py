"""Shared helpers for ai-task-autotuner scripts (stdlib only).

score.py / pipeline.py authors may import from here: run_experiment.py puts this
directory on sys.path before loading workspace code.
"""
import hashlib
import json
import math
import os
import random
import re
import time
from pathlib import Path

# ---------------------------------------------------------------- JSON / IO


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def extract_json(text):
    """Return the first complete JSON object/array in text (fences allowed), else None.

    Scans for balanced brackets instead of a greedy regex so trailing prose or a
    second JSON block does not break parsing.
    """
    if text is None:
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    try:
        return json.loads(s)
    except ValueError:
        pass
    for start, ch in enumerate(s):
        if ch not in "{[":
            continue
        close = "}" if ch == "{" else "]"
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == ch:
                depth += 1
            elif c == close:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        # try the next opening bracket
    return None


# ---------------------------------------------------------------- hashing


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_files(path):
    p = Path(path)
    if p.is_file():
        yield p
    elif p.is_dir():
        for q in sorted(p.rglob("*")):
            if q.is_file() and "__pycache__" not in q.parts:
                yield q


def hash_paths(paths, base=None):
    """{relative_path: sha256} for files/dirs; directories are expanded."""
    out = {}
    for path in paths:
        for f in iter_files(path):
            key = str(f if base is None else os.path.relpath(f, base)).replace("\\", "/")
            out[key] = sha256_file(f)
    return out


def digest(mapping):
    return sha256_text(json.dumps(mapping, sort_keys=True))[:16]


# ---------------------------------------------------------------- stats


def percentile(values, q):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def sign_test(improved, regressed):
    """Two-sided exact binomial sign test p-value."""
    n = improved + regressed
    if n == 0:
        return 1.0
    k = min(improved, regressed)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_ci(values, n_boot=2000, alpha=0.05, seed=0):
    vals = [v for v in values if v is not None]
    if not vals:
        return (None, None)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [vals[rng.randrange(len(vals))] for _ in vals]
        means.append(sum(sample) / len(sample))
    return (percentile(means, alpha / 2), percentile(means, 1 - alpha / 2))


def cohen_kappa(pairs):
    """pairs: iterable of (label_a, label_b). Returns kappa or None."""
    pairs = list(pairs)
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({json.dumps(x, sort_keys=True) for p in pairs for x in p})
    po = sum(1 for a, b in pairs if a == b) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for a, _ in pairs if json.dumps(a, sort_keys=True) == lab) / n
        pb = sum(1 for _, b in pairs if json.dumps(b, sort_keys=True) == lab) / n
        pe += pa * pb
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def confusion(pairs):
    """pairs of (pred_label, ref_label) -> {ref: {pred: count}} with JSON-string keys."""
    table = {}
    for pred, ref in pairs:
        r = ref if isinstance(ref, str) else json.dumps(ref, ensure_ascii=False)
        p = pred if isinstance(pred, str) else json.dumps(pred, ensure_ascii=False)
        table.setdefault(r, {}).setdefault(p, 0)
        table[r][p] += 1
    return table


def paired_items(items_a, items_b):
    """Compare two item lists ({id, score}) on shared ids.

    Returns dict(n, improved, regressed, delta_mean, band, p) where band is a 95%
    half-width of the paired mean difference (2*sqrt(sum d^2)/n): an approximate
    noise floor when a and b are repeated runs of the same candidate.
    """
    a = {i["id"]: i.get("score", 0.0) for i in items_a}
    b = {i["id"]: i.get("score", 0.0) for i in items_b}
    ids = sorted(set(a) & set(b))
    diffs = [b[k] - a[k] for k in ids]
    n = len(ids)
    if n == 0:
        return {"n": 0, "improved": 0, "regressed": 0, "delta_mean": None, "band": None, "p": 1.0}
    improved = sum(1 for d in diffs if d > 1e-9)
    regressed = sum(1 for d in diffs if d < -1e-9)
    band = 2 * math.sqrt(sum(d * d for d in diffs)) / n
    return {
        "n": n,
        "improved": improved,
        "regressed": regressed,
        "delta_mean": sum(diffs) / n,
        "band": band,
        "p": sign_test(improved, regressed),
        "improved_ids": [k for k, d in zip(ids, diffs) if d > 1e-9],
        "regressed_ids": [k for k, d in zip(ids, diffs) if d < -1e-9],
    }


def text_similarity(a, b):
    """Character 3-gram Jaccard similarity; language-agnostic novelty check."""
    def grams(s):
        s = re.sub(r"\s+", " ", (s or "").lower()).strip()
        return {s[i:i + 3] for i in range(max(0, len(s) - 2))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ---------------------------------------------------------------- reference sets


def load_reference(ws, split):
    """Merge eval/reference/<split>.jsonl (agreed) and <split>.adjudicated.jsonl.

    Each line: {"key": ..., "label": ...} or {"key", "acceptable": [...]} or
    {"key", "exclude": true}. Adjudicated lines override agreed ones. Keys listed
    only in <split>.disagreements.jsonl are returned under "__unresolved__".
    """
    base = Path(ws) / "eval" / "reference"
    ref = {}
    for row in read_jsonl(base / f"{split}.jsonl"):
        ref[row["key"]] = row
    for row in read_jsonl(base / f"{split}.adjudicated.jsonl"):
        ref[row["key"]] = row
    unresolved = [r["key"] for r in read_jsonl(base / f"{split}.disagreements.jsonl") if r["key"] not in ref]
    ref["__unresolved__"] = unresolved
    return ref


def label_matches(pred, entry):
    """True if pred satisfies a reference entry (label or acceptable set)."""
    if entry is None or entry.get("exclude"):
        return None
    if "acceptable" in entry:
        return any(pred == x for x in entry["acceptable"])
    return pred == entry.get("label")


# ---------------------------------------------------------------- prices / cost

# USD per million tokens (first-party list prices; update when they change).
PRICES = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "in_long": 0.50, "out_long": 2.50, "long_from": 100_000},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00},
    "claude-fable-5-1": {"in": 10.00, "out": 50.00},
}
MODEL_ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}


def estimate_cost(model, usage, prices=None):
    """List-price estimate: cache writes 1.25x input, cache reads 0.1x input."""
    table = dict(PRICES)
    table.update(prices or {})
    model = MODEL_ALIASES.get(model, model)
    p = table.get(model)
    if p is None or usage is None:
        return None
    inp = usage.get("input") or 0
    cr = usage.get("cache_read") or 0
    cw = usage.get("cache_write") or 0
    out = usage.get("output") or 0
    long_ctx = p.get("long_from") and (inp + cr + cw) > p["long_from"]
    pin = p["in_long"] if long_ctx else p["in"]
    pout = p["out_long"] if long_ctx else p["out"]
    return (inp * pin + cw * pin * 1.25 + cr * pin * 0.1 + out * pout) / 1e6


# ---------------------------------------------------------------- splits


def make_splits(ids, dev_n, holdout_n, seed=0, groups=None, strata=None):
    """Random, stratified, group-aware split.

    Whole groups (near-duplicates, same source page, same parent product...) go to
    one split so nothing leaks between dev and holdout. Each group goes to the open split
    whose share of that group's stratum is furthest below target, so small strata are spread
    over dev and holdout instead of filling dev first. Never choose splits by baseline score -
    that buys regression to the mean instead of improvement.
    """
    rng = random.Random(seed)
    groups = groups or {i: i for i in ids}
    strata = strata or {i: "_" for i in ids}
    g2ids = {}
    for i in ids:
        g2ids.setdefault(groups[i], []).append(i)
    by_stratum = {}
    for g, members in g2ids.items():
        by_stratum.setdefault(strata[members[0]], []).append(g)
    for gs in by_stratum.values():
        gs.sort(key=str)
        rng.shuffle(gs)
    order = []
    while any(by_stratum.values()):
        for st in sorted(by_stratum, key=str):
            if by_stratum[st]:
                order.append(by_stratum[st].pop())
    total = len(ids) or 1
    target = {"dev": dev_n, "holdout": holdout_n}
    out = {"dev": [], "holdout": [], "rest": []}
    per = {k: {} for k in target}
    for g in order:
        members = g2ids[g]
        st = strata[members[0]]
        open_splits = [k for k in target if len(out[k]) < target[k]]
        if not open_splits:
            out["rest"] += members
            continue
        k = min(open_splits, key=lambda k: (per[k].get(st, 0) / (target[k] / total), -(target[k] - len(out[k]))))
        out[k] += members
        per[k][st] = per[k].get(st, 0) + len(members)
    return out
