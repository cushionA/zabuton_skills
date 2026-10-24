# Final report

Write `WS/report.md` in the user's language. Every number must trace to a file under `WS/`
(runs, ledger, audit). Prefer tables. Mark anything not measured as "not measured" or "not
reported by the backend" - never fill gaps with estimates presented as measurements.

## Required sections

1. **Summary** - what was built, the recommended configuration, the holdout result vs
   baseline (with interval and reference tier), cost and time per unit, the main limitation.
2. **Method and settings** - pipeline structure of the final candidate (calls per unit, rule
   steps, prompts by path), backend, CLI/SDK version, model requested/served, effort,
   concurrency, retries/timeouts. Extra data, derived vocabularies or external fetches, kept
   separate from the "provided inputs only" condition.
   Distinguish fixed business/evaluation units, processing groups and model calls. Explain
   compact intermediate representations, code expansion and preserved global constraints.
3. **Scope** - units and items in scope vs original totals; exclusions with reasons and counts;
   subsets used for dev/holdout/full with ids file paths; auxiliary/load-test data kept apart.
   State the valid domain and size/skew range, and handling of invalid versus unsupported
   inputs. Keep group-disjoint few-shot examples separate from selection-dev and holdout.
4. **Data audit** - gate result, findings by severity, known limitations, data defects found.
5. **Evaluation** - primary and guard metrics, reference tier and how it was built
   (model, runs, disagreement rate, adjudication counts, human-checked sample size), splits
   and grouping, noise floor, validation checks. What may be called accuracy and what not.
   Link the reviewed policy in `eval/eval_spec.md` and the actual reference-build records in
   `eval/reference_provenance.json`; keep run metadata out of the frozen policy document.
   State human/independent verification scope, sampling method and unresolved/excluded cases.
   For model judges report calibration and independent validation, false passes/failures,
   bias controls and actual judge cost. Separate targeted stress results from population estimates.
   Link `review/design_contracts.json` and separate execution evidence: selected conditional
   tests, assumptions, oracle/relation, tolerance and passed/failed/inconclusive/not-run status.
   Explain material non-applicability. Definition validation is not execution or correctness.
6. **Results** - per outcome class (e.g. matched / no match / unsure / conditional /
   failed / not processed) counts against the total, which classes count as auto-decided;
   breakdown by the audit's strata (asymmetric cases, sizes, missing text...); quality on dev
   and holdout; pass^k and flaky items. Do not present attribute-level or candidate-rank
   numbers as overall correctness.
   Report confirmed rare contract failures separately from aggregate score changes; severity
   is not determined by a p-value. A stage-oracle probe is diagnostic evidence under an
   intervention, not proof of the cause of every end-to-end error.
   Define automatic-decision coverage and error denominators, false-accept/reject/hold costs,
   threshold-selection data and final evaluation scope. State the assumptions and confidence
   level for any error-rate bound; zero observed errors is not zero population risk.
7. **Time** - run wall time, sum of unit times, per unit and per item (state which is an
   apportioned average and which is a measured per-call latency), median/p90/slowest units,
   concurrency, cold vs warm cache, retries and timeouts.
8. **Usage and cost** - calls, input/output/cache-read/cache-write/thinking tokens per unit
   and total, cost with its basis, uncached comparison, harness overhead (calibration) noted.
   Distinguish measured zero (including a code-only candidate) from missing/unreported cost.
   An uncached cost estimate remains unknown if any required token component is unreported.
   Positive -> zero efficiency is a 100% reduction; zero -> zero is a tie. State any observed
   limit overrun caused by calls already in flight rather than claiming an absolute USD cap.
   Show total input (including cache reads/writes) and output separately. State the efficiency
   objective and its limits: fewer calls alone does not establish fewer tokens. For applicable
   scale checks, show actual input sizes (`N`, `M`, fanout/skew) and required output `K` alongside
   tokens, time and completeness; label predicted growth separately from measured results.
9. **Improvement history** - ledger table: experiment, lever, hypothesis (short), delta,
   decision; what moved the score and why; what did not work (dead ends are findings too);
   phase switch point; stop reason.
    Include the source-data generation identity for compared runs. If data, loader or splits
    changed, keep the generations separate; rescoring old outputs does not measure new inputs.
10. **Examples** - representative successes, failures and holds with the evidence used
    (ids, short quotes), especially for the hard strata.
11. **Rules, dictionaries, maintenance** - size of rules/vocabularies/prompts, what would need
    updating when the data or site changes, expected maintenance effort.
12. **What code alone achieves** - the best no-model candidate, the share of items code decides
    in the final pipeline and its agreement; if code alone is within noise of the best, say so
    first in the summary (details in `non_ai_findings.md`).
13. **Production settings** - from `deliverables/production_config.json`: max_tokens, timeout,
    concurrency, effort/thinking, caching, expected cost and latency per unit and per 1,000
    units, each with the measurement it comes from and the backend caveats.
14. **Limitations and next steps** - reference bias, data not independent of past
    development, untested backends, levers left untried, what more budget would buy.

## Replay package

Write the final design and report in `WS/` before running `export_deliverables.py`. Export to
an empty destination; use `--out <new empty directory>` for a later export. Select runs of
the recommended candidate with the same effective measured settings, so the production
recommendation has a single reproducible configuration.

The package includes the selected pipeline, the baseline candidate checked by task review,
design/eval files, audit and any recorded user acceptance, task review, ledger, run
metadata/summaries/scores, selected calls/outputs and baseline outputs needed for validation.
It also retains candidate code for all currently accepted experiments needed for comparison
or rollback.
Include the contract definitions, relevant fixtures and separate execution evidence under
`review/`; definitions and observations serve different purposes during replay.
Unrelated raw prompts/calls/outputs are omitted; rescoring those historical runs needs the
original workspace. `replay.json` lists retained history and external dependencies. Supplied
raw inputs outside the workspace are not silently copied into the package.

Before replay on another machine, use:

```bash
python SKILL_DIR/scripts/relocate_workspace.py PACKAGE --data-map OLD_DATA_PREFIX=NEW_DATA_PREFIX --dry-run
python SKILL_DIR/scripts/relocate_workspace.py PACKAGE --data-map OLD_DATA_PREFIX=NEW_DATA_PREFIX
```

Bundled workspace paths are mapped automatically; external inputs/auxiliary files and briefs
need explicit maps if their original paths are unavailable. Relocation checks the audited
data and reviewed design bytes before writing any path or approval record. It preserves an
existing acceptance only for verified identical content; changed content requires audit and
review. It cannot fabricate an approval. Original STOP, time/budget and holdout history remain
in force after export and relocation. A package can be inspected even when those controls
prevent more measurements; list unavailable final checks as not run.

## History table (from the ledger)

```
| exp | lever | change | dev primary | delta | cost/unit | decision |
```
`ledger.py history WS` prints it; include discarded rows - they show the search.

## Final message to the user

Short, in their language: what was built, the headline numbers with their names (accuracy vs
agreement), whether code alone suffices for some or all of it, the recommended settings, where
the deliverables are, and the main limitation.
