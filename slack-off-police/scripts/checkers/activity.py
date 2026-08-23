"""
サボり判定に渡す「作業活動」を1か所に集めるモジュール。

  - エディタ側     : checkers/vscode_activity.py（VS Codeの操作履歴）
  - エージェント側 : backends/*.transcript_prompts()（Claude Code / Codex のトランスクリプト）

どちらのソースからも何も取れなければ None を返し、監視ループは git 差分だけで動く。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backends
from checkers import vscode_activity

MAX_ITEMS = 8


def collect(dir_path, since_ts, use_vscode=True, include_text=True):
    editor = vscode_activity.collect(dir_path, since_ts, include_text) if use_vscode else None
    prompts, count = backends.transcript_prompts(dir_path, since_ts, include_text)

    seen, unique = set(), []
    for text in prompts:
        if text not in seen:
            seen.add(text)
            unique.append(text)

    if editor is None and not count:
        return None
    return {
        "editor": editor,
        "agent_prompts": unique[:MAX_ITEMS],
        "agent_sessions": count,
    }


def has_activity(activity):
    if not activity:
        return False
    if activity["agent_sessions"]:
        return True
    return vscode_activity.has_editor_activity(activity["editor"])


def format_for_prompt(activity):
    """
    判定器に渡す1ブロック分のテキスト。中身は信用できないデータとして扱うこと。
    """
    if not activity:
        return "(作業活動の履歴は取得できず)"

    lines = []
    if activity["agent_prompts"]:
        lines.append("ai_chat_prompts:")
        lines.extend("- " + p for p in activity["agent_prompts"])
    elif activity["agent_sessions"]:
        lines.append("ai_chat_prompts_count: " + str(activity["agent_sessions"]))
    editor = vscode_activity.format_editor(activity["editor"])
    if editor:
        lines.append(editor)
    return "\n".join(lines) or "(前回チェック以降の作業活動なし)"


def describe(activity):
    """
    監視ログに1行で出すための要約。
    """
    if not activity:
        return "作業活動なし"

    parts = []
    if activity["agent_sessions"]:
        parts.append("AI質問%d件" % activity["agent_sessions"])
    editor = activity["editor"]
    if editor:
        in_editor = len(editor["chat_prompts"]) or editor["chat_sessions"]
        if in_editor:
            parts.append("エディタ内チャット%d件" % in_editor)
        if editor["saved_files"]:
            parts.append("保存%d件" % len(editor["saved_files"]))
        if editor["opened_files"] and not editor["first_run"]:
            parts.append("閲覧%d件" % len(editor["opened_files"]))
        if editor["ext_counts"]:
            parts.append("拡張イベント%d件" % sum(editor["ext_counts"].values()))
    return ", ".join(parts) or "作業活動なし"
