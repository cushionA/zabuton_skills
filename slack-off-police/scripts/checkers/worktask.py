"""
「いま何の作業をしているはずか」を、監視対象ディレクトリ側から集めるモジュール。

作業テーマは初回設定で固定した文字列なので、それだけでは今日の作業が分からない。
ブランチ名・直近のコミット・未コミットの変更・タスクメモを渡して、判定器に現在地を教える。
"""
import os
import subprocess

MAX_COMMITS = 3
MAX_FILES = 10
MAX_NOTE_CHARS = 600
TASK_FILES = ("TASKS.md", "TODO.md", "todo.md", "TASK.md", "NEXT.md")


def _git(dir_path, args):
    try:
        return subprocess.run(
            ["git"] + args, cwd=dir_path, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None


def _lines(result, limit):
    if result is None or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()][:limit]


def _task_note(dir_path):
    for name in TASK_FILES:
        path = os.path.join(dir_path, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return name, f.read(MAX_NOTE_CHARS)
        except OSError:
            continue
    return "", ""


def collect(dir_path):
    branch = _lines(_git(dir_path, ["rev-parse", "--abbrev-ref", "HEAD"]), 1)
    commits = _lines(_git(dir_path, ["log", "-%d" % MAX_COMMITS, "--format=%cr / %s"]), MAX_COMMITS)
    changed = _lines(_git(dir_path, ["status", "--porcelain"]), MAX_FILES)
    task_file, note = _task_note(dir_path)

    if not (branch or commits or changed or note):
        return None
    return {
        "branch": branch[0] if branch else "",
        "commits": commits,
        "changed": changed,
        "task_file": task_file,
        "task_note": note,
    }


def format_for_prompt(state):
    """
    判定器に渡す1ブロック分のテキスト。コミットメッセージもタスクメモもリポジトリ側の
    文字列なので、信用できないデータとして扱うこと。
    """
    if not state:
        return "(作業状況は取得できず)"

    lines = []
    if state["branch"]:
        lines.append("branch: " + state["branch"])
    if state["commits"]:
        lines.append("recent_commits:")
        lines.extend("- " + c for c in state["commits"])
    if state["changed"]:
        lines.append("uncommitted: " + ", ".join(state["changed"]))
    if state["task_note"]:
        lines.append(state["task_file"] + ":")
        lines.append(state["task_note"])
    return "\n".join(lines) or "(作業状況なし)"
