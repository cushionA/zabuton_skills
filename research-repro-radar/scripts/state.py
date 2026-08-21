import datetime as dt
import json
import os
import sys
from pathlib import Path

SEEN_RETENTION_DAYS = 365
SEEN_MAX_ENTRIES = 2000


def force_utf8_stdio():
    # Windowsのコンソールは既定でcp932などのANSIコードページを使うため、
    # print()した日本語がstdout経由で読まれると文字化けする。stdinも同様。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _read(path, default):
    if not path.exists():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return value


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parse(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def candidate_key(record):
    return str(
        record.get("doi") or record.get("arxiv_id") or record.get("id") or record.get("url") or ""
    ).casefold()


def load_seen(state_dir):
    value = _read(Path(state_dir) / "seen.json", {"version": 1, "reported": {}})
    reported = value.get("reported")
    if not isinstance(reported, dict):
        raise ValueError("seen.json must contain a reported object")
    return reported


def record_seen(state_dir, items, tier):
    reported = load_seen(state_dir)
    now = dt.datetime.now(dt.timezone.utc)
    added = 0
    for item in items:
        key = str(item.get("id") or "").casefold()
        if not key:
            continue
        reported[key] = {"title": item.get("title"), "tier": tier, "at": now.isoformat()}
        added += 1
    cutoff = now - dt.timedelta(days=SEEN_RETENTION_DAYS)
    # 日付が壊れている項目は捨てずに残す。忘れて再報告するほうが害が大きい。
    kept = {}
    for key, value in reported.items():
        at = _parse(value.get("at")) if isinstance(value, dict) else None
        if at is None or at >= cutoff:
            kept[key] = value
    ordered = sorted(kept.items(), key=lambda pair: str(pair[1].get("at", "")), reverse=True)
    _write(Path(state_dir) / "seen.json", {"version": 1, "reported": dict(ordered[:SEEN_MAX_ENTRIES])})
    return added


def load_queue(state_dir, today, max_carry_over_days):
    value = _read(Path(state_dir) / "queue.json", {"version": 1, "candidates": []})
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("queue.json must contain a candidates array")
    fresh = []
    for record in candidates:
        if not isinstance(record, dict):
            continue
        since = _parse(record.get("carried_over_since"))
        if since is not None and (today - since.date()).days > max_carry_over_days:
            continue
        fresh.append(record)
    return fresh


def save_queue(state_dir, records, today):
    stamped = []
    for record in records:
        item = dict(record)
        item.setdefault("carried_over_since", today.isoformat())
        item["carry_count"] = int(item.get("carry_count") or 0) + 1
        stamped.append(item)
    _write(Path(state_dir) / "queue.json", {"version": 1, "candidates": stamped})
    return len(stamped)


def load_mail(state_dir):
    value = _read(Path(state_dir) / "mail.json", {"sent": []})
    if not isinstance(value.get("sent"), list):
        raise ValueError("mail.json must contain a sent array")
    return value


def save_mail(state_dir, value):
    _write(Path(state_dir) / "mail.json", value)
