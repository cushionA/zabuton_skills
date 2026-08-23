"""
codex exec (Codex CLI 非対話モード) を呼んでサボり判定させるバックエンド。

codex exec にはシステムプロンプト用のフラグが無いので、指示文もプロンプト本文に混ぜる。
出力形式は --output-schema で固定する。
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile

from ._common import (VERDICT_SCHEMA, clean_prompt, is_agent_noise, iso_to_ts,
                      normalize_verdict, same_tree)

NAME = "codex"
LABEL = "codex exec"
DEFAULT_MODEL = None
TIMEOUT_SEC = 180

STANDALONE = os.path.join(
    os.path.expanduser("~"), ".codex", "packages", "standalone", "current", "bin", "codex.exe"
)


def cli_path():
    if os.path.isfile(STANDALONE):
        return STANDALONE
    return shutil.which("codex")


def run(instruction_lines, payload, model=None):
    codex = cli_path()
    if codex is None:
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="slack-off-police-") as run_dir:
            schema_path = os.path.join(run_dir, "verdict.schema.json")
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(VERDICT_SCHEMA, f, ensure_ascii=False)

            argv = [
                codex, "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--cd", run_dir,
                "--color", "never",
                "--output-schema", schema_path,
            ]
            if model:
                argv.extend(["--model", model])
            argv.append("-")

            result = subprocess.run(
                argv, input="\n".join(instruction_lines) + "\n\n" + payload,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_SEC, cwd=run_dir,
            )
    except (subprocess.SubprocessError, OSError, ValueError):
        return None

    if result.returncode != 0:
        return None
    try:
        return normalize_verdict(json.loads(result.stdout))
    except (TypeError, ValueError):
        return None


SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def transcript_prompts(dir_path, since_ts, include_text=True):
    """
    codex のロールアウトログから、監視対象ディレクトリで投げた質問を拾う。
    (質問文のリスト, 件数) を返す。1ファイルに複数セッションが入ることがあるので、
    session_meta の cwd を追いながら読む。判定器自身の呼び出しは --ephemeral で記録されない。
    """
    prompts, count = [], 0
    for path in glob.glob(os.path.join(SESSIONS_DIR, "*", "*", "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < since_ts:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        in_scope = False
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            payload = obj.get("payload") if isinstance(obj, dict) else None
            if not isinstance(payload, dict):
                continue
            if obj.get("type") == "session_meta":
                cwd = payload.get("cwd")
                in_scope = bool(cwd) and same_tree(cwd, dir_path)
                continue
            if not in_scope or payload.get("type") != "user_message":
                continue
            ts = iso_to_ts(obj.get("timestamp"))
            if ts is None or ts < since_ts:
                continue
            text = payload.get("message")
            if not isinstance(text, str) or not text.strip() or is_agent_noise(text):
                continue
            count += 1
            if include_text:
                prompts.append(clean_prompt(text))
    return prompts, count
