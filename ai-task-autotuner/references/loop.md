# The improvement loop

Contents: round routine · writing a hypothesis · decision rules · repeats · phases and the
efficiency staircase · avoiding dead ends (diversify, fresh eyes, headroom probes, branch
points) · stop rules · journal · running long jobs

## Round routine

1. `ledger.py status WS` and follow RECOMMEND. It already applies the rules below.
2. Analyze the current best run on dev:
   - `analyze_run.py RUN` - failure tags, worst units, efficiency, slow calls, truncations.
   - `analyze_run.py RUN --show <unit or item>` for 3-5 representative failures (truncated).
   - Many failures or long transcripts: `analyze_run.py RUN --dump-failures WS/tmp/fail.jsonl`
     and give the file to a subagent: "cluster these failures by root cause; per cluster:
     count, one-sentence mechanism, 2 example ids, the smallest change that would fix it;
     under 300 words". Do not read the dump yourself.
3. Bucket what you see before choosing a fix:

   | Bucket | Tell | Response |
   |---|---|---|
   | model gap | model lacked a rule/fact or misapplied one | prompt/context/decomposition lever |
   | grader/reference error | output looks right, score says wrong | fix eval (see evaluation.md), rescore |
   | harness | timeouts, truncation, parse failures | harness/output lever; not a model problem |
   | structure | info present but not used; same failure across many variants | decomposition, input representation |
   | noise | item flips between identical runs | measure variability with repeats; retain confirmed contract violations |

4. Write one hypothesis (below), make a new candidate by copying the best (or a branch
   point) to `candidates/cNNN-<slug>`, change only what the hypothesis needs, `diff -u` it.
5. `ledger.py propose ...`, smoke run (`--purpose smoke --limit 2`), full dev run with
   `--exp`, `ledger.py decide`, journal block.

`--exp` binds a run to the candidate registered by that proposal. Passing a different candidate
is refused before work starts; the ledger also rejects historical mismatches rather than
crediting their scores to the proposal. Candidate bytes and merged backend/model/effort/runtime
settings freeze at the first non-smoke measurement. Copy to a new candidate before changing
them; historical mixed settings cannot be averaged as repeats of the same configuration.

## Writing a hypothesis

- One behavior and one falsifiable hypothesis (it may touch several places). Start with a
  single-factor probe, then test a specified risky interaction when independent changes miss
  the failure; keep the interaction and its expected effect explicit.
- `--expect`: predicted effect with a range, e.g. "+0.02..0.05 primary via 'unit-mismatch'
  items; cost unchanged". `--falsifier`: the result that would show you were wrong.
- For aggregate optimization gains, scale the change to how many items show the behavior;
  a fix can gain at most what those items lose. Below `min_delta`, the aggregate rule may
  not distinguish the gain. Prefer a larger opportunity or a justified measurement plan,
  while still fixing confirmed hard-contract violations and keeping their regression cases.
- Generalize, never memorize selection-dev or holdout answers. Few-shot demonstrations may
  come from synthetic cases or the predeclared group-disjoint example pool (task-design.md).
  Do not move scored cases between partitions or edit the scorer to make a candidate win.
- De-fluff every prompt change: cut platitudes ("be careful"), restated defaults and
  unfalsifiable advice. Fluff costs tokens on every call and dilutes the rules that matter.

## Decision rules (applied by `ledger.py decide`)

Compared with the current best on the same split and eval version, items paired by id:

- `void`: harness error rate > max(5%, 2x best) - fix infrastructure, re-run.
- `invalid`: any hard-gate failure.
- `discard`: a guard metric violated.
- Quality phase: `keep` when primary improves by >= `min_delta` and more items improved than
  regressed; also `keep` a quality tie (|delta| < min_delta) that is >= 10% cheaper on the
  efficiency metric - simpler and cheaper wins ties. Otherwise `discard`.
- Efficiency phase: `keep` when primary respects its quality bound (>= the floor for `max`,
  <= the ceiling for `min`) and the efficiency metric improves by >= `min_efficiency_gain`
  (10%), or primary improves in its declared direction at equal cost.
- A measured efficiency of zero is valid: positive -> zero is a 100% reduction and zero ->
  zero is a tie. Missing, non-finite or otherwise invalid measurements remain unknown; they
  cannot become a free candidate or a claimed efficiency gain.
- `rerun`: a marginal keep (delta < 2x min_delta, or near the quality bound) from one run - repeat it
  (`--tag r2`) and decide again on the pooled runs. The decision line also prints a
  unit-bootstrap 95% interval; when it straddles zero, more repeats beat more rounds.
- Decisions keep the numbers of their time. After a scorer/reference change and `--rescore`,
  `status` re-pools the best from rescored runs and flags anything still stale. Changed source
  data, loader or splits require newly generated outputs, as described in evaluation.md.
- These thresholds compare aggregate candidate performance. A reproducible hard-contract
  violation remains a fault even when rare or statistically insignificant in the aggregate.
  Apply its documented failure action and retain the counterexample; p-values do not rank
  severity. A contract definition is not a test execution result.
