# Phase 1: data audit and quality gate

Purpose: decide whether experiments may start at all, and learn from the data how the
task should be solved. Data quality is a start condition, not a score.

## Steps

1. **Read what the user provided about the data** (brief, README, schemas). List stated
   facts and stated limitations separately ("labels not included", "10 descriptions
   unavailable"). Stated limitations become `known=True` findings: they are reported, not
   blocking - unless the plan cannot work around them. A general acceptance ("pairs are
   unverified, use them anyway") does not cover specific defects you find later (three pairs
   that are clearly different products): those are new findings - unless the brief already
   prescribes how to handle that class ("record doubtful pairs separately"): then follow the
   brief, record the defects with severity medium and quote the brief as the justification. Side files the checks read
   (provenance, mappings) go in `charter.data.aux` so they are hashed too.
2. **Profile** every input the pipeline will read:
   `profile_data.py <files/dirs> --out WS/audit/profile` (`--out` is a file prefix). Note record counts, keys
   (`KEY?`), duplicated values in id/url/title-like fields, null rates, size distributions.
3. **Write `audit/checks.py`** - computed checks, no eyeballing. Cover at least:
   - schema: required fields present, types, ids unique, references between records resolve;
   - duplicates and near-duplicates of units (same source URL/title/text hash) - they double
     count and leak across splits, so they define `group_of` for splitting;
   - labels/reference: present? provenance? internally consistent (same input, different label)?
     Can every unit be scored? Count units that cannot;
   - distribution and difficulty drivers (sizes, asymmetries, missing text, rare classes),
     with counts per stratum - these become report breakdowns;
   - how much a trivial rule would solve (exact match after normalization, majority class,
     nearest neighbour by title similarity). If rules solve nearly everything, model
     optimization may not be worth it - say so, and if a derived task is too easy (random
     distractors), plan harder candidates rather than celebrating a high score;
   - shortcuts and leakage: the answer or its id appearing verbatim in the input (item codes in
     descriptions, file names, URLs), features that only exist for one class, duplicated units
     that let a model "remember" across splits;
   - prompt-injection-like text inside data (instructions, "ignore previous"...), as info;
   - cost/feasibility: input size per unit vs context; rough full-pass cost from calibration.
4. **Close-read a stratified sample** (10-20 units, more for heterogeneous data). Record one
   line per unit in `audit/close_read.jsonl`:
   `{"id", "verdict": "ok|hard|ambiguous|defect", "evidence", "severity"?, "known"?}`.
   The gate turns `defect` lines into a finding (default high) and `ambiguous` into medium.
   Distinguish genuine hard cases (keep them - they are often the point) from defects.
   Close-read units are forced into dev when splits are made (the template's `--make-splits`
   reads close_read.jsonl), because their content is already in your context.
5. **Write `audit/data_audit.md`** (template below), then run `quality_gate.py WS`.

## Severity and the gate

| Severity | Meaning | Gate |
|---|---|---|
| critical | the task or its scoring cannot be defined on this data: unreadable inputs, missing required fields, contradictory labels, unremovable leakage | STOP |
| high | results would mislead unless a human decides: doubtful label provenance, severe skew, duplicated units that double count, many unscorable units | REVIEW |
| medium | affects interpretation; carry into the report | PASS |
| low / info | facts | PASS |

INCOMPLETE (exit 5) means an audit step is missing (profile, data_audit.md, checks.py,
close_read.jsonl) - finish it; it is not something to report.

STOP or REVIEW: do not run experiments. Report to the user: finding ids, evidence (counts,
example ids), impact on what can be measured, and concrete options with a recommended default
(so "推奨案でOK" is a complete answer). Drafting design notes that the options depend on is fine;
freezing eval files or sending data to a model is not. Never rewrite labels, delete
inconvenient units, or mark an issue `known` just to pass. Only the user can accept a REVIEW
(`--accept-review "<their words>"`). The gate pauses the time-limit clock while you wait and
acceptance resumes it. If the data changes, re-run the audit; runs refuse to start when input
hashes differ from the gate.

A recorded REVIEW acceptance carries forward only when the substantive blocking findings,
audited data and check code are identical. A finding id is not the accepted scope: new affected
records, evidence or severity under the same id invalidate the old acceptance. Older close-read
gates stored only truncated defect details, so they need one fresh audit and, if still REVIEW,
fresh user acceptance; the new snapshot preserves the complete defect scope for later checks.
This applies only to genuine data issues, not routine design-review fixes.

## Scope decisions

Record in `charter.json` (`data.scope_notes`) and the audit: what is in scope, what is
excluded and why, with counts against the original totals. Load-test or auxiliary sets stay
out of accuracy denominators. Duplicate representations of the same data (e.g. one JSONL and
one-file-per-record copies) are counted once.

## data_audit.md template

```markdown
# Data audit - <task> (<date>)
## Inputs
file, size, records, sha256 (short), what one unit is
## Stated by the user (known limitations)
## Findings
| id | severity | finding | evidence (counts, example ids) | impact | recommendation |
## Difficulty and strata
easy vs hard drivers with counts; strata to report separately
## Trivial-solution and shortcut checks
what a rule/majority/nearest-neighbour baseline would get; answer leakage in inputs; whether model
optimization is worth it (if not, say so - it goes to non_ai_findings.md)
## Close read
how many units, how chosen, verdict counts (details in close_read.jsonl)
## Scope
included / excluded with reasons and counts
## Gate
PASS / REVIEW / STOP and why
```
