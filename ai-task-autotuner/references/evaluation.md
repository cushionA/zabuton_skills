# Evaluation design, references and validation

Contents: metrics by task type · hard gates and failure classes · splits · reference tiers
and the silver protocol · LLM judges · validation and noise · changing the eval · naming numbers

## Before baseline: review the draft

Write the draft spec, loader, scorer, splits and baseline candidate during Design, then obtain
a current PASS [independent task review](task-review.md) before baseline or reference runs.
This reviews the task and measurement design; `validate_eval.py` subsequently tests the
implementation against real runs. Neither replaces the other. If review finds a mismatch,
fix it and re-review the affected scope before freezing the evaluation.

After the review, keep task and evaluation policy in `eval/eval_spec.md` fixed. Store actual
reference-build provenance separately in `eval/reference_provenance.json`: reference tier,
models requested/served, prompt paths and versions, run ids, dates, disagreement rates,
adjudication records/counts and human-check scope. This generated file is not part of the
task-design fingerprint. Creating reference labels or recording their provenance does not
by itself require another task review.

## Metrics by task type

Score items at the finest stable granularity (one label, one matched row, one extracted
field) so paired comparisons and failure tags work. Pick one primary metric and guard metrics
that must not get worse.

| Task | Primary (typical) | Guards / diagnostics |
|---|---|---|
| Classification | accuracy or macro-F1 (skewed classes) | per-class recall, majority-class baseline |
| Extraction | field-level exact match after normalization | missing-field rate, fabricated-value rate |
| Matching / reconciliation with "no match" and "unsure" | correct-decision rate over all items | false-accept (wrong match) rate, auto-decided coverage, precision of decided items |
| Retrieval / selection among candidates | top-1 correct incl. correct "none" | false-positive rate on no-answer queries, recall@k of any pre-filter; report present vs removed queries separately |
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
- Cover the whole output contract: type and null errors, duplicates, invalid evidence ids,
  required add-on conditions and unresolved choices. Test a correct class/id with missing or
  contradictory conditions; it must not silently receive full task success. If only class/id
  agreement is measured, name it that way and report the other requirements separately.
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

The runner checks the actual loaded ids against held-out membership, including ids declared
in `splits.json` and returned by the holdout loader. A `full` run or a custom split alias
containing those ids is a holdout evaluation. Code-only candidates count too. Target
evaluations that include held-out ids require `--purpose holdout` or `--purpose full`, a
current PASS review and a current PASS eval validation. A smoke, probe, baseline or experiment
purpose cannot expose holdout by another name. Holdout reference preparation is the separate exception
below; it does not score target performance or count as a target peek.

## Reference tiers

| Tier | Source | May be called |
|---|---|---|
| gold | human labels with stated provenance | accuracy |
| verified silver | silver + human check of a stated sample | agreement with reference (sample-verified) |
| silver | strong model x2 + adjudication | agreement with <model> reference |
| same-model reference | target model x2 + adjudication; no independent correctness check | agreement with same-model reference |
| none | - | only coverage, consistency, rule violations |

### Silver protocol (no labels)

1. Follow `reference_model` in the charter. On Codex it defaults to Luna (`gpt-6-luna`), as
   does the target; never automatically switch to a stronger model. Record this as a same-model
   reference and disclose that agreement may measure shared errors/self-similarity, not
   independent correctness. Strong independent silver requires an explicitly configured
   different/stronger reference model; Claude's default remains `opus` (or an explicitly
   selected `sonnet`). Never use the candidate being tuned to produce its own labels. Smoke
   one unit and extrapolate to two runs x (dev + holdout); report supported costs and quota
   exposure first, leaving unknown costs unknown. Do not infer Luna pricing from another model.
2. Write a careful reference candidate (`candidates/ref-*`): full instructions, per-item
   evidence, no cost cutting. Make the two runs differ - two prompt/representation variants,
   or two explicitly configured reference models: the same prompt twice mostly measures the model's self-consistency
   (in one test, 0 of 544 holdout items disagreed), and agreement between the runs is never
   verification.
3. Run the reference candidate twice on dev and holdout (`--purpose reference`) after task
   review. Holdout reference preparation writes outputs without target grading or a target peek;
   never use the reference path to test the candidate being tuned. Then
   `reference.py merge WS RUN_A RUN_B --split dev` (needs `score.flatten`).
4. Adjudicate every disagreement by reading the source data (or with a subagent for
   volume): a label, an `acceptable` set for genuinely ambiguous items, or `exclude` for data
   defects (report them). For holdout, delegate adjudication to a subagent and look only at
   counts, so holdout content does not enter your context (no subagent tool: adjudicate with a
   configured reference model through `llm_call.py` and read only the counts; do not
   automatically upgrade it). Then run a consistency pass:
   group adjudicated and agreed items by pattern (same kind of option, same unit mismatch) and
   check that equal patterns carry equal labels - inconsistent references surface later as
   unexplainable candidate failures and force an eval version change.
5. `reference.py export-csv` gives a sheet (disagreements + random agreements) for a human
   check; `import-csv` records verdicts with source "human". Do it when the user can.
