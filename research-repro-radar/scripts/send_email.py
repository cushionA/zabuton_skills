import argparse
import datetime as dt
import hashlib
import json
import os
import smtplib
import ssl
import state
from email.message import EmailMessage
from pathlib import Path


def heartbeat_due(mail_state, heartbeat_days):
    if heartbeat_days <= 0:
        return False
    last = mail_state.get("last_sent_at")
    if not isinstance(last, str) or not last:
        return True
    try:
        parsed = dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - parsed >= dt.timedelta(days=heartbeat_days)


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"environment variable {name} is required")
    return value


def main():
    state.force_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("body")
    parser.add_argument("--subject")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--digest-limit", type=int, default=15)
    parser.add_argument("--heartbeat-days", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    recommended = report.get("recommended")
    if not isinstance(recommended, list):
        raise ValueError("report must contain a recommended array")
    digest_items = report.get("digest", [])
    if not isinstance(digest_items, list):
        raise ValueError("report digest must be an array")
    if args.digest_limit >= 0:
        digest_items = digest_items[: args.digest_limit]

    ids = []
    allowed_categories = {"large_effect", "deep_dive_value", "大きな効果", "深掘り価値"}
    for index, item in enumerate(recommended, 1):
        if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
            raise ValueError(f"recommended item {index} needs id and title")
        if item.get("category") not in allowed_categories:
            raise ValueError(f"recommended item {index} needs a valid category")
        ids.append(str(item["id"]))
    for index, item in enumerate(digest_items, 1):
        if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
            raise ValueError(f"digest item {index} needs id and title")
        ids.append(str(item["id"]))

    mail_state = state.load_mail(args.state_dir)
    today = dt.date.today().isoformat()
    if recommended:
        tier = "detailed"
        default_subject = f"[Research Repro Radar] 今週の注目研究 {len(recommended)}件"
    elif digest_items:
        tier = "digest"
        default_subject = f"[Research Repro Radar] 今週のダイジェスト {len(digest_items)}件 ({today})"
    else:
        tier = "heartbeat"
        default_subject = f"[Research Repro Radar] 今週は該当なし ({today})"
        if not heartbeat_due(mail_state, args.heartbeat_days):
            print(json.dumps({"sent": False, "reason": "nothing_to_report"}))
            return

    body = Path(args.body).read_text(encoding="utf-8")
    subject = args.subject or default_subject
    # detailed は内容だけで指紋を取り、再実行での二重送信を止める。digest と heartbeat は
    # 「毎回届くこと」自体が目的なので、件名に日付が入り指紋が実行ごとに変わる。
    fingerprint = hashlib.sha256((tier + "\n" + subject + "\n" + body + "\n" + "\n".join(ids)).encode("utf-8")).hexdigest()
    if fingerprint in mail_state["sent"]:
        print(json.dumps({"sent": False, "reason": "duplicate", "fingerprint": fingerprint}))
        return
    if args.dry_run:
        print(json.dumps({"sent": False, "reason": "dry_run", "tier": tier, "recommended_count": len(recommended), "digest_count": len(digest_items), "fingerprint": fingerprint}))
        return

    host = required_env("RESEARCH_RADAR_SMTP_HOST")
    port_text = required_env("RESEARCH_RADAR_SMTP_PORT")
    username = required_env("RESEARCH_RADAR_SMTP_USERNAME")
    password = required_env("RESEARCH_RADAR_SMTP_PASSWORD")
    recipients = [item.strip() for item in required_env("RESEARCH_RADAR_EMAIL_TO").split(",") if item.strip()]
    if not recipients:
        raise ValueError("RESEARCH_RADAR_EMAIL_TO has no recipients")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("RESEARCH_RADAR_SMTP_PORT must be an integer") from exc

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("RESEARCH_RADAR_EMAIL_FROM", username)
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as client:
            client.login(username, password)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.starttls(context=context)
            client.login(username, password)
            client.send_message(message)

    mail_state["sent"] = (mail_state["sent"] + [fingerprint])[-200:]
    mail_state["last_sent_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state.save_mail(args.state_dir, mail_state)
    # ダイジェストは「まだ検証していない」印なので既報にしない。既報にすると
    # 次回キューに残した候補が二度と拾われなくなる。
    state.record_seen(args.state_dir, recommended, tier)
    print(json.dumps({"sent": True, "tier": tier, "recommended_count": len(recommended), "digest_count": len(digest_items), "fingerprint": fingerprint}))


if __name__ == "__main__":
    main()
