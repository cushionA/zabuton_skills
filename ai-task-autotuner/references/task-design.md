# Task design: from audit to experiment plan

Contents: plan template · code + model pipelines · deriving a task from existing data ·
baseline · draft evaluation and independent review · prompt conventions · thinking and output budget · lever catalog · caching ·
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
- For every automatic decision, state the evidence that makes it safe and construct a
  counterexample that almost passes (swapped axes, different capacity/count/model, missing
  or free add-ons). Values matching is insufficient when their roles differ. If a required
  distinction cannot be checked, defer to the model or review outcome. Record these probes
  for the [independent task review](task-review.md); zero observed errors is not a proof.

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
JSON output with ids, effort `low`, thinking off where supported, no examples. Backend `auto`
selects Codex/Luna (`gpt-6-luna`) on Codex and Claude/Haiku on Claude; follow explicit choices
and retain the resolved settings in the charter. If a no-model
heuristic is plausible, measure it too - it calibrates whether the model adds value, and it
is the cheapest efficiency-phase fallback.

## Draft evaluation and independent review

Finish `experiment_plan.md`, `eval/eval_spec.md`, `eval/dataset.py`, `eval/score.py`,
`eval/splits.json` and the baseline candidate before reference-building or baseline runs.
These are drafts until reviewed: include the complete output contract, failure/hold handling,
metrics and guards, grouping, negative construction and the reference-building plan. Reference
labels need not exist yet. Use synthetic and dev-only probes to exercise the scorer and rules;
do not inspect holdout examples to prepare the review.

Keep the reviewed task/evaluation policy in `eval/eval_spec.md`. Later reference-run models,
prompt versions, run ids, dates and adjudication records/counts belong in generated
`eval/reference_provenance.json`; writing them does not change the reviewed design.

Run [task-review.md](task-review.md) once for this design. An independent reviewer checks
whether the task serves the goal and whether its input, code decisions and evaluation can
support the intended claims. A REVISE verdict is work for the author: fix it, prepare an
updated request and have the reviewer re-check the changes and their effects. Ask the user
only for a decision that belongs to them. A current PASS permits reference and baseline runs;
evaluation validation and freezing follow the two baseline runs.

Ordinary prompt/code tuning candidates do not trigger another full design review. If tuning
changes the task definition, output contract, metrics, grouping or safety assumptions, update
the design files and review the affected scope before measuring that changed design.

## Prompt conventions

- Instructions and any reasoning fields in English; input data verbatim (no translation, no
  normalization of keys or values). Output values copied from input exactly.
- State decision criteria as rules, including when to abstain or mark unsure if allowed.
- Wrap data in a clearly delimited block and say it is untrusted data.
- Output: one JSON object, ids instead of copied text, the smallest schema that scoring
  needs. Short per-item rationale only if it measurably helps (it costs output tokens).
- Fixed content first (system prompt, rules, examples), the unit's data last - this is what
  prompt caching can reuse.
- Avoid `--json-schema` on the Claude backend (it adds a tool round-trip); ask for JSON and
  parse with `extract_json`. Codex also requests JSON in the prompt; its JSONL CLI events
  wrap the final answer, not the task's output schema. On the api backend structured outputs
  are fine.

## Thinking and output budget

- Default: effort `low`, `thinking: "off"` where supported (honored on the api backend for
  Haiku 5.5 and Sonnet 5.5). Neither CLI guarantees thinking off. Claude reports thinking
  usage; Codex does not expose it separately, so record it as unreported.
- Raise effort only as an experiment (`model.effort`) and keep it only if quality gains pay
  for the extra output tokens and latency.
- `max_tokens` on supporting backends during experiments: generous enough that truncation never decides a result
  (watch `truncated` in analyze_run); the production value comes from the measured output
  distribution (`export_deliverables.py`). Codex CLI does not expose a supported per-response
  `max_tokens` control; report that limit as unenforced, not as a production guarantee.

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

Static prefix first, variable data last. Claude CLI caching is automatic (watch
`cache_hit_ratio` in the summary); Codex reports cached input tokens when available, with
uncached input calculated as total input minus cached input. On the api backend mark the system prompt with
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
