"""
Claude Codeがチャットでヒアリングした内容をCLI引数として受け取り、
.slack-off-police.yaml に保存する。

このスクリプト自体はinput()を使わない。対話はClaude Codeが担当する想定。
"""
import argparse
from config_schema import save_config


def main():
    parser = argparse.ArgumentParser(description="サボり警察の初回設定を保存する")
    parser.add_argument("--dir", required=True, help="監視対象ディレクトリのフルパス")
    parser.add_argument(
        "--progress", default="git_diff", choices=["git_diff", "mtime"],
        help="進捗の証拠にする方法"
    )
    parser.add_argument("--interval", type=int, default=15, help="判定間隔(分)")
    parser.add_argument(
        "--targets", required=True,
        help="カンマ区切りのサボり対象キーワード（例: YouTube,Steam,Twitch）"
    )
    parser.add_argument(
        "--theme", default="",
        help="作業テーマ（例: Rustで自作CLIツールを書く）。サボり判定器がこれを基準に判断する"
    )
    parser.add_argument(
        "--min-lines", type=int, default=3,
        help="この行数未満の変更は進捗とみなさず、サボり判定器に回す"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="claude -p によるサボり判定を使わず、検出したら即サボり扱いにする"
    )
    parser.add_argument(
        "--no-warn", action="store_true",
        help="警告フェーズを挟まず、1回目の検出でいきなりkillする"
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
    )

    print(f"設定を保存しました: {path}")
    print(f"監視対象ディレクトリ: {args.dir}")
    print(f"進捗判定方法: {args.progress} (閾値 {args.min_lines}行)")
    print(f"判定間隔: {args.interval}分")
    print(f"kill対象: {', '.join(targets)}")
    print(f"作業テーマ: {args.theme or '(未設定)'}")
    print(f"サボり判定器: {'無効' if args.no_llm else 'claude -p (sonnet)'}")
    print(f"警告フェーズ: {'無し（即kill）' if args.no_warn else '有り（2回目でkill）'}")


if __name__ == "__main__":
    main()
