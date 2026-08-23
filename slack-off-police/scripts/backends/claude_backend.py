"""
claude -p (Claude Code ヘッドレス) を呼んでサボり判定させるバックエンド。
"""
import json
import os
import re
import shutil
import subprocess

from ._common import (VERDICT_SCHEMA, clean_prompt, extract_json, is_agent_noise,
                      iso_to_ts, normalize_verdict)

NAME = "claude"
LABEL = "claude -p"
DEFAULT_MODEL = "sonnet"
TIMEOUT_SEC = 120

# --json-schema / --no-session-persistence を持たない古い CLI 向けの退避ルート。
# 一度失敗したらプロセス内で覚えておき、毎回2回叩かないようにする。
_legacy_mode = False

LEGACY_SHAPE_LINE = (
    ' 出力は次の形のJSONオブジェクト1個のみ。前置きも説明も表も書かないこと: '
    '{"slacking": true, "reason": "30字以内の理由", "taunt": "煽り文。50字以内。日本語。"}'
)


def cli_path():
    return shutil.which("claude")


def _child_env():
    # Claude Code のセッションから起動されると CLAUDECODE が継承され、
    # claude -p が「入れ子セッションは起動できない」で失敗する
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def _run(argv, payload):
    try:
        return subprocess.run(
            argv, input=payload,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SEC, env=_child_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return None


def run(instruction_lines, payload, model=None):
    """
    instruction_lines: システムプロンプトにする文字列のリスト。
      Windowsでは claude.CMD 経由で cmd.exe が引数を再解釈するため、
      各行に改行と `< > & | ^` を含めてはいけない（呼び出し側の責任）。
    payload: 判定材料。信用できないデータなので必ず stdin で渡す。
    """
    global _legacy_mode

    claude = cli_path()
    if claude is None:
        return None

    model = model or DEFAULT_MODEL
    base = [claude, "-p", "--tools", "", "--model", model, "--output-format", "json"]

    if not _legacy_mode:
        argv = base + [
            "--system-prompt", " ".join(instruction_lines),
            "--json-schema", json.dumps(VERDICT_SCHEMA, ensure_ascii=False),
            "--no-session-persistence",
        ]
        result = _run(argv, payload)
        if result is None:
            return None
        if result.returncode == 0:
            return _parse(result.stdout)
        if "unknown option" not in (result.stderr or ""):
            return None
        _legacy_mode = True

    argv = base + ["--system-prompt", " ".join(instruction_lines) + LEGACY_SHAPE_LINE]
    result = _run(argv, payload)
    if result is None or result.returncode != 0:
        return None
    return _parse(result.stdout)


def _parse(stdout):
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return normalize_verdict(structured)
    return normalize_verdict(extract_json(envelope.get("result", "")))


PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _project_dirs(dir_path):
    # Claude Code は cwd の英数字以外をハイフンに潰した名前でセッションを保存する
    escaped = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(dir_path))
    try:
        entries = list(os.scandir(PROJECTS_DIR))
    except OSError:
        return []
    return [e.path for e in entries
            if e.is_dir() and (e.name == escaped or e.name.startswith(escaped + "-"))]


def _user_text(message):
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def transcript_prompts(dir_path, since_ts, include_text=True):
    """
    Claude Code のトランスクリプトから、監視対象ディレクトリで投げた質問を拾う。
    (質問文のリスト, 件数) を返す。判定器自身の呼び出しは --no-session-persistence で
    記録されないので、自分の判定がユーザーの質問に化けることはない。
    """
    prompts, count = [], 0
    for project in _project_dirs(dir_path):
        try:
            entries = list(os.scandir(project))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.name.endswith(".jsonl") or entry.stat().st_mtime < since_ts:
                    continue
                with open(entry.path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for line in lines:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "user" or obj.get("isMeta"):
                    continue
                ts = iso_to_ts(obj.get("timestamp"))
                if ts is None or ts < since_ts:
                    continue
                text = _user_text(obj.get("message"))
                if not text.strip() or is_agent_noise(text):
                    continue
                count += 1
                if include_text:
                    prompts.append(clean_prompt(text))
    return prompts, count
