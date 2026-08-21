import argparse
import datetime as dt
import json
from pathlib import Path

import state


def main():
    state.force_utf8_stdio()
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("recommended", help="JSON array of {id, title, published_at, category}")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--today")
    parser.add_argument("--profile", default=str(skill_root / "references" / "profile.json"))
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    fresh_within_days = int(profile.get("adaptive_freshness", {}).get("fresh_within_days", 30))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    items = json.loads(Path(args.recommended).read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("recommended must be a JSON array")

    added = state.record_seen(args.state_dir, items, "detailed")
    fresh, total = state.record_freshness(args.state_dir, items, today, fresh_within_days)
    print(json.dumps({"recorded_seen": added, "freshness_fresh": fresh, "freshness_total": total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
