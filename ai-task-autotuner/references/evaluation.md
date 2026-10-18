# Evaluation design, references and validation

Contents: metrics by task type · hard gates and failure classes · splits · reference tiers
and the silver protocol · LLM judges · validation and noise · changing the eval · naming numbers

## Metrics by task type

Score items at the finest stable granularity (one label, one matched row, one extracted
field) so paired comparisons and failure tags work. Pick one primary metric and guard metrics
that must not get worse.

| Task | Primary (typical) | Guards / diagnostics |
|---|---|---|
| Classification | accuracy or macro-F1 (skewed classes) | per-class recall, majority-class baseline |
| Extraction | field-level exact match after normalization | missing-field rate, fabricated-value rate |
| Matching / reconciliation with "no match" and "unsure" | correct-decision rate over all items | false-accept (wrong match) rate, auto-decided coverage, precision of decided items |
| Retrieval / selection among candidates | top-1 correct incl. correct "none" | false-positive rate on no-answer queries, recall@k of any pre-filter |
| Ranking | NDCG@k / pairwise accuracy | top-1 |
| Generation / summarization | rubric pass rate from a validated judge | length, faithfulness flags, cost |

**When abstaining is allowed** (hold/review/conditional), a wrong confident answer is usually
worse than a hold. Make the primary metric reward correct decisions, and put a hard guard on
the false-accept rate (e.g. `{"metric": "false_accept_rate", "max": 0.02}`); otherwise the
loop learns to guess. Report coverage (share auto-decided) separately; it is not accuracy.

## Hard gates and failure classes

- Hard gates in `score()` -> `gate_failures`: output schema, every required item answered
  exactly once, only real ids referenced (no fabricated keys/rows). Any gate failure makes the
  run invalid for keeping.
- **System failures** (refusal, unparseable or truncated output, exception in the pipeline)
  are scored as wrong - never dropped.
- **Harness failures** (timeouts, rate limits, CLI crash) are missing data. The runner marks
  them `harness_error`; a run with clearly more of them than the best is `void` and re-run.

## Splits

- `dev`: tuning; read freely. `holdout`: sealed until phase switch/final. `full`: the whole
  in-scope set for time/usage/outcome distribution; accuracy only where references exist.
- Random, stratified by the audit's strata, **group-aware** (`group_of`: near-duplicates,
  same source page, same parent product stay in one split). Never choose by baseline score.
- Size dev so the noise floor is below the improvements you care about: for a binary item
  score the 95% half-width is about `1/sqrt(n_items)` per run. Items, not units, set n, but
  items in one unit are correlated - prefer more units over more items per unit.
- Predefined subsets in the data (e.g. "smoke_10", "largest_20") are fine as dev/holdout if
  they are disjoint after grouping; record which ids and why.

## Reference tiers

| Tier | Source | May be called |
|---|---|---|
| gold | human labels with stated provenance | accuracy |
| verified silver | silver + human check of a stated sample | agreement with reference (sample-verified) |
| silver | strong model x2 + adjudication | agreement with <model> reference |
| none | - | only coverage, consistency, rule violations |

### Silver protocol (no labels)

1. Reference model = stronger than the target (default `opus`, effort high). Never the target
   model, never the candidate you are tuning - agreement would then measure self-similarity.
2. Write a careful reference candidate (`candidates/ref-*`): full instructions, per-item
   evidence, no cost cutting. Optionally two different prompt variants to decorrelate errors.
3. Run it twice on dev and holdout (`--purpose reference`), then
   `reference.py merge WS RUN_A RUN_B --split dev` (needs `score.flatten`).
4. Adjudicate every disagreement by reading the source data (or with a subagent for
   volume): a label, an `acceptable` set for genuinely ambiguous items, or `exclude` for data
   defects (report them). For holdout, delegate adjudication to a subagent and look only at
   counts, so holdout content does not enter your context.
5. `reference.py export-csv` gives a sheet (disagreements + random agreements) for a human
   check; `import-csv` records verdicts with source "human". Do it when the user can.
6. Record tier, model, prompt, dates, disagreement rate and adjudication counts in
   `eval/eval_spec.md`. Expect the silver reference to share the reference model's blind
   spots; a target that imitates them looks better than it is.

## LLM judges (generative tasks only)

Code first; a judge only for what code cannot decide. One criterion per judge. Anchored
rubric, structured output with evidence, emit classes and map to scores outside the model,
randomize pair order, forbid rewarding length, different/stronger model than the target. It
must fail an empty answer, "I don't know" and a confident answer to a different question.
Validate against human labels (Cohen's kappa via `common.cohen_kappa`); well below ~0.8 means
iterate the judge before trusting it. Judge cost is recorded separately.

## Validation and noise (`validate_eval.py`)

Before the loop: null test (missing outputs score at the floor), oracle test (reference-built
outputs reach the ceiling), corruption test, determinism, rescore of stored runs, two
baseline runs for the noise floor, and a manual spot-check of the worst baseline items
(read ~5-10: is the grader right, is the reference right?). If more than ~1 in 10 baseline
failures are grader/reference errors, fix the eval first.

`min_delta` = max(paired noise band of two identical runs, one item). If the noise floor is
not below the headroom, more rounds cannot show wins - enlarge dev, add repeats, or use a
finer metric, and tell the user.

## Changing the eval mid-loop

Allowed only to fix a real grader/reference error, never because a candidate "should" score
higher. Then: fix, note it in the journal and eval_spec, `run_experiment.py --rescore` every
run used in decisions, re-run `validate_eval.py`, and re-check whether the best candidate is
still the best. The ledger refuses decisions on stale runs.

## Naming numbers in reports

- "accuracy" only against gold. Otherwise "agreement with <tier/model> reference".
- Automation (auto-decided) rate, schema-valid rate, self-agreement, model-model agreement:
  report them, but not as accuracy.
- Report every outcome class against the original totals, including failed and excluded units.
- Few verified examples do not establish overall accuracy; give n and confidence intervals.
