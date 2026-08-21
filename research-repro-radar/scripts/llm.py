import argparse
import json
import os
import re
import shutil
import state
import subprocess
import tempfile

TIMEOUT_SEC = 180
# claude の --system-prompt は argv に載る。Windows では claude.CMD 経由で cmd.exe が
# 引数を再解釈するため、改行と < > & | ^ を含めるとコマンドラインがそこで壊れる。
UNSAFE_ARGV = re.compile(r"[<>&|^\r\n]+")
JSON_TYPES = {
    "boolean": bool,
    "string": str,
    "number": (int, float),
    "integer": int,
    "array": list,
    "object": dict,
}


def claude_path():
    return shutil.which("claude")


def codex_path():
    standalone = os.path.join(
        os.path.expanduser("~"), ".codex", "packages", "standalone", "current", "bin", "codex.exe"
    )
    if os.path.isfile(standalone):
        return standalone
    return shutil.which("codex")


def backend():
    name = os.environ.get("RADAR_LLM", "").strip().casefold()
    if name in ("claude", "codex"):
        return name
    if name:
        raise ValueError("RADAR_LLM must be 'claude' or 'codex'")
    if claude_path():
        return "claude"
    if codex_path():
        return "codex"
    raise ValueError("no LLM CLI found; install claude or codex, or set RADAR_LLM")


def _child_env():
    # Claude Code のセッションから起動されると claude -p が入れ子セッションとして拒否される
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def _flatten(text):
    return UNSAFE_ARGV.sub(" ", text).strip()


def _shape(schema):
    properties = schema.get("properties", {})
    return json.dumps({key: value.get("type", "string") for key, value in properties.items()}, ensure_ascii=False)


def _extract_json(text):
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


def _valid(verdict, schema):
    if not isinstance(verdict, dict):
        return False
    for key in schema.get("required", []):
        if key not in verdict:
            return False
    for key, spec in schema.get("properties", {}).items():
        if key not in verdict:
            continue
        expected = JSON_TYPES.get(spec.get("type"))
        if expected is None:
            continue
        if expected is bool:
            if type(verdict[key]) is not bool:
                return False
        elif isinstance(verdict[key], bool) or not isinstance(verdict[key], expected):
            return False
    return True


def _judge_claude(instruction, payload, schema, model, timeout):
    claude = claude_path()
    if claude is None:
        return None
    system_prompt = _flatten(instruction) + " 出力は次の形のJSONオブジェクト1個のみ。前置きも説明も書かないこと: " + _flatten(_shape(schema))
    command = [claude, "-p", "--tools", "", "--system-prompt", system_prompt, "--output-format", "json"]
    if model:
        command.extend(["--model", model])
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_child_env(),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    return _extract_json(str(envelope.get("result", "")))


def _judge_codex(instruction, payload, schema, model, timeout):
    codex = codex_path()
    if codex is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="research-repro-radar-") as run_dir:
            schema_path = os.path.join(run_dir, "verdict.schema.json")
            with open(schema_path, "w", encoding="utf-8") as handle:
                json.dump(schema, handle, ensure_ascii=False)
            command = [
                codex,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--cd", run_dir,
                "--color", "never",
                "--output-schema", schema_path,
            ]
            if model:
                command.extend(["--model", model])
            command.append("-")
            result = subprocess.run(
                command,
                input=instruction + "\n\n" + payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=run_dir,
            )
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        return None


def judge(instruction, payload, schema, model=None, timeout=TIMEOUT_SEC):
    name = backend()
    if name == "claude":
        verdict = _judge_claude(instruction, payload, schema, model, timeout)
    else:
        verdict = _judge_codex(instruction, payload, schema, model, timeout)
    if not _valid(verdict, schema):
        return None
    return verdict


def main():
    state.force_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    args = parser.parse_args()
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}},
        "required": ["ok", "note"],
        "additionalProperties": False,
    }
    verdict = judge(
        "接続確認です。okをtrue、noteに ready と入れて返してください。",
        "[data]\n(なし)\n[/data]",
        schema,
        model=args.model,
    )
    print(json.dumps({"backend": backend(), "verdict": verdict}, ensure_ascii=False))


if __name__ == "__main__":
    main()
