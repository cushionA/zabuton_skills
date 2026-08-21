import argparse
import datetime as dt
import json
import state
from pathlib import Path


def load_records(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
        return records
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise ValueError("input must be a JSON object, array, or JSON Lines")


def required_text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"field '{key}' must be a non-empty string")
    return value.strip()


def optional_number(record, key):
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"field '{key}' must be numeric")
    return float(value)


def hot_count(value):
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, list):
        return len({str(item).casefold() for item in value if str(item).strip()})
    raise ValueError("hot_signals must be a non-negative integer or array")


def age_bonus(record, today, freshness_window_days):
    # 鮮度は加点だけに使う。年代を理由に候補を落とさない。
    # 何年前の論文でも今trendingなら拾う。関連性・話題性のゲートは score() 側の
    # topic_hits / hot_signals / trend_rank だけが担う。
    published_at = record.get("published_at")
    if not published_at:
        return 0
    if not isinstance(published_at, str):
        raise ValueError("published_at must be an ISO date string")
    parsed = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    age = (today - parsed.date()).days
    if age < 0:
        raise ValueError("published_at is in the future")
    if age <= 3:
        return 3
    if age <= 7:
        return 2
    if age <= freshness_window_days:
        return 1
    return 0


def score(record, profile, today, key, freshness_multiplier=1.0):
    title = required_text(record, "title")
    source = required_text(record, "source").casefold()
    url = required_text(record, "url")
    abstract = record.get("abstract", "")
    if not isinstance(abstract, str):
        raise ValueError("abstract must be a string")
    text = f"{title}\n{abstract}".casefold()
    source_priorities = {
        item["name"].casefold(): float(item["priority"])
        for item in profile["sources"]
        if item.get("enabled", True)
    }
    if source not in source_priorities:
        return "rejected", {"id": key, "title": title, "reason": "disabled_or_unknown_source"}

    total = source_priorities[source]
    reasons = [f"source+{source_priorities[source]:g}"]
    topic_hits = {}
    for interest in profile["interests"]:
        hits = sorted({keyword for keyword in interest["keywords"] if keyword.casefold() in text})
        if hits:
            contribution = float(interest["weight"]) * min(len(hits), 2)
            total += contribution
            topic_hits[interest["id"]] = hits
            reasons.append(f"{interest['id']}+{contribution:g}")

    signals = min(hot_count(record.get("hot_signals")), 4)
    total += signals * 2
    if signals:
        reasons.append(f"hot+{signals * 2}")
    trend_rank = record.get("trend_rank")
    if trend_rank is not None:
        if isinstance(trend_rank, bool) or not isinstance(trend_rank, int) or trend_rank <= 0:
            raise ValueError("trend_rank must be a positive integer")
        trend_bonus = 5 if trend_rank <= 3 else 3 if trend_rank <= 10 else 1 if trend_rank <= 30 else 0
        total += trend_bonus
        if trend_bonus:
            reasons.append(f"trend_rank+{trend_bonus}")

    mode = "interest"
    if not topic_hits:
        if signals >= 3 or trend_rank is not None and trend_rank <= 5:
            mode = "wildcard"
            reasons.append("wildcard")
        else:
            return "rejected", {"id": key, "title": title, "reason": "not_relevant_or_hot"}

    for field, bonus in (("has_code", 4), ("has_model", 3), ("has_data", 2)):
        value = record.get(field, False)
        if not isinstance(value, bool):
            raise ValueError(f"field '{field}' must be boolean")
        if value:
            total += bonus
            reasons.append(f"{field}+{bonus}")

    recency = age_bonus(record, today, profile["lookback_days"])
    boosted_recency = round(recency * freshness_multiplier, 2)
    total += boosted_recency
    if boosted_recency:
        tag = f"recency+{boosted_recency:g}"
        if freshness_multiplier != 1.0:
            tag += f"(freshness_boost x{freshness_multiplier:g})"
        reasons.append(tag)
    estimated_cost = optional_number(record, "estimated_cost_jpy") or 0
    if estimated_cost < 0:
        raise ValueError("estimated_cost_jpy cannot be negative")
    if estimated_cost > profile["constraints"]["money_budget_jpy"]:
        return "rejected", {"id": key, "title": title, "reason": "cash_budget_exceeded"}

    requires_gpu = record.get("requires_gpu", False)
    if not isinstance(requires_gpu, bool):
        raise ValueError("requires_gpu must be boolean")
    cpu_minutes = optional_number(record, "estimated_cpu_minutes")
    gpu_minutes = optional_number(record, "estimated_gpu_minutes")
    ram_gb = optional_number(record, "estimated_ram_gb")
    for name, value in (("estimated_cpu_minutes", cpu_minutes), ("estimated_gpu_minutes", gpu_minutes), ("estimated_ram_gb", ram_gb)):
        if value is not None and value < 0:
            raise ValueError(f"{name} cannot be negative")
    estimated_check_minutes = gpu_minutes if requires_gpu else cpu_minutes
    if estimated_check_minutes is None:
        estimated_check_minutes = 10
    item = {
        "id": key,
        "title": title,
        "source": source,
        "url": url,
        "published_at": record.get("published_at"),
        "score": round(total, 2),
        "mode": mode,
        "topic_hits": topic_hits,
        "reasons": reasons,
        "requires_gpu": requires_gpu,
        "estimated_check_minutes": estimated_check_minutes,
    }
    if requires_gpu and not profile["constraints"]["gpu"]["enabled"]:
        item["reason"] = "gpu_disabled"
        return "deferred", item
    if requires_gpu and estimated_check_minutes > profile["constraints"]["gpu"]["max_hours_per_candidate"] * 60:
        item["reason"] = "gpu_time_limit"
        return "deferred", item
    if cpu_minutes is not None and cpu_minutes > profile["constraints"]["max_cpu_minutes_per_candidate"]:
        item["reason"] = "cpu_time_limit"
        return "deferred", item
    if ram_gb is not None and ram_gb > profile["constraints"]["max_ram_gb"]:
        item["reason"] = "ram_limit"
        return "deferred", item
    if not requires_gpu:
        item["score"] += 3
        item["reasons"].append("cpu_feasible+3")
    return "eligible", item


