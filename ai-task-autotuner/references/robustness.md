# Input models, conditional tests and pipeline rewrites

Read during Design and when a failure challenges an assumption. Choose checks from the task's
risks; this is not a requirement to run every test family or a fixed number of model calls.
Record the selected contracts in `review/design_contracts.json` and keep detailed fixtures,
commands under `review/probes/`, with results outside that directory. Start with bounded code-only probes. Use model calls
only for behavior the code cannot establish, within the existing budget and run gates.

## Model the input before choosing probes

Describe both the structure and the parameters of the supported input space:

- **Structure:** entities and relationships, nesting, ordering, shared context, identifiers,
  cardinalities (one-to-one, one-to-many, many-to-many), and constraints spanning records.
- **Parameters:** meaningful values and ranges, sizes and skew, missing/duplicate/conflicting
  fields, representation or language variants, and dependencies between parameters. Include
  domain meaning: equal numbers in different roles may differ; different numbers with
  convertible units may agree.
- **Boundary:** distinguish valid difficult inputs from invalid inputs and valid but unsupported
  inputs. For each, specify normal results, explicit rejection, abstention or escalation.
  Difficulty alone is not a reason to exclude a case or treat it as broken source data.

Start from the audit, task requirements and implementation assumptions. A collection of
typical examples is not a complete input model. Retain identifiers and group membership for
generated probes; use synthetic or approved development material, never sealed holdout data.

## Record the design contracts

Fill the template created by initialization (or seeded by migration), then run
`python SKILL_DIR/scripts/design_contracts.py WS`. This checks completeness and references,
not correctness or execution. The version-1 document contains:

| Field | Required content |
|---|---|
| `input_model` | Nonempty `business_unit`, `processing_unit`, `call_unit`, `valid_domain`, `invalid_input_policy`, `unsupported_input_policy`, `size_model` strings |
| `assumptions` | Nonempty entries with unique `id`, `statement`, `basis`; state the input or decision assumptions the design relies on |
| `contracts` | Nonempty entries with `id`, `kind` (`example`, `property`, `metamorphic`, `scale`, `fault`), `input_class` (`valid`, `invalid`, `unsupported`), known assumption IDs in `assumptions`, and nonempty `precondition`, `transformation`, `expected_relation`, `oracle`, `tolerance`, `failure_action`, `check`; include at least one valid-input contract |
| `coverage` | Exactly `boundary`, `growth`, `relations`, `skew`, `interaction`, `prompt_context`; each has either a nonempty `contracts` list of known IDs or a reason in `not_applicable` |
| `rewrites` | Entries with `id`, `change`, `applicability`, `preserved_semantics`, `risks`, and known contract IDs in `validation_contracts`; if empty, provide `no_rewrite_reason` |

Set top-level `version` to `1`. Contract `check` names the runnable command or concrete
review procedure. For an unchanged example, state that its transformation is the identity;
for an exact invariant, state zero tolerance. Do not insert vague filler to pass validation.
Reuse one contract across relevant coverage dimensions; do not invent tests for every kind.
At least one coverage dimension must reference a contract; marking all dimensions inapplicable
does not provide a concrete check for the design.

Definitions and optional fixtures under `review/probes/` are frozen by the design review.
Put execution records in `review/contract_results.jsonl` or another file outside `probes/`:
contract ID, candidate/run ID, status (`passed`, `failed`, `not_applicable`, `not_run`,
`inconclusive`), and evidence or reason. Appending results does not invalidate the review;
changing a definition or fixture does. There is no automatic contract-test runner: the author
executes applicable checks and the reviewer challenges their conditions, oracles and evidence.
Never turn a definition-only validation result into a robustness claim.

## Choose tests conditionally

Each selected test needs an applicability condition, the behavior it checks, a bounded
fixture/generator, an oracle or relation, and evidence. Explain why an unselected family is
irrelevant where that omission would hide a material risk. A planned model probe is not an
observed pass; separate design readiness from measured robustness.

| Family | Use when | Contract and a useful small probe |
|---|---|---|
| Example/boundary | A domain rule or difficult outcome can be stated concretely | A minimal positive, near miss, and boundary with an acceptable answer set or required abstention |
| Property | Code or output must satisfy an invariant over a defined domain | Generate valid inputs; check ID existence, uniqueness, coverage, constraint preservation, or round trips; shrink failures |
| Metamorphic | An exact answer is costly but a relation between answers is known | State the precondition and expected invariance or directional change; test reordering only when order has no meaning |
| Scale | Input size, fanout, skew, repeated context or output size can change feasibility | Vary the relevant sizes independently; measure calls, tokens, runtime, memory where relevant, and output completeness |
| Fault/recovery | A dependency or stage can fail, time out or return malformed/partial data | Inject the failure locally; verify bounded retries, explicit outcomes, preserved denominators and no false success |

Change one factor first to localize a failure, then test risk-targeted interactions: e.g.
missing attributes with duplicate candidates, or large fanout with a partial model response.
Use constrained pairwise or higher-order combinations when the input model warrants them;
do not enumerate impossible combinations or claim that pairwise coverage proves correctness.
Do not turn an alternative-input requirement (`A OR B`) into a stricter `A AND B` rule while
generating tests or splitting a task. Preserve predicates and aggregation logic explicitly.

Use deterministic properties for code. For a stochastic model, state allowed answers,
failure-rate limits or repeated paired measurements as appropriate; exact rerun equality is
not a universal property. A confirmed rare violation of a hard contract remains a defect even
if its effect on aggregate accuracy is below `min_delta`. Statistical significance measures
evidence for an aggregate difference, not the severity of a counterexample. Do not require a
small p-value before fixing a real fault, or use one as proof of practical importance.

