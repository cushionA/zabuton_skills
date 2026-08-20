"""
サボり警察 監視ループ本体。

使い方:
    python monitor.py --dir "C:/path/to/project" [--audio "C:/path/to/scold.mp3"] [--log "C:/path/to/police.log"]

事前に setup.py で .slack-off-police.yaml が作られている必要がある。
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config_schema import load_config
from checkers.progress import check_progress
from checkers.targets import find_matches
from checkers.judge import judge
from punisher.messages import get_insult
from punisher.audio import play_audio
from punisher.kill import kill_matches
from punisher.popup import show_popup

REQUIRED_KEYS = ("interval_min", "targets", "progress_method")
STRIKES_TO_KILL = 2

_log_file = None


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def punish(matched, taunt, audio, kill_now):
    names = sorted(set((w["process_name"] or w["title"]) for w in matched))
    header = "サボり警察 (最終警告)" if kill_now else "サボり警察 (警告)"
    body = taunt
    if kill_now:
        body += "\n\n対象を強制終了します: " + ", ".join(names)
    else:
        body += "\n\n次の判定までに進捗が無ければ落とす: " + ", ".join(names)

    show_popup(header, body)
    if audio:
        play_audio(audio)

    if not kill_now:
        return
    for w in kill_matches(matched):
        log(f"  → 強制終了: {w['process_name']} (PID {w['pid']}) / \"{w['title']}\"")


def main():
    global _log_file
    parser = argparse.ArgumentParser(description="サボり警察 監視ループ")
    parser.add_argument("--dir", required=True, help="監視対象ディレクトリのフルパス")
    parser.add_argument("--audio", default=None, help="サボり検知時に鳴らす音声ファイルのパス（任意）")
    parser.add_argument("--log", default=None,
                        help="ログの書き出し先（任意）。監視対象ディレクトリ内は指定しないこと")
    args = parser.parse_args()

    if args.log:
        # ログを監視対象ディレクトリに置くと、自分の書き込みが「進捗」と誤判定される
        log_dir = os.path.abspath(os.path.dirname(args.log) or ".")
        if os.path.commonpath([log_dir, os.path.abspath(args.dir)]) == os.path.abspath(args.dir):
            print("エラー: --log は監視対象ディレクトリの外を指定してください。")
            return
        _log_file = open(args.log, "a", encoding="utf-8")

    config = load_config(args.dir)
    if config is None:
        log("設定が見つかりません。先に setup.py を実行してください。")
        return
    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        log(f"設定が壊れています（不足キー: {', '.join(missing)}）。setup.py を再実行してください。")
        return
    if not config["targets"]:
        log("kill対象が空です。setup.py を再実行してください。")
        return

    interval_sec = config["interval_min"] * 60
    targets = config["targets"]

    log("サボり警察、監視開始します。")
    log(f"対象ディレクトリ: {args.dir}")
    log(f"判定間隔: {config['interval_min']}分 / kill対象: {', '.join(targets)}")
    log(f"作業テーマ: {config['work_theme'] or '(未設定)'}")
    log(f"サボり判定器: {'claude -p (sonnet)' if config['use_llm'] else '無効'}")

    last_check_ts = time.time()
    strikes = 0

    while True:
        time.sleep(interval_sec)
        now_ts = time.time()

        progressed, diff_text = check_progress(
            args.dir, last_check_ts, config["progress_method"], config["min_lines"]
        )
        matched = find_matches(targets)

        if progressed:
            log("進捗あり。今回は見逃す。")
            strikes = 0
        elif not matched:
            log("進捗なし。ただしサボり対象は検出されず。今回は見逃す。")
        else:
            titles = [w["title"] for w in matched]
            log(f"進捗なし。サボり対象検出: {', '.join(titles)}")

            verdict = judge(config["work_theme"], titles, diff_text) if config["use_llm"] else None

            if config["use_llm"] and verdict is None:
                # 判定器を呼べなかった。誤爆のほうが害が大きいのでkillしない側に倒す
                log("判定器を呼べなかったため今回は見逃す（claudeコマンド不在 or 実行失敗）。")
            elif verdict is not None and not verdict["slacking"]:
                log(f"サボりではないと判定: {verdict['reason']}")
                strikes = 0
            else:
                strikes += 1
                taunt = verdict["taunt"] if verdict else get_insult(", ".join(titles))
                if verdict:
                    log(f"サボり判定: {verdict['reason']}")
                log(f"{taunt} (strike {strikes}/{STRIKES_TO_KILL})")
                kill_now = (not config["warn_first"]) or strikes >= STRIKES_TO_KILL
                punish(matched, taunt, args.audio, kill_now)
                if kill_now:
                    strikes = 0

        last_check_ts = now_ts


if __name__ == "__main__":
    main()
