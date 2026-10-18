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
3. **Scope** - units and items in scope vs original totals; exclusions with reasons and counts;
   subsets used for dev/holdout/full with ids file paths; auxiliary/load-test data kept apart.
4. **Data audit** - gate result, findings by severity, known limitations, data defects found.
5. **Evaluation** - primary and guard metrics, reference tier and how it was built
   (model, runs, disagreement rate, adjudication counts, human-checked sample size), splits
   and grouping, noise floor, validation checks. What may be called accuracy and what not.
6. **Results** - per outcome class (e.g. matched / no match / unsure / conditional /
   failed / not processed) counts against the total, which classes count as auto-decided;
   breakdown by the audit's strata (asymmetric cases, sizes, missing text...); quality on dev
   and holdout; pass^k and flaky items. Do not present attribute-level or candidate-rank
   numbers as overall correctness.
7. **Time** - run wall time, sum of unit times, per unit and per item (state which is an
   apportioned average and which is a measured per-call latency), median/p90/slowest units,
   concurrency, cold vs warm cache, retries and timeouts.
8. **Usage and cost** - calls, input/output/cache-read/cache-write/thinking tokens per unit
   and total, cost with its basis, uncached comparison, harness overhead (calibration) noted.
9. **Improvement history** - ledger table: experiment, lever, hypothesis (short), delta,
   decision; what moved the score and why; what did not work (dead ends are findings too);
   phase switch point; stop reason.
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

## History table (from the ledger)

```
| exp | lever | change | dev primary | delta | cost/unit | decision |
```
`ledger.py history WS` prints it; include discarded rows - they show the search.

## Final message to the user

Short, in their language: what was built, the headline numbers with their names (accuracy vs
agreement), whether code alone suffices for some or all of it, the recommended settings, where
the deliverables are, and the main limitation.
