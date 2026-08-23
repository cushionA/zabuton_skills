"""
サボり警察 監視ループ本体。

使い方:
    python monitor.py --dir "C:/path/to/project" [--audio "C:/path/to/scold.mp3"] [--log "C:/path/to/police.log"]

事前に setup.py で .slack-off-police.yaml が作られている必要がある。

「作業しているか」の判定はLLMに任せ、警告とkillのエスカレーションだけを機械的に処理する。
エディタが閉じられたら作業セッション終了とみなして監視自体を終わる（休憩はユーザーが自分で止める）。
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backends import resolve_backend
from config_schema import load_config
from checkers.progress import check_progress
from checkers.targets import find_matches
from checkers.judge import judge
from checkers import activity as work_activity
from checkers import worktask
from checkers.timeline import Timeline
from checkers.vscode_activity import is_editor_running
from punisher.messages import get_insult
from punisher.audio import play_audio
from punisher.kill import kill_matches
from punisher.popup import show_popup

REQUIRED_KEYS = ("interval_min", "targets", "progress_method")
STRIKES_TO_KILL = 2

_log_file = None


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    if sys.stdout:
        print(line)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def punish(matched, taunt, audio, kill_now):
    header = "サボり警察 (最終警告)" if kill_now else "サボり警察 (警告)"
    body = taunt
    if matched:
        names = sorted(set((w["process_name"] or w["title"]) for w in matched))
        if kill_now:
            body += "\n\n対象を強制終了します: " + ", ".join(names)
        else:
            body += "\n\n次の判定でも作業していなければ落とす: " + ", ".join(names)
    else:
        body += "\n\n（kill対象は開かれていないので警告だけ）"

    show_popup(header, body)
    if audio:
        play_audio(audio)

    if not kill_now or not matched:
        return
    for w in kill_matches(matched):
        log("  → 強制終了: %s (PID %s) / \"%s\"" % (w["process_name"], w["pid"], w["title"]))


def collect_activity(config, dir_path, since_ts):
    try:
        return work_activity.collect(dir_path, since_ts,
                                     config["use_vscode"], config["chat_text"])
    except OSError:
        return None


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
        log("設定が壊れています（不足キー: %s）。setup.py を再実行してください。" % ", ".join(missing))
        return
    if not config["targets"]:
        log("kill対象が空です。setup.py を再実行してください。")
        return

    interval_sec = config["interval_min"] * 60
    targets = config["targets"]
    backend = resolve_backend(config["agent"]) if config["use_llm"] else None

    log("サボり警察、監視開始します。")
    log("対象ディレクトリ: %s" % args.dir)
    log("判定間隔: %d分 / kill対象: %s" % (config["interval_min"], ", ".join(targets)))
    log("作業テーマ: %s" % (config["work_theme"] or "(未設定)"))
    if not config["use_llm"]:
        log("作業判定: LLM無効（進捗も操作履歴も無ければ警告）")
    elif backend is None:
        log("作業判定: CLIが見つからず判定できません（この状態では誰もkillされません）")
    else:
        log("作業判定: %s (%s)" % (backend.LABEL, config["model"] or backend.DEFAULT_MODEL or "既定モデル"))
    log("VS Code操作履歴: %s / AIチャット履歴: %s"
        % ("使う" if config["use_vscode"] else "使わない",
           "本文つき" if config["chat_text"] else "件数のみ"))
    log("例外ルール: %s" % (config["rules"] or "(なし)"))
    log("エディタ連動: %s" % ("有効（閉じたら監視終了）" if config["editor_gate"] else "無効"))

    timeline = Timeline(config["interval_min"])
    last_check_ts = time.time()
    strikes = 0
    editor_seen = False

    while True:
        time.sleep(interval_sec)
        now_ts = time.time()

        if config["editor_gate"]:
            running = is_editor_running()
            if running:
                editor_seen = True
            elif editor_seen:
                log("エディタが閉じられました。作業セッション終了とみなして監視を終わります。")
                return
            else:
                log("エディタがまだ起動していないので待機（判定もしない）。")
                last_check_ts = now_ts
                continue

        progressed, diff_text = check_progress(
            args.dir, last_check_ts, config["progress_method"], config["min_lines"]
        )
        matched = find_matches(targets)
        activity = collect_activity(config, args.dir, last_check_ts)
        progress_note = "あり" if progressed else "なし（閾値 %d行）" % config["min_lines"]

        log("進捗: %s / 作業の様子: %s" % (progress_note, work_activity.describe(activity)))
        if matched:
            log("kill対象を検出: %s" % ", ".join(w["title"] for w in matched))

        verdict = None
        if config["use_llm"]:
            verdict = judge({
                "work_theme": config["work_theme"],
                "rules": config["rules"],
                "context": timeline.format_for_prompt(strikes, progress_note),
                "work_state": worktask.collect(args.dir),
                "window_titles": [w["title"] for w in matched],
                "diff": diff_text,
                "activity": activity,
            }, agent=config["agent"], model=config["model"])
            if verdict:
                log("いまの様子: %s" % verdict["doing"])

        if config["use_llm"] and verdict is None:
            # 判定器を呼べなかった。誤爆のほうが害が大きいので何もしない側に倒す
            log("判定器を呼べなかったため今回は見逃す（CLI不在 or 実行失敗）。")
            timeline.record("判定不能")
        elif verdict is None and (progressed or work_activity.has_activity(activity)):
            # LLM無効時の機械的な判定
            log("LLM無効。進捗または操作履歴を確認したので見逃す。")
            timeline.record("作業中", work_activity.describe(activity))
            strikes = 0
        elif verdict is not None and not verdict["slacking"]:
            log("作業中と判定: %s" % verdict["reason"])
            timeline.record("作業中", verdict["doing"], verdict["reason"])
            strikes = 0
        else:
            strikes += 1
            taunt = verdict["taunt"] if verdict else get_insult(", ".join(targets))
            if verdict:
                log("作業していないと判定: %s" % verdict["reason"])
            log("%s (strike %d/%d)" % (taunt, strikes, STRIKES_TO_KILL))
            timeline.record("サボり判定",
                            verdict["doing"] if verdict else "",
                            verdict["reason"] if verdict else "")
            kill_now = (not config["warn_first"]) or strikes >= STRIKES_TO_KILL
            if kill_now and not matched:
                log("kill対象のウィンドウが無いため、今回は警告だけにする。")
            punish(matched, taunt, args.audio, kill_now)
            if kill_now and matched:
                strikes = 0

        last_check_ts = now_ts


if __name__ == "__main__":
    main()
