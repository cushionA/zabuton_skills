# Evaluation design, references and validation

Contents: metrics by task type · hard gates and failure classes · splits · reference tiers
and the silver protocol · LLM judges · validation and noise · changing the eval · naming numbers

For uncertain references, model judges or cost-sensitive acceptance, use
[evaluation-trust.md](evaluation-trust.md): sampling and adjudication, conditional judge
controls, risk/coverage denominators, threshold selection and sample-size assumptions.

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
Define the numerator and denominator of every error guard. An error fraction among automatic
decisions is different from one over all inputs. A justified hold may be the correct answer;
keep it in scope and report its operational cost separately.

### Metric direction and quality bounds

Set `primary.direction` to `max` when higher is better and `min` for error or loss. Quality
targets use `>=` for max and `<=` for min. The existing `quality_floor` field is a quality
bound: a lower floor for max, an upper ceiling for min. Its automatic value is incumbent
minus `min_delta` for max, incumbent plus `min_delta` for min.

`primary.item_direction` describes the individual `items[].score` values used in paired
comparisons. It defaults to `max` for correctness scores, independently of the primary
metric. Use `min` when item scores are losses. For example, an error-rate headline with
0/1 correctness items uses primary `min` and item `max`; per-item error indicators use both
`min`. Report item-score improvement intervals as such, not as primary-metric intervals.

Validator thresholds follow the primary direction:

| Direction | Missing-output threshold | Oracle threshold | Best attainable value |
|---|---|---|---|
| `max` | `null_max`, default 0 | `oracle_min`, default 0.999 | `optimum`, otherwise legacy `ceiling` or 1 |
| `min` | `null_min`, default 1 | `oracle_max`, default 0.001 | `optimum`, default 0 |

The min defaults assume normalized error. Set explicit finite thresholds and `optimum` for
another scale. An oracle must meet its threshold without scoring beyond the declared
optimum; the oracle threshold must be better than the missing-output threshold. Headroom is
the distance from the observed primary to that optimum in the configured direction.

## Hard gates and failure classes

- Hard gates in `score()` -> `gate_failures`: output schema, every required item answered
  exactly once, only real ids referenced (no fabricated keys/rows). Any gate failure makes the
  run invalid for keeping. These checks and configured guards also apply to the first
  baseline; `--override keep` cannot bypass hard measurement constraints or guards.
- Cover the whole output contract: type and null errors, duplicates, invalid evidence ids,
  required add-on conditions and unresolved choices. Test a correct class/id with missing or
  contradictory conditions; it must not silently receive full task success. If only class/id
  agreement is measured, name it that way and report the other requirements separately.
- The template rejects malformed output objects, non-list or null `items`, malformed rows,
  missing labels, duplicate keys and unknown unit/item ids. A malformed unit receives zero
  across its expected scored rows plus hard-gate failures; it cannot gain credit from a
  duplicate's last value. Missing required rows remain in the denominator. A present JSON
  label may legitimately be `null`, `false`, `0` or a JSON container; missing the label field
  is a different case. JSON booleans remain distinct from numbers: `true` is not label `1`,
  nor `false` label `0`, including inside arrays or objects. Explicit reference exclusions
  remain unscored and may be omitted.
- Reference creation uses strict `flatten`: malformed or duplicate predictions refuse the
  merge before existing references are rewritten. Well-formed partial output is allowed
  there, but expected-item coverage must still pass after reference adjudication.
- **System failures** (refusal, unparseable or truncated output, exception in the pipeline)
  are scored as wrong - never dropped.
- **Harness failures** (timeouts, rate limits, CLI crash) are missing data. The runner marks
  them `harness_error`; a run with clearly more of them than the best is `void` and re-run.

## Splits

- `dev`: tuning; read freely. `holdout`: sealed until phase switch/final. `full`: the whole
  in-scope set for time/usage/outcome distribution; accuracy only where references exist.
- Random, stratified by the audit's strata, **group-aware** (`group_of`: near-duplicates,
  same source page, same parent product stay in one split). Never choose by baseline score.
- Size dev for the effects and error bounds that matter, accounting for source-group
  correlation. For independent binary observations the worst-case normal-approximation 95%
  half-width is about `0.98/sqrt(n)`; this is not a rare-error bound or a paired power analysis.
  Prefer more independent products/documents over many correlated items from a few groups.
- Predefined subsets in the data (e.g. "smoke_10", "largest_20") are fine as dev/holdout if
  they are disjoint after grouping; record which ids and why.

