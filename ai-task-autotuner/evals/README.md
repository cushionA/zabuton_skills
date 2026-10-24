# Behavioral evaluations for the design skill

These cases evaluate whether the **skill produces a sound task design and review**.
They are not the harness's Python unit tests and are not evidence that a live model
benchmark has passed. Adding or validating these files does not run a design agent,
a target pipeline, or an external model. No performance results are included here.

## Cases and scope

| IDs | Mode | Purpose |
|---|---|---|
| 1–2 | Integration; external data required | Preserve the original ecommerce workflows, including audit, authorization, measurement, and budget discipline. Replace `DATA_DIR` / `BRIEF_PATH` with real paths before use. |
| 3 | Offline design | Factor SKU matching into shared product context and conditional attribute mappings without inventing sparse variants or violating global capacity. |
| 4 | Offline design | Derive threshold, skew, candidate-count, output-cardinality, and token-budget tests. |
| 5 | Offline design | Preserve evidence and meaning through extraction, chunk boundaries, negation, and amendments. |
| 6 | Offline design | Distinguish valid invariance from required-change controls and handle unknown domains. |
| 7 | Offline design | Detect correlated false references and rare-case regressions without inventing an oracle. |
| 8 | Offline evaluation design | Distinguish legitimate holds, scoring errors, business denominators, rare-event release evidence, and adaptive selection bias. |

IDs 3–8 require only the supplied JSON fixtures and local deterministic inspection.
The design agent must not run target-model calls, upgrade a model, start an external
benchmark, or claim those measurements occurred. Local calculations are permitted.
If an independent reviewer is used in an authorized skill evaluation, record that
reviewer's overhead; do not mistake design/review calls for target-pipeline trials.

The fixtures are deliberately small, synthetic probes. Their written policies are
normative **only for these examples**, not asserted real ecommerce or business rules.
For example, one-to-one target capacity applies to case 3 because its policy says so;
case 4 explicitly permits many matches. Avoid generalizing either to every task.

## Candidate input and grader-only material

1. Resolve `FIXTURE_ROOT` to this directory's `fixtures/` path.
2. Give the candidate only its case's `prompt` and the files in `files`.
3. Keep `expected_output`, `assertions`, `naive_failure_modes`, and
   `expected_fixture_results` private to the evaluator. Do not include the entire
   `evals.json`, this README, or the grader's notes in the candidate's context.
4. Save the candidate output, relevant tool trace, tool errors, effective model and
   configuration, skill revision, and the hashes of the case and fixture.

This separation prevents the answer key from becoming an input feature. The legacy
reference columns in case 7 are intentionally visible **untrusted evidence**; they
must not be supplied to a production inference stage as if they were input facts.

Expected facts do not depend on a second model agreeing with the first. They follow
from explicit finite rules: equality and conditional dictionaries in case 3,
integer arithmetic in case 4, binding-date precedence and source text in case 5,
the stated ticket policy in case 6, strict types plus age thresholds in case 7, and
the business rules, arithmetic and conditional binomial calculation in case 8.
Case 8's zero-error bound assumes an IID representative sample of automatic decisions
from a rule fixed before sampling; its sample size is not a universal evaluation budget.
An evaluator should inspect these derivations before using the suite. A model may
help review an explanation, but cannot redefine these facts by voting. New cases
that need domain judgment require separately established labels or must be marked
diagnostic/unresolved; do not generate answers and then call them verified gold.

## Old-skill / new-skill / no-skill comparison

Use a snapshot of the old skill, the proposed new revision, and no skill as separate
conditions. Run each case in a fresh context/workspace with the same prompt,
fixture bytes, model, effort, available tools, and explicit resource limits. The
no-skill condition receives the same task and data, without the skill or its bundled
instructions. The old condition must not see the new revision. Record any platform
instructions that make isolation imperfect.

Predeclare the number of independent repetitions and the evaluation budget; a
small authorized pilot can use three repetitions per condition. Do not silently
expand that budget. Randomize condition order, save every run including failures,
and use anonymized outputs for grading. Do not share feedback across conditions.
Record unsupported settings and missing usage as unknown, not zero.

