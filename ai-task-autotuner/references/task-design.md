# Task design: from audit to experiment plan

Contents: plan template · code + model pipelines · deriving a task from existing data ·
baseline · prompt conventions · thinking and output budget · lever catalog · caching ·
leakage · task_design.md

## experiment_plan.md

```markdown
# Experiment plan - <task>
## Decision this serves      one sentence: what the result will be used for
## Problem taxonomy          sub-problems found in the audit, with counts (easy / hard drivers)
## Approaches, ranked        approach - evidence from the audit - expected strength/weakness - cost
                             (include code-only and code+model hybrids, not just prompts)
## Derived test data         how units, candidates, negatives were built from the raw data (if any)
## Baseline                  the simplest reasonable design, plus a no-model rule baseline if plausible
## Metrics                   primary, guards (with thresholds), diagnostics, efficiency metric
## Splits                    dev / holdout / full ids, stratification, grouping, sizes, noise estimate
## Reference                 tier, how it is built, estimated cost of building it
## Budget and stop           user limits; otherwise improvement-limit rule; holdout peeks <= 3
## Risks                     leakage, reference bias, data defects carried as known limitations
```

Let the audit drive the approach. If normalization or exact matching solves most units,
start with a rule pass and send only the rest to the model. If the hard cases depend on
text far from the structured fields, plan input selection early. If units vary 50x in size,
plan for splitting large units.

## Code + model pipelines

The pipeline is ordinary Python: anything deterministic is fair game and usually cheaper,
faster and more stable than a model call.
- Before the model: parse and normalize (units, sizes, colors, full/half-width, synonyms),
  decide exact or near-exact cases, prune candidates, select the relevant text lines, split
  large units.
- After the model: validate ids and schema, enforce one answer per item, repair formats,
  apply hard constraints the model ignored (different numbers can never match).
- Count what code decided (`ctx.count("rule_decided", n)`) and score those items like any
  other; a rule that is fast but wrong is not a win.
- If a code-only candidate reaches the best quality within noise (the ledger's NO-LLM line),
  write `non_ai_findings.md` (what rule, which strata it covers, where it fails, maintenance
  cost) and tell the user - possibly before the loop ends. When code covers part of the data,
  report that share too ("62% of items decided by rules at 99% agreement; model only for the rest").
- Rules and dictionaries are part of the design: record their size and where they came from
  (dev only), because they are what needs maintenance later.

## Deriving a task from existing data

Often the data holds positives only (pairs, links, past decisions) and the task must be built:
- **Units can be derived:** one record may yield several units (e.g. "query with the true item
  among candidates" and "same query with it removed"). Units derived from one record share a
  `group_of` key so they land in the same split.
- **"None" cases:** remove the true item from the candidate pool. Decide the share (often
  30-50%) and keep it fixed; report results for present and removed queries separately.
- **Candidates:** random distractors make the task trivially easy. Build pools from the
  closest lookalikes (same shop/brand/category, nearest by text similarity) and measure the
  candidate generator's recall@K - if the true item is often missing, the model cannot win.
- **Unlabelled positives:** a non-partner candidate may also be correct (duplicate listings,
  bundles). Check near-duplicates on close reading; use acceptable sets for them.
- **Many-to-one and duplicates:** dedupe the pool, decide what "same" means (same listing vs
  same purchasable variant) and write the definition into `eval_spec.md`.
- Store constructed inputs under `WS/data_built/` with the script that built them; they are
  part of the test set you deliver.

## Baseline

The simplest design that could work: one call per natural unit, complete instructions,
JSON output with ids, effort `low`, thinking off where possible, no examples. If a no-model
heuristic is plausible, measure it too - it calibrates whether the model adds value, and it
is the cheapest efficiency-phase fallback.

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

## Thinking and output budget

- Default: effort `low`, `thinking: "off"` in config (honored on the api backend for Haiku 5.5
  and Sonnet 5.5; the cli backend still thinks a little - measured as `usage.thinking`).
- Raise effort only as an experiment (`model.effort`) and keep it only if quality gains pay
  for the extra output tokens and latency.
- `max_tokens` during experiments: generous enough that truncation never decides a result
  (watch `truncated` in analyze_run); the production value comes from the measured output
  distribution (`export_deliverables.py`).

## Lever catalog

| Lever (group.detail) | Use when | Measure |
|---|---|---|
| prompt.instructions | a rule is missing, vague or misapplied | target tag counts |
| prompt.examples | a format or boundary is hard to state; use synthetic or dev-only examples | overfit: dev vs holdout |
| prompt.reasoning | multi-step judgments; a short checklist before the answer | output tokens, latency |
| input.representation | the model misreads structure (nested JSON, long lists) - try tables, grouping, ordering | primary, input tokens |
| input.selection | long irrelevant text; send only lines/fields that can matter | recall of needed evidence |
| output.format | parse failures, truncation, verbose outputs | parse/truncation rate, output tokens |
| decomposition.split | one call does several jobs; split into extract -> compare, or per item | calls, cost, primary |
| decomposition.batch | many small units; several per call with a shared prefix | cost/unit, cross-unit interference |
| deterministic.pre | normalization, exact matches, candidate pruning, unit parsing | share decided by code, its precision |
| deterministic.post | validating/repairing output (ids exist, one answer per item, hard constraints) | gate failures |
| model.choice / model.effort | capacity vs cost trade | staircase walk (loop.md) |
| ensemble.verify | a second focused pass only on uncertain or conflicting items | added cost vs gain |
| ensemble.cascade | code or a cheap model first, escalate hard items | escalation rate, cost/unit |
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

## task_design.md (deliverable, user's language)

```markdown
# <task> - task design
## Purpose and scope            what it decides, for which inputs, what it does not do
## Pipeline                     numbered steps; for each: code or model, input, output, failure handling
## Model calls                  model, effort/thinking, prompts (paths), I/O schema, calls per unit
## Rules and dictionaries       what code decides, size, source, maintenance
## Output definition            every outcome class and what it means (e.g. matched / no match / unsure / conditional)
## Production settings          from production_config.json, with the measurements behind them
## Expected quality, cost, time from the final report (with reference tier)
## Operating it                 how to run, how to re-measure with eval_kit, what to monitor, when to re-tune
```