6. Record the actual tier, model, prompt versions, run ids, dates, disagreement rate,
   adjudication records/counts and human-check scope in `eval/reference_provenance.json`.
   Keep the reviewed reference-building and label policy in `eval/eval_spec.md`; do not edit
   it just to add run metadata. Expect the silver reference to share the reference model's
   blind spots; a target that imitates them looks better than it is.

## LLM judges (generative tasks only)

Code first; a judge only for what code cannot decide. One criterion per judge. Anchored
rubric, structured output with evidence, emit classes and map to scores outside the model,
randomize pair order, forbid rewarding length, different/stronger model than the target. It
must fail an empty answer, "I don't know" and a confident answer to a different question.
Validate against human labels (Cohen's kappa via `common.cohen_kappa`); well below ~0.8 means
iterate the judge before trusting it. Judge cost is recorded separately.
Use only an explicitly configured judge model; never upgrade Codex Luna automatically to
satisfy this requirement. Without an independent validated judge, report that limitation
rather than calling same-model judgments independent correctness.

## Validation and noise (`validate_eval.py`)

Reference-based scorers implement `reference_coverage(units, split)` for both dev and holdout.
Return expected **item**, not just unit, counts: `{"covered": N, "total": N, "unresolved": 0}`
(`covered_items`/`total_items` are also accepted). Counts must be finite non-negative integers,
`total` must be positive and cover every expected item in that split, `covered == total`, and
`unresolved == 0`. Explicit, documented exclusions count as covered but remain unscored and
reported separately. Missing/malformed reference records must fail coverage rather than
shrink the expected total or scoring denominator. Genuinely reference-free tasks may omit the
hook; do not remove it to hide incomplete references.

Before the loop: null test (missing outputs score at the floor), oracle test (reference-built
outputs reach the ceiling), corruption test, determinism, rescore of stored runs, two
baseline runs for the noise floor, and a manual spot-check of the worst baseline items
(read ~5-10: is the grader right, is the reference right?). If more than ~1 in 10 baseline
failures are grader/reference errors, fix the eval first.

`min_delta` = max(paired noise band of two identical runs, one item). The band is computed
over units (all items of a unit together), because items inside a unit flip together: in one
test a single 60-row product flipped between identical runs and moved the score by 0.08 while
an item-independent band claimed 0.03. When a few units carry most items
(`top5_unit_item_share` in the noise record), run candidates more than once, prefer more units
over more items per unit, and report unit-bootstrap intervals (`paired_items` gives `ci`). If the
noise floor is not below the headroom, more rounds cannot show wins - enlarge dev, add repeats,
or use a finer metric, and tell the user.

Holdout diagnostics: `analyze_run.py` on a holdout run prints aggregates only (failure tags,
reference->prediction confusion, failures-per-unit histogram). Those are allowed; ids and texts
are not. Repeated runs with the same candidate identity, bytes and effective settings count
as one peek, including repeated holdout-bearing `full` runs. Model, effort and actual concurrency
are part of those settings. They freeze at the first non-smoke measurement; changing them needs
a new candidate. Historical runs with different settings count separately and cannot be pooled
as identical repeats. Missing legacy settings cannot establish that two runs are the same.
Renaming the purpose or split does not reset the cap.

## Changing the eval mid-loop

Allowed only to fix a real grader/reference error, never because a candidate "should" score
higher. The eval hash includes the source-data fingerprint as well as loader, splits, scorer
and references. Each run pins a separate generation-time `meta.data_hash`: input and auxiliary
file bytes, source roles and order, scope, loader and splits. File locations are excluded so
moving identical data preserves identity; directory-relative contents still matter.

For scorer/reference-only changes: fix, note it in the journal (reference corrections also in
`eval/reference_provenance.json`), `run_experiment.py --rescore` every run used in decisions,
re-run `validate_eval.py`, and re-check whether the best candidate is still the best. The
ledger refuses decisions on stale runs. Update `eval_spec.md` only if the task/evaluation
policy changes, not for reference content or run metadata alone.

For changed input/auxiliary bytes, source roles/order/scope, loader or splits: audit and review
the changed design as needed, then generate new runs and validate that generation. Existing
outputs cannot be made current with `--rescore`; their original `data_hash` remains immutable.
Runs without verifiable generation provenance cannot be reused to certify the new data.

Changes to the reviewed spec, scorer, loader or splits also make the task review stale.
Before new measured runs, prepare a new request and have the independent reviewer check the
change and its affected scope; unchanged checks may cite the prior evidence after confirming
it still applies. Generated reference labels, `reference_provenance.json` and ordinary tuning
candidates are outside the design fingerprint, so updating them alone does not demand another
task-design review. Reference corrections still require evaluation revalidation and rescoring.
If reference work reveals a changed label policy, metric or task definition, update the spec
and review that change.

## Naming numbers in reports

- "accuracy" only against gold. Otherwise "agreement with <tier/model> reference".
- Automation (auto-decided) rate, schema-valid rate, self-agreement, model-model agreement:
  report them, but not as accuracy.
- Report every outcome class against the original totals, including failed and excluded units.
- Few verified examples do not establish overall accuracy; give n and confidence intervals.
