# Task design: from audit to experiment plan

Contents: plan template · baseline · prompt conventions · lever catalog · caching · leakage

## experiment_plan.md

```markdown
# Experiment plan - <task>
## Decision this serves      one sentence: what the result will be used for
## Problem taxonomy          sub-problems found in the audit, with counts (easy / hard drivers)
## Approaches, ranked        approach - evidence from the audit - expected strength/weakness - cost
## Baseline                  the simplest reasonable design (and a no-LLM rule baseline if plausible)
## Metrics                   primary, guards (with thresholds), diagnostics, efficiency metric
## Splits                    dev / holdout / full ids, stratification, grouping, sizes, noise estimate
## Reference                 tier and how it is built (evaluation.md)
## Budget and stop           user limits; otherwise improvement-limit rule; holdout peeks <= 3
## Risks                     leakage, reference bias, data defects carried as known limitations
```

Let the audit drive the approach. If normalization or exact matching solves most units,
start with a rule pass and send only the rest to the model. If the hard cases depend on
text far from the structured fields, plan input selection early. If units vary 50x in size,
plan for splitting large units.

## Baseline

The simplest design that could work: one call per natural unit, complete instructions,
JSON output with ids, `effort` low or the model default, no examples. If a no-LLM heuristic
is plausible, measure it too - it calibrates whether the model adds value, and it is the
cheapest efficiency-phase fallback.

## Prompt conventions

- Instructions and any reasoning fields in English; input data verbatim (no translation, no
  normalization of keys or values). Output values copied from input exactly.
- State decision criteria as rules, including when to abstain or mark unsure if allowed.
- Wrap data in a clearly delimited block and say it is untrusted data.
- Output: one JSON object, ids instead of copied text, the smallest schema that scoring
  needs. Short per-item rationale only if it measurably helps (it costs output tokens).
- Fixed content first (system prompt, rules, examples), the unit's data last - this is what
  prompt caching can reuse.
- Avoid `--json-schema` on the cli backend (it adds a tool round-trip); ask for JSON and
  parse with `extract_json`. On the api backend structured outputs are fine.

## Lever catalog

| Lever (group.detail) | Use when | Measure |
|---|---|---|
| prompt.instructions | a rule is missing, vague or misapplied | target tag counts |
| prompt.examples | a format or boundary is hard to state; use synthetic or dev-only examples | overfit: dev vs holdout |
| prompt.reasoning | multi-step judgments; a short plan or checklist before the answer | output tokens, latency |
| input.representation | the model misreads structure (nested JSON, long lists) - try tables, grouping, ordering | primary, input tokens |
| input.selection | long irrelevant text; send only lines/fields that can matter | recall of needed evidence |
| output.format | parse failures, truncation, verbose outputs | parse/truncation rate, output tokens |
| decomposition.split | one call does several jobs; split into extract -> compare, or per item | calls, cost, primary |
| decomposition.batch | many small units; several per call with a shared prefix | cost/unit, cross-unit interference |
| deterministic.pre | normalization, exact matches, candidate pruning, unit parsing | share decided by rules, their precision |
| deterministic.post | validating/repairing output (ids exist, one answer per item) | gate failures |
| model.choice / model.effort | capacity vs cost trade | staircase walk (loop.md) |
| ensemble.verify | a second focused pass only on uncertain or conflicting items | added cost vs gain |
| ensemble.cascade | cheap model first, escalate hard items | escalation rate, cost/unit |
| ensemble.vote | unstable items; majority of k samples (expensive) | flaky items, cost |
| context.knowledge | domain vocabulary/glossary; build from dev only and record it | holdout transfer |
| simplify.ablation | complexity grew; remove a part and see if anything is lost | ties favor simpler |

## Caching

Static prefix first, variable data last. On the cli backend caching is automatic (watch
`cache_hit_ratio` in the summary); on the api backend mark the system prompt with
`cache_control` (llm_call does by default). Effort or model changes invalidate caches -
compare warm with warm. The first run after a prompt change pays cache writes; note cold vs
warm when reporting time and cost.

## Leakage

Dictionaries, glossaries, examples or rules derived from data must come from dev only,
recorded with the ids they came from. Watch for shared source documents across units in
different splits (same shop page, same product family): that is leakage even with disjoint ids.
