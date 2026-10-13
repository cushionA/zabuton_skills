import argparse
import json
import os
import re
import shutil
import state
import subprocess
import tempfile
import time
import hashlib
import sys
import math

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
    if name in ("claude", "codex", "native"):
        return name
    if name:
        raise ValueError("RADAR_LLM must be 'claude', 'codex' or 'native'")
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
        json.dumps(verdict, allow_nan=False)
        jsonschema.validate(instance=verdict, schema=schema)
    except (jsonschema.ValidationError, ValueError, TypeError):
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


# Public diagnostics deliberately retain no arbitrary child text, paths, prompts,
# environment values or exception messages. Signals are an allowlist, not redaction
# regexes: unfamiliar credentials/PII therefore cannot escape into a public log.
SIGNALS = {
    "unsupported_option": r"unexpected argument|unrecognized (?:argument|option)|unknown option",
    "authentication": r"unauthorized|authentication|not logged in|login required|401",
    "model_unavailable": r"model[^\n]{0,100}(?:not found|not supported|unavailable|does not exist|access)|model_not_found",
    "read_only": r"read.only file system|read.only.*(?:error|denied)|permission denied",
    "appserver": r"app.?server",
    "rate_limit": r"rate.limit|quota|429",
}


def _stream_summary(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = value or ""
    return {
        "chars": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "signals": [name for name, pattern in SIGNALS.items()
                    if re.search(pattern, value, re.I)],
        "text": "[withheld]" if value else "",
    }


def _failure_kind(signals):
    for key in ("unsupported_option", "authentication", "model_unavailable",
                "read_only", "appserver", "rate_limit"):
        if key in signals:
            return key
    return "child_nonzero"


def _judge_codex(instruction, payload, schema, model, timeout, diagnostics=None):
    diag = diagnostics if diagnostics is not None else {}
    started = time.monotonic()
    diag.update(stage="discovery", failure=None, child_returncode=None,
                model_requested=bool(model))
    codex = codex_path()
    if codex is None:
        diag.update(failure="cli_not_found", elapsed_sec=0.0)
        return None
    # Hashes identify the actual selected executable without disclosing a user path.
    diag["executable_path_sha256"] = hashlib.sha256(os.path.abspath(codex).encode()).hexdigest()
    diag["executable_realpath_sha256"] = hashlib.sha256(os.path.realpath(codex).encode()).hexdigest()
    diag["platform"] = sys.platform
    try:
        with open(codex, "rb") as handle:
            executable = handle.read()
        diag["executable_sha256"] = hashlib.sha256(executable).hexdigest()
        diag["executable_kind"] = "script_or_wrapper" if executable.startswith(b"#!") or codex.lower().endswith((".cmd", ".bat")) else "binary_or_other"
    except OSError:
        diag["executable_kind"] = "uninspected"
    required = ["--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                "--sandbox", "--cd", "--color", "--output-schema"]
    try:
        # One shared deadline, no retries. Inspect the selected CLI (which may be
        # an environment wrapper) rather than assuming upstream options exist.
        def run(command, **kwargs):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            diag["child_returncode"] = None
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    timeout=remaining, **kwargs)
            diag.update(child_returncode=result.returncode,
                        stdout=_stream_summary(result.stdout),
                        stderr=_stream_summary(result.stderr))
            diag.setdefault("processes", []).append({"stage": diag["stage"],
                "returncode": result.returncode, "stdout": diag["stdout"], "stderr": diag["stderr"]})
            return result

        diag["stage"] = "version"
        version = run([codex, "--version"])
        if version.returncode:
            diag["failure"] = _failure_kind(diag["stderr"]["signals"] + diag["stdout"]["signals"])
            return None
        match = re.search(r"codex(?:-cli)?\s+(\d+\.\d+\.\d+)", version.stdout or "")
        diag["cli_version"] = match.group(1) if match else "unrecognized"
        diag["version_stderr"] = diag["stderr"]
        diag["stage"] = "help"
        help_result = run([codex, "exec", "--help"])
        if help_result.returncode:
            diag["failure"] = _failure_kind(diag["stderr"]["signals"] + diag["stdout"]["signals"])
            return None
        help_text = help_result.stdout or ""
        def supported(flag):
            return re.search(re.escape(flag) + r"(?![\w-])", help_text) is not None
        missing = [flag for flag in required + (["--model"] if model else []) if not supported(flag)]
        diag["unsupported_options"] = missing
        if missing:
            diag["failure"] = "unsupported_option"
            return None
        with tempfile.TemporaryDirectory(prefix="research-repro-radar-") as run_dir:
            schema_path = os.path.join(run_dir, "verdict.schema.json")
            answer_path = os.path.join(run_dir, "answer.json")
            with open(schema_path, "w", encoding="utf-8") as handle:
                json.dump(schema, handle, ensure_ascii=False)
            command = [codex, "exec", "--ephemeral", "--ignore-user-config",
                       "--skip-git-repo-check", "--sandbox", "read-only",
                       "--cd", run_dir, "--color", "never", "--output-schema", schema_path]
            use_file = supported("--output-last-message")
            diag["response_source"] = "last_message_file" if use_file else "stdout"
            if use_file:
                command.extend(["--output-last-message", answer_path])
            if model:
                command.extend(["--model", model])
            command.append("-")
            diag["stage"] = "execution"
            result = run(command, input=instruction + "\n\n" + payload, cwd=run_dir)
            if result.returncode:
                diag["failure"] = _failure_kind(diag["stderr"]["signals"] + diag["stdout"]["signals"])
                return None
            diag["stage"] = "extraction"
            if use_file:
                if not os.path.isfile(answer_path):
                    diag["failure"] = "final_response_missing"
                    return None
                with open(answer_path, encoding="utf-8") as handle:
                    answer = handle.read()
            else:
                # Official non-JSON exec emits its final result on stdout. Never
                # treat JSONL progress events or arbitrary brace substrings as a verdict.
                answer = result.stdout
            if not answer or not answer.strip():
                diag["failure"] = "empty_response"
                return None
            diag["stage"] = "json_parse"
            try:
                return json.loads(answer)
            except (TypeError, ValueError):
                diag["failure"] = "invalid_json"
                return None
    except subprocess.TimeoutExpired as exc:
        diag.update(failure="timeout", stdout=_stream_summary(exc.stdout),
                    stderr=_stream_summary(exc.stderr))
        return None
    except FileNotFoundError:
        diag["failure"] = "cli_not_found" if diag["stage"] != "extraction" else "final_response_missing"
        return None
    except (subprocess.SubprocessError, OSError, ValueError):
        diag["failure"] = "launch_failed" if diag["stage"] in ("version", "help", "execution") else "io_failed"
        return None
    finally:
        diag["elapsed_sec"] = round(time.monotonic() - started, 3)


def judge(instruction, payload, schema, model=None, timeout=TIMEOUT_SEC, diagnostics=None, *, native_request=None, native_response=None):
    """Return a schema-valid object, or None for an unavailable/invalid verdict.

    Invalid caller schemas raise jsonschema.SchemaError before any LLM is run.
    """
    positive_timeout(timeout)
    if not isinstance(schema, (dict, bool)):
        raise jsonschema.SchemaError("schema must be a JSON object or boolean")
    jsonschema.validators.validator_for(schema).check_schema(schema)
    try:
        name = backend()
    except ValueError:
        if diagnostics is not None:
            diagnostics.update(stage="discovery", failure="backend_unavailable")
            return None
        raise
    if diagnostics is not None:
        diagnostics["backend"] = name
    if name == "native":
        # Native agents are spawned by the host orchestrator, never by Python.
        # Consume only the response for this exact prepared task; no CLI fallback.
        import native_judge
        if model is not None:
            if diagnostics is not None:
                diagnostics.update(stage="native_validation", failure="native_model_selection_external")
            return None
        if not isinstance(native_request, dict):
            if diagnostics is not None:
                diagnostics.update(stage="native_validation", failure="native_request_required")
            return None
        try:
            actual_task = {key: native_request.get(key) for key in ("instruction", "payload", "schema")}
            expected_task = {"instruction": instruction, "payload": payload, "schema": schema}
            same_task = native_judge._canonical(actual_task) == native_judge._canonical(expected_task)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            same_task = False
        if not same_task:
            if diagnostics is not None:
                diagnostics.update(stage="native_validation", failure="native_task_mismatch")
            return None
        result = native_judge.validate_response(native_request, native_response)
        if diagnostics is not None:
            diagnostics.update(result["diagnostics"])
        return result["verdict"]
    if name == "claude":
        verdict = _judge_claude(instruction, payload, schema, model, timeout)
    else:
        if diagnostics is None:
            verdict = _judge_codex(instruction, payload, schema, model, timeout)
        else:
            verdict = _judge_codex(instruction, payload, schema, model, timeout, diagnostics)
    if not _valid(verdict, schema):
        if diagnostics is not None and not diagnostics.get("failure"):
            diagnostics.update(stage="schema_validation", failure="schema_violation" if verdict is not None else "verdict_unavailable")
        return None
    if diagnostics is not None:
        diagnostics.update(stage="complete", failure=None)
    return verdict


def judge_with_diagnostics(instruction, payload, schema, model=None, timeout=TIMEOUT_SEC, *, native_request=None, native_response=None):
    diagnostics = {}
    verdict = judge(instruction, payload, schema, model, timeout, diagnostics=diagnostics, native_request=native_request, native_response=native_response)
    return {"verdict": verdict, "diagnostics": diagnostics}


PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["ok", "note"], "additionalProperties": False,
}


def positive_timeout(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout must be positive and finite")
    return seconds


def preflight(model=None, timeout=TIMEOUT_SEC):
    result = judge_with_diagnostics(
        "接続確認です。okをtrue、noteに ready と入れて返してください。",
        "[data]\n(なし)\n[/data]", PROBE_SCHEMA, model, timeout)
    if result["verdict"] is not None and result["verdict"] != {"ok": True, "note": "ready"}:
        result["verdict"] = None
        result["diagnostics"].update(stage="probe_validation", failure="probe_mismatch")
    return result


def main():
    state.force_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=positive_timeout, default=TIMEOUT_SEC)
    # Parse after all arguments are registered.
    args = parser.parse_args()
    result = preflight(args.model, args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verdict"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