## Preserve decisions while changing the implementation

Write the semantic decision contract before comparing rewrites: what decision is required,
acceptable answer sets, evidence obligations, global constraints, and how uncertainty or
failure is represented. Compare implementations against this contract and the frozen scorer,
not against literal equality with the old model's answers. An old answer may itself be wrong.
Keep exact equality for deterministic outputs only where the contract requires it.

Separate three units: the fixed business/evaluation unit, the processing group, and the model
call. A larger batch must not change the denominator or hide failed items. `run_all(units, ctx)`
can group existing evaluation units and return results keyed by their original IDs. A compact
model output may be expanded by code into the full task output; score that final output.

Consider the smallest relevant set of alternatives: deterministic preprocessing and joins;
shared representations or attribute maps; candidate retrieval with measured recall; batching;
split extraction and comparison; focused verification or cascades; and removal of unnecessary
stages. Record what each rewrite assumes and which check would disprove it. Do not assume a
decomposition improves both quality and cost: test the complete pipeline after local changes.

Global constraints do not automatically require one giant prompt. Compare partitioning with
boundary reconciliation, shared state or sufficient summaries, and a deterministic global
solver (for example assignment or constraint solving) when these preserve the decision.
If a dependency cannot be preserved, reject that rewrite or measure an explicitly bounded
approximation under the existing task contract. Do not silently change the task to make a
candidate win.

For a suspected stage bottleneck, replace one stage's output with verified/oracle input on
approved development cases and run the downstream path. This estimates downstream headroom
under that intervention. It does not prove the upstream model is incapable or that all real
failures have one cause: oracle inputs may change the downstream distribution, and stages can
interact. Confirm the proposed repair end to end.

## Measure growth and token work

Define the relevant sizes (e.g. `N` left records, `M` right records, `K` required output links)
and state the expected growth of each stage. Measure representative small, typical and large
valid inputs within the supported range, including skew when relevant. Report actual sizes,
calls, input/output tokens, time and completeness; distinguish a complexity argument from a
measured scaling trend. Include output work: producing `K` explicit links costs at least
proportional to `K`, even if candidate generation avoids `N * M` comparisons.

Count total input including cache reads/writes separately from billable input, and keep output
tokens separate. Attribute growth to repeated common context, candidate lists, examples,
intermediate prose, final results or retries. A lower call count alone does not establish
lower token use. Preserve quality and the same business denominator; report unknown usage as
unknown and disclose the chosen efficiency objective and its blind spots.

## SKU application: product context plus variant mappings

For an established product pair, compare a design that sends product context once, asks the
model for attribute-axis/value mappings and exception conditions, then joins **existing** SKU
rows in code. Preserve dependent attributes, counts, bundles, optional additions, missing
values and many-to-many matches. Never manufacture purchasable variants by expanding every
attribute combination. Route ambiguous or conditional cases to a focused model pass or the
task's review outcome.

If the mapping yields valid normalized keys, a hash join can take expected `O(N + M + K)`
work after mapping. This is conditional: building mappings, resolving dependencies and
producing a large `K` may dominate. Test skew and collisions, verify that pruning retains
correct matches, and score final SKU decisions and required conditions. If product pairing
is also unknown, evaluate its candidate retrieval separately. This SKU design is an
application of the principles below, not an example reported by those papers.

## Prompt and context behavior

Inspect the prompt as an executable decision specification: consistent predicates, explicit
abstention, sufficient evidence, clear data boundaries, concise examples and a minimal output
contract. Select relevant input without losing distinguishing evidence. Check misleading
instructions inside data, conflicting evidence, label/ID confusion, distractors and ordering
effects when applicable. Changes to negation, units or evidence may require a changed answer;
do not label every textual perturbation an invariance test.

Use short model explanations only when an experiment shows they help or the task requires
evidence. Test instruction wording, example selection, representation and decomposition as
different hypotheses. Examples must be group-disjoint from selection-dev and holdout; keep
their IDs/provenance. Compare a rewrite with the same evaluation and measure the saved tokens
as well as any new failures. More words, reasoning or stages are not automatic improvements.

## Sources and what is adapted

- [NIST: input space modeling](https://www.nist.gov/publications/input-space-modeling-methodology-combinatorial-testing?pub_id=913205)
  separates structural relationships from parameters, values and constraints; the workflow
  above applies that distinction to AI task inputs.
- [NIST SP 800-142](https://csrc.nist.gov/pubs/sp/800/142/final) motivates constrained
  interaction testing; select interaction strength from risk rather than treating it as proof.
- [CheckList](https://aclanthology.org/2020.acl-main.442/) motivates behavioral tests beyond
  held-out accuracy, including minimum functionality, invariance and directional expectations.
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/) supplies a practical pattern for
  domain-constrained generated tests and small reproducible counterexamples. It is optional;
  bounded standard-library probes are sufficient when they test the relevant property.
- [DocETL, PVLDB 18:3035](https://www.vldb.org/pvldb/vol18/p3035-shankar.pdf) motivates
  evaluating rewrites of the task, data or both, rather than only changing prompt wording.
- [MOAR, PVLDB 19:3062](https://www.vldb.org/pvldb/vol19/p3062-shankar.pdf) motivates joint
  quality/cost search and evaluating interactions in complete rewritten pipelines. Its
  benchmark results do not establish gains for this task or justify a fixed call budget.
