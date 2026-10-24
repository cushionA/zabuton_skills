"""One model call, measured the same way on every backend.

Backends:
  auto - choose the current Codex/Claude host, or the sole installed CLI
  codex - `codex exec` (Luna by default)
  claude / cli - `claude -p` (Haiku by default; cli is a legacy alias)
  api  - Anthropic Messages API via the official `anthropic` SDK (needs credentials)

Every call returns a record with text, usage (input/output/cache/thinking tokens),
cost, API-side latency and wall time, attempts and error class, so experiments can
be compared on the same footing. Usage that a backend does not report is None
(never guessed).

CLI:
  python llm_call.py --calibrate [--backend codex|claude] [--model MODEL]
  python llm_call.py --effort low --system-file sys.txt < prompt.txt
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MODEL_ALIASES, estimate_cost, extract_json, sha256_text  # noqa: E402
from codex_backend import attempt as _codex_attempt  # noqa: E402

DEFAULT_SYSTEM = "Follow the user's instructions exactly."
DEFAULT_EFFORT = "low"
BACKENDS = ("auto", "codex", "claude", "cli", "api")
DEFAULT_MODELS = {"codex": "gpt-6-luna", "claude": "haiku", "api": "haiku"}
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
# Subscription/session quota exhaustion: retrying only burns time until the reset.
QUOTA_MARKERS = ("session limit", "usage limit", "hit your limit", "limit reached", "resets ")
# thinking="off" per model family on the api backend (cli cannot turn adaptive thinking off)
API_THINKING_OFF = {"claude-haiku-5-5": {"type": "disabled"}, "claude-sonnet-5-5": {"type": "between_tools"}}
# Flags that strip Claude Code's own context so the call behaves like a bare API
# request: no tools, no settings/CLAUDE.md, no MCP, no skills, no saved session.
CLI_ISOLATION = [
    "--tools", "",
    "--setting-sources", "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--exclude-dynamic-system-prompt-sections",
]


def resolve_backend(backend="auto"):
    """Resolve once before a run; never switch providers after a failed call."""
    backend = backend or "auto"
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; choose {', '.join(BACKENDS)}")
    if backend != "auto":
        return "claude" if backend == "cli" else backend
    override = os.environ.get("AITT_BACKEND")
    if override:
        if override == "auto" or override not in BACKENDS:
            raise ValueError("AITT_BACKEND must be codex, claude, cli or api")
        return resolve_backend(override)
    claude_host = any(os.environ.get(k) for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"))
    codex_host = any(os.environ.get(k) for k in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_CAAS_SESSION_ID"))
    if claude_host and codex_host:
        raise ValueError("both Codex and Claude host markers are present; set --backend or AITT_BACKEND explicitly")
    if claude_host:
        return "claude"
    if codex_host:
        return "codex"
    installed = [name for name in ("codex", "claude") if shutil.which(name)]
    if len(installed) == 1:
        return installed[0]
    if installed:
        raise ValueError("both CLIs are installed outside a detected host; set --backend or AITT_BACKEND explicitly")
    raise ValueError("no Codex/Claude CLI found; install one or select --backend api explicitly")


def resolve_model(model, backend):
    backend = resolve_backend(backend)
    if model is None or model == "auto":
        return DEFAULT_MODELS[backend]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty model id or auto")
    return "gpt-6-luna" if backend == "codex" and model == "luna" else model


def _child_env():
    # claude -p refuses to start inside another Claude Code session
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    # Effort comes from the candidate, not the parent shell. The effort env var
    # overrides --effort; fixed-budget switches also change the thinking policy
    # outside the recorded candidate configuration. Preserve auth and proxy vars.
    for key in ("CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS",
                "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"):
        env.pop(key, None)
    return env


def _neutral_cwd():
    # A fixed empty directory keeps the cached prefix stable and avoids picking
    # up a project's CLAUDE.md.
    d = os.path.join(tempfile.gettempdir(), "ai-task-autotuner-neutral")
    os.makedirs(d, exist_ok=True)
    return d


def _empty_usage():
    # thinking stays None (= not reported) unless some attempt reports it
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking": None}


def _add_usage(total, u):
    if u is None:
        return
    for k in total:
        v = u.get(k)
        if v is not None:
            total[k] = (total[k] or 0) + v


def _cli_attempt(prompt, system, model, effort, json_schema, timeout, extra_args):
    claude = shutil.which("claude")
    if claude is None:
        return {"ok": False, "error_kind": "harness", "error": "claude CLI not found"}
    fd, sys_path = tempfile.mkstemp(suffix=".txt", prefix="aitt-sys-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(system)
    args = [claude, "-p", "--output-format", "json", "--model", model,
            *CLI_ISOLATION, "--system-prompt-file", sys_path]
    if effort:
        args += ["--effort", effort]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))]
    args += list(extra_args or [])
    t0 = time.time()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=_child_env(), cwd=_neutral_cwd())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_kind": "timeout", "error": f"timeout after {timeout}s",
                "wall_ms": int((time.time() - t0) * 1000), "retryable": True}
    except OSError as e:
        return {"ok": False, "error_kind": "harness", "error": str(e), "retryable": False}
    finally:
        try:
            os.unlink(sys_path)
        except OSError:
            pass
    wall_ms = int((time.time() - t0) * 1000)
    try:
        env = json.loads(r.stdout)
    except ValueError:
        tail = (r.stderr or r.stdout or "")[-400:]
        return {"ok": False, "error_kind": "harness", "error": f"non-JSON output rc={r.returncode}: {tail}",
                "wall_ms": wall_ms, "retryable": True}
    usage, cost, served = None, env.get("total_cost_usd"), None
    mu = env.get("modelUsage") or {}
    if mu:
        usage = _empty_usage()
        for m in mu.values():
            _add_usage(usage, {"input": m.get("inputTokens"), "output": m.get("outputTokens"),
                               "cache_read": m.get("cacheReadInputTokens"),
                               "cache_write": m.get("cacheCreationInputTokens"),
                               "thinking": m.get("thinkingTokens")})
        served = ",".join(sorted(mu))
    elif env.get("usage"):
        u = env["usage"]
        usage = {"input": u.get("input_tokens"), "output": u.get("output_tokens"),
                 "cache_read": u.get("cache_read_input_tokens"),
                 "cache_write": u.get("cache_creation_input_tokens"),
                 "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens")}
    rec = {"wall_ms": wall_ms, "api_ms": env.get("duration_api_ms"), "cli_ms": env.get("duration_ms"),
           "ttft_ms": env.get("ttft_ms"), "num_turns": env.get("num_turns"), "usage": usage,
           "cost_usd": cost, "cost_basis": "cli_reported_list" if cost is not None else None,
           "model_served": served, "stop_reason": env.get("stop_reason"),
           "text": env.get("result"), "structured": env.get("structured_output")}
    if env.get("is_error") or r.returncode != 0:
        status = env.get("api_error_status")
        text = str(env.get("result") or "") + " " + (r.stderr or "")[-300:]
        if any(m in text.lower() for m in QUOTA_MARKERS):
            rec.update(ok=False, error_kind="quota", error=f"quota exhausted: {text[:300]}", retryable=False)
            return rec
        retryable = status in RETRYABLE_STATUS or (status is None and any(
            w in text.lower() for w in ("overload", "rate limit", "timeout", "temporarily")))
        rec.update(ok=False, error_kind="api" if status else "harness",
                   error=f"status={status} {text[:300]}", retryable=retryable)
        return rec
    if not (rec["text"] or "").strip() and rec["structured"] is None:
        # seen occasionally from claude -p: end_turn with empty text; a re-call usually works
        rec.update(ok=False, error_kind="empty", error="empty reply", retryable=True)
        return rec
    rec["ok"] = True
    return rec


def api_thinking_off(model, effort):
    """The api request body that turns thinking off, or None where the model rejects it."""
    model_id = MODEL_ALIASES.get(model, model)
    if model_id in API_THINKING_OFF and effort not in ("xhigh", "max"):
        return API_THINKING_OFF[model_id]
    return None


def _api_attempt(prompt, system, model, effort, json_schema, timeout, max_tokens, cache_system, thinking):
    try:
        import anthropic  # official SDK; only needed for the api backend
    except ImportError:
        return {"ok": False, "error_kind": "harness", "retryable": False,
                "error": "api backend needs the SDK: python -m pip install anthropic"}
    client = anthropic.Anthropic(max_retries=0, timeout=timeout)
    block = {"type": "text", "text": system}
    if cache_system:
        block["cache_control"] = {"type": "ephemeral"}
    model_id = MODEL_ALIASES.get(model, model)
    kwargs = {"model": model_id, "max_tokens": max_tokens or 16000,
              "system": [block], "messages": [{"role": "user", "content": prompt}]}
    if thinking == "off" and api_thinking_off(model_id, effort) is not None:
        kwargs["thinking"] = api_thinking_off(model_id, effort)
    oc = {}
    if effort:
        oc["effort"] = effort
    if json_schema is not None:
        oc["format"] = {"type": "json_schema", "schema": json_schema}
    if oc:
        kwargs["output_config"] = oc
    t0 = time.time()
    try:
        resp = client.messages.create(**kwargs)
    except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
        return {"ok": False, "error_kind": "api", "error": str(e)[:300], "retryable": True,
                "wall_ms": int((time.time() - t0) * 1000)}
    except anthropic.APIStatusError as e:
        if any(m in str(e).lower() for m in QUOTA_MARKERS):
            return {"ok": False, "error_kind": "quota", "error": str(e)[:300], "retryable": False}
        return {"ok": False, "error_kind": "api", "error": f"status={e.status_code} {str(e)[:300]}",
                "retryable": e.status_code in RETRYABLE_STATUS, "wall_ms": int((time.time() - t0) * 1000)}
    wall_ms = int((time.time() - t0) * 1000)
    u = resp.usage
    details = getattr(u, "output_tokens_details", None)
    usage = {"input": u.input_tokens, "output": u.output_tokens,
             "cache_read": getattr(u, "cache_read_input_tokens", None) or 0,
             "cache_write": getattr(u, "cache_creation_input_tokens", None) or 0,
             "thinking": getattr(details, "thinking_tokens", None) if details else None}
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    rec = {"ok": True, "wall_ms": wall_ms, "api_ms": wall_ms, "usage": usage, "model_served": resp.model,
           "stop_reason": resp.stop_reason, "text": text, "num_turns": 1,
           "cost_usd": estimate_cost(resp.model, usage), "cost_basis": "estimated_list_price"}
    if resp.stop_reason == "refusal":
        rec.update(ok=False, error_kind="refusal", error="model refused", retryable=False)
    return rec


def call(prompt, system=None, *, backend="auto", model=None, effort=None, max_tokens=None,
         json_schema=None, expect_json=False, timeout=300, retries=2, cache_system=True,
         thinking="off", extra_args=None, before_attempt=None, on_attempt=None):
    """Run one model call with retries; always returns a record (never raises).

    Omitted effort resolves to low; thinking="off" requests disabled thinking where the
    api backend allows it. The CLI adapters do not apply this thinking control: they
    use effort and retain reported usage.thinking (unknown is not zero). The runner
    may supply before_attempt() (an optional
    remaining-time cap in seconds) and on_attempt(record) to enforce limits
    between physical retries without changing logical-call accounting.
    """
    system = system or DEFAULT_SYSTEM
    effort = DEFAULT_EFFORT if effort is None else effort
    total = _empty_usage()
    any_usage = False
    missing_usage = set()
    cost = 0.0
    cost_complete = True
    attempt_errors = []
    t_start = time.time()
    rec = {}
    attempts = 0
    backend_requested = backend
    resolution_error = None
    try:
        backend = resolve_backend(backend)
        model = resolve_model(model, backend)
    except ValueError as e:
        resolution_error = str(e)
    for attempt in range(retries + 1):
        attempt_timeout = timeout
        remaining = None
        if before_attempt is not None and not resolution_error:
            try:
                remaining = before_attempt()
                if remaining is not None:
                    if (not isinstance(remaining, (int, float)) or isinstance(remaining, bool)
                            or not math.isfinite(remaining) or remaining <= 0):
                        raise RuntimeError("no execution time remains")
                    attempt_timeout = min(timeout, remaining)
            except Exception as e:
                rec = dict(rec, ok=False, error_kind="harness", error=f"execution policy: {e}",
                           retryable=False, policy_refused=True, text=None, structured=None, stop_reason=None)
                break
        attempts += 1
        attempt_start = time.monotonic()
        if resolution_error:
            rec = {"ok": False, "error_kind": "harness", "error": resolution_error, "retryable": False}
        elif backend == "api":
            rec = _api_attempt(prompt, system, model, effort, json_schema, attempt_timeout, max_tokens, cache_system,
                               thinking)
        elif backend == "codex":
            rec = _codex_attempt(prompt, system, model, effort, json_schema, attempt_timeout, extra_args,
                                 max_tokens=max_tokens, thinking=thinking)
        else:
            rec = _cli_attempt(prompt, system, model, effort, json_schema, attempt_timeout, extra_args)
        if rec.get("usage") is not None:
            any_usage = True
            _add_usage(total, rec["usage"])
            missing_usage.update(k for k in total if rec["usage"].get(k) is None)
        else:
            missing_usage.update(total)
        if rec.get("cost_usd") is not None:
            cost += rec["cost_usd"]
        else:
            cost_complete = False
        if on_attempt is not None and not resolution_error:
            try:
                on_attempt(rec)
            except Exception as e:
                rec = dict(rec, ok=False, error_kind="harness", error=f"execution accounting: {e}",
                           retryable=False, policy_refused=True, text=None, structured=None, stop_reason=None)
        if rec.get("ok") or not rec.get("retryable"):
            break
        if rec.get("error_kind") == "empty" and attempts >= 2:
            break  # one retry is enough for empty replies; then it is the model's answer
        attempt_errors.append(rec.get("error"))
        if attempt < retries:
            delay = min(30, 2 ** (attempt + 1)) * (0.75 + 0.5 * (time.time() % 1))
            if remaining is not None:
                delay = min(delay, max(0.0, remaining - (time.monotonic() - attempt_start)))
            time.sleep(delay)
    for k in missing_usage:
        total[k] = None
    out = {
        "backend": backend, "backend_requested": backend_requested,
        "model_requested": model, "model_served": rec.get("model_served"),
        # Legacy thinking is a request, never evidence of the model's actual mode.
        "effort": effort, "thinking": thinking, "thinking_requested": thinking,
        "ok": bool(rec.get("ok")), "error_kind": rec.get("error_kind"),
        "error": rec.get("error"), "text": rec.get("text"), "structured": rec.get("structured"),
        "stop_reason": rec.get("stop_reason"), "usage": total if any_usage else None,
        "cost_usd": cost if cost_complete else None, "cost_basis": rec.get("cost_basis") if cost_complete else None,
        "api_ms": rec.get("api_ms"), "wall_ms": int((time.time() - t_start) * 1000),
        "last_attempt_wall_ms": rec.get("wall_ms"), "ttft_ms": rec.get("ttft_ms"),
        "num_turns": rec.get("num_turns"), "attempts": attempts,
        "attempt_errors": attempt_errors, "prompt_chars": len(prompt), "system_chars": len(system),
        "prompt_sha": sha256_text(prompt)[:16], "system_sha": sha256_text(system)[:16],
        "t_start": t_start,
    }
    for key in ("thread_id", "unsupported_settings", "policy_refused"):
        if key in rec:
            out[key] = rec[key]
    if backend in ("codex", "claude"):
        out["unsupported_settings"] = [k for k, value in (("max_tokens", max_tokens), ("thinking", thinking))
                                       if value is not None]
    elif backend == "api":
        # e.g. Opus 5.5 rejects disabled thinking: the request runs with the model's default.
        out["unsupported_settings"] = ["thinking"] if thinking == "off" and api_thinking_off(model, effort) is None else []
    if out["ok"] and out["stop_reason"] == "max_tokens":
        out["error_kind"] = "truncated"
    if expect_json:
        parsed = out["structured"] if out["structured"] is not None else extract_json(out["text"])
        out["json"] = parsed
        if out["ok"] and parsed is None:
            out["error_kind"] = out["error_kind"] or "parse"
    return out


def is_harness_failure(rec):
    """Infra failures (timeouts, rate limits, CLI crashes) are missing data, not model errors."""
    return (not rec.get("ok")) and rec.get("error_kind") in ("timeout", "api", "harness", "quota")


def calibrate(backend="auto", model=None, n=3):
    """Measure the fixed per-call overhead of the backend with a trivial prompt."""
    rows = []
    for _ in range(n):
        r = call('Reply with {"ok": true}', "Reply with JSON only.", backend=backend, model=model,
                 effort="low", expect_json=True, retries=1, timeout=120)
        u = r["usage"] or {}
        counts = [u.get(k) for k in ("input", "cache_read", "cache_write")]
        rows.append({"ok": r["ok"], "input_side": sum(counts) if all(v is not None for v in counts) else None,
                     "cache_read": u.get("cache_read"),
                     "wall_ms": r["wall_ms"], "api_ms": r["api_ms"], "served": r["model_served"],
                     "cost_usd": r["cost_usd"]})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="auto", choices=BACKENDS)
    ap.add_argument("--model", help="default: gpt-6-luna for Codex, haiku for Claude/API")
    ap.add_argument("--effort", help="default: low; raise only for a justified experiment")
    ap.add_argument("--system-file")
    ap.add_argument("--json", action="store_true", help="parse JSON from the reply")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    if a.calibrate:
        rows = calibrate(a.backend, a.model)
        for row in rows:
            print(json.dumps(row))
        print("# input_side ~= fixed overhead tokens per call (the bare API would not add it);"
              " wall_ms - api_ms ~= process start-up cost per call.")
        return
    system = open(a.system_file, encoding="utf-8").read() if a.system_file else None
    prompt = sys.stdin.read()
    rec = call(prompt, system, backend=a.backend, model=a.model, effort=a.effort,
               expect_json=a.json, timeout=a.timeout)
    print(json.dumps(rec, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