These are public development cases. Once used to edit the skill, they are regression
tests, not an unseen generalization test. Before making broad improvement claims,
add independently prepared tasks or variants that were not used during revision,
freeze their expectations, and evaluate once under the declared protocol. The
synthetic suite is not representative of a production workload distribution.

## Grading and false-positive accounting

Score each assertion independently:

- **2 — demonstrated:** the output gives the correct concrete result or a specific,
  applicable design/test with its preconditions and expected behavior.
- **1 — partial:** the relevant concern is mentioned, but the derivation, condition,
  test oracle, or required implementation detail is missing.
- **0 — failed:** absent, contradicted, unsupported, or wrong.

Report assertion-level results and evidence excerpts, not only an average. A case
is fully satisfied only when every assertion marked `critical` scores 2. An average
cannot compensate for invented SKU matches, lost evidence, hidden omissions, or
an incorrect oracle. Report exact fixture decision correctness separately from
design/rubric completeness. Expected null, unknown, ambiguous, and invalid-input
results are valid outcomes and remain in their original denominators.

Before grading open-ended issue findings, use the assertions and naive failure modes
to create a deduplicated issue inventory. Distinguish a **demonstrated defect**, a
**risk in a proposed strategy**, and an **unresolved question**. There is no existing
implementation to accuse of every possible risk in an offline design case.

For each candidate, record:

- Supported issues detected, with affected fixture IDs and a counterexample or rule.
- Expected issues missed.
- False positives: a claimed defect contradicted by the contract, an invalid
  metamorphic relation, an unjustified global STOP, or a claim of proof with no oracle.
- Additional valid findings, reviewed independently and added consistently to the
  inventory for all conditions; do not count a finding false merely because it was
  absent from the original list.

Deduplicate findings before computing issue recall and issue precision. Precision is
supported findings / all asserted findings; recall is expected issues detected /
expected issues. If a denominator is zero, report N/A. Unresolved questions and
explicitly conditional risks are not confirmed defects or false positives by default.
Track their usefulness separately so verbosity does not inflate issue counts.

Useful negative controls include legitimate no-matches and ambiguity in case 3,
many-match output in case 4, quoted negation in case 6, and the unresolved external
search disagreement in case 7. Case 8 includes a justified hold that must satisfy its
decision policy, plus an empty response on an answerable case that must fail. Its
predeclared invalid exclusions are reported separately from the valid business scope.
A reviewer must not flag these simply for differing
from a preferred pattern. Conversely, an always-null/always-unknown pipeline must
fail the positive and required-change controls.

## Quality and efficiency reporting

Report two distinct layers for every comparison:

1. **Design outcome:** exact fixture decisions, critical assertion failures, review
   issue precision/recall, useful tests produced, and supported versus speculative
   redesign claims. Do not collapse all of this into one opaque score.
2. **Design overhead:** the design agent and all delegated reviewers' input/output
   tokens, cached/reasoning tokens where reported, model calls, tool calls, elapsed
   time, and observed monetary cost. Identify the telemetry boundary and avoid
   counting cached/reasoning subsets twice. Keep unknown fields unknown.

For a proposed target pipeline, separately report **estimated serving work**: logical
job denominator, candidate counts, stage call counts, input/output representation,
expected output size, and costs that still need measurement. These offline probes
cannot establish target-model accuracy, tokenizer counts, p95 latency, or savings.
If later authorized, measure serving quality and usage on fixed evaluation units;
do not make fewer batches look cheaper by changing the denominator. Include retries,
fallbacks, and incomplete results. Keep reference creation and optimization cost
separate from serving cost and state the expected reuse volume for amortization.

Compare conditions per case and repetition, showing regressions and variability as
well as averages. Preserve failed and partial runs. Use the evidence to identify
tradeoffs: a better design may take more design tokens while reducing future serving
work. No such improvement is claimed merely by adding this suite.