The runner checks the actual loaded ids against held-out membership, including ids declared
in `splits.json` and returned by the holdout loader. A `full` run or a custom split alias
containing those ids is a holdout evaluation. Code-only candidates count too. Target
evaluations that include held-out ids require `--purpose holdout` or `--purpose full`, a
current PASS review and a current PASS eval validation. A smoke, probe, baseline or experiment
purpose cannot expose holdout by another name. Holdout reference preparation is the separate exception
below; it does not score target performance or count as a target peek.
Thresholds and judge prompts are tuned on development/calibration data, not on repeated
final holdout feedback. A peek cap is not a guarantee against adaptive overfitting; see
[the selection protocol](evaluation-trust.md#define-loss-denominators-and-a-selection-protocol).

## Reference tiers

| Tier | Source | May be called |
|---|---|---|
| gold | human-adjudicated or independently verified expected outcomes, with provenance | accuracy on that defined scope |
| verified silver | silver + human check of a stated sample | agreement with reference (sample-verified) |
| silver | configured reference-model variants + adjudication | agreement with <model> reference |
| same-model reference | target model x2 + adjudication; no independent correctness check | agreement with same-model reference |
| none | - | only coverage, consistency, rule violations |

### Silver protocol (no labels)

1. Follow `reference_model` in the charter. On Codex it defaults to Luna (`gpt-6-luna`), as
   does the target; never automatically switch to a stronger model. Record this as a same-model
   reference and disclose that agreement may measure shared errors/self-similarity, not
   independent correctness. A different/stronger reference must be explicitly configured;
   it may still share errors with the target. Claude's default remains `opus` (or an explicitly
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
   `reference.py merge WS RUN_A RUN_B --split dev` (needs `score.flatten`). It refuses the
   same run twice, non-reference purposes, another split, outputs from changed data and
   candidates in the experiment ledger, and records the merged runs in
   `eval/reference/<split>.merge.json`.
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

Record the sampling method and evidence for reviewed agreements as well as disagreements.
A human-checked sample does not promote all other labels to gold. Treat source-supported
uncertainty as an explicit outcome, not a reference defect to exclude; finite multiple correct
answers can use `acceptable`, while open-ended correctness may need a semantic rubric.

## LLM judges

Code first; a judge only for semantics code cannot decide. Use an anchored rubric, separate
criteria and structured verdicts with evidence, mapped to scores outside the model. Validate
on independent labeled cases, including decisive errors, order/identity bias, equivalent
wording, verbosity and instructions embedded in evaluated answers. Empty or generic
abstention fails when an answer is required; justified abstention must pass when the contract
requires it. Always-accept and always-hold judges must fail the appropriate controls.
Report false passes/failures by criterion and stratum, with uncertainty. Agreement and
Cohen's kappa can supplement these measures; no universal kappa cutoff or published agreement
percentage validates a different task. Test reasoning or few-shot additions rather than
enabling them by default. Judge cost is recorded separately.
Use only an explicitly configured judge model; never upgrade Codex Luna automatically to
satisfy this requirement. Without an independent validated judge, report that limitation
rather than calling same-model judgments independent correctness.
No judge runner is bundled. A task-specific integration must preserve verdicts/provenance
for deterministic rescoring and invalidate grades when evaluated outputs or judge policy
change; see [judge validation](evaluation-trust.md#validate-a-judge-against-the-decision-it-must-make).

## Validation and noise (`validate_eval.py`)

Reference-based scorers implement `reference_coverage(units, split)` for both dev and holdout.
Return expected **item**, not just unit, counts: `{"covered": N, "total": N, "unresolved": 0}`
(`covered_items`/`total_items` are also accepted). Counts must be finite non-negative integers,
`total` must be positive and cover every expected item in that split, `covered == total`, and
`unresolved == 0`. Explicit, documented exclusions count as covered but remain unscored and
reported separately. Missing/malformed reference records must fail coverage rather than
shrink the expected total or scoring denominator. Genuinely reference-free tasks may omit the
hook; do not remove it to hide incomplete references.

Before the loop: null test (missing outputs score at the configured worst threshold), oracle
test (reference-built outputs reach the configured optimum), corruption test, determinism,
rescore of stored runs, two baseline runs for the noise floor, and source-based spot-checks
of baseline successes and failures. Select a bounded sample from the relevant risks; correct
confirmed grader/reference defects before tuning against them. A targeted failure sample is
not an estimate of the overall reference error rate. The script checks the presence of the
spot-check note, not whether a human actually verified its contents. The oracle test checks
scorer/reference consistency, not whether the reference itself is true.

The two noise runs must be distinct run directories and each must cover exactly the full
dev unit set. Metadata ids and output-row ids must be unique and complete; partial limits,
unknown units, duplicate rows and passing the same run twice cannot validate a smaller or
artificially identical sample. Stored item scores must reproduce from the outputs as well as
the stored primary; noise cannot rely on edited or reduced item evidence. Repeats need the
same item set and stable unit assignments so clustered comparisons keep the same sampling
units. The same requirements apply to `--renoise`. When oracle
outputs are available, a supplied `corrupt` hook must worsen the primary in its configured
direction; a broken hook fails validation rather than producing an optional warning.

`min_delta` = max(paired noise band of two identical runs, one item). The band is computed
over units (all items of a unit together), because items inside a unit flip together: in one
test a single 60-row product flipped between identical runs and moved the score by 0.08 while
an item-independent band claimed 0.03. When a few units carry most items
(`top5_unit_item_share` in the noise record), run candidates more than once, prefer more units
over more items per unit, and report unit-bootstrap intervals (`paired_items` gives `ci`). If the
noise floor is not below the headroom, more rounds cannot show wins - enlarge dev, add repeats,
or use a finer metric, and tell the user.
These diagnostics do not establish a formal sequential-testing or release guarantee. The
ledger's normal KEEP rule uses `min_delta`, guards and paired item improvements; it does not
require a confidence interval excluding zero or correct for every adaptively tried candidate.

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
higher. The eval hash includes the source-data fingerprint, loader, splits, scorer,
references, the full `primary` contract and guards. Changing direction, thresholds or guards
therefore invalidates the old validation. Each run pins a separate generation-time
`meta.data_hash`: input and auxiliary
file bytes, source roles and order, scope, loader and splits. File locations are excluded so
moving identical data preserves identity; directory-relative contents still matter.

For scorer/reference or metric-contract-only changes: fix, note it in the journal (reference corrections also in
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
- New scorer templates use the neutral `reference_agreement` metric key. Existing custom
  scorers are not rewritten automatically; when renaming an old `accuracy` metric, update
  dependent guards/reporting, version the evaluation and rescore as above.
- Automation (auto-decided) rate, schema-valid rate, self-agreement, model-model agreement:
  report them, but not as accuracy.
- Report every outcome class against the original totals, including failed and excluded units.
- Few verified examples do not establish overall accuracy; give n and confidence intervals.