- `--override keep|discard --reason` exists for evidence the rules cannot see; use rarely and
  say why. Discarded and kept candidates both stay on disk.

## Phases

**Quality phase** - improve the primary metric in its declared direction under the guards. Ends when the quality target
is met, or on plateau: >= `patience` (5) non-keeps since the last keep, covering >= 3 lever
groups, with a fresh-eyes round done. Before declaring a plateau, re-measure noise on the
incumbent (`validate_eval.py WS --renoise RUN_A RUN_B` on two runs of the best): a stabilized
prompt often flips far fewer items than the baseline did, which lowers min_delta and can reveal
headroom the baseline noise hid. Then `ledger.py phase WS efficiency` (quality bound = best minus
`min_delta` for `max`, best plus `min_delta` for `min`) and, optionally, one holdout check of the best (`--split holdout --purpose holdout`)
to catch overfitting early.

Structural rewrites and prompt/context tests are eligible in either phase. Use the input model
and [conditional contracts](robustness.md) to investigate assumptions, information loss and
scale before adding more instructions. A rewrite must preserve semantic decisions, including
alternative conditions and global constraints; old-model answer equality is not the oracle.

Increase thinking only after a diagnosed failure or a documented starting exception under
[the thinking policy](task-design.md#thinking-and-output-budget). Change one supported level
at a time and check the affected cases plus the frozen evaluation and guards. Where safe,
route only the difficult subset to that level; retain shared constraints and count routing
cost. Revisit the added thinking after prompt or structural improvements.

**Efficiency / structure phase** - respect the quality bound, cut cost per unit, latency, or calls;
simplify. Choose the order by expected gain and risk; structural waste may dominate wording:
1. hygiene: remove instructions the model does not need, shrink input to what decisions use,
   compact output (ids, codes, no prose rationale unless it measurably helps), static prefix
   first for caching;
2. deterministic work: rule pre-filters, exact-match shortcuts, candidate pruning - the
   model only sees what needs judgment;
3. structure: batch several units or items per call, or split one heavy call into a cheap
   screen plus a focused second pass only where needed (cascade); test shared context,
   compact attribute mappings and code joins when applicable. Preserve global constraints
   through reconciliation or a solver where possible; a single giant call is not required;
4. model x effort staircase: try the next supported lower effort or thinking off; retain
   the lower setting when quality and contracts hold. If it fails, keep the incumbent.
   Consider a different model only with a concrete quality/cost hypothesis; do not respond
   to every failure by upgrading. Stop when no unprobed cell is projected cheaper;
5. re-check one quality lever on the cheaper configuration (prompt wins do not always transfer).

Report cost per completed unit, not per call: a cheaper call that fails more is not cheaper.
Keep the evaluation denominator fixed and report failures against the original scope. Inspect
input and output tokens separately, including repeated context and required output size. If
the configured efficiency objective is call count, explicitly report its token blind spot;
it cannot establish a token reduction. Measure growth against relevant input sizes and output
`K`, and validate the complete pipeline after a local optimization.
Each efficiency keep may give back a little quality; `status` prints the DRIFT since the phase
switch and warns when the cumulative drop exceeds min_delta - check holdout before cutting further.
Runs broken by infrastructure (quota, outages) never count: the ledger skips quota-exhausted runs,
and `ledger.py void WS RUN --reason` excludes any other broken run. `--concurrency N` records
the actual parallelism in the effective settings. Set it before the first non-smoke measurement;
changing it afterward, even for a code-only candidate, requires a new candidate.

## Avoiding dead ends

Similar-looking attempts in a row are the usual way a loop wastes hours. The ledger enforces
diversity mechanically; your job is to take its advice seriously.

- **Lever groups.** Every proposal names `group.detail`: prompt, input, output,
  decomposition, deterministic, model, ensemble, context, simplify (harness only for infra).
  `status` shows kept/tried per group and the untried ones.
- **DIVERSIFY** (two non-keeps and the last three in one group, or many non-keeps in few
  groups): the next experiment must use another group, untried groups first.
- **Novelty check.** `propose` refuses a hypothesis that is textually close to a discarded one,
  or the same lever and family. Override only with `--force-similar "<new evidence>"`.
- **FRESH_EYES** (three non-keeps since the last keep). Do all three, then
  `ledger.py mark WS fresh-eyes "<what changed in your understanding>"`:
  1. Re-bucket all remaining dev failures (table above) with counts.
  2. Headroom probe - a bounded diagnostic on approved development cases: the same prompt
     on an explicitly configured stronger model, verified/oracle input to one stage, or an
     ablation removing a component. A stronger model's failure does not identify the cause;
     an oracle intervention estimates downstream headroom under changed inputs, not a proven
     ceiling or the original stage's incapacity. Confirm repairs end to end. Run it with
     `--purpose probe` and record it with `ledger.py mark WS headroom "<result>"`. Probes are
     diagnostics, never candidates.
  3. Fresh subagent, deliberately without the ledger or journal (no anchoring). Give it
     `audit/data_audit.md`, `eval/eval_spec.md`, the best candidate's files and the failure
     dump, and ask:
     > Propose 5 different ways to raise <metric> on this task. Each must use a different
     > mechanism (prompting, input representation, decomposition, deterministic processing,
     > verification/cascade, model/effort...). For each: the failure cluster it targets with
     > counts, why it should work, expected effect, cost impact, and how to test it in one
     > experiment. Do not propose rewording the existing instructions.
     Pick the proposal most different from what the ledger already tried, not the most familiar.
- **Branch points.** Kept candidates are listed as branch points. When a line of edits has
  accumulated complexity, branch from an earlier keep, or try `simplify.ablation` - removing
  parts is a legitimate experiment and often wins ties.
- **Exploration quota.** If three experiments in a row used one group, spend the next on an
  untried group even when things are going well (EXPLORE).
- **Breadth pass for long tails.** When the remaining failures are many small unrelated
  clusters, one experiment may cover several independent gaps at once - say so in the
  hypothesis; this is the one sanctioned exception to one-change-per-experiment.

## Stop rules

In priority order:
1. A user limit is reached (cost, hours, experiments, deadline) - finalize and report from
   available evidence. If the score is still clearly climbing, say so in the report and offer
   to continue; do not exceed a limit the user set.
2. User targets met (quality, then efficiency if set) - finalize.
3. No limits given: stop when the efficiency phase plateaus (same plateau rule) - this is the
   improvement limit. Also stop on repeated infrastructure failure you cannot fix (report it).
4. Quota exhausted (the runner prints QUOTA EXHAUSTED, pauses the clock, and the run is void):
   write the state and stop; after the reset `ledger.py clock WS resume` and rerun the void
   experiment. If your own session was cut off (quota, crash), start the resumed session with
   `ledger.py clock WS resume --gap` - it books the silent gap as an outage. Time limits count
   active time only: put a duration ("2 hours") in `limits.max_hours`; `limits.deadline` is an
   absolute wall-clock time and does not move with pauses.
5. Never stop silently: `ledger.py stop WS --reason "..."`, then finalize.

The runner enforces explicit ledger STOP, a paused clock, observed USD spend, active hours
and wall-clock deadline at entry, before units, and before each physical model attempt.
Semaphore-queued calls re-check after acquiring a slot; retries check again too. Reached
money/time/deadline limits and STOP block new work, including finalization calls. Calls
already in flight may complete above the observed USD cap; this is not an absolute billing
reservation. Unreported costs cannot be treated as zero to satisfy a USD limit.
An abandoned prior run without cost evidence also prevents claiming the remaining USD budget;
the current run is exempt only until its own attempts produce observed cost evidence.

`limits.max_experiments` reserves slots for proposals and direct candidates. The last allowed
proposal can still run and repeat within its reserved slot. Final checks can reuse an already
measured candidate, but cannot add a new candidate beyond the cap or bypass STOP, paused time,
money or deadline restrictions. Do not set STOP before optional final measurements you still
intend to run; once stopped, export and report the existing evidence.

## Finalize

Do these measurements only while execution limits permit them. If a hard limit or STOP has
already fired, report which checks were not run and use existing evidence.

1. Holdout: run the final best (and baseline, if not yet) once: `--split holdout --purpose
   holdout`. The headline is the holdout delta baseline -> best with intervals. If dev gains do
   not reproduce on holdout, do not claim them; recommend the simpler configuration.
2. Consistency: 3 runs of the best on dev (`--tag r1..r3`), `analyze_run.py --consistency`
   for pass^k and flaky items.
3. Full-scope run (`--split full --purpose full`) for time, usage and outcome distribution.
   If it includes holdout ids it also consumes a peek for that candidate's identity, bytes
   and effective settings; identical repeats of an already evaluated candidate share its peek.
   Review applicable contract results for the chosen candidate as well: record passed, failed,
   inconclusive or not-run checks with evidence, including supported-size limits. Execute only
   the remaining risk-relevant checks that the budget permits; do not imply coverage from a
   complete definition file alone.
4. Write `WS/task_design.md` (task-design.md) and `WS/report.md` (report.md), then run
   `export_deliverables.py WS --candidate <best> --runs <full run> <holdout run>` for the
   measured production settings and replay dependencies. Use an empty export destination;
   after a later report update, export again with `--out <new empty directory>`.

## Journal (`journal.md`)

English, terse. Per experiment:

```
E007 input.representation (c007-table-view <- c004): show SKU rows as a compact table
  expect +0.03 via conflated-axis items | got +0.041 (+9/-2 items) KEEP | cost x0.82
  why: model stopped merging gender+size; still misses unit conversions (6 items)
```

Keep `## Learnings` (true statements about task and model, max ~20) and `## Idea backlog`
(group: idea - evidence - status) current; they are what a resumed session needs.

## Running long jobs

Runs over ~2 minutes: start `run_experiment.py` as a background command and wait for its
completion notice instead of polling with sleep. While it runs you may prepare the next
candidate, but do not start another measured run that competes for the same rate limit unless
concurrency is part of the experiment. Verify on disk (summary.json exists, unit count right)
before deciding.
