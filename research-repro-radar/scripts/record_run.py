import argparse
import datetime as dt
import json
from pathlib import Path

import state


def main():
    state.force_utf8_stdio()
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("recommended", help="Recommended JSON array, or report object with recommended and optional completed_ids")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--today")
    parser.add_argument("--profile", default=str(skill_root / "references" / "profile.json"))
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    fresh_within_days = int(profile.get("adaptive_freshness", {}).get("fresh_within_days", 30))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    report = json.loads(Path(args.recommended).read_text(encoding="utf-8"))
    if isinstance(report, list):
        report = {"recommended": report}
    completed_ids = state.get_completed_ids(report)
    items = report["recommended"]

    added = state.record_seen(args.state_dir, items, "detailed")
    fresh, total = state.record_freshness(args.state_dir, items, today, fresh_within_days, run_id=report.get("run_id"))
    completed = state.remove_completed(args.state_dir, completed_ids)
    print(json.dumps({"recorded_seen": added, "freshness_fresh": fresh, "freshness_total": total, "completed_queue_items": completed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
