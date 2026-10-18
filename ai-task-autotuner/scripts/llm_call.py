"""One model call, measured the same way on every backend.

Backends:
  cli  - `claude -p` (Claude Code subscription; the default API substitute)
  api  - Anthropic Messages API via the official `anthropic` SDK (needs credentials)

Every call returns a record with text, usage (input/output/cache/thinking tokens),
cost, API-side latency and wall time, attempts and error class, so experiments can
be compared on the same footing. Usage that a backend does not report is None
(never guessed).

CLI:
  python llm_call.py --calibrate [--backend cli] [--model haiku]
  python llm_call.py --model haiku --effort low --system-file sys.txt < prompt.txt
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MODEL_ALIASES, estimate_cost, extract_json, sha256_text  # noqa: E402

DEFAULT_SYSTEM = "Follow the user's instructions exactly."
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
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


def _child_env():
    # claude -p refuses to start inside another Claude Code session
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
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
        text = str(env.get("result") or "")
        retryable = status in RETRYABLE_STATUS or (status is None and any(
            w in text.lower() for w in ("overload", "rate limit", "timeout", "temporarily")))
        rec.update(ok=False, error_kind="api" if status else "harness",
                   error=f"status={status} {text[:300]}", retryable=retryable)
        return rec
    rec["ok"] = True
    return rec


def _api_attempt(prompt, system, model, effort, json_schema, timeout, max_tokens, cache_system):
    try:
        import anthropic  # official SDK; only needed for the api backend
    except ImportError:
        return {"ok": False, "error_kind": "harness", "retryable": False,
                "error": "api backend needs the SDK: python -m pip install anthropic"}
    client = anthropic.Anthropic(max_retries=0, timeout=timeout)
    block = {"type": "text", "text": system}
    if cache_system:
        block["cache_control"] = {"type": "ephemeral"}
    kwargs = {"model": MODEL_ALIASES.get(model, model), "max_tokens": max_tokens or 16000,
              "system": [block], "messages": [{"role": "user", "content": prompt}]}
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


def call(prompt, system=None, *, backend="cli", model="haiku", effort=None, max_tokens=None,
         json_schema=None, expect_json=False, timeout=300, retries=2, cache_system=True,
         extra_args=None):
    """Run one model call with retries; always returns a record (never raises)."""
    system = system or DEFAULT_SYSTEM
    total = _empty_usage()
    any_usage = False
    cost = 0.0
    attempt_errors = []
    t_start = time.time()
    rec = {}
    attempts = 0
    for attempt in range(retries + 1):
        attempts += 1
        if backend == "api":
            rec = _api_attempt(prompt, system, model, effort, json_schema, timeout, max_tokens, cache_system)
        else:
            rec = _cli_attempt(prompt, system, model, effort, json_schema, timeout, extra_args)
        if rec.get("usage") is not None:
            any_usage = True
            _add_usage(total, rec["usage"])
        if rec.get("cost_usd") is not None:
            cost += rec["cost_usd"]
        if rec.get("ok") or not rec.get("retryable"):
            break
        attempt_errors.append(rec.get("error"))
        if attempt < retries:
            time.sleep(min(30, 2 ** (attempt + 1)) * (0.75 + 0.5 * (time.time() % 1)))
    out = {
        "backend": backend, "model_requested": model, "model_served": rec.get("model_served"),
        "effort": effort, "ok": bool(rec.get("ok")), "error_kind": rec.get("error_kind"),
        "error": rec.get("error"), "text": rec.get("text"), "structured": rec.get("structured"),
        "stop_reason": rec.get("stop_reason"), "usage": total if any_usage else None,
        "cost_usd": cost if any_usage else None, "cost_basis": rec.get("cost_basis"),
        "api_ms": rec.get("api_ms"), "wall_ms": int((time.time() - t_start) * 1000),
        "last_attempt_wall_ms": rec.get("wall_ms"), "ttft_ms": rec.get("ttft_ms"),
        "num_turns": rec.get("num_turns"), "attempts": attempts,
        "attempt_errors": attempt_errors, "prompt_chars": len(prompt), "system_chars": len(system),
        "prompt_sha": sha256_text(prompt)[:16], "system_sha": sha256_text(system)[:16],
        "t_start": t_start,
    }
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
    return (not rec.get("ok")) and rec.get("error_kind") in ("timeout", "api", "harness")


def calibrate(backend="cli", model="haiku", n=3):
    """Measure the fixed per-call overhead of the backend with a trivial prompt."""
    rows = []
    for _ in range(n):
        r = call('Reply with {"ok": true}', "Reply with JSON only.", backend=backend, model=model,
                 effort="low", expect_json=True, retries=1, timeout=120)
        u = r["usage"] or {}
        rows.append({"ok": r["ok"], "input_side": (u.get("input") or 0) + (u.get("cache_read") or 0)
                     + (u.get("cache_write") or 0), "cache_read": u.get("cache_read"),
                     "wall_ms": r["wall_ms"], "api_ms": r["api_ms"], "served": r["model_served"],
                     "cost_usd": r["cost_usd"]})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="cli", choices=["cli", "api"])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--effort")
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
