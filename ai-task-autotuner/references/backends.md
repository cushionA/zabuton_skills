# Model backends and what their numbers mean

## Backend selection and model defaults

`backend` accepts `auto` (default), `codex`, `claude` and `api`. Legacy `cli` is an alias for
`claude`, so existing workspaces retain their original backend. Explicit backend/model
configuration wins. Otherwise `AITT_BACKEND` selects the backend, then `auto` detects the
parent host from `CLAUDECODE`/`CLAUDE_CODE_ENTRYPOINT` or
`CODEX_THREAD_ID`/`CODEX_SESSION_ID`/`CODEX_CAAS_SESSION_ID`. Without a parent signal, a sole
available CLI is selected. Conflicting host signals, two available CLIs without a host signal,
or no available CLI fail with guidance to set `--backend`; do not silently choose another
provider.

Initialization saves the canonical backend and resolved model in the charter. Later runs use
that saved choice, even on another host. Candidates inherit it unless explicitly overridden;
the candidate template does not force Haiku. An omitted/automatic model resolves by backend:

| Backend | Default target | Default reference model |
|---|---|---|
| `codex` | Luna, `gpt-6-luna` | Luna, `gpt-6-luna` |
| `claude` (legacy `cli`) | `haiku` | `opus` |
| `api` (Anthropic) | `haiku` | `opus` |

On Codex, do not upgrade either model automatically. A stronger reference model must be
explicitly configured. Same-model reference agreement does not independently establish
correctness; see [evaluation.md](evaluation.md).

## codex: `codex exec` as an API stand-in

The adapter uses `codex exec --json --ephemeral --ignore-user-config`, read-only sandboxing,
a neutral working directory and disabled tools/apps/plugins. Authentication is preserved.
The system instruction is supplied through a temporary file; task data goes through stdin. JSONL events
are parsed for the final answer and usage; any attempted tool event makes the call fail
closed rather than accepting an answer produced with tools. This is a one-shot request, not
a continuation of the author's chat.

Codex 0.159.2 was exercised against a local fake Responses server using the actual CLI.
The request exposed `tools: []`, a normal streamed final answer was accepted and usage was
read from the CLI events. The adapter enables only `features.code_mode_host`, which the CLI
initializes at startup, while `code_mode` and model-visible tool features stay disabled.
This removes the host-disabled startup error without ignoring error events or enabling model
tool use. Provider error responses are still failures.

Tiny live Luna requests were attempted, but authentication refresh failed with HTTP 401
before a successful model response. The final one-attempt smoke request after the startup fix
no longer emitted the Code Mode startup error and reported `auth_required: true`.
Authentication errors are non-retryable even when the
diagnostic also mentions connection failures or trying again. No login or credential settings
were changed. Actual Luna execution remains unverified; Claude live calls were not re-run here.
Verify authentication and smoke one unit before a benchmark. Flags and event schemas may change.

- Default model is Luna (`gpt-6-luna`), effort `low`. Neither `thinking: "off"` nor an explicit
  per-response `max_tokens` limit is guaranteed by this CLI. Treat the token limit as
  unsupported, not enforced; unsupported controls are recorded in `unsupported_settings`.
  Separate thinking-token usage is unreported.
- Event `input_tokens` includes cached input. Normalize uncached input to
  `input_tokens - cached_input_tokens`, recording the cache tokens separately; never add the
  cache count on top of the reported total.
- Wall time includes CLI startup and the model call. API-only latency is unreported. Do not
  substitute wall time for `api_ms` or invent an API overhead estimate.
- Dollar cost and a Luna price schedule are not available from these events. Keep cost
  unknown; do not treat missing cost as zero or borrow another model's pricing. New Codex
  workspaces use `calls_per_unit` as the efficiency metric. At every `Ctx.llm` call, the
  effective backend (including per-call overrides) is checked: a Codex call with non-null
  `limits.max_cost_usd` is blocked because that budget cannot be enforced. Code-only candidates
  may still run. Use a priced backend for a dollar limit, or limits on measurable
  time/experiment count; never silently remove the user's budget.
- CLI events may not expose the served model. Record the requested model separately;
  `model_served` remains unreported unless the backend actually supplies it.
- Calls spend the configured Codex account's quota. Preserve quota errors and count failed
  attempts; do not switch to a different provider/model to bypass a limit.

Exported Codex production settings show `max_tokens.value: null` (unsupported) and
`thinking: "backend managed"`. The recommended timeout is derived from observed CLI wall-time
p99, not API latency. State those limits alongside the measurements instead of presenting
unenforced settings as guarantees.

