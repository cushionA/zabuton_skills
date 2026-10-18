# Model backends and what their numbers mean

## cli (default): `claude -p` as an API stand-in

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
- Nearly all input is booked as cache writes/reads by the CLI. `cost_usd` is the CLI-reported
  list-price cost under that caching. `summary.cost_uncached_list_usd` re-prices every
  input-side token at the base input price as a backend-neutral comparison point.
- `--json-schema` works but adds a tool round-trip (more tokens and latency); prefer JSON
  in the prompt plus `extract_json`.
- Pass `--effort` explicitly (configs default to `low`); the CLI's own default may differ from
  the API default. Thinking tokens are reported per call (`usage.thinking`).
- 8 concurrent calls ran without throttling here; raise or lower `concurrency` per measured
  errors. Retries back off with jitter and are counted; failed-attempt usage still counts.
- Windows: `claude` is a `.CMD` shim; argv passes through cmd.exe. Prompts via stdin and the
  system prompt via file avoid the breakage; keep `<>&|^` out of any argv (e.g. JSON schemas).

Change the model with `--model haiku|sonnet|opus` or a full id in `charter.backend` or a
candidate's `config.json`. The served model is recorded per call (`model_served`); the runner
lists it in the summary - check it matches what you asked for.

## api: Anthropic Messages API

Set `"backend": "api"` and install the SDK (`python -m pip install anthropic`); credentials
come from the environment (`ANTHROPIC_API_KEY` or an `ant auth login` profile). Aliases map
to `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`. `effort` goes to
`output_config.effort`; a JSON schema to `output_config.format`; the system prompt carries
`cache_control`. Usage comes from `response.usage` (thinking tokens when reported); cost is a
list-price estimate (`common.PRICES`; Haiku 5.5 doubles past 100K input tokens). For large
final runs the Batches API halves cost but is not wired in here.

The api backend was not exercised live when this skill was written (no key in that
environment); unit tests cover the cli path. Verify with a one-unit smoke run first.

## What to write in reports

- Backend, CLI version, model requested and served, effort, concurrency.
- Tokens by type from the backend's usage; anything not reported is "not reported", never
  estimated from bytes or characters.
- Cost with its basis (CLI-reported list price, or list-price estimate) and the uncached
  comparison; do not convert call-count or byte reductions into cost reductions.
- Latency as api_ms (API-equivalent) and wall (harness) separately; per unit and per call;
  run wall time vs sum of unit times (parallelism); cold vs warm cache.
