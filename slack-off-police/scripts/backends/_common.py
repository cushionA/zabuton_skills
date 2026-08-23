"""
バックエンド共通のユーティリティ。
"""
import json
import os
import re
from datetime import datetime

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "slacking": {"type": "boolean"},
        "doing": {"type": "string", "maxLength": 100},
        "reason": {"type": "string", "maxLength": 100},
        "taunt": {"type": "string", "maxLength": 200},
    },
    "required": ["slacking", "doing", "reason", "taunt"],
    "additionalProperties": False,
}


def extract_json(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if not brace:
        return None
    try:
        return json.loads(brace.group(0))
    except ValueError:
        return None


def normalize_verdict(verdict):
    """
    LLMの出力を {"slacking": bool, "reason": str, "taunt": str} に正規化する。
    形が合わなければ None（呼び出し側はkillしない側に倒すこと）。
    """
    if not isinstance(verdict, dict) or "slacking" not in verdict:
        return None
    return {
        "slacking": bool(verdict.get("slacking")),
        "doing": str(verdict.get("doing", ""))[:100],
        "reason": str(verdict.get("reason", ""))[:100],
        "taunt": str(verdict.get("taunt", ""))[:200],
    }


def iso_to_ts(text):
    """
    トランスクリプトの ISO8601 文字列を epoch 秒にする。読めなければ None。
    """
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def same_tree(a, b):
    try:
        a, b = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    except (OSError, ValueError):
        return False
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def clean_prompt(text, limit=300):
    return re.sub(r'[\x00-\x1f\x7f]+', " ", str(text)).strip()[:limit]


NOISE_TAG = re.compile(r"^<[A-Za-z][A-Za-z0-9_-]*>")


def is_agent_noise(text):
    """
    IDE統合やフックがユーザー発言として差し込むタグ付きメッセージを弾く。
    Codexの <ide_opened_file> や Claude Code の <command-name> 等が該当する。
    """
    return bool(NOISE_TAG.match(str(text).lstrip()))
