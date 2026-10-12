import argparse
import json
import os
import re
import shutil
import state
import subprocess
import tempfile

try:
    import jsonschema
except ImportError as exc:
    raise ImportError(
        "research-repro-radar requires jsonschema; install it with "
        "python -m pip install -r research-repro-radar/requirements.txt"
    ) from exc

TIMEOUT_SEC = 180
# claude の --system-prompt は argv に載る。Windows では claude.CMD 経由で cmd.exe が
# 引数を再解釈するため、改行と < > & | ^ を含めるとコマンドラインがそこで壊れる。
UNSAFE_ARGV = re.compile(r"[<>&|^\r\n]+")


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
    try:
        jsonschema.validate(instance=verdict, schema=schema)
    except jsonschema.ValidationError:
        return False
    return isinstance(verdict, dict)


def _judge_claude(instruction, payload, schema, model, timeout):
    claude = claude_path()
    if claude is None:
        return None
    system_prompt = _flatten(instruction) + (
        " 入力JSONのjudgment_inputを判断し、output_schemaの完全なJSON Schemaに従って"
        "JSONオブジェクト1個のみを返すこと。前置きも説明も書かないこと。"
    )
    # schemaにも記号や改行を含められるため、argvへ載せず全文をstdinで渡す。
    stdin_payload = json.dumps(
        {"output_schema": schema, "judgment_input": payload}, ensure_ascii=False
    )
    command = [claude, "-p", "--tools", "", "--system-prompt", system_prompt, "--output-format", "json"]
    if model:
        command.extend(["--model", model])
    try:
        result = subprocess.run(
            command,
            input=stdin_payload,
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
    """Return a schema-valid object, or None for an unavailable/invalid verdict.

    Invalid caller schemas raise jsonschema.SchemaError before any LLM is run.
    """
    if not isinstance(schema, (dict, bool)):
        raise jsonschema.SchemaError("schema must be a JSON object or boolean")
    jsonschema.validators.validator_for(schema).check_schema(schema)
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
