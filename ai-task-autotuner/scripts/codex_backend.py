"""One isolated ``codex exec`` call used as a pseudo API.

The CLI keeps its authentication home, but loads neither the user's config nor
project instructions. Tools are disabled where the CLI exposes a switch; any
tool item in the event stream makes the call fail. Managed instructions imposed
by the installation can still apply. This is not an OS-level process sandbox.

Codex does not expose a per-call output-token limit or a thinking-off switch.
``max_tokens`` and ``thinking`` are accepted for the common adapter interface,
but are not represented as enforced controls or reported token measurements.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


QUOTA_MARKERS = (
    "usage limit", "usage_limit", "session limit", "hit your limit",
    "limit reached", "insufficient_quota", "quota exhausted", "resets ",
    "credit balance",
)
TRANSIENT_MARKERS = (
    "rate limit", "rate_limit", "429", "overload", "temporarily", "timeout",
    "timed out", "connection", "stream disconnected", "502", "503", "504",
    "try again",
)
AUTH_MARKERS = (
    "401 unauthor", "status=401", "http 401", "http error: 401",
    "invalid_api_key", "authentication_error", "invalid authentication",
    "access token could not be refreshed", "refresh token", "token has expired",
    "please sign in", "sign in again", "please log in", "not logged in",
)
# These switches are available in Codex CLI 0.159.2. Config loading and rules
# loading are separately disabled below; disabling features is not a substitute
# for checking the emitted items for unexpected tool use.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "view_image", "multi_agent", "multi_agent_v2",
    "apps", "plugins", "hooks", "skill_search", "code_mode",
    "browser_use", "browser_use_external", "computer_use", "image_generation",
    "memories", "sleep_tool", "workspace_dependencies", "tool_suggest",
    "in_app_browser", "in_app_local_automation", "remote_plugin",
)
ISOLATION_CONFIG = (
    'approval_policy="never"', 'web_search="disabled"', 'mcp_servers={}',
    # The CLI initializes this runtime even when code_mode is disabled. Turning
    # the host off emits a startup error before inference. Keep the runtime
    # available, while code_mode and all model-visible tools remain disabled.
    # A local-provider probe on 0.159.2 verified that the request has tools: [].
    'features.code_mode_host=true',
    'project_doc_max_bytes=0', 'skills.include_instructions=false',
    'include_environment_context=false', 'include_apps_instructions=false',
    'include_collaboration_mode_instructions=false',
    'developer_instructions="Return only the requested answer. Do not invoke tools, '
    'inspect files, browse, or delegate to other agents."',
)


def _failure(kind, message, *, retryable=False, **fields):
    return {"ok": False, "error_kind": kind, "error": str(message)[:800],
            "retryable": retryable, **fields}


def _counter(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid Codex usage counter: {name}")
    return value


def _usage(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid Codex usage object")
    total = _counter(raw.get("input_tokens"), "input_tokens")
    cached = _counter(raw.get("cached_input_tokens"), "cached_input_tokens")
    output = _counter(raw.get("output_tokens"), "output_tokens")
    if total is not None and cached is not None and cached > total:
        raise ValueError("cached Codex input exceeds total input")
    return {"input": total - cached if total is not None and cached is not None else None,
            "output": output, "cache_read": cached, "cache_write": None, "thinking": None}


def _events(stdout):
    """Parse exactly one turn, retaining its cumulative usage only once."""
    messages, final_messages, errors = [], [], []
    usage, thread_id, tool_error = None, None, None
    completed = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError("invalid Codex JSONL event")
        kind = event["type"]
        if kind == "thread.started":
            thread_id = event.get("thread_id")
        elif kind == "turn.started":
            pass
        elif kind == "turn.completed":
            completed += 1
            if completed > 1:
                raise ValueError("multiple Codex turns in a single pseudo-API call")
            usage = _usage(event.get("usage"))
        elif kind in ("turn.failed", "error"):
            detail = event.get("error", event.get("message", event))
            errors.append(detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                raise ValueError("invalid Codex item")
            item_kind = item.get("type")
            if item_kind == "error":
                # Codex also emits CLI/provider diagnostics as completed
                # items, rather than only as top-level error events. Keep
                # those messages for quota/transient classification.
                detail = item.get("message", item.get("text", item.get("error", item)))
                errors.append(detail if isinstance(detail, str)
                              else json.dumps(detail, ensure_ascii=False))
                continue
            if item_kind not in ("agent_message", "reasoning"):
                # Do not accept a successful result from an agent that used any
                # tools, even if the eventual final answer looks valid. Finish
                # parsing so any reported usage for the rejected call survives.
                tool_error = tool_error or f"Codex pseudo-API attempted tool item: {item_kind}"
                continue
            if kind == "item.completed" and item_kind == "agent_message":
                value = item.get("text")
                if not isinstance(value, str):
                    raise ValueError("invalid Codex agent message")
                if item.get("phase") != "commentary":
                    messages.append(value)
                if item.get("phase") == "final_answer":
                    final_messages.append(value)
        else:
            raise ValueError(f"unexpected Codex event type: {kind}")
    if tool_error:
        return _failure("harness", tool_error, usage=usage, thread_id=thread_id,
                        num_turns=completed)
    return {"text": (final_messages or messages or [None])[-1], "usage": usage,
            "thread_id": thread_id, "num_turns": completed, "errors": errors}


def _error_record(message, **fields):
    lower = message.lower()
    # CLI auth failures can mention websocket connections or "try again" in
    # their guidance. They require an account action, not transport retries.
    if any(marker in lower for marker in AUTH_MARKERS):
        return _failure("harness", message, **fields)
    if any(marker in lower for marker in QUOTA_MARKERS):
        return _failure("quota", message, **fields)
    transient = any(marker in lower for marker in TRANSIENT_MARKERS)
    return _failure("api" if transient else "harness", message, retryable=transient, **fields)


def attempt(prompt, system, model, effort, json_schema, timeout, extra_args,
            max_tokens=None, thinking=None):
    """Return a call record without raising; never run a resumed Codex thread."""
    codex = shutil.which("codex")
    if codex is None:
        return _failure("harness", "codex CLI not found")
    if extra_args:
        return _failure("harness", "Codex extra_args are not supported: they can override isolation")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="aitt-codex-") as directory:
            system_file = Path(directory) / "system.txt"
            system_file.write_text(system, encoding="utf-8")
            args = [codex, "exec", "--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                    "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                    "--model", model]
            for setting in ISOLATION_CONFIG:
                args += ["-c", setting]
            args += ["-c", "model_instructions_file=" + json.dumps(str(system_file))]
            if effort:
                args += ["-c", "model_reasoning_effort=" + json.dumps(effort)]
            for feature in DISABLED_FEATURES:
                args += ["--disable", feature]
            if json_schema is not None:
                schema_file = Path(directory) / "response.schema.json"
                schema_file.write_text(json.dumps(json_schema, ensure_ascii=False), encoding="utf-8")
                args += ["--output-schema", str(schema_file)]
            args += ["-"]
            # Preserve auth and endpoint environment, including CODEX_HOME.
            result = subprocess.run(args, input=prompt, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout,
                                    env=dict(os.environ), cwd=directory)
    except subprocess.TimeoutExpired:
        return _failure("timeout", f"timeout after {timeout}s", retryable=True,
                        wall_ms=int((time.monotonic() - started) * 1000))
    except (OSError, ValueError, TypeError) as exc:
        return _failure("harness", str(exc), wall_ms=int((time.monotonic() - started) * 1000))
    wall_ms = int((time.monotonic() - started) * 1000)
    try:
        parsed = _events(result.stdout)
    except (ValueError, TypeError) as exc:
        # CLI diagnostics may be plain text before JSONL can be initialized.
        diagnostic = (result.stderr or result.stdout or "")[-600:]
        if result.returncode != 0:
            return _error_record(f"Codex rc={result.returncode}: {diagnostic}", wall_ms=wall_ms)
        return _failure("harness", f"invalid Codex JSONL: {exc}", retryable=True, wall_ms=wall_ms)
    rec = {"wall_ms": wall_ms, "api_ms": None, "cli_ms": None, "ttft_ms": None,
           "usage": parsed.get("usage"), "cost_usd": None, "cost_basis": None,
           "model_served": None, "stop_reason": None, "text": parsed.get("text"),
           "structured": None, "num_turns": parsed.get("num_turns"),
           "thread_id": parsed.get("thread_id")}
    if parsed.get("ok") is False:
        return {**rec, **parsed}
    if result.returncode != 0 or parsed["errors"]:
        diagnostic = " ".join(parsed["errors"]) + " " + (result.stderr or "")[-600:]
        return _error_record(f"Codex rc={result.returncode}: {diagnostic.strip()}", **rec)
    if parsed["num_turns"] != 1:
        return _failure("harness", "Codex stream has no completed turn", retryable=True, **rec)
    if not (rec["text"] or "").strip():
        return _failure("empty", "empty reply", retryable=True, **rec)
    rec["ok"] = True
    return rec
