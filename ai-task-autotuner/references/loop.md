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
   | noise | item flips between identical runs | stop tuning this; more data or repeats |

4. Write one hypothesis (below), make a new candidate by copying the best (or a branch
   point) to `candidates/cNNN-<slug>`, change only what the hypothesis needs, `diff -u` it.
5. `ledger.py propose ...`, smoke run (`--purpose smoke --limit 2`), full dev run with
   `--exp`, `ledger.py decide`, journal block.

## Writing a hypothesis

- One behavior, one root cause, one change (it may touch several places).
- `--expect`: predicted effect with a range, e.g. "+0.02..0.05 primary via 'unit-mismatch'
  items; cost unchanged". `--falsifier`: the result that would show you were wrong.
- Scale the change to how many items show the behavior; a fix can gain at most what those
  items lose. If that is below `min_delta`, it cannot be seen - pick a bigger target.
- Generalize, never memorize: describe the failure behavior and the rule, do not paste
  item content, ids or answers from dev into prompts. That overfits dev and fails holdout.
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
- Efficiency phase: `keep` when primary stays >= the floor and the efficiency metric improves
  by >= `min_efficiency_gain` (10%), or primary improves at equal cost.
- `rerun`: a marginal keep (delta < 2x min_delta, or near the floor) from one run - repeat it
  (`--tag r2`) and decide again on the pooled runs.
- `--override keep|discard --reason` exists for evidence the rules cannot see; use rarely and
  say why. Discarded and kept candidates both stay on disk.

## Phases

**Quality phase** - maximize the primary metric under the guards. Ends when the quality target
is met, or on plateau: >= `patience` (5) non-keeps since the last keep, covering >= 3 lever
groups, with a fresh-eyes round done. Before declaring a plateau, re-measure noise on the
incumbent (`validate_eval.py WS --renoise RUN_A RUN_B` on two runs of the best): a stabilized
prompt often flips far fewer items than the baseline did, which lowers min_delta and can reveal
headroom the baseline noise hid. Then `ledger.py phase WS efficiency` (floor = best minus
min_delta) and, optionally, one holdout check of the best (`--split holdout --purpose holdout`)
to catch overfitting early.

**Efficiency / structure phase** - hold the floor, cut cost per unit, latency, or calls;
simplify. Order the levers by expected gain per risk:
1. hygiene: remove instructions the model does not need, shrink input to what decisions use,
   compact output (ids, codes, no prose rationale unless it measurably helps), static prefix
   first for caching;
2. deterministic work: rule pre-filters, exact-match shortcuts, candidate pruning - the
   model only sees what needs judgment;
3. structure: batch several units or items per call, or split one heavy call into a cheap
   screen plus a focused second pass only where needed (cascade);
4. model x effort staircase: from the current cell, step effort down; when quality holds,
   step down; when it fails, step right at most 1-2 notches; prune cells projected to cost
   more than the incumbent; stop when no unprobed cell is projected cheaper;
5. re-check one quality lever on the cheaper configuration (prompt wins do not always transfer).

Report cost per completed unit, not per call: a cheaper call that fails more is not cheaper.
Each efficiency keep may give back a little quality; `status` prints the DRIFT since the phase
switch and warns when the cumulative drop exceeds min_delta - check holdout before cutting further.
Runs broken by infrastructure (quota, outages) never count: the ledger skips quota-exhausted runs,
and `ledger.py void WS RUN --reason` excludes any other broken run. Concurrency is a runtime knob:
`run_experiment.py --concurrency N` changes it without editing (and re-freezing) the candidate.

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
  2. Headroom probe - one dev run that tells you where the ceiling is, e.g. the same prompt
     on a stronger model (if it also fails, the spec or reference is the problem, not model
     capacity); or feed one stage oracle input (true candidates, true attributes) to measure
     the ceiling of the next stage; or an ablation removing a component. Record it with
     `ledger.py mark WS headroom "<result>"`. Probes are diagnostics, not candidates to keep.
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
1. A user limit is reached (cost, hours, experiments, deadline) - finalize and report. If the
   score is still clearly climbing, say so in the report and offer to continue; do not exceed a
   limit the user set.
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

## Finalize

1. Holdout: run the final best (and baseline, if not yet) once: `--split holdout --purpose
   holdout`. The headline is the holdout delta baseline -> best with intervals. If dev gains do
   not reproduce on holdout, do not claim them; recommend the simpler configuration.
2. Consistency: 3 runs of the best on dev (`--tag r1..r3`), `analyze_run.py --consistency`
   for pass^k and flaky items.
3. Full-scope run (`--split full --purpose full`) for time, usage and outcome distribution.
4. `export_deliverables.py WS --candidate <best> --runs <full run> <holdout run>` for the
   measured production settings; write `task_design.md` (task-design.md) and the report
   (report.md); re-run export so they are copied in.

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