## claude (legacy cli): `claude -p` as an API stand-in

`llm_call.py` runs `claude -p --output-format json` with isolation flags so a call behaves
like a bare request: `--tools ""` (no tools - also an injection safeguard),
`--setting-sources ""`, `--strict-mcp-config`, `--disable-slash-commands`,
`--no-session-persistence`, `--exclude-dynamic-system-prompt-sections`, a fixed empty
working directory, and the system prompt from a temp file (`--system-prompt-file`). The
prompt goes through stdin. `CLAUDECODE` is removed from the child environment so nested
calls work inside a Claude Code session.

Measured on 2026-10-18 (Claude Code 2.1.302, Haiku 5.5), expect drift - re-run
`llm_call.py --calibrate` per session:
- Fixed input overhead about 800 tokens per call even with a one-line system prompt (about
  6,000 without the isolation flags). A bare API call would not carry it.
- Wall time per call = process start (~1-4 s, more under parallel load) + API time. Use
  `api_ms` (`duration_api_ms`) as the API-equivalent latency; `wall_ms` is what this harness
  costs. Report both, labeled.
- Nearly all input is booked as cache writes/reads by the CLI, and it caches the whole prompt,
  not just the static prefix: an identical rerun was ~6x cheaper than the cold run (and faster).
  `cost_usd` is the CLI-reported list-price cost under that caching, so it depends on run order.
  Compare candidates on `cost_uncached_list_usd_per_unit` (every input-side token at the base
  input price) - the template's default efficiency metric.
- `--json-schema` works but adds a tool round-trip (more tokens and latency); prefer JSON
  in the prompt plus `extract_json`.
- Thinking cannot be fully turned off through `claude -p` for Haiku 5.5: effort `low` still
  produced ~150-280 thinking tokens on a small arithmetic prompt (`MAX_THINKING_TOKENS=0` and
  `alwaysThinkingEnabled: false` did not stop it). Use effort `low` as the minimum, report
  `usage.thinking`, and note that the api backend with thinking off would save those tokens.
  Pass `--effort` explicitly; the CLI default may differ from the API default.
- Quota: `claude -p` spends the same subscription limits as the session running the skill. A
  session-limit error (`error_kind: "quota"`) is not retried; the runner stops launching calls
  and marks the run void. Heavy strong-model jobs are the usual cause - price them first.
- 8 concurrent calls ran without throttling here; raise or lower `concurrency` per measured
  errors. Retries back off with jitter and are counted; failed-attempt usage still counts.
- Windows: `claude` is a `.CMD` shim; argv passes through cmd.exe. Prompts via stdin and the
  system prompt via file avoid the breakage; keep `<>&|^` out of any argv (e.g. JSON schemas).

Change the model with `--model haiku|sonnet|opus` or a full id in the charter's model field or
a candidate's `config.json`. When the CLI reports a served model, it is recorded per call
(`model_served`) and listed in the summary; check it matches what you asked for. A requested
model name alone is not evidence of the served model.

## api: Anthropic Messages API

Set `"backend": "api"` and install the SDK (`python -m pip install anthropic`); credentials
come from the environment (`ANTHROPIC_API_KEY` or an `ant auth login` profile). Aliases map
to `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`. `effort` goes to
`output_config.effort`; a JSON schema to `output_config.format`; the system prompt carries
`cache_control`. `thinking: "off"` (the default) sends `{"type": "disabled"}` for Haiku 5.5 and
`{"type": "between_tools"}` for Sonnet 5.5 (both only at effort high or below); Opus 5.5 cannot
disable thinking - lower the effort instead. Usage comes from `response.usage` (thinking tokens when reported); cost is a
list-price estimate (`common.PRICES`; Haiku 5.5 is priced higher past 100K input tokens). For large
final runs the Batches API halves cost but is not wired in here.

The api backend was not exercised live when this skill was written (no key in that
environment). Verify with a one-unit smoke run first.

## What to write in reports

- Resolved backend, CLI version, model requested and served (unreported if absent), effort,
  concurrency and whether requested controls were supported.
- Tokens by type from the backend's usage; anything not reported is "not reported", never
  estimated from bytes or characters.
- Cost with its basis (CLI-reported list price, list-price estimate or unknown) and the
  uncached comparison where supported; do not convert call-count or byte reductions into cost reductions.
- Latency as api_ms (API-equivalent, if reported) and wall (harness) separately; per unit and per call;
  run wall time vs sum of unit times (parallelism); cold vs warm cache.
