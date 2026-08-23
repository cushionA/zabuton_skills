"""
エージェント（Claude Code / Codex）がチャットでヒアリングした内容をCLI引数として受け取り、
.slack-off-police.yaml に保存する。

このスクリプト自体はinput()を使わない。対話はスキルを起動したエージェントが担当する想定。
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backends import BACKENDS, available_agents, detect_agent
from config_schema import save_config


def main():
    parser = argparse.ArgumentParser(description="サボり警察の初回設定を保存する")
    parser.add_argument("--dir", required=True, help="監視対象ディレクトリのフルパス")
    parser.add_argument(
        "--progress", default="git_diff", choices=["git_diff", "mtime"],
        help="進捗の証拠にする方法"
    )
    parser.add_argument("--interval", type=int, default=10, help="判定間隔(分)")
    parser.add_argument(
        "--targets", required=True,
        help="カンマ区切りのサボり対象キーワード（例: YouTube,Steam,Twitch）"
    )
    parser.add_argument(
        "--theme", default="",
        help="作業テーマ（例: Rustで自作CLIツールを書く）。サボり判定器がこれを基準に判断する"
    )
    parser.add_argument(
        "--agent", default="auto", choices=["auto"] + list(BACKENDS),
        help="サボり判定に使うCLI。スキルを起動したエージェント自身を指定すること"
    )
    parser.add_argument(
        "--model", default=None,
        help="判定に使うモデル名（省略時は各CLIの既定）"
    )
    parser.add_argument(
        "--rules", default="",
        help="ユーザーが決めた例外ルールを自然言語で（例: 昼休みは自由、25分作業したら5分休憩可）"
    )
    parser.add_argument(
        "--min-lines", type=int, default=3,
        help="この行数未満の変更は進捗とみなさず、サボり判定器に回す"
    )
    parser.add_argument(
        "--no-editor-gate", action="store_true",
        help="エディタが閉じられても監視を続ける（既定は閉じたら監視終了）"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="LLMによるサボり判定を使わず、検出したら即サボり扱いにする"
    )
    parser.add_argument(
        "--no-warn", action="store_true",
        help="警告フェーズを挟まず、1回目の検出でいきなりkillする"
    )
    parser.add_argument(
        "--no-vscode", action="store_true",
        help="VS Codeの操作履歴（資料閲覧・保存・エディタ内チャット）を判定材料にしない"
    )
    parser.add_argument(
        "--no-chat-text", action="store_true",
        help="チャットやAIエージェントへの質問文をLLMに渡さず、件数だけを渡す"
    )
    args = parser.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    if not targets:
        print("エラー: --targets が空です。最低1つは指定してください。")
        return

    path = save_config(
        args.dir, args.progress, args.interval, targets,
        work_theme=args.theme, use_llm=not args.no_llm,
        warn_first=not args.no_warn, min_lines=args.min_lines,
        agent=args.agent, model=args.model,
        use_vscode=not args.no_vscode, chat_text=not args.no_chat_text,
        rules=args.rules,
        editor_gate=not args.no_editor_gate,
    )

    resolved = detect_agent() if args.agent == "auto" else args.agent
    judge_label = "無効" if args.no_llm else "%s (%s)" % (resolved or "判定不能", args.model or "既定モデル")

    print("設定を保存しました: %s" % path)
    print("監視対象ディレクトリ: %s" % args.dir)
    print("進捗判定方法: %s (閾値 %d行)" % (args.progress, args.min_lines))
    print("判定間隔: %d分" % args.interval)
    print("kill対象: %s" % ", ".join(targets))
    print("作業テーマ: %s" % (args.theme or "(未設定)"))
    print("サボり判定器: %s" % judge_label)
    print("利用可能なCLI: %s" % (", ".join(available_agents()) or "なし"))
    print("VS Code操作履歴: %s" % ("使わない" if args.no_vscode else "使う"))
    print("AIチャット履歴: %s" % ("件数のみ" if args.no_chat_text else "本文つき"))
    print("例外ルール: %s" % (args.rules or "(なし)"))
    print("エディタ連動: %s" % ("無効" if args.no_editor_gate else "有効（閉じたら監視終了）"))
    print("警告フェーズ: %s" % ("無し（即kill）" if args.no_warn else "有り（2回目でkill）"))


if __name__ == "__main__":
    main()