def main():
    state.force_utf8_stdio()
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--profile", default=str(skill_root / "references" / "profile.json"))
    parser.add_argument("--output")
    parser.add_argument("--today")
    parser.add_argument("--state-dir")
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    # 掘り起こし(古い論文)ばかりで新しい理論が入ってこなくなったら、鮮度加点を自動で強める。
    # 新しい深掘り候補が戻れば直近の実績比率が上がり、次回以降は自然に緩む。
    af = profile.get("adaptive_freshness")
    freshness_multiplier = 1.0
    freshness_ratio = None
    if af and args.state_dir:
        freshness_ratio = state.freshness_ratio(args.state_dir, af["stale_trigger_runs"])
        if freshness_ratio is not None and freshness_ratio < af["stale_trigger_max_fresh_ratio"]:
            freshness_multiplier = float(af["boost_multiplier"])

    eligible = []
    deferred = []
    rejected = []
    seen = set()
    raw_by_key = {}
    records = load_records(args.input)
    reported = {}
    carried = []
    if args.state_dir:
        reported = state.load_seen(args.state_dir)
        # 今回の探索で再発見された候補は新しいメタデータのほうを使う
        fresh_keys = {state.candidate_key(record) for record in records if isinstance(record, dict)}
        carried = [
            record
            for record in state.load_queue(args.state_dir, today, profile["constraints"]["max_carry_over_days"])
            if state.candidate_key(record) not in fresh_keys
        ]
        records = records + carried
    # 1件の壊れた候補で全体を落とさない。自動収集した入力は必ず欠損を含む。
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            rejected.append({"id": f"#{index}", "title": None, "reason": "invalid_record", "detail": "candidate must be an object"})
            continue
        key = str(record.get("doi") or record.get("arxiv_id") or record.get("id") or record.get("url") or "").casefold()
        if not key:
            rejected.append({"id": f"#{index}", "title": record.get("title"), "reason": "invalid_record", "detail": "needs id, DOI, arXiv ID, or URL"})
            continue
        if key in seen:
            rejected.append({"id": key, "title": record.get("title"), "reason": "duplicate"})
            continue
        seen.add(key)
        if key in reported:
            rejected.append({"id": key, "title": record.get("title"), "reason": "already_reported"})
            continue
        raw_by_key[key] = record
        try:
            status, item = score(record, profile, today, key, freshness_multiplier)
        except ValueError as exc:
            rejected.append({"id": key, "title": record.get("title"), "reason": "invalid_record", "detail": str(exc)})
            continue
        if status == "eligible":
            eligible.append(item)
        elif status == "deferred":
            deferred.append(item)
        else:
            rejected.append(item)

    eligible.sort(key=lambda item: (-item["score"], item["title"].casefold()))
    time_budget = float(profile["constraints"]["run_time_budget_minutes"])
    used_minutes = 0.0
    scheduled_checks = []
    for item in eligible:
        if used_minutes + item["estimated_check_minutes"] <= time_budget:
            scheduled_checks.append(item)
            used_minutes += item["estimated_check_minutes"]
        else:
            deferred_item = dict(item)
            deferred_item["reason"] = "run_time_budget"
            deferred.append(deferred_item)

    # 読解は実行より桁で安い。実行できない候補も含めて広く読む対象を出す。
    review_size = int(profile["exploration"]["review_pool_size"])
    eligible_ids = {item["id"] for item in eligible}
    review_pool = sorted(
        eligible + [item for item in deferred if item["id"] not in eligible_ids and "score" in item],
        key=lambda item: (-item.get("score", 0), str(item.get("title", "")).casefold()),
    )[:review_size]

    queued = 0
    if args.state_dir:
        # 時間切れだけを繰り越す。GPU必須やRAM超過は次回も同じ制約に当たる。
        carry = [
            raw_by_key[item["id"]]
            for item in deferred
            if item.get("reason") == "run_time_budget" and item["id"] in raw_by_key
        ]
        queued = state.save_queue(args.state_dir, carry, today)

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "freshness_boost": {
            "active": freshness_multiplier != 1.0,
            "multiplier": freshness_multiplier,
            "recent_fresh_ratio": freshness_ratio,
        },
        "profile_version": profile["profile_version"],
        "counts": {
            "input": len(records),
            "carried_in": len(carried),
            "eligible": len(eligible),
            "scheduled": len(scheduled_checks),
            "review_pool": len(review_pool),
            "deferred": len(deferred),
            "queued": queued,
            "rejected": len(rejected),
            "already_reported": sum(1 for item in rejected if item.get("reason") == "already_reported"),
            "invalid": sum(1 for item in rejected if item.get("reason") == "invalid_record"),
        },
        "ranked_candidates": eligible,
        "review_pool": review_pool,
        "scheduled_checks": scheduled_checks,
        "used_time_budget_minutes": used_minutes,
        "remaining_time_budget_minutes": max(0, time_budget - used_minutes),
        "deferred": sorted(deferred, key=lambda item: (-item.get("score", 0), str(item.get("title", "")).casefold())),
        "rejected": rejected,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
